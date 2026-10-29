-- ═══════════════════════════════════════════════════════════════════════════
-- statwise/supabase-audit-hardening.sql
-- One idempotent migration for AUDIT_REPORT.md findings:
--   C-1, C-2, C-3, C-5, C-6, C-7, H-4, H-5, H-6, H-8,
--   M-19, M-20, M-23, M-24, M-39, M-40
--
-- WHAT IT DOES
--   14 numbered sections (see banners below). Each section converges the
--   live database to the audited target state: server-managed tier columns,
--   no world-readable PII, a subscription-expiry job, tier-gated booking/
--   insights access, service-only admin & audit tables, SELECT-only sports
--   data, minimal table grants, own-row payment/subscription RLS, atomic
--   counter/referral RPCs, and payment-queue bounds (open-checkout cap +
--   expires_at ordering support for scripts/verify-payments.ts).
--
-- HOW TO APPLY — THIS FILE IS NOT APPLIED AUTOMATICALLY
--   You must paste it into the Supabase SQL editor of the LIVE project
--   (Dashboard → SQL Editor → New query → paste → Run). Nothing in this
--   repo applies it: there is no migration runner, and CI never touches the
--   database. Verify afterwards with the queries in the VERIFICATION block
--   at the bottom of this file.
--
-- RE-RUN SAFETY (every statement is safe to run repeatedly)
--   * whole file wrapped in a single transaction — on any error nothing is
--     committed; fix the error and paste the whole file again;
--   * create table if not exists / add column if not exists /
--     create index if not exists / create or replace function;
--   * policies: drop-then-create loops over pg_policies (name-agnostic) or
--     drop policy if exists — Postgres has no CREATE POLICY IF NOT EXISTS;
--   * triggers: drop trigger if exists then create;
--   * grants/revokes are naturally idempotent;
--   * existence guards use to_regclass / to_regprocedure / information_schema
--     and raise notices instead of failing when a prerequisite object (e.g.
--     payment_events from section 5 of supabase-pending-migrations.sql, or
--     push_tokens from supabase-native-push-migration.sql) is absent.
--
-- ORDER MATTERS — sections build on each other (section 3 uses the helper
-- from section 1, section 8's re-grants follow sections 5–7's revokes), so
-- always run the file top-to-bottom in one go.
--
-- NOTE: if you ever re-apply supabase-schema.sql / database_schema.sql
-- (they create some of the permissive policies this file drops), re-apply
-- THIS file afterwards — order of the two matters.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 1 — C-1: tier & counter columns are server-managed
--   Any signed-in user could PATCH user_profiles with current_tier='VVIP
--   Tier' (the RLS policy constrains the ROW, not the COLUMNS). The old
--   protect_tier_columns trigger (pending-migrations section 5) covered only
--   six columns, only UPDATE, and trusted any user-JWT-less session.
--   Here we REPLACE that trigger with a jsonb-key guard (jsonb lookups skip
--   columns that do not exist on this live schema — no runtime errors if
--   points/reward_points/... were never created), add an INSERT guard so a
--   fresh signup can only bootstrap as Free Tier (M-20's upsert path), and
--   add public.is_service_writer() as the single trust predicate.
-- ═══════════════════════════════════════════════════════════════════════════

-- Trust predicate for every guard in this file. VOLATILE on purpose: it
-- reads GUCs that change within a transaction (register_referral sets the
-- trusted-writer flag, then the triggers run in the same transaction).
-- EXECUTE stays granted to PUBLIC — the trigger functions call it while
-- executing as the connected role, so revoking would break all user writes.
create or replace function public.is_service_writer()
returns boolean
language plpgsql
as $$
declare
  v_claims text;
begin
  -- A SECURITY DEFINER RPC on this server (register_referral) marks the
  -- rest of its transaction as trusted. set_config(..., is_local => true)
  -- is transaction-local and can only be issued from SQL executed on the
  -- server: PostgREST only routes functions from the exposed schema, and
  -- set_config lives in pg_catalog — so a REST client cannot mint this
  -- privilege for itself.
  if current_setting('app.statwise_trusted_writer', true) = '1' then
    return true;
  end if;

  -- A session bound to a real user is never a service writer. (auth.uid()
  -- can only fail on malformed claims — treat that as untrusted.)
  begin
    if auth.uid() is not null then
      return false;
    end if;
  exception when others then
    return false;
  end;

  -- No JWT at all (SQL editor, cron, migration session) → server side.
  v_claims := current_setting('request.jwt.claims', true);
  if v_claims is null or btrim(v_claims) = '' then
    return true;
  end if;

  -- JWT present but no user: only the service role is trusted. The anon
  -- key (role = 'anon') lands here and is treated as NON-service, which is
  -- the fail-closed reading.
  begin
    return coalesce((v_claims::jsonb ->> 'role') = 'service_role', false);
  exception when others then
    return false; -- malformed claims → fail closed
  end;
end;
$$;

create or replace function public.protect_tier_columns()
returns trigger
language plpgsql
as $$
declare
  newj jsonb;
  oldj jsonb;
  k    text;
  v    jsonb;
  -- Every protected key. Looked up in to_jsonb(new)/to_jsonb(old), so a key
  -- whose column does not exist on this live schema is simply skipped —
  -- the guard never errors on schema drift between the repo variants.
  v_keys constant text[] := array[
    'tier', 'current_tier', 'subscription_status', 'subscription_start',
    'subscription_end', 'subscription_period', 'tier_expiry',
    'subscription_expires_at', 'points', 'reward_points', 'referral_count',
    'referral_codes', 'referral_earned', 'referral_code', 'referred_by',
    'total_referrals', 'total_rewards', 'total_referral_rewards', 'is_admin'];
  -- Counters that may only be seeded zero/false/'' on INSERT (M-19: honest
  -- signups send nothing or 0; a self-minted 999999 must be rejected).
  v_falsy constant text[] := array[
    'points', 'reward_points', 'referral_count', 'referral_earned',
    'total_referrals', 'total_rewards', 'total_referral_rewards', 'is_admin'];
begin
  newj := to_jsonb(new);
  if tg_op = 'UPDATE' then
    oldj := to_jsonb(old);
  else
    oldj := '{}'::jsonb; -- never dereference unassigned OLD on INSERT
  end if;

  if public.is_service_writer() then
    return new; -- poller / expire job / trusted RPC (GUC) may write
  end if;

  if tg_op = 'INSERT' then
    -- Bootstrap only: a profile row may be created, but only as Free Tier.
    -- (The row-level INSERT policy already forces id = auth.uid().)
    foreach k in array array['tier', 'current_tier'] loop
      if newj ? k and newj ->> k is not null
                and lower(btrim(newj ->> k)) <> 'free tier' then
        raise exception
          'user_profiles.% may only be ''Free Tier'' at creation — tiers are granted by the verification poller only', k
          using errcode = '42501';
      end if;
    end loop;
    if newj ? 'subscription_status' and newj ->> 'subscription_status' is not null
           and lower(btrim(newj ->> 'subscription_status')) <> 'active' then
      raise exception
        'user_profiles.subscription_status may only be ''active'' at creation'
        using errcode = '42501';
    end if;
    foreach k in array v_falsy loop
      if newj ? k then
        v := newj -> k;
        if v is not null and v <> 'null'::jsonb and (
             (jsonb_typeof(v) = 'number'  and (v)::text::numeric <> 0)
          or (jsonb_typeof(v) = 'boolean' and (v)::text = 'true')
          or (jsonb_typeof(v) = 'string'  and btrim((v)::text) <> '')
          or jsonb_typeof(v) not in ('number', 'boolean', 'string')
           ) then
          raise exception
            'user_profiles.% cannot be seeded on insert — server-managed counter', k
            using errcode = '42501';
        end if;
      end if;
    end loop;
    return new;
  end if;

  -- UPDATE: any client change to a protected key is rejected, whether it
  -- upgrades (self-grant) or downgrades (the signup upsert racing a fresh
  -- purchase must not silently strip a paid tier either).
  foreach k in array v_keys loop
    if newj ? k and (not oldj ? k or newj -> k is distinct from oldj -> k) then
      raise exception
        'user_profiles.% is server-managed (verification poller / referral RPC only)', k
        using errcode = '42501';
    end if;
  end loop;
  -- Suffix rules for any other counter-shaped column this schema may have.
  for k in select jsonb_object_keys(newj) loop
    if (k like '%\_count' or k like '%\_earned' or k like '%\_rewards')
       and (not oldj ? k or newj -> k is distinct from oldj -> k) then
      raise exception
        'user_profiles.% is a server-managed counter', k
        using errcode = '42501';
    end if;
  end loop;
  return new;
end;
$$;

do $$
begin
  if to_regclass('public.user_profiles') is null then
    raise notice 'section 1 (C-1): public.user_profiles missing — triggers skipped';
    return;
  end if;
  drop trigger if exists protect_tier_columns on public.user_profiles;
  create trigger protect_tier_columns
    before update on public.user_profiles
    for each row
    execute function public.protect_tier_columns();
  drop trigger if exists protect_tier_columns_ins on public.user_profiles;
  create trigger protect_tier_columns_ins
    before insert on public.user_profiles
    for each row
    execute function public.protect_tier_columns();
  raise notice 'section 1 (C-1): protect_tier_columns installed (UPDATE + INSERT guards)';
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 2 — C-2: user_profiles is no longer world-readable
--   "Allow public read for referral validation" (USING (true)) plus
--   GRANT SELECT ... TO anon made every email, tier, billing and referral
--   field readable with the anon key from the shipped bundle. Drop every
--   public-read SELECT policy, keep/create only the own-row policy, and
--   revoke the anon grant. Signup's referral-code check reads
--   referral_codes (NOT user_profiles), so it keeps working.
--   Known side effect: the forum's embedded user_profiles(...) join
--   (avatars/tier badges of other authors) now returns null for other
--   users — forum.tsx already renders from the message's own username
--   fields when the embed is absent.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
declare p record;
begin
  if to_regclass('public.user_profiles') is null then
    raise notice 'section 2 (C-2): public.user_profiles missing — skipped';
    return;
  end if;

  -- The audit-named policy, then a name-agnostic sweep of anything else
  -- whose USING clause is exactly `true` (whitespace/paren-insensitive).
  drop policy if exists "Allow public read for referral validation" on public.user_profiles;
  for p in
    select policyname from pg_policies
     where schemaname = 'public'
       and tablename = 'user_profiles'
       and cmd = 'SELECT'
       and regexp_replace(coalesce(qual, ''), '[\s()]', '', 'g') = 'true'
  loop
    execute format('drop policy %I on public.user_profiles', p.policyname);
    raise notice 'section 2 (C-2): dropped public-read policy %', p.policyname;
  end loop;

  if not exists (
    select 1 from pg_policies
     where schemaname = 'public'
       and tablename = 'user_profiles'
       and cmd = 'SELECT'
       and policyname = 'Users can view their own profile'
  ) then
    create policy "Users can view their own profile"
      on public.user_profiles for select
      to authenticated
      using (id = auth.uid());
  end if;

  revoke select on public.user_profiles from anon;
  -- C-2 side-effect guard: the tier-gated SELECT policies on `predictions`
  -- and `insights` (sections 7 & 10) subquery user_profiles to check the
  -- caller's tier. Postgres validates that subquery's table privileges at
  -- PLAN time, so a full revoke above made every anon read of predictions /
  -- insights fail with "permission denied for table user_profiles" — killing
  -- even the free-tier rows that must stay public (the marketing site's
  -- live-predictions page reads them with the anon key).
  --
  -- A COLUMN-level grant fixes the plan while keeping C-2's real goal intact:
  -- anon sees only id + current_tier (both non-sensitive; tier is already
  -- leaked by the policies themselves). email, username, display_name,
  -- phone, referral codes, counters etc. stay revoked.
  grant select (id, current_tier) on public.user_profiles to anon;
  raise notice 'section 2 (C-2): user_profiles readable by its owner only (anon grant revoked; anon retains column-level id,current_tier so tier-gated policies can plan)';
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 3 — H-6 / M-19: referral & reward rows cannot be client-minted
--   referrals and rewards had WITH CHECK (true) INSERT policies (attacker
--   chooses referrer/reward_amount), and referral_codes.total_referrals was
--   self-writable (dishonest users set 999999; honest referrals silently
--   no-oped because RLS filtered the counter UPDATE — M-19). No app code
--   writes any of these tables directly (the only writer is the
--   register_referral RPC from section 12), so:
--     * referral_codes: non-service INSERT may only seed total_referrals
--       0/null; non-service UPDATE may not change total_referrals;
--     * referrals / rewards: no non-service writes at all;
--     * the loose INSERT policies are dropped (belt — the triggers are the
--       braces, RLS is the belt in case a legacy migration re-adds them).
--   The section-12 RPC sets the trusted-writer GUC so its own counter
--   bumps pass both this trigger and the section-1 trigger.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace function public.enforce_referral_row_integrity()
returns trigger
language plpgsql
as $$
begin
  -- service role, or register_referral inside its trusted transaction
  if public.is_service_writer() then
    return new;
  end if;

  if tg_table_name = 'referral_codes' then
    if tg_op = 'INSERT' then
      if new.total_referrals is not null and new.total_referrals <> 0 then
        raise exception 'referral_codes.total_referrals must start at 0 (server-managed)'
          using errcode = '42501';
      end if;
      return new;
    end if;
    -- UPDATE: bootstrap may change username/active/… but never the counter.
    if new.total_referrals is distinct from old.total_referrals then
      raise exception 'referral_codes.total_referrals is server-managed (register_referral RPC only)'
        using errcode = '42501';
    end if;
    return new;
  end if;

  -- referrals / rewards: no client path exists — everything goes through
  -- register_referral (section 12), so reject outright.
  raise exception 'public.% is written by the server only (register_referral RPC)', tg_table_name
    using errcode = '42501';
end;
$$;

do $$
declare p record;
begin
  -- Drop the loose INSERT policies (H-6 evidence: WITH CHECK (true) /
  -- participant-only) on both tables.
  if to_regclass('public.referrals') is not null then
    for p in
      select policyname from pg_policies
       where schemaname = 'public' and tablename = 'referrals' and cmd = 'INSERT'
    loop
      execute format('drop policy %I on public.referrals', p.policyname);
      raise notice 'section 3 (H-6): dropped referrals INSERT policy %', p.policyname;
    end loop;
    execute 'drop trigger if exists referral_write_guard on public.referrals';
    execute 'create trigger referral_write_guard before insert or update on public.referrals for each row execute function public.enforce_referral_row_integrity()';
  else
    raise notice 'section 3: public.referrals missing — skipped';
  end if;

  if to_regclass('public.rewards') is not null then
    for p in
      select policyname from pg_policies
       where schemaname = 'public' and tablename = 'rewards' and cmd = 'INSERT'
    loop
      execute format('drop policy %I on public.rewards', p.policyname);
      raise notice 'section 3 (H-6): dropped rewards INSERT policy %', p.policyname;
    end loop;
    execute 'drop trigger if exists referral_write_guard on public.rewards';
    execute 'create trigger referral_write_guard before insert or update on public.rewards for each row execute function public.enforce_referral_row_integrity()';
  else
    raise notice 'section 3: public.rewards missing — skipped';
  end if;

  if to_regclass('public.referral_codes') is not null then
    execute 'drop trigger if exists referral_write_guard on public.referral_codes';
    execute 'create trigger referral_write_guard before insert or update on public.referral_codes for each row execute function public.enforce_referral_row_integrity()';
    raise notice 'section 3 (H-6/M-19): referral_write_guard installed on referrals, rewards, referral_codes';
  else
    raise notice 'section 3: public.referral_codes missing — skipped';
  end if;
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 4 — C-3: subscriptions actually expire
--   One purchase granted a permanent tier — no cron/RPC/job ever compared
--   expiry to now(). expire_subscriptions() downgrades every profile whose
--   expiry column has passed: tier/current_tier → 'Free Tier',
--   subscription_status → 'expired', expiry column(s) → null. SECURITY
--   DEFINER (must update every user's row), dynamic column list so it runs
--   on any of the repo schema variants (first existing of
--   subscription_expires_at / tier_expiry / subscription_end — actually all
--   that exist are honoured: if ANY says the sub is over, it is over),
--   returns the number of rows changed, and is idempotent (rows already
--   downgraded no longer match the WHERE).
--   Invoked hourly by .github/workflows/expire-subscriptions.yml via
--   POST /rest/v1/rpc/expire_subscriptions with the service-role key
--   (workflow added separately).
-- ═══════════════════════════════════════════════════════════════════════════

create or replace function public.expire_subscriptions()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_cols  text[] := '{}';
  v_pred  text   := '';
  v_clear text   := '';
  v_count integer := 0;
begin
  if to_regclass('public.user_profiles') is null then
    raise notice 'expire_subscriptions: public.user_profiles missing — nothing to do';
    return 0;
  end if;

  select coalesce(array_agg(column_name), '{}')
    into v_cols
    from information_schema.columns
   where table_schema = 'public'
     and table_name = 'user_profiles'
     and column_name in ('subscription_expires_at', 'tier_expiry', 'subscription_end');

  if v_cols = '{}'::text[] then
    raise notice 'expire_subscriptions: no expiry column on user_profiles — nothing to do';
    return 0;
  end if;

  -- (col is not null and col <= now()) for each existing column …
  select coalesce(string_agg(format('(%I is not null and %I <= now())', c, c), ' or '), '')
    into v_pred
    from unnest(v_cols) as t(c);
  -- … and clear every one of them on success.
  select coalesce(string_agg(format('%I = null', c), ', '), '')
    into v_clear
    from unnest(v_cols) as t(c);

  -- tier/current_tier/subscription_status/updated_at exist in every schema
  -- variant in this repo (database_schema.sql and supabase-schema.sql).
  execute format(
    'update public.user_profiles'
    || ' set tier = %L, current_tier = %L, subscription_status = %L, %s, updated_at = now()'
    || ' where %s',
    'Free Tier', 'Free Tier', 'expired', v_clear, v_pred);
  get diagnostics v_count = row_count;
  raise notice 'expire_subscriptions: downgraded % profile(s)', v_count;
  return v_count;
end;
$$;

revoke execute on function public.expire_subscriptions()
  from public, anon, authenticated;
grant execute on function public.expire_subscriptions()
  to service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 5 — C-5: booking codes & AI-pick entitlements are tier-gated
--   ACTIVE FIX (already in code, no migration needed): ai/sportybet_booking.py
--   ::get_ai_picks() now filters to tier == 'free', so the code published to
--   the world-readable app_settings.booking_code row can no longer carry a
--   premium/vip/vvip pick. That closes the live C-5 leak on its own.
--
--   This section is PREPARED, HARDENED FORWARD INFRASTRUCTURE — it is NOT yet
--   wired to any reader/writer (verified: no .py/.ts references these objects).
--   It is the intended long-term design IF/WHEN we want to serve tier-gated
--   booking codes to entitled paying users instead of only free picks:
--     * booking_codes: service-only home for the generated SportyBet code
--       (RLS on, zero client policies, client grants revoked);
--     * ai_pick_access: append-only ledger of "user X may see the pick for
--       match Y"; its INSERT policy is STRENGTHENED beyond auth.uid() =
--       user_id: the row's tier must equal the prediction's tier AND the
--       caller's current_tier must rank at least that high — otherwise the
--       ledger itself would become a self-service entitlement mint;
--     * claim_pick_access(): SECURITY DEFINER claim path where the DB's
--       prediction tier is authoritative (null/blank → treated as 'vvip',
--       fail closed), the optional p_tier hint must agree with it, and the
--       caller's tier is re-checked on every call (a later downgrade stops
--       further claims). Returns true/false; never raises for normal
--       refusals.
--   Applying this section is safe (idempotent, exposes nothing) but has no
--   runtime effect until a client is pointed at booking_codes/claim_pick_access.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists public.booking_codes (
  id           uuid primary key default gen_random_uuid(),
  code         text not null,                       -- SportyBet share code
  bookmaker    text not null default 'SportyBet',
  share_url    text,
  selections   jsonb not null default '[]'::jsonb,   -- per-pick payload
  signature    text,
  tier         text not null default 'free',        -- highest pick tier
  status       text not null default 'active'
               check (status in ('active', 'expired')),
  is_active    boolean not null default true,
  expires_at   timestamptz,
  generated_at timestamptz not null default now(),
  created_at   timestamptz not null default now()
);

alter table public.booking_codes enable row level security;
-- Deliberately NO policies: only the service role (bypasses RLS) writes or
-- reads this table, so no anon/authenticated row can ever expose a code.
revoke all on public.booking_codes from anon, authenticated;
grant all on public.booking_codes to service_role;

create table if not exists public.ai_pick_access (
  user_id    uuid not null references auth.users (id) on delete cascade,
  match_id   text not null,                          -- predictions.match_id
  tier       text not null default 'free',           -- tier the pick needs
  created_at timestamptz not null default now(),
  primary key (user_id, match_id)
);

alter table public.ai_pick_access enable row level security;

drop policy if exists "Read own AI-pick access" on public.ai_pick_access;
create policy "Read own AI-pick access"
  on public.ai_pick_access for select
  to authenticated
  using (user_id = auth.uid());

drop policy if exists "Claim own AI-pick access with entitlement" on public.ai_pick_access;
create policy "Claim own AI-pick access with entitlement"
  on public.ai_pick_access for insert
  to authenticated
  with check (
    user_id = auth.uid()
    and exists (
      select 1
        from public.predictions p
       where p.match_id = public.ai_pick_access.match_id
         -- the ledger row must claim exactly the pick's real tier …
         and lower(coalesce(nullif(btrim(public.ai_pick_access.tier), ''), 'vvip'))
             = lower(coalesce(nullif(btrim(p.tier), ''), 'vvip'))
         -- … and the caller must rank at least that high (unknown pick
         -- tier ranks as vvip → fail closed; missing profile → null →
         -- comparison null → denied).
         and (case lower(coalesce(nullif(btrim(p.tier), ''), 'vvip'))
                when 'free' then 0
                when 'premium' then 1
                when 'vip' then 2
                when 'vvip' then 3
                else 3
              end)
             <= (select case lower(coalesce(nullif(btrim(u.current_tier), ''), 'free tier'))
                          when 'free tier' then 0
                          when 'free' then 0
                          when 'premium tier' then 1
                          when 'premium' then 1
                          when 'vip tier' then 2
                          when 'vip' then 2
                          when 'vvip tier' then 3
                          when 'vvip' then 3
                          else 0
                        end
                    from public.user_profiles u
                   where u.id = auth.uid())
    )
  );

revoke all on public.ai_pick_access from anon, authenticated;
grant select, insert on public.ai_pick_access to authenticated;
grant all on public.ai_pick_access to service_role;

create or replace function public.claim_pick_access(p_match_id text, p_tier text default null)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_uid        uuid := auth.uid();
  v_pick       text;
  v_need       text;
  v_need_rank  integer;
  v_user_rank  integer;
begin
  if v_uid is null then
    raise exception 'claim_pick_access requires an authenticated session'
      using errcode = '42501';
  end if;

  select p.tier into v_pick
    from public.predictions p
   where p.match_id = p_match_id;
  if not found then
    return false; -- unknown match → no access
  end if;

  -- The database's tier on the prediction is authoritative; null/blank is
  -- treated as the most restrictive tier (fail closed).
  v_need := lower(coalesce(nullif(btrim(v_pick), ''), 'vvip'));
  if p_tier is not null and lower(btrim(p_tier)) <> v_need then
    return false; -- caller's hint disagrees with the DB → refuse
  end if;

  v_need_rank := case v_need
    when 'free' then 0
    when 'premium' then 1
    when 'vip' then 2
    when 'vvip' then 3
    else 3
  end;

  select case lower(coalesce(nullif(btrim(u.current_tier), ''), 'free tier'))
           when 'free tier' then 0
           when 'free' then 0
           when 'premium tier' then 1
           when 'premium' then 1
           when 'vip tier' then 2
           when 'vip' then 2
           when 'vvip tier' then 3
           when 'vvip' then 3
           else 0
         end
    into v_user_rank
    from public.user_profiles u
   where u.id = v_uid;

  if not found or v_user_rank < v_need_rank then
    return false; -- no profile yet, or not entitled to this pick's tier
  end if;

  insert into public.ai_pick_access (user_id, match_id, tier)
  values (v_uid, p_match_id, v_need)
  on conflict (user_id, match_id) do nothing;

  -- true = access is valid now (freshly granted or already on file; the
  -- rank check above re-validates on every call, so a downgrade after the
  -- fact stops new claims).
  return true;
end;
$$;

revoke execute on function public.claim_pick_access(text, text)
  from public, anon, authenticated;
grant execute on function public.claim_pick_access(text, text)
  to authenticated, service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 6 — C-6: admin tables and logs are service-only
--   admin_urls (FOR ALL USING (true)), admin_users (SELECT true) and
--   system_logs (SELECT/INSERT true) were fully open to the anon key:
--   admin identities exfiltrated, admin URLs rewritten, audit log forged.
--   No app code references them. Converge to: RLS on, ZERO policies (only
--   roles that bypass RLS — service_role / table owner — can touch them),
--   all client grants revoked. cleanup_expired_admin_urls() is SECURITY
--   DEFINER (writes admin_urls as its owner), so its EXECUTE is revoked
--   from client roles too — otherwise anon could invoke an owner-privileged
--   DELETE at will.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
declare
  t text;
  p record;
begin
  foreach t in array array['admin_urls', 'admin_users', 'system_logs'] loop
    if to_regclass('public.' || t) is null then
      raise notice 'section 6 (C-6): public.% missing — skipped', t;
    else
      execute format('alter table public.%I enable row level security', t);
      for p in
        select policyname from pg_policies
         where schemaname = 'public' and tablename = t
      loop
        execute format('drop policy %I on public.%I', p.policyname, t);
      end loop;
      execute format('revoke all on public.%I from anon, authenticated', t);
      execute format('grant all on public.%I to service_role', t);
      raise notice 'section 6 (C-6): public.% is service-only (RLS on, no policies, client grants revoked)', t;
    end if;
  end loop;

  if to_regprocedure('public.cleanup_expired_admin_urls()') is not null then
    execute 'revoke execute on function public.cleanup_expired_admin_urls() from public, anon, authenticated';
    execute 'grant execute on function public.cleanup_expired_admin_urls() to service_role';
  end if;
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 7 — C-7: sports data & accuracy history are SELECT-only
--   matches / team_stats / prediction_accuracy carried FOR ALL USING (true)
--   policies — anyone could DELETE fixtures or rewrite the public hit-rate
--   record. Writes happen with the service-role key (bypasses RLS), so
--   clients only need SELECT. For predictions the tier policy must be
--   KEPT (it is the paywall), but any world-readable USING (true) SELECT
--   policy would leak premium rows, so that is swept too and the tier
--   policy is (re)created if the sweep left none.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
declare
  t text;
  p record;
begin
  foreach t in array array['matches', 'team_stats', 'prediction_accuracy'] loop
    if to_regclass('public.' || t) is null then
      raise notice 'section 7 (C-7): public.% missing — skipped', t;
    else
      execute format('alter table public.%I enable row level security', t);
      for p in
        select policyname, cmd from pg_policies
         where schemaname = 'public' and tablename = t
           and cmd in ('ALL', 'INSERT', 'UPDATE', 'DELETE')
      loop
        execute format('drop policy %I on public.%I', p.policyname, t);
        raise notice 'section 7 (C-7): dropped % policy on public.%', p.cmd, t;
      end loop;
      if not exists (
        select 1 from pg_policies
         where schemaname = 'public' and tablename = t and cmd = 'SELECT'
      ) then
        execute format('create policy "Public read for %I" on public.%I for select using (true)', t, t);
      end if;
      execute format('revoke insert, update, delete on public.%I from anon, authenticated', t);
    end if;
  end loop;

  -- predictions: drop every write policy AND any USING (true) SELECT
  -- policy; keep/create the tier-gated SELECT (the paywall itself).
  if to_regclass('public.predictions') is null then
    raise notice 'section 7 (C-7): public.predictions missing — skipped';
  else
    for p in
      select policyname, cmd, qual from pg_policies
       where schemaname = 'public'
         and tablename = 'predictions'
         and (cmd in ('ALL', 'INSERT', 'UPDATE', 'DELETE')
              or regexp_replace(coalesce(qual, ''), '[\s()]', '', 'g') = 'true')
    loop
      execute format('drop policy %I on public.predictions', p.policyname);
      raise notice 'section 7 (C-7): dropped % policy on predictions', p.cmd;
    end loop;

    if not exists (
      select 1 from pg_policies
       where schemaname = 'public' and tablename = 'predictions' and cmd = 'SELECT'
    ) then
      create policy "Users can view predictions based on their subscription tier"
        on predictions for select
        using (
          CASE
            WHEN tier = 'free' THEN true
            WHEN tier = 'premium' THEN EXISTS (
              SELECT 1 FROM user_profiles
               WHERE id = auth.uid()
                 AND current_tier IN ('Premium Tier', 'VIP Tier', 'VVIP Tier'))
            WHEN tier = 'vip' THEN EXISTS (
              SELECT 1 FROM user_profiles
               WHERE id = auth.uid()
                 AND current_tier IN ('VIP Tier', 'VVIP Tier'))
            WHEN tier = 'vvip' THEN EXISTS (
              SELECT 1 FROM user_profiles
               WHERE id = auth.uid()
                 AND current_tier = 'VVIP Tier')
            ELSE false
          END
        );
      raise notice 'section 7 (C-7): recreated the tier-gated SELECT policy on predictions';
    end if;
    execute 'revoke insert, update, delete on public.predictions from anon, authenticated';
  end if;
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 8 — H-8: table grants reduced to what the app actually uses
--   supabase-schema.sql:622 granted ALL tables to authenticated (and
--   database_schema.sql:291 granted ALL to anon as well): write GRANTS,
--   independent of RLS, sat open on every table. Blanket-revoke client
--   writes, then re-grant the audited minimum — cross-checked against the
--   app's complete set of direct .insert/.update/.delete/.upsert calls
--   (user_profiles, user_favorites, push_tokens, referral_codes,
--   payment_events, client_events; everything else writes via
--   SECURITY DEFINER RPCs or the service role).
--   Tables the app never touches (user_prediction_history, notifications,
--   push_subscriptions, user_history, account_history) keep only SELECT.
--   Sequences are deliberately NOT touched (client_events uses an identity
--   column whose sequence grant must survive).
--   M-39 note: the Python jobs fall back to a hardcoded ANON key when env
--   is missing — after this section those writes fail loudly (permission
--   denied) instead of silently mutating row-by-row, which is the DB-side
--   half of that finding; the env fallback itself is code, not SQL.
-- ═══════════════════════════════════════════════════════════════════════════

revoke insert, update, delete on all tables in schema public from anon, authenticated;

do $$
declare
  g record;
begin
  for g in
    select * from (values
      -- anon + authenticated SELECT (public reads the app/website rely on)
      ('select', 'predictions',           'anon, authenticated'),
      ('select', 'referral_codes',        'anon, authenticated'),  -- signup code check
      ('select', 'matches',               'anon, authenticated'),
      ('select', 'team_stats',            'anon, authenticated'),
      ('select', 'prediction_accuracy',   'anon, authenticated'),
      ('select', 'app_settings',          'anon, authenticated'),
      ('select', 'forum_messages',        'anon, authenticated'),
      -- authenticated-only writes/reads (post-blanket-revoke re-grants)
      ('select, insert, update', 'user_profiles',        'authenticated'),
      ('select, insert, update', 'referral_codes',       'authenticated'),
      ('select, insert',         'payment_events',       'authenticated'),  -- checkout queue
      ('select',                 'payment_transactions', 'authenticated'),  -- history read
      ('select, insert',         'subscription_events',  'authenticated'),
      ('select, insert, delete', 'user_favorites',       'authenticated'),
      ('insert',                 'client_events',        'authenticated'),  -- telemetry
      ('select, insert',         'ai_pick_access',       'authenticated')   -- section 5
    ) as t(perms, tbl, roles)
  loop
    if to_regclass('public.' || g.tbl) is null then
      raise notice 'section 8 (H-8): public.% missing — grant skipped', g.tbl;
    else
      execute format('grant %s on public.%I to %s', g.perms, g.tbl, g.roles);
    end if;
  end loop;

  -- push_tokens only exists once supabase-native-push-migration.sql ran;
  -- grant mirrors that file exactly (upsert needs UPDATE, sign-out DELETE).
  if to_regclass('public.push_tokens') is not null then
    execute 'grant select, insert, update, delete on public.push_tokens to authenticated';
  end if;
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 9 — H-5: payment_transactions / subscription_events are own-row
--   Both tables shipped with FOR INSERT WITH CHECK (user_id = auth.uid())
--   plus FOR UPDATE USING (true) — cross-tenant tampering, and a client
--   could squat payment_transactions' UNIQUE transaction_id with a forged
--   "successful"/"VVIP Tier" row. Converge to RLS on + own-row SELECT (+
--   own-row INSERT for subscription_events, as the base schema had).
--
--   *** DELIBERATE DEVIATION from the audit's literal INSERT wording ***
--   payment_transactions gets NO client INSERT policy at all: section 5 of
--   supabase-pending-migrations.sql deliberately dropped client INSERTs
--   ("the audit trail is written by the poller alone"), the audit's own
--   fix says "SELECT-own + service-role-write only", and re-adding INSERT
--   would let a user squat the global UNIQUE transaction_id. If a client
--   payment-history screen ever needs to write, it must go through a
--   SECURITY DEFINER RPC instead.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
declare p record;
begin
  if to_regclass('public.payment_transactions') is not null then
    execute 'alter table public.payment_transactions enable row level security';
    for p in
      select policyname from pg_policies
       where schemaname = 'public' and tablename = 'payment_transactions'
    loop
      execute format('drop policy %I on public.payment_transactions', p.policyname);
    end loop;
    create policy "Users can view their own payment transactions"
      on public.payment_transactions for select
      to authenticated
      using (user_id = auth.uid());
    -- NO INSERT / UPDATE / DELETE policy — see deviation note above.
    execute 'revoke insert, update, delete on public.payment_transactions from anon, authenticated';
    raise notice 'section 9 (H-5): payment_transactions = SELECT-own only, service-role write';
  else
    raise notice 'section 9 (H-5): public.payment_transactions missing — skipped';
  end if;

  if to_regclass('public.subscription_events') is not null then
    execute 'alter table public.subscription_events enable row level security';
    for p in
      select policyname from pg_policies
       where schemaname = 'public' and tablename = 'subscription_events'
    loop
      execute format('drop policy %I on public.subscription_events', p.policyname);
    end loop;
    create policy "Users can view their own subscription events"
      on public.subscription_events for select
      to authenticated
      using (user_id = auth.uid());
    create policy "Users can insert their own subscription events"
      on public.subscription_events for insert
      to authenticated
      with check (user_id = auth.uid());
    -- anon gets nothing; authenticated's INSERT grant comes from section 8,
    -- UPDATE/DELETE stay revoked (blanket revoke in section 8).
    execute 'revoke insert, update, delete on public.subscription_events from anon';
    raise notice 'section 9 (H-5): subscription_events = own-row SELECT + INSERT';
  else
    raise notice 'section 9 (H-5): public.subscription_events missing — skipped';
  end if;
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 10 — M-23: insights exists, with RLS and a tier gate
--   The Insights screen queries a table that no file in the repo creates —
--   if it was made via the dashboard it has no RLS and the anon key reads
--   every VIP article. Create it (idempotently), add the app-compat
--   columns (the screen does select * and orders by published_at; the
--   Insight type carries summary/content/category/tier_required), enable
--   RLS, drop any pre-existing policies, and leave exactly one SELECT
--   policy: free-tier rows for everyone, higher rows only for a profile
--   whose current_tier ranks high enough. The gate uses
--   greatest(rank(tier), rank(tier_required)) so a writer that fills only
--   tier_required cannot leak a VIP article; unknown values rank as
--   VIP+ (fail closed) while an unknown/absent profile ranks as free.
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists public.insights (
  id            uuid primary key default gen_random_uuid(),
  title         text not null,
  body          text,
  tier          text not null default 'free',   -- free | premium | vip | vvip
  -- app-compat (lib/insightsCore.ts Insight + the screen's order clause):
  published_at  timestamptz not null default now(),
  summary       text,
  content       text,
  category      text,
  tier_required text,                            -- e.g. 'VIP Tier' (display)
  created_at    timestamptz not null default now()
);

-- A dashboard-created insights table (M-23's "must check live" case) gets
-- the same columns added rather than failing.
alter table public.insights add column if not exists tier text not null default 'free';
alter table public.insights add column if not exists published_at timestamptz not null default now();
alter table public.insights add column if not exists summary text;
alter table public.insights add column if not exists content text;
alter table public.insights add column if not exists category text;
alter table public.insights add column if not exists tier_required text;
alter table public.insights add column if not exists body text;
alter table public.insights add column if not exists created_at timestamptz not null default now();

do $$
declare
  p record;
  ok boolean;
begin
  if to_regclass('public.insights') is null then
    raise notice 'section 10 (M-23): public.insights missing — policy skipped';
    return;
  end if;

  alter table public.insights enable row level security;

  -- Drop whatever a dashboard creation left behind so the tier gate is the
  -- only rule (policies are OR'ed — a leftover USING (true) would win).
  for p in
    select policyname from pg_policies
     where schemaname = 'public' and tablename = 'insights'
  loop
    execute format('drop policy %I on public.insights', p.policyname);
    raise notice 'section 10 (M-23): dropped pre-existing insights policy %', p.policyname;
  end loop;

  select count(*) = 2 into ok
    from information_schema.columns
   where table_schema = 'public'
     and table_name = 'insights'
     and column_name in ('tier', 'tier_required');
  if not ok then
    raise notice 'section 10 (M-23): insights.tier/tier_required unavailable — SELECT policy NOT created (table stays service-only)';
    return;
  end if;

  create policy "Insights are tier-gated"
    on public.insights for select
    to anon, authenticated
    using (
      greatest(
        case lower(coalesce(nullif(btrim(public.insights.tier), ''), 'free'))
          when 'free' then 0
          when 'free tier' then 0
          when 'premium' then 1
          when 'premium tier' then 1
          when 'vip' then 2
          when 'vip tier' then 2
          when 'vvip' then 3
          when 'vvip tier' then 3
          else 3
        end,
        case lower(coalesce(nullif(btrim(public.insights.tier_required), ''), 'free'))
          when 'free' then 0
          when 'free tier' then 0
          when 'premium' then 1
          when 'premium tier' then 1
          when 'vip' then 2
          when 'vip tier' then 2
          when 'vvip' then 3
          when 'vvip tier' then 3
          else 3
        end
      ) = 0
      or exists (
        select 1
          from public.user_profiles u
         where u.id = auth.uid()
           and (case lower(coalesce(nullif(btrim(u.current_tier), ''), 'free tier'))
                  when 'free tier' then 0
                  when 'free' then 0
                  when 'premium tier' then 1
                  when 'premium' then 1
                  when 'vip tier' then 2
                  when 'vip' then 2
                  when 'vvip tier' then 3
                  when 'vvip' then 3
                  else 0
                end)
               >= greatest(
                  case lower(coalesce(nullif(btrim(public.insights.tier), ''), 'free'))
                    when 'free' then 0
                    when 'free tier' then 0
                    when 'premium' then 1
                    when 'premium tier' then 1
                    when 'vip' then 2
                    when 'vip tier' then 2
                    when 'vvip' then 3
                    when 'vvip tier' then 3
                    else 3
                  end,
                  case lower(coalesce(nullif(btrim(public.insights.tier_required), ''), 'free'))
                    when 'free' then 0
                    when 'free tier' then 0
                    when 'premium' then 1
                    when 'premium tier' then 1
                    when 'vip' then 2
                    when 'vip tier' then 2
                    when 'vvip' then 3
                    when 'vvip tier' then 3
                    else 3
                  end
                )
      )
    );
  raise notice 'section 10 (M-23): insights created/updated with a single tier-gated SELECT policy';
end $$;

revoke all on public.insights from anon, authenticated;
grant select on public.insights to anon, authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 11 — M-40: atomic counter bumps for app_settings
--   app_settings.settlement_stats / booking stats were unlocked
--   read-modify-write from Python (concurrent settle runs lose
--   increments). bump_counter() does one locked upsert that merges
--   full-join-style: numeric keys are SUMMED (callers pass increments),
--   every other key is REPLACED by the caller's value, keys only in the
--   old value are kept. SERVICE ROLE ONLY — no app caller exists (grep
--   found none), so anon/authenticated cannot even execute it.
-- ═══════════════════════════════════════════════════════════════════════════

create or replace function public.bump_counter(p_key text, p_delta jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_result jsonb;
begin
  if p_key is null or btrim(p_key) = '' then
    raise exception 'bump_counter: p_key must be a non-empty string'
      using errcode = '22023';
  end if;
  if p_delta is null or jsonb_typeof(p_delta) <> 'object' then
    raise exception 'bump_counter: p_delta must be a jsonb object of increments'
      using errcode = '22023';
  end if;
  if to_regclass('public.app_settings') is null then
    raise exception 'bump_counter: public.app_settings does not exist'
      using errcode = '42P01';
  end if;

  insert into public.app_settings as s (setting_key, setting_value)
  values (p_key, p_delta)
  on conflict (setting_key) do update
    set setting_value = (
      select coalesce(
               jsonb_object_agg(
                 coalesce(o.k, n.k),
                 case
                   when o.k is null then n.v                     -- new key
                   when n.k is null then o.v                     -- untouched key
                   when jsonb_typeof(o.v) = 'number'
                    and jsonb_typeof(n.v) = 'number'
                     then to_jsonb((o.v)::numeric + (n.v)::numeric)  -- M-40: atomic sum
                   else n.v                                      -- non-numbers replace
                 end),
               '{}'::jsonb)
        from jsonb_each(coalesce(s.setting_value, '{}'::jsonb)) as o(k, v)
        full join jsonb_each(excluded.setting_value) as n(k, v)
          on o.k = n.k
    )
  returning s.setting_value into v_result;

  return coalesce(v_result, p_delta);
end;
$$;

revoke execute on function public.bump_counter(text, jsonb)
  from public, anon, authenticated;
grant execute on function public.bump_counter(text, jsonb)
  to service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 12 — M-19 / M-20: register_referral() — the referral actually counts
--   The client used to UPDATE the referrer's referral_codes.total_referrals
--   directly: RLS filtered the other user's row to 0 rows and supabase-js
--   reported no error, so every honest referral silently no-oped (M-19),
--   while a dishonest user could inflate their own counter. The app now
--   calls supabase.rpc('register_referral', { referrer_code }) from the
--   signup flow and checks the returned boolean — so this function
--   RETURNS BOOLEAN (not void): false = unknown/inactive code, self-referral
--   or already-credited; true = counters bumped. SECURITY DEFINER so the
--   referrer's rows are visible/writable; sets the trusted-writer GUC from
--   section 1 so the counter bumps pass the section-1 and section-3
--   guards; idempotent via the unique index below plus ON CONFLICT DO
--   NOTHING (race-safe: the losing concurrent call returns false).
-- ═══════════════════════════════════════════════════════════════════════════

do $$
begin
  if to_regclass('public.referrals') is null then
    raise notice 'section 12 (M-19): public.referrals missing — unique index skipped';
    return;
  end if;
  -- One referred user can be credited to at most one referrer. If legacy
  -- data already contains duplicates the CREATE fails with unique_violation
  -- — catch it so the migration still applies (register_referral keeps
  -- working via its exists-check in that case).
  begin
    create unique index if not exists referrals_referred_uniq
      on public.referrals (referred_id);
  exception when unique_violation then
    raise notice 'section 12 (M-19): duplicate referred_id rows exist — unique index skipped';
  end;
end $$;

create or replace function public.register_referral(referrer_code text)
returns boolean
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_uid   uuid    := auth.uid();
  v_code  text    := upper(btrim(coalesce(referrer_code, '')));
  v_id    uuid;
  v_owner uuid;
begin
  if v_uid is null then
    raise exception 'register_referral requires an authenticated session'
      using errcode = '42501';
  end if;
  if v_code = '' then
    return false;
  end if;

  select id, user_id into v_id, v_owner
    from public.referral_codes
   where upper(code) = v_code
     and active is distinct from false
   for update;
  if not found then
    return false; -- unknown or deactivated code
  end if;
  if v_owner = v_uid then
    return false; -- self-referral (was only blocked client-side: H-6)
  end if;
  if exists (select 1 from public.referrals where referred_id = v_uid) then
    return false; -- this signup is already credited to somebody
  end if;

  -- Trusted writer for the remainder of this transaction (section 1 + 3
  -- guards honour it; REST clients cannot set GUCs — see is_service_writer).
  perform set_config('app.statwise_trusted_writer', '1', true);

  insert into public.referrals (referrer_id, referred_id, referral_code)
  values (v_owner, v_uid, v_code)
  on conflict do nothing; -- any unique violation (race) → false below
  if not found then
    return false;
  end if;

  -- M-19: BOTH counters, server-side, actually visible to the referrer.
  update public.referral_codes
     set total_referrals = coalesce(total_referrals, 0) + 1,
         updated_at = now()
   where id = v_id;

  update public.user_profiles
     set total_referrals = coalesce(total_referrals, 0) + 1
   where id = v_owner;

  return true;
end;
$$;

revoke execute on function public.register_referral(text)
  from public, anon, authenticated;
grant execute on function public.register_referral(text)
  to authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 13 — H-4 / M-24 support: payment_events.expires_at
--   scripts/verify-payments.ts orders its queue closest-to-expiry first.
--   A literal expires_at = created_at + 24 h column makes that order
--   explicit (and self-documenting in the DB) instead of relying on the
--   caller knowing created_at drives the TTL. Preferred form is a
--   GENERATED column — clients physically cannot write it — with a plain
--   defaulted column as automatic fallback if this Postgres rejects the
--   expression as non-immutable; the fallback is backfilled to
--   created_at + 24 h so every existing row gets its true deadline.
--   Requires payment_events (section 5 of supabase-pending-migrations.sql);
--   skipped with a notice otherwise.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
begin
  if to_regclass('public.payment_events') is null then
    raise notice 'section 13 (H-4): public.payment_events missing — expires_at skipped';
    return;
  end if;

  if not exists (
    select 1 from information_schema.columns
     where table_schema = 'public'
       and table_name = 'payment_events'
       and column_name = 'expires_at'
  ) then
    begin
      alter table public.payment_events
        add column expires_at timestamptz
        generated always as (created_at + interval '24 hours') stored;
      raise notice 'section 13 (H-4): payment_events.expires_at added (generated)';
    exception when others then
      raise notice 'section 13 (H-4): generated column rejected (%) — using a defaulted column', sqlerrm;
      alter table public.payment_events
        add column expires_at timestamptz default (now() + interval '24 hours');
      update public.payment_events
         set expires_at = created_at + interval '24 hours';
    end;
  end if;

  -- Serves the poller's closest-to-expiry-first listing (H-4) exactly the
  -- way payment_events_pending_idx serves created_at.
  create index if not exists payment_events_pending_expires_idx
    on public.payment_events (expires_at)
    where status = 'pending';
  raise notice 'section 13 (H-4): payment_events.expires_at ready';
end $$;

-- ═══════════════════════════════════════════════════════════════════════════
-- SECTION 14 — H-4: the checkout queue cannot be flooded
--   The INSERT policy accepted unlimited pending rows for the caller with
--   no plan_id/tx_ref requirement — a free account could loop-insert
--   well-formed pending events, fill the poller's per-run budget and starve
--   genuine purchases until they aged out at 24 h. Converge to exactly two
--   client policies on payment_events (matching the verification block of
--   supabase-pending-migrations.sql) with a hardened queue policy:
--     * own row, status = 'pending', non-empty plan_id and tx_ref;
--     * at most 3 open (pending) checkouts per account — the same
--       MAX_PENDING_CHECKOUTS = 3 the client enforces in
--       app/(tabs)/subscriptions.tsx, so behaviour is unchanged for honest
--       users while the flood vector is closed at the source;
--     * NO client UPDATE/DELETE ever (only the poller, service role, flips
--       a row to verified/failed/expired).
--   The open-checkout count goes through a SECURITY DEFINER helper: a
--   policy expression that subqueried payment_events itself would recurse
--   through RLS, and the definer path counts without policy interference.
--   Pairs with the poller-side caps in scripts/verify-payments.ts (H-4).
-- ═══════════════════════════════════════════════════════════════════════════

create or replace function public.open_checkout_count(p_user_id uuid)
returns integer
language sql
security definer
set search_path = public, pg_temp
as $$
  select case
           when auth.uid() is not null and auth.uid() = p_user_id
             then (select count(*)::integer
                     from public.payment_events
                    where user_id = p_user_id
                      and status = 'pending')
           else 0 -- never disclose another account's queue state
         end;
$$;

do $$
declare p record;
begin
  if to_regclass('public.payment_events') is null then
    raise notice 'section 14 (H-4): public.payment_events missing — skipped';
    return;
  end if;

  execute 'alter table public.payment_events enable row level security';

  -- Drop every INSERT policy, then rebuild the hardened one (name-agnostic
  -- so this converges whatever the live policy is called).
  for p in
    select policyname from pg_policies
     where schemaname = 'public' and tablename = 'payment_events' and cmd = 'INSERT'
  loop
    execute format('drop policy %I on public.payment_events', p.policyname);
    raise notice 'section 14 (H-4): dropped payment_events INSERT policy %', p.policyname;
  end loop;
  -- Drop every SELECT policy too, so USING (true) (if a legacy variant has
  -- one) cannot expose other users' payment events.
  for p in
    select policyname from pg_policies
     where schemaname = 'public' and tablename = 'payment_events' and cmd = 'SELECT'
  loop
    execute format('drop policy %I on public.payment_events', p.policyname);
    raise notice 'section 14 (H-4): dropped payment_events SELECT policy %', p.policyname;
  end loop;

  create policy "Read own payment events"
    on public.payment_events for select
    to authenticated
    using (auth.uid() = user_id);

  create policy "Queue own pending payment event"
    on public.payment_events for insert
    to authenticated
    with check (
      auth.uid() = user_id
      and status = 'pending'
      and amount >= 0
      and btrim(plan_id) <> ''
      and btrim(tx_ref) <> ''
      -- ≤ 3 open checkouts per account (mirrors MAX_PENDING_CHECKOUTS).
      and public.open_checkout_count(auth.uid()) < 3
    );

  -- Client grants for the queue, order-independent of section 8: the
  -- checkout flow must INSERT and poll its own rows, and nothing else.
  execute 'revoke insert, update, delete on public.payment_events from anon';
  execute 'revoke update, delete on public.payment_events from authenticated';
  execute 'grant select, insert on public.payment_events to authenticated';
  raise notice 'section 14 (H-4): payment_events queue policy hardened (own rows, required fields, ≤ 3 open)';
end $$;

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICATION (run after applying — read-only)
-- ═══════════════════════════════════════════════════════════════════════════
-- 1) Tier guards installed (expect two rows: protect_tier_columns[_ins]):
--    select tgname, tgisinternal from pg_trigger
--     where tgrelid = 'public.user_profiles'::regclass
--       and tgname like 'protect_tier_columns%';
--
-- 2) No public-read policy on user_profiles, no anon select grant:
--    select policyname, cmd, qual from pg_policies
--     where schemaname = 'public' and tablename = 'user_profiles';
--    select has_table_privilege('anon', 'public.user_profiles', 'select');
--    -- expect: false
--
-- 3) Admin tables have ZERO policies and no client grants:
--    select tablename, rowsecurity from pg_tables
--     where schemaname = 'public'
--       and tablename in ('admin_urls', 'admin_users', 'system_logs');
--    select count(*) from pg_policies
--     where schemaname = 'public'
--       and tablename in ('admin_urls', 'admin_users', 'system_logs');
--    -- expect: rowsecurity = true × 3, count = 0
--
-- 4) Payment tables: payment_transactions = SELECT-own only;
--    payment_events = exactly the two queue policies:
--    select tablename, policyname, cmd from pg_policies
--     where schemaname = 'public'
--       and tablename in ('payment_transactions', 'payment_events',
--                         'subscription_events')
--     order by tablename, policyname;
--
-- 5) Server-only functions are not client-callable (expect three false):
--    select has_function_privilege('authenticated', 'public.expire_subscriptions()', 'execute'),
--           has_function_privilege('anon', 'public.bump_counter(text,jsonb)', 'execute'),
--           has_function_privilege('anon', 'public.register_referral(text)', 'execute');
--    -- register_referral: authenticated must be TRUE (the app calls it).
--
-- 6) Manual test of the C-1 guard, run AS THE ANON KEY (expect 42501):
--    curl -s -X PATCH "$SUPABASE_URL/rest/v1/user_profiles?id=eq.<own-uid>" \
--      -H "apikey: $SUPABASE_ANON_KEY" \
--      -H "Authorization: Bearer $SUPABASE_ANON_KEY" \
--      -H "Content-Type: application/json" -H "Prefer: return=minimal" \
--      -d '{"current_tier":"VVIP Tier"}'
--    -- expect: {"code":"42501", ... server-managed ...}
--
-- 7) Expiry job dry call (service key; expect a JSON/integer result):
--    curl -s -X POST "$SUPABASE_URL/rest/v1/rpc/expire_subscriptions" \
--      -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
--      -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY"
-- ═══════════════════════════════════════════════════════════════════════════

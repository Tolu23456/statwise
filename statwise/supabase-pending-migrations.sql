-- ═══════════════════════════════════════════════════════════════════════════
-- StatWise — ALL PENDING MIGRATIONS IN ONE FILE (run once in the Supabase
-- SQL editor; every statement is idempotent, so re-running is safe).
--
-- Contents, in order:
--   1) push_subscriptions      — web push (plan 3.2): per-browser subscription
--                                rows; the notify sender no-ops until this
--                                exists.
--   2) security hardening      — plan 4.3: rate-limited + sanitized
--                                send_forum_message RPC (6 msg/60s), forum
--                                write policies collapsed to the RPC only,
--                                app_settings read-only for clients (booking
--                                code integrity), payment/subscription
--                                tables no longer cross-user writable,
--                                referrals participant-only.
--   3) client_events           — plan 4.4: error/performance telemetry table
--                                with payload caps + per-IP budget; the daily
--                                metrics dashboard reads it.
--
-- AFTER RUNNING: the verification queries at the bottom of each section must
-- pass (they are included verbatim). Also confirm the public storage bucket
-- `forum-media` exists (Storage → New bucket → "forum-media" → Public) for
-- forum image/video uploads.
-- ═══════════════════════════════════════════════════════════════════════════

-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 1 of 3 — push notification subscriptions                     ║
-- ╚══════════════════════════════════════════════════════════════════════╝

-- Push notification subscriptions (plan step 3.2).
--
-- Run this in the Supabase SQL editor. Until it exists, ai/notify.py and
-- the app's push registration detect the missing table and no-op with a
-- clear message — nothing breaks.
--
-- Web push subscriptions are per-browser: one row per device. The endpoint
-- is unique — re-subscribing the same browser updates the keys.

create table if not exists push_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  endpoint    text not null unique,
  p256dh      text not null,
  auth        text not null,
  user_agent  text,
  created_at  timestamptz default now(),
  last_seen_at timestamptz default now()
);

create index if not exists push_subscriptions_user_id_idx
  on push_subscriptions (user_id);

alter table push_subscriptions enable row level security;

-- Users manage only their own subscriptions.
drop policy if exists "Users can view their own push subscriptions" on push_subscriptions;
create policy "Users can view their own push subscriptions"
  on push_subscriptions for select
  using (auth.uid() = user_id);

drop policy if exists "Users can insert their own push subscriptions" on push_subscriptions;
create policy "Users can insert their own push subscriptions"
  on push_subscriptions for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can update their own push subscriptions" on push_subscriptions;
create policy "Users can update their own push subscriptions"
  on push_subscriptions for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Users can delete their own push subscriptions" on push_subscriptions;
create policy "Users can delete their own push subscriptions"
  on push_subscriptions for delete
  using (auth.uid() = user_id);

-- The notification sender (service role) reads every row — the default
-- service_role bypass above covers that; no extra policy needed.

-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 2 of 3 — security hardening (rate limits, RLS locks)         ║
-- ╚══════════════════════════════════════════════════════════════════════╝

-- ═══════════════════════════════════════════════════════════════════════════
-- StatWise — security hardening migration (plan step 4.3: Abuse Prevention)
--
-- Run once in the Supabase SQL editor. Safe to re-run (every statement is
-- idempotent). After running, the verification queries at the bottom must
-- pass.
--
-- WHAT THIS FIXES (found by the step 4.3 security audit):
--
--  1. send_forum_message had NO server-side rate limit and NO server-side
--     length/sanitization checks, and forum_messages had four different
--     INSERT policies from three setup scripts — so any authenticated user
--     could bypass the RPC and insert unbounded rows directly (spam).
--     Now: the RPC strips control/invisible chars, caps at 1000 chars and
--     enforces 6 messages / 60s per user; ALL client-side write policies on
--     forum_messages are dropped, so the RPC is the only client write path.
--
--  2. app_settings (which holds the published booking code + generation
--     stats) had policy "Admin users can manage app settings" FOR ALL USING
--     (true) — ANY anon-key holder could overwrite or delete the booking
--     code row. The column that matters is written exclusively by the
--     backend (ai/sportybet_booking.py with the service key, which bypasses
--     RLS — verified: no anon-key writers exist in the repo).
--     Now: clients (anon + authenticated) can only SELECT; writes have no
--     policy at all = default deny for everyone but the service role.
--
--  3. payment_transactions and subscription_events had "FOR UPDATE USING
--     (true)" — any user could update ANY user's rows. Nothing in the app
--     or the workflows updates these tables as a client (verified: 0 files);
--     the service role bypasses RLS regardless.
--     Now: no client UPDATE policy — default deny.
--
--  4. referrals had "FOR INSERT WITH CHECK (true)" — any authenticated user
--     could insert arbitrary referral rows between any pair of user ids.
--     Now: only the two participants of the link may insert it (the signup
--     flow inserts with referred_id = auth.uid(), so it still passes). The
--     participant-scoped UPDATE policy stays because AuthContext uses an
--     upsert (INSERT ... ON CONFLICT DO UPDATE needs the UPDATE policy).
--
--  5. Client app-side companions (committed with this file):
--       lib/validation.ts      – central input validation + sanitization
--       lib/rateLimit.ts       – client half of the throttling (auth, forum,
--                                profile writes) with human-readable waits
--       lib/bookingGuard.ts    – booking code shape + https/sportybet.com
--                                allowlist for every shared/opened URL
--       signup/forgot-password – honeypot fields (bot trap, silent drop)
--
-- HONEST LIMITS (documented in the plan's security audit box):
--   * Tier upgrades (current_tier) are still attested by the client after
--     the Flutterwave checkout callback — closing that needs SERVER-SIDE
--     payment verification (webhook or edge function holding the Flutterwave
--     secret). It cannot be closed from this repo alone without breaking the
--     payment flow; see IMPROVEMENT_PLAN.md step 4.3 for the recommended
--     design.
--   * admin_urls / admin_users / system_logs keep their permissive policies
--     because an admin panel outside this repo may depend on them; flagged
--     as findings, not changed blind.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Forum: single, rate-limited, server-sanitized write path
-- ─────────────────────────────────────────────────────────────────────────

create or replace function public.send_forum_message(p_message text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_username text;
  v_clean    text;
  v_recent   int;
begin
  if auth.uid() is null then
    raise exception 'Not authenticated';
  end if;

  -- Server-side sanitization (mirrors lib/validation.ts sanitizeUserText):
  -- C0 control chars minus tab/newline, DEL, then zero-width + bidi
  -- overrides + line/paragraph separators. E-string double-escapes the
  -- backslashes so the REGEX engine — not the string parser — sees \xHH.
  v_clean := regexp_replace(coalesce(p_message, ''),
             E'[\\x{00}-\\x{08}\\x{0B}\\x{0C}\\x{0E}-\\x{1F}\\x{7F}]', '', 'g');
  v_clean := regexp_replace(v_clean,
             E'[\\x{200B}-\\x{200F}\\x{202A}-\\x{202E}\\x{2028}\\x{2029}]', '', 'g');
  v_clean := left(btrim(v_clean), 1000);

  if v_clean = '' then
    raise exception 'Message is empty';
  end if;

  -- Server-side rate limit: 6 messages / 60s per user. This is the check
  -- that matters — it holds for anyone who talks to PostgREST directly,
  -- bypassing the app entirely (the client mirrors it for instant UX).
  select count(*) into v_recent
  from public.forum_messages
  where user_id = auth.uid()
    and created_at > now() - interval '60 seconds';

  if v_recent >= 6 then
    raise exception 'Too many messages — wait a moment before sending again.';
  end if;

  select coalesce(display_name, username, email) into v_username
  from public.user_profiles
  where id = auth.uid()
  limit 1;

  insert into public.forum_messages (user_id, username, message, created_at)
  values (auth.uid(), coalesce(v_username, 'User'), v_clean, now());
end;
$$;

grant execute on function public.send_forum_message(text) to authenticated;

-- Drop every client-side write policy the three setup scripts created
-- (supabase-schema.sql, database_schema.sql, supabase-forum-setup.sql,
-- supabase-forum-fix.sql all named theirs differently — Postgres ORs
-- permissive policies, so ALL of them have to go). With no INSERT/UPDATE/
-- DELETE policy left, forum rows can only be written through the hardened
-- RPC above (app path) or by the service role (moderation/backend).
drop policy if exists "Users can send forum messages"             on public.forum_messages;
drop policy if exists "Authenticated users can send forum messages" on public.forum_messages;
drop policy if exists "Authenticated users can post messages"     on public.forum_messages;
drop policy if exists "Authenticated users can insert forum messages" on public.forum_messages;
drop policy if exists "Users can update their own forum messages" on public.forum_messages;
drop policy if exists "Users can update their own messages"       on public.forum_messages;
drop policy if exists "Users can delete their own forum messages" on public.forum_messages;
drop policy if exists "Users can delete their own messages"       on public.forum_messages;

-- ─────────────────────────────────────────────────────────────────────────
-- 2. app_settings: read-only for clients (booking code integrity)
-- ─────────────────────────────────────────────────────────────────────────

-- "Admin users can manage app settings" was FOR ALL USING (true): anon key
-- holders could PATCH the booking code, the generation stats, anything.
-- Reads stay (booking code row + stats are published to the app on purpose;
-- the SELECT policy is is_active = true and has no role restriction).
drop policy if exists "Admin users can manage app settings" on public.app_settings;
-- No replacement write policy → INSERT/UPDATE/DELETE are default-deny for
-- anon and authenticated. The backend (service key) bypasses RLS.

-- ─────────────────────────────────────────────────────────────────────────
-- 3. payment_transactions / subscription_events: no cross-user updates
-- ─────────────────────────────────────────────────────────────────────────

drop policy if exists "Allow system updates to payment transactions" on public.payment_transactions;
drop policy if exists "Allow system updates to subscription events"  on public.subscription_events;
-- Remaining policies (verified against repo usage):
--   payments: SELECT own, INSERT own  → app's post-checkout flow needs these
--   events:   SELECT own, INSERT own  → scoped to the participant
-- UPDATE for both: service role only.

-- ─────────────────────────────────────────────────────────────────────────
-- 4. referrals: participants only
-- ─────────────────────────────────────────────────────────────────────────

drop policy if exists "Users can insert referrals" on public.referrals;
drop policy if exists "Users can insert their own referral links" on public.referrals;
create policy "Users can insert their own referral links"
  on public.referrals
  for insert
  to authenticated
  with check (referrer_id = auth.uid() or referred_id = auth.uid());
-- UPDATE policy "Users can update their own referrals" is intentionally
-- left alone: AuthContext upserts (ON CONFLICT DO UPDATE requires it) and
-- it is already scoped to the two participants.

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICATION — run in the SQL editor after the migration
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 1) Nothing but SELECT policies remain on app_settings / forum_messages
--    for non-service roles:
--
--    select tablename, policyname, cmd
--    from pg_policies
--    where schemaname = 'public'
--      and tablename in ('app_settings', 'forum_messages',
--                        'payment_transactions', 'subscription_events')
--    order by tablename, policyname;
--    -- expect: no INSERT/UPDATE/DELETE on app_settings or forum_messages,
--    --         no UPDATE on payment_transactions/subscription_events
--
-- 2) From a shell (anon key) — booking code readable but unwritable:
--
--    curl -s "$SUPABASE_URL/rest/v1/app_settings?select=setting_key&setting_key=eq.booking_code" \
--      -H "apikey: $SUPABASE_ANON_KEY" -H "Authorization: Bearer $ANON_KEY"
--    -- expect: the row
--
--    curl -s -X PATCH "$SUPABASE_URL/rest/v1/app_settings?setting_key=eq.__probe__" \
--      -H "apikey: $ANON_KEY" -H "Authorization: Bearer $ANON_KEY" \
--      -H "Content-Type: application/json" -H "Prefer: return=representation" \
--      -d '{"description":"probe"}'
--    -- expect: 0 rows / permission denied (was: writable by anyone)
--
-- 3) Forum rate limit (authenticated): send a 7th message within 60s —
--    the RPC now raises "Too many messages — wait a moment...".
-- ═══════════════════════════════════════════════════════════════════════════

-- ╔══════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 3 of 3 — client telemetry (client_events table)              ║
-- ╚══════════════════════════════════════════════════════════════════════╝

-- ═══════════════════════════════════════════════════════════════════════════
-- StatWise — client telemetry table (plan step 4.4: Observability)
--
-- Run once in the Supabase SQL editor. Safe to re-run.
--
-- WHAT: `client_events` receives the app's error/performance events
-- (lib/telemetry.ts → setEventSink in app/_layout.tsx):
--   render_error  React ErrorBoundary caught a render crash
--   react_error   minified React invariant (e.g. hydration mismatch #418)
--   uncaught      window error / unhandledrejection
--   slow_query    Supabase REST call ≥ 1000 ms (Resource Timing)
--   custom        explicit screen events
--
-- ACCESS MODEL (deliberate):
--   INSERT  anon + authenticated  — any visitor's browser must be able to
--           report its own errors; the payload is sanitized and capped
--           client-side, and a trigger enforces the same caps + a per-IP
--           budget server-side (a hostile client can spam rows otherwise).
--   SELECT  service role ONLY — the daily metrics workflow (read-only via
--           the service key) is the sole reader; there is no client SELECT
--           policy, so no PII-shaped data is exposed back to the anon key.
--   DELETE/UPDATE  service role only (the workflow can prune old rows).
--
-- Until this migration runs, every capture in the app is a swallowed
-- no-op — telemetry can never break the product.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

create table if not exists public.client_events (
  id          bigint generated always as identity primary key,
  kind        text not null check (kind in
                ('render_error', 'react_error', 'uncaught', 'slow_query', 'custom')),
  message     text not null default '',
  context     jsonb,
  path        text,
  session_id  text not null,
  app_version text not null default '1.0.0',
  created_at  timestamptz not null default now()
);

create index if not exists client_events_kind_created_idx
  on public.client_events (kind, created_at desc);
create index if not exists client_events_session_idx
  on public.client_events (session_id);

alter table public.client_events enable row level security;

-- Anonymous and signed-in visitors may only append their own events.
drop policy if exists "Visitors can append client events" on public.client_events;
create policy "Visitors can append client events"
  on public.client_events
  for insert
  to anon, authenticated
  with check (true);
-- No SELECT / UPDATE / DELETE policy → default deny for both roles; the
-- service role (metrics workflow) bypasses RLS entirely.

-- ─────────────────────────────────────────────────────────────────────────
-- Server-side guard: payload caps + per-IP budget.
-- The client already sanitizes (lib/telemetry.ts), but the anon key is
-- public, so the database is the authority: oversized rows and floods are
-- rejected here regardless of who is sending.
-- ─────────────────────────────────────────────────────────────────────────

create or replace function public.enforce_client_event_limits()
returns trigger
language plpgsql
as $$
declare
  v_recent int;
  v_ip     text := coalesce(
    nullif(current_setting('request.headers', true)::jsonb ->> 'x-forwarded-for', ''),
    'unknown');
begin
  -- Caps identical to TELEMETRY in lib/telemetry.ts.
  if length(coalesce(new.message, '')) > 500 then
    raise exception 'client_events: message too long';
  end if;
  if length(coalesce(new.context::text, '')) > 2048 then
    raise exception 'client_events: context too large';
  end if;
  if length(coalesce(new.path, '')) > 200 then
    new.path := left(new.path, 200);
  end if;

  -- Budget: 60 events / 10 min per IP (the client caps a session at 30/hour;
  -- this is the hostile-client bound, generous on purpose so real incidents
  -- near a major outage are never silently dropped).
  select count(*) into v_recent
  from public.client_events
  where created_at > now() - interval '10 minutes'
    and coalesce(
      nullif(current_setting('request.headers', true)::jsonb ->> 'x-forwarded-for', ''),
      'unknown') = v_ip;

  if v_recent >= 60 then
    raise exception 'client_events: rate limit exceeded';
  end if;

  return new;
end;
$$;

drop trigger if exists client_events_limits on public.client_events;
create trigger client_events_limits
  before insert on public.client_events
  for each row execute function public.enforce_client_event_limits();

-- Retention: keep 90 days. pg_cron is NOT assumed (may not be enabled on
-- this project) — the metrics workflow prunes on its daily run instead, and
-- this comment documents the intent:
--   delete from public.client_events where created_at < now() - interval '90 days';

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICATION (run after the migration)
-- ═══════════════════════════════════════════════════════════════════════════
-- 1) Policies: exactly one INSERT policy, no client SELECT.
--    select policyname, cmd, roles from pg_policies
--    where schemaname='public' and tablename='client_events';
--
-- 2) Anon append works, anon read does not:
--    curl -s -X POST "$SUPABASE_URL/rest/v1/client_events" \
--      -H "apikey: $ANON" -H "Authorization: Bearer $ANON" \
--      -H "Content-Type: application/json" -H "Prefer: return=minimal" \
--      -d '{"kind":"custom","message":"migration probe","session_id":"probe"}'
--    -- expect: 201
--    curl -s "$SUPABASE_URL/rest/v1/client_events?limit=1" \
--      -H "apikey: $ANON" -H "Authorization: Bearer $ANON"
--    -- expect: 401/403 (no SELECT policy)
--
-- 3) Oversized row rejected by the trigger:
--    (same POST with "message": "<600 chars>") → 400/500 from the trigger.
-- ═══════════════════════════════════════════════════════════════════════════

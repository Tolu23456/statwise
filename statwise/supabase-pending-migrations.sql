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
--   4) social layer            — bookmarks synced to user_favorites, the
--                                notifications inbox, forum replies
--                                (reply_to) and reply/mention triggers.
--
-- AFTER RUNNING: the verification queries at the bottom of each section must
-- pass (they are included verbatim). Also confirm the public storage bucket
-- `forum-media` exists (Storage → New bucket → "forum-media" → Public) for
-- forum image/video uploads.
-- ═══════════════════════════════════════════════════════════════════════════

-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 1 of 4 of 4 — push notification subscriptions ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

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


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 2 of 4 of 4 — security hardening (rate limits, RLS locks) ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

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
--     (Signature: p_message text, p_reply_to uuid default null — identical
--     to supabase-social-migration.sql so any run order converges on ONE
--     function; p_reply_to only widens the feature, all hardening above
--     applies unchanged.)
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

-- Clear legacy 1-argument overloads first (a leftover makes PostgREST
-- unable to resolve the name), then the shared 2-argument definition —
-- byte-identical in behaviour to the one in
-- supabase-social-migration.sql so any execution order converges.
drop function if exists public.send_forum_message(text);

create or replace function public.send_forum_message(
  p_message  text,
  p_reply_to uuid default null
)
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

  -- A quote whose target no longer exists degrades to a plain message
  -- instead of failing the send.
  if p_reply_to is not null and not exists (
    select 1 from public.forum_messages where id = p_reply_to
  ) then
    p_reply_to := null;
  end if;

  select coalesce(display_name, username, email) into v_username
  from public.user_profiles
  where id = auth.uid()
  limit 1;

  insert into public.forum_messages (user_id, username, message, reply_to, created_at)
  values (auth.uid(), coalesce(v_username, 'User'), v_clean, p_reply_to, now());
end;
$$;

grant execute on function public.send_forum_message(text, uuid) to authenticated;

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


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 3 of 4 of 4 — client telemetry (client_events table) ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

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


-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║ SECTION 4 of 4 of 4 — social layer (favorites, inbox, replies/mentions) ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ═══════════════════════════════════════════════════════════════════════════
-- StatWise — social + notification layer (user request 2026-10-27)
--
-- Run once in the Supabase SQL editor (or as section 4 of
-- supabase-pending-migrations.sql). Safe to re-run — every statement is
-- idempotent, including the function signature swap.
--
-- WHAT THIS ADDS:
--
--  1. user_favorites — bookmarks synced off the device so the notify
--     cron can alert the RIGHT user when THEIR bookmarked match is about
--     to start (favorites were AsyncStorage-only: invisible to the
--     backend). The app keeps AsyncStorage as the instant UI source and
--     best-effort-syncs rows here; a missing table no-ops silently.
--
--  2. notifications — the inbox rows behind reply/mention alerts.
--     Written ONLY by the trigger below (SECURITY DEFINER) and the
--     service role; clients can SELECT their own rows and mark read_at
--     but cannot insert (a user must not be able to fabricate alerts
--     attributed to someone else). ai/notify.py --kind inbox pushes
--     unpushed rows to that user's devices and stamps push_sent_at.
--
--  3. forum_messages.reply_to + a rebuilt send_forum_message — quoting a
--     message turns a flat chat into actual replies. The rebuild keeps
--     EVERY hardening guarantee from supabase-security-migration.sql
--     (sanitization, 1000-char cap, 6 msg/60s rate limit, single write
--     path) and only widens the signature:
--         send_forum_message(p_message text, p_reply_to uuid default null)
--     That file creates the SAME signature so running the files in any
--     order converges on one function (the drop-if-exists clears legacy
--     overloads — PostgREST cannot resolve ambiguous overloads).
--
--  4. Trigger: on every new forum message, notify the parent author of a
--     reply and every @mentioned user. Mentions match either handle
--     usernames (@theirname) or names with spaces compacted
--     (@TestSubjectTwo matches the profile username "Test Subject Two").
--     Self-notifications are skipped and one message can notify a user
--     at most once.
--
-- HONEST LIMITS:
--   * Push delivery still requires the push_subscriptions table (section
--     1 of the pending runner) + the VAPID_PRIVATE_KEY repo secret;
--     without them notifications accumulate unread but nothing breaks.
--   * There is no in-app bell UI yet — rows are queryable and pushed;
--     read_at is reserved for that future screen.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

-- ─────────────────────────────────────────────────────────────────────────
-- 1. user_favorites — server-side copy of the device bookmarks
-- ─────────────────────────────────────────────────────────────────────────

create table if not exists public.user_favorites (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users (id) on delete cascade,
  prediction_id text not null,
  created_at    timestamptz not null default now(),
  unique (user_id, prediction_id)
);

create index if not exists user_favorites_user_idx
  on public.user_favorites (user_id);

alter table public.user_favorites enable row level security;

drop policy if exists "Users can view their own favorites" on public.user_favorites;
create policy "Users can view their own favorites"
  on public.user_favorites for select
  using (auth.uid() = user_id);

drop policy if exists "Users can add their own favorites" on public.user_favorites;
create policy "Users can add their own favorites"
  on public.user_favorites for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can remove their own favorites" on public.user_favorites;
create policy "Users can remove their own favorites"
  on public.user_favorites for delete
  using (auth.uid() = user_id);

-- No UPDATE policy on purpose: a bookmark is (user_id, prediction_id) —
-- there is nothing to update, and the client upserts with ON CONFLICT
-- DO NOTHING (insert-only), which the INSERT policy covers.

-- ─────────────────────────────────────────────────────────────────────────
-- 2. notifications — the inbox (trigger-written, cron-pushed)
-- ─────────────────────────────────────────────────────────────────────────

create table if not exists public.notifications (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users (id) on delete cascade,
  kind          text not null check (kind in
                  ('forum_reply', 'forum_mention', 'bookmark_live', 'odds_change')),
  title         text not null,
  body          text not null default '',
  url           text not null default '/',
  data          jsonb,
  push_sent_at  timestamptz,
  read_at       timestamptz,
  created_at    timestamptz not null default now()
);

create index if not exists notifications_user_idx
  on public.notifications (user_id, created_at desc);
create index if not exists notifications_unpushed_idx
  on public.notifications (created_at)
  where push_sent_at is null;

alter table public.notifications enable row level security;

drop policy if exists "Users can view their own notifications" on public.notifications;
create policy "Users can view their own notifications"
  on public.notifications for select
  using (auth.uid() = user_id);

drop policy if exists "Users can mark their own notifications read" on public.notifications;
create policy "Users can mark their own notifications read"
  on public.notifications for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- No INSERT policy: rows come from the trigger (SECURITY DEFINER) or the
-- service role. No DELETE policy: retention is a backend concern.

-- ─────────────────────────────────────────────────────────────────────────
-- 3. forum replies: column + hardened RPC (signature swap, any-order safe)
-- ─────────────────────────────────────────────────────────────────────────

alter table public.forum_messages add column if not exists reply_to uuid;

create index if not exists forum_messages_reply_idx
  on public.forum_messages (reply_to)
  where reply_to is not null;

-- Clear legacy 1-argument overloads (ambiguous for PostgREST), then the
-- single authoritative definition. Identical to the one in
-- supabase-security-migration.sql except for p_reply_to.
drop function if exists public.send_forum_message(text);

create or replace function public.send_forum_message(
  p_message  text,
  p_reply_to uuid default null
)
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

  -- A quote whose target no longer exists degrades to a plain message
  -- instead of failing the send.
  if p_reply_to is not null and not exists (
    select 1 from public.forum_messages where id = p_reply_to
  ) then
    p_reply_to := null;
  end if;

  select coalesce(display_name, username, email) into v_username
  from public.user_profiles
  where id = auth.uid()
  limit 1;

  insert into public.forum_messages (user_id, username, message, reply_to, created_at)
  values (auth.uid(), coalesce(v_username, 'User'), v_clean, p_reply_to, now());
end;
$$;

grant execute on function public.send_forum_message(text, uuid) to authenticated;

-- ─────────────────────────────────────────────────────────────────────────
-- 4. Reply + mention notifications (fires on every insert, inserts nothing
--    for messages that neither reply nor mention anyone)
-- ─────────────────────────────────────────────────────────────────────────

create or replace function public.notify_forum_participants()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_sender      uuid := new.user_id;
  v_sender_name text;
  v_parent      uuid;
  v_mention     text;
  v_target      uuid;
begin
  select coalesce(display_name, username, 'Someone')
    into v_sender_name
  from public.user_profiles
  where id = v_sender
  limit 1;
  v_sender_name := left(coalesce(v_sender_name, 'Someone'), 60);

  -- Reply → the author of the quoted message.
  if new.reply_to is not null then
    select user_id into v_parent
    from public.forum_messages
    where id = new.reply_to;

    if v_parent is not null and v_parent <> v_sender then
      insert into public.notifications (user_id, kind, title, body, data)
      values (
        v_parent, 'forum_reply', 'New reply to your message',
        left(v_sender_name || ' replied to you', 300),
        jsonb_build_object('message_id', new.id, 'from', v_sender,
                           'from_name', v_sender_name)
      );
    end if;
  end if;

  -- Mentions: @handle, or @CompactedName for usernames containing spaces.
  -- The NOT EXISTS guard means at most one notification per (user, message)
  -- even if the reply trigger already fired for the same row.
  for v_mention in
    select distinct lower(m[1])
    from regexp_matches(coalesce(new.message, ''),
                        '@([A-Za-z0-9_]{3,30})', 'g') as seq(m)
  loop
    select id into v_target
    from public.user_profiles
    where lower(coalesce(username, '')) = v_mention
       or lower(regexp_replace(coalesce(username, ''), '\s+', '', 'g')) = v_mention
    limit 1;

    if v_target is not null and v_target <> v_sender then
      insert into public.notifications (user_id, kind, title, body, data)
      select v_target, 'forum_mention', 'You were mentioned',
             left(v_sender_name || ' mentioned you in the community', 300),
             jsonb_build_object('message_id', new.id, 'from', v_sender,
                                'from_name', v_sender_name)
      where not exists (
        select 1 from public.notifications n
        where n.user_id = v_target
          and n.data ->> 'message_id' = new.id::text
      );
    end if;
  end loop;

  return new;
end;
$$;

drop trigger if exists forum_notifications_ins on public.forum_messages;
create trigger forum_notifications_ins
  after insert on public.forum_messages
  for each row execute function public.notify_forum_participants();

commit;

-- ═══════════════════════════════════════════════════════════════════════════
-- VERIFICATION (run after the migration)
-- ═══════════════════════════════════════════════════════════════════════════
-- 1) Exactly one send_forum_message overload, two arguments:
--    select proname, pg_get_function_identity_arguments(oid)
--    from pg_proc where proname = 'send_forum_message';
--    -- expect: one row, "p_message text, p_reply_to uuid"
--
-- 2) Tables + RLS:
--    select tablename, rowsecurity from pg_tables
--    where schemaname='public' and tablename in
--      ('user_favorites','notifications');
--    -- expect: rowsecurity = true for both
--
-- 3) Trigger exists:
--    select tgname from pg_trigger where tgname='forum_notifications_ins';
--
-- 4) End-to-end: as a second user, send a message mentioning your username
--    (e.g. "@yourname hi") →
--    select kind, title, body from notifications order by created_at desc limit 1;
--    -- expect: forum_mention row addressed to you.
-- ═══════════════════════════════════════════════════════════════════════════

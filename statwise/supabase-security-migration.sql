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

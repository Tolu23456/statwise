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

-- ═══════════════════════════════════════════════════════════════════════════
-- supabase-native-push-migration.sql — native (Expo) push tokens
--
-- Created for the PWA → Android conversion of statwise/ (2026-10-29): the app
-- now registers an EXPO PUSH TOKEN per install instead of a browser
-- service-worker subscription.  ai/notify.py reads this table
-- (fetch_native_tokens) and forwards every payload to Expo's push API
-- (https://exp.host/--/api/v2/push/send) with the SAME audience/tier gating
-- it uses for web push.
--
-- IDEMPOTENT: every statement is guarded — paste the whole file into the
-- Supabase SQL editor and run it; re-running is safe.
--
-- Operational checklist (outside SQL):
--   1. Run this file against the live project (project id in
--      statwise/lib/supabase.ts: fwpriiugfjhtoymcbmso).
--   2. Supabase Dashboard → Authentication → URL Configuration → Redirect
--      URLs: add `statwise://login-callback` (native Google sign-in returns
--      there; see app/(auth)/login.tsx) plus the site origin for web.
--   3. Optional: set the EXPO_ACCESS_TOKEN repository secret (raises Expo's
--      push rate limit; anonymous push works without it) and SITE_BASE_URL
--      (ai/notify.py uses it to absolutise data.url — default
--      https://statwise.vercel.app must match your real domain).
--   4. The app's Profile → Notifications toggle writes a row here; until
--      steps 1 runs it fails with a "run supabase-native-push-migration"
--      message (harmless, nothing else breaks).
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists public.push_tokens (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  -- Expo push token, e.g. `ExpoPushToken[xxxx]` (validated client-side by
  -- isExpoToken() in statwise/lib/pushCore.ts).
  expo_token  text not null,
  -- 'android' | 'ios' | 'web' (web kept as a value only so stray rows from
  -- the old PWA era remain readable; nothing writes it any more).
  platform    text not null default 'android',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  -- The upsert the app issues is `on_conflict=user_id,expo_token`; a second
  -- install for the same user is a SECOND row (that is intended: one row
  -- per device).
  constraint push_tokens_user_token_key unique (user_id, expo_token)
);

-- Lookups: own-row reads/deletes by user, purge by token (notify.py
-- purge_dead_tokens deletes `expo_token in (…)` in chunks of 100).
create index if not exists push_tokens_user_id_idx   on public.push_tokens (user_id);
create index if not exists push_tokens_expo_token_idx on public.push_tokens (expo_token);
create index if not exists push_tokens_updated_at_idx on public.push_tokens (updated_at);

-- ── Row Level Security ─────────────────────────────────────────────────────
-- Tokens are readable by the OWNER and by the service role only: the anon
-- key must never list devices (the file also revokes anon explicitly, so a
-- future `grant default privileges` cannot quietly reopen it).
alter table public.push_tokens enable row level security;

do $$
begin
  -- SELECT: own rows (the Profile screen reads nothing back today, but the
  -- "this device is registered" state may; keep it owner-only).
  drop policy if exists "push_tokens_select_own" on public.push_tokens;
  create policy "push_tokens_select_own"
    on public.push_tokens for select
    to authenticated
    using (auth.uid() = user_id);

  -- INSERT: only your own rows. user_id can never point at someone else's
  -- account (a push token row costs nothing to mint but could otherwise be
  -- used to ATTACH a device to a victim's notifications).
  drop policy if exists "push_tokens_insert_own" on public.push_tokens;
  create policy "push_tokens_insert_own"
    on public.push_tokens for insert
    to authenticated
    with check (auth.uid() = user_id);

  -- UPDATE: the app upserts (conflict on user_id,expo_token → update path),
  -- so an owner may refresh platform/updated_at on their own rows only.
  drop policy if exists "push_tokens_update_own" on public.push_tokens;
  create policy "push_tokens_update_own"
    on public.push_tokens for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

  -- DELETE: unregisterPush() deletes by token without a user filter
  -- (statwise/lib/push.ts); the USING clause still confines it to the
  -- caller's own rows. Service role purges dead tokens bypassing RLS.
  drop policy if exists "push_tokens_delete_own" on public.push_tokens;
  create policy "push_tokens_delete_own"
    on public.push_tokens for delete
    to authenticated
    using (auth.uid() = user_id);
end
$$;

revoke all on public.push_tokens from anon;

-- Grant only what PostgREST needs for authenticated clients. (Listed here so
-- it can be audited: SELECT/INSERT/UPDATE for the upsert + read path; DELETE
-- for unregisterPush. No sequence grant is required — id is a uuid.)
grant select, insert, update, delete on public.push_tokens to authenticated;

comment on table public.push_tokens is
  'One row per Android install: Expo push token registered by statwise/ (lib/push.ts registerForPush). Read by ai/notify.py fetch_native_tokens() and forwarded to the Expo push API with the same tier/audience gating as web push. Tokens Expo reports as DeviceNotRegistered are purged there.';

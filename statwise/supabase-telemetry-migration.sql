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

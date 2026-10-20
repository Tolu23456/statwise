-- ═══════════════════════════════════════════════════════════════════════════
-- StatWise — tier-gated Row Level Security for public.predictions
--
-- WHY: supabase-migration-predictions.sql used to create a
--   CREATE POLICY "Public read predictions" ... USING (true)
-- which lets ANY holder of the anon key (i.e. every free user, and anyone
-- reading the bundle) select premium/vip rows straight from the REST API.
-- The app filters client-side, but the data itself must be gated.
--
-- WHAT: removes the public-read policy and installs a single tier-based one:
--   Free Tier     -> free
--   Premium Tier  -> free, premium
--   VIP Tier      -> free, premium, vip
--   VVIP Tier     -> everything
--
-- Run once in the Supabase SQL editor (or `supabase db execute`).
-- Safe to re-run.
-- ═══════════════════════════════════════════════════════════════════════════

begin;

alter table public.predictions enable row level security;

drop policy if exists "Public read predictions"              on public.predictions;
drop policy if exists "Free users can view free predictions" on public.predictions;
drop policy if exists "Tier-gated prediction access"         on public.predictions;

create policy "Tier-gated prediction access"
  on public.predictions
  for select
  using (
    predictions.tier = 'free'
    or exists (
      select 1
      from public.user_profiles p
      where p.id = auth.uid()
        and case p.current_tier
              when 'VVIP Tier'    then true
              when 'VIP Tier'     then predictions.tier in ('free', 'premium', 'vip')
              when 'Premium Tier' then predictions.tier in ('free', 'premium')
              else false
            end
    )
  );

-- Row access is enforced by the policy above; grants only make the table
-- addressable. Service-role writes keep working (service role bypasses RLS).
grant select on public.predictions to anon, authenticated;

commit;

-- ── Verification (run after the migration) ─────────────────────────────────
-- With the ANON key (no user session) this must only ever return free rows:
--   curl "$SUPABASE_URL/rest/v1/predictions?select=tier" -H "apikey: $ANON_KEY"

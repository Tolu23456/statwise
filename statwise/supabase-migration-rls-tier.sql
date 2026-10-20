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

-- Policies are OR'ed in Postgres: a single permissive policy (e.g. the
-- "Allow system operations on predictions" FOR ALL USING (true) created by
-- supabase-schema.sql) exposes every row no matter what else exists.
-- Drop EVERY policy on the table, then install the one tier policy.
do $$
declare pol record;
begin
  for pol in
    select policyname
    from pg_policies
    where schemaname = 'public'
      and tablename  = 'predictions'
      and policyname <> 'Tier-gated prediction access'
  loop
    execute format('drop policy if exists %I on public.predictions', pol.policyname);
  end loop;
end $$;

drop policy if exists "Tier-gated prediction access" on public.predictions;

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

-- ── Verification — run these two statements in the SQL editor ──────────────
-- Simulates the anon key (no signed-in user): must return ONE row, tier=free.
begin;
  set local role anon;
  select tier, count(*) from public.predictions group by tier order by tier;
rollback;

-- From a shell, with the ANON key, this must only ever return free rows:
--   curl "$SUPABASE_URL/rest/v1/predictions?select=tier" -H "apikey: $ANON_KEY"

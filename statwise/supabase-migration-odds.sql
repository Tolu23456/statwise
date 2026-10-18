-- ═══════════════════════════════════════════════════════════════════════════
-- Odds snapshots + in-play states (The Odds API sync)
-- Run this in the Supabase SQL editor (same as previous migrations).
-- ═══════════════════════════════════════════════════════════════════════════

create table if not exists public.odds_snapshots (
    id            bigint generated always as identity primary key,
    match_id      text        not null,
    market        text        not null default 'h2h',      -- h2h | totals
    live          boolean     not null default false,      -- in-play snapshot
    commence_time timestamptz,
    taken_at      timestamptz not null default now(),
    bookmakers    int         not null default 0,
    odds          jsonb,       -- de-vigged consensus odds  {home,draw,away}
    best_odds     jsonb,       -- best price per outcome    {home,draw,away}
    probs         jsonb,       -- de-vigged consensus probs {home,draw,away}
    totals        jsonb,       -- {over,under,over_odds,under_odds} when market=totals
    sport_key     text,
    source        text        not null default 'the-odds-api'
);

create index if not exists odds_snapshots_match_idx
    on public.odds_snapshots (match_id, taken_at desc);
create index if not exists odds_snapshots_time_idx
    on public.odds_snapshots (taken_at desc);

-- In-play state: score, minute, model in-play probs, live market probs
create table if not exists public.inplay_states (
    id            bigint generated always as identity primary key,
    match_id      text        not null,
    minute        real,
    score_home    int,
    score_away    int,
    model_probs   jsonb,       -- conditional-Poisson probs {home,draw,away}
    market_probs  jsonb,       -- live de-vigged market      {home,draw,away}
    final_probs   jsonb,       -- blended final              {home,draw,away}
    lambda_home   real,
    lambda_away   real,
    edge          jsonb,       -- final - market per outcome
    taken_at      timestamptz not null default now()
);

create index if not exists inplay_states_match_idx
    on public.inplay_states (match_id, taken_at desc);

-- Public read (app uses anon key), service-role write (sync jobs)
alter table public.odds_snapshots enable row level security;
alter table public.inplay_states   enable row level security;

do $$
begin
    if not exists (select 1 from pg_policies where policyname = 'odds_snapshots_public_read' and tablename = 'odds_snapshots') then
        create policy odds_snapshots_public_read on public.odds_snapshots
            for select using (true);
    end if;
    if not exists (select 1 from pg_policies where policyname = 'inplay_states_public_read' and tablename = 'inplay_states') then
        create policy inplay_states_public_read on public.inplay_states
            for select using (true);
    end if;
end $$;

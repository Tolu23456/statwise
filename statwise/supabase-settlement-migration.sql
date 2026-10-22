-- =====================================================================
-- StatWise — Schema Completion Migration
-- =====================================================================
-- The Python backend (ai/model/predictor.py, ai/odds_sync.py) already
-- writes to these tables/columns, but they do not exist in the database yet.
-- That means:
--   * predictions are never settled  → the "Prediction Accuracy" screen
--     in the app is always empty
--   * the Odds Sync workflow crashes   → no real market odds ever reach
--     the model, so there is no "value edge" to show on a card
--
-- HOW TO RUN
--   1. Supabase dashboard → SQL Editor → New query
--   2. Paste everything below and click "Run"
--   3. Everything picks it up immediately — no app rebuild needed
--
-- This script is idempotent — running it twice is safe.
-- =====================================================================


-- ── 1. Settlement columns on `predictions` ───────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'predictions' AND column_name = 'actual_result'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN actual_result TEXT;
    COMMENT ON COLUMN public.predictions.actual_result IS
      'Settled outcome: ''Home Win'' | ''Draw'' | ''Away Win''. NULL until the match finishes.';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'predictions' AND column_name = 'home_score'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN home_score INTEGER;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'predictions' AND column_name = 'away_score'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN away_score INTEGER;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'predictions' AND column_name = 'settled_at'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN settled_at TIMESTAMPTZ;
  END IF;
END
$$;


-- ── 2. Pre-match odds snapshots ──────────────────────────────────────
-- Written by ai/odds_sync.py (prematch mode). Gives the app a real
-- market price to compare Pico's probability against ("value" badge).
CREATE TABLE IF NOT EXISTS public.odds_snapshots (
  id              BIGSERIAL PRIMARY KEY,
  match_id        TEXT,
  market          TEXT        NOT NULL DEFAULT 'h2h',
  live            BOOLEAN     NOT NULL DEFAULT FALSE,
  commence_time   TIMESTAMPTZ,
  bookmakers      JSONB,
  odds            JSONB,
  best_odds       JSONB,
  probs           JSONB,
  totals          JSONB,
  sport_key       TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Tables can already exist as bare stubs (id + match_id only). CREATE TABLE
-- IF NOT EXISTS does nothing in that case, so add any column that is missing.
DO $$
DECLARE
  col RECORD;
BEGIN
  FOR col IN
    SELECT * FROM (VALUES
      ('market',        'TEXT'),
      ('live',          'BOOLEAN NOT NULL DEFAULT FALSE'),
      ('commence_time', 'TIMESTAMPTZ'),
      ('bookmakers',    'JSONB'),
      ('odds',          'JSONB'),
      ('best_odds',     'JSONB'),
      ('probs',         'JSONB'),
      ('totals',        'JSONB'),
      ('sport_key',     'TEXT'),
      ('created_at',    'TIMESTAMPTZ NOT NULL DEFAULT NOW()')
    ) AS c(name, type)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'odds_snapshots'
        AND column_name = col.name
    ) THEN
      EXECUTE format('ALTER TABLE public.odds_snapshots ADD COLUMN %I %s',
                     col.name, col.type);
    END IF;
  END LOOP;
END
$$;

CREATE INDEX IF NOT EXISTS odds_snapshots_match_idx
  ON public.odds_snapshots (match_id, created_at DESC);

CREATE INDEX IF NOT EXISTS odds_snapshots_commence_idx
  ON public.odds_snapshots (commence_time DESC);

CREATE INDEX IF NOT EXISTS odds_snapshots_sport_idx
  ON public.odds_snapshots (sport_key, created_at DESC);


-- ── 3. In-play state snapshots ───────────────────────────────────────
-- Written by ai/odds_sync.py (inplay mode).
CREATE TABLE IF NOT EXISTS public.inplay_states (
  id            BIGSERIAL PRIMARY KEY,
  match_id      TEXT,
  minute        DOUBLE PRECISION,
  score_home    INTEGER,
  score_away    INTEGER,
  model_probs   JSONB,
  market_probs  JSONB,
  final_probs   JSONB,
  edge          JSONB,
  lambda_home   DOUBLE PRECISION,
  lambda_away   DOUBLE PRECISION,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Same stub-table safeguard as odds_snapshots above.
DO $$
DECLARE
  col RECORD;
BEGIN
  FOR col IN
    SELECT * FROM (VALUES
      ('minute',        'DOUBLE PRECISION'),
      ('score_home',    'INTEGER'),
      ('score_away',    'INTEGER'),
      ('model_probs',   'JSONB'),
      ('market_probs',  'JSONB'),
      ('final_probs',   'JSONB'),
      ('edge',          'JSONB'),
      ('lambda_home',   'DOUBLE PRECISION'),
      ('lambda_away',   'DOUBLE PRECISION'),
      ('created_at',    'TIMESTAMPTZ NOT NULL DEFAULT NOW()')
    ) AS c(name, type)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'inplay_states'
        AND column_name = col.name
    ) THEN
      EXECUTE format('ALTER TABLE public.inplay_states ADD COLUMN %I %s',
                     col.name, col.type);
    END IF;
  END LOOP;
END
$$;

CREATE INDEX IF NOT EXISTS inplay_states_match_idx
  ON public.inplay_states (match_id, created_at DESC);


-- ── 4. Helpful indexes for the accuracy screen ───────────────────────
CREATE INDEX IF NOT EXISTS predictions_settled_idx
  ON public.predictions (match_date DESC)
  WHERE actual_result IS NOT NULL;

CREATE INDEX IF NOT EXISTS predictions_settled_tier_idx
  ON public.predictions (tier, match_date DESC)
  WHERE actual_result IS NOT NULL;


-- ── 5. Verify ────────────────────────────────────────────────────────
DO $$
DECLARE
  missing_cols TEXT;
  missing_tbls TEXT;
  missing_snap TEXT;
  missing_live TEXT;
BEGIN
  SELECT string_agg(c, ', ')
    INTO missing_cols
  FROM unnest(ARRAY['actual_result','home_score','away_score','settled_at']) AS c
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'predictions' AND column_name = c
  );

  SELECT string_agg(t, ', ')
    INTO missing_tbls
  FROM unnest(ARRAY['odds_snapshots','inplay_states']) AS t
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = t
  );

  SELECT string_agg(c, ', ')
    INTO missing_snap
  FROM unnest(ARRAY['market','live','commence_time','bookmakers','odds',
                    'best_odds','probs','totals','sport_key','created_at']) AS c
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'odds_snapshots' AND column_name = c
  );

  SELECT string_agg(c, ', ')
    INTO missing_live
  FROM unnest(ARRAY['minute','score_home','score_away','model_probs',
                    'market_probs','final_probs','edge','created_at']) AS c
  WHERE NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'inplay_states' AND column_name = c
  );

  IF missing_cols IS NULL AND missing_tbls IS NULL
     AND missing_snap IS NULL AND missing_live IS NULL THEN
    RAISE NOTICE 'StatWise schema migration complete — everything is in place.';
  ELSE
    RAISE EXCEPTION 'Migration incomplete. missing columns: % | missing tables: % | odds_snapshots: % | inplay_states: %',
      COALESCE(missing_cols, 'none'), COALESCE(missing_tbls, 'none'),
      COALESCE(missing_snap, 'none'), COALESCE(missing_live, 'none');
  END IF;
END
$$;

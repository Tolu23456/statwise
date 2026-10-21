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

CREATE INDEX IF NOT EXISTS odds_snapshots_match_idx
  ON public.odds_snapshots (match_id, created_at DESC);

CREATE INDEX IF NOT EXISTS odds_snapshots_commence_idx
  ON public.odds_snapshots (commence_time DESC);


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

  IF missing_cols IS NULL AND missing_tbls IS NULL THEN
    RAISE NOTICE 'StatWise schema migration complete — everything is in place.';
  ELSE
    RAISE EXCEPTION 'Migration incomplete. missing columns: % | missing tables: %',
      COALESCE(missing_cols, 'none'), COALESCE(missing_tbls, 'none');
  END IF;
END
$$;

-- ═══════════════════════════════════════════════════════════════════════════
-- StatWise – Supabase migration for the new project (fwpriiugfjhtoymcbmso)
-- Run this in:  Dashboard → SQL Editor → New query → Run
--
-- Safe to re-run: every statement checks existence first.
-- What it does:
--   1. predictions – adds the columns the AI backend, GitHub Actions fetcher
--      and the app screens need (match_title, league_slug, match_date, status,
--      tier_required, stats, actual_result, scores, settled_at, 1X2 odds)
--   2. guarantees the UNIQUE(match_id) constraint the upserts depend on
--   3. indexes for the app's main queries (today's list, league filter, status)
--   4. RLS: public read for predictions (writes go through the service role)
--   5. realtime for predictions (the app live-refreshes the list)
--   6. updated_at auto-touch trigger
--   7. user_profiles – any missing profile columns
--   8. public 'app-assets' storage bucket (logo etc.)
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. predictions: missing columns ─────────────────────────────────────────
ALTER TABLE predictions
  ADD COLUMN IF NOT EXISTS match_title   VARCHAR,
  ADD COLUMN IF NOT EXISTS league_slug   VARCHAR,
  ADD COLUMN IF NOT EXISTS match_date    DATE,
  ADD COLUMN IF NOT EXISTS tier_required VARCHAR DEFAULT 'Free Tier',
  ADD COLUMN IF NOT EXISTS status        VARCHAR DEFAULT 'upcoming',
  ADD COLUMN IF NOT EXISTS stats         JSONB,
  ADD COLUMN IF NOT EXISTS actual_result VARCHAR,
  ADD COLUMN IF NOT EXISTS home_score    INTEGER,
  ADD COLUMN IF NOT EXISTS away_score    INTEGER,
  ADD COLUMN IF NOT EXISTS settled_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS odds_home     DECIMAL(6,3),
  ADD COLUMN IF NOT EXISTS odds_draw     DECIMAL(6,3),
  ADD COLUMN IF NOT EXISTS odds_away     DECIMAL(6,3);

-- ── 2. UNIQUE(match_id) so .upsert(on_conflict: 'match_id') works ───────────
-- (table is currently empty, so this cannot fail on duplicates)
ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_match_id_key;
ALTER TABLE predictions
  ADD CONSTRAINT predictions_match_id_key UNIQUE (match_id);

-- ── 3. indexes for the app's queries ────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_predictions_match_date  ON predictions (match_date);
CREATE INDEX IF NOT EXISTS idx_predictions_league_slug ON predictions (league_slug);
CREATE INDEX IF NOT EXISTS idx_predictions_status      ON predictions (status);
CREATE INDEX IF NOT EXISTS idx_predictions_kickoff     ON predictions (kickoff_time);

-- ── 4. RLS: tier-gated reads ────────────────────────────────────────────────
-- Free users must never see premium/vip rows.  A USING (true) policy here
-- exposes the whole table to every anon-key holder (the app's client-side
-- filter is not a security boundary).  See supabase-migration-rls-tier.sql.
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public read predictions"              ON predictions;
DROP POLICY IF EXISTS "Free users can view free predictions" ON predictions;
DROP POLICY IF EXISTS "Tier-gated prediction access"         ON predictions;

CREATE POLICY "Tier-gated prediction access"
  ON predictions FOR SELECT
  USING (
    predictions.tier = 'free'
    OR EXISTS (
      SELECT 1 FROM public.user_profiles p
      WHERE p.id = auth.uid()
        AND CASE p.current_tier
              WHEN 'VVIP Tier'    THEN true
              WHEN 'VIP Tier'     THEN predictions.tier IN ('free', 'premium', 'vip')
              WHEN 'Premium Tier' THEN predictions.tier IN ('free', 'premium')
              ELSE false
            END
    )
  );

GRANT SELECT ON predictions TO anon, authenticated;

-- ── 5. realtime (app subscribes to postgres_changes on predictions) ─────────
DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE predictions;
EXCEPTION
  WHEN duplicate_object THEN NULL;   -- already a member
  WHEN undefined_object  THEN NULL;  -- publication missing (won't happen on Supabase)
END $$;

-- ── 6. keep updated_at fresh on every UPDATE ────────────────────────────────
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_predictions_updated_at ON predictions;
CREATE TRIGGER trg_predictions_updated_at
  BEFORE UPDATE ON predictions
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ── 7. user_profiles: make sure every column the app reads exists ───────────
ALTER TABLE user_profiles
  ADD COLUMN IF NOT EXISTS tier               VARCHAR DEFAULT 'free',
  ADD COLUMN IF NOT EXISTS current_tier       VARCHAR DEFAULT 'Free Tier',
  ADD COLUMN IF NOT EXISTS subscription_status VARCHAR DEFAULT 'inactive',
  ADD COLUMN IF NOT EXISTS avatar_url         TEXT,
  ADD COLUMN IF NOT EXISTS notifications      BOOLEAN DEFAULT true,
  ADD COLUMN IF NOT EXISTS last_login         TIMESTAMPTZ;

-- ── 8. public storage bucket for app assets (logo) ──────────────────────────
INSERT INTO storage.buckets (id, name, public)
VALUES ('app-assets', 'app-assets', true)
ON CONFLICT (id) DO UPDATE SET public = true;

-- ── verify ──────────────────────────────────────────────────────────────────
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'predictions'
ORDER BY ordinal_position;

-- Migration: Add missing columns to predictions table
-- Run this in your Supabase SQL Editor if you already have the database set up.
-- These columns are required for the GitHub Actions football data fetcher.

ALTER TABLE predictions
  ADD COLUMN IF NOT EXISTS match_title VARCHAR,
  ADD COLUMN IF NOT EXISTS league_slug VARCHAR,
  ADD COLUMN IF NOT EXISTS match_date DATE,
  ADD COLUMN IF NOT EXISTS tier_required VARCHAR DEFAULT 'Free Tier',
  ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'upcoming';

-- Add index for match_date filtering (used by the app's today query)
CREATE INDEX IF NOT EXISTS idx_predictions_match_date ON predictions(match_date);
CREATE INDEX IF NOT EXISTS idx_predictions_league_slug ON predictions(league_slug);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);

-- Make match_id unique so upserts work correctly
ALTER TABLE predictions
  DROP CONSTRAINT IF EXISTS predictions_match_id_key;
ALTER TABLE predictions
  ADD CONSTRAINT predictions_match_id_key UNIQUE (match_id);

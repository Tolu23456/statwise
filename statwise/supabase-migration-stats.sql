-- Add stats column to predictions table for structured match insights
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS stats JSONB;

-- Comment for documentation
COMMENT ON COLUMN predictions.stats IS 'Structured match statistics for display: {h2h: {home_wins, draws, away_wins}, home_form: [float], away_form: [float], avg_goals: float}';

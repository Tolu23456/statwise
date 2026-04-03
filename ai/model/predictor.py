"""
PredictionEngine – loads the trained model and generates predictions for
upcoming matches, then pushes them to Supabase.
Also handles backtesting: settling past predictions with actual results.
"""
from __future__ import annotations
import os, logging, datetime
import pandas as pd
from typing import Optional

from .trainer import FootballPredictor
from .live_fetcher import fetch_upcoming_matches, fetch_match_result
from .downloader import load_training_data

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://pdrcyuzfdqjnsltqqxvr.supabase.co"
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    os.environ.get(
        "SUPABASE_ANON_KEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBkcmN5dXpmZHFqbnNsdHFxeHZyIiwi"
        "cm9sZSI6ImFub24iLCJpYXQiOjE3NzQ4Nzg2NTYsImV4cCI6MjA5MDQ1NDY1Nn0."
        "LNWI2nhJdubyZmYGh1b-60fqzeo-fTenCknXyMwYmw8"
    )
)

TIER_THRESHOLDS = {
    "Free Tier":    (0,   55),
    "Premium Tier": (55,  70),
    "VIP Tier":     (70,  82),
    "VVIP Tier":    (82, 100),
}
TIER_DB = {
    "Free Tier":    "free",
    "Premium Tier": "premium",
    "VIP Tier":     "vip",
    "VVIP Tier":    "vvip",
}


def _tier_for_confidence(confidence: int) -> tuple[str, str]:
    for tier_name, (lo, hi) in TIER_THRESHOLDS.items():
        if lo <= confidence < hi:
            return tier_name, TIER_DB[tier_name]
    return "VVIP Tier", "vvip"


def _get_supabase_client():
    try:
        from supabase import create_client
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        logger.error(f"Cannot create Supabase client: {e}")
        return None


class PredictionEngine:
    def __init__(self, predictor: Optional[FootballPredictor] = None,
                 history: Optional[pd.DataFrame] = None):
        self.predictor = predictor
        self.history   = history if history is not None else pd.DataFrame()
        self._supabase = None

    def _ensure_supabase(self):
        if self._supabase is None:
            self._supabase = _get_supabase_client()
        return self._supabase

    def load_or_train(self, force_retrain: bool = False) -> "PredictionEngine":
        model_path = os.path.join(
            os.path.dirname(__file__), '..', 'models', 'football_predictor.pkl'
        )
        if not force_retrain and os.path.exists(model_path):
            try:
                self.predictor = FootballPredictor.load(model_path)
                logger.info("Loaded existing trained model.")
                return self
            except (ValueError, Exception) as e:
                logger.warning(f"Could not load model ({e}) – retraining…")

        logger.info("Downloading training data…")
        df = load_training_data()
        if df.empty:
            logger.error("No training data available.")
            return self

        self.history = df.copy()
        self.predictor = FootballPredictor()
        try:
            self.predictor.train(df)
            self.predictor.save(model_path)
        except ValueError as e:
            logger.error(f"Training failed: {e}")
            self.predictor = None
        return self

    def run(self, leagues: list[str] | None = None, days_ahead: int = 7) -> list[dict]:
        if self.predictor is None:
            logger.error("No trained model – cannot generate predictions.")
            return []

        fixtures = fetch_upcoming_matches(leagues=leagues, days_ahead=days_ahead)
        if not fixtures:
            logger.warning("No upcoming fixtures found.")
            return []

        results = []
        for fix in fixtures:
            try:
                pred = self.predictor.predict_match(
                    home_team  = fix['home_team'],
                    away_team  = fix['away_team'],
                    league_slug= fix.get('league_slug', 'all'),
                    history    = self.history,
                    odds_home  = fix.get('odds_home'),
                    odds_draw  = fix.get('odds_draw'),
                    odds_away  = fix.get('odds_away'),
                )
                tier_name, tier_db = _tier_for_confidence(pred['confidence'])
                kickoff    = fix.get('kickoff_time', '')
                match_date = kickoff[:10] if kickoff else str(datetime.date.today())

                # Use real odds from API if available, fall back to model suggestion
                odds_val = (fix.get('odds_home') or pred.get('suggested_odds') or 1.90)

                row = {
                    "match_id":      fix['match_id'],
                    "match_title":   fix['match_title'],
                    "home_team":     fix['home_team'],
                    "away_team":     fix['away_team'],
                    "league":        fix.get('league_name', fix.get('league_slug', 'Unknown')),
                    "league_slug":   fix.get('league_slug', 'all'),
                    "prediction":    pred['prediction'],
                    "confidence":    pred['confidence'],
                    "odds":          odds_val,
                    "odds_home":     fix.get('odds_home'),
                    "odds_draw":     fix.get('odds_draw'),
                    "odds_away":     fix.get('odds_away'),
                    "reasoning":     pred['reasoning'],
                    "kickoff_time":  kickoff,
                    "match_date":    match_date,
                    "tier":          tier_db,
                    "tier_required": tier_name,
                    "status":        "upcoming",
                }
                results.append(row)
            except Exception as e:
                logger.warning(f"Prediction failed for {fix.get('match_title', '?')}: {e}")

        logger.info(f"Generated {len(results)} predictions.")
        return results

    # Columns that exist in the Supabase predictions table.
    # odds_home / odds_draw / odds_away are NOT in the schema — strip them.
    _DB_COLUMNS = {
        "match_id", "match_title", "home_team", "away_team",
        "league", "league_slug", "prediction", "confidence",
        "odds", "reasoning", "kickoff_time", "match_date",
        "tier", "tier_required", "status",
        "actual_result", "settled_at",
    }

    def push_to_supabase(self, predictions: list[dict]) -> int:
        if not predictions:
            return 0
        sb = self._ensure_supabase()
        if sb is None:
            logger.error("Supabase client unavailable – predictions not saved.")
            return 0

        saved = 0
        for p in predictions:
            try:
                # Strip any keys not in the schema to avoid PGRST204 errors
                row = {k: v for k, v in p.items() if k in self._DB_COLUMNS}
                sb.table("predictions").upsert(row, on_conflict="match_id").execute()
                saved += 1
            except Exception as e:
                logger.warning(f"Failed to save prediction {p.get('match_id')}: {e}")

        logger.info(f"Saved {saved}/{len(predictions)} predictions to Supabase.")
        return saved

    def settle_past_predictions(self) -> int:
        """
        Fetch past predictions that are still 'upcoming' and try to settle
        them with actual results for backtesting accuracy tracking.
        Returns number of predictions settled.
        """
        sb = self._ensure_supabase()
        if sb is None:
            return 0

        now = datetime.datetime.utcnow().isoformat() + "Z"
        try:
            resp = sb.table("predictions") \
                .select("id, match_id, home_team, away_team, kickoff_time, prediction") \
                .eq("status", "upcoming") \
                .lt("kickoff_time", now) \
                .execute()
            past = resp.data or []
        except Exception as e:
            logger.warning(f"Could not fetch past predictions: {e}")
            return 0

        if not past:
            logger.info("No past predictions to settle.")
            return 0

        logger.info(f"Attempting to settle {len(past)} past predictions…")
        settled = 0
        for p in past:
            result = fetch_match_result(
                match_id     = p.get("match_id", ""),
                home_team    = p.get("home_team", ""),
                away_team    = p.get("away_team", ""),
                kickoff_time = p.get("kickoff_time", ""),
            )
            if result:
                try:
                    update = {
                        "actual_result": result["actual_result"],
                        "home_score":    result.get("home_score"),
                        "away_score":    result.get("away_score"),
                        "status":        "completed",
                    }
                    sb.table("predictions").update(update).eq("id", p["id"]).execute()
                    settled += 1
                except Exception as e:
                    logger.warning(f"Failed to settle prediction {p['id']}: {e}")

        logger.info(f"Settled {settled}/{len(past)} predictions.")
        return settled

    def run_and_push(self, leagues: list[str] | None = None,
                     days_ahead: int = 14) -> int:
        preds = self.run(leagues=leagues, days_ahead=days_ahead)
        return self.push_to_supabase(preds)

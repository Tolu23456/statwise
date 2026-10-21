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
from .live_fetcher import fetch_upcoming_matches, fetch_match_result, _mock_allowed
from .downloader import load_training_data

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://fwpriiugfjhtoymcbmso.supabase.co"
)
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    os.environ.get(
        "SUPABASE_ANON_KEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwi"
        "cm9sZSI6ImFub24iLCJpYXQiOjE3NTY5MDc3MTgsImV4cCI6MjA3MjQ4MzcxOH0."
        "Sy3iehhGx2UNi28UBEjfNd81TjMxOVXJjY9HJS3xVjs"
    )
)

TIER_THRESHOLDS = {
    "Free Tier":    (0,   40),
    "Premium Tier": (40,  55),
    "VIP Tier":     (55,  65),
    "VVIP Tier":    (65,  101),
}

# Certainty at/above which the model is considered genuinely sure -> "AI Pick"
AI_PICK_CERTAINTY = 65.0
TIER_DB = {
    "Free Tier":    "free",
    "Premium Tier": "premium",
    "VIP Tier":     "vip",
    "VVIP Tier":    "vvip",
}


def _tier_for_confidence(confidence: int) -> tuple[str, str]:
    """Map a certainty score (0-100) to a tier."""
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

        # Hard guard: placeholder fixtures must never be published as real
        # predictions. They are only permitted when explicitly opted in.
        if not _mock_allowed():
            fixtures = [f for f in fixtures if not str(f.get('match_id', '')).startswith('mock_')]
            if not fixtures:
                logger.error("Only mock fixtures were returned – refusing to publish.")
                return []
        else:
            logger.warning(
                "ALLOW_MOCK_FIXTURES is set – placeholder fixtures may be published."
            )

        results = []
        for fix in fixtures:
            try:
                # Match-time weather (Open-Meteo forecast) — a live model feature (slots 133-140)
                kickoff    = fix.get('kickoff_time', '')
                match_date = kickoff[:10] if kickoff else str(datetime.date.today())
                weather = None
                try:
                    from .team_venues import resolve_venue, fetch_forecast_weather
                    v = resolve_venue(fix['home_team'], None)
                    if v and v.get("lat") is not None:
                        weather = fetch_forecast_weather(v["lat"], v["lon"], match_date)
                except Exception as we:
                    logger.debug(f"Weather fetch failed for {fix.get('match_title', '?')}: {we}")

                pred = self.predictor.predict_match(
                    home  = fix['home_team'],
                    away  = fix['away_team'],
                    league= fix.get('league_slug', 'all'),
                    history= self.history,
                    # market odds feed BOTH the odds features (has_odds etc.)
                    # and the post-hoc alpha blend with the de-vigged market
                    odds_home=float(fix.get('odds_home') or 0),
                    odds_draw=float(fix.get('odds_draw') or 0),
                    odds_away=float(fix.get('odds_away') or 0),
                    weather= weather,
                )
                uncertainty = pred.get('uncertainty') or {}
                is_uncertain = bool(uncertainty.get('is_uncertain'))
                certainty = float(uncertainty.get('certainty') or 0)
                raw_conf = int(pred.get('confidence', 50))

                # "AI Pick" = model is genuinely sure (high certainty score).
                is_ai_pick = bool(certainty >= AI_PICK_CERTAINTY and not is_uncertain)

                # Tier is driven by certainty, not raw confidence.
                # Uncertain picks are forced down to the free tier.
                tier_score = raw_conf if is_uncertain else max(certainty, raw_conf * 0.6)
                if is_uncertain:
                    tier_score = min(tier_score, 39)
                tier_name, tier_db = _tier_for_confidence(int(round(tier_score)))

                stats = pred.get('stats') or {}
                stats['certainty'] = round(certainty, 1)
                stats['is_ai_pick'] = is_ai_pick
                stats['uncertainty'] = uncertainty
                stats['prob_home'] = pred.get('prob_home')
                stats['prob_draw'] = pred.get('prob_draw')
                stats['prob_away'] = pred.get('prob_away')
                # Value edge vs market: model prob for our pick minus implied prob
                # from the available odds. Positive = the model likes it more
                # than the book does.
                edge = None
                try:
                    oh = float(fix.get('odds_home') or 0)
                    od = float(fix.get('odds_draw') or 0)
                    oa = float(fix.get('odds_away') or 0)
                    if oh > 1.01 and od > 1.01 and oa > 1.01:
                        implied = [1.0 / oh, 1.0 / od, 1.0 / oa]
                        tot = sum(implied)
                        implied = [x / tot for x in implied]
                        pick_idx = {'Home Win': 0, 'Draw': 1, 'Away Win': 2}.get(pred['prediction'], 0)
                        model_prob = float(pred.get('prob_home' if pick_idx == 0 else ('prob_draw' if pick_idx == 1 else 'prob_away')) or 0)
                        if model_prob > 0:
                            edge = round((model_prob / 100.0 - implied[pick_idx]) * 100, 1)
                except Exception:
                    edge = None
                if edge is not None:
                    stats['value_edge'] = edge
                # Keep the raw market odds so the app can show model vs market
                # side-by-side without needing extra columns.
                for k in ('odds_home', 'odds_draw', 'odds_away'):
                    v = fix.get(k)
                    if v:
                        stats[k] = float(v)
                if weather:
                    stats['details'] = {**(stats.get('details') or {}), 'weather': weather}

                row = {
                    "match_id":      fix['match_id'],
                    "match_title":   fix['match_title'],
                    "home_team":     fix['home_team'],
                    "away_team":     fix['away_team'],
                    "league":        fix.get('league_name', fix.get('league_slug', 'Unknown')),
                    "league_slug":   fix.get('league_slug', 'all'),
                    "prediction":    pred['prediction'],
                    "confidence":    pred['confidence'],
                    "odds":          fix.get('odds_home') or 1.90,
                    "reasoning":     pred['reasoning'],
                    "stats":         stats,
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

    _DB_COLUMNS = {
        "match_id", "match_title", "home_team", "away_team",
        "league", "league_slug", "prediction", "confidence",
        "odds", "reasoning", "stats", "kickoff_time", "match_date",
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
                row = {k: v for k, v in p.items() if k in self._DB_COLUMNS}
                sb.table("predictions").upsert(row, on_conflict="match_id").execute()
                saved += 1
            except Exception as e:
                logger.warning(f"Failed to save prediction {p.get('match_id')}: {e}")

        logger.info(f"Saved {saved}/{len(predictions)} predictions to Supabase.")
        return saved

    def settle_past_predictions(self) -> int:
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

#!/usr/bin/env python3
"""
StatWise AI Scheduler
=====================
Runs every 20 minutes:
  1. Fetches upcoming football fixtures (3 sources, key-free fallback)
  2. Generates AI predictions using the trained XGBoost ensemble
  3. Upserts predictions to Supabase (real-time update for the frontend)

Usage:
    python ai/scheduler.py

Environment variables (optional but recommended for live data):
    FOOTBALL_API_KEY   – football-data.org free API key
    X_RAPIDAPI_KEY     – API-Football via RapidAPI key
    SUPABASE_SERVICE_KEY – Supabase service role key (bypasses RLS)
    SUPABASE_URL       – override Supabase URL (defaults to hardcoded)

The scheduler also writes a heartbeat to ai/data/heartbeat.json so the
frontend can detect staleness.
"""
from __future__ import annotations
import os, sys, time, json, logging, datetime, signal, pathlib

# Add parent directory so we can import from ai/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt  = "%Y-%m-%d %H:%M:%S",
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "data", "scheduler.log"),
            mode="a", encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("statwise.scheduler")

os.makedirs(os.path.join(os.path.dirname(__file__), "data"),   exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(__file__), "models"), exist_ok=True)

INTERVAL_SECONDS  = 20 * 60       # 20 minutes – prediction cycle
RETRAIN_SECONDS   = 24 * 60 * 60  # 24 hours   – model retraining
HEARTBEAT_FILE    = os.path.join(os.path.dirname(__file__), "data", "heartbeat.json")


def write_heartbeat(status: str, n_predictions: int = 0, error: str = "",
                    n_leagues: int = 0) -> None:
    data = {
        "timestamp":          datetime.datetime.utcnow().isoformat() + "Z",
        "status":             status,
        "n_predictions":      n_predictions,
        "n_leagues":          n_leagues,
        "error":              error,
        "next_run_in_seconds": INTERVAL_SECONDS,
    }
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not write heartbeat: {e}")


def run_prediction_cycle(engine) -> int:
    """
    One full fetch → predict → push cycle.
    Returns number of predictions saved.
    """
    logger.info("── Starting prediction cycle ──────────────────────────────")
    try:
        preds = engine.run()
        n_leagues = len({p.get('league_slug', '') for p in preds})
        n = engine.push_to_supabase(preds)
        logger.info(f"── Cycle complete: {n} predictions across {n_leagues} leagues ────")
        write_heartbeat("ok", n, n_leagues=n_leagues)
        return n
    except Exception as e:
        logger.error(f"Prediction cycle failed: {e}", exc_info=True)
        write_heartbeat("error", error=str(e))
        return 0


def retrain_engine() -> object:
    """Force-retrain the model on fresh data and return the new engine."""
    from model.predictor import PredictionEngine
    logger.info("═" * 60)
    logger.info(" Starting scheduled model retrain…")
    logger.info("═" * 60)
    engine = PredictionEngine()
    engine.load_or_train(force_retrain=True)
    logger.info("Retrain complete ✓")
    return engine


def build_engine():
    """Build and warm up the prediction engine (load or train model)."""
    from model.predictor import PredictionEngine
    logger.info("Initialising PredictionEngine…")
    engine = PredictionEngine()
    engine.load_or_train(force_retrain=False)
    return engine


_running = True


def _handle_sigterm(signum, frame):
    global _running
    logger.info("Received SIGTERM – shutting down gracefully…")
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT,  _handle_sigterm)

    logger.info("=" * 60)
    logger.info(" StatWise AI Scheduler starting up")
    logger.info(f" Prediction interval : every {INTERVAL_SECONDS // 60} minutes")
    logger.info(f" Model retrain       : every {RETRAIN_SECONDS // 3600} hours")
    logger.info("=" * 60)

    try:
        engine = build_engine()
    except Exception as e:
        logger.error(f"Failed to initialise engine: {e}", exc_info=True)
        write_heartbeat("init_error", error=str(e))
        sys.exit(1)

    # Run immediately on startup
    run_prediction_cycle(engine)

    last_predict = time.monotonic()
    last_retrain = time.monotonic()

    while _running:
        now = time.monotonic()

        # Scheduled retrain (every 24 hours)
        if now - last_retrain >= RETRAIN_SECONDS:
            try:
                engine = retrain_engine()
            except Exception as e:
                logger.error(f"Retrain failed: {e}", exc_info=True)
            last_retrain = time.monotonic()

        # Scheduled prediction cycle (every 20 minutes)
        elif now - last_predict >= INTERVAL_SECONDS:
            run_prediction_cycle(engine)
            last_predict = time.monotonic()

        else:
            # Sleep in 5-second ticks to stay signal-responsive
            time.sleep(5.0)

    logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()

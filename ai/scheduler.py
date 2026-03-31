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

INTERVAL_SECONDS  = 20 * 60   # 20 minutes
HEARTBEAT_FILE    = os.path.join(os.path.dirname(__file__), "data", "heartbeat.json")


def write_heartbeat(status: str, n_predictions: int = 0, error: str = "") -> None:
    data = {
        "timestamp":    datetime.datetime.utcnow().isoformat() + "Z",
        "status":       status,
        "n_predictions": n_predictions,
        "error":        error,
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
        n = engine.run_and_push()
        logger.info(f"── Cycle complete: {n} predictions saved ────────────────")
        write_heartbeat("ok", n)
        return n
    except Exception as e:
        logger.error(f"Prediction cycle failed: {e}", exc_info=True)
        write_heartbeat("error", error=str(e))
        return 0


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
    logger.info(f" Interval: every {INTERVAL_SECONDS // 60} minutes")
    logger.info("=" * 60)

    try:
        engine = build_engine()
    except Exception as e:
        logger.error(f"Failed to initialise engine: {e}", exc_info=True)
        write_heartbeat("init_error", error=str(e))
        sys.exit(1)

    # Run immediately on startup
    run_prediction_cycle(engine)

    last_run = time.monotonic()

    while _running:
        elapsed = time.monotonic() - last_run
        remaining = INTERVAL_SECONDS - elapsed

        if remaining <= 0:
            run_prediction_cycle(engine)
            last_run = time.monotonic()
        else:
            # Sleep in 5-second increments so we respond to signals quickly
            sleep_for = min(5.0, remaining)
            time.sleep(sleep_for)

    logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()

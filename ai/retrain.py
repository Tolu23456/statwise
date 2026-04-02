#!/usr/bin/env python3
"""
Background retraining script.

Downloads data from all 5 open-source football data sources, merges them,
trains the ensemble model, and atomically replaces the .pkl file.

Usage:
    python3 ai/retrain.py [--force]

Can also be imported and called programmatically by the scheduler.
"""
from __future__ import annotations
import os, sys, logging, time, json, datetime, shutil, tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
)
logger = logging.getLogger("statwise.retrain")

MODEL_PATH = os.path.join(BASE_DIR, "models", "football_predictor.pkl")
INFO_PATH  = os.path.join(BASE_DIR, "data",   "model_info.json")
os.makedirs(os.path.join(BASE_DIR, "models"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "data"),   exist_ok=True)


def run(force: bool = False) -> bool:
    """
    Full retrain pipeline. Returns True on success.
    Safe to call from the scheduler — writes to a temp file first,
    then atomically moves it into place so predictions keep working.
    """
    import pandas as pd
    from model.downloader   import load_training_data, SEASONS, LEAGUE_CODES
    from model.open_sources import load_all_open_sources
    from model.trainer      import FootballPredictor

    t0 = time.time()
    logger.info("=" * 60)
    logger.info(" StatWise multi-source retrain starting")
    logger.info("=" * 60)

    # ── Source 1: football-data.co.uk (primary) ───────────────────
    logger.info("[1/5] football-data.co.uk …")
    df1 = load_training_data(seasons=SEASONS[:6], leagues=list(LEAGUE_CODES.keys()))
    logger.info(f"      {len(df1):,} rows")

    # ── Sources 2-5: supplementary open datasets ───────────────────
    logger.info("[2-5] StatsBomb / OpenFootball / FiveThirtyEight / ClubElo …")
    df2 = load_all_open_sources()
    logger.info(f"      {len(df2):,} rows")

    # ── Merge ──────────────────────────────────────────────────────
    frames = [f for f in [df1, df2] if not f.empty]
    if not frames:
        logger.error("No training data available — aborting retrain.")
        return False

    df = pd.concat(frames, ignore_index=True)
    # Remove exact duplicates
    df = df.drop_duplicates(subset=["home_team", "away_team", "date", "home_goals", "away_goals"])
    logger.info(f"Combined dataset: {len(df):,} unique matches")

    # ── Train ──────────────────────────────────────────────────────
    logger.info("Training model (XGBoost + HistGB + TabTransformer neural net) …")
    t1 = time.time()
    predictor = FootballPredictor(use_neural_net=True)
    try:
        predictor.train(df)
    except Exception as e:
        logger.error(f"Training failed: {e}")
        return False
    logger.info(f"Training done in {time.time() - t1:.1f}s")

    # ── Save atomically ────────────────────────────────────────────
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pkl", dir=os.path.dirname(MODEL_PATH))
    os.close(tmp_fd)
    try:
        predictor.save(tmp_path)
        shutil.move(tmp_path, MODEL_PATH)
    except Exception as e:
        logger.error(f"Save failed: {e}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False

    # ── Write model metadata ───────────────────────────────────────
    info = {
        "trained_at":   datetime.datetime.utcnow().isoformat() + "Z",
        "total_matches": int(len(df)),
        "sources": {
            "football_data_co_uk": int(len(df1)),
            "supplementary_open_sources": int(len(df2)),
        },
        "duration_seconds": round(time.time() - t0, 1),
    }
    try:
        with open(INFO_PATH, "w") as f:
            json.dump(info, f, indent=2)
    except Exception:
        pass

    logger.info(f"Model saved to {MODEL_PATH}")
    logger.info(f"Total retrain time: {time.time() - t0:.1f}s")
    return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    success = run(force=args.force)
    sys.exit(0 if success else 1)

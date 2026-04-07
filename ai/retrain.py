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
import os, sys, logging, time, json, datetime, shutil, tempfile, glob

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


def _load_clean_data() -> "pd.DataFrame":
    """Load all matched from ai/data/clean/YYYY_matches.csv (unified 48-col schema)."""
    import pandas as pd
    clean_dir = os.path.join(BASE_DIR, "data", "clean")
    paths = sorted(glob.glob(os.path.join(clean_dir, "????_matches.csv")))
    if not paths:
        return pd.DataFrame()
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, low_memory=False))
        except Exception as e:
            logger.warning(f"Could not read {p}: {e}")
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    for col in ["home_team", "away_team", "home_goals", "away_goals"]:
        if col not in df.columns:
            return pd.DataFrame()
    df = df.dropna(subset=["home_team", "away_team", "home_goals", "away_goals"])
    df["home_goals"] = pd.to_numeric(df["home_goals"], errors="coerce").fillna(0).astype(int)
    df["away_goals"] = pd.to_numeric(df["away_goals"], errors="coerce").fillna(0).astype(int)
    for col in ["odds_home", "odds_draw", "odds_away"]:
        if col not in df.columns:
            df[col] = float("nan")
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("date").reset_index(drop=True)
    # Drop international matches (model is tuned for club football)
    if "is_international" in df.columns:
        df = df[pd.to_numeric(df["is_international"], errors="coerce").fillna(0) == 0]
    # Drop very-low-quality rows
    if "quality_score" in df.columns:
        df = df[pd.to_numeric(df["quality_score"], errors="coerce").fillna(0) >= 10]
    logger.info(f"Clean data: {len(df):,} rows from {len(paths)} year files")
    return df.reset_index(drop=True)


def run(force: bool = False) -> bool:
    """
    Full retrain pipeline. Returns True on success.
    Safe to call from the scheduler — writes to a temp file first,
    then atomically moves it into place so predictions keep working.
    """
    import pandas as pd
    from model.trainer import FootballPredictor

    t0 = time.time()
    logger.info("=" * 60)
    logger.info(" StatWise multi-source retrain starting")
    logger.info("=" * 60)

    # ── Primary: clean CSV pipeline (ai/data/clean/*.csv) ─────────
    logger.info("[1/2] Loading clean match data (ai/data/clean/)…")
    df = _load_clean_data()

    if df.empty:
        # Fallback: old per-source download approach
        logger.warning("Clean data not found — falling back to live download…")
        from model.downloader   import load_training_data, SEASONS, LEAGUE_CODES
        from model.open_sources import load_all_open_sources

        df1 = load_training_data(seasons=SEASONS, leagues=list(LEAGUE_CODES.keys()))
        logger.info(f"      football-data.co.uk: {len(df1):,} rows")
        df2 = load_all_open_sources()
        logger.info(f"      supplementary open sources: {len(df2):,} rows")

        frames = [f for f in [df1, df2] if not f.empty]
        if not frames:
            logger.error("No training data available — aborting retrain.")
            return False
        df = pd.concat(frames, ignore_index=True)

        # Strip ClubElo synthetic rows (fake 1-0 home wins that bias the model)
        before = len(df)
        if "league_slug" in df.columns:
            df = df[df["league_slug"] != "clubelo-reference"]
        if "away_team" in df.columns:
            df = df[df["away_team"] != "Reference"]
        logger.info(f"Dropped {before - len(df):,} synthetic ClubElo rows")

        df = df.drop_duplicates(
            subset=["home_team", "away_team", "date", "home_goals", "away_goals"])
    else:
        logger.info("[2/2] Supplementary live sources skipped (clean data available)")

    logger.info(f"Combined dataset: {len(df):,} unique real matches")

    # ── Train ──────────────────────────────────────────────────────
    logger.info("Training model (5-model deep stacking ensemble: XGB+HGB+ET+RF+NeuralNet→LR) …")
    t1 = time.time()
    predictor = FootballPredictor()
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
        "trained_at":       datetime.datetime.utcnow().isoformat() + "Z",
        "total_matches":    int(len(df)),
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

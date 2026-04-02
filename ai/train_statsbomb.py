"""
Train (or fine-tune) the StatWise prediction model on StatsBomb open data.

StatsBomb free tier covers ~1,800 matches across 25+ competitions including
La Liga (Messi era), Champions League, Women's Super League, and more.

Run:
    python3 ai/train_statsbomb.py

Output:
    ai/models/football_predictor.pkl   — updated model
    ai/data/statsbomb_training.csv     — cached training dataset
    ai/data/model_info.json            — version + training metadata
"""
from __future__ import annotations
import os, sys, json, logging, datetime, warnings
warnings.filterwarnings("ignore")

# ── paths ─────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "models", "football_predictor.pkl")
DATA_DIR   = os.path.join(BASE_DIR, "data")
SB_CSV     = os.path.join(DATA_DIR, "statsbomb_training.csv")
INFO_PATH  = os.path.join(DATA_DIR, "model_info.json")
os.makedirs(os.path.join(BASE_DIR, "models"), exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

sys.path.insert(0, BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("statsbomb_trainer")

import numpy  as np
import pandas as pd

# ── StatsBomb helpers ──────────────────────────────────────────────────────────
try:
    from statsbombpy import sb
    logger.info("statsbombpy loaded.")
except ImportError:
    logger.error("statsbombpy not installed. Run: pip install statsbombpy")
    sys.exit(1)


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning(f"  ↳ skipped: {e}")
        return None


def fetch_statsbomb_data() -> pd.DataFrame:
    """Download all free StatsBomb competitions and return a training DataFrame."""
    logger.info("Fetching StatsBomb competition list…")
    comps = sb.competitions()
    if comps is None or comps.empty:
        raise RuntimeError("No competitions returned from StatsBomb.")

    logger.info(f"Found {len(comps)} competition-seasons.")
    rows = []

    for _, comp in comps.iterrows():
        cid = comp["competition_id"]
        sid = comp["season_id"]
        cname = comp["competition_name"]
        sname = comp["season_name"]

        logger.info(f"  → {cname} {sname} (comp={cid}, season={sid})")
        matches = _safe(sb.matches, competition_id=cid, season_id=sid)
        if matches is None or matches.empty:
            continue

        n = 0
        for _, m in matches.iterrows():
            home_score = m.get("home_score", None)
            away_score = m.get("away_score", None)
            if home_score is None or away_score is None:
                continue
            try:
                home_score, away_score = int(home_score), int(away_score)
            except (ValueError, TypeError):
                continue

            home_team = m.get("home_team", {})
            away_team = m.get("away_team", {})
            if isinstance(home_team, dict):
                home_team = home_team.get("home_team_name", "")
            if isinstance(away_team, dict):
                away_team = away_team.get("away_team_name", "")

            # Determine result
            if home_score > away_score:
                result = "H"
            elif away_score > home_score:
                result = "A"
            else:
                result = "D"

            match_id = m.get("match_id")
            xg_home = xg_away = shots_home = shots_away = None
            possession_home = None

            # Try to get event-level stats (xG, shots, possession)
            events = _safe(sb.events, match_id=match_id)
            if events is not None and not events.empty:
                shots = events[events["type"].str.lower().str.contains("shot", na=False)]
                home_shots = shots[shots["team"] == (home_team if isinstance(home_team, str) else "")]
                away_shots = shots[shots["team"] == (away_team if isinstance(away_team, str) else "")]

                shots_home = len(home_shots)
                shots_away = len(away_shots)

                if "shot_statsbomb_xg" in events.columns:
                    xg_home = home_shots["shot_statsbomb_xg"].sum() if not home_shots.empty else 0.0
                    xg_away = away_shots["shot_statsbomb_xg"].sum() if not away_shots.empty else 0.0

                # Possession estimate from pass counts
                home_passes = len(events[(events["type"].str.lower() == "pass") &
                                          (events["team"] == home_team)])
                away_passes = len(events[(events["type"].str.lower() == "pass") &
                                          (events["team"] == away_team)])
                total_passes = home_passes + away_passes
                if total_passes > 0:
                    possession_home = round(home_passes / total_passes * 100, 1)

            row = {
                "home_team":       str(home_team),
                "away_team":       str(away_team),
                "home_goals":      home_score,
                "away_goals":      away_score,
                "result":          result,
                "league_slug":     cname.lower().replace(" ", "-"),
                "league_name":     cname,
                "season":          str(sname),
                "xg_home":         xg_home,
                "xg_away":         xg_away,
                "shots_home":      shots_home,
                "shots_away":      shots_away,
                "possession_home": possession_home,
            }
            rows.append(row)
            n += 1

        logger.info(f"    ✓ {n} matches collected")

    df = pd.DataFrame(rows)
    logger.info(f"Total StatsBomb rows: {len(df)}")
    return df


def merge_with_existing(sb_df: pd.DataFrame) -> pd.DataFrame:
    """Merge StatsBomb data with existing training data from downloader."""
    try:
        from model.downloader import load_training_data
        existing = load_training_data()
        if not existing.empty:
            logger.info(f"Existing training data: {len(existing)} rows")
            combined = pd.concat([existing, sb_df], ignore_index=True)
            combined = combined.drop_duplicates(
                subset=["home_team", "away_team", "home_goals", "away_goals", "league_slug"],
                keep="last",
            )
            logger.info(f"Combined dataset: {len(combined)} rows")
            return combined
    except Exception as e:
        logger.warning(f"Could not load existing training data: {e}")
    return sb_df


def train_and_save(df: pd.DataFrame):
    """Train the FootballPredictor on the merged dataset and save."""
    from model.trainer import FootballPredictor

    logger.info("Training model on combined dataset…")
    predictor = FootballPredictor()
    predictor.train(df)
    predictor.save(MODEL_PATH)
    logger.info(f"Model saved → {MODEL_PATH}")

    # Write model info metadata
    info = {
        "version": "2.0-statsbomb",
        "trained_at": datetime.datetime.utcnow().isoformat() + "Z",
        "training_rows": len(df),
        "statsbomb_rows": len(df[df["xg_home"].notna()]) if "xg_home" in df.columns else 0,
        "features": [
            "Elo ratings", "Win/draw/loss rates", "Goals scored/conceded",
            "Form (last 5 & 10)", "Head-to-head history", "Market odds",
            "xG (Expected Goals)", "Shots on target", "Possession %",
            "Venue-specific form", "Scoring consistency", "League averages",
        ],
        "leagues_covered": sorted(df["league_slug"].unique().tolist()) if "league_slug" in df else [],
        "algorithms": ["XGBoost", "HistGradientBoosting", "Platt Scaling (calibration)"],
    }
    with open(INFO_PATH, "w") as f:
        json.dump(info, f, indent=2)
    logger.info(f"Model info saved → {INFO_PATH}")
    return info


def main():
    logger.info("═══════════════════════════════════════════════════")
    logger.info("  StatWise × StatsBomb Training Pipeline")
    logger.info("═══════════════════════════════════════════════════")

    # 1. Fetch or load cached StatsBomb data
    if os.path.exists(SB_CSV):
        logger.info(f"Loading cached StatsBomb data from {SB_CSV}…")
        sb_df = pd.read_csv(SB_CSV)
        logger.info(f"Loaded {len(sb_df)} rows from cache.")
    else:
        sb_df = fetch_statsbomb_data()
        sb_df.to_csv(SB_CSV, index=False)
        logger.info(f"StatsBomb data cached → {SB_CSV}")

    if sb_df.empty:
        logger.error("No data collected. Aborting.")
        sys.exit(1)

    # 2. Merge with existing training data
    df = merge_with_existing(sb_df)

    # 3. Train + save model
    info = train_and_save(df)

    logger.info("═══════════════════════════════════════════════════")
    logger.info(f"  Done! {info['training_rows']:,} rows | version {info['version']}")
    logger.info("═══════════════════════════════════════════════════")


if __name__ == "__main__":
    main()

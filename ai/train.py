#!/usr/bin/env python3
"""
One-shot training script.
Run this once to download historical data and train the model.
After this, the scheduler reuses the saved model automatically.

Usage:
    python ai/train.py [--force] [--seasons N] [--leagues league1 league2 ...]

Options:
    --force     Force re-download and re-train even if model exists
    --seasons N Number of past seasons to train on (default: 6)
    --leagues   Specific league slugs to include (default: all major leagues)
"""
from __future__ import annotations
import os, sys, argparse, logging, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
)
logger = logging.getLogger("statwise.train")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the StatWise AI predictor")
    parser.add_argument("--force",   action="store_true", help="Force re-train")
    parser.add_argument("--seasons", type=int, default=6,  help="Seasons to train on")
    parser.add_argument("--leagues", nargs="*", default=None)
    args = parser.parse_args()

    from model.downloader import load_training_data, SEASONS, LEAGUE_CODES
    from model.trainer    import FootballPredictor

    seasons = SEASONS[:args.seasons]
    leagues = args.leagues or list(LEAGUE_CODES.keys())

    logger.info(f"Downloading data for {len(leagues)} leagues × {len(seasons)} seasons…")
    t0 = time.time()
    df = load_training_data(seasons=seasons, leagues=leagues)

    if df.empty:
        logger.error("No data downloaded – check your internet connection.")
        sys.exit(1)

    logger.info(f"Downloaded {len(df):,} matches in {time.time()-t0:.1f}s")

    model_path = os.path.join(os.path.dirname(__file__), "models", "football_predictor.pkl")

    if not args.force and os.path.exists(model_path):
        logger.info(f"Model already exists at {model_path}. Use --force to retrain.")
        return

    logger.info("Starting training pipeline…")
    t1 = time.time()
    predictor = FootballPredictor()
    try:
        predictor.train(df)
    except ValueError as e:
        logger.error(f"Training error: {e}")
        sys.exit(1)

    saved = predictor.save(model_path)
    logger.info(f"Training completed in {time.time()-t1:.1f}s. Model saved to {saved}")

    # Quick sanity-check
    logger.info("Running sanity-check prediction…")
    result = predictor.predict_match(
        "Arsenal", "Chelsea",
        league_slug="premier-league",
        history=df,
        odds_home=2.10, odds_draw=3.40, odds_away=3.20,
    )
    logger.info(
        f"Arsenal vs Chelsea → {result['prediction']} "
        f"(confidence: {result['confidence']}%)"
    )
    logger.info(f"  Win: {result['prob_home']}% | Draw: {result['prob_draw']}% | Loss: {result['prob_away']}%")
    logger.info(f"  Reasoning: {result['reasoning']}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Quick accuracy backtest — uses the last 300 historical matches
(where outcomes are known) to measure prediction accuracy.
Results are written to ai/data/backtest_results.json
"""
from __future__ import annotations
import os, sys, json, logging, datetime
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backtest")

OUT = os.path.join(os.path.dirname(__file__), "data", "backtest_results.json")

def main():
    log.info("Loading training data…")
    from model.downloader import load_training_data
    df = load_training_data()
    log.info(f"  {len(df)} total historical matches")

    # Use last 300 matches as held-out test set (not used in training window)
    test = df.tail(300).copy()
    log.info(f"  Testing on last {len(test)} matches")

    log.info("Loading trained model…")
    from model.trainer import FootballPredictor
    try:
        model = FootballPredictor.load()
    except Exception as e:
        log.error(f"Could not load model: {e}")
        sys.exit(1)

    # Build history = everything BEFORE the test set
    history_df = df.iloc[:-300]

    correct = 0
    home_wins = draw = away_wins = 0
    home_correct = draw_correct = away_correct = 0
    high_conf_correct = high_conf_total = 0
    errors = 0
    results = []

    log.info("Running predictions…")
    for i, (_, row) in enumerate(test.iterrows()):
        if i % 50 == 0:
            log.info(f"  {i}/{len(test)}")
        try:
            match = {
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "league":    row.get("league", "unknown"),
                "date":      str(row.get("date", "")),
            }
            history = history_df[
                (history_df["home_team"].isin([row["home_team"], row["away_team"]])) |
                (history_df["away_team"].isin([row["home_team"], row["away_team"]]))
            ]
            pred = model.predict_match(match, history)

            actual_home = int(row["home_goals"])
            actual_away = int(row["away_goals"])
            if actual_home > actual_away:
                actual = "home"
                home_wins += 1
            elif actual_home == actual_away:
                actual = "draw"
                draw += 1
            else:
                actual = "away"
                away_wins += 1

            predicted = pred["predicted_outcome"]
            conf = pred.get("confidence", 0)
            hit = (predicted == actual)
            if hit:
                correct += 1
                if actual == "home":   home_correct += 1
                elif actual == "draw": draw_correct += 1
                else:                  away_correct += 1

            if conf >= 70:
                high_conf_total += 1
                if hit:
                    high_conf_correct += 1

            results.append({
                "home": match["home_team"], "away": match["away_team"],
                "predicted": predicted, "actual": actual,
                "confidence": conf, "correct": hit,
            })
        except Exception:
            errors += 1

    n = len(results)
    accuracy = correct / n if n else 0
    summary = {
        "tested":           n,
        "errors":           errors,
        "overall_accuracy": round(accuracy * 100, 1),
        "home_accuracy":    round(home_correct / home_wins * 100, 1) if home_wins else 0,
        "draw_accuracy":    round(draw_correct / draw * 100, 1)      if draw      else 0,
        "away_accuracy":    round(away_correct / away_wins * 100, 1) if away_wins else 0,
        "high_conf_accuracy": round(high_conf_correct / high_conf_total * 100, 1) if high_conf_total else 0,
        "high_conf_sample": high_conf_total,
        "timestamp":        datetime.datetime.utcnow().isoformat() + "Z",
    }

    log.info("=" * 50)
    log.info(f"RESULTS  ({n} matches tested)")
    log.info(f"  Overall accuracy  : {summary['overall_accuracy']}%")
    log.info(f"  Home win accuracy : {summary['home_accuracy']}%")
    log.info(f"  Draw accuracy     : {summary['draw_accuracy']}%")
    log.info(f"  Away win accuracy : {summary['away_accuracy']}%")
    log.info(f"  High-conf (>=70%) : {summary['high_conf_accuracy']}%  (n={high_conf_total})")
    log.info("=" * 50)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"summary": summary, "sample": results[:20]}, f, indent=2)
    log.info(f"Full results saved to {OUT}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Lean accuracy backtest — reads one league CSV, tests 50 held-out matches.
Writes results to ai/data/backtest_results.json
"""
from __future__ import annotations
import os, sys, json, logging, datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backtest")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUT      = os.path.join(DATA_DIR, "backtest_results.json")

# Test leagues (use files we already have locally)
TEST_CSVS = [
    "premier-league_2324.csv",
    "la-liga_2324.csv",
    "serie-a_2324.csv",
]

def load_csv(name):
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, usecols=lambda c: c in
        ["Date","HomeTeam","AwayTeam","FTHG","FTAG"])
    df = df.dropna(subset=["HomeTeam","AwayTeam","FTHG","FTAG"])
    df = df.rename(columns={
        "Date":"date","HomeTeam":"home_team","AwayTeam":"away_team",
        "FTHG":"home_goals","FTAG":"away_goals"
    })
    df["home_goals"] = df["home_goals"].astype(int)
    df["away_goals"] = df["away_goals"].astype(int)
    return df

def main():
    log.info("Loading model…")
    from model.trainer import FootballPredictor
    try:
        model = FootballPredictor.load()
    except Exception as e:
        log.error(f"Could not load model: {e}")
        sys.exit(1)
    log.info("Model loaded OK")

    all_results = []

    for csv_name in TEST_CSVS:
        df = load_csv(csv_name)
        if df is None:
            log.warning(f"  {csv_name} not found, skipping")
            continue

        # history = first 80%, test = last 50 matches
        split   = max(0, len(df) - 50)
        history = df.iloc[:split]
        test    = df.iloc[split:]
        league  = csv_name.replace("_2324.csv", "")
        log.info(f"Testing {league}: {len(test)} matches (history={len(history)})")

        for _, row in test.iterrows():
            try:
                home, away = row["home_team"], row["away_team"]
                # limit history to recent team matches to save memory
                team_hist = history[
                    (history["home_team"].isin([home, away])) |
                    (history["away_team"].isin([home, away]))
                ].tail(200)

                pred = model.predict_match(home, away, league, team_hist)

                hg, ag = int(row["home_goals"]), int(row["away_goals"])
                actual = "home" if hg > ag else ("draw" if hg == ag else "away")
                predicted = pred["prediction"]

                all_results.append({
                    "league":     league,
                    "home":       home,
                    "away":       away,
                    "predicted":  predicted,
                    "actual":     actual,
                    "confidence": pred.get("confidence", 0),
                    "correct":    predicted == actual,
                })
            except Exception as ex:
                log.warning(f"  Error on {row.get('home_team')} vs {row.get('away_team')}: {ex}")

    if not all_results:
        log.error("No results — check model and CSV files")
        sys.exit(1)

    # ── Compute metrics ────────────────────────────────────────────
    n      = len(all_results)
    hits   = sum(1 for r in all_results if r["correct"])
    acc    = hits / n

    by_actual = {"home": [0,0], "draw": [0,0], "away": [0,0]}
    hc_hits = hc_total = 0
    for r in all_results:
        a = r["actual"]
        by_actual[a][1] += 1
        if r["correct"]:
            by_actual[a][0] += 1
        if r["confidence"] >= 70:
            hc_total += 1
            if r["correct"]:
                hc_hits += 1

    summary = {
        "tested":             n,
        "overall_accuracy":   round(acc * 100, 1),
        "home_accuracy":      round(by_actual["home"][0] / by_actual["home"][1] * 100, 1) if by_actual["home"][1] else 0,
        "draw_accuracy":      round(by_actual["draw"][0] / by_actual["draw"][1] * 100, 1) if by_actual["draw"][1] else 0,
        "away_accuracy":      round(by_actual["away"][0] / by_actual["away"][1] * 100, 1) if by_actual["away"][1] else 0,
        "high_conf_accuracy": round(hc_hits / hc_total * 100, 1) if hc_total else 0,
        "high_conf_sample":   hc_total,
        "timestamp":          datetime.datetime.utcnow().isoformat() + "Z",
    }

    log.info("=" * 50)
    log.info(f"BACKTEST RESULTS  ({n} matches, {len(TEST_CSVS)} leagues)")
    log.info(f"  Overall accuracy  : {summary['overall_accuracy']}%")
    log.info(f"  Home win accuracy : {summary['home_accuracy']}%")
    log.info(f"  Draw accuracy     : {summary['draw_accuracy']}%")
    log.info(f"  Away win accuracy : {summary['away_accuracy']}%")
    log.info(f"  High-conf (>=70%) : {summary['high_conf_accuracy']}%  (n={hc_total})")
    log.info("=" * 50)

    with open(OUT, "w") as f:
        json.dump({"summary": summary, "sample": all_results[:30]}, f, indent=2)
    log.info(f"Saved to {OUT}")

if __name__ == "__main__":
    main()

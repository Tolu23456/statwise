#!/usr/bin/env python3
"""
StatWise backtest — multi-league, multi-season accuracy evaluation.

Improvements over v1:
  • Tests all available leagues across multiple seasons (not just 3 CSVs)
  • Uses last 20 % of each file as held-out test set (not a fixed 50)
  • Confusion matrix  (predicted × actual)
  • Prediction-distribution report (exposes home-win bias)
  • Calibration by confidence band (50-60, 60-70, 70-80, 80-90, 90+)
  • Per-league accuracy breakdown
  • Flat-stake ROI simulation using implied odds
  • Full structured JSON saved to ai/data/backtest_results.json
"""
from __future__ import annotations
import os, sys, json, logging, datetime, glob
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backtest")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
OUT      = os.path.join(DATA_DIR, "backtest_results.json")

# ── leagues to backtest (slug → display name) ──────────────────────────────
LEAGUES = {
    "premier-league": "Premier League",
    "la-liga":        "La Liga",
    "serie-a":        "Serie A",
    "bundesliga":     "Bundesliga",
    "ligue1":         "Ligue 1",
    "eredivisie":     "Eredivisie",
    "primeira-liga":  "Primeira Liga",
    "super-lig":      "Super Lig",
    "championship":   "Championship",
    "serie-b":        "Serie B",
    "bundesliga-2":   "2. Bundesliga",
    "ligue2":         "Ligue 2",
    "la-liga-2":      "La Liga 2",
    "scottish-prem":  "Scottish Prem",
    "belgian-pro":    "Belgian Pro",
    "greek-super":    "Greek Super",
}

# seasons in ascending age order
SEASONS = ["2122", "2223", "2324"]

# fraction of each CSV used as held-out test set
TEST_FRACTION = 0.20

# ── helpers ────────────────────────────────────────────────────────────────

def load_csv(league_slug: str, season: str) -> pd.DataFrame | None:
    path = os.path.join(DATA_DIR, f"{league_slug}_{season}.csv")
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, usecols=lambda c: c in
            ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
        df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
        df = df.rename(columns={
            "Date": "date", "HomeTeam": "home_team", "AwayTeam": "away_team",
            "FTHG": "home_goals", "FTAG": "away_goals",
        })
        df["home_goals"] = df["home_goals"].astype(int)
        df["away_goals"] = df["away_goals"].astype(int)
        return df if len(df) >= 20 else None
    except Exception:
        return None


def normalize(label: str) -> str:
    """Collapse any label variant to 'home' / 'draw' / 'away'."""
    s = str(label).lower().strip()
    if "home" in s:
        return "home"
    if "away" in s:
        return "away"
    if "draw" in s:
        return "draw"
    return s


def conf_band(conf: float) -> str:
    if conf < 60:
        return "50-59%"
    if conf < 70:
        return "60-69%"
    if conf < 80:
        return "70-79%"
    if conf < 90:
        return "80-89%"
    return "90%+"


def _pct(hits: int, total: int) -> float:
    return round(hits / total * 100, 1) if total else 0.0


# ── main ──────────────────────────────────────────────────────────────────

def main():
    log.info("Loading model…")
    from model.trainer import FootballPredictor
    try:
        model = FootballPredictor.load()
    except Exception as e:
        log.error(f"Could not load model: {e}")
        sys.exit(1)
    log.info("Model loaded OK")

    all_results: list[dict] = []
    league_stats: dict[str, dict] = {}

    # ── iterate leagues × seasons ──────────────────────────────────────────
    for slug, name in LEAGUES.items():
        frames = []
        for season in SEASONS:
            df = load_csv(slug, season)
            if df is not None:
                df["season"] = season
                frames.append(df)

        if not frames:
            log.warning(f"  {name}: no CSV found, skipping")
            continue

        combined  = pd.concat(frames, ignore_index=True)
        n_total   = len(combined)
        n_test    = max(10, int(n_total * TEST_FRACTION))
        split     = n_total - n_test
        history   = combined.iloc[:split].copy()
        test      = combined.iloc[split:].copy()

        log.info(f"Testing {name}: {len(test)} matches  (history={len(history)}, {len(frames)} seasons)")

        league_hits = league_total = 0

        for _, row in test.iterrows():
            try:
                home, away = row["home_team"], row["away_team"]

                team_hist = history[
                    (history["home_team"].isin([home, away])) |
                    (history["away_team"].isin([home, away]))
                ].tail(300)

                pred = model.predict_match(home, away, slug, team_hist)

                hg, ag      = int(row["home_goals"]), int(row["away_goals"])
                actual_raw  = "home" if hg > ag else ("draw" if hg == ag else "away")
                actual      = normalize(actual_raw)
                predicted   = normalize(pred["prediction"])
                conf        = pred.get("confidence", 0)
                correct     = predicted == actual

                all_results.append({
                    "league":     slug,
                    "home":       home,
                    "away":       away,
                    "predicted":  predicted,
                    "actual":     actual,
                    "confidence": conf,
                    "correct":    correct,
                    "prob_home":  pred.get("prob_home", 0),
                    "prob_draw":  pred.get("prob_draw", 0),
                    "prob_away":  pred.get("prob_away", 0),
                })

                league_total += 1
                if correct:
                    league_hits += 1

            except Exception as ex:
                log.warning(f"  Error on {row.get('home_team')} vs {row.get('away_team')}: {ex}")

        if league_total:
            league_stats[slug] = {
                "name":     name,
                "tested":   league_total,
                "accuracy": _pct(league_hits, league_total),
            }

    if not all_results:
        log.error("No results — check model and CSV files")
        sys.exit(1)

    # ── overall accuracy ───────────────────────────────────────────────────
    n    = len(all_results)
    hits = sum(1 for r in all_results if r["correct"])

    # ── per-outcome accuracy ───────────────────────────────────────────────
    by_actual = {"home": [0, 0], "draw": [0, 0], "away": [0, 0]}
    for r in all_results:
        a = r["actual"]
        if a in by_actual:
            by_actual[a][1] += 1
            if r["correct"]:
                by_actual[a][0] += 1

    # ── confusion matrix (actual → predicted) ─────────────────────────────
    outcomes = ["home", "draw", "away"]
    confusion: dict[str, dict[str, int]] = {
        a: {p: 0 for p in outcomes} for a in outcomes
    }
    for r in all_results:
        a = r["actual"]
        p = r["predicted"]
        if a in confusion and p in confusion[a]:
            confusion[a][p] += 1

    # ── prediction distribution ────────────────────────────────────────────
    pred_dist = {"home": 0, "draw": 0, "away": 0}
    for r in all_results:
        p = r["predicted"]
        if p in pred_dist:
            pred_dist[p] += 1

    # ── calibration by confidence band ────────────────────────────────────
    calibration: dict[str, dict] = {}
    for r in all_results:
        band = conf_band(r["confidence"])
        if band not in calibration:
            calibration[band] = {"hits": 0, "total": 0}
        calibration[band]["total"] += 1
        if r["correct"]:
            calibration[band]["hits"] += 1
    for band, d in calibration.items():
        d["accuracy"] = _pct(d["hits"], d["total"])

    # ── high-confidence (≥70%) ─────────────────────────────────────────────
    hc = [r for r in all_results if r["confidence"] >= 70]
    hc_acc = _pct(sum(1 for r in hc if r["correct"]), len(hc))

    # ── flat-stake ROI simulation ──────────────────────────────────────────
    # Bet 1 unit on every prediction using implied odds from model probabilities.
    roi_profit = 0.0
    roi_bets   = 0
    for r in all_results:
        p_key = f"prob_{r['predicted']}"
        prob  = r.get(p_key, 0) / 100.0
        if prob > 0:
            implied_odds = 1.0 / prob
            if r["correct"]:
                roi_profit += implied_odds - 1.0
            else:
                roi_profit -= 1.0
            roi_bets += 1
    roi_pct = round(roi_profit / roi_bets * 100, 1) if roi_bets else 0.0

    # ── build summary ──────────────────────────────────────────────────────
    summary = {
        "tested":             n,
        "leagues_tested":     len(league_stats),
        "overall_accuracy":   _pct(hits, n),
        "home_accuracy":      _pct(by_actual["home"][0], by_actual["home"][1]),
        "draw_accuracy":      _pct(by_actual["draw"][0], by_actual["draw"][1]),
        "away_accuracy":      _pct(by_actual["away"][0], by_actual["away"][1]),
        "high_conf_accuracy": hc_acc,
        "high_conf_sample":   len(hc),
        "roi_pct":            roi_pct,
        "roi_bets":           roi_bets,
        "timestamp":          datetime.datetime.utcnow().isoformat() + "Z",
    }

    # ── print report ───────────────────────────────────────────────────────
    SEP = "=" * 60
    log.info(SEP)
    log.info(f"BACKTEST RESULTS  ({n} matches, {len(league_stats)} leagues)")
    log.info(SEP)
    log.info(f"  Overall accuracy  : {summary['overall_accuracy']}%")
    log.info(f"  Home win accuracy : {summary['home_accuracy']}%  (n={by_actual['home'][1]})")
    log.info(f"  Draw accuracy     : {summary['draw_accuracy']}%  (n={by_actual['draw'][1]})")
    log.info(f"  Away win accuracy : {summary['away_accuracy']}%  (n={by_actual['away'][1]})")
    log.info(f"  High-conf (>=70%) : {hc_acc}%  (n={len(hc)})")
    log.info(f"  Flat-stake ROI    : {roi_pct:+.1f}%  ({roi_bets} bets)")

    log.info("")
    log.info("PREDICTION DISTRIBUTION (what the model actually predicts)")
    for outcome in outcomes:
        cnt = pred_dist[outcome]
        log.info(f"  Predicted {outcome:<4}  : {cnt:4d}  ({_pct(cnt, n):.1f}%)")

    log.info("")
    log.info("CONFUSION MATRIX  (rows = actual, cols = predicted)")
    log.info(f"  {'':10s}  {'pred home':>10}  {'pred draw':>10}  {'pred away':>10}")
    for a in outcomes:
        row_vals = "  ".join(f"{confusion[a][p]:>10}" for p in outcomes)
        log.info(f"  {'actual ' + a:<10}  {row_vals}")

    log.info("")
    log.info("CALIBRATION BY CONFIDENCE BAND")
    for band in ["50-59%", "60-69%", "70-79%", "80-89%", "90%+"]:
        if band in calibration:
            d = calibration[band]
            log.info(f"  {band}  →  {d['accuracy']:5.1f}%  (n={d['total']})")

    log.info("")
    log.info("PER-LEAGUE ACCURACY")
    sorted_leagues = sorted(league_stats.values(), key=lambda x: x["accuracy"], reverse=True)
    for ls in sorted_leagues:
        log.info(f"  {ls['name']:<20}  {ls['accuracy']:5.1f}%  (n={ls['tested']})")

    log.info(SEP)

    # ── save JSON ──────────────────────────────────────────────────────────
    output = {
        "summary":           summary,
        "by_actual_outcome": {
            k: {"hits": v[0], "total": v[1], "accuracy": _pct(v[0], v[1])}
            for k, v in by_actual.items()
        },
        "prediction_distribution": {
            k: {"count": v, "pct": _pct(v, n)} for k, v in pred_dist.items()
        },
        "confusion_matrix":  confusion,
        "calibration":       calibration,
        "per_league":        league_stats,
        "sample":            all_results[:50],
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)

    log.info(f"Saved to {OUT}")


if __name__ == "__main__":
    main()

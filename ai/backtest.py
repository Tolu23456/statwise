#!/usr/bin/env python3
"""
StatWise backtest v3 — temporal holdout evaluation on clean CSV data.

Improvements over v2:
  • Loads from ai/data/clean/YYYY_matches.csv (unified 48-col schema)
  • Strict chronological split: history < cutoff, test >= cutoff
  • Filters international matches and low-quality rows
  • Per-league accuracy breakdown (top leagues only)
  • Brier score + log-loss in addition to accuracy
  • Calibration by confidence band
  • Flat-stake ROI simulation
  • Full JSON saved to ai/data/backtest_results.json
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

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
CLEAN_DIR = os.path.join(BASE_DIR, "data", "clean")
OUT       = os.path.join(BASE_DIR, "data", "backtest_results.json")

# Use matches from this date onward as the test set
TEST_CUTOFF = "2023-08-01"

# Minimum quality_score to include a row (0 = include all)
MIN_QUALITY = 10

# Leagues to report individually (slug must appear in league_slug column)
REPORT_LEAGUES = {
    "premier-league": "Premier League",
    "la-liga":        "La Liga",
    "bundesliga":     "Bundesliga",
    "serie-a":        "Serie A",
    "ligue1":         "Ligue 1",
    "championship":   "Championship",
    "serie-b":        "Serie B",
    "bundesliga-2":   "2. Bundesliga",
    "la-liga-2":      "La Liga 2",
    "eredivisie":     "Eredivisie",
    "primeira-liga":  "Primeira Liga",
    "super-lig":      "Super Lig",
    "scottish-prem":  "Scottish Prem",
    "belgian-pro":    "Belgian Pro",
    "greek-super":    "Greek Super",
}


# ── data loading ───────────────────────────────────────────────────────────────

def load_clean_data() -> pd.DataFrame:
    paths = sorted(glob.glob(os.path.join(CLEAN_DIR, "????_matches.csv")))
    if not paths:
        log.error(f"No clean CSV files found in {CLEAN_DIR}")
        return pd.DataFrame()

    want = {"date", "home_team", "away_team", "home_goals", "away_goals",
            "league_slug", "odds_home", "odds_draw", "odds_away",
            "quality_score", "is_international"}
    frames = []
    for p in paths:
        try:
            df = pd.read_csv(p, low_memory=False,
                             usecols=lambda c: c in want)
            frames.append(df)
        except Exception as e:
            log.warning(f"Could not read {p}: {e}")

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["home_team", "away_team", "home_goals", "away_goals"])
    df["home_goals"] = pd.to_numeric(df["home_goals"], errors="coerce").fillna(0).astype(int)
    df["away_goals"] = pd.to_numeric(df["away_goals"], errors="coerce").fillna(0).astype(int)
    df["date"]       = pd.to_datetime(df.get("date", pd.Series(dtype=str)), errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Drop international matches (different dynamics, model is club-tuned)
    if "is_international" in df.columns:
        df = df[pd.to_numeric(df["is_international"], errors="coerce").fillna(0) == 0]

    # Quality filter
    if "quality_score" in df.columns and MIN_QUALITY > 0:
        before = len(df)
        df = df[pd.to_numeric(df["quality_score"], errors="coerce").fillna(0) >= MIN_QUALITY]
        log.info(f"Quality filter (≥{MIN_QUALITY}): kept {len(df):,} / {before:,} rows")

    log.info(f"Loaded {len(df):,} club matches from {len(paths)} year files "
             f"({df['date'].min().date()} → {df['date'].max().date()})")
    return df


# ── helpers ────────────────────────────────────────────────────────────────────

def normalize(label: str) -> str:
    s = str(label).lower().strip()
    if "home" in s: return "home"
    if "away" in s: return "away"
    if "draw" in s: return "draw"
    return s


def conf_band(conf: float) -> str:
    if conf < 60: return "50-59%"
    if conf < 70: return "60-69%"
    if conf < 80: return "70-79%"
    if conf < 90: return "80-89%"
    return "90%+"


def _pct(hits: int, total: int) -> float:
    return round(hits / total * 100, 1) if total else 0.0


def brier_score(results: list[dict]) -> float:
    """Multi-class Brier score (lower = better, random = 0.667)."""
    total = 0.0
    for r in results:
        ph = r["prob_home"] / 100
        pd_ = r["prob_draw"] / 100
        pa = r["prob_away"] / 100
        a = r["actual"]
        oh = 1.0 if a == "home" else 0.0
        od = 1.0 if a == "draw" else 0.0
        oa = 1.0 if a == "away" else 0.0
        total += (ph - oh)**2 + (pd_ - od)**2 + (pa - oa)**2
    return round(total / len(results), 4) if results else 0.0


def log_loss(results: list[dict], eps: float = 1e-7) -> float:
    total = 0.0
    for r in results:
        a = r["actual"]
        p = r[f"prob_{a}"] / 100
        total += -np.log(max(p, eps))
    return round(total / len(results), 4) if results else 0.0


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("Loading model…")
    from model.trainer import FootballPredictor
    try:
        model = FootballPredictor.load()
    except Exception as e:
        log.error(f"Could not load model: {e}")
        sys.exit(1)
    log.info("Model loaded OK")

    df = load_clean_data()
    if df.empty:
        log.error("No data — run the data pipeline first.")
        sys.exit(1)

    cutoff = pd.Timestamp(TEST_CUTOFF)
    history_df = df[df["date"] < cutoff].copy()
    test_df    = df[df["date"] >= cutoff].copy()

    log.info(f"History: {len(history_df):,} matches  |  Test: {len(test_df):,} matches  "
             f"(cutoff={TEST_CUTOFF})")

    if len(test_df) < 50:
        log.error("Test set too small — check TEST_CUTOFF or run the pipeline first.")
        sys.exit(1)

    history_list = history_df.to_dict("records")
    all_results: list[dict] = []
    league_stats: dict[str, dict] = {}

    log.info("Running predictions on test set…")
    for i, row in enumerate(test_df.itertuples(index=False), 1):
        if i % 500 == 0:
            log.info(f"  {i}/{len(test_df)} …")
        try:
            home  = row.home_team
            away  = row.away_team
            slug  = getattr(row, "league_slug", "all") or "all"

            # Team-filtered history (last 400 matches involving either team)
            team_hist = history_df[
                (history_df["home_team"].isin([home, away])) |
                (history_df["away_team"].isin([home, away]))
            ].tail(400)

            oh = getattr(row, "odds_home", None)
            od = getattr(row, "odds_draw", None)
            oa = getattr(row, "odds_away", None)

            pred = model.predict_match(home, away, slug, team_hist,
                                       odds_home=oh, odds_draw=od, odds_away=oa)

            hg, ag = int(row.home_goals), int(row.away_goals)
            actual    = "home" if hg > ag else ("draw" if hg == ag else "away")
            predicted = normalize(pred["prediction"])
            conf      = pred.get("confidence", 0)
            correct   = predicted == actual

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

            # Per-league tracking (only for report leagues)
            display = REPORT_LEAGUES.get(slug)
            if display:
                if slug not in league_stats:
                    league_stats[slug] = {"name": display, "hits": 0, "total": 0}
                league_stats[slug]["total"] += 1
                if correct:
                    league_stats[slug]["hits"] += 1

        except Exception as ex:
            pass  # skip silently to avoid flooding logs

    if not all_results:
        log.error("No results — check model and data")
        sys.exit(1)

    n    = len(all_results)
    hits = sum(1 for r in all_results if r["correct"])

    # Per-outcome accuracy
    by_actual = {"home": [0, 0], "draw": [0, 0], "away": [0, 0]}
    for r in all_results:
        a = r["actual"]
        if a in by_actual:
            by_actual[a][1] += 1
            if r["correct"]:
                by_actual[a][0] += 1

    # Confusion matrix
    outcomes = ["home", "draw", "away"]
    confusion = {a: {p: 0 for p in outcomes} for a in outcomes}
    for r in all_results:
        a, p = r["actual"], r["predicted"]
        if a in confusion and p in confusion[a]:
            confusion[a][p] += 1

    # Prediction distribution
    pred_dist = {"home": 0, "draw": 0, "away": 0}
    for r in all_results:
        if r["predicted"] in pred_dist:
            pred_dist[r["predicted"]] += 1

    # Calibration by confidence band
    calibration: dict[str, dict] = {}
    for r in all_results:
        band = conf_band(r["confidence"])
        if band not in calibration:
            calibration[band] = {"hits": 0, "total": 0}
        calibration[band]["total"] += 1
        if r["correct"]:
            calibration[band]["hits"] += 1
    for d in calibration.values():
        d["accuracy"] = _pct(d["hits"], d["total"])

    # High-confidence subset
    hc = [r for r in all_results if r["confidence"] >= 70]
    hc_acc = _pct(sum(1 for r in hc if r["correct"]), len(hc))

    # Brier score + log-loss
    bs  = brier_score(all_results)
    ll  = log_loss(all_results)

    # Flat-stake ROI (bet 1 unit on every prediction at implied model odds)
    roi_profit, roi_bets = 0.0, 0
    for r in all_results:
        p_key = f"prob_{r['predicted']}"
        prob  = r.get(p_key, 0) / 100.0
        if prob > 0.01:
            implied_odds = 1.0 / prob
            roi_profit  += (implied_odds - 1.0) if r["correct"] else -1.0
            roi_bets    += 1
    roi_pct = round(roi_profit / roi_bets * 100, 1) if roi_bets else 0.0

    # Per-league accuracy
    for slug, d in league_stats.items():
        d["accuracy"] = _pct(d["hits"], d["total"])

    summary = {
        "tested":             n,
        "overall_accuracy":   _pct(hits, n),
        "home_accuracy":      _pct(by_actual["home"][0], by_actual["home"][1]),
        "draw_accuracy":      _pct(by_actual["draw"][0], by_actual["draw"][1]),
        "away_accuracy":      _pct(by_actual["away"][0], by_actual["away"][1]),
        "high_conf_accuracy": hc_acc,
        "high_conf_sample":   len(hc),
        "brier_score":        bs,
        "log_loss":           ll,
        "roi_pct":            roi_pct,
        "roi_bets":           roi_bets,
        "test_cutoff":        TEST_CUTOFF,
        "history_matches":    len(history_df),
        "timestamp":          datetime.datetime.utcnow().isoformat() + "Z",
    }

    SEP = "=" * 62
    log.info(SEP)
    log.info(f"BACKTEST v3  ({n:,} matches, cutoff={TEST_CUTOFF})")
    log.info(SEP)
    log.info(f"  Overall accuracy  : {summary['overall_accuracy']}%")
    log.info(f"  Home win accuracy : {summary['home_accuracy']}%  (n={by_actual['home'][1]:,})")
    log.info(f"  Draw accuracy     : {summary['draw_accuracy']}%  (n={by_actual['draw'][1]:,})")
    log.info(f"  Away win accuracy : {summary['away_accuracy']}%  (n={by_actual['away'][1]:,})")
    log.info(f"  High-conf (≥70%)  : {hc_acc}%  (n={len(hc):,})")
    log.info(f"  Brier score       : {bs:.4f}  (random≈0.667, perfect=0)")
    log.info(f"  Log-loss          : {ll:.4f}  (lower is better)")
    log.info(f"  Flat-stake ROI    : {roi_pct:+.1f}%  ({roi_bets:,} bets)")

    log.info("")
    log.info("PREDICTION DISTRIBUTION")
    for o in outcomes:
        cnt = pred_dist[o]
        log.info(f"  Predicted {o:<4}  : {cnt:5,}  ({_pct(cnt, n):.1f}%)")

    log.info("")
    log.info("CONFUSION MATRIX  (rows=actual, cols=predicted)")
    log.info(f"  {'':10}  {'pred home':>10}  {'pred draw':>10}  {'pred away':>10}")
    for a in outcomes:
        vals = "  ".join(f"{confusion[a][p]:>10,}" for p in outcomes)
        log.info(f"  {'actual '+a:<10}  {vals}")

    log.info("")
    log.info("CALIBRATION BY CONFIDENCE BAND")
    for band in ["50-59%", "60-69%", "70-79%", "80-89%", "90%+"]:
        if band in calibration:
            d = calibration[band]
            log.info(f"  {band}  →  {d['accuracy']:5.1f}%  (n={d['total']:,})")

    log.info("")
    log.info("PER-LEAGUE ACCURACY  (top 15 leagues)")
    sorted_lg = sorted(league_stats.values(), key=lambda x: -x["accuracy"])
    for ls in sorted_lg:
        log.info(f"  {ls['name']:<22}  {ls['accuracy']:5.1f}%  (n={ls['total']:,})")

    log.info(SEP)

    output = {
        "summary":           summary,
        "by_actual_outcome": {
            k: {"hits": v[0], "total": v[1], "accuracy": _pct(v[0], v[1])}
            for k, v in by_actual.items()
        },
        "prediction_distribution": {k: {"count": v, "pct": _pct(v, n)} for k, v in pred_dist.items()},
        "confusion_matrix":  confusion,
        "calibration":       calibration,
        "per_league":        league_stats,
        "sample":            all_results[:100],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(output, f, indent=2)
    log.info(f"Saved → {OUT}")


if __name__ == "__main__":
    main()

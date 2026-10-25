#!/usr/bin/env python3
"""Audit: what probabilities actually reach the user, and how well they are calibrated.

Runs the DEPLOYED pickle through the SAME inference path production uses
(FeaturePipeline.build_features + outcome_pipe.predict_proba + alpha blend)
on a sample of recent settled matches, and compares:

  * rich-history features  (what retrain.py's holdout evaluation assumes)
  * empty-history features (what ai/scheduler.py / push_ai_predictions.py
    actually pass: PredictionEngine.history stays an empty DataFrame when the
    model is loaded from the pickle instead of trained in-process)
  * with vs without the market alpha blend
  * against the de-vigged market favourite

Produces accuracy, log loss, ECE and a reliability table for each variant.

Usage:  cd <repo>/ai && ../.venv/bin/python ../analysis/serve_path_reliability.py [n_sample]
No training, no writes outside analysis/.
"""
from __future__ import annotations

import gc
import json
import os
import sys
import time

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root
AI = os.path.join(BASE, "ai")
sys.path.insert(0, AI)

N_SAMPLE = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
EVAL_START = pd.Timestamp("2026-08-09")   # retrain eval half = last 2250 holdout rows
EVAL_END = pd.Timestamp("2026-10-18")

USECOLS = ["date", "home_team", "away_team", "home_goals", "away_goals", "league_slug",
           "odds_home", "odds_draw", "odds_away",
           "avg_odds_home", "avg_odds_draw", "avg_odds_away"]


def rss(tag: str = "") -> None:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    print(f"[rss {tag}] {int(line.split()[1]) / 1024:.0f} MB")
    except OSError:
        pass


def devig(o: np.ndarray) -> np.ndarray:
    imp = 1.0 / np.maximum(o, 1.0001)
    return imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1.0)).mean())


def ece_table(p: np.ndarray, y: np.ndarray, bins: int = 10):
    conf = p.max(axis=1)
    pred = p.argmax(axis=1)
    correct = (pred == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for b in range(bins):
        m = (conf >= edges[b]) & (conf < edges[b + 1] if b < bins - 1 else conf <= edges[b + 1])
        if not m.any():
            continue
        acc, cf = float(correct[m].mean()), float(conf[m].mean())
        w = float(m.mean())
        ece += w * abs(acc - cf)
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()),
                     "mean_p": round(cf, 4), "emp_acc": round(acc, 4),
                     "gap": round(acc - cf, 4)})
    return round(ece, 4), rows


def main() -> None:
    import joblib

    t0 = time.time()
    print("loading pickle…")
    d = joblib.load(os.path.join(AI, "models", "football_predictor.pkl"))
    fp = d["feature_pipe"]
    outcome = d["outcome_pipe"]
    alphas = d.get("alphas") or {}
    cinfo = d.get("calibration_info") or {}
    print(f"loaded in {time.time() - t0:.0f}s | mode={cinfo.get('mode')} "
          f"eval_ll={cinfo.get('eval_log_loss')} acc={cinfo.get('accuracy')} "
          f"alpha={cinfo.get('alpha_default')} calibrators={d.get('calibrators')}")
    rss("pickle")

    # ── data ────────────────────────────────────────────────────────
    frames = [pd.read_csv(os.path.join(AI, "data", "clean", f"{y}_matches.csv"),
                          usecols=USECOLS, low_memory=False) for y in (2024, 2025, 2026)]
    raw = pd.concat(frames, ignore_index=True)
    for c in USECOLS[4:]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["home_team", "away_team", "home_goals", "away_goals", "date"])
    raw = raw.sort_values("date").reset_index(drop=True)
    print(f"history frame: {len(raw):,} rows  max {raw['date'].max().date()}")
    del frames
    gc.collect()

    hist_ctx = raw[raw["date"] < EVAL_START].copy()      # no lookahead into the eval window
    sample = raw[(raw["date"] >= EVAL_START) & (raw["date"] <= EVAL_END)].copy()
    if len(sample) > N_SAMPLE:
        sample = sample.sample(N_SAMPLE, random_state=0).sort_values("date")
    sample = sample.reset_index(drop=True)
    print(f"sample {len(sample):,} settled matches {sample['date'].min().date()}.."
          f"{sample['date'].max().date()} | context history {len(hist_ctx):,} rows "
          f"(< {EVAL_START.date()})")
    empty_hist = pd.DataFrame(columns=["date", "home_team", "away_team",
                                       "home_goals", "away_goals"])
    rss("data")

    y = np.where(sample.home_goals.values > sample.away_goals.values, 0,
                 np.where(sample.home_goals.values == sample.away_goals.values, 1, 2)).astype(int)

    def matches_block() -> list[dict]:
        out = []
        for _, r in sample.iterrows():
            out.append({"home": r.home_team, "away": r.away_team,
                        "league": r.league_slug if isinstance(r.league_slug, str) else "all",
                        "date": r.date,
                        "odds_home": float(r.odds_home or 0), "odds_draw": float(r.odds_draw or 0),
                        "odds_away": float(r.odds_away or 0)})
        return out

    mblock = matches_block()

    def featurize(hist: pd.DataFrame) -> np.ndarray:
        X = fp.build_features(mblock, hist)
        X = np.nan_to_num(np.asarray(X, dtype=np.float64), nan=0.0, posinf=1e6, neginf=-1e6)
        return X

    print("building features: rich history…")
    t = time.time()
    X_rich = featurize(hist_ctx)
    print(f"  {time.time() - t:.1f}s  {X_rich.shape}")
    print("building features: EMPTY history (production path)…")
    t = time.time()
    X_empty = featurize(empty_hist)
    print(f"  {time.time() - t:.1f}s  {X_empty.shape}")
    rss("features")
    del hist_ctx
    gc.collect()

    # how different are the two feature vectors?
    diff = np.abs(X_rich - X_empty)
    col_names = __import__("model.features", fromlist=["FEATURE_NAMES"]).FEATURE_NAMES
    per_col = diff.mean(axis=0)
    n_moved = int((diff.max(axis=0) > 1e-9).sum())
    top = np.argsort(per_col)[::-1][:25]
    print(f"feature columns changed by empty history: {n_moved}/{X_rich.shape[1]}")
    print("largest mean |delta|:", [(col_names[i], round(float(per_col[i]), 3)) for i in top[:15]])

    odds = sample[["avg_odds_home", "avg_odds_draw", "avg_odds_away"]].fillna(0).values
    odds_fb = sample[["odds_home", "odds_draw", "odds_away"]].fillna(0).values
    odds = np.where(odds > 1.01, odds, odds_fb)
    valid = (odds[:, 0] > 1.01) & (odds[:, 1] > 1.01) & (odds[:, 2] > 1.01)
    mkt = np.full((len(sample), 3), 1 / 3.0)
    mkt[valid] = devig(odds[valid])
    alpha_def = float(alphas.get("_default", 1.0))

    results = {}

    def evaluate(tag: str, P: np.ndarray) -> dict:
        acc = float((P.argmax(1) == y).mean())
        ll = logloss(P, y)
        ece, tbl = ece_table(P, y)
        out = {"n": len(y), "accuracy": round(acc, 4), "log_loss": round(ll, 5),
               "ece": ece, "mean_conf": round(float(P.max(1).mean()), 4),
               "pred_home_pct": round(float((P.argmax(1) == 0).mean()), 3),
               "pred_draw_pct": round(float((P.argmax(1) == 1).mean()), 3),
               "pred_away_pct": round(float((P.argmax(1) == 2).mean()), 3),
               "brier": round(float(((P - np.eye(3)[y]) ** 2).mean()), 5),
               "reliability": tbl}
        print(json.dumps({k: v for k, v in out.items() if k != "reliability"}, indent=1))
        results[tag] = out
        return out

    def blend(P: np.ndarray, a: float) -> np.ndarray:
        Q = a * P + (1.0 - a) * mkt
        Q = np.clip(Q, 1e-12, None)
        return Q / Q.sum(axis=1, keepdims=True)

    print("\n=== raw model, rich history (retrain-holdout equivalent) ===")
    P_rich_raw = outcome.predict_proba(X_rich)
    evaluate("rich_raw", P_rich_raw)

    print("\n=== raw model, EMPTY history (production feature path) ===")
    P_empty_raw = outcome.predict_proba(X_empty)
    evaluate("empty_raw", P_empty_raw)

    print(f"\n=== production: empty history + alpha {alpha_def} blend ===")
    P_prod = blend(P_empty_raw, alpha_def)
    evaluate("prod_blend", P_prod)

    print("\n=== rich history + alpha blend ===")
    evaluate("rich_blend", blend(P_rich_raw, alpha_def))

    # market-only baseline on the SAME rows
    mm = mkt
    evaluate("market_only", mm)
    fav = mkt.argmax(1)
    print(f"\nmarket favourite accuracy on the same {len(y)} rows "
          f"(odds coverage {valid.mean():.0%}): {float((fav[valid] == y[valid]).mean()):.4f} "
          f"over {int(valid.sum())} priced rows")
    results["market_fav_priced"] = {
        "n": int(valid.sum()),
        "accuracy": round(float((fav[valid] == y[valid]).mean()), 4),
        "always_home_all_rows": round(float((y == 0).mean()), 4),
        "label_rates": {"home": round(float((y == 0).mean()), 4),
                        "draw": round(float((y == 1).mean()), 4),
                        "away": round(float((y == 2).mean()), 4)},
    }

    # argmax flips caused by empty history (the direct accuracy cost)
    flip = (P_rich_raw.argmax(1) != P_empty_raw.argmax(1))
    print(f"\nargmax flips rich->empty history: {int(flip.sum())}/{len(y)} "
          f"({float(flip.mean()):.1%}); "
          f"rich correct where empty wrong: {int(((P_rich_raw.argmax(1) == y) & flip).sum())}, "
          f"empty correct where rich wrong: {int(((P_empty_raw.argmax(1) == y) & flip).sum())}")
    results["history_skew"] = {
        "flips": int(flip.sum()),
        "rich_only_correct": int(((P_rich_raw.argmax(1) == y) & flip).sum()),
        "empty_only_correct": int(((P_empty_raw.argmax(1) == y) & flip).sum()),
        "feature_cols_changed": n_moved,
    }

    # alpha sweep on this sample (accuracy + log loss), production feature path
    sweep = {}
    for a in [0.0, 0.2, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        Pb = blend(P_empty_raw, a)
        sweep[str(a)] = {"acc": round(float((Pb.argmax(1) == y).mean()), 4),
                         "ll": round(logloss(Pb, y), 5)}
    print("\nalpha sweep (empty-history model):", json.dumps(sweep))
    results["alpha_sweep_empty"] = sweep
    sweep2 = {}
    for a in [0.0, 0.2, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        Pb = blend(P_rich_raw, a)
        sweep2[str(a)] = {"acc": round(float((Pb.argmax(1) == y).mean()), 4),
                          "ll": round(logloss(Pb, y), 5)}
    print("alpha sweep (rich-history model):", json.dumps(sweep2))
    results["alpha_sweep_rich"] = sweep2

    # draw promotion sweep (does promoting the 2nd-ranked pick help top-1?)
    for tag, P in (("rich", P_rich_raw), ("empty", P_empty_raw)):
        for delta in (0.0, 0.02, 0.05, 0.08):
            Q = P.copy()
            for i in range(len(Q)):
                order = np.argsort(Q[i])[::-1]
                if Q[i][order[1]] >= Q[i][order[0]] - delta:
                    Q[i] = 0
                    Q[i][order[1]] = 1
            results[f"draw_promote_{tag}_{delta}"] = round(float((Q.argmax(1) == y).mean()), 4)
    print("draw-promotion acc:", {k: v for k, v in results.items()
                                  if k.startswith("draw_promote")})

    out_path = os.path.join(BASE, "analysis", "serve_path_reliability.json")
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"\nwrote {out_path}  (total {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Draw blind-spot analysis (user report: "it never picks Draw").

Two different questions, answered on one out-of-time holdout with the
production protocol (same split/calibration as ai/retrain.py):

  1. IS THE MODEL ANTI-DRAW?   Calibration by class + rank.  If matches where
     draw sits 2nd come out draw more often than predicted, the probabilities
     are biased against draws (a log-loss problem, fixed by recalibration).

  2. WOULD PROMOTING DRAWS HELP THE DISPLAYED PICK?  Sweep a promotion rule —
     "call it Draw when p_draw >= p_max - delta" — and measure pick accuracy
     for each delta.  Argmax is already accuracy-optimal *if* the probabilities
     are right, so any gain here means the probabilities are miscalibrated in
     the draw class; any loss means we'd be trading hits for noise.

Probabilities are never modified, so log loss is reported only as context.

Usage:
    .venv/bin/python ai/experimental/eval_draw_picks.py
    HOLDOUT_DAYS=90 MODEL_MODE=single .venv/bin/python ai/experimental/eval_draw_picks.py

Exit codes: 0 = analysis complete (any verdict), 1 = error.
"""
from __future__ import annotations

import gc
import logging
import os
import sys
import time

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../statwise/ai
sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s")
logger = logging.getLogger("statwise.eval_draws")

from retrain import _load_clean_data  # noqa: E402
from model.trainer import FootballPredictor  # noqa: E402

HOLDOUT_DAYS = int(os.environ.get("HOLDOUT_DAYS", "90"))
MODE = os.environ.get("MODEL_MODE", "single").strip().lower()
DELTAS = [0.0, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0]
NAMES = ["Home Win", "Draw", "Away Win"]


def _ll(p, y):
    from sklearn.metrics import log_loss
    return float(log_loss(y, p, labels=[0, 1, 2]))


def class_calibration(p, y, n_bins=10):
    """Predicted vs observed frequency per class, plus per-class ECE."""
    out = {}
    for k in range(3):
        pk, yk = p[:, k], (y == k).astype(float)
        edges = np.linspace(0, 1, n_bins + 1)
        rows, ece, n = [], 0.0, len(pk)
        for b in range(n_bins):
            lo, hi = edges[b], edges[b + 1]
            m = (pk >= lo) & (pk < hi if b < n_bins - 1 else pk <= hi)
            if m.sum() < 5:
                continue
            conf, freq = float(pk[m].mean()), float(yk[m].mean())
            rows.append((round(conf, 3), round(freq, 3), int(m.sum())))
            ece += abs(conf - freq) * m.sum()
        out[NAMES[k]] = {"ece": round(ece / max(n, 1), 4),
                         "mean_pred": round(float(pk.mean()), 4),
                         "observed": round(float(yk.mean()), 4),
                         "bins": rows}
    return out


def rank_analysis(p, y):
    """Where does draw sit in the model's ranking, and what actually happened?"""
    order = np.argsort(-p, axis=1)
    draw_rank = (order == 1).argmax(axis=1)          # 0 = draw is top pick
    out = {}
    for r in range(3):
        m = draw_rank == r
        if m.sum() < 50:
            continue
        out[f"draw_rank_{r + 1}"] = {
            "n": int(m.sum()),
            "mean_p_draw": round(float(p[m, 1].mean()), 4),
            "observed_draw_rate": round(float((y[m] == 1).mean()), 4),
            "gap": round(float((y[m] == 1).mean() - p[m, 1].mean()), 4),
        }
    return out


def pick_sweep(p, y):
    """Displayed-pick accuracy under 'promote draw within delta of the top'."""
    base = p.argmax(axis=1)
    rows = []
    for d in DELTAS:
        pick = base.copy()
        if d > 0:
            top = p.max(axis=1)
            promote = (p[:, 1] >= top - d / 100.0) & (base != 1)
            pick[promote] = 1
        rows.append({
            "delta_pp": d,
            "accuracy": round(float((pick == y).mean()), 4),
            "draw_picks": int((pick == 1).sum()),
            "changed": int((pick != base).sum()),
            "changed_hit_rate": (round(float((pick[pick != base] == y[pick != base]).mean()), 4)
                                 if (pick != base).any() else None),
        })
    return rows


def main() -> int:
    t0 = time.time()
    df = _load_clean_data()
    if df.empty:
        logger.error("No clean dataset found — run ai/retrain.py first")
        return 1
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    dates = pd.to_datetime(df["date"], errors="coerce")
    split_at = dates.max() - pd.Timedelta(days=HOLDOUT_DAYS)
    logger.info(f"dataset: {len(df):,} matches; holdout = last {HOLDOUT_DAYS}d "
                f"(<= {split_at.date()} is train)")

    predictor = FootballPredictor()
    predictor.feature_pipe.fit(df)
    X, y, g, d = predictor.feature_pipe.build_training_set(df)
    del df
    gc.collect()

    X = np.nan_to_num(np.ascontiguousarray(X, dtype=np.float32),
                      nan=0.0, posinf=1e6, neginf=-1e6)
    logger.info(f"features: {X.shape} {X.dtype} = {X.nbytes / 1e6:.0f} MB")

    dates_all = np.asarray(pd.to_datetime(d, errors="coerce"))
    mask_tr = dates_all <= split_at
    mask_ho = ~mask_tr

    # ── one training run, same protocol as ai/retrain.py ──────────────────
    from sklearn.isotonic import IsotonicRegression
    pred = FootballPredictor()
    pred.fit_stacks(X[mask_tr], y[mask_tr], g[mask_tr], calibrate=False, mode=MODE)
    raw = pred._outcome_pipe.predict_proba(X[mask_ho])
    y_ho = y[mask_ho]
    mid = max(len(y_ho) // 2, 100)
    cals = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw[:mid, k], (y_ho[:mid] == k).astype(float))
        cals.append(iso)
    p_cal = np.column_stack([iso.predict(raw[mid:, k]) for k, iso in enumerate(cals)])
    p_cal = np.clip(p_cal, 1e-6, None)
    p_cal = p_cal / p_cal.sum(axis=1, keepdims=True)
    ll_raw, ll_cal = _ll(raw[mid:], y_ho[mid:]), _ll(p_cal, y_ho[mid:])
    p = p_cal if ll_cal < ll_raw else raw[mid:]
    y_ev = y_ho[mid:]

    # market reference on the same rows, if odds were captured
    market_draw = None
    odds = getattr(predictor.feature_pipe, "last_meta", {}).get("odds")
    if odds is not None:
        idx = np.flatnonzero(mask_ho)[mid:]
        o = odds[idx]
        valid = (o[:, 0] > 1.01) & (o[:, 1] > 1.01) & (o[:, 2] > 1.01)
        if int(valid.sum()) > 50:
            imp = 1.0 / np.maximum(o[valid], 1.0001)
            mkt = imp / imp.sum(axis=1, keepdims=True)
            market_draw = {
                "mean_model_p_draw": round(float(p[valid, 1].mean()), 4),
                "mean_market_p_draw": round(float(mkt[:, 1].mean()), 4),
                "observed_draw_rate": round(float((y_ev[valid] == 1).mean()), 4),
                "n": int(valid.sum()),
            }

    # ── report ─────────────────────────────────────────────────────────────
    logger.info("=" * 74)
    logger.info(f"eval rows={len(y_ev):,} | ll_raw={ll_raw:.5f} ll_cal={ll_cal:.5f} | "
                f"acc(argmax)={float((p.argmax(1) == y_ev).mean()):.4f}")
    logger.info(f"outcome mix: home={float((y_ev == 0).mean()):.3f} "
                f"draw={float((y_ev == 1).mean()):.3f} away={float((y_ev == 2).mean()):.3f}")

    cal = class_calibration(p, y_ev)
    for name, c in cal.items():
        logger.info(f"calibration {name:9s} mean_pred={c['mean_pred']:.3f} "
                    f"observed={c['observed']:.3f} gap={c['observed'] - c['mean_pred']:+.3f} "
                    f"ECE={c['ece']:.4f}")

    ranks = rank_analysis(p, y_ev)
    for k, v in ranks.items():
        logger.info(f"when draw is {k[-1]} pick: n={v['n']:,} "
                    f"p_draw={v['mean_p_draw']:.3f} actual={v['observed_draw_rate']:.3f} "
                    f"gap={v['gap']:+.3f}")

    if market_draw:
        logger.info(f"draw prob: model={market_draw['mean_model_p_draw']:.3f} "
                    f"market={market_draw['mean_market_p_draw']:.3f} "
                    f"observed={market_draw['observed_draw_rate']:.3f} (n={market_draw['n']:,})")

    sweep = pick_sweep(p, y_ev)
    logger.info("-" * 74)
    logger.info("promotion sweep (displayed pick only — probabilities unchanged)")
    best = max(sweep, key=lambda r: r["accuracy"])
    for r in sweep:
        mark = " <= best" if r is best else ""
        logger.info(f"  delta={r['delta_pp']:>4.1f}pp  acc={r['accuracy']:.4f}  "
                    f"draw_picks={r['draw_picks']:>6,}  changed={r['changed']:>6,}  "
                    f"hit_rate_of_changed={r['changed_hit_rate']}{mark}")
    base = sweep[0]
    logger.info("=" * 74)
    logger.info(f"argmax accuracy            = {base['accuracy']:.4f}")
    logger.info(f"best promoted accuracy     = {best['accuracy']:.4f} "
                f"(delta={best['delta_pp']}pp, {best['changed']:,} picks changed)")
    if market_draw:
        logger.info(f"model draw prob vs market  = "
                    f"{market_draw['mean_model_p_draw'] - market_draw['mean_market_p_draw']:+.3f}")
    logger.info(f"total {time.time() - t0:.1f}s")
    return 0


def _rss_mb() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

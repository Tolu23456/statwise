#!/usr/bin/env python3
"""
Ablation test for the new feature blocks (Step 1.1 of IMPROVEMENT_PLAN.md):

    141-145  travel/fatigue proxies  (ai/model/travel.py)
    146-154  recent-form differentials (ai/model/form.py)

Builds the feature matrix ONCE, then trains the production stack twice on the
identical chronological split with identical seeds:

    arm A  new features populated       (candidate behaviour)
    arm B  slots 141-154 zeroed         (current production behaviour)

Both arms are calibrated on the earlier half of the out-of-time holdout and
scored on the untouched later half, mirroring ai/retrain.py, so any difference
is attributable to the added features alone.

Usage:
    .venv/bin/python ai/experimental/eval_new_features.py
    MODEL_MODE=both HOLDOUT_DAYS=90 .venv/bin/python ai/experimental/eval_new_features.py

Exit codes: 0 = no degradation (delta <= +0.0005), 2 = features hurt, 1 = error.
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
logger = logging.getLogger("statwise.eval_features")

from retrain import _load_clean_data  # noqa: E402
from model.trainer import FootballPredictor  # noqa: E402

NEW_SLICE = slice(141, 155)      # travel (141-145) + form (146-154)
HOLDOUT_DAYS = int(os.environ.get("HOLDOUT_DAYS", "90"))
MODE = os.environ.get("MODEL_MODE", "single").strip().lower()
HALF_LIFE = float(os.environ.get("RECENCY_HALF_LIFE_DAYS", "1460") or 0)


def _ll(p, y):
    from sklearn.metrics import log_loss
    return float(log_loss(y, p, labels=[0, 1, 2]))


def _recency_weights(mask, dates_all, ref_date):
    if HALF_LIFE <= 0:
        return None
    age_days = np.asarray((ref_date - dates_all) / pd.Timedelta(days=1), dtype=np.float64)
    w = np.power(0.5, np.clip(age_days[mask], 0.0, None) / HALF_LIFE)
    return np.maximum(w, 0.02).astype(np.float32)


def train_and_score(X, y, g, mask_tr, mask_ho, w_tr, mode):
    """Fit one arm on the train slice, score the later holdout half."""
    from sklearn.isotonic import IsotonicRegression

    pred = FootballPredictor()
    pred.fit_stacks(X[mask_tr], y[mask_tr], g[mask_tr],
                    calibrate=False, mode=mode, sample_weight=w_tr)
    raw = pred._outcome_pipe.predict_proba(X[mask_ho])
    gp = pred._goals_pipe.predict_proba(X[mask_ho])[:, 1]

    y_ho = y[mask_ho]
    g_ho = g[mask_ho]
    mid = max(len(y_ho) // 2, 100)
    cals = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw[:mid, k], (y_ho[:mid] == k).astype(float))
        cals.append(iso)
    p_cal = np.column_stack([iso.predict(raw[mid:, k]) for k, iso in enumerate(cals)])
    p_cal = np.clip(p_cal, 1e-6, None)
    p_cal = p_cal / p_cal.sum(axis=1, keepdims=True)

    ll_raw = _ll(raw[mid:], y_ho[mid:])
    ll_cal = _ll(p_cal, y_ho[mid:])
    use_cal = ll_cal < ll_raw
    p_ev = p_cal if use_cal else raw[mid:]
    out = {
        "eval_log_loss": min(ll_raw, ll_cal),
        "raw_log_loss": ll_raw,
        "cal_log_loss": ll_cal,
        "calibration_used": bool(use_cal),
        "accuracy": float((p_ev.argmax(axis=1) == y_ho[mid:]).mean()),
        "goals_brier": float(((gp[mid:] - g_ho[mid:]) ** 2).mean()),
        "n_eval": int(len(y_ho[mid:])),
        "n_train": int(mask_tr.sum()),
    }
    del pred, raw, p_cal, p_ev
    gc.collect()
    return out


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
    # free the raw frame before training — the feature matrix is all we need now
    del df
    gc.collect()

    # float32 early: 428 MB float64 -> 214 MB, done as one rebinding so the
    # float64 buffer is released immediately (this box has 2.7 GB total).
    X = np.nan_to_num(np.ascontiguousarray(X, dtype=np.float32),
                      nan=0.0, posinf=1e6, neginf=-1e6)
    logger.info(f"features: {X.shape} {X.dtype} = {X.nbytes / 1e6:.0f} MB "
                f"in {time.time() - t0:.1f}s ({_rss_mb():.0f} MB RSS)")

    trav = X[:, 141:146]
    form = X[:, 146:155]
    logger.info(f"travel block: mean={float(np.abs(trav).mean()):.4f} "
                f"nonzero_rows={float((np.abs(trav).sum(1) > 0).mean()) * 100:.1f}%")
    logger.info(f"form  block: mean={float(np.abs(form).mean()):.4f} "
                f"nonzero_rows={float((np.abs(form).sum(1) > 0).mean()) * 100:.1f}%")

    dates_all = np.asarray(pd.to_datetime(d, errors="coerce"))
    mask_tr = dates_all <= split_at
    mask_ho = ~mask_tr
    ref_date = dates_all[mask_tr].max()
    w_tr = _recency_weights(mask_tr, dates_all, ref_date)

    results = {}
    logger.info(f"── arm A: new features present (mode={MODE})")
    t1 = time.time()
    results["A_with_new_features"] = train_and_score(X, y, g, mask_tr, mask_ho, w_tr, MODE)
    results["A_with_new_features"]["seconds"] = round(time.time() - t1, 1)
    logger.info(f"   {results['A_with_new_features']}")

    # control arm: zero the new slots IN PLACE (no second copy of the matrix)
    X[:, NEW_SLICE] = 0.0
    gc.collect()
    logger.info(f"── arm B: slots {NEW_SLICE.start}-{NEW_SLICE.stop - 1} zeroed (production behaviour)")
    t1 = time.time()
    results["B_without_new_features"] = train_and_score(X, y, g, mask_tr, mask_ho, w_tr, MODE)
    results["B_without_new_features"]["seconds"] = round(time.time() - t1, 1)
    logger.info(f"   {results['B_without_new_features']}")

    # market baseline on the same eval rows (context only)
    meta = getattr(predictor.feature_pipe, "last_meta", {}) or {}
    odds = meta.get("odds")
    if odds is not None:
        idx = np.flatnonzero(mask_ho)[max(int(mask_ho.sum()) // 2, 100):]
        o = odds[idx]
        imp = 1.0 / np.maximum(o, 1.0001)
        mkt = imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)
        valid = (o[:, 0] > 1.01) & (o[:, 1] > 1.01) & (o[:, 2] > 1.01)
        if int(valid.sum()) > 50:
            results["market_baseline"] = {"eval_log_loss": _ll(mkt[valid], y[idx][valid]),
                                          "n_eval": int(valid.sum())}

    a, b = results["A_with_new_features"], results["B_without_new_features"]
    delta = a["eval_log_loss"] - b["eval_log_loss"]
    logger.info("=" * 74)
    logger.info(f"with new features    ll={a['eval_log_loss']:.5f}  acc={a['accuracy']:.4f}  "
                f"goals_brier={a['goals_brier']:.5f}")
    logger.info(f"without (production) ll={b['eval_log_loss']:.5f}  acc={b['accuracy']:.4f}  "
                f"goals_brier={b['goals_brier']:.5f}")
    if "market_baseline" in results:
        logger.info(f"market baseline      ll={results['market_baseline']['eval_log_loss']:.5f}")
    verdict = ("IMPROVES" if delta < -0.0005 else
               "HURTS" if delta > 0.0005 else "within noise")
    logger.info(f"delta (with - without) = {delta:+.5f}  -> {verdict}")
    logger.info(f"total {time.time() - t0:.1f}s | {_rss_mb():.0f} MB RSS")

    return 0 if delta <= 0.0005 else 2


def _rss_mb() -> float:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


if __name__ == "__main__":
    sys.exit(main())

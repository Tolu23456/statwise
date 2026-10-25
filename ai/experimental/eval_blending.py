#!/usr/bin/env python3
"""
Blending ablation — Step 1.4 of IMPROVEMENT_PLAN.md (Better Market Blending).

What production does today (ai/retrain.py):

  * alpha is grid-searched in 0.05 steps on the SAME later holdout half it is
    reported on, so ``blended_market_log_loss`` is in-sample for alpha;
  * the market side of the blend comes from ``odds_*`` (single book, though
    the cleaner already prefers closing columns);
  * per-league alphas are fitted and deployed, but only the GLOBAL alpha is
    ever scored, so their benefit is never measured.

This script trains the production stack ONCE on the identical chronological
split (same protocol as retrain.py) and then scores the blending variants on
the untouched later half of the holdout:

  model            model only, no market term
  market_base      de-vigged odds_*
  market_closing   de-vigged closing consensus (avg closing, fallback base)
  current          alpha fitted on the eval half itself (what retrain reports)
  honest_base      alpha fitted on the EARLIER (calibration) half
  honest_closing   same, market side = closing consensus
  league_base      per-league alpha fitted on the earlier half
  honest_grid01    honest_base with a 0.01 alpha grid

Market sources are compared on the COMMON valid rows so the verdict is not an
artifact of one source having more coverage than the other.

Usage:
    HOLDOUT_DAYS=180 MODEL_MODE=joint .venv/bin/python ai/experimental/eval_blending.py

Exit codes: 0 = ran, 1 = error.
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
logger = logging.getLogger("statwise.eval_blend")

from retrain import _load_clean_data  # noqa: E402
from model.trainer import FootballPredictor  # noqa: E402

HOLDOUT_DAYS = int(os.environ.get("HOLDOUT_DAYS", "180"))
MODE = os.environ.get("MODEL_MODE", "joint").strip().lower()
HALF_LIFE = float(os.environ.get("RECENCY_HALF_LIFE_DAYS", "1460") or 0)
SHRINK_K = float(os.environ.get("ALPHA_SHRINK_K", "300") or 300)
MIN_LEAGUE_N = int(os.environ.get("ALPHA_MIN_LEAGUE_N", "60"))


def _ll(p, y):
    from sklearn.metrics import log_loss
    return float(log_loss(y, p, labels=[0, 1, 2]))


def _mkt(o):
    """De-vigged market probabilities from a 3-column odds matrix."""
    imp = 1.0 / np.maximum(o, 1.0001)
    return imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)


def _valid(o):
    return (o[:, 0] > 1.01) & (o[:, 1] > 1.01) & (o[:, 2] > 1.01)


def fit_alpha(p_model, mkt, y, grid=0.05):
    """alpha in [0,1] minimising log loss of alpha*model + (1-alpha)*market."""
    best_a, best_ll = 0.5, float("inf")
    for a in np.arange(0.0, 1.0001, grid):
        ll = _ll(a * p_model + (1.0 - a) * mkt, y)
        if ll < best_ll:
            best_ll, best_a = ll, float(a)
    return float(best_a)


def blend_ll(alpha, p_model, mkt, y):
    return _ll(alpha * p_model + (1.0 - alpha) * mkt, y)


def _recency_weights(mask, dates_all, ref_date):
    if HALF_LIFE <= 0:
        return None
    age_days = np.asarray((ref_date - dates_all) / pd.Timedelta(days=1), dtype=np.float64)
    w = np.power(0.5, np.clip(age_days[mask], 0.0, None) / HALF_LIFE)
    return np.maximum(w, 0.02).astype(np.float32)


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
    meta = getattr(predictor.feature_pipe, "last_meta", {}) or {}
    odds_base = np.asarray(meta.get("odds"), dtype=np.float64)
    odds_close = np.asarray(meta.get("odds_closing", meta.get("odds")), dtype=np.float64)
    leagues = np.asarray(meta.get("leagues"))
    del df
    gc.collect()

    X = np.nan_to_num(np.ascontiguousarray(X, dtype=np.float32),
                      nan=0.0, posinf=1e6, neginf=-1e6)
    logger.info(f"features: {X.shape} {X.dtype} = {X.nbytes / 1e6:.0f} MB "
                f"in {time.time() - t0:.1f}s ({_rss_mb():.0f} MB RSS)")

    dates_all = np.asarray(pd.to_datetime(d, errors="coerce"))
    mask_tr = dates_all <= split_at
    mask_ho = ~mask_tr
    ref_date = dates_all[mask_tr].max()
    w_tr = _recency_weights(mask_tr, dates_all, ref_date)

    logger.info(f"training '{MODE}' on {int(mask_tr.sum()):,} rows "
                f"(holdout {int(mask_ho.sum()):,}) …")
    t1 = time.time()
    pred = FootballPredictor()
    pred.fit_stacks(X[mask_tr], y[mask_tr], g[mask_tr],
                    calibrate=False, mode=MODE, sample_weight=w_tr)
    ho_idx = np.flatnonzero(mask_ho)
    raw = pred._outcome_pipe.predict_proba(X[mask_ho])
    logger.info(f"trained in {time.time() - t1:.1f}s")

    y_ho = y[mask_ho]
    mid = max(len(y_ho) // 2, 100)

    # calibrate on the earlier half, score on the later half (retrain protocol)
    from sklearn.isotonic import IsotonicRegression
    cals = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw[:mid, k], (y_ho[:mid] == k).astype(float))
        cals.append(iso)
    p_all = np.column_stack([iso.predict(raw[:, k]) for k, iso in enumerate(cals)])
    p_all = np.clip(p_all, 1e-6, None)
    p_all = p_all / p_all.sum(axis=1, keepdims=True)

    ll_raw_ev = _ll(raw[mid:], y_ho[mid:])
    ll_cal_ev = _ll(p_all[mid:], y_ho[mid:])
    use_cal = ll_cal_ev < ll_raw_ev
    p_cal = p_all[:mid] if use_cal else raw[:mid]
    p_ev = p_all[mid:] if use_cal else raw[mid:]
    y_cal, y_ev = y_ho[:mid], y_ho[mid:]
    logger.info(f"model: raw_ll={ll_raw_ev:.5f} cal_ll={ll_cal_ev:.5f} "
                f"use_cal={use_cal} (eval n={len(y_ev):,})")

    # ── market arrays, split the same way ───────────────────────────────
    ob_c, ob_e = odds_base[ho_idx][:mid], odds_base[ho_idx][mid:]
    oc_c, oc_e = odds_close[ho_idx][:mid], odds_close[ho_idx][mid:]
    mb_c, mb_e = _mkt(ob_c), _mkt(ob_e)
    mc_c, mc_e = _mkt(oc_c), _mkt(oc_e)
    vb_c, vb_e = _valid(ob_c), _valid(ob_e)
    vc_c, vc_e = _valid(oc_c), _valid(oc_e)
    common_e = vb_e & vc_e          # same rows for both sources
    leagues_ev = leagues[ho_idx][mid:]
    logger.info(f"odds coverage (eval): base {int(vb_e.sum()):,}/{len(vb_e):,} "
                f"closing {int(vc_e.sum()):,}/{len(vc_e):,} "
                f"common {int(common_e.sum()):,}")

    out: dict[str, dict] = {}

    def record(name, ll, note=""):
        out[name] = {"ll": round(ll, 5), "note": note}
        logger.info(f"BLEND {name:16s} ll={ll:.5f}  {note}")

    # baselines
    record("model", _ll(p_ev, y_ev), f"no market term, n={len(y_ev):,}")
    if int(vb_e.sum()) > 50:
        record("market_base", _ll(mb_e[vb_e], y_ev[vb_e]), f"n={int(vb_e.sum()):,}")
    if int(vc_e.sum()) > 50:
        record("market_closing", _ll(mc_e[vc_e], y_ev[vc_e]), f"n={int(vc_e.sum()):,}")
    if int(common_e.sum()) > 50:
        record("market_base@common", _ll(mb_e[common_e], y_ev[common_e]),
               f"n={int(common_e.sum()):,}")
        record("market_closing@common", _ll(mc_e[common_e], y_ev[common_e]),
               f"n={int(common_e.sum()):,}")

    # what production reports today: alpha fitted on the eval half itself
    if int(vb_e.sum()) > 50:
        a_cur = fit_alpha(p_ev[vb_e], mb_e[vb_e], y_ev[vb_e])
        record("current_insample", blend_ll(a_cur, p_ev[vb_e], mb_e[vb_e], y_ev[vb_e]),
               f"alpha={a_cur:.2f} fitted on eval (in-sample)")

    # honest: fit on the earlier half, score on the later half
    a_base = a_close = None
    if int(vb_c.sum()) > 50 and int(vb_e.sum()) > 50:
        a_base = fit_alpha(p_cal[vb_c], mb_c[vb_c], y_cal[vb_c])
        record("honest_base", blend_ll(a_base, p_ev[vb_e], mb_e[vb_e], y_ev[vb_e]),
               f"alpha={a_base:.2f} fitted on cal half")
    if int(vc_c.sum()) > 50 and int(vc_e.sum()) > 50:
        a_close = fit_alpha(p_cal[vc_c], mc_c[vc_c], y_cal[vc_c])
        record("honest_closing", blend_ll(a_close, p_ev[vc_e], mc_e[vc_e], y_ev[vc_e]),
               f"alpha={a_close:.2f} fitted on cal half")
    # same rows, so the market SOURCE verdict is apples-to-apples
    if a_base is not None and int(common_e.sum()) > 50:
        record("honest_base@common", blend_ll(a_base, p_ev[common_e], mb_e[common_e],
                                               y_ev[common_e]), f"alpha={a_base:.2f}")
    if a_close is not None and int(common_e.sum()) > 50:
        record("honest_closing@common", blend_ll(a_close, p_ev[common_e], mc_e[common_e],
                                                  y_ev[common_e]), f"alpha={a_close:.2f}")

    # per-league alpha, fitted on the earlier half and shrunk toward global
    if a_base is not None and int(vb_e.sum()) > 50:
        alphas = {"_default": a_base}
        n_league = 0
        leagues_cal = leagues[ho_idx][:mid]
        for slug in pd.unique(leagues_cal):
            m = (leagues_cal == slug) & vb_c
            n_l = int(m.sum())
            if n_l >= MIN_LEAGUE_N:
                a_l = fit_alpha(p_cal[m], mb_c[m], y_cal[m])
                wgt = n_l / (n_l + SHRINK_K)
                alphas[str(slug)] = round(float(wgt * a_l + (1.0 - wgt) * a_base), 4)
                n_league += 1
        use = np.array([alphas.get(str(s), a_base) for s in leagues_ev])
        pred_blend = (use[:, None] * p_ev) + ((1.0 - use[:, None]) * mb_e)
        record("league_base", _ll(pred_blend[vb_e], y_ev[vb_e]),
               f"n_leagues={n_league} global_alpha={a_base:.2f}")

    # finer grid
    if int(vb_c.sum()) > 50 and int(vb_e.sum()) > 50:
        a01 = fit_alpha(p_cal[vb_c], mb_c[vb_c], y_cal[vb_c], grid=0.01)
        record("honest_grid01", blend_ll(a01, p_ev[vb_e], mb_e[vb_e], y_ev[vb_e]),
               f"alpha={a01:.2f} grid=0.01")

    # ── verdict ─────────────────────────────────────────────────────────
    honest = {k: v["ll"] for k, v in out.items() if k.startswith("honest")}
    current = out.get("current_insample", {}).get("ll")
    if honest:
        best_name = min(honest, key=honest.get)
        logger.info("=" * 76)
        logger.info(f"BLEND best honest variant = {best_name} "
                    f"ll={honest[best_name]:.5f}")
        if current is not None:
            gap = honest[best_name] - current
            logger.info(f"BLEND optimism gap (honest - reported in-sample) = {gap:+.5f}")
        if "honest_base@common" in out and "honest_closing@common" in out:
            d = out["honest_closing@common"]["ll"] - out["honest_base@common"]["ll"]
            logger.info(f"BLEND closing-vs-base on common rows = {d:+.5f} "
                        f"({'closing wins' if d < -0.0002 else 'base wins' if d > 0.0002 else 'tie'})")
        if "league_base" in out and "honest_base" in out:
            d = out["league_base"]["ll"] - out["honest_base"]["ll"]
            logger.info(f"BLEND per-league vs global = {d:+.5f} "
                        f"({'league wins' if d < -0.0002 else 'global wins' if d > 0.0002 else 'tie'})")
        logger.info(f"BLEND model ll={out['model']['ll']:.5f} | "
                    f"market_base ll={out.get('market_base', {}).get('ll')} | "
                    f"market_closing ll={out.get('market_closing', {}).get('ll')}")

    logger.info(f"total {time.time() - t0:.1f}s | {_rss_mb():.0f} MB RSS")
    return 0


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

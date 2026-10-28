#!/usr/bin/env python3
"""
Honest 100k-row holdout audit (plan 5.14) — READ-ONLY, never deploys.

Question: "how well does the model actually do on a large, unseen slice of
real data?"  The production gate only ever looks at a 180-day holdout
(~2,250 matches). This audit holds out the most recent AUDIT_TEST_ROWS
(default 100,000) matches by date — ~6.8 years back from the data edge —
trains on everything EARLIER with the SAME recipe production uses, and
reports log loss / accuracy / calibration / market comparison on the 100k.

Design constraints:
  - reuses retrain._load_clean_data + FootballPredictor + the exact
    calibrate-on-first-half / evaluate-on-second-half OOT protocol, so the
    numbers are comparable to the production metric, not a new methodology;
  - READ-ONLY: it never calls predictor.save(), never touches
    ai/data/model_info.json, never reads FORCE_REPLACE. It cannot clobber
    the deployed model — that is the whole point of a separate script
    instead of reusing retrain.py (which DEPLOY any model on the
    "not-comparable" path);
  - stdlib + the existing venv (no new packages);
  - heavy — runs in GitHub Actions (train-model-scale), not locally.

Usage (Actions):
    cd ai && python audit_100k.py
Env:
    AUDIT_TEST_ROWS  rows to hold out from the end (default 100000)
    AUDIT_OUT        report JSON path (default data/audit_100k_report.json)
"""
from __future__ import annotations

import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from retrain import (  # noqa: E402  (retrain import is side-effect-free)
    _load_clean_data,
    _devig,
    _valid_odds,
    _rss_mb,
    damp_alpha,
)

TEST_ROWS = int(os.environ.get("AUDIT_TEST_ROWS", "100000"))
OUT_PATH = os.environ.get(
    "AUDIT_OUT", os.path.join(BASE_DIR, "data", "audit_100k_report.json")
)


def _ll(p, y):
    import numpy as np
    from sklearn.metrics import log_loss
    return float(log_loss(y, p, labels=[0, 1, 2]))


def _ci95(rows: "list[float]") -> tuple[float, float]:
    """Normal-approx 95% CI for the mean of per-row log losses."""
    import numpy as np
    a = np.asarray(rows, dtype=np.float64)
    if len(a) < 2:
        return (float("nan"), float("nan"))
    se = float(a.std(ddof=1) / np.sqrt(len(a)))
    m = float(a.mean())
    return (m - 1.96 * se, m + 1.96 * se)


def main() -> int:
    import numpy as np
    import pandas as pd
    from sklearn.isotonic import IsotonicRegression
    from model.trainer import FootballPredictor

    t0 = time.time()
    df = _load_clean_data()
    if df.empty:
        print("::error::no clean data — aborting audit")
        return 1

    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    dates = pd.to_datetime(df["date"], errors="coerce")
    n_total = len(df)
    if n_total <= TEST_ROWS + 5000:
        print(f"::error::dataset ({n_total:,}) too small to hold out {TEST_ROWS:,}")
        return 1

    # The split: last TEST_ROWS matches are the audit holdout; everything
    # earlier trains.  Boundary is a DATE so the mask is clean.
    boundary_idx = n_total - TEST_ROWS
    split_at = dates.iloc[boundary_idx]
    print(f"[audit] dataset {n_total:,} rows | train <= {split_at.date()} "
          f"({boundary_idx:,}) | holdout {TEST_ROWS:,} "
          f"({dates.iloc[-1].date() - split_at} span)")

    print("[audit] building feature state (single pass over full frame)…")
    t0f = time.time()
    predictor = FootballPredictor()
    predictor.feature_pipe.fit(df)
    X_all, y_all, g_all, d_all = predictor.feature_pipe.build_training_set(df)
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=1e6, neginf=-1e6).astype(
        np.float32, copy=False
    )
    meta = getattr(predictor.feature_pipe, "last_meta", {}) or {}
    print(f"[audit] features {X_all.shape[0]:,} x {X_all.shape[1]} in "
          f"{time.time() - t0f:.1f}s ({_rss_mb():.0f} MB RSS)")

    m_tr = pd.to_datetime(d_all) <= split_at
    m_ho = ~m_tr
    if int(m_ho.sum()) < 2000:
        print(f"::error::holdout only {int(m_ho.sum())} rows — audit meaningless")
        return 1

    Xt, yt, gt = X_all[m_tr], y_all[m_tr], g_all[m_tr]
    Xh, yh, gh = X_all[m_ho], y_all[m_ho], g_all[m_ho]
    d_ho = pd.to_datetime(d_all)[m_ho]

    _bt = meta.get("y_btts"); _ot = meta.get("y_o35")
    _aux_ok = (_bt is not None and len(_bt) == len(m_tr)
               and _ot is not None and len(_ot) == len(m_tr))
    aux_tr = {"y_btts": _bt[m_tr] if _aux_ok else None,
              "y_o35": _ot[m_tr] if _aux_ok else None}
    bh = _bt[m_ho] if _aux_ok else None
    oh = _ot[m_ho] if _aux_ok else None

    # Same recency half-life the production recipe settled on (4y).
    half_life = float(os.environ.get("RECENCY_HALF_LIFE_DAYS", "1460") or 0)
    dates_all = pd.to_datetime(d_all, errors="coerce")
    ref_date = dates_all[m_tr].max()
    if half_life > 0:
        age_days = np.asarray(
            (ref_date - dates_all[m_tr]) / pd.Timedelta(days=1),
            dtype=np.float64)
        w_tr = np.maximum(np.power(0.5, np.clip(age_days, 0, None) / half_life),
                          0.02).astype(np.float32)
    else:
        w_tr = None

    # Train on the EARLIER slice with the production recipe (mode=single).
    print(f"[audit] training single-mode stacks on {Xt.shape[0]:,} rows…")
    t1 = time.time()
    cand = FootballPredictor()
    cand.feature_pipe = predictor.feature_pipe
    cand.fit_stacks(Xt, yt, gt, calibrate=False, mode="single",
                    sample_weight=w_tr,
                    y_btts=aux_tr["y_btts"], y_o35=aux_tr["y_o35"])
    raw_h = cand._outcome_pipe.predict_proba(Xh)
    print(f"[audit] trained in {time.time() - t1:.1f}s")

    # Production OOT protocol: calibrate on the EARLIER half of the holdout,
    # evaluate on the LATER half.  With 100k rows that is ~50k eval rows —
    # a far tighter read than the production 2,250.
    mid = max(len(yh) // 2, 100)
    y_ev = yh[mid:]
    d_ev = d_ho.iloc[mid:]
    cals = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_h[:mid, k], (yh[:mid] == k).astype(float))
        cals.append(iso)
    p_cal = np.column_stack([cals[k].predict(raw_h[mid:, k]) for k in range(3)])
    p_cal = np.clip(p_cal, 1e-6, None)
    p_cal = p_cal / p_cal.sum(axis=1, keepdims=True)
    p_raw = raw_h[mid:]
    ll_raw = _ll(p_raw, y_ev)
    ll_cal = _ll(p_cal, y_ev)
    use_cal = ll_cal < ll_raw
    p_ev = p_cal if use_cal else p_raw
    ll_eval = min(ll_raw, ll_cal)

    # Per-row log loss → 95% CI (the large-n payoff).
    eps = 1e-12
    per_row = -np.log(np.clip(p_ev[np.arange(len(y_ev)), y_ev], eps, 1 - eps))
    lo_ci, hi_ci = _ci95(list(per_row))

    conf = p_ev.max(axis=1)
    correct = (p_ev.argmax(axis=1) == y_ev).astype(float)
    acc = float(correct.mean())
    ece = 0.0
    edges = np.linspace(0, 1, 11)
    for bi in range(10):
        m = (conf >= edges[bi]) & (conf < edges[bi + 1] if bi < 9
                                   else conf <= edges[bi + 1])
        if m.sum():
            ece += float(m.mean()) * abs(float(correct[m].mean())
                                         - float(conf[m].mean()))

    # Reliability curve (calibration honesty, plan 5.9 data).
    bins = []
    for bi in range(10):
        m = (conf >= edges[bi]) & (conf < edges[bi + 1] if bi < 9
                                   else conf <= edges[bi + 1])
        if int(m.sum()) >= 30:
            bins.append({
                "bin": f"{edges[bi]:.1f}-{edges[bi + 1]:.1f}",
                "n": int(m.sum()),
                "confidence": round(float(conf[m].mean()), 4),
                "accuracy": round(float(correct[m].mean()), 4),
            })

    # Market comparison on the SAME eval rows (production's two sides).
    odds_h = meta.get("odds")
    odds_h = odds_h[m_ho] if odds_h is not None else np.zeros((len(Xh), 3))
    odds_close_h = meta.get("odds_closing")
    odds_close_h = odds_close_h[m_ho] if odds_close_h is not None else odds_h
    o_ev = odds_h[mid:]
    o_ev_c = odds_close_h[mid:]
    mkt = _devig(o_ev)
    mkt_c = _devig(o_ev_c)
    valid = _valid_odds(o_ev)
    valid_c = _valid_odds(o_ev_c)

    # Blend alpha fitted + damped exactly as production does.
    ALPHA_PRIOR = float(os.environ.get("ALPHA_PRIOR", "0.35") or 0.35)
    ALPHA_PRIOR_K = float(os.environ.get("ALPHA_PRIOR_K", "1000") or 1000)

    def _best_alpha(mask):
        m = mask & valid_c
        if int(m.sum()) < 50:
            return None
        best_a, best_ll = None, float("inf")
        for a in np.arange(0.0, 1.001, 0.05):
            ll = _ll(a * p_ev[m] + (1.0 - a) * mkt_c[m], y_ev[m])
            if ll < best_ll:
                best_ll, best_a = ll, float(a)
        return float(best_a)

    n_a = int(valid_c.sum())
    ga_raw = _best_alpha(np.ones(len(y_ev), dtype=bool))
    alpha_dep = (damp_alpha(ga_raw, n_a, ALPHA_PRIOR, ALPHA_PRIOR_K)
                 if ga_raw is not None else ALPHA_PRIOR)

    # Headline metrics.
    metrics = {
        "audit": "100k out-of-time holdout (READ-ONLY, nothing deployed)",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_rows": int(n_total),
        "train_rows": int(m_tr.sum()),
        "holdout_rows": int(m_ho.sum()),
        "eval_rows": int(len(y_ev)),
        "split_at": str(split_at.date()),
        "holdout_span_days": int((dates.iloc[-1] - split_at).days),
        "mode": "single",
        "recency_half_life_days": half_life,
        "raw_log_loss": round(ll_raw, 5),
        "isotonic_log_loss": round(ll_cal, 5),
        "calibration_used": bool(use_cal),
        "eval_log_loss": round(ll_eval, 5),
        "eval_log_loss_ci95": [round(lo_ci, 5), round(hi_ci, 5)],
        "accuracy": round(acc, 4),
        "brier": round(float(((p_ev - np.eye(3)[y_ev]) ** 2).mean()), 5),
        "ece": round(ece, 4),
        "market_log_loss": round(_ll(mkt[valid], y_ev[valid]), 5)
        if int(valid.sum()) > 50 else None,
        "market_rows": int(valid.sum()),
        "market_closing_log_loss": round(_ll(mkt_c[valid_c], y_ev[valid_c]), 5)
        if int(valid_c.sum()) > 50 else None,
        "market_closing_rows": int(valid_c.sum()),
        "alpha_fitted_raw": ga_raw,
        "alpha_damped": round(float(alpha_dep), 4) if ga_raw is not None else None,
        "blended_market_log_loss": round(
            _ll(alpha_dep * p_ev[valid_c] + (1.0 - alpha_dep) * mkt_c[valid_c],
                y_ev[valid_c]), 5) if int(valid_c.sum()) >= 50 else None,
        "reliability_bins": bins,
        "goals_brier": None,
        "btts_brier": None,
        "o35_brier": None,
        "train_seconds": round(time.time() - t0, 1),
    }

    # Auxiliary-head honest Brier on the eval rows.
    gp = cand._goals_pipe.predict_proba(Xh[mid:])[:, 1]
    metrics["goals_brier"] = round(float(((gp - gh[mid:]) ** 2).mean()), 5)
    if getattr(cand, "_btts_pipe", None) is not None and bh is not None:
        bp = cand._btts_pipe.predict_proba(Xh[mid:])[:, 1]
        metrics["btts_brier"] = round(float(((bp - bh[mid:]) ** 2).mean()), 5)
    if getattr(cand, "_o35_pipe", None) is not None and oh is not None:
        op = cand._o35_pipe.predict_proba(Xh[mid:])[:, 1]
        metrics["o35_brier"] = round(float(((op - oh[mid:]) ** 2).mean()), 5)

    # Reference: the PRODUCTION model's last published gate numbers, for a
    # like-for-like sanity column (different window — NOT apples-to-apples,
    # the CI + market gap are the real read on a 100k slice).
    try:
        with open(os.path.join(BASE_DIR, "data", "model_info.json")) as f:
            prod = json.load(f).get("calibration", {})
        metrics["production_reference"] = {
            "note": "production 180d holdout (DIFFERENT window — context only)",
            "eval_log_loss": prod.get("eval_log_loss"),
            "accuracy": prod.get("accuracy"),
            "blended_market_log_loss": prod.get("blended_market_log_loss"),
            "split_at": prod.get("split_at"),
        }
    except Exception:
        pass

    # Per-league breakdown (top leagues by eval volume) — where the model
    # is strong/weak on the large slice.
    leagues_h = meta.get("leagues")
    if leagues_h is not None and len(leagues_h) == len(Xh):
        lg = leagues_h[mid:]
        per_league = []
        for slug in pd.unique(lg):
            m = lg == slug
            n = int(m.sum())
            if n >= 500:
                per_league.append({
                    "league": str(slug),
                    "n": n,
                    "eval_log_loss": round(_ll(p_ev[m], y_ev[m]), 5),
                    "accuracy": round(float((p_ev[m].argmax(1) == y_ev[m]).mean()), 4),
                })
        per_league.sort(key=lambda r: r["eval_log_loss"])
        metrics["per_league"] = per_league

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    # Console summary (Actions log + job summary when $GITHUB_STEP_SUMMARY set).
    lines = [
        "",
        "=" * 64,
        f" 100k out-of-time holdout audit — {metrics['eval_rows']:,} eval rows",
        "=" * 64,
        f" split_at {metrics['split_at']} | train {metrics['train_rows']:,} "
        f"| holdout {metrics['holdout_rows']:,} ({metrics['holdout_span_days']}d)",
        f" eval log loss   {metrics['eval_log_loss']:.5f}  "
        f"95% CI [{lo_ci:.5f}, {hi_ci:.5f}]",
        f"   raw {ll_raw:.5f} | isotonic {ll_cal:.5f} (used={use_cal})",
        f" accuracy        {acc:.4f}",
        f" brier           {metrics['brier']:.5f} | ece {ece:.4f} | "
        f"goals_brier {metrics['goals_brier']:.5f}",
        f" market(closing) {metrics['market_closing_log_loss']} on "
        f"{metrics['market_closing_rows']:,} rows",
        f" blended         {metrics['blended_market_log_loss']} "
        f"(alpha={metrics['alpha_damped']})",
        f" report -> {OUT_PATH}",
        "=" * 64,
    ]
    summary = "\n".join(lines)
    print(summary)

    sm = os.environ.get("GITHUB_STEP_SUMMARY")
    if sm:
        try:
            with open(sm, "a") as fh:
                fh.write("\n## 100k holdout audit (read-only)\n\n")
                fh.write("```\n" + summary + "\n```\n")
                if per_league:
                    fh.write("\n### Per-league (n>=500)\n\n")
                    fh.write("| league | n | log loss | acc |\n|---|---|---|---|\n")
                    for r in per_league[:15]:
                        fh.write(f"| {r['league']} | {r['n']:,} | "
                                 f"{r['eval_log_loss']} | {r['accuracy']} |\n")
        except Exception:
            pass

    # READ-ONLY guarantee: nothing saved, nothing deployed.
    print("[audit] read-only: no model saved, model_info.json untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

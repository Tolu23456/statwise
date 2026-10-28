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

Simulation arm (plan 5.14 companion): AUDIT_SYNTH=1 trains the SAME audit
with Monte Carlo soft-label distillation (retrain.py Step 1.5, TRAIN rows
only) so the two arms are apples-to-apples on the identical split.  The
teacher recipe must PRE-DATE the audit split — ai/sim/distill.py's
freshness guard refuses a teacher whose split_at is later than ours (it
would have seen holdout outcomes).  When the guard rejects, this script
writes a rejection record and exits 1: it never silently falls back to
the baseline (that would masquerade as a sim-arm result).

Usage (Actions):
    cd ai && python audit_100k.py
Env:
    AUDIT_TEST_ROWS        rows to hold out from the end (default 100000)
    AUDIT_OUT              report JSON path (default data/audit_100k_report.json)
    AUDIT_SYNTH            '1' = train with simulation distillation (default off)
    AUDIT_SYNTH_RECIPE     teacher recipe JSON (default data/sim_audit/worlds.json —
                           the pre-split teacher built by audit-sim-teacher.yml;
                           the published lab recipe ai/data/sim/worlds.json has
                           split_at 2026-04-21 and is REJECTED by the guard here)
    AUDIT_SYNTH_FRACTION   fraction of train rows duplicated with teacher labels (0.5)
    AUDIT_SYNTH_SEED       sampling seed (0)
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

# Simulation-distillation arm (off by default: the baseline control).
SYNTH = os.environ.get("AUDIT_SYNTH", "0").strip().lower() in ("1", "true", "yes")
SYNTH_RECIPE = os.environ.get(
    "AUDIT_SYNTH_RECIPE",
    os.path.join(BASE_DIR, "data", "sim_audit", "worlds.json"))
SYNTH_FRACTION = float(os.environ.get("AUDIT_SYNTH_FRACTION", "0.5") or 0.5)
SYNTH_SEED = int(os.environ.get("AUDIT_SYNTH_SEED", "0") or 0)
# AUDIT_PAIR=1: train BOTH arms (baseline + sim) in one job and score them
# on the SAME eval rows, so the sim-vs-baseline delta gets a PAIRED
# significance test instead of overlapping marginal CIs.  Implies SYNTH.
PAIR = os.environ.get("AUDIT_PAIR", "0").strip().lower() in ("1", "true", "yes")
SYNTH = SYNTH or PAIR


def _resolve_recipe(path: str) -> str:
    """Accept both repo-root-relative and ai/-relative recipe paths."""
    if os.path.exists(path):
        return path
    alt = os.path.join(BASE_DIR, path)
    return alt if os.path.exists(alt) else path


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


def _reject_synthetic(synth_info: dict, generated_at: str) -> int:
    """AUDIT_SYNTH=1 asked for distillation but it must not happen.

    Write a rejection record (NOT a metrics report — no model was trained)
    and exit 1 so the run is visibly red.  Silently training the baseline
    instead would masquerade as a sim-arm result.
    """
    synth_info["applied"] = False
    payload = {
        "audit": "100k out-of-time holdout (READ-ONLY, nothing deployed)",
        "status": "distillation-rejected",
        "generated_at": generated_at,
        "distillation": synth_info,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"::error::AUDIT_SYNTH=1 but distillation was rejected: "
          f"{synth_info.get('reason')} — no model trained, see {OUT_PATH}")
    return 1


def _per_league(p_ev, y_ev, lg, ll_fn) -> "list[dict]":
    """Per-league eval breakdown (n>=500), sorted by log loss."""
    import pandas as pd
    out = []
    for slug in pd.unique(lg):
        m = lg == slug
        n = int(m.sum())
        if n >= 500:
            out.append({
                "league": str(slug),
                "n": n,
                "eval_log_loss": round(ll_fn(p_ev[m], y_ev[m]), 5),
                "accuracy": round(float((p_ev[m].argmax(1) == y_ev[m]).mean()), 4),
            })
    out.sort(key=lambda r: r["eval_log_loss"])
    return out


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
          f"({dates.iloc[-1] - split_at} span)")

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

    # ── Simulation-distillation arm (AUDIT_SYNTH=1) ──────────────────────
    # Port of retrain._distill_soft_labels for the audit path: duplicate a
    # fraction of TRAIN rows with teacher-sampled scoreline labels (1X2 +
    # goals/BTTS/O3.5 from the SAME draw — ai/sim/distill.py).  Two
    # differences from retrain: (a) LOUD — a missing recipe or a failed
    # freshness guard exits 1 with a rejection record instead of training
    # the baseline silently; (b) split_at here is the AUDIT split, so the
    # guard guarantees the teacher never saw any audit-holdout outcome.
    # PAIR keeps references to the PRE-distillation train arrays: the block
    # below rebinds names via concatenate and never mutates, so these stay
    # pristine for the baseline arm's second training pass.
    Xt0, yt0, gt0, w0, aux0 = Xt, yt, gt, w_tr, dict(aux_tr)
    synth_info: dict = {"requested": bool(SYNTH)}
    if SYNTH:
        from sim.distill import load_teacher, teacher_may_fit, sample_soft_labels

        recipe_path = _resolve_recipe(SYNTH_RECIPE)
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        synth_info.update({
            "recipe": recipe_path,
            "fraction": SYNTH_FRACTION,
            "seed": SYNTH_SEED,
            "audit_split_at": str(split_at.date()),
        })
        worlds, rmeta = load_teacher(recipe_path)
        if not worlds:
            synth_info["reason"] = f"no usable teacher recipe at {recipe_path}"
            return _reject_synthetic(synth_info, generated_at)
        synth_info["recipe_split_at"] = (rmeta or {}).get("split_at")
        synth_info["teacher_worlds"] = len(worlds)
        ok, why = teacher_may_fit(rmeta or {}, split_at)
        synth_info["guard"] = why
        if not ok:
            synth_info["reason"] = f"leak guard rejected the teacher: {why}"
            return _reject_synthetic(synth_info, generated_at)
        _h = meta.get("home"); _a = meta.get("away"); _l = meta.get("leagues")
        if (_h is None or _a is None or _l is None
                or len(_h) != len(m_tr) or len(_a) != len(m_tr)
                or len(_l) != len(m_tr)):
            synth_info["reason"] = ("meta missing aligned home/away/leagues "
                                    "— teacher cannot price train rows")
            return _reject_synthetic(synth_info, generated_at)
        idx, y_s, g_s, b_s, o_s = sample_soft_labels(
            worlds, _h[m_tr], _a[m_tr], _l[m_tr],
            fraction=SYNTH_FRACTION, seed=SYNTH_SEED)
        if not len(idx):
            synth_info["reason"] = ("sample_soft_labels produced 0 rows — the "
                                    "recipe knows none of the train teams")
            return _reject_synthetic(synth_info, generated_at)
        # TRAIN side only: holdout slices above (Xh, yh, …) never see these
        # rows; the aux labels get the TEACHER's label from the same
        # scoreline draw (retrain.py plan-1.6 semantics).
        synth_info["rows_before"] = int(len(yt))
        Xt = np.concatenate([Xt, Xt[idx]])
        yt = np.concatenate([np.asarray(yt), y_s])
        gt = np.concatenate([np.asarray(gt), g_s])
        w_tr = (np.concatenate([w_tr, w_tr[idx]])
                if w_tr is not None else None)
        if aux_tr["y_btts"] is not None:
            aux_tr["y_btts"] = np.concatenate([aux_tr["y_btts"], b_s])
        if aux_tr["y_o35"] is not None:
            aux_tr["y_o35"] = np.concatenate([aux_tr["y_o35"], o_s])
        synth_info.update({"applied": True, "rows_added": int(len(idx))})
        print(f"[audit] distillation: +{len(idx):,} teacher-labeled "
              f"duplicates ({synth_info['rows_before']:,} -> {Xt.shape[0]:,} "
              f"rows, {len(worlds)} worlds, teacher split "
              f"{synth_info['recipe_split_at']})")

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
    d_ev = d_ho[mid:]   # DatetimeIndex (or Series): plain slicing, no .iloc
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

    # ── Paired significance (AUDIT_PAIR=1) ───────────────────────────────
    # Retrain the BASELINE arm on the pre-distillation arrays with the exact
    # same protocol, then score BOTH models on the SAME eval rows.  The
    # sim-vs-baseline delta gets a paired test (per-row loss differences /
    # McNemar on discordant calls) — far tighter than overlapping marginal
    # CIs because row difficulty cancels out.
    pair = None
    base_block = None
    if PAIR:
        print(f"[audit] pair: training baseline stacks on {Xt0.shape[0]:,} rows…")
        t2 = time.time()
        cand2 = FootballPredictor()
        cand2.feature_pipe = predictor.feature_pipe
        cand2.fit_stacks(Xt0, yt0, gt0, calibrate=False, mode="single",
                         sample_weight=w0,
                         y_btts=aux0["y_btts"], y_o35=aux0["y_o35"])
        raw2 = cand2._outcome_pipe.predict_proba(Xh)
        cals2 = []
        for k in range(3):
            iso2 = IsotonicRegression(out_of_bounds="clip")
            iso2.fit(raw2[:mid, k], (yh[:mid] == k).astype(float))
            cals2.append(iso2)
        p_cal2 = np.column_stack(
            [cals2[k].predict(raw2[mid:, k]) for k in range(3)])
        p_cal2 = np.clip(p_cal2, 1e-6, None)
        p_cal2 = p_cal2 / p_cal2.sum(axis=1, keepdims=True)
        p2_raw = raw2[mid:]
        ll_raw2 = _ll(p2_raw, y_ev)
        ll_cal2 = _ll(p_cal2, y_ev)
        use_cal2 = ll_cal2 < ll_raw2
        p2_ev = p_cal2 if use_cal2 else p2_raw
        ll2 = min(ll_raw2, ll_cal2)
        per_row2 = -np.log(
            np.clip(p2_ev[np.arange(len(y_ev)), y_ev], eps, 1 - eps))
        lo2, hi2 = _ci95(list(per_row2))
        acc2 = float((p2_ev.argmax(axis=1) == y_ev).mean())

        from math import erfc as _erfc, sqrt as _sqrt
        d_ll = per_row - per_row2          # per-row LL difference (sim - base)
        dll_lo, dll_hi = _ci95(list(d_ll))
        dll_mean = float(d_ll.mean())
        dll_se = (dll_hi - dll_lo) / (2 * 1.96)
        dll_z = dll_mean / dll_se if dll_se > 0 else 0.0
        dll_p = float(_erfc(abs(dll_z) / _sqrt(2))) if dll_se > 0 else None

        c1 = p_ev.argmax(axis=1) == y_ev
        c0 = p2_ev.argmax(axis=1) == y_ev
        e = c1.astype(np.float64) - c0.astype(np.float64)
        dacc_lo, dacc_hi = _ci95(list(e))
        n10 = int((c1 & ~c0).sum())   # sim right, baseline wrong
        n01 = int((~c1 & c0).sum())   # baseline right, sim wrong
        if n10 + n01 > 0:
            chi2 = (abs(n10 - n01) - 1) ** 2 / (n10 + n01)
            mcnemar_p = float(_erfc(_sqrt(max(chi2, 0.0) / 2.0)))
        else:
            mcnemar_p = 1.0

        pair = {
            "note": ("both arms trained in THIS job on the identical split; "
                     "delta = sim - baseline (negative LL = sim better)"),
            "ll_delta": round(dll_mean, 6),
            "ll_delta_ci95": [round(dll_lo, 6), round(dll_hi, 6)],
            "ll_delta_z": round(dll_z, 3),
            "ll_delta_p": round(dll_p, 6) if dll_p is not None else None,
            "ll_delta_significant": bool(dll_p is not None and dll_p < 0.05),
            "accuracy_delta": round(float(e.mean()), 5),
            "accuracy_delta_ci95": [round(dacc_lo, 5), round(dacc_hi, 5)],
            "discordant": {"sim_right_base_wrong": n10,
                           "base_right_sim_wrong": n01},
            "mcnemar_p": round(mcnemar_p, 6),
        }
        base_block = {
            "eval_log_loss": round(ll2, 5),
            "eval_log_loss_ci95": [round(lo2, 5), round(hi2, 5)],
            "accuracy": round(acc2, 4),
            "brier": round(float(((p2_ev - np.eye(3)[y_ev]) ** 2).mean()), 5),
            "raw_log_loss": round(ll_raw2, 5),
            "isotonic_log_loss": round(ll_cal2, 5),
            "calibration_used": bool(use_cal2),
            "train_seconds": round(time.time() - t2, 1),
        }
        print(f"[audit] pair: baseline LL {ll2:.5f} [{lo2:.5f}, {hi2:.5f}] "
              f"acc {acc2:.4f} | sim-baseline dLL {dll_mean:+.5f} "
              f"[{dll_lo:+.5f}, {dll_hi:+.5f}] p={dll_p:.4g} | "
              f"McNemar p={mcnemar_p:.4g}")

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
        "distillation": synth_info,
        "paired": pair,
        "paired_baseline": base_block,
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
        # Top-1 (argmax) accuracy of the market's own implied probabilities,
        # on the SAME eval rows — the honest yardstick for "what accuracy is
        # even achievable on 3-way 1X2" (football outcomes are intrinsically
        # uncertain; the market's closing prices sit around 0.99-1.00 LL).
        "market_closing_accuracy": round(
            float((mkt_c[valid_c].argmax(1) == y_ev[valid_c]).mean()), 4)
        if int(valid_c.sum()) > 50 else None,
        "blended_accuracy": round(
            float(((alpha_dep * p_ev[valid_c]
                    + (1.0 - alpha_dep) * mkt_c[valid_c]).argmax(1)
                   == y_ev[valid_c]).mean()), 4)
        if int(valid_c.sum()) > 50 else None,
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
    # is strong/weak on the large slice.  meta["leagues"] is aligned to the
    # FULL feature frame (the rows m_ho masks), not to Xh — slice with the
    # holdout mask first.  (The old len==len(Xh) check could never fire,
    # which is why earlier reports carried no per_league at all.)
    per_league = []
    lg_ev = None
    leagues_h = meta.get("leagues")
    if leagues_h is not None and len(leagues_h) == len(X_all):
        lg_ev = leagues_h[m_ho][mid:]
        per_league = _per_league(p_ev, y_ev, lg_ev, _ll)
        metrics["per_league"] = per_league
    # Same breakdown for the paired baseline arm, side-by-side comparable.
    if PAIR and base_block is not None and lg_ev is not None:
        base_block["per_league"] = _per_league(p2_ev, y_ev, lg_ev, _ll)

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
        " distillation     " + (
            f"ON: +{synth_info.get('rows_added', 0):,} teacher rows "
            f"(recipe split {synth_info.get('recipe_split_at')}, "
            f"{synth_info.get('teacher_worlds')} worlds | "
            f"guard: {synth_info.get('guard')})"
            if synth_info.get("applied") else "off"),
        f" eval log loss   {metrics['eval_log_loss']:.5f}  "
        f"95% CI [{lo_ci:.5f}, {hi_ci:.5f}]",
        f"   raw {ll_raw:.5f} | isotonic {ll_cal:.5f} (used={use_cal})",
        f" accuracy        {acc:.4f}",
        f" brier           {metrics['brier']:.5f} | ece {ece:.4f} | "
        f"goals_brier {metrics['goals_brier']:.5f}",
        f" market(closing) {metrics['market_closing_log_loss']} on "
        f"{metrics['market_closing_rows']:,} rows | "
        f"acc {metrics['market_closing_accuracy']}",
        f" blended         {metrics['blended_market_log_loss']} "
        f"(alpha={metrics['alpha_damped']})",
        f" report -> {OUT_PATH}",
    ] + ([
        f" PAIR baseline    LL {base_block['eval_log_loss']:.5f} "
        f"CI {base_block['eval_log_loss_ci95']} "
        f"| acc {base_block['accuracy']:.4f}",
        f" delta (sim-base) LL {pair['ll_delta']:+.5f} "
        f"CI {pair['ll_delta_ci95']} p={pair['ll_delta_p']} "
        f"sig={pair['ll_delta_significant']}",
        f"                   acc {pair['accuracy_delta']:+.5f} "
        f"CI {pair['accuracy_delta_ci95']} "
        f"| discordant {pair['discordant']} McNemar p={pair['mcnemar_p']}",
    ] if pair else []) + [
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
                if pair:
                    fh.write("\n### Paired: sim vs baseline (same eval rows)\n\n")
                    fh.write(f"- baseline LL {base_block['eval_log_loss']} "
                             f"CI {base_block['eval_log_loss_ci95']} | "
                             f"sim LL {metrics['eval_log_loss']} "
                             f"CI {metrics['eval_log_loss_ci95']}\n")
                    fh.write(f"- delta {pair['ll_delta']:+.5f} "
                             f"CI {pair['ll_delta_ci95']} "
                             f"p={pair['ll_delta_p']} "
                             f"(significant: {pair['ll_delta_significant']})\n")
                    fh.write(f"- accuracy delta {pair['accuracy_delta']:+.5f} "
                             f"CI {pair['accuracy_delta_ci95']} "
                             f"McNemar p={pair['mcnemar_p']} "
                             f"discordant={pair['discordant']}\n")
        except Exception:
            pass

    # READ-ONLY guarantee: nothing saved, nothing deployed.
    print("[audit] read-only: no model saved, model_info.json untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())

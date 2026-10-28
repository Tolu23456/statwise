"""
Experiment library for the StatWise model-improvement campaign (plan 5.16).

READ-ONLY with respect to production paths: nothing in here writes
ai/models/**, touches model_info.json, calls predictor.save() or reads
FORCE_REPLACE.  It REUSES (imports/copies, never modifies) the production
training path so every arm is measured with the production recipe on the
identical 100k out-of-time audit split (see ai/audit_100k.py).

Heavy training belongs in GitHub Actions (.github/workflows/exp-audit.yml).
Local runs stay tiny via EXP_MAX_ROWS / EXP_TEST_ROWS.
"""
from __future__ import annotations

import math
import os
import sys
import time

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Production learner hyper-parameters, copied verbatim from
# model.trainer._lgbm_single so an arm with no overrides is bit-identical
# to the production recipe (drift here would invalidate every comparison).
PROD_PARAMS = dict(
    n_estimators=800,
    learning_rate=0.035,
    num_leaves=96,
    max_bin=127,
    min_child_samples=50,
    subsample=0.85,
    subsample_freq=1,
    colsample_bytree=0.7,
    reg_alpha=0.2,
    reg_lambda=2.0,
    force_row_wise=True,
    random_state=42,
    n_jobs=4,
    verbosity=-1,
)

PROD_HALF_LIFE = 1460.0          # production recency half-life (days)
AUDIT_BASELINE_LL = 1.01581      # plan 5.14 headline (guard target)
AUDIT_BASELINE_ACC = 0.4912


# ─────────────────────────────────────────────────────────────────────────────
# Frame / features / split
# ─────────────────────────────────────────────────────────────────────────────
def load_frame(max_rows: int = 0) -> pd.DataFrame:
    """Production clean corpus, sorted chronologically (audit-identical)."""
    from retrain import _load_clean_data
    df = _load_clean_data()
    if df.empty:
        raise RuntimeError("no clean data — aborting experiment")
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    if max_rows and len(df) > max_rows:
        df = df.tail(int(max_rows)).reset_index(drop=True)
    return df


def build_features(df: pd.DataFrame):
    """Feature matrix + labels + meta via the production pipeline (one pass).

    Returns (X_all float32, y_all, g_all, d_all, meta, predictor).  The
    audit's KNOWN SMALL LEAK (league-level aggregates fit on the full frame)
    is inherited unchanged — comparability with 1.01581 matters more here
    than removing it, and it is the same for every arm.
    """
    from model.trainer import FootballPredictor
    predictor = FootballPredictor()
    predictor.feature_pipe.fit(df)
    X_all, y_all, g_all, d_all = predictor.feature_pipe.build_training_set(df)
    X_all = np.nan_to_num(X_all, nan=0.0, posinf=1e6, neginf=-1e6).astype(
        np.float32, copy=False)
    meta = getattr(predictor.feature_pipe, "last_meta", {}) or {}
    return X_all, y_all, g_all, d_all, meta, predictor


def all_idx_for(n_built: int) -> np.ndarray:
    """Row indices build_training_set actually emitted (features.py semantics).

    `n_built` is the LENGTH of the built matrix (X_all), which already
    reflects any MAX_TRAINING_SAMPLES cap, so these indices are guaranteed
    to align with X_all / meta row-for-row.
    """
    return np.arange(int(n_built), dtype=np.int64)


def split_masks(full_dates, d_all, test_rows: int):
    """Audit split, semantics identical to ai/audit_100k.py.

    The boundary date is taken from the FULL sorted corpus (`full_dates`,
    position n_total - test_rows) and then applied as a mask over the BUILT
    feature rows (`d_all`), which is exactly how audit_100k.py derives its
    232,017 train rows.  Returns (split_at, m_tr, m_ho) with masks aligned
    to d_all / X_all.
    """
    full = pd.to_datetime(full_dates, errors="coerce")
    n_total = len(full)
    if n_total <= test_rows + 5000:
        raise RuntimeError(f"dataset ({n_total:,}) too small for {test_rows:,}")
    split_at = full[n_total - test_rows]
    built = pd.to_datetime(d_all, errors="coerce")
    m_tr = built <= split_at
    m_ho = ~m_tr
    return split_at, m_tr, m_ho


def recency_weights(d_all, m_tr, half_life: float):
    """Production weight curve (retrain._recency_weights / audit_100k)."""
    dates_all = pd.to_datetime(d_all, errors="coerce")
    ref_date = dates_all[m_tr].max()
    if not half_life or half_life <= 0:
        return None
    age_days = np.asarray((ref_date - dates_all) / pd.Timedelta(days=1),
                          dtype=np.float64)
    w = np.power(0.5, np.clip(age_days, 0.0, None) / float(half_life))
    return np.maximum(w, 0.02).astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Training (outcome head only — identical to the audit's outcome head)
# ─────────────────────────────────────────────────────────────────────────────
def build_estimator(seed: int = 42, **overrides):
    from lightgbm import LGBMClassifier
    params = dict(PROD_PARAMS)
    params["random_state"] = int(seed)
    params.update(overrides)
    return LGBMClassifier(**params)


def fit_outcome_head(X, y, sample_weight=None, seed: int = 42, **overrides):
    """One 3-class LightGBM head, early-stopped exactly like production."""
    from model.trainer import _fit_with_early_stopping
    est = build_estimator(seed, **overrides)
    _fit_with_early_stopping(est, X, y, sample_weight=sample_weight)
    return est


# ─────────────────────────────────────────────────────────────────────────────
# Metrics / calibration
# ─────────────────────────────────────────────────────────────────────────────
def ll(p, y) -> float:
    from sklearn.metrics import log_loss
    return float(log_loss(y, p, labels=[0, 1, 2]))


def per_row_ll(p, y, eps: float = 1e-12) -> np.ndarray:
    p = np.asarray(p, dtype=np.float64)
    y = np.asarray(y)
    return -np.log(np.clip(p[np.arange(len(y)), y], eps, 1.0 - eps))


def ci95(rows) -> tuple[float, float]:
    a = np.asarray(rows, dtype=np.float64)
    if len(a) < 2:
        return (float("nan"), float("nan"))
    se = float(a.std(ddof=1) / math.sqrt(len(a)))
    m = float(a.mean())
    return (m - 1.96 * se, m + 1.96 * se)


def ece_of(p, y, n_bins: int = 10) -> float:
    conf = p.max(axis=1)
    correct = (p.argmax(axis=1) == np.asarray(y)).astype(float)
    edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for bi in range(n_bins):
        m = (conf >= edges[bi]) & (conf < edges[bi + 1] if bi < n_bins - 1
                                   else conf <= edges[bi + 1])
        if m.sum():
            ece += float(m.mean()) * abs(float(correct[m].mean())
                                         - float(conf[m].mean()))
    return float(ece)


def metrics_block(p, y) -> dict:
    p = np.asarray(p)
    y = np.asarray(y)
    onehot = np.eye(3)[y]
    return {
        "eval_log_loss": round(ll(p, y), 5),
        "accuracy": round(float((p.argmax(1) == y).mean()), 4),
        "brier": round(float(((p - onehot) ** 2).mean()), 5),
        "ece": round(ece_of(p, y), 4),
    }


def paired_test(p_a, p_b, y) -> dict:
    """Paired comparison of two probability sets on the SAME rows.

    delta = a - b (negative LL delta = a better).  Per-row loss differences
    -> normal-approx CI/z/p; accuracy discordance -> McNemar (chi-square
    with continuity correction, same formula as ai/audit_100k.py).
    """
    la = per_row_ll(p_a, y)
    lb = per_row_ll(p_b, y)
    d = la - lb
    lo, hi = ci95(list(d))
    mean = float(d.mean())
    se = (hi - lo) / (2 * 1.96)
    z = mean / se if se > 0 else 0.0
    p_val = float(math.erfc(abs(z) / math.sqrt(2))) if se > 0 else None

    ca = p_a.argmax(axis=1) == y
    cb = p_b.argmax(axis=1) == y
    e = ca.astype(np.float64) - cb.astype(np.float64)
    alo, ahi = ci95(list(e))
    n10 = int((ca & ~cb).sum())
    n01 = int((~ca & cb).sum())
    if n10 + n01 > 0:
        chi2 = (abs(n10 - n01) - 1) ** 2 / (n10 + n01)
        mcnemar_p = float(math.erfc(math.sqrt(max(chi2, 0.0) / 2.0)))
    else:
        mcnemar_p = 1.0
    return {
        "ll_delta": round(mean, 6),
        "ll_delta_ci95": [round(lo, 6), round(hi, 6)],
        "ll_delta_z": round(z, 3),
        "ll_delta_p": round(p_val, 8) if p_val is not None else None,
        "ll_delta_significant_001": bool(p_val is not None and p_val < 0.01),
        "ll_delta_significant_005": bool(p_val is not None and p_val < 0.05),
        "accuracy_delta": round(float(e.mean()), 5),
        "accuracy_delta_ci95": [round(alo, 5), round(ahi, 5)],
        "discordant": {"a_right_b_wrong": n10, "b_right_a_wrong": n01},
        "mcnemar_p": round(mcnemar_p, 8),
        "ece_delta": round(ece_of(p_a, y) - ece_of(p_b, y), 5),
        "rows": int(len(y)),
    }


def fit_iso3(raw_cal: np.ndarray, y_cal) -> list:
    from sklearn.isotonic import IsotonicRegression
    cals = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_cal[:, k], (np.asarray(y_cal) == k).astype(float))
        cals.append(iso)
    return cals


def apply_iso(cals: list, raw: np.ndarray) -> np.ndarray:
    q = np.column_stack([iso.predict(np.asarray(raw)[:, k])
                         for k, iso in enumerate(cals)])
    q = np.clip(q, 1e-6, None)
    return q / q.sum(axis=1, keepdims=True)


def fit_platt3(raw_cal: np.ndarray, y_cal, raw_ev: np.ndarray):
    """Per-class Platt scaling on the logit of the raw probability."""
    from sklearn.linear_model import LogisticRegression
    eps = 1e-6
    def logits(p):
        p = np.clip(np.asarray(p, dtype=np.float64), eps, 1 - eps)
        return np.log(p / (1 - p))
    y = np.asarray(y_cal)
    models = []
    for k in range(3):
        m = LogisticRegression(max_iter=2000)
        m.fit(logits(raw_cal[:, k]).reshape(-1, 1), (y == k).astype(int))
        models.append(m)
    q = np.column_stack([m.predict_proba(logits(raw_ev)[:, [k]])[:, 1]
                         for k, m in enumerate(models)])
    q = np.clip(q, 1e-6, None)
    return q / q.sum(axis=1, keepdims=True)


def fit_vector_scaling(raw_cal: np.ndarray, y_cal, raw_ev: np.ndarray,
                       l2: float = 0.01):
    """p'_k ∝ exp(a_k log p_k + b_k), fitted by NLL (weak L2 on (a,b))."""
    from scipy.optimize import minimize
    eps = 1e-9
    pc = np.clip(np.asarray(raw_cal, dtype=np.float64), eps, 1 - eps)
    logp = np.log(pc)
    y = np.asarray(y_cal)
    pe = np.clip(np.asarray(raw_ev, dtype=np.float64), eps, 1 - eps)
    loge = np.log(pe)

    def nll(theta):
        a = theta[:3]
        b = theta[3:]
        z = logp * a[None, :] + b[None, :]
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        pr = e / e.sum(axis=1, keepdims=True)
        loss = -np.log(np.clip(pr[np.arange(len(y)), y], 1e-12, 1.0)).mean()
        reg = l2 * (((a - 1.0) ** 2).sum() + (b ** 2).sum())
        return loss + reg

    res = minimize(nll, np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0]),
                   method="L-BFGS-B")
    a, b = res.x[:3], res.x[3:]
    z = loge * a[None, :] + b[None, :]
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    q = e / e.sum(axis=1, keepdims=True)
    return q, {"a": [round(float(v), 4) for v in a],
               "b": [round(float(v), 4) for v in b]}


def fit_dirichlet(raw_cal: np.ndarray, y_cal, raw_ev: np.ndarray, C: float = 1.0):
    """Dirichlet-style matrix scaling: multinomial logistic on log probs."""
    from sklearn.linear_model import LogisticRegression
    eps = 1e-9
    def feats(p):
        return np.log(np.clip(np.asarray(p, dtype=np.float64), eps, 1 - eps))
    m = LogisticRegression(C=C, max_iter=2000)
    m.fit(feats(raw_cal), np.asarray(y_cal))
    q = m.predict_proba(feats(raw_ev))
    q = np.clip(q, 1e-6, None)
    return q / q.sum(axis=1, keepdims=True)


def fit_league_isotonic(raw_cal, y_cal, lg_cal, raw_ev, lg_ev,
                        shrink_k: float = 200.0, min_n: int = 50):
    """Per-league isotonic blended toward the global isotonic, w = n/(n+k)."""
    global_cals = fit_iso3(raw_cal, y_cal)
    base = apply_iso(global_cals, raw_ev)
    lg_cal = np.asarray(lg_cal)
    lg_ev = np.asarray(lg_ev)
    used = {}
    total = 0
    for slug in pd.unique(lg_cal):
        m_tr = lg_cal == slug
        n = int(m_tr.sum())
        if n < min_n:
            continue
        iso = fit_iso3(np.asarray(raw_cal)[m_tr], np.asarray(y_cal)[m_tr])
        sub = apply_iso(iso, np.asarray(raw_ev)[lg_ev == slug])
        w = n / (n + shrink_k)
        sel = lg_ev == slug
        base[sel] = (1.0 - w) * base[sel] + w * sub
        used[str(slug)] = round(float(w), 3)
        total += 1
    base = np.clip(base, 1e-6, None)
    base = base / base.sum(axis=1, keepdims=True)
    return base, {"leagues": total, "weights": used}


# ─────────────────────────────────────────────────────────────────────────────
# Market blend (retrain.damp_alpha / _devig / _valid_odds semantics)
# ─────────────────────────────────────────────────────────────────────────────
def devig(o) -> np.ndarray:
    imp = 1.0 / np.maximum(np.asarray(o, dtype=float), 1.0001)
    return imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)


def valid_odds(o) -> np.ndarray:
    a = np.asarray(o, dtype=float)
    return (a[:, 0] > 1.01) & (a[:, 1] > 1.01) & (a[:, 2] > 1.01)


def best_alpha(p, mkt, y, mask, step: float = 0.05):
    m = np.asarray(mask)
    if int(m.sum()) < 50:
        return None
    best_a, best_ll = None, float("inf")
    a = 0.0
    while a <= 1.0001:
        v = ll(a * p[m] + (1.0 - a) * mkt[m], y[m])
        if v < best_ll:
            best_ll, best_a = v, float(a)
        a += step
    return float(best_a)


def damp_alpha(fit_alpha, n_valid, prior: float = 0.35,
               prior_k: float = 1000.0) -> float:
    if fit_alpha is None:
        return round(float(prior), 4)
    if prior_k <= 0:
        return round(float(fit_alpha), 4)
    w = float(n_valid) / (float(n_valid) + float(prior_k))
    return round(float(w) * float(fit_alpha) + (1.0 - float(w)) * float(prior), 4)


def league_alphas(p_cal, mkt_cal, y_cal, lg_cal, valid_cal,
                  global_alpha: float, shrink_k: float = 300.0,
                  min_n: int = 60) -> dict:
    """Per-league alpha fitted on the CALIBRATION window only (E2)."""
    out = {}
    lg_cal = np.asarray(lg_cal)
    for slug in pd.unique(lg_cal):
        m = (lg_cal == slug) & valid_cal
        n_l = int(m.sum())
        if n_l < min_n:
            continue
        a = best_alpha(p_cal, mkt_cal, y_cal, m, step=0.01)
        if a is None:
            continue
        w = n_l / (n_l + shrink_k)
        out[str(slug)] = round(float(w * a + (1.0 - w) * global_alpha), 4)
    return out


def blend_apply(p, mkt, alphas: dict | None, lg, default: float) -> np.ndarray:
    out = np.array(p, dtype=np.float64, copy=True)
    lg = np.asarray(lg)
    for slug in pd.unique(lg):
        a = default
        if alphas:
            a = float(alphas.get(str(slug), default))
        sel = lg == slug
        out[sel] = a * out[sel] + (1.0 - a) * mkt[sel]
    out = np.clip(out, 1e-12, None)
    return out / out.sum(axis=1, keepdims=True)


def per_league(p_ev, y_ev, lg, min_n: int = 500) -> list:
    out = []
    y_ev = np.asarray(y_ev)
    lg = np.asarray(lg)
    for slug in pd.unique(lg):
        m = lg == slug
        n = int(m.sum())
        if n >= min_n:
            out.append({
                "league": str(slug),
                "n": n,
                "eval_log_loss": round(ll(np.asarray(p_ev)[m], y_ev[m]), 5),
                "accuracy": round(float((np.asarray(p_ev)[m].argmax(1)
                                         == y_ev[m]).mean()), 4),
            })
    out.sort(key=lambda r: r["eval_log_loss"])
    return out

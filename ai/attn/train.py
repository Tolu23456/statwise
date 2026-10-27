#!/usr/bin/env python3
"""Train the attention student — CI entry point.

    python -m attn.train [--out PATH] [--report PATH] [--epochs N] [--smoke]

Protocol (mirrors ai/retrain.py line for line so every number is
apples-to-apples with the deployed Pico baseline):

  * same clean-data load, same fitted FeaturePipeline, same out-of-time
    holdout split (HOLDOUT_DAYS, default 180): first holdout half fits the
    isotonic calibrator, second half is evaluated EXACTLY ONCE;
  * hyper-parameters and early stopping see only a train-side inner tail
    (ATTN_INNER_VAL rows at the end of the train slice) — never the holdout;
  * the Monte Carlo lab recipe enters as 13 extra input tokens
    (ai/attn/teacher.py, guarded by teacher_may_fit: recipe must have been
    fit on data ending no later than our split) and optionally as soft
    1X2 targets (ATTN_DISTILL_WEIGHT, default 0 = off) — synthetic mass
    never becomes raw rows;
  * production serving runs the pure-NumPy mirror; the torch<->numpy parity
    gap is measured on the inner-val batch and recorded in the report;
  * report: eval LL/acc/brier/ECE + goals/btts/o35 briers + market
    baselines + in-run LightGBM single-mode reference, next to the deployed
    Pico numbers from ai/data/model_info.json.

Outputs: the student bundle (models/attn_student.pkl — standardisers +
weights + recipe) and attn/reports/attn_report.json.
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import sys
import time

AI_DIR = os.path.dirname(os.path.abspath(__file__))   # .../ai/attn
AI_ROOT = os.path.dirname(AI_DIR)                     # .../ai
if AI_ROOT not in sys.path:
    sys.path.insert(0, AI_ROOT)

import numpy as np

from attn.config import AttnConfig
from attn.teacher import N_TEACHER, load_recipe, teacher_matrix
# NOTE: attn.model (torch) is imported LAZILY inside the functions that need
# it — libtorch's footprint has no business sitting in RSS while the 377k-row
# frame and the teacher batches are being built (CI runners are roomy; the
# dev box is 2.7 GiB shared with other work).

logger = logging.getLogger("statwise.attn.train")


# ── path helpers ─────────────────────────────────────────────────────────
def _resolve(p: str) -> str:
    """Config paths are repo-root relative ('ai/...'); tolerate cwd=ai too."""
    if os.path.isabs(p):
        return p
    repo = os.path.dirname(AI_ROOT)
    if os.path.exists(p):
        return p
    if os.path.exists(os.path.join(repo, p)):
        return os.path.join(repo, p)
    if os.path.exists(os.path.join(AI_ROOT, p)):
        return os.path.join(AI_ROOT, p)
    head = p.split(os.sep)[0]
    if head in ("ai", "statwise", ".github"):
        return os.path.join(repo, p)
    return os.path.abspath(p)


# ── metric helpers (identical formulas to ai/retrain.py) ────────────────
def _softmax(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z, dtype=np.float64)
    e = np.exp(z - z.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def _ll(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(p, dtype=np.float64), 1e-12, 1.0)
    return float(-np.log(p[np.arange(len(y)), y]).mean())


def _ece(p: np.ndarray, y: np.ndarray) -> float:
    conf = p.max(axis=1)
    correct = (p.argmax(axis=1) == y).astype(float)
    edges = np.linspace(0, 1, 11)
    out = 0.0
    for bi in range(10):
        m = (conf >= edges[bi]) & (conf < edges[bi + 1] if bi < 9
                                   else conf <= edges[bi + 1])
        if m.sum():
            out += float(m.mean()) * abs(float(correct[m].mean())
                                          - float(conf[m].mean()))
    return out


def _isotonic_fit(raw_cal, y_cal):
    from sklearn.isotonic import IsotonicRegression
    cals = []
    for k in range(3):
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(raw_cal[:, k], (y_cal == k).astype(float))
        cals.append(iso)
    return cals


def _isotonic_apply(raw, cals) -> np.ndarray:
    q = np.column_stack([iso.predict(raw[:, k]) for k, iso in enumerate(cals)])
    q = np.clip(q, 1e-6, None)
    return q / q.sum(axis=1, keepdims=True)


# ── training ─────────────────────────────────────────────────────────────
def train_model(Ztr: np.ndarray, tgt: np.ndarray, wtr: np.ndarray,
                aux_tr: dict, Zv: np.ndarray, yv: np.ndarray, cfg) \
        -> tuple[dict, dict]:
    """AdamW + warmup/cosine; early-stop on inner-val 1X2 LL (train side).

    Returns (torch_module, history).  Only ever sees the train slice.
    """
    import torch
    import torch.nn.functional as F
    from attn.model import TabTransformer

    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)
    n = len(Ztr)
    model = TabTransformer(n_tokens=Ztr.shape[1], d_model=cfg.d_model,
                           n_heads=cfg.n_heads, n_layers=cfg.n_layers,
                           ff_mult=cfg.ff_mult, dropout=cfg.dropout)
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr,
                            weight_decay=cfg.weight_decay)

    Zt = torch.from_numpy(np.ascontiguousarray(Ztr))
    Tt = torch.from_numpy(np.ascontiguousarray(tgt))
    Wt = torch.from_numpy(np.ascontiguousarray(wtr))
    aux_t = {k: torch.from_numpy(np.ascontiguousarray(v, dtype=np.float32))
             for k, v in aux_tr.items() if v is not None}
    Zv_t = torch.from_numpy(np.ascontiguousarray(Zv))
    yv_t = torch.from_numpy(np.ascontiguousarray(yv.astype(np.int64)))

    batch = cfg.batch_size
    steps_per_epoch = max(1, (n + batch - 1) // batch)
    total_steps = max(1, cfg.epochs * steps_per_epoch)
    warmup = max(1, int(total_steps * cfg.warmup_frac))

    def _lr_lambda(step: int) -> float:
        if step < warmup:
            return (step + 1) / warmup
        # cosine decay to ~5% of peak
        import math
        t = (step - warmup) / max(1, total_steps - warmup)
        return 0.05 + 0.95 * 0.5 * (1.0 + math.cos(math.pi * min(t, 1.0)))

    sched = torch.optim.lr_scheduler.LambdaLR(opt, _lr_lambda)

    eval_chunk = max(64, int(os.environ.get("ATTN_EVAL_CHUNK", "1024")))

    def _eval_val() -> float:
        model.eval()
        with torch.no_grad():
            logits = torch.cat([model(Zv_t[i:i + eval_chunk])["logits_1x2"]
                                for i in range(0, len(Zv_t), eval_chunk)])
        return _ll(torch.softmax(logits, 1).numpy(), yv)

    history = []
    best_ll, best_state, bad = float("inf"), None, 0
    t0 = time.time()
    for ep in range(cfg.epochs):
        model.train()
        perm = torch.randperm(n)
        ep_loss = 0.0
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            wb = Wt[idx]
            wsum = wb.sum().clamp_min(1e-8)
            out = model(Zt[idx])
            logp = F.log_softmax(out["logits_1x2"], dim=1)
            loss_1x2 = (wb * -(Tt[idx] * logp).sum(-1)).sum() / wsum * cfg.w_1x2
            loss = loss_1x2
            for key, head, weight in (("g", "logit_o25", cfg.w_goals),
                                      ("btts", "logit_btts", cfg.w_btts),
                                      ("o35", "logit_o35", cfg.w_o35)):
                if key in aux_t and weight > 0:
                    bce = F.binary_cross_entropy_with_logits(
                        out[head].squeeze(-1), aux_t[key][idx],
                        reduction="none")
                    loss = loss + (wb * bce).sum() / wsum * weight
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            ep_loss += float(loss.detach())
        val_ll = _eval_val()
        history.append({"epoch": ep, "train_loss": round(ep_loss / max(1, steps_per_epoch), 5),
                        "inner_val_ll": round(val_ll, 5),
                        "lr": round(opt.param_groups[0]["lr"], 7)})
        improved = val_ll < best_ll - 1e-4
        if improved:
            best_ll, bad = val_ll, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        logger.info(f"[attn] epoch {ep + 1}/{cfg.epochs} "
                    f"loss={ep_loss / max(1, steps_per_epoch):.4f} "
                    f"inner_val_ll={val_ll:.5f}"
                    f"{' *best*' if improved else ''} ({time.time() - t0:.0f}s)")
        if bad >= cfg.patience:
            logger.info(f"[attn] early stop at epoch {ep + 1} "
                        f"(best inner-val LL {best_ll:.5f})")
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return model, {"history": history, "best_inner_val_ll": round(best_ll, 5),
                   "best_epoch": int(np.argmin([h["inner_val_ll"] for h in history]))}


def _lgbm_reference(Xtr, ytr, gtr, wtr, aux, Xh, yh, gh, bh, oh, mid):
    """In-run Pico-style single-mode LightGBM on the SAME slices."""
    try:
        from model.trainer import FootballPredictor
        t0 = time.time()
        ref = FootballPredictor()
        ref.fit_stacks(Xtr, ytr, gtr, calibrate=False, mode="single",
                       sample_weight=wtr,
                       y_btts=aux.get("y_btts"), y_o35=aux.get("y_o35"))
        raw = ref._outcome_pipe.predict_proba(Xh)
        cals = _isotonic_fit(raw[:mid], yh[:mid])
        p_cal = _isotonic_apply(raw[mid:], cals)
        ll_raw, ll_cal = _ll(raw[mid:], yh[mid:]), _ll(p_cal, yh[mid:])
        use = ll_cal < ll_raw
        p_ev = p_cal if use else raw[mid:]
        goals_brier = float(((ref._goals_pipe.predict_proba(Xh[mid:])[:, 1]
                              - gh[mid:]) ** 2).mean())
        out = {"ll_raw": round(ll_raw, 5), "ll_cal": round(ll_cal, 5),
               "eval_ll": round(min(ll_raw, ll_cal), 5),
               "use_cal": bool(use),
               "acc": round(float((p_ev.argmax(1) == yh[mid:]).mean()), 4),
               "brier": round(float(((p_ev - np.eye(3)[yh[mid:]]) ** 2).mean()), 5),
               "goals_brier": round(goals_brier, 5),
               "seconds": round(time.time() - t0, 1)}
        del ref
        return out
    except Exception as e:                                # noqa: BLE001
        logger.warning(f"[attn] LightGBM reference failed: {e}")
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None, help="student bundle path")
    ap.add_argument("--report", default=None, help="report JSON path")
    ap.add_argument("--epochs", type=int, default=None)
    ap.add_argument("--smoke", action="store_true",
                    help="tiny end-to-end run for local debugging")
    ap.add_argument("--skip-lgbm", action="store_true",
                    help="skip the in-run LightGBM reference")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s")

    cfg = AttnConfig()
    if args.epochs:
        cfg.epochs = args.epochs
    if args.smoke:
        cfg.epochs = min(cfg.epochs, 2)
        # batch 512 keeps ~1GB of attention maps (B,H,T,T) alive per layer and
        # a 4096-row eval chunk adds ~1.5 GB; fine on CI (16 GiB) but the
        # 2.7 GiB dev box needs both tiny.
        cfg.batch_size = min(cfg.batch_size, 32)
        cfg.patience = 1
        os.environ.setdefault("ATTN_TEACHER_BATCH", "1500")
        os.environ.setdefault("ATTN_EVAL_CHUNK", "256")
    out_path = _resolve(args.out or cfg.model_out)
    rep_path = _resolve(args.report or cfg.report_out)
    logger.info(f"[attn] out={out_path} report={rep_path} "
                f"epochs={cfg.epochs} batch={cfg.batch_size} "
                f"seed={cfg.seed} distill_weight={cfg.distill_weight}")

    import pandas as pd
    from retrain import _devig, _load_clean_data, _valid_odds, damp_alpha

    t_start = time.time()
    timing: dict = {}

    # ── 1. data + features: identical to ai/retrain.py ──────────────
    df = _load_clean_data()
    if df.empty:
        logger.error("[attn] no clean data at ai/data/clean/")
        return 2
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    if args.smoke:
        df = df.tail(int(os.environ.get("ATTN_SMOKE_TAIL", "40000"))).reset_index(drop=True)
    dates_all = pd.to_datetime(df["date"], errors="coerce")
    holdout_days = int(cfg.holdout_days)
    split_at = dates_all.max() - pd.Timedelta(days=holdout_days)

    from model.trainer import FootballPredictor
    t0 = time.time()
    predictor = FootballPredictor()
    predictor.feature_pipe.fit(df)
    X, y, g, d = predictor.feature_pipe.build_training_set(df)
    X = np.nan_to_num(X, nan=0.0, posinf=1e6, neginf=-1e6).astype(np.float32,
                                                                  copy=False)
    meta = getattr(predictor.feature_pipe, "last_meta", {}) or {}
    timing["features_s"] = round(time.time() - t0, 1)
    cfg.n_features = int(X.shape[1])
    logger.info(f"[attn] features {X.shape[0]:,} x {X.shape[1]} "
                f"(split_at={split_at.date()}, holdout={holdout_days}d, "
                f"{timing['features_s']}s)")
    # The full 332k-row frame and the fitted pipe have served their purpose
    # (X/y/g/d + meta are copies).  Drop them BEFORE the teacher batches so
    # the 2.7 GiB dev box never tips into the OOM killer (CI: 16 GiB).
    del df
    predictor = None
    gc.collect()

    dts = pd.to_datetime(d)
    m_tr = dts <= split_at
    m_ho = ~m_tr
    n_tr, n_ho = int(m_tr.sum()), int(m_ho.sum())
    if n_tr < 2000 or n_ho < 300:
        logger.error(f"[attn] split too small: train={n_tr} holdout={n_ho}")
        return 2

    # ── 2. teacher features (guarded; neutral rows when inactive) ───
    recipe_path = _resolve(cfg.recipe_path)
    worlds, rmeta = load_recipe(recipe_path, split_at)
    home = meta.get("home")
    away = meta.get("away")
    leagues = meta.get("leagues")
    teacher_active = bool(worlds) and home is not None \
        and away is not None and leagues is not None and len(home) == len(X)
    recipe_raw = None
    if teacher_active:
        t0 = time.time()
        try:
            with open(recipe_path, encoding="utf-8") as fh:
                recipe_raw = json.load(fh)
            t_batch = int(os.environ.get("ATTN_TEACHER_BATCH", "4000") or 4000)
            T = teacher_matrix(worlds, home, away, leagues, batch=t_batch)
            timing["teacher_s"] = round(time.time() - t0, 1)
            logger.info(f"[attn] teacher ACTIVE: {len(worlds)} worlds, "
                        f"recipe split {(rmeta or {}).get('split_at')}, "
                        f"{T.shape[0]:,} rows in {timing['teacher_s']}s")
        except Exception as e:                            # noqa: BLE001
            logger.warning(f"[attn] teacher failed ({e}) — neutral features")
            teacher_active = False
            T = None
    if T is None:
        T = np.zeros((len(X), N_TEACHER), dtype=np.float32)
        logger.warning("[attn] teacher INACTIVE (recipe missing or saw "
                       "holdout outcomes) — neutral teacher features, "
                       "distillation disabled")
    status = {"active": bool(teacher_active), "worlds": len(worlds) if worlds else 0,
              "recipe_split": (rmeta or {}).get("split_at"),
              "recipe_path": recipe_path}

    # ── 3. slices, inner val, scalers, targets ──────────────────────
    Xt0, yt0, gt0 = X[m_tr], y[m_tr].astype(np.int64), g[m_tr]
    Tt0 = T[m_tr]
    dtr = dts[m_tr]
    n_fit = len(Xt0)
    inner = cfg.inner_val_rows if not args.smoke else min(cfg.inner_val_rows, 3000)
    inner = int(max(500, min(inner, n_fit // 4)))
    Xfit, yfit, gfit, Tfit = (Xt0[:-inner], yt0[:-inner], gt0[:-inner], Tt0[:-inner])
    Xv, yv, gv, Tv = Xt0[-inner:], yt0[-inner:], gt0[-inner:], Tt0[-inner:]

    def _aux_full(name):
        """Full-length aux label array (None when absent/misaligned)."""
        arr = meta.get(name)
        if arr is None or len(arr) != len(X):
            return None
        return arr
    _btts_full, _o35_full = _aux_full("y_btts"), _aux_full("y_o35")
    aux_fit = {"g": gfit,
               "btts": _btts_full[m_tr][:-inner] if _btts_full is not None else None,
               "o35": _o35_full[m_tr][:-inner] if _o35_full is not None else None}

    # recency weights — exact formula from retrain.py
    half_life = float(cfg.recency_half_life)
    if half_life > 0:
        ref_date = dtr.max()
        age = np.clip(np.asarray((ref_date - dtr) / pd.Timedelta(days=1),
                                 dtype=np.float64), 0.0, None)
        w_tr = np.maximum(np.power(0.5, age / half_life), 0.02).astype(np.float32)
        wfit = w_tr[:-inner]
        eff = float((wfit.sum() ** 2)
                    / (np.power(wfit.astype(np.float64), 2).sum()))
        logger.info(f"[attn] recency weights: half-life {half_life:.0f}d, "
                    f"effective_n={eff:,.0f}")
    else:
        wfit = np.ones(len(Xfit), dtype=np.float32)
    timing["prep_s"] = round(time.time() - t_start, 1)

    x_mean, x_std = Xfit.mean(0), Xfit.std(0)
    x_std[x_std < 1e-6] = 1.0
    t_mean, t_std = Tfit.mean(0), Tfit.std(0)
    t_std[t_std < 1e-6] = 1.0

    def _scale(xs, ts):
        z = np.hstack([(xs - x_mean) / x_std,
                       (ts - t_mean) / t_std]).astype(np.float32)
        return z

    Zfit, Zv = _scale(Xfit, Tfit), _scale(Xv, Tv)
    assert Zfit.shape[1] == cfg.n_features + N_TEACHER

    # targets: hard 1X2 -> optional teacher soft mix -> label smoothing
    tgt = np.eye(3, dtype=np.float32)[yfit]
    lam = float(cfg.distill_weight) if teacher_active else 0.0
    distill_used = {"weight": lam, "rows": 0, "frac": cfg.distill_frac}
    if lam > 0:
        soft = np.clip(Tfit[:, :3].astype(np.float64), 1e-6, None)
        soft = soft / soft.sum(axis=1, keepdims=True)
        rng = np.random.default_rng(cfg.seed)
        sub = rng.random(len(tgt)) < cfg.distill_frac
        tgt[sub] = ((1.0 - lam) * tgt[sub]
                    + lam * soft[sub]).astype(np.float32)
        distill_used["rows"] = int(sub.sum())
    if cfg.label_smoothing > 0:
        tgt = ((1.0 - cfg.label_smoothing) * tgt
               + cfg.label_smoothing / 3.0).astype(np.float32)

    # ── 4. torch training (train side only) ─────────────────────────
    model, train_info = train_model(Zfit, tgt, wfit, aux_fit, Zv, yv, cfg)
    timing["train_s"] = round(time.time() - t_start - timing["prep_s"], 1)

    # ── 5. numpy export + parity (production path) ──────────────────
    from attn.model import NumpyTabTransformer  # noqa: F811 (lazy, torch-free)
    net = model.export_numpy()
    assert isinstance(net, NumpyTabTransformer)
    sample = Zv[: min(4096, len(Zv))]
    import torch
    with torch.no_grad():
        p_t = _softmax(model(torch.from_numpy(sample))["logits_1x2"].numpy())
    p_n = net.predict(sample.astype(np.float64))["p_1x2"]
    parity = float(np.max(np.abs(p_t - p_n)))
    logger.info(f"[attn] torch<->numpy parity max|diff| = {parity:.2e}")
    if parity > 1e-3:
        logger.error("[attn] parity check FAILED — not saving a serving "
                     "bundle whose predictions would differ from training")
        return 3

    # ── 6. holdout evaluation (numpy path, once) ────────────────────
    Xh, yh, gh, Th = X[m_ho], y[m_ho].astype(np.int64), g[m_ho], T[m_ho]
    Zh = _scale(Xh, Th)
    r_ho = net.predict(Zh.astype(np.float64))
    p_ho = r_ho["p_1x2"]
    mid = max(len(yh) // 2, 100)
    y_ev = yh[mid:]
    raw_cal, raw_ev = p_ho[:mid], p_ho[mid:]
    cals = _isotonic_fit(raw_cal, yh[:mid])
    p_cal = _isotonic_apply(raw_ev, cals)
    ll_raw, ll_cal = _ll(raw_ev, y_ev), _ll(p_cal, y_ev)
    use_cal = ll_cal < ll_raw
    p_ev = p_cal if use_cal else raw_ev
    ll_eval = min(ll_raw, ll_cal)
    acc = float((p_ev.argmax(1) == y_ev).mean())
    brier = float(((p_ev - np.eye(3)[y_ev]) ** 2).mean())

    def _brier_on(p_full, lab_full):
        """Eval-half Brier for a binary head over the full holdout slice."""
        if lab_full is None:
            return None
        return float(((p_full[mid:] - lab_full[mid:]) ** 2).mean())

    goals_brier = _brier_on(r_ho["p_o25"], gh)
    btts_brier = _brier_on(r_ho["p_btts"], _btts_full[m_ho]
                           if _btts_full is not None else None)
    o35_brier = _brier_on(r_ho["p_o35"], _o35_full[m_ho]
                          if _o35_full is not None else None)

    # teacher-only baseline on the eval half (its own posterior, no model)
    te = Th[mid:, :3].astype(np.float64)
    p_teacher = np.clip(te, 1e-6, None)
    p_teacher = p_teacher / p_teacher.sum(axis=1, keepdims=True)
    teacher_eval_ll = _ll(p_teacher, y_ev) if teacher_active else None

    # market baselines (same devig/alpha protocol as retrain.py)
    odds = meta.get("odds")
    odds_c = meta.get("odds_closing")
    odds_ev = odds[m_ho][mid:] if odds is not None and len(odds) == len(X) \
        else np.zeros((len(y_ev), 3))
    odds_cev = odds_c[m_ho][mid:] if odds_c is not None and len(odds_c) == len(X) \
        else odds_ev
    mkt, mkt_c = _devig(odds_ev), _devig(odds_cev)
    valid, valid_c = _valid_odds(odds_ev), _valid_odds(odds_cev)
    mkt_ll = _ll(mkt[valid], y_ev[valid]) if int(valid.sum()) > 50 else None
    mktc_ll = _ll(mkt_c[valid_c], y_ev[valid_c]) if int(valid_c.sum()) > 50 else None
    blend_ll = None
    alpha_default = None
    alpha_prior = float(os.environ.get("ALPHA_PRIOR", "0.35") or 0.35)
    alpha_prior_k = float(os.environ.get("ALPHA_PRIOR_K", "1000") or 1000)
    if int(valid_c.sum()) >= 50:
        best_a, best_ll_a = None, float("inf")
        for a in np.arange(0.0, 1.001, 0.05):
            ll_a = _ll(a * p_ev[valid_c] + (1.0 - a) * mkt_c[valid_c],
                       y_ev[valid_c])
            if ll_a < best_ll_a:
                best_ll_a, best_a = ll_a, float(a)
        alpha_default = damp_alpha(best_a, int(valid_c.sum()),
                                   alpha_prior, alpha_prior_k)
        blend_ll = _ll(alpha_default * p_ev[valid_c]
                       + (1.0 - alpha_default) * mkt_c[valid_c], y_ev[valid_c])

    # ── 7. in-run LightGBM reference (same slices, honest compare) ──
    lgbm_ref = None
    if not args.skip_lgbm:
        if half_life > 0:
            ref_date = dtr.max()
            age = np.clip(np.asarray((ref_date - dtr) / pd.Timedelta(days=1),
                                     dtype=np.float64), 0.0, None)
            w_full = np.maximum(np.power(0.5, age / half_life), 0.02) \
                .astype(np.float32)
        else:
            w_full = np.ones(n_tr, dtype=np.float32)
        aux_full = {"y_btts": _btts_full[m_tr] if _btts_full is not None else None,
                    "y_o35": _o35_full[m_tr] if _o35_full is not None else None}
        lgbm_ref = _lgbm_reference(
            X[m_tr], yt0, gt0, w_full, aux_full, Xh, yh, gh,
            _btts_full[m_ho] if _btts_full is not None else None,
            _o35_full[m_ho] if _o35_full is not None else None,
            mid)

    # ── 8. Pico reference + verdict ─────────────────────────────────
    pico = {}
    try:
        info_path = os.path.join(AI_ROOT, "data", "model_info.json")
        with open(info_path) as fh:
            prev = json.load(fh)
        pico = prev.get("calibration") or {}
        pico["version"] = prev.get("version")
    except Exception as e:                                # noqa: BLE001
        logger.warning(f"[attn] Pico reference unavailable: {e}")
    pico_ll = pico.get("eval_log_loss")
    verdict = None
    if isinstance(pico_ll, (int, float)):
        verdict = "win" if ll_eval < pico_ll else "hold"
        if abs(ll_eval - pico_ll) <= 0.0005:
            verdict = "tie"

    timing["total_s"] = round(time.time() - t_start, 1)
    metrics = {
        "holdout_days": holdout_days,
        "split_at": str(split_at.date()),
        "holdout_matches": int(len(y_ev)),
        "train_rows": int(n_tr),
        "train_used": int(len(Xfit)),
        "inner_val_rows": int(inner),
        "raw_log_loss": round(ll_raw, 5),
        "isotonic_log_loss": round(ll_cal, 5),
        "calibration_used": bool(use_cal),
        "eval_log_loss": round(ll_eval, 5),
        "accuracy": round(acc, 4),
        "brier": round(brier, 5),
        "ece": round(_ece(p_ev, y_ev), 4),
        "goals_brier": round(goals_brier, 5) if goals_brier is not None else None,
        "btts_brier": round(btts_brier, 5) if btts_brier is not None else None,
        "o35_brier": round(o35_brier, 5) if o35_brier is not None else None,
        "teacher_eval_log_loss": (round(teacher_eval_ll, 5)
                                  if teacher_eval_ll is not None else None),
        "market_log_loss": round(mkt_ll, 5) if mkt_ll is not None else None,
        "market_closing_log_loss": round(mktc_ll, 5) if mktc_ll is not None else None,
        "blended_market_log_loss": round(blend_ll, 5) if blend_ll is not None else None,
        "alpha_default": alpha_default,
        "market_rows": int(valid_c.sum()),
    }
    report = {
        "protocol": {
            "split": "out-of-time (same as ai/retrain.py)",
            "holdout_eval": "once; isotonic on first half, scored on second",
            "selection": "train-side inner tail only (early stopping)",
            "torch_numpy_parity_max_abs": parity,
            "teacher": status,
            "distillation": distill_used,
        },
        "config": cfg.to_dict(),
        "timing_s": timing,
        "train": train_info,
        "eval": metrics,
        "lgbm_reference": lgbm_ref,
        "pico_deployed": {k: pico.get(k) for k in
                          ("version", "split_at", "eval_log_loss", "raw_log_loss",
                           "accuracy", "brier", "goals_brier",
                           "market_log_loss", "market_closing_log_loss",
                           "blended_market_log_loss")},
        "verdict_vs_pico": verdict,
        "git_sha": os.environ.get("GITHUB_SHA", ""),
        "smoke": bool(args.smoke),
    }

    # ── 9. save bundle + report ─────────────────────────────────────
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(os.path.dirname(rep_path), exist_ok=True)
    import joblib
    payload = {
        "cfg": cfg.to_dict(),
        "params": net.p,
        "n_heads": cfg.n_heads,
        "x_mean": x_mean, "x_std": x_std,
        "t_mean": t_mean, "t_std": t_std,
        "recipe": recipe_raw if teacher_active else None,
        "recipe_meta": rmeta or {},
        "git_sha": report["git_sha"],
    }
    joblib.dump(payload, out_path)
    with open(rep_path, "w") as fh:
        json.dump(report, fh, indent=1)
    logger.info(f"[attn] saved {out_path} "
                f"({os.path.getsize(out_path) / 1e6:.1f} MB) + {rep_path}")
    logger.info(f"[attn] EVAL: ll={ll_eval:.5f} (raw={ll_raw:.5f}) "
                f"acc={acc:.4f} brier={brier:.5f} | Pico={pico_ll} "
                f"-> {verdict} | market={mkt_ll} closing={mktc_ll} "
                f"blend={blend_ll}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

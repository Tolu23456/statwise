"""The honest gate: every number the lab produces is scored on REAL outcomes.

Hard rule (written into the plan): synthetic data may never be scored against
synthetic holdout.  A model evaluated on its own draws is agreeing with
itself, which is exactly the trap a "simulate trillions of matches" plan
falls into by default.

So: `evaluate()` scores the world-ensemble on the real out-of-time holdout
(the same 180-day split retrain.py uses), against three baselines — uniform,
the league prior, and the de-vigged market when odds exist.  A world that
cannot beat the league prior on real outcomes does not get published.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import StrengthModel
from .simulate import posterior_markets

EPS = 1e-9


def league_priors(train: pd.DataFrame) -> dict[str, dict]:
    """Per-league outcome/goal base rates, computed on TRAIN rows only."""
    out: dict[str, dict] = {}
    if train is None or not len(train):
        return out
    df = train.copy()
    lg = (df["league_slug"] if "league_slug" in df.columns
          else df.get("league", "all")).astype(str)
    hg = df["home_goals"].astype(float)
    ag = df["away_goals"].astype(float)
    for name, idx in lg.groupby(lg).groups.items():
        h, a = hg.loc[idx], ag.loc[idx]
        hw = (h > a).mean()
        dr = (h == a).mean()
        aw = (h < a).mean()
        out[str(name)] = {
            "home": float(hw), "draw": float(dr), "away": float(aw),
            "btts": float(((h > 0) & (a > 0)).mean()),
            "over25": float(((h + a) > 2.5).mean()),
            "over35": float(((h + a) > 3.5).mean()),
            "n": int(len(idx)),
        }
    # pooled fallback for a league the train split does not carry
    out.setdefault("all", {
        "home": float((hg > ag).mean()), "draw": float((hg == ag).mean()),
        "away": float((hg < ag).mean()),
        "btts": float(((hg > 0) & (ag > 0)).mean()),
        "over25": float(((hg + ag) > 2.5).mean()),
        "over35": float(((hg + ag) > 3.5).mean()),
        "n": int(len(train)),
    })
    return out


def _log_loss(p: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(np.clip(p, EPS, 1.0)).mean())


def _brier(p: np.ndarray, y: np.ndarray) -> float:
    """Binary Brier for a (n,) probability and (n,) {0,1} labels."""
    return float(((p - y) ** 2).mean())


def _ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total, ece = len(p), 0.0
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        m = (p >= lo) & (p < hi if b < bins - 1 else p <= hi)
        if not m.any():
            continue
        ece += (m.sum() / total) * abs(float(p[m].mean()) - float(y[m].mean()))
    return round(ece, 4)


def _multiclass_ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    conf = p.max(axis=1)
    correct = (p.argmax(axis=1) == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    total, ece = len(p), 0.0
    for b in range(bins):
        lo, hi = edges[b], edges[b + 1]
        m = (conf >= lo) & (conf < hi if b < bins - 1 else conf <= hi)
        if not m.any():
            continue
        ece += (m.sum() / total) * abs(float(conf[m].mean()) - float(correct[m].mean()))
    return round(ece, 4)


def evaluate(models: list[StrengthModel], holdout: pd.DataFrame,
             priors: dict[str, dict], *, seed: int = 0) -> dict:
    """Score the ensemble on real held-out outcomes.  Returns a metrics dict."""
    if holdout is None or not len(holdout):
        return {"error": "empty holdout"}

    # score only rows whose teams the worlds were actually fit on
    h = holdout["home_team"].astype(str).map(
        models[0].t2i).to_numpy(dtype=float)
    a = holdout["away_team"].astype(str).map(
        models[0].t2i).to_numpy(dtype=float)
    ok = ~(np.isnan(h) | np.isnan(a))
    df = holdout.loc[ok].reset_index(drop=True)
    h = h[ok].astype(np.int64)
    a = a[ok].astype(np.int64)
    lg_series = (df["league_slug"] if "league_slug" in df.columns
                 else df.get("league", "all")).astype(str)
    lg = lg_series.map(models[0].l2i).fillna(models[0].l2i.get(
        "all", 0)).to_numpy().astype(np.int64)
    if not len(df):
        return {"error": "no holdout rows whose teams the worlds know"}

    y_h = df["home_goals"].astype(float).to_numpy()
    y_a = df["away_goals"].astype(float).to_numpy()
    y1x2 = np.where(y_h > y_a, 0, np.where(y_h == y_a, 1, 2)).astype(int)
    y_btts = ((y_h > 0) & (y_a > 0)).astype(int)
    y_o25 = ((y_h + y_a) > 2.5).astype(int)
    y_o35 = ((y_h + y_a) > 3.5).astype(int)

    pm = posterior_markets(models, h, a, lg)
    p1 = np.column_stack([pm["home"], pm["draw"], pm["away"]])
    p1 = p1 / p1.sum(axis=1, keepdims=True)

    # ── world-ensemble on real outcomes ──────────────────────────────────
    res = {
        "n": int(len(df)),
        "worlds": len(models),
        "home_1x2": {
            "log_loss": round(_log_loss(p1[np.arange(len(df)), y1x2], y1x2), 5),
            "brier": round(float(((p1 - np.eye(3)[y1x2]) ** 2).mean()), 5),
            "accuracy": round(float((p1.argmax(1) == y1x2).mean()), 4),
            "ece": _multiclass_ece(p1, y1x2),
        },
        "btts": {
            "log_loss": round(_log_loss(pm["btts"], y_btts), 5),
            "brier": round(_brier(pm["btts"], y_btts), 5),
            "ece": _ece(pm["btts"], y_btts),
        },
        "over25": {
            "log_loss": round(_log_loss(pm["over25"], y_o25), 5),
            "brier": round(_brier(pm["over25"], y_o25), 5),
            "ece": _ece(pm["over25"], y_o25),
        },
        "over35": {
            "log_loss": round(_log_loss(pm["over35"], y_o35), 5),
            "brier": round(_brier(pm["over35"], y_o35), 5),
            "ece": _ece(pm["over35"], y_o35),
        },
        "uncertainty": {
            "mean_home_std": round(float(pm["home_std"].mean()), 4),
            "mean_btts_std": round(float(pm["btts_std"].mean()), 4),
        },
    }

    # ── baselines ────────────────────────────────────────────────────────
    uniform1 = np.full((len(df), 3), 1 / 3)
    base_rate_btts = float(y_btts.mean())
    base_rate_o25 = float(y_o25.mean())
    base_rate_o35 = float(y_o35.mean())
    res["baselines"] = {
        "uniform": {
            "home_1x2_log_loss": round(_log_loss(uniform1[np.arange(len(df)), y1x2], y1x2), 5),
            "btts_log_loss": round(_log_loss(np.full(len(df), base_rate_btts + EPS), y_btts), 5),
            "over25_log_loss": round(_log_loss(np.full(len(df), base_rate_o25 + EPS), y_o25), 5),
            "over35_log_loss": round(_log_loss(np.full(len(df), base_rate_o35 + EPS), y_o35), 5),
        }
    }

    # league prior (train-only rates) — the "informed but dumb" bar
    def _prior_vec(key: str, default: float = 1 / 3) -> np.ndarray:
        return np.array([float(priors.get(l_, priors.get("all", {})).get(key, default))
                         for l_ in lg_series])

    lp = np.column_stack([_prior_vec("home"), _prior_vec("draw"), _prior_vec("away")])
    lp = lp / lp.sum(axis=1, keepdims=True)
    lp_btts = _prior_vec("btts", 0.45)
    lp_o25 = _prior_vec("over25", 0.50)
    lp_o35 = _prior_vec("over35", 0.27)
    res["baselines"]["league_prior"] = {
        "home_1x2_log_loss": round(_log_loss(lp[np.arange(len(df)), y1x2], y1x2), 5),
        "btts_log_loss": round(_log_loss(lp_btts, y_btts), 5),
        "over25_log_loss": round(_log_loss(lp_o25, y_o25), 5),
        "over35_log_loss": round(_log_loss(lp_o35, y_o35), 5),
    }

    # market (de-vigged closing/opening odds) when the row has them
    if {"odds_home", "odds_draw", "odds_away"}.issubset(df.columns):
        oh = pd.to_numeric(df["odds_home"], errors="coerce").to_numpy()
        od = pd.to_numeric(df["odds_draw"], errors="coerce").to_numpy()
        oa = pd.to_numeric(df["odds_away"], errors="coerce").to_numpy()
        good = np.isfinite(oh) & np.isfinite(od) & np.isfinite(oa) & (oh > 1) & (od > 1) & (oa > 1)
        if good.sum() > 50:
            inv = np.column_stack([1 / oh[good], 1 / od[good], 1 / oa[good]])
            mv = inv / inv.sum(axis=1, keepdims=True)
            res["baselines"]["market"] = {
                "n": int(good.sum()),
                "home_1x2_log_loss": round(
                    _log_loss(mv[np.arange(good.sum()), y1x2[good]], y1x2[good]), 5),
            }

    # ── verdicts ─────────────────────────────────────────────────────────
    res["beats"] = {
        "league_prior_1x2": bool(res["home_1x2"]["log_loss"]
                                 < res["baselines"]["league_prior"]["home_1x2_log_loss"]),
        "league_prior_btts": bool(res["btts"]["log_loss"]
                                  < res["baselines"]["league_prior"]["btts_log_loss"]),
        "league_prior_over25": bool(res["over25"]["log_loss"]
                                    < res["baselines"]["league_prior"]["over25_log_loss"]),
        "league_prior_over35": bool(res["over35"]["log_loss"]
                                    < res["baselines"]["league_prior"]["over35_log_loss"]),
        "market_1x2": bool(res["baselines"].get("market")
                           and res["home_1x2"]["log_loss"]
                           < res["baselines"]["market"]["home_1x2_log_loss"]),
    }
    res["publishable"] = bool(
        res["beats"]["league_prior_btts"] and res["beats"]["league_prior_over25"]
        and res["beats"]["league_prior_over35"])
    return res


def synthesize_on_history(models: list[StrengthModel], frame: pd.DataFrame, *,
                          batch: int = 20000) -> np.ndarray:
    """Teacher probabilities for REAL rows — the distillation soft labels.

    One row of output per row of `frame`, shaped (n, 3) for H/D/A plus the
    goal markets appended by the caller.  Computed exactly (grid average over
    worlds), never by Monte-Carlo sampling, so the labels carry no sampling
    noise.
    """
    if not len(frame):
        return np.zeros((0, 3))
    h = frame["home_team"].astype(str).map(models[0].t2i).to_numpy(dtype=float)
    a = frame["away_team"].astype(str).map(models[0].t2i).to_numpy(dtype=float)
    lg = ((frame["league_slug"] if "league_slug" in frame.columns else frame.get("league"))
          .astype(str).map(models[0].l2i).fillna(0).to_numpy(dtype=float))
    out = np.zeros((len(frame), 3))
    known = ~(np.isnan(h) | np.isnan(a))
    for start in range(0, len(frame), batch):
        sl = slice(start, min(start + batch, len(frame)))
        m = known[sl]
        if not m.any():
            continue
        pm = posterior_markets(models, h[sl][m].astype(np.int64),
                               a[sl][m].astype(np.int64),
                               lg[sl][m].astype(np.int64))
        p = np.column_stack([pm["home"], pm["draw"], pm["away"]])
        out[sl][m] = p / p.sum(axis=1, keepdims=True)
    return out

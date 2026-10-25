#!/usr/bin/env python3
"""Audit run 2 — production feature path (empty history), calibrated properly.

Differences from serve_path_reliability.py:
  * sample = EXACTLY retrain.py's eval half (second half, by row count, of the
    180-day out-of-time holdout), with the same quality/international filters;
  * two clock conditions:
        "matchdate"  cts = the match's own date  (what retrain.py's holdout
                     evaluation uses, and what a live fixture approximates:
                     production passes pd.Timestamp.now() ≈ kickoff ± 14d)
        "now"        cts = pd.Timestamp.now()    (literal production code path;
                     for matches 70 days in the past this inflates rest days)
  * metrics restricted to odds-priced rows (where the market blend changes
    anything) as well as overall;
  * probability matrices are saved to analysis/serve_probs.npz so further
    analysis never needs another model run.

Usage: cd <repo>/ai && ../.venv/bin/python -u ../analysis/serve_path_reliability_v2.py
"""
from __future__ import annotations

import gc
import json
import os
import sys
import time

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI = os.path.join(BASE, "ai")
sys.path.insert(0, AI)

USECOLS = ["date", "home_team", "away_team", "home_goals", "away_goals", "league_slug",
           "odds_home", "odds_draw", "odds_away",
           "avg_odds_home", "avg_odds_draw", "avg_odds_away",
           "quality_score", "is_international"]


def logloss(p, y):
    return float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1.0)).mean())


def ece_table(p, y, bins=10):
    conf, pred = p.max(axis=1), p.argmax(axis=1)
    correct = (pred == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    rows, ece = [], 0.0
    for b in range(bins):
        m = (conf >= edges[b]) & (conf < edges[b + 1] if b < bins - 1 else conf <= edges[b + 1])
        if not m.any():
            continue
        acc, cf = float(correct[m].mean()), float(conf[m].mean())
        ece += float(m.mean()) * abs(acc - cf)
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()),
                     "mean_p": round(cf, 4), "emp_acc": round(acc, 4),
                     "gap": round(acc - cf, 4)})
    return round(ece, 4), rows


def devig(o):
    imp = 1.0 / np.maximum(o, 1.0001)
    return imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9)


def metrics(P, y, mask=None, tag=""):
    if mask is not None:
        P, y = P[mask], y[mask]
    ece, tbl = ece_table(P, y)
    return {"tag": tag, "n": int(len(y)),
            "accuracy": round(float((P.argmax(1) == y).mean()), 4),
            "log_loss": round(logloss(P, y), 5),
            "ece": ece,
            "mean_conf": round(float(P.max(1).mean()), 4),
            "brier": round(float(((P - np.eye(3)[y]) ** 2).mean()), 5),
            "pred_home": round(float((P.argmax(1) == 0).mean()), 3),
            "pred_draw": round(float((P.argmax(1) == 1).mean()), 3),
            "pred_away": round(float((P.argmax(1) == 2).mean()), 3),
            "mean_p_true": round(float(np.exp(-logloss(P, y))), 4),
            "reliability": tbl}


def main():
    import joblib

    t0 = time.time()
    print("loading pickle…", flush=True)
    d = joblib.load(os.path.join(AI, "models", "football_predictor.pkl"))
    fp, outcome = d["feature_pipe"], d["outcome_pipe"]
    alphas = d.get("alphas") or {}
    cinfo = d.get("calibration_info") or {}
    print(f"artifact: mode={cinfo.get('mode')} eval_ll={cinfo.get('eval_log_loss')} "
          f"acc={cinfo.get('accuracy')} alpha={cinfo.get('alpha_default')} "
          f"calibrators={d.get('calibrators')} trained={cinfo.get('split_at')}", flush=True)

    # ── data + retrain's exact filters and split ────────────────────
    frames = [pd.read_csv(os.path.join(AI, "data", "clean", f"{y}_matches.csv"),
                          usecols=USECOLS, low_memory=False) for y in (2025, 2026)]
    raw = pd.concat(frames, ignore_index=True)
    for c in USECOLS[4:]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["home_team", "away_team", "home_goals", "away_goals", "date"])
    raw = raw[raw["is_international"].fillna(0) == 0]
    raw = raw[raw["quality_score"].fillna(0) >= 40]
    raw = raw.sort_values("date", kind="stable").reset_index(drop=True)

    split_at = raw["date"].max() - pd.Timedelta(days=180)
    ho = raw[raw["date"] > split_at].reset_index(drop=True)
    n_ev = len(ho) // 2
    ev = ho.iloc[len(ho) - n_ev:].reset_index(drop=True)   # later half = eval
    ev_start = ev["date"].min()
    print(f"retrain eval half: {len(ev):,} rows {ev_start.date()}..{ev['date'].max().date()} "
          f"(split_at {split_at.date()})", flush=True)

    hist = raw[raw["date"] < ev_start].copy()              # no lookahead
    empty_hist = pd.DataFrame(columns=["date", "home_team", "away_team",
                                       "home_goals", "away_goals"])
    y = np.where(ev.home_goals.values > ev.away_goals.values, 0,
                 np.where(ev.home_goals.values == ev.away_goals.values, 1, 2)).astype(int)
    print(f"label rates: home {float((y == 0).mean()):.3f} draw {float((y == 1).mean()):.3f} "
          f"away {float((y == 2).mean()):.3f}", flush=True)

    odds_fill = ev[["odds_home", "odds_draw", "odds_away"]].fillna(0).values
    mblock = [{"home": r.home_team, "away": r.away_team,
               "league": r.league_slug if isinstance(r.league_slug, str) else "all",
               "date": r.date,
               "odds_home": float(odds_fill[i, 0]), "odds_draw": float(odds_fill[i, 1]),
               "odds_away": float(odds_fill[i, 2])}
              for i, r in enumerate(ev.itertuples())]
    del raw, ho, frames
    gc.collect()

    # clock patch: features.py calls pd.Timestamp.now() once per row
    queue: list = []
    ctr = {"now_calls": 0}
    _orig_ts = pd.Timestamp

    class _TS(pd.Timestamp):
        @classmethod
        def now(cls, tz=None):
            if queue:
                ctr["now_calls"] += 1
                return cls(queue.pop(0))
            return super().now(tz=tz)

    def build(tag, clock):
        t = time.time()
        queue.clear()
        if clock == "matchdate":
            queue.extend(list(pd.to_datetime([m["date"] for m in mblock])))
        n_before = ctr["now_calls"]
        pd.Timestamp = _TS
        try:
            X = fp.build_features(mblock, empty_hist)
        finally:
            pd.Timestamp = _orig_ts
        X = np.nan_to_num(np.asarray(X, dtype=np.float64), nan=0.0, posinf=1e6, neginf=-1e6)
        P = np.asarray(outcome.predict_proba(X))
        print(f"  [{tag}] {time.time() - t:.0f}s  shape {X.shape}  "
              f"clock substitutions {ctr['now_calls'] - n_before}/{len(mblock)}", flush=True)
        del X
        gc.collect()
        return P

    print("building EMPTY-history features, clock = match date…", flush=True)
    P_md = build("matchdate", "matchdate")
    print("building EMPTY-history features, clock = now() (literal production)…", flush=True)
    P_now = build("now", "now")

    # market side
    o_c = ev[["avg_odds_home", "avg_odds_draw", "avg_odds_away"]].fillna(0).values
    o_b = ev[["odds_home", "odds_draw", "odds_away"]].fillna(0).values
    odds = np.where(o_c > 1.01, o_c, o_b)
    valid = (odds[:, 0] > 1.01) & (odds[:, 1] > 1.01) & (odds[:, 2] > 1.01)
    mkt = np.full((len(y), 3), 1 / 3.0)
    mkt[valid] = devig(odds[valid])
    alpha_def = float(alphas.get("_default", 1.0))

    def blend(P, a):
        Q = np.clip(a * P + (1.0 - a) * mkt, 1e-12, None)
        return Q / Q.sum(axis=1, keepdims=True)

    out = {"n": int(len(y)), "eval_start": str(ev_start.date()),
           "eval_end": str(ev["date"].max().date()), "alpha_default": alpha_def,
           "label_rates": {"home": round(float((y == 0).mean()), 4),
                           "draw": round(float((y == 1).mean()), 4),
                           "away": round(float((y == 2).mean()), 4)},
           "always_home_acc": round(float((y == 0).mean()), 4),
           "odds_coverage": round(float(valid.mean()), 4)}

    print("\n### production path, clock = match date (live-serving proxy)", flush=True)
    out["serve_matchdate_raw"] = metrics(P_md, y, tag="serve_matchdate_raw")
    print(json.dumps({k: v for k, v in out["serve_matchdate_raw"].items() if k != "reliability"}))
    out["serve_matchdate_blend"] = metrics(blend(P_md, alpha_def), y,
                                            tag=f"serve_matchdate_blend(a={alpha_def})")
    print(json.dumps({k: v for k, v in out["serve_matchdate_blend"].items() if k != "reliability"}))

    print("\n### production path, clock = now() (literal code path)", flush=True)
    out["serve_now_raw"] = metrics(P_now, y, tag="serve_now_raw")
    print(json.dumps({k: v for k, v in out["serve_now_raw"].items() if k != "reliability"}))
    out["serve_now_blend"] = metrics(blend(P_now, alpha_def), y,
                                      tag=f"serve_now_blend(a={alpha_def})")
    print(json.dumps({k: v for k, v in out["serve_now_blend"].items() if k != "reliability"}))

    print("\n### market + baselines", flush=True)
    out["market_all"] = metrics(mkt, y, tag="market_uniform_unpriced")
    print(json.dumps({k: v for k, v in out["market_all"].items() if k != "reliability"}))
    out["market_priced"] = metrics(mkt, y, valid, tag="market_priced")
    print(json.dumps({k: v for k, v in out["market_priced"].items() if k != "reliability"}))
    out["model_priced_raw"] = metrics(P_md, y, valid, tag="model_priced_raw")
    print(json.dumps({k: v for k, v in out["model_priced_raw"].items() if k != "reliability"}))
    for a in (0.0, 0.2, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0):
        out[f"blend_priced_{a}"] = metrics(blend(P_md, a), y, valid, tag=f"blend_priced_a={a}")
    print("alpha sweep (priced rows, match-date clock):")
    print(json.dumps({k: {"acc": v["accuracy"], "ll": v["log_loss"]}
                      for k, v in out.items() if k.startswith("blend_priced_")}, indent=1))
    out["alpha_sweep_all"] = {str(a): {"acc": metrics(blend(P_md, a), y)["accuracy"],
                                       "ll": metrics(blend(P_md, a), y)["log_loss"]}
                              for a in (0.0, 0.35, 0.4, 0.6, 0.8, 1.0)}

    # ── draw diagnostics: is the draw class under-rated? ────────────
    diag = {}
    for tag, P in (("serve_matchdate", P_md), ("serve_now", P_now)):
        order = np.argsort(P, axis=1)[:, ::-1]
        rank_draw = np.argmax(order == 1, axis=1)          # 0/1/2
        sub = {}
        for r in (0, 1, 2):
            m = rank_draw == r
            if m.sum() == 0:
                continue
            sub[f"draw_rank_{r}"] = {"n": int(m.sum()),
                                     "draw_rate": round(float((y[m] == 1).mean()), 4),
                                     "mean_p_draw": round(float(P[m, 1].mean()), 4)}
        # among rows where draw is 2nd, does promoting it within delta help?
        promo = {}
        for delta in (0.0, 0.02, 0.05, 0.08):
            Q = P.copy()
            flip = 0
            for i in range(len(Q)):
                a1, a2 = order[i, 0], order[i, 1]
                if Q[i, a2] >= Q[i, a1] - delta:
                    Q[i] = 0
                    Q[i, a2] = 1
                    flip += 1
            promo[str(delta)] = {"acc": round(float((Q.argmax(1) == y).mean()), 4),
                                 "flips": flip}
        diag[tag] = {"by_draw_rank": sub, "draw_promotion": promo}
    out["draw_diagnostics"] = diag
    print("\ndraw diagnostics:", json.dumps(diag, indent=1))

    np.savez_compressed(os.path.join(BASE, "analysis", "serve_probs.npz"),
                        p_matchdate=P_md, p_now=P_now, y=y, valid=valid,
                        odds=odds, mkt=mkt,
                        dates=ev["date"].astype(str).values,
                        leagues=ev["league_slug"].astype(str).values,
                        home=ev["home_team"].values, away=ev["away_team"].values,
                        hg=ev["home_goals"].values, ag=ev["away_goals"].values)

    path = os.path.join(BASE, "analysis", "serve_path_reliability_v2.json")
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"\nwrote {path} + analysis/serve_probs.npz  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()

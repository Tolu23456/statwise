#!/usr/bin/env python3
"""Reliability / accuracy audit of what production ACTUALLY served.

Reads the settled predictions pulled from Supabase (status=completed,
actual_result filled) — the same rows the user's "47/97" screen is built
from — and scores the probabilities that were published with them
(stats.prob_home / prob_draw / prob_away, i.e. post-calibration,
post-market-blend, empty-history feature path).

Usage: .venv/bin/python analysis/live_predictions_reliability.py [preds.json]
Writes analysis/live_reliability.json
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/preds_all.json"
LABEL = {"Home Win": 0, "Draw": 1, "Away Win": 2}


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


def main():
    rows = json.load(open(SRC))
    rows = [r for r in rows if r.get("actual_result")]
    print(f"settled rows: {len(rows)}")

    # published pick over EVERY settled row (this is the user's screen: 47/97)
    pub_ok = sum(1 for r in rows if r.get("prediction") == r.get("actual_result"))
    pub = {"n": len(rows), "correct": pub_ok,
           "accuracy": round(pub_ok / max(len(rows), 1), 4),
           "always_home_accuracy": round(
               sum(1 for r in rows if r.get("actual_result") == "Home Win") / max(len(rows), 1), 4),
           "label_rates": {k: round(sum(1 for r in rows if r.get("actual_result") == k)
                                    / max(len(rows), 1), 4)
                           for k in ("Home Win", "Draw", "Away Win")}}

    # market odds stored on the published row (only some fixtures carry them)
    mv, mok, mhit = 0, 0, 0
    for r in rows:
        s = r.get("stats") or {}
        try:
            o = [float(s.get("odds_home") or 0), float(s.get("odds_draw") or 0),
                 float(s.get("odds_away") or 0)]
        except (TypeError, ValueError):
            continue
        if min(o) > 1.01:
            mv += 1
            imp = np.array([1 / x for x in o])
            imp /= imp.sum()
            if ("Home Win", "Draw", "Away Win")[int(np.argmax(imp))] == r.get("actual_result"):
                mok += 1
            if ("Home Win", "Draw", "Away Win")[int(np.argmax(imp))] == r.get("prediction"):
                mhit += 1
    pub["market_rows"] = mv
    pub["market_favourite_accuracy"] = round(mok / mv, 4) if mv else None
    pub["model_equals_market_favourite"] = round(mhit / mv, 4) if mv else None
    print("published-pick baseline:", json.dumps(pub))

    y, P, conf, picked, created = [], [], [], [], []
    for r in rows:
        s = r.get("stats") or {}
        ph, pd_, pa = s.get("prob_home"), s.get("prob_draw"), s.get("prob_away")
        if ph is None or pd_ is None or pa is None:
            continue
        p = np.array([float(ph), float(pd_), float(pa)], dtype=float)
        p = np.clip(p, 1e-6, None)
        p = p / p.sum()
        y.append(LABEL.get(r.get("actual_result"), -1))
        P.append(p)
        conf.append(float(r.get("confidence") or s.get("certainty") or 0) / 100.0)
        picked.append(LABEL.get(r.get("prediction"), -1))
        created.append(str(r.get("created_at") or "")[:10])
    keep = [i for i, v in enumerate(y) if v >= 0]
    y = np.array(y)[keep]
    P = np.array(P)[keep]
    conf = np.array(conf)[keep]
    picked = np.array(picked)[keep]
    n = len(y)

    acc = float((P.argmax(1) == y).mean())
    ll = float(-np.log(np.clip(P[np.arange(n), y], 1e-12, 1)).mean())
    brier = float(((P - np.eye(3)[y]) ** 2).mean())
    ece, tbl = ece_table(P, y)
    mean_conf = float(P.max(axis=1).mean())

    # published `confidence` field vs empirical accuracy of those picks
    conf_acc = {}
    for c in sorted({round(c, 1) for c in conf}):
        m = np.isclose(np.round(conf, 1), c)
        if m.sum():
            conf_acc[str(c)] = {"n": int(m.sum()),
                                "acc": round(float((P.argmax(1)[m] == y[m]).mean()), 3)}

    pred_counts = Counter(int(v) for v in P.argmax(1))
    label_counts = Counter(int(v) for v in y)
    names = ["Home Win", "Draw", "Away Win"]

    # confusion: pick -> actual
    confm = {}
    for pi in range(3):
        m = P.argmax(1) == pi
        confm[names[pi]] = {names[ai]: int(((y[m] == ai)).sum()) for ai in range(3)}
        confm[names[pi]]["n"] = int(m.sum())
        confm[names[pi]]["hit_rate"] = round(float((y[m] == pi).mean()), 3) if m.sum() else None

    out = {
        "n_settled": n,
        "published_all_rows": pub,        "accuracy": round(acc, 4),
        "correct": int((P.argmax(1) == y).sum()),
        "log_loss": round(ll, 5),
        "brier": round(brier, 5),
        "ece": ece,
        "mean_max_prob": round(mean_conf, 4),
        "geometric_mean_p_true": round(float(np.exp(-ll)), 4),
        "label_rates": {names[i]: round(float((y == i).mean()), 4) for i in range(3)},
        "always_home_accuracy": round(float((y == 0).mean()), 4),
        "pick_distribution": {names[i]: {"n": int(pred_counts.get(i, 0)),
                                         "pct": round(pred_counts.get(i, 0) / n, 3)}
                              for i in range(3)},
        "hit_rate_by_pick": confm,
        "confidence_field_accuracy": conf_acc,
        "reliability": tbl,
        "created_at_days": dict(Counter(created)),
        "picked_equals_argmax": round(float((picked == P.argmax(1)).mean()), 4),
    }

    # top-1 accuracy if the 2nd-ranked class were promoted within delta
    order = np.argsort(P, axis=1)[:, ::-1]
    promo = {}
    for delta in (0.0, 0.02, 0.05, 0.08, 0.12):
        pred = order[:, 0].copy()
        flip = 0
        for i in range(n):
            if P[i, order[i, 1]] >= P[i, order[i, 0]] - delta:
                pred[i] = order[i, 1]
                flip += 1
        promo[str(delta)] = {"acc": round(float((pred == y).mean()), 4), "flips": flip}
    out["second_place_promotion"] = promo

    # pick the class with the highest probability EXCLUDING nothing ->
    # sanity: argmax vs published prediction (rounding of confidence)
    print(json.dumps({k: v for k, v in out.items()
                      if k not in ("reliability",)}, indent=1))

    path = os.path.join(BASE, "analysis", "live_reliability.json")
    json.dump(out, open(path, "w"), indent=1)
    print("wrote", path)


if __name__ == "__main__":
    main()

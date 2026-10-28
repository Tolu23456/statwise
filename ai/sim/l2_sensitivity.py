"""L2 shrinkage sensitivity — Stage 6 experiment (train-internal only).

Realism v1 (ai/sim/reports/realism_v1.md) left exactly ONE systematic
defect: the favourite-gap terciles fail R4 at |z| 7-9 in the SAME pattern
both directions — in the low tercile the sim is too optimistic about home
wins (obs 0.351 vs pred 0.419) and too pessimistic about away wins, and in
the high tercile exactly the reverse.  Both directions of the same sign
error mean the simulated outcomes are systematically LESS EXTREME than the
real ones: att/def spread is over-shrunk toward zero, so every matchup's
predicted margin regresses toward the mean.  The L2 penalty (0.02, chosen
when the fit was flat-history on a different population) is the prime
suspect: under recency decay the effective sample per team shrinks while
the penalty keeps the same absolute weight, compressing the strengths.

Protocol (leak-proof by construction):
  * validation slice = the 180 days BEFORE the earliest realism eval
    window start (W3 begins 2024-10-28), so no eval-window row ever
    influences the L2 choice;
  * train = every row before the slice, fitted with the production 4-year
    half-life — identical to how each realism window fits its model;
  * every candidate is scored with realism.evaluate_window's OWN
    statistics (favourite-gap terciles, W1 shape distance, 1X2 LL,
    moments z), so the comparison is exactly the suite's language;
  * the current default 0.02 is always in the grid as the control.

The default is only recommended to change when the winner is consistent
across the overall LL AND the favourite-gap bias.  Otherwise this script
prints NO recommendation — an inconclusive sensitivity is a valid result.

Usage (local, ~1 min):

    python ai/sim/l2_sensitivity.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sim.model import fit_strengths                    # noqa: E402
from sim.realism import evaluate_window, load_frame    # noqa: E402

# Earliest realism eval window start (W3 in make_windows, 4 windows/180d
# back from the data end).  Everything strictly BEFORE this date is
# untouched by the suite's acceptance rules and may tune hyperparameters.
FIRST_EVAL_WINDOW_START = pd.Timestamp("2024-10-28")
VAL_DAYS = 180


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-glob", default=os.path.join(
        os.path.dirname(HERE), "data", "clean", "????_matches.csv"))
    ap.add_argument("--half-life", type=float, default=1460.0)
    ap.add_argument("--grid", default="0.02,0.01,0.005,0.002,0.001,0.0")
    ap.add_argument("--epochs", type=int, default=240)
    ap.add_argument("--n-boot", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261028)
    args = ap.parse_args()

    t0 = time.time()
    df = load_frame(args.data_glob)
    cut = FIRST_EVAL_WINDOW_START - pd.Timedelta(days=VAL_DAYS)
    train = df[df["date"] <= cut].reset_index(drop=True)
    val = df[(df["date"] > cut) & (df["date"] < FIRST_EVAL_WINDOW_START)]
    val = val.reset_index(drop=True)
    print(f"train {len(train):,} rows (<= {cut.date()}) | "
          f"validation {len(val):,} rows ({cut.date()} .. "
          f"{FIRST_EVAL_WINDOW_START.date()}, disjoint from every realism "
          f"eval window)", flush=True)

    rows = []
    spreads = []
    for l2 in [float(x) for x in args.grid.split(",")]:
        m = fit_strengths(train, epochs=args.epochs, seed=args.seed,
                          half_life_days=args.half_life,
                          ref_date=FIRST_EVAL_WINDOW_START, l2=l2)
        rep = evaluate_window(f"l2={l2}", [m], train, val,
                              model_source=f"fit(l2={l2})",
                              seed=args.seed, n_boot=args.n_boot)
        fg_rows = rep["groups"]["favourite_tercile"]
        fg_home_z = [abs(r["z_home_win"]) for r in fg_rows]
        spread = float(np.std(m.att - m.defn))
        spreads.append(spread)
        rows.append({
            "l2": l2,
            "ll": rep["scores"]["sim"]["ll_1x2"],
            "rps": rep["scores"]["sim"]["rps_1x2"],
            "w1_total": rep["distances"]["w1_total"],
            "favgap_worst_|z|": max(fg_home_z),
            "favgap_all_|z|": [round(z, 2) for z in fg_home_z],
            "strength_spread": round(spread, 4),
            "home_adv": round(float(np.exp(m.home_adv)), 4),
            "R4": rep["verdict"]["R4_conditional"],
        })
        print(f"  l2={l2:<6} ll={rows[-1]['ll']:.5f} "
              f"w1={rows[-1]['w1_total']:.4f} "
              f"favgap worst |z|={rows[-1]['favgap_worst_|z|']:.2f} "
              f"spread={spread:.4f} R4={rows[-1]['R4']} "
              f"({time.time() - t0:.0f}s)", flush=True)

    control = next(r for r in rows if r["l2"] == 0.02)
    best_ll = min(rows, key=lambda r: r["ll"])
    best_fg = min(rows, key=lambda r: r["favgap_worst_|z|"])
    # spread must GROW monotonically as L2 shrinks — the mechanism check
    # (grid is ordered descending L2, so spread ascends along it)
    monotone = all(spreads[i] <= spreads[i + 1] + 1e-9
                   for i in range(len(spreads) - 1))
    consistent = (best_ll["l2"] == best_fg["l2"] != 0.02
                  and best_ll["ll"] < control["ll"]
                  and best_fg["favgap_worst_|z|"] < control["favgap_worst_|z|"]
                  and monotone)

    print("\n=== summary ===", flush=True)
    for r in rows:
        print(f"  l2={r['l2']:<6} ll={r['ll']:.5f} rps={r['rps']:.5f} "
              f"w1={r['w1_total']:.4f} favgap |z| {r['favgap_all_|z|']} "
              f"spread={r['strength_spread']} home_adv={r['home_adv']} "
              f"R4={r['R4']}", flush=True)
    print(f"spread monotone in L2 (compression mechanism confirmed): "
          f"{monotone}", flush=True)
    if consistent:
        print(f"RECOMMEND: l2={best_fg['l2']} — best on BOTH overall LL "
              f"({best_fg['ll']:.5f} vs control {control['ll']:.5f}) and "
              f"favourite-gap worst |z| "
              f"({best_fg['favgap_worst_|z|']:.2f} vs "
              f"{control['favgap_worst_|z|']:.2f}); verify on the full "
              f"realism suite before adopting.", flush=True)
    else:
        print("NO RECOMMENDATION: no L2 beats the control consistently on "
              "both LL and favourite-gap bias (or the compression "
              "mechanism did not behave) — the remaining defect is model "
              "form, not shrinkage.  Control 0.02 stands.", flush=True)
    print(f"total {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

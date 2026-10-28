"""Unit tests for ai/sim/compare_production.py primitives (no data needed).

Run: .venv/bin/python ai/experimental/test_compare.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retrain import damp_alpha  # noqa: E402
from sim.compare_production import (  # noqa: E402
    _ci_str,
    _fmt,
    _signed,
    best_alpha,
    ll3,
    ll_rows,
    paired_ll_ci,
)

checks = 0


def check(name: str, cond: bool) -> None:
    global checks
    checks += 1
    print(("PASS " if cond else "FAIL ") + name, flush=True)
    if not cond:
        print("FAILED:", name)
        sys.exit(1)


rng = np.random.default_rng(0)
n = 400
y = rng.integers(0, 3, size=n)

# pa knows the truth better than pb: build probs correlated with y.
pa = np.full((n, 3), 0.08)
pa[np.arange(n), y] = 0.84
pb = rng.dirichlet(np.ones(3), size=n)

# ll_rows / ll3: proper scoring.
row_ll = ll_rows(pa, y)
check("ll_rows: shape matches n", row_ll.shape == (n,))
check("ll_rows: matches -log(p_true)", abs(row_ll[0] - -np.log(pa[0, y[0]])) < 1e-12)
check("ll3: equals mean of ll_rows", abs(ll3(pa, y) - row_ll.mean()) < 1e-12)
check("ll_rows: better forecast -> lower mean LL", ll3(pa, y) < ll3(pb, y))

# best_alpha: weight on pa; pa is better so the optimum is 1.0.
mask = np.ones(n, dtype=bool)
a = best_alpha(pa, pb, y, mask)
check("best_alpha: picks full weight on the better forecast", a == 1.0)
a_flip = best_alpha(pb, pa, y, mask)
check("best_alpha: picks zero weight when roles flipped", a_flip == 0.0)
a_small = best_alpha(pa, pb, y, np.arange(n) < 10)
check("best_alpha: None when mask < 50 rows", a_small is None)
# intermediate case: blend is genuinely better than either endpoint
pm = 0.5 * pa + 0.5 * pb
a_mid = best_alpha(pm, pb, y, mask)
check("best_alpha: stays inside [0, 1]", 0.0 <= a_mid <= 1.0)

# paired_ll_ci: zero-width at zero difference, sign follows the data.
ci0 = paired_ll_ci(np.zeros(n), n_boot=200, seed=1)
check("paired_ll_ci: zero diff -> mean 0", ci0["mean"] == 0.0)
check("paired_ll_ci: zero-width CI", ci0["ci90"] == [0.0, 0.0])
d = row_ll - ll_rows(pb, y)  # pa - pb, negative on average
ci = paired_ll_ci(d, n_boot=200, seed=1)
check("paired_ll_ci: sign of mean follows data", ci["mean"] < 0)
check("paired_ll_ci: CI brackets the mean",
      ci["ci90"][0] <= ci["mean"] <= ci["ci90"][1])
check("paired_ll_ci: p_a_better high when pa clearly better",
      ci["p_a_better"] > 0.95)
ci_empty = paired_ll_ci(np.array([]), n_boot=10, seed=1)
check("paired_ll_ci: empty -> None summary",
      ci_empty["mean"] is None and ci_empty["ci90"] == [None, None])

# damp_alpha (from retrain, the production formula the script reuses).
check("damp_alpha: n=0 -> exactly the prior",
      abs(damp_alpha(0.0, 0.0, 0.35, 1000.0) - 0.35) < 1e-12)
check("damp_alpha: None fit -> prior",
      abs(damp_alpha(None, 5000, 0.35, 1000.0) - 0.35) < 1e-12)
check("damp_alpha: n=1000 -> half prior half fit",
      abs(damp_alpha(0.0, 1000.0, 0.35, 1000.0) - 0.175) < 1e-3)
check("damp_alpha: huge n -> nearly fit alpha",
      abs(damp_alpha(0.9, 1e7, 0.35, 1000.0) - 0.9) < 1e-3)

# rendering helpers tolerate None.
check("fmt(None) -> '-'", _fmt(None) == "-")
check("fmt(1.5, '.3f') formats", _fmt(1.5, ".3f") == "1.500")
check("_ci_str(None, None) -> '-'", _ci_str(None, None) == "-")
check("_ci_str formats signed CI", _ci_str(-0.01, 0.02) == "[-0.01000, +0.02000]")
check("_signed(None) -> 'n/a'", _signed(None) == "n/a")
check("_signed formats", _signed(0.01) == "+0.01000")

print(f"\nALL PASS ({checks} checks)", flush=True)

"""Checks for market blending (IMPROVEMENT_PLAN Step 1.4).

Three things can silently break the blend:

  1. de-vigging no longer producing probabilities that sum to 1 (a leftover
     bookmaker vig silently biases the blend toward one outcome)
  2. rows without odds being counted as "market available", which drags alpha
     toward 1 (i.e. throw the market edge away)
  3. alpha damping drifting the deployed weight away from the prior that won
     the rolling weekly validation, or failing to move it when the fit is
     confident — both directions matter

Run:  .venv/bin/python ai/experimental/test_alpha_blend.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)

from retrain import _devig, _valid_odds, damp_alpha    # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


# ── 1. de-vig ──────────────────────────────────────────────────────────────
odds = np.array([
    [2.0, 4.0, 4.0],      # balanced book (sum of imps = 1.0)
    [1.50, 4.0, 6.0],     # vigged book: imps sum to > 1
    [1.0, 0.0, 0.0],      # missing odds -> clamped, must not divide by zero
])
p = _devig(odds)
check("de-vig rows sum to 1", np.allclose(p.sum(axis=1), 1.0), f"sums={p.sum(axis=1)}")
check("balanced book stays balanced", np.allclose(p[0], [0.5, 0.25, 0.25]), f"p0={p[0]}")
check("vig removed (favourite shrinks)", p[1, 0] < 1.0 / 1.5, f"p1={p[1]}")
check("no NaN/inf on missing odds", np.isfinite(p).all(), f"p2={p[2]}")

# ── 2. odds validity mask ──────────────────────────────────────────────────
o = np.array([
    [2.0, 3.2, 4.5],
    [0.0, 3.2, 4.5],      # no home price
    [1.0, 1.0, 1.0],      # placeholders
    [1.6, 1.005, 7.0],    # draw price below threshold
])
v = _valid_odds(o)
check("full price row valid", bool(v[0]), f"v={v}")
check("missing odds invalid", not bool(v[1]) and not bool(v[2]), f"v={v}")
check("sub-threshold odds invalid", not bool(v[3]), f"v={v}")

# ── 3. alpha damping ───────────────────────────────────────────────────────
PRIOR, K = 0.35, 1000.0
check("fit == prior stays put", damp_alpha(0.35, 1280, PRIOR, K) == 0.35,
      f"a={damp_alpha(0.35, 1280, PRIOR, K)}")
check("no odds rows -> prior", damp_alpha(None, 0, PRIOR, K) == PRIOR,
      f"a={damp_alpha(None, 0, PRIOR, K)}")
check("fit=None -> prior", damp_alpha(None, 1280, PRIOR, K) == PRIOR)
check("prior_k=0 disables damping", damp_alpha(0.8, 1280, PRIOR, 0) == 0.8)
check("weight is n/(n+k) — at n==k the fit gets half",
      damp_alpha(1.0, 1000, 0.0, 1000.0) == 0.5,
      f"a={damp_alpha(1.0, 1000, 0.0, 1000.0)}")

low = damp_alpha(0.0, 1280, PRIOR, K)     # a fit that says "trust the market"
high = damp_alpha(1.0, 1280, PRIOR, K)    # a fit that says "ignore the market"
check("damped fit cannot reach 0", 0.0 < low < PRIOR, f"low={low}")
check("damped fit cannot reach 1", PRIOR < high < 1.0, f"high={high}")
check("more evidence moves toward the fit",
      damp_alpha(1.0, 5000, PRIOR, K) > damp_alpha(1.0, 100, PRIOR, K),
      f"n=5000 -> {damp_alpha(1.0, 5000, PRIOR, K)}, n=100 -> {damp_alpha(1.0, 100, PRIOR, K)}")

# ── 4. the blend itself moves in the right direction ───────────────────────
# Market knows better than the model here; blending with a mid alpha must
# improve log loss over the model alone and over the market alone.
rng = np.random.default_rng(7)
y = rng.integers(0, 3, size=4000)
mk = np.full((4000, 3), 1 / 3)
mk[np.arange(4000), y] = 0.7          # market: right outcome, probability 0.7
mk = mk / mk.sum(axis=1, keepdims=True)
model = rng.dirichlet([1, 1, 1], size=4000)   # uninformative model output

from sklearn.metrics import log_loss              # noqa: E402
ll_m = log_loss(y, model, labels=[0, 1, 2])
ll_k = log_loss(y, mk, labels=[0, 1, 2])
a = damp_alpha(0.35, 4000, PRIOR, K)
blended = a * model + (1 - a) * mk
blended = blended / blended.sum(axis=1, keepdims=True)
ll_b = log_loss(y, blended, labels=[0, 1, 2])
check("market beats model on this sample", ll_k < ll_m, f"model={ll_m:.4f} market={ll_k:.4f}")
check("blended beats model-only", ll_b < ll_m, f"blend={ll_b:.4f} vs model={ll_m:.4f}")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)

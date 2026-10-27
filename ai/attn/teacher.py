"""Lab-teacher posterior features: the 80-world ensemble as model input.

The committed recipe (ai/data/sim/worlds.json) is the distilled output of
the Monte Carlo Match Lab.  For every REAL fixture row we compute the exact
world-averaged scoreline grid (no Monte-Carlo error) and expose 13 marginals
+ uncertainty spreads as extra input tokens for the attention student —
a second, independently-regularised model opinion that Pico's LightGBM
never sees.  Worlds regenerate from the recipe, so this scales to any
fixture count without ever storing rows.
"""
from __future__ import annotations

import logging

import numpy as np

from sim.distill import load_teacher, teacher_may_fit
from sim.simulate import MAX_GOALS, pois_pmf, posterior_markets

logger = logging.getLogger("statwise.attn.teacher")

TEACHER_FEATURES = (
    "t_p_home", "t_p_draw", "t_p_away", "t_p_btts", "t_p_over15",
    "t_p_over25", "t_p_over35", "t_lam_h", "t_lam_a", "t_home_std",
    "t_btts_std", "t_over25_std", "t_exp_goals",
)
N_TEACHER = len(TEACHER_FEATURES)


def load_recipe(path: str, split_at) -> tuple[list | None, dict]:
    """(worlds, recipe_meta) or (None, {}) — refuses a teacher that saw
    outcomes inside the holdout window (same guard as step-1.5 distill)."""
    worlds, meta = load_teacher(path)
    if not worlds:
        return None, {}
    ok, why = teacher_may_fit(meta or {}, split_at)
    if not ok:
        logger.warning("attn teacher rejected: %s", why)
        return None, {}
    return worlds, dict(meta or {})


def fast_posterior(worlds: list, h: np.ndarray, a: np.ndarray,
                   lg: np.ndarray) -> dict[str, np.ndarray]:
    """Exact world-averaged marginals in O(W*F*11), not O(W*F*121).

    `sim.simulate.posterior_markets` materialises the full (W, F, 11, 11)
    scoreline grid (chunked) to take marginals — ~40s per 1k rows with the
    80-world recipe, i.e. 4+ hours for the 377k training rows.  But the grid
    factorises: cell(i, j) = ph_i * pa_j * tau(i, j), Dixon-Coles tau touches
    only the four low-score cells, and every published market is a linear
    functional of ph/pa — so cumsums along the 11-goal axis replace the outer
    product entirely, with identical results (asserted to 1e-9 in
    ai/experimental/test_attention.py against the reference implementation).

    Returns the same key set as posterior_markets for the marginals used
    here; falls back to the caller (original implementation) on any shape
    assumption being violated.
    """
    W = len(worlds)
    att = np.stack([m.att for m in worlds])                  # (W, n_teams)
    dfn = np.stack([m.defn for m in worlds])
    mu = np.array([m.mu for m in worlds], dtype=np.float64)  # (W,)
    ha = np.array([m.home_adv for m in worlds], dtype=np.float64)
    xi = np.array([m.xi for m in worlds], dtype=np.float64)
    nl = max(len(m.league_offset) for m in worlds)
    if any(len(m.att) != att.shape[1] or len(m.league_offset) != nl
           for m in worlds):
        raise ValueError("ragged recipe — use posterior_markets")
    lo = np.zeros((W, nl), dtype=np.float64)
    for i, m in enumerate(worlds):
        lo[i, :len(m.league_offset)] = m.league_offset

    # ── rates, stacked: exact copy of StrengthModel.rates vectorised ──
    off = lo[:, lg]                                           # (W, F)
    lam_h = np.exp(np.clip(mu[:, None] + off + ha[:, None]
                           + att[:, h] - dfn[:, a], -6.0, 6.0))
    lam_a = np.exp(np.clip(mu[:, None] + off
                           + att[:, a] - dfn[:, h], -6.0, 6.0))
    ph = pois_pmf(lam_h)                                      # (W, F, 11)
    pa = pois_pmf(lam_a)

    # ── linear functionals of the outer product ph_i * pa_j ──
    Sh, Sa = ph.sum(-1), pa.sum(-1)
    Rh = np.cumsum(ph[..., ::-1], axis=-1)[..., ::-1]         # Rh[..., m] = sum_{i>=m}
    Ra = np.cumsum(pa[..., ::-1], axis=-1)[..., ::-1]
    # base marginals WITHOUT tau: home = sum_{i>j} ph_i pa_j, etc.
    home_b = (pa[..., :MAX_GOALS] * Rh[..., 1:]).sum(-1)      # j=10 term is 0
    away_b = (ph[..., :MAX_GOALS] * Ra[..., 1:]).sum(-1)
    draw_b = (ph * pa).sum(-1)
    btts_b = (Sh - ph[..., 0]) * (Sa - pa[..., 0])

    def _over(n: int) -> np.ndarray:
        # sum_{i+j>=n} ph_i pa_j = sum_j pa_j * Rh[max(n-j, 0)]
        # (Rh[..., 0] = Sh, i.e. for j > n every i qualifies)
        out = np.zeros(Sh.shape, dtype=np.float64)
        for j in range(MAX_GOALS + 1):
            out += pa[..., j] * Rh[..., n - j if n - j > 0 else 0]
        return out

    # Dixon-Coles tau: only cells (0,0), (1,0), (0,1), (1,1) differ from
    # the product form (same factors as sim.simulate.apply_tau).
    t00, t10, t01, t11 = 1.0 - xi, 1.0 + xi, 1.0 + xi, 1.0 - xi
    p00 = ph[..., 0] * pa[..., 0]
    p10 = ph[..., 1] * pa[..., 0]
    p01 = ph[..., 0] * pa[..., 1]
    p11 = ph[..., 1] * pa[..., 1]
    d00 = p00 * (t00[:, None] - 1.0)
    d10 = p10 * (t10[:, None] - 1.0)
    d01 = p01 * (t01[:, None] - 1.0)
    d11 = p11 * (t11[:, None] - 1.0)
    s = Sh * Sa + d00 + d10 + d01 + d11
    s = np.maximum(s, 1e-300)
    denom = 1.0 / s

    marg: dict[str, np.ndarray] = {
        "home": (home_b + d10) * denom,
        "draw": (draw_b + d00 + d11) * denom,
        "away": (away_b + d01) * denom,
        "btts": (btts_b + d11) * denom,
        # tau cells with total>=2: only (1,1); totals >=3 unaffected
        "over15": (_over(2) + d11) * denom,
        "over25": _over(3) * denom,
        "over35": _over(4) * denom,
        "exp_goals": ((ph * np.arange(MAX_GOALS + 1)).sum(-1) * Sa
                      + Sh * (pa * np.arange(MAX_GOALS + 1)).sum(-1)
                      + d10 + d01 + 2.0 * d11) * denom,
    }

    out: dict[str, np.ndarray] = {}
    for k, v in marg.items():
        out[k] = v.mean(axis=0)                               # (F,)
    for k in ("home", "draw", "away", "btts", "over25"):
        v = marg[k]
        out[f"{k}_std"] = np.sqrt(np.maximum((v * v).mean(axis=0)
                                             - out[k] ** 2, 0.0))
    out["lam_h"] = lam_h.mean(axis=0)
    out["lam_a"] = lam_a.mean(axis=0)
    return out


def teacher_matrix(worlds: list, home: np.ndarray, away: np.ndarray,
                   leagues: np.ndarray, *, batch: int = 20000) -> np.ndarray:
    """(n, N_TEACHER) exact posterior features for aligned row arrays.

    Rows whose teams the recipe does not know get the NEUTRAL row (zeros
    after standardisation = the ensemble average), which is also what
    inference uses for a team the recipe has never seen.
    """
    out = np.zeros((len(home), N_TEACHER), dtype=np.float32)
    if not len(home):
        return out
    m0 = worlds[0]
    h = np.fromiter((m0.t2i.get(str(x), -1) for x in home),
                    dtype=np.int64, count=len(home))
    a = np.fromiter((m0.t2i.get(str(x), -1) for x in away),
                    dtype=np.int64, count=len(away))
    # canonical league slugs (retrain) vs the recipe's raw CSV slugs: unknown
    # ones fall back to the most neutral offset (zero-mean -> the average
    # league).  Same handling as ai/sim/distill.py.
    neutral = int(np.argmin(np.abs(m0.league_offset))) if len(m0.league_offset) else 0
    lg = np.fromiter((m0.l2i.get(str(x), neutral) for x in leagues),
                     dtype=np.int64, count=len(leagues))
    cand = np.flatnonzero((h >= 0) & (a >= 0))
    for start in range(0, len(cand), batch):
        sel = cand[start:start + batch]
        try:
            pm = fast_posterior(worlds, h[sel], a[sel], lg[sel])
        except (ValueError, IndexError):      # ragged/odd recipe -> reference
            pm = posterior_markets(worlds, h[sel], a[sel], lg[sel])
        out[sel, 0] = pm["home"]
        out[sel, 1] = pm["draw"]
        out[sel, 2] = pm["away"]
        out[sel, 3] = pm["btts"]
        out[sel, 4] = pm["over15"]
        out[sel, 5] = pm["over25"]
        out[sel, 6] = pm["over35"]
        out[sel, 7] = pm["lam_h"]
        out[sel, 8] = pm["lam_a"]
        out[sel, 9] = pm["home_std"]
        out[sel, 10] = pm["btts_std"]
        out[sel, 11] = pm["over25_std"]
        out[sel, 12] = pm["exp_goals"]
    return out

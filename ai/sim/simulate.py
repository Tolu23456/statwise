"""Monte Carlo simulation + the mergeable sufficient statistics.

Two jobs, deliberately separated:

1. **Per-fixture markets** — posterior-predictive probabilities for a real
   fixture, computed by averaging the scoreline grid over every sampled world
   (exact integration over the ensemble, so the number we publish carries no
   Monte-Carlo error).
2. **The mass simulation** — `n_sims` scorelines drawn per fixture, which is
   where the "tens of trillions of outcomes" idea actually lives.  The rows are
   never stored: what survives is a small set of *sufficient statistics*
   (scoreline counts, calibration histograms, market-frequency tables, policy
   counters) that any shard can produce and the merge can simply **sum**.

Storage rule: recipe + counters, never rows.  10^7 simulated outcomes cost a
few hundred KB of int64 counters here; as rows they would cost ~300 MB and
disappear with the artifact 90 days later.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

from .model import StrengthModel, tau_factor

MAX_GOALS = 10          # grid dimension (0..10 goals a side)
N_PROB_BINS = 10        # calibration bins for a probability
N_TOTAL_BINS = 9        # expected-total bins (0.8-wide, 0.0 .. 6.4+)

_LOG_FACT = np.concatenate([[0.0], np.cumsum(np.log(np.arange(1, MAX_GOALS + 1)))])


def pois_pmf(lam: np.ndarray) -> np.ndarray:
    """Poisson pmf over 0..MAX_GOALS for an array of rates -> (..., G+1)."""
    lam = np.clip(np.asarray(lam, dtype=np.float64), 1e-9, 20.0)
    k = np.arange(MAX_GOALS + 1)
    shape = lam.shape + (MAX_GOALS + 1,)
    L = lam[..., None]
    K = k.reshape((1,) * lam.ndim + (MAX_GOALS + 1,))
    F = _LOG_FACT.reshape((1,) * lam.ndim + (MAX_GOALS + 1,))
    return np.exp(K * np.log(L) - L - F).reshape(shape)


_TAU_MASKS = None


def _tau_masks():
    global _TAU_MASKS
    if _TAU_MASKS is None:
        ii, jj = np.meshgrid(np.arange(MAX_GOALS + 1), np.arange(MAX_GOALS + 1),
                             indexing="ij")
        _TAU_MASKS = ((ii == 0) & (jj == 0), (ii == 1) & (jj == 0),
                      (ii == 0) & (jj == 1), (ii == 1) & (jj == 1))
    return _TAU_MASKS


def apply_tau(grid: np.ndarray, xi: float) -> np.ndarray:
    """Dixon-Coles low-score correction on a (..., G+1, G+1) grid."""
    if not xi:
        return grid
    m00, m10, m01, m11 = _tau_masks()
    out = grid.copy()
    out[..., m00] *= (1.0 - xi)
    out[..., m10] *= (1.0 + xi)
    out[..., m01] *= (1.0 + xi)
    out[..., m11] *= (1.0 - xi)
    return np.maximum(out, 0.0)


def grid_marginals(grid: np.ndarray) -> dict[str, np.ndarray]:
    """H/D/A, BTTS and over-lines from a batch of grids shaped (..., G+1, G+1)."""
    s = grid.sum(axis=(-2, -1), keepdims=True)
    g = grid / np.where(s > 0, s, 1.0)
    idx = np.arange(MAX_GOALS + 1)
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    goals = ii + jj
    return {
        "home": g[..., (ii > jj)].sum(-1),
        "draw": g[..., (ii == jj)].sum(-1),
        "away": g[..., (ii < jj)].sum(-1),
        "btts": g[..., ((ii > 0) & (jj > 0))].sum(-1),
        "over15": g[..., (goals > 1.5)].sum(-1),
        "over25": g[..., (goals > 2.5)].sum(-1),
        "over35": g[..., (goals > 3.5)].sum(-1),
        "exp_goals": (g * goals).sum(axis=(-2, -1)),
    }


def fixture_grids(models: list[StrengthModel], h: np.ndarray, a: np.ndarray,
                  lg: np.ndarray) -> np.ndarray:
    """Scoreline grids for W worlds x F fixtures -> (W, F, G+1, G+1)."""
    ph, pa = [], []
    for m in models:
        lam_h, lam_a = m.rates(h, a, lg)
        ph.append(pois_pmf(lam_h))
        pa.append(pois_pmf(lam_a))
    ph, pa = np.stack(ph), np.stack(pa)
    grid = ph[..., :, None] * pa[..., None, :]
    return np.stack([apply_tau(g, m.xi) for g, m in zip(grid, models)])


def posterior_markets(models: list[StrengthModel], h: np.ndarray, a: np.ndarray,
                      lg: np.ndarray) -> dict[str, np.ndarray]:
    """World-averaged market probabilities per fixture — what we publish.

    Averages exactly over the ensemble (no Monte-Carlo error), but does so in
    *world chunks* so that 120 worlds x a few thousand fixtures never builds
    the full (W, F, 11, 11) grid at once (that would be ~0.5 GB for the
    merge job's real-holdout scoring).
    """
    if not len(models):
        raise ValueError("posterior_markets needs at least one world")
    F = len(h)
    # keep one chunk's grid under ~256 MB: chunk * F * 121 * 8 bytes
    chunk = max(1, min(len(models), int(260_000 / max(F, 1))))
    acc: dict[str, np.ndarray] = {}
    acc_sq: dict[str, np.ndarray] = {}
    lam_h = np.zeros(F)
    lam_a = np.zeros(F)
    seen = 0
    for start in range(0, len(models), chunk):
        sub = models[start:start + chunk]
        grid = fixture_grids(sub, h, a, lg)
        marg = grid_marginals(grid)
        for k, v in marg.items():
            if k not in acc:
                acc[k] = np.zeros(F)
                acc_sq[k] = np.zeros(F)
            acc[k] += v.sum(axis=0)
            acc_sq[k] += (v ** 2).sum(axis=0)
        for m in sub:
            lh, la = m.rates(h, a, lg)
            lam_h += lh
            lam_a += la
        seen += len(sub)
    out = {k: v / seen for k, v in acc.items()}
    for k in ("home", "draw", "away", "btts", "over25"):
        var = np.maximum(acc_sq[k] / seen - out[k] ** 2, 0.0)
        out[f"{k}_std"] = np.sqrt(var)
    out["lam_h"] = lam_h / seen
    out["lam_a"] = lam_a / seen
    return out


# ── Monte Carlo mass simulation ────────────────────────────────────────────
def simulate_scorelines(models: list[StrengthModel], h: np.ndarray, a: np.ndarray,
                        lg: np.ndarray, n_sims: int, *,
                        rng: np.random.Generator
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Draw `n_sims` outcomes per fixture, round-robin over worlds.

    Returns (home_goals, away_goals, fixture_index) — fixture ids are carried
    through the rejection step so they can never drift out of alignment.

    Dixon-Coles' tau is applied by rejection sampling — accept with
    tau / max(1, max tau) — which leaves the target distribution exact.  With
    |xi| <= 0.12 that accepts ~90%+ of draws, so the sample stays large.
    """
    if n_sims <= 0 or len(h) == 0:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z
    W = len(models)
    F = len(h)
    # fixture-major expansion: fixture k owns [k*n_sims, (k+1)*n_sims)
    h_rep = np.repeat(np.asarray(h), n_sims)
    a_rep = np.repeat(np.asarray(a), n_sims)
    lg_rep = np.repeat(np.asarray(lg), n_sims)
    fid = np.repeat(np.arange(F, dtype=np.int64), n_sims)
    world_of = np.tile(np.arange(n_sims) % W, F)

    lam_h = np.empty(F * n_sims)
    lam_a = np.empty(F * n_sims)
    xi_of = np.empty(F * n_sims)
    for w in range(W):
        sel = np.where(world_of == w)[0]
        if not len(sel):
            continue
        lh, la = models[w].rates(h_rep[sel], a_rep[sel], lg_rep[sel])
        lam_h[sel], lam_a[sel] = lh, la
        xi_of[sel] = models[w].xi

    i = np.clip(rng.poisson(lam_h), 0, MAX_GOALS)
    j = np.clip(rng.poisson(lam_a), 0, MAX_GOALS)

    if np.any(xi_of):
        # tau is piecewise in xi (at most 7 grid-search values) — group by value
        t = np.ones(F * n_sims)
        for x in np.unique(xi_of):
            m = xi_of == x
            t[m] = tau_factor(i[m], j[m], float(x))
        env = max(1.0, float(t.max()))
        keep = rng.random(F * n_sims) < (t / env)
        i, j, fid = i[keep], j[keep], fid[keep]
    return i, j, fid


@dataclass
class SuffStats:
    """Everything worth keeping from a simulation run.  Merge = element-wise sum."""
    scoreline: np.ndarray = field(default_factory=lambda: np.zeros(
        (MAX_GOALS + 1, MAX_GOALS + 1), dtype=np.int64))
    outcome_by_prob_bin: np.ndarray = field(default_factory=lambda: np.zeros(
        (N_PROB_BINS, 3), dtype=np.int64))
    total_by_exp_bin: np.ndarray = field(default_factory=lambda: np.zeros(
        (N_TOTAL_BINS, MAX_GOALS * 2 + 1), dtype=np.int64))
    btts_by_pred_bin: np.ndarray = field(default_factory=lambda: np.zeros(
        (N_PROB_BINS, 2), dtype=np.int64))
    over25_by_pred_bin: np.ndarray = field(default_factory=lambda: np.zeros(
        (N_PROB_BINS, 2), dtype=np.int64))
    over35_by_pred_bin: np.ndarray = field(default_factory=lambda: np.zeros(
        (N_PROB_BINS, 2), dtype=np.int64))
    policy: dict = field(default_factory=dict)
    n_sims: int = 0
    n_fixtures: int = 0
    worlds: int = 0

    def merge(self, other: "SuffStats") -> "SuffStats":
        self.scoreline += other.scoreline
        self.outcome_by_prob_bin += other.outcome_by_prob_bin
        self.total_by_exp_bin += other.total_by_exp_bin
        self.btts_by_pred_bin += other.btts_by_pred_bin
        self.over25_by_pred_bin += other.over25_by_pred_bin
        self.over35_by_pred_bin += other.over35_by_pred_bin
        for k, v in other.policy.items():
            if isinstance(v, dict):
                cur = self.policy.setdefault(k, {})
                for kk, vv in v.items():
                    cur[kk] = cur.get(kk, 0) + vv
            else:
                self.policy[k] = self.policy.get(k, 0) + v
        self.n_sims += other.n_sims
        self.n_fixtures += other.n_fixtures
        self.worlds = max(self.worlds, other.worlds)
        return self

    _ARRAYS = ("scoreline", "outcome_by_prob_bin", "total_by_exp_bin",
               "btts_by_pred_bin", "over25_by_pred_bin", "over35_by_pred_bin")

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in self._ARRAYS:
            d[k] = np.asarray(d[k], dtype=np.int64).tolist()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SuffStats":
        s = cls()
        for k in cls._ARRAYS:
            if d.get(k) is not None:
                setattr(s, k, np.asarray(d[k], dtype=np.int64))
        s.policy = dict(d.get("policy") or {})
        s.n_sims = int(d.get("n_sims", 0))
        s.n_fixtures = int(d.get("n_fixtures", 0))
        s.worlds = int(d.get("worlds", 0))
        return s


def bin_index(p: np.ndarray, n_bins: int = N_PROB_BINS) -> np.ndarray:
    return np.clip((np.asarray(p, dtype=np.float64) * n_bins).astype(int),
                   0, n_bins - 1)


def fold(s: SuffStats, *, fixture_probs: np.ndarray, pred_btts: np.ndarray,
         pred_over25: np.ndarray, pred_over35: np.ndarray, pred_exp: np.ndarray,
         fixture_of_sim: np.ndarray, sim_i: np.ndarray, sim_j: np.ndarray) -> None:
    """Fold one shard's simulated outcomes into the sufficient statistics."""
    s.scoreline += np.bincount(
        sim_i.astype(np.int64) * (MAX_GOALS + 1) + sim_j.astype(np.int64),
        minlength=(MAX_GOALS + 1) ** 2,
    ).reshape(MAX_GOALS + 1, MAX_GOALS + 1)

    fp = fixture_probs[fixture_of_sim]
    outcome = np.where(sim_i > sim_j, 0, np.where(sim_i == sim_j, 1, 2))
    np.add.at(s.outcome_by_prob_bin, (bin_index(fp), outcome), 1)

    total = np.clip(sim_i + sim_j, 0, MAX_GOALS * 2)
    exp_of_sim = pred_exp[fixture_of_sim]
    tb = np.clip((exp_of_sim / 0.8).astype(int), 0, N_TOTAL_BINS - 1)
    np.add.at(s.total_by_exp_bin, (tb, total), 1)

    btts = (sim_i > 0) & (sim_j > 0)
    o25 = total > 2.5
    o35 = total > 3.5
    for key, pred, sim in (("btts_by_pred_bin", pred_btts, btts),
                           ("over25_by_pred_bin", pred_over25, o25),
                           ("over35_by_pred_bin", pred_over35, o35)):
        np.add.at(getattr(s, key), (bin_index(pred[fixture_of_sim]),
                                    sim.astype(np.int64)), 1)

    s.n_sims += len(sim_i)


def simulate_fixtures(models: list[StrengthModel], h: np.ndarray, a: np.ndarray,
                      lg: np.ndarray, *, n_sims: int, seed: int,
                      batch_fixtures: int = 4000) -> tuple[SuffStats, dict]:
    """Simulate `n_sims` outcomes per fixture across all worlds.

    Fixtures are processed in batches so a 10^7-outcome run never holds more
    than a few million integers at once (RAM on the runners is limited).
    """
    rng = np.random.default_rng(seed)
    stats = SuffStats(worlds=len(models))
    pm = posterior_markets(models, h, a, lg)
    n = len(h)
    for start in range(0, n, batch_fixtures):
        stop = min(start + batch_fixtures, n)
        sl = slice(start, stop)
        sim_i, sim_j, fid = simulate_scorelines(models, h[sl], a[sl], lg[sl],
                                                n_sims, rng=rng)
        if not len(sim_i):
            continue
        fold(stats,
             fixture_probs=pm["home"][sl],
             pred_btts=pm["btts"][sl],
             pred_over25=pm["over25"][sl],
             pred_over35=pm["over35"][sl],
             pred_exp=pm["exp_goals"][sl],
             fixture_of_sim=fid,
             sim_i=sim_i, sim_j=sim_j)
    # fixture ids are LOCAL to each batch, so the count comes from the pool
    # size here, not from the ids fold() saw
    stats.n_fixtures = n
    return stats, {k: np.asarray(v) for k, v in pm.items()}


# ── reporting ──────────────────────────────────────────────────────────────
def calibration_table(hist: np.ndarray) -> dict:
    """Bin -> (predicted, observed) frequency: is a 60% bucket really 60%?"""
    rows = []
    for b in range(hist.shape[0]):
        n = int(hist[b].sum())
        if n < 50:
            continue
        rows.append({"bin": round((b + 0.5) / hist.shape[0], 3), "n": n,
                     "p": [round(float(x / n), 4) for x in hist[b]]})
    return {"bins": rows}


def scoreline_table(hist: np.ndarray) -> dict:
    total = max(int(hist.sum()), 1)
    p = hist / total
    idx = np.arange(hist.shape[0])
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    return {
        "n": total,
        "home": round(float(p[ii > jj].sum()), 4),
        "draw": round(float(p[ii == jj].sum()), 4),
        "away": round(float(p[ii < jj].sum()), 4),
        "btts": round(float(p[(ii > 0) & (jj > 0)].sum()), 4),
        "over25": round(float(p[(ii + jj) > 2.5].sum()), 4),
        "over35": round(float(p[(ii + jj) > 3.5].sum()), 4),
        "grid": hist.tolist(),
    }

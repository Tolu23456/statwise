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
def _stack_world_params(models: list[StrengthModel]):
    """Stack world parameters into arrays so ONE gather indexes any world.

    Returns (att, defn, mu, ha, off, xi) shaped (W, n_teams), (W, n_teams),
    (W,), (W,), (W, n_leagues), (W,).  Vectorising the world loop here is
    what turns the mass simulation from ~0.4M outcomes/s into the tens of
    millions per second a multi-billion-outcome run needs.
    """
    att = np.stack([m.att for m in models])
    dfn = np.stack([m.defn for m in models])
    mu = np.array([m.mu for m in models], dtype=np.float64)
    ha = np.array([m.home_adv for m in models], dtype=np.float64)
    nl = max(len(m.league_offset) for m in models)
    off = np.zeros((len(models), max(nl, 1)), dtype=np.float64)
    for w, m in enumerate(models):
        if len(m.league_offset):
            off[w, :len(m.league_offset)] = m.league_offset
    xi = np.array([m.xi for m in models], dtype=np.float64)
    return att, dfn, mu, ha, off, xi


_TAU_LUT: dict[float, np.ndarray] = {}


def _tau_lookup(xi: float) -> np.ndarray:
    """tau(i, j; xi) flattened over the 121 scoreline cells — cached per xi."""
    tbl = _TAU_LUT.get(xi)
    if tbl is None:
        ii, jj = np.meshgrid(np.arange(MAX_GOALS + 1), np.arange(MAX_GOALS + 1),
                             indexing="ij")
        tbl = tau_factor(ii, jj, xi).reshape(-1)
        _TAU_LUT[xi] = tbl
    return tbl


_XI_LUTS: dict = {}


def _xi_cell_lut(xi_w: np.ndarray) -> np.ndarray:
    """(W, 121) tau table: one gather indexes (world, scoreline cell)."""
    key = tuple(np.round(xi_w, 6).tolist())
    lut = _XI_LUTS.get(key)
    if lut is None:
        cells = (MAX_GOALS + 1) ** 2
        lut = np.ones((len(xi_w), cells))
        for w, x in enumerate(xi_w):
            if x:
                lut[w] = _tau_lookup(float(x))
        _XI_LUTS[key] = lut
    return lut


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

    Implementation notes (the billion-outcome fast path):
      * world parameters are stacked once and gathered with a single
        `world_of * stride + team` index instead of a Python loop per world;
      * the tau correction is a 121-cell lookup indexed by (xi, i, j)
        instead of a per-group boolean-mask walk;
      * the expansion is processed in <=1M-draw sub-chunks with int32 index
        arrays, so every gather stays cache-resident even on small-RAM
        machines (memory bandwidth, not compute, is the bottleneck).
    """
    if n_sims <= 0 or len(h) == 0:
        z = np.zeros(0, dtype=np.int64)
        return z, z, z
    W = len(models)
    att, dfn, mu, ha, off, xi_w = _stack_world_params(models)
    has_xi = bool(np.any(xi_w))
    out_i, out_j, out_f = [], [], []
    per_sub = max(1, min(len(h), 1_048_576 // max(n_sims, 1)))

    for start in range(0, len(h), per_sub):
        stop = min(start + per_sub, len(h))
        Fb = stop - start
        N = Fb * n_sims
        # fixture-major expansion: fixture k owns [k*n_sims, (k+1)*n_sims)
        w_of = np.tile(np.arange(n_sims, dtype=np.int32) % W, Fb)
        # fixture ids stay LOCAL to the caller's `h` array (0-based across
        # all of it), so `simulate_fixtures` can slice posterior rows to match.
        fid_local = np.tile(np.arange(Fb, dtype=np.int64), n_sims)
        fid_base = fid_local + start

        # rates depend on (world, fixture) only — compute the tiny (W, Fb)
        # table first, then ONE gather per side replaces three big gathers.
        hh = np.asarray(h[start:stop], dtype=np.int64)
        aa = np.asarray(a[start:stop], dtype=np.int64)
        ll = np.asarray(lg[start:stop], dtype=np.int64)
        tab_base = mu[:, None] + off[:, ll]
        lam_h_tab = np.exp(np.clip(tab_base + ha[:, None] + att[:, hh] - dfn[:, aa],
                                   -6, 6)).reshape(-1)
        lam_a_tab = np.exp(np.clip(tab_base + att[:, aa] - dfn[:, hh],
                                   -6, 6)).reshape(-1)
        del tab_base

        lam_idx = w_of * Fb + fid_local
        i = np.clip(rng.poisson(lam_h_tab[lam_idx]), 0, MAX_GOALS)
        j = np.clip(rng.poisson(lam_a_tab[lam_idx]), 0, MAX_GOALS)
        del lam_h_tab, lam_a_tab, lam_idx
        if has_xi:
            # tau depends on (world, scoreline cell) only — one (W, 121)
            # LUT gathered by a single index, instead of a mask walk per xi.
            cell = i * (MAX_GOALS + 1) + j
            t = _xi_cell_lut(xi_w).reshape(-1)[w_of * (MAX_GOALS + 1) ** 2 + cell]
            env = max(1.0, float(t.max()))
            keep = rng.random(N) < (t / env)
            i, j = i[keep], j[keep]
            fid_sub = fid_base[keep]
        else:
            fid_sub = fid_base
        out_i.append(i)
        out_j.append(j)
        out_f.append(fid_sub)
    return (np.concatenate(out_i), np.concatenate(out_j),
            np.concatenate(out_f))


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
    """Fold one shard's simulated outcomes into the sufficient statistics.

    Every counter is a `np.bincount` over a flat (bin * width + value) index —
    `np.add.at` was the per-draw bottleneck of the whole lab (an order of
    magnitude slower than bincount), and folding tens of billions of outcomes
    through it would dominate the runtime.
    """
    sim_i = np.asarray(sim_i, dtype=np.int64)
    sim_j = np.asarray(sim_j, dtype=np.int64)
    G = MAX_GOALS + 1
    s.scoreline += np.bincount(sim_i * G + sim_j,
                               minlength=G * G).reshape(G, G)

    fp = fixture_probs[fixture_of_sim]
    outcome = np.where(sim_i > sim_j, 0, np.where(sim_i == sim_j, 1, 2))
    s.outcome_by_prob_bin += np.bincount(
        bin_index(fp) * 3 + outcome,
        minlength=N_PROB_BINS * 3).reshape(N_PROB_BINS, 3)

    total = np.clip(sim_i + sim_j, 0, MAX_GOALS * 2)
    exp_of_sim = pred_exp[fixture_of_sim]
    tb = np.clip((exp_of_sim / 0.8).astype(np.int64), 0, N_TOTAL_BINS - 1)
    s.total_by_exp_bin += np.bincount(
        tb * (MAX_GOALS * 2 + 1) + total,
        minlength=N_TOTAL_BINS * (MAX_GOALS * 2 + 1)
    ).reshape(N_TOTAL_BINS, MAX_GOALS * 2 + 1)

    btts = (sim_i > 0) & (sim_j > 0)
    o25 = total > 2.5
    o35 = total > 3.5
    for key, pred, sim in (("btts_by_pred_bin", pred_btts, btts),
                           ("over25_by_pred_bin", pred_over25, o25),
                           ("over35_by_pred_bin", pred_over35, o35)):
        pb = bin_index(pred[fixture_of_sim]) * 2 + sim.astype(np.int64)
        getattr(s, key)[:] += np.bincount(
            pb, minlength=N_PROB_BINS * 2).reshape(N_PROB_BINS, 2)

    s.n_sims += len(sim_i)


def simulate_fixtures(models: list[StrengthModel], h: np.ndarray, a: np.ndarray,
                      lg: np.ndarray, *, n_sims: int, seed: int,
                      batch_fixtures: int = 4000,
                      batch_elements: int = 4_000_000
                      ) -> tuple[SuffStats, dict]:
    """Simulate `n_sims` outcomes per fixture across all worlds.

    Fixtures are processed in batches so a 10^9-outcome run never holds more
    than a few million integers at once (RAM on the runners is limited).
    """
    rng = np.random.default_rng(seed)
    stats = SuffStats(worlds=len(models))
    pm = posterior_markets(models, h, a, lg)
    n = len(h)
    # One batch expands to batch_fixtures * n_sims draws carried as ~10
    # parallel int arrays (~8 B each) — cap the expansion at ~4M elements
    # (roughly 400 MB) so `--sims 500000` (billions of outcomes across
    # shards) still runs in a few hundred MB instead of tens of GB.
    if n_sims > 0:
        batch_fixtures = max(1, min(batch_fixtures, max(1, batch_elements) // n_sims))
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

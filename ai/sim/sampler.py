"""The "cap down" posterior sampler + the breeding operator.

cap down
--------
The rule as specified: *take a step forward, add a noise, then another step
backward, add a noise, and keep doing that until we are left with possible
matches.*

Written literally that is a two-sided random walk around the fitted strengths.
Made rigorous it becomes a **symmetric mixture proposal** — one Gaussian step
toward better fit ("forward"), one Gaussian step away from it ("backward"),
each with its own injected noise — accepted with the Metropolis-Hastings
ratio:

    q(y | x) = 0.5 * N(y; x - eta * grad, sigma^2)      # forward + noise
              + 0.5 * N(y; x + eta * grad, sigma^2)      # backward + noise

    accept  = min(1, pi(y) * q(x | y) / (pi(x) * q(y | x)))

The backward step is what keeps the chain from just descending into the
nearest optimum: it is the proposal that lets the sampler *climb*, so the
ensemble it produces is a spread of plausible strength vectors (a posterior
sample) rather than one brittle point estimate.  With eta -> 0 the forward
term alone is Langevin/SGLD, so this is a noisy local search with an exact
correction bolted on.

breeding
--------
Parents are two sampled worlds; the child is an element-wise crossover of
their strength genes plus mutation noise:

    child = alpha * A + (1 - alpha) * B + N(0, mutation)

Mutation is what "randomly make teams strong and weak" does: it injects
variation the data has not ruled out.  A child is kept when its penalised
deviance improves (elitism) or with Metropolis probability at temperature T,
so the population can escape a local basin instead of collapsing onto one
clone.

Both operators only ever move *strength vectors* — the matches themselves are
generated afterwards by ai/sim/simulate.py, which is why the recipe stays
~1 MB instead of becoming 7 GB of rows.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import StrengthModel, deviance, league_series


@dataclass
class MatchData:
    """Flat arrays for one fit/evaluation frame (train or holdout)."""
    h: np.ndarray
    a: np.ndarray
    lg: np.ndarray
    y_h: np.ndarray
    y_a: np.ndarray
    l2: float = 0.02

    @classmethod
    def from_frame(cls, df, model: StrengthModel, l2: float = 0.02) -> "MatchData":
        h = df["home_team"].astype(str).map(model.t2i).to_numpy(dtype=float)
        a = df["away_team"].astype(str).map(model.t2i).to_numpy(dtype=float)
        lg = league_series(df).map(model.l2i).fillna(0).to_numpy()
        ok = ~(np.isnan(h) | np.isnan(a))
        return cls(h=h[ok].astype(np.int64), a=a[ok].astype(np.int64),
                   lg=lg[ok].astype(np.int64),
                   y_h=df["home_goals"].astype(float).to_numpy()[ok],
                   y_a=df["away_goals"].astype(float).to_numpy()[ok], l2=l2)

    def __len__(self) -> int:
        return len(self.h)

    def subsample(self, batch: int, rng: np.random.Generator) -> "MatchData":
        if batch <= 0 or batch >= len(self.h):
            return self
        idx = rng.choice(len(self.h), size=batch, replace=False)
        return MatchData(self.h[idx], self.a[idx], self.lg[idx],
                         self.y_h[idx], self.y_a[idx], self.l2)


# ── flat parameter vector <-> model ────────────────────────────────────────
def pack(m: StrengthModel) -> np.ndarray:
    return np.concatenate([m.att, m.defn, [m.mu, m.home_adv], m.league_offset, [m.xi]])


def unpack(m: StrengthModel, theta: np.ndarray) -> StrengthModel:
    n, nl = len(m.teams), len(m.leagues)
    out = StrengthModel.from_dict(m.to_dict())
    out.att = np.asarray(theta[:n], dtype=np.float64)
    out.defn = np.asarray(theta[n:2 * n], dtype=np.float64)
    out.mu = float(theta[2 * n])
    out.home_adv = float(theta[2 * n + 1])
    out.league_offset = np.asarray(theta[2 * n + 2:2 * n + 2 + nl], dtype=np.float64)
    out.xi = float(theta[-1])
    return out


def objective(m: StrengthModel, d: MatchData) -> float:
    """Penalised deviance — the quantity cap-down minimises (pi∝ exp(-L))."""
    if len(d) == 0:
        return 0.0
    base = deviance(m, d.h, d.a, d.lg, d.y_h, d.y_a)
    pen = d.l2 * float((m.att ** 2).sum() + (m.defn ** 2).sum()
                       + (m.league_offset ** 2).sum())
    return base + pen


def gradient(m: StrengthModel, d: MatchData) -> np.ndarray:
    """Gradient of `objective` w.r.t. pack(m) — pure numpy, one pass."""
    if len(d) == 0:
        return np.zeros_like(pack(m))
    lam_h, lam_a = m.rates(d.h, d.a, d.lg)
    r_h = lam_h - d.y_h
    r_a = lam_a - d.y_a
    n, nl = len(m.teams), len(m.leagues)

    g_att = (np.bincount(d.h, weights=r_h, minlength=n)
             + np.bincount(d.a, weights=r_a, minlength=n)) / len(d) + d.l2 * m.att
    g_def = (np.bincount(d.a, weights=-r_h, minlength=n)
             + np.bincount(d.h, weights=-r_a, minlength=n)) / len(d) + d.l2 * m.defn
    g_off = np.bincount(d.lg, weights=r_h + r_a, minlength=nl) / len(d) \
        + d.l2 * m.league_offset
    g_mu = float(r_h.sum() + r_a.sum()) / len(d)
    g_ha = float(r_h.sum()) / len(d)

    # Dixon-Coles xi term: d/dxi [ -log tau ]
    xi = m.xi
    if xi:
        y_h = d.y_h.astype(int)
        y_a = d.y_a.astype(int)
        c0011 = ((y_h == 0) & (y_a == 0)) | ((y_h == 1) & (y_a == 1))
        c1001 = ((y_h == 1) & (y_a == 0)) | ((y_h == 0) & (y_a == 1))
        g_xi = (float(c0011.sum()) / max(1.0 - xi, 1e-6)
                - float(c1001.sum()) / max(1.0 + xi, 1e-6)) / len(d)
    else:
        g_xi = 0.0

    return np.concatenate([g_att, g_def, [g_mu, g_ha], g_off, [g_xi]])


def _log_q(y: np.ndarray, x: np.ndarray, g: np.ndarray, eta: float,
           sigma: float) -> float:
    """log of the symmetric mixture proposal density q(y | x)."""
    s2 = sigma * sigma
    d1 = y - (x - eta * g)
    d2 = y - (x + eta * g)
    # log( 0.5 * N(d1) + 0.5 * N(d2) ) via log-sum-exp
    lp1 = -0.5 * float((d1 * d1).sum()) / s2
    lp2 = -0.5 * float((d2 * d2).sum()) / s2
    hi = max(lp1, lp2)
    return hi + np.log(0.5 * (np.exp(lp1 - hi) + np.exp(lp2 - hi)))


def cap_down(model: StrengthModel, data: MatchData, *, steps: int = 400,
             eta: float = 0.02, sigma: float = 0.01, warmup: int = 100,
             batch: int = 0, seed: int = 0, keep_every: int = 1,
             adapt: bool = True):
    """Run the forward/backward noisy sampler.  Returns (final_model, chain, meta).

    `chain` holds `pack(theta)` snapshots (every `keep_every` post-warmup step)
    — these are the "possible matches" strength vectors the breeding stage
    picks parents from.  `batch > 0` subsamples matches per gradient call
    (unbiased estimate; use it when the full frame is too slow).
    """
    rng = np.random.default_rng(seed)
    theta = pack(model)
    n_params = theta.size
    g = gradient(model, data)
    L = objective(model, data)
    chain: list[np.ndarray] = []
    accepted = 0
    sig = sigma
    acc_win = 0

    for t in range(steps):
        d = data.subsample(batch, rng) if batch else data
        g = gradient(unpack(model, theta), d)

        # one noisy step: forward (toward better fit) or backward (away from
        # it, so the chain can climb out of a basin), each with fresh noise
        y_fwd = theta - eta * g + sig * rng.normal(size=n_params)
        y_bwd = theta + eta * g + sig * rng.normal(size=n_params)
        y = y_fwd if rng.random() < 0.5 else y_bwd

        m_y = unpack(model, y)
        L_y = objective(m_y, d)
        g_y = gradient(m_y, d)
        # MH on the symmetric mixture proposal: pi(y)/pi(x) * q(x|y)/q(y|x)
        log_alpha = (L - L_y
                     + _log_q(theta, y, g_y, eta, sig)
                     - _log_q(y, theta, g, eta, sig))
        if np.log(rng.random() + 1e-300) < log_alpha:
            theta, L, accepted = y, L_y, accepted + 1
            acc_win += 1

        if adapt and t < warmup and (t + 1) % 10 == 0:
            rate = acc_win / 10.0
            sig = float(np.clip(sig * (1.25 if rate > 0.55 else
                                       0.8 if rate < 0.25 else 1.0), 1e-6, 1.0))
            acc_win = 0
        if t >= warmup and (t - warmup) % keep_every == 0:
            chain.append(theta.copy())

    final = unpack(model, theta)
    meta = {"steps": steps, "eta": eta, "sigma_final": round(float(sig), 6),
            "acceptance": round(accepted / max(steps, 1), 4),
            "warmup": warmup, "n_params": int(n_params),
            "batch": int(batch or len(data)), "n_matches": int(len(data)),
            "final_objective": round(float(L), 4)}
    return final, chain, meta


def breed(parent_a: StrengthModel, parent_b: StrengthModel, data: MatchData, *,
          rng: np.random.Generator, mutation: float = 0.02, alpha: float | None = None,
          temperature: float = 0.0) -> tuple[StrengthModel, bool]:
    """Crossover + mutation of two strength vectors; returns (child, accepted)."""
    a, b = pack(parent_a), pack(parent_b)
    if alpha is None:
        alpha = float(rng.uniform(0.15, 0.85))
    child = alpha * a + (1.0 - alpha) * b
    # mutation: random strong/weak perturbation, applied to the strength genes
    # only (not the base rates) so a child stays a plausible goal process
    n = len(parent_a.teams)
    child[:2 * n] += rng.normal(0.0, mutation, 2 * n)
    child[2 * n + 1] += rng.normal(0.0, mutation * 0.25)   # home advantage
    child[-1] = float(np.clip(child[-1], -0.2, 0.2))       # xi stays in range

    m_child = unpack(parent_a, child)
    L_c = objective(m_child, data)
    L_best = min(objective(parent_a, data), objective(parent_b, data))
    if L_c <= L_best:
        return m_child, True
    if temperature > 0:
        if rng.random() < np.exp(-(L_c - L_best) / temperature):
            return m_child, True
    return (parent_a if objective(parent_a, data) <= objective(parent_b, data)
            else parent_b), False


def breed_population(worlds: list[StrengthModel], data: MatchData, *,
                     rng: np.random.Generator, generations: int = 12,
                     population: int = 8, mutation: float = 0.02,
                     temperature: float = 0.0) -> tuple[list[StrengthModel], dict]:
    """Evolve a population of worlds: pick parents, crossover, keep the fit."""
    pop = list(worlds)
    stats = {"children": 0, "accepted": 0}
    for _ in range(generations):
        scored = sorted(pop, key=lambda m: objective(m, data))
        elite = scored[:max(2, population // 2)]
        while len(pop) < population:
            if len(elite) >= 2:
                pa, pb = rng.choice(len(elite), size=2, replace=False)
            else:
                # only one world available (worlds=1): self-crossover — the
                # mutation term still supplies the variation
                pa = pb = 0
            child, ok = breed(elite[pa], elite[pb], data, rng=rng,
                              mutation=mutation, temperature=temperature)
            stats["children"] += 1
            stats["accepted"] += int(ok)
            pop.append(child)
        pop = sorted(pop, key=lambda m: objective(m, data))[:population]
    return pop, stats  # type: ignore[return-value]

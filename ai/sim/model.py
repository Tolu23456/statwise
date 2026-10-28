"""Dixon-Coles style goal process: team strengths -> scoreline distribution.

The "fool's plan" simulator needs a generative model of a match.  We use the
classic bivariate Poisson with a Dixon-Coles low-score correction:

    lam_h = exp(mu + league_offset[L] + home_adv + att[h] - def[a])
    lam_a = exp(mu + league_offset[L] +                 att[a] - def[h])
    P(i,j) = Pois(i; lam_h) * Pois(j; lam_a) * tau(i, j; xi)

`tau` is Dixon-Coles' correction for the 0-0 / 1-0 / 0-1 / 1-1 cells, which a
plain independent Poisson systematically underestimates — and those are
exactly the scorelines that decide draws and BTTS markets.

Everything here is deliberately numpy-only and vectorised: a full fit over the
377k-row clean set is a few seconds, which is why the heavy work lives in
GitHub Actions (see .github/workflows/simulate.yml) and the unit tests use a
few hundred synthetic rows.

Fitting is penalised maximum likelihood (Poisson deviance + L2) by gradient
descent with a zero-mean projection on the strength vectors so the
att/def/mu decomposition stays identifiable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Scorelines the Dixon-Coles tau correction applies to.
_DC_CELLS = ((0, 0), (1, 0), (0, 1), (1, 1))

# log(k!) for k <= 20 — goal counts never exceed this, and a lookup beats a
# vectorised Python loop over 327k rows seven times over (the xi grid search).
_LOGF = np.concatenate([[0.0], np.cumsum(np.log(np.arange(1, 21)))])


def league_series(df: pd.DataFrame) -> pd.Series:
    """The frame's league labels as strings, whatever the column is called."""
    for col in ("league_slug", "league"):
        if col in df.columns:
            return df[col].astype(str)
    return pd.Series(["all"] * len(df), index=df.index)


def tau_factor(i: np.ndarray, j: np.ndarray, xi: float) -> np.ndarray:
    """Dixon-Coles tau(i, j; xi) for arrays of scorelines.

    xi = 0 disables the correction (returns ones).  xi < 0 is the usual fit:
    independent Poisson over-produces 1-1 and under-produces 0-0/1-0/0-1.
    """
    out = np.ones(np.broadcast(i, j).shape, dtype=np.float64)
    if xi == 0:
        return out
    for (a, b) in _DC_CELLS:
        m = (i == a) & (j == b)
        if not m.any():
            continue
        if a == 0 and b == 0:
            out[m] = 1.0 - xi
        elif (a, b) == (1, 0) or (a, b) == (0, 1):
            out[m] = 1.0 + xi
        else:  # (1, 1)
            out[m] = 1.0 - xi
    return np.maximum(out, 1e-6)


def scoreline_probs(lam_h: float, lam_a: float, xi: float = 0.0,
                    max_goals: int = 10) -> np.ndarray:
    """(max_goals+1, x max_goals+1) matrix of P(home=i, away=j)."""
    i = np.arange(max_goals + 1)
    ph = _pois_pmf(lam_h, i)
    pa = _pois_pmf(lam_a, i)
    grid = np.outer(ph, pa)
    if xi:
        ii, jj = np.meshgrid(i, i, indexing="ij")
        grid = grid * tau_factor(ii, jj, xi)
    s = grid.sum()
    return grid / s if s > 0 else grid


def _pois_pmf(lam: float, k: np.ndarray) -> np.ndarray:
    """Poisson pmf without scipy (lam is a scalar, k an int array)."""
    lam = float(max(lam, 1e-9))
    # log pmf = k*log(lam) - lam - lgamma(k+1); use Stirling-free exact form
    log_fact = np.array([np.sum(np.log(np.arange(1, int(x) + 1))) if x > 0 else 0.0
                         for x in k], dtype=np.float64)
    return np.exp(k * np.log(lam) - lam - log_fact)


def market_probs(grid: np.ndarray) -> dict:
    """Marginals every market we publish, from one scoreline grid."""
    total = grid.sum()
    if total <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3, "btts": 0.0,
                "over15": 0.0, "over25": 0.0, "over35": 0.0}
    grid = grid / total
    n = grid.shape[0]
    idx = np.arange(n)
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    goals = ii + jj
    return {
        "home": float(grid[ii > jj].sum()),
        "draw": float(grid[ii == jj].sum()),
        "away": float(grid[ii < jj].sum()),
        "btts": float(grid[(ii > 0) & (jj > 0)].sum()),
        "over15": float(grid[goals > 1.5].sum()),
        "over25": float(grid[goals > 2.5].sum()),
        "over35": float(grid[goals > 3.5].sum()),
        "lam_h": float((grid * ii).sum()),
        "lam_a": float((grid * jj).sum()),
    }


class StrengthModel:
    """Fitted team strengths + league base rates.

    Attributes are plain numpy/dict so the whole thing serialises to ~1 MB of
    JSON — the "recipe, not the rows" storage rule.
    """

    def __init__(self, teams: list[str], leagues: list[str]):
        self.teams = list(teams)
        self.leagues = list(leagues)
        self.t2i = {t: i for i, t in enumerate(self.teams)}
        self.l2i = {l: i for i, l in enumerate(self.leagues)}
        n, m = len(self.teams), len(self.leagues)
        self.att = np.zeros(n)
        self.defn = np.zeros(n)
        self.mu = np.log(1.35)          # ~2.7 total goals / 2 sides
        self.league_offset = np.zeros(m)
        self.home_adv = np.log(1.20)     # ~20% home boost to the home side
        self.xi = 0.0
        self.n_matches = 0

    # ── serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "teams": self.teams,
            "leagues": self.leagues,
            "att": np.round(self.att, 5).tolist(),
            "def": np.round(self.defn, 5).tolist(),
            "mu": round(float(self.mu), 6),
            "league_offset": np.round(self.league_offset, 5).tolist(),
            "home_adv": round(float(self.home_adv), 6),
            "xi": round(float(self.xi), 6),
            "n_matches": int(self.n_matches),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrengthModel":
        m = cls(d["teams"], d["leagues"])
        m.att = np.asarray(d["att"], dtype=np.float64)
        m.defn = np.asarray(d["def"], dtype=np.float64)
        m.mu = float(d["mu"])
        m.league_offset = np.asarray(d.get("league_offset", np.zeros(len(m.leagues))),
                                     dtype=np.float64)
        m.home_adv = float(d.get("home_adv", 0.0))
        m.xi = float(d.get("xi", 0.0))
        m.n_matches = int(d.get("n_matches", 0))
        return m

    # ── rates ────────────────────────────────────────────────────────────
    def rates(self, h_idx: np.ndarray, a_idx: np.ndarray,
              l_idx: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Expected goals (lam_h, lam_a) for arrays of team/league indices."""
        off = self.league_offset[l_idx] if l_idx is not None else 0.0
        base = self.mu + off + self.home_adv
        lam_h = np.exp(np.clip(base + self.att[h_idx] - self.defn[a_idx], -6, 6))
        lam_a = np.exp(np.clip(self.mu + off + self.att[a_idx] - self.defn[h_idx], -6, 6))
        return lam_h, lam_a

    def sample_strengths(self, rng: np.random.Generator,
                         scale: float = 0.0) -> "StrengthModel":
        """A copy with its strengths perturbed — used to seed cap-down worlds."""
        m = StrengthModel.from_dict(self.to_dict())
        if scale:
            m.att = m.att + rng.normal(0.0, scale, len(m.att))
            m.defn = m.defn + rng.normal(0.0, scale, len(m.defn))
            m.home_adv = m.home_adv + rng.normal(0.0, scale * 0.2)
        return m


def decay_weights(dates: pd.Series, half_life_days: float,
                  ref_date=None) -> np.ndarray:
    """Exponential recency weights: w = 0.5 ** (age_days / half_life).

    `ref_date` defaults to the newest date in `dates`, so a windowed refit
    (realism suite) decays relative to its own window start.  Rows with
    unparseable dates get weight 0 (they cannot be placed in time, so they
    must not dominate a recency-weighted fit).
    """
    d = pd.to_datetime(dates, errors="coerce")
    ref = pd.Timestamp(ref_date) if ref_date is not None else d.max()
    age = (ref - d).dt.days.to_numpy(dtype=np.float64)
    w = np.where(np.isfinite(age), np.power(2.0, -np.clip(age, 0.0, None)
                                            / float(half_life_days)), 0.0)
    return w


def _build_arrays(df, teams: list[str], leagues: list[str]):
    t2i = {t: i for i, t in enumerate(teams)}
    l2i = {l: i for i, l in enumerate(leagues)}
    h = df["home_team"].astype(str).map(t2i).to_numpy()
    a = df["away_team"].astype(str).map(t2i).to_numpy()
    lg = league_series(df).map(l2i).fillna(0).to_numpy()
    y_h = df["home_goals"].astype(float).to_numpy()
    y_a = df["away_goals"].astype(float).to_numpy()
    ok = ~(np.isnan(h.astype(float)) | np.isnan(a.astype(float)))
    return h[ok], a[ok], lg[ok], y_h[ok], y_a[ok], ok


def fit_strengths(df, *, home_adv: float = None, l2: float = 0.02,
                  lr: float = 0.35, epochs: int = 260, xi: bool = True,
                  seed: int = 0, half_life_days: float | None = None,
                  ref_date=None) -> StrengthModel:
    """Penalised-MLE team strengths by gradient descent.

    `df` needs home_team, away_team, home_goals, away_goals (+ league_slug,
    date when half_life_days is set).  Deliberately plain numpy: no
    sklearn/scipy dependency, fully deterministic for a given `seed`.

    `half_life_days` (None = flat history, the legacy behaviour) applies
    exponential recency weights to EVERY term of the likelihood — mu,
    home_adv, league offsets, att/def and the xi grid search — so the whole
    goal process is internally consistent at one time scale.  Production
    measured a 4-year half-life strictly best for the LightGBM
    (retrain.py: 1.00999 vs 1.01275 flat); the default in run.py mirrors
    that measurement rather than tuning against any eval window.
    """
    # `seed` kept for API symmetry with the runner; the fit itself is a
    # deterministic gradient descent, so a given frame + epochs -> same model.
    teams = sorted(set(df["home_team"].astype(str)) | set(df["away_team"].astype(str)))
    leagues = sorted(set(league_series(df)))
    m = StrengthModel(teams, leagues)
    h, a, lg, y_h, y_a, ok = _build_arrays(df, teams, leagues)
    m.n_matches = len(h)
    if len(h) == 0:
        return m

    if half_life_days is not None:
        w = decay_weights(df["date"], half_life_days, ref_date=ref_date)[ok]
    else:
        w = np.ones(len(h))
    w_sum = max(float(w.sum()), 1e-9)

    n, nl = len(teams), len(leagues)
    if home_adv is not None:
        m.home_adv = float(home_adv)

    # Work on flat float arrays so every update is a plain ufunc.
    att, dfn = np.zeros(n), np.zeros(n)
    off = np.zeros(nl)
    mu, ha = m.mu, m.home_adv

    # Precompute the per-match league indicator as index arrays (no sparse mat).
    for _ in range(epochs):
        base = mu + off[lg]
        lam_h = np.exp(np.clip(base + ha + att[h] - dfn[a], -6, 6))
        lam_a = np.exp(np.clip(base + att[a] - dfn[h], -6, 6))
        r_h = w * (lam_h - y_h)      # d NLL / d log-lam_h, recency-weighted
        r_a = w * (lam_a - y_a)

        g_att = np.bincount(h, weights=r_h, minlength=n) + \
                np.bincount(a, weights=r_a, minlength=n)
        g_def = np.bincount(a, weights=-r_h, minlength=n) + \
                np.bincount(h, weights=-r_a, minlength=n)
        g_off = np.bincount(lg, weights=r_h + r_a, minlength=nl)
        g_mu = float(r_h.sum() + r_a.sum())
        g_ha = float(r_h.sum())

        att -= lr * (g_att / w_sum + l2 * att)
        dfn -= lr * (g_def / w_sum + l2 * dfn)
        off -= lr * (g_off / w_sum + l2 * off)
        mu -= lr * (g_mu / w_sum + 1e-4 * mu)
        ha -= lr * (g_ha / w_sum + 1e-4 * ha)

        # Identifiability: strengths are only defined up to a constant.
        att -= att.mean()
        dfn -= dfn.mean()
        off -= off.mean()

    m.att, m.defn, m.mu, m.home_adv, m.league_offset = att, dfn, mu, ha, off
    if xi:
        m.xi = _fit_xi(m, h, a, lg, y_h, y_a, w=w)
    return m


def _fit_xi(m: StrengthModel, h, a, lg, y_h, y_a, w=None) -> float:
    """Grid-search the Dixon-Coles correction (a cheap 1-D search)."""
    lam_h, lam_a = m.rates(h, a, lg)
    lh = np.clip(lam_h, 1e-9, None)
    la = np.clip(lam_a, 1e-9, None)
    yi, aj = y_h.astype(int), y_a.astype(int)
    if w is None:
        w = np.ones(len(h))
    # Poisson part is xi-independent; only log(tau) changes across the grid
    pois = float((w * (y_h * np.log(lh) - lh + y_a * np.log(la) - la
                       - _LOGF[np.clip(yi, 0, 20)]
                       - _LOGF[np.clip(aj, 0, 20)])).sum())
    best_xi, best_ll = 0.0, pois          # xi = 0 => log tau = 0
    for xi in (-0.12, -0.09, -0.06, -0.03, 0.03, 0.06):
        ll = pois + float((w * np.log(tau_factor(yi, aj, xi))).sum())
        if ll > best_ll:
            best_ll, best_xi = ll, xi
    return best_xi


def deviance(m: StrengthModel, h, a, lg, y_h, y_a, w=None) -> float:
    """Poisson deviance (fit objective, lower is better) — used by cap-down.

    `w` (None = uniform) applies the same recency weights the base fit saw,
    so the sampler's posterior stays centred on the decayed optimum instead
    of dragging worlds back toward the flat-history fit.
    """
    lam_h, lam_a = m.rates(h, a, lg)
    eps = 1e-9
    term_h = lam_h - y_h * np.log(np.clip(lam_h, eps, None))
    term_a = lam_a - y_a * np.log(np.clip(lam_a, eps, None))
    t = tau_factor(y_h.astype(int), y_a.astype(int), m.xi)
    per_row = term_h + term_a - np.log(t)
    if w is not None:
        per_row = per_row * w
    return float(per_row.sum())

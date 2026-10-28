"""Historical realism validation — simulated vs REAL held-out matches.

Answers the mission's mandatory question: *does the simulator reproduce the
statistical behaviour of football matches it has never seen?*  Nothing here
fits anything to the evaluation rows: every window's model is trained only on
matches strictly before the window start (chronological splits only), and the
published recipe is used only for windows that start after its split_at.

What is compared, per held-out window (all frequencies get Wilson 95% CIs on
the observed side and a calibration z on the predicted side):

  * mean/variance of home, away and total goals;
  * total-goal, goal-difference and exact-score distributions
    (Wasserstein-1 distances);
  * draw / home-win / away-win / BTTS / O1.5 / O2.5 / O3.5 / 0-0 / 1-0 /
    1-1 / 2-1 / high-scoring (>=5 goals) frequencies;
  * proper scores (log loss, Brier, Ranked Probability Score, ECE) against
    the league-prior and de-vigged-market baselines on the SAME rows;
  * conditional realism: per league, by predicted total, by predicted
    favourite strength — a simulator that matches the overall mean while
    being wrong for a subgroup must be detected;
  * half-time structure where the data supports it (halftime_* coverage is
    reported, never imputed) and red-card / neutral-venue data facts, which
    the static score generator does NOT model (stated, not hidden).

Acceptance rules (documented, derived — not arbitrary):

  R1 global calibration: every headline event's calibration z (sum of
     residuals over sqrt of binomial variance) within +/-3.  Under a
     calibrated simulator each has a ~0.27% two-sided exceedance rate; with
     7 headline events the family-wise false-fail rate is ~2%.
  R2 distribution shape: W1(total goals) and W1(goal difference) must fall
     under the SPLIT-HALF NOISE FLOOR — the 95th percentile of W1 between
     two random halves of the same real window.  That floor is measured
     from historical data itself (sample-size dependent), and halves at n/2
     are ~2x noisier than a full-sample vs truth comparison, so the rule is
     conservative rather than lenient.
  R3 proper scores: reported with bootstrap CIs vs league prior and market;
     a window "passes" R3 only if the point estimate beats the league prior
     (the same bar ai/sim/evaluate.py gates on — never a new bar).
  R4 conditional: no group with n >= 100 may show |z| > 4 on a headline
     event (stricter than R1 to account for testing many groups).

Overall pass = every window passes R1, R2 and R4; R3 is reported alongside
(R3 failures mean "not a better predictor than a league constant", which the
separate publishable gate already decides).  A single passing number is
never presented as proof — the report always shows all metrics.

Usage (CI, .github/workflows/simulate.yml -> mode realism):

    python ai/sim/realism.py --tag v1 --windows 4 \
        --recipe ai/data/sim/worlds.json --out-dir ai/sim/reports

Local runs must stay tiny: the unit tests (ai/experimental/test_realism.py)
exercise this module on a few hundred synthetic rows.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # ai/ -> `import sim...`

from sim.model import StrengthModel, fit_strengths               # noqa: E402
from sim.simulate import MAX_GOALS, fixture_grids, pois_pmf      # noqa: E402

REALISM_SCHEMA = "statwise.sim.realism.v1"
EPS = 1e-9
G = MAX_GOALS + 1                        # grid dimension 0..10 (11x11 cells)
TOTAL_BINS = 8                           # total-goals bins: 0,1,2,3,4,5,6,7+
N_TOTAL = TOTAL_BINS                     # ...and their count (0..6 unit bins + 7+)
GD_LO, GD_HI = -4, 4                     # goal-difference bins (clip ends)

USECOLS = ("date", "home_team", "away_team", "home_goals", "away_goals",
           "league_slug", "is_international", "quality_score", "is_neutral",
           "reds_home", "reds_away", "halftime_home", "halftime_away",
           "odds_home", "odds_draw", "odds_away",
           "avg_odds_home", "avg_odds_draw", "avg_odds_away")


# ── data ───────────────────────────────────────────────────────────────────
def load_frame(data_glob: str) -> pd.DataFrame:
    """Production-aligned rows: club football, quality >= 40, >= 1993-08-01.

    Deliberately the SAME population ai/retrain.py trains on (audit W1: the
    lab's own loader kept internationals and low-quality rows, which made its
    holdout incomparable with production's).  Neutral-venue and red-card
    columns are kept for the conditional/data-fact sections.
    """
    paths = sorted(glob.glob(data_glob))
    if not paths:
        raise SystemExit(f"no season files matching {data_glob}")
    frames = [pd.read_csv(p, usecols=lambda c: c in USECOLS, low_memory=False)
              for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "home_team", "away_team",
                           "home_goals", "away_goals"])
    df = df[df["date"] >= pd.Timestamp("1993-08-01")]
    if "is_international" in df.columns:
        df = df[pd.to_numeric(df["is_international"],
                              errors="coerce").fillna(0) == 0]
    if "quality_score" in df.columns:
        df = df[pd.to_numeric(df["quality_score"],
                              errors="coerce").fillna(0) >= 40]
    if "league_slug" not in df.columns:
        df["league_slug"] = "all"
    df["league_slug"] = df["league_slug"].fillna("all").astype(str)
    return df.sort_values("date", kind="stable").reset_index(drop=True)


def make_windows(df: pd.DataFrame, holdout_days: int, n_windows: int, *,
                 min_train: int = 5000, min_eval: int = 100) -> list[dict]:
    """n chronological (train, eval) windows ending at the data cutoff.

    Window k evaluates (max-(k+1)*H, max-k*H] and may train only on rows
    <= its start, so nothing in an evaluation window was ever fitted.
    """
    dmax = pd.to_datetime(df["date"]).max()
    out = []
    for k in range(n_windows):
        start = dmax - pd.Timedelta(days=(k + 1) * holdout_days)
        end = dmax - pd.Timedelta(days=k * holdout_days)
        train = df[df["date"] <= start]
        ev = df[(df["date"] > start) & (df["date"] <= end)]
        if len(ev) < min_eval or len(train) < min_train:
            continue
        out.append({"k": k, "start": start, "end": end,
                    "train": train.reset_index(drop=True),
                    "eval": ev.reset_index(drop=True)})
    return out


# ── model helpers ──────────────────────────────────────────────────────────
def pad_worlds(worlds: list[StrengthModel]) -> list[StrengthModel]:
    """Append a league-average 'unknown team' slot (att=def=0) to every world.

    A team promoted after the training cutoff would otherwise have no
    strength vector; mapping it to league average is the honest default and
    the number of such rows is reported instead of silently dropped
    (dropping promoted teams would bias the sample — they are systematically
    weaker than the table suggests).
    """
    out = []
    for m in worlds:
        c = StrengthModel.from_dict(m.to_dict())
        c.att = np.concatenate([m.att, [0.0]])
        c.defn = np.concatenate([m.defn, [0.0]])
        out.append(c)
    return out


def map_teams(df: pd.DataFrame, worlds: list[StrengthModel]
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Row -> (home idx, away idx, league idx, n_unknown_teams_rows).

    Unknown teams land on the padded dummy slot; unknown leagues on the most
    neutral offset (zero-mean offsets => the average league).
    """
    m0 = worlds[0]
    dummy = len(m0.teams)
    h = np.fromiter((m0.t2i.get(str(x), dummy) for x in df["home_team"]),
                    dtype=np.int64, count=len(df))
    a = np.fromiter((m0.t2i.get(str(x), dummy) for x in df["away_team"]),
                    dtype=np.int64, count=len(df))
    neutral = int(np.argmin(np.abs(m0.league_offset))) if len(m0.league_offset) else 0
    lg = np.fromiter((m0.l2i.get(str(x), neutral) for x in df["league_slug"]),
                     dtype=np.int64, count=len(df))
    n_unknown = int(((h == dummy) | (a == dummy)).sum())
    return h, a, lg, n_unknown


def average_grids(worlds: list[StrengthModel], h: np.ndarray, a: np.ndarray,
                  lg: np.ndarray, *, row_batch: int = 4000) -> np.ndarray:
    """World-averaged scoreline grid per fixture -> (n, G, G).  Exact
    integration over the ensemble (no Monte-Carlo error), chunked so
    worlds x row_batch x 121 never blows past ~256 MB."""
    n = len(h)
    out = np.zeros((n, G, G), dtype=np.float64)
    W = len(worlds)
    w_chunk = max(1, min(W, int(260_000 / max(row_batch, 1) // (G * G) or 1)))
    for s in range(0, n, row_batch):
        e = min(s + row_batch, n)
        acc = np.zeros((e - s, G, G))
        for w0 in range(0, W, w_chunk):
            acc += fixture_grids(worlds[w0:w0 + w_chunk],
                                 h[s:e], a[s:e], lg[s:e]).sum(axis=0)
        acc /= W
        tot = acc.sum(axis=(1, 2), keepdims=True)
        out[s:e] = acc / np.where(tot > 0, tot, 1.0)
    return out


# ── scoring primitives ─────────────────────────────────────────────────────
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson 95% interval for an observed frequency."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (float((c - r) / d), float((c + r) / d))


def z_cal(y: np.ndarray, p: np.ndarray) -> float:
    """Calibration-in-the-large z: sum(y-p) / sqrt(sum p(1-p)).

    0 = perfectly centred; |z| ~ number of standard deviations the observed
    event count sits from the simulator's prediction.
    """
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    denom = np.sqrt(float((p * (1 - p)).sum()))
    return float((y - p).sum() / denom) if denom > 0 else 0.0


def wasserstein1(obs: np.ndarray, pred: np.ndarray) -> float:
    """W1 between two PMFs over the same ordered bins (unit bin cost)."""
    o = np.asarray(obs, dtype=float)
    q = np.asarray(pred, dtype=float)
    o = o / max(o.sum(), EPS)
    q = q / max(q.sum(), EPS)
    return float(np.abs(np.cumsum(o) - np.cumsum(q)).sum())


def rps_mean(probs: np.ndarray, y: np.ndarray) -> float:
    """Ranked Probability Score for ordered classes [away, draw, home]."""
    order = np.array([2, 1, 0])                 # away < draw < home
    p = probs[:, order]
    cdf_p = np.cumsum(p, axis=1)[:, :2]
    onehot = np.eye(3)[:, order][y]
    cdf_o = np.cumsum(onehot, axis=1)[:, :2]
    return float((((cdf_p - cdf_o) ** 2).sum(axis=1)).mean() * 0.5)


def ll_multiclass(probs: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(np.clip(probs[np.arange(len(y)), y], EPS, 1.0)).mean())


def ll_binary(p: np.ndarray, y: np.ndarray) -> float:
    q = np.where(y > 0, p, 1 - p)
    return float(-np.log(np.clip(q, EPS, 1.0)).mean())


def ece_binary(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    total, ece = len(p), 0.0
    for b in range(bins):
        m = (p >= edges[b]) & (p < edges[b + 1] if b < bins - 1 else p <= edges[b + 1])
        if m.any():
            ece += (int(m.sum()) / total) * abs(float(p[m].mean()) - float(y[m].mean()))
    return float(round(ece, 4))


def bootstrap_mean_ci(d: np.ndarray, *, n_boot: int = 1000, seed: int = 0,
                      q: float = 0.05) -> tuple[float, float]:
    """Percentile CI of the mean of per-row quantities (e.g. LL differences)."""
    d = np.asarray(d, dtype=float)
    if not len(d):
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(n_boot, len(d)))
    means = d[idx].mean(axis=1)
    return (float(np.quantile(means, q)), float(np.quantile(means, 1 - q)))


def split_half_floor(values: np.ndarray, n_bins: int, *, n_boot: int = 300,
                     seed: int = 0, q: float = 0.95) -> float:
    """95th pct W1 between two random halves of ONE real window's histogram.

    This is the historical-variability threshold: it is measured from real
    matches at the same sample size, not chosen.  Halves at n/2 are ~2x
    noisier than a full-sample-vs-truth comparison, so passing it is a
    conservative requirement.  `values` must already be bin indices in
    [0, n_bins).
    """
    values = np.asarray(values, dtype=int)
    n = len(values)
    if n < 40:
        return float("nan")
    rng = np.random.default_rng(seed)
    floors = []
    for _ in range(n_boot):
        perm = rng.permutation(n)
        half = n // 2
        pa = np.bincount(values[perm[:half]], minlength=n_bins)[:n_bins]
        pb = np.bincount(values[perm[half:2 * half]], minlength=n_bins)[:n_bins]
        floors.append(wasserstein1(pa, pb))
    return float(np.quantile(floors, q))


# ── events ─────────────────────────────────────────────────────────────────
def event_masks(i: np.ndarray, j: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean indicator per named event for observed scorelines."""
    tot = i + j
    return {
        "home_win": i > j,
        "draw": i == j,
        "away_win": i < j,
        "btts": (i > 0) & (j > 0),
        "over15": tot > 1.5,
        "over25": tot > 2.5,
        "over35": tot > 3.5,
        "score_0_0": (i == 0) & (j == 0),
        "score_1_0": (i == 1) & (j == 0),
        "score_1_1": (i == 1) & (j == 1),
        "score_2_1": (i == 2) & (j == 1),
        "high_scoring": tot >= 5,
    }


HEADLINE_EVENTS = ("home_win", "draw", "away_win", "btts", "over15",
                   "over25", "over35")
CONDITIONAL_EVENTS = ("home_win", "draw", "away_win", "btts", "over25")
EXACT_EVENTS = ("score_0_0", "score_1_0", "score_1_1", "score_2_1",
                "high_scoring")


def grids_event_probs(grids: np.ndarray, events: tuple[str, ...]
                      ) -> dict[str, np.ndarray]:
    """Per-fixture probability of each event from (n, G, G) grids."""
    idx = np.arange(G)
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    obs_i, obs_j = ii.ravel(), jj.ravel()
    flat = grids.reshape(len(grids), -1)
    out: dict[str, np.ndarray] = {}
    masks = event_masks(obs_i, obs_j)
    for name in events:
        out[name] = flat @ masks[name].astype(np.float64)
    return out


def grid_moments(grids: np.ndarray) -> dict[str, np.ndarray]:
    """Per-fixture predictive mean/var for home, away and total goals."""
    idx = np.arange(G)
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    g = grids
    eh = (g * ii).sum(axis=(1, 2))
    ea = (g * jj).sum(axis=(1, 2))
    vh = (g * (ii - eh[:, None, None]) ** 2).sum(axis=(1, 2))
    va = (g * (jj - ea[:, None, None]) ** 2).sum(axis=(1, 2))
    et = eh + ea
    # total variance needs the covariance term — computed directly from the
    # joint grid, not var_h + var_a (which would ignore it)
    vt = (g * (ii + jj - et[:, None, None]) ** 2).sum(axis=(1, 2))
    return {"lam_h": eh, "lam_a": ea, "var_h": vh, "var_a": va,
            "lam_t": et, "var_t": vt}


# ── baselines ──────────────────────────────────────────────────────────────
def league_prior_probs(train: pd.DataFrame, eval_df: pd.DataFrame
                       ) -> tuple[np.ndarray, np.ndarray]:
    """Per-league 1X2 prior from TRAIN rows only (fallback: pooled).

    Returns (prior probabilities over eval rows (n,3), observed y1x2 (n,))."""
    lg_tr = train["league_slug"].astype(str)
    y_arr = _y1x2(train)
    table: dict[str, np.ndarray] = {}
    for name, idx in lg_tr.groupby(lg_tr).groups.items():
        y = y_arr[lg_tr.index.get_indexer(idx)]   # groupby gives labels, y is positional
        table[str(name)] = np.bincount(y, minlength=3) / max(len(y), 1)
    pooled = np.bincount(y_arr, minlength=3) / max(len(y_arr), 1)
    y_ev = _y1x2(eval_df)
    prior = np.stack([table.get(str(s), pooled) for s in eval_df["league_slug"]])
    return prior, y_ev


def _y1x2(df: pd.DataFrame) -> np.ndarray:
    h = df["home_goals"].to_numpy(dtype=float)
    a = df["away_goals"].to_numpy(dtype=float)
    return np.where(h > a, 0, np.where(h == a, 1, 2)).astype(int)


def market_probs(eval_df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """De-vigged closing-consensus 1X2 where odds exist.

    Returns (probs (m,3), mask over eval rows).  avg_odds_* is the cross-book
    average of closing prices (same source production's alpha blends on);
    falls back to the single-book price.
    """
    n = len(eval_df)

    def _col(name: str) -> np.ndarray:
        out = np.full(n, np.nan)
        for src in (f"avg_odds_{name}", f"odds_{name}"):
            if src in eval_df.columns:
                v = pd.to_numeric(eval_df[src], errors="coerce").to_numpy(dtype=float)
                fill = np.isnan(out) & np.isfinite(v) & (v > 1.01)
                out = np.where(fill, v, out)
        return out

    o = np.column_stack([_col("home"), _col("draw"), _col("away")])
    good = np.isfinite(o).all(axis=1) & (o > 1.01).all(axis=1)
    inv = np.where(good[:, None], 1.0 / np.maximum(o, 1.0001), 0.0)
    s = inv.sum(axis=1, keepdims=True)
    mv = np.where(good[:, None] & (s > 0), inv / np.maximum(s, EPS), 0.0)
    return mv, good


# ── window evaluation ──────────────────────────────────────────────────────
def evaluate_window(name: str, worlds: list[StrengthModel], train: pd.DataFrame,
                    ev: pd.DataFrame, *, model_source: str, seed: int,
                    n_boot: int = 1000) -> dict:
    """Full realism report for one held-out window."""
    t0 = time.time()
    if "league_slug" not in ev.columns:
        ev = ev.assign(league_slug="all")
    if "league_slug" not in train.columns:
        train = train.assign(league_slug="all")
    worlds = pad_worlds(worlds)
    h, a, lg, n_unknown = map_teams(ev, worlds)
    grids = average_grids(worlds, h, a, lg)

    obs_i = ev["home_goals"].to_numpy(dtype=int)
    obs_j = ev["away_goals"].to_numpy(dtype=int)
    y = _y1x2(ev)
    n = len(ev)

    # predictive 1X2 marginals from the averaged grids
    ev_home = grids_event_probs(grids, ("home_win",))["home_win"]
    ev_draw = grids_event_probs(grids, ("draw",))["draw"]
    ev_away = grids_event_probs(grids, ("away_win",))["away_win"]
    probs = np.column_stack([ev_home, ev_draw, ev_away])
    probs = probs / probs.sum(axis=1, keepdims=True)

    ev_pred = grids_event_probs(grids, HEADLINE_EVENTS + EXACT_EVENTS)
    obs_masks = event_masks(obs_i, obs_j)

    # ── global frequencies: observed (Wilson CI) vs predicted (z) ─────────
    global_tbl = {}
    for name_ev in HEADLINE_EVENTS + EXACT_EVENTS:
        k = int(obs_masks[name_ev].sum())
        p = ev_pred[name_ev]
        global_tbl[name_ev] = {
            "obs": round(k / n, 4), "obs_ci": [round(x, 4) for x in wilson(k, n)],
            "pred": round(float(p.mean()), 4),
            "z": round(z_cal(obs_masks[name_ev].astype(float), p), 3),
            "n": n,
        }

    # ── moments ───────────────────────────────────────────────────────────
    mom = grid_moments(grids)
    obs_tot = obs_i + obs_j
    moments = {}
    for key, obs_v, lam_v, var_v in (
            ("home_goals", obs_i.astype(float), mom["lam_h"], mom["var_h"]),
            ("away_goals", obs_j.astype(float), mom["lam_a"], mom["var_a"]),
            ("total_goals", obs_tot.astype(float), mom["lam_t"], mom["var_t"])):
        ov, pv = float(obs_v.mean()), float(lam_v.mean())
        # SE of (observed mean - predictive mean): under the model the
        # observed mean varies with per-fixture variance + between-fixture
        # spread of expectations, both estimated from the predictive grids.
        se = float(np.sqrt((var_v.mean() + lam_v.var()) / max(n, 1)))
        moments[key] = {
            "obs_mean": round(ov, 4), "pred_mean": round(pv, 4),
            "obs_var": round(float(obs_v.var()), 4),
            "pred_var": round(float(var_v.mean() + lam_v.var()), 4),
            "delta": round(ov - pv, 4),
            "z": round((ov - pv) / se, 3) if se > 0 else 0.0,
        }

    # ── histograms + W1 with the split-half noise floor ───────────────────
    tot_bins = np.minimum(obs_tot, N_TOTAL - 1)
    pred_total_pmf = grids.reshape(n, -1) @ _total_pmf_rows()
    gd_bins = np.clip(obs_i - obs_j, GD_LO, GD_HI) - GD_LO
    pred_gd_pmf = grids.reshape(n, -1) @ _gd_pmf_rows()
    floors = {
        "total": round(split_half_floor(tot_bins, N_TOTAL, seed=seed), 5),
        "gd": round(split_half_floor(gd_bins, GD_HI - GD_LO + 1,
                                     seed=seed + 1), 5),
    }
    distances = {
        "w1_total": round(wasserstein1(np.bincount(tot_bins, minlength=N_TOTAL),
                                       pred_total_pmf.mean(axis=0)), 5),
        "w1_total_floor95": floors["total"],
        "w1_gd": round(wasserstein1(np.bincount(gd_bins,
                                                minlength=GD_HI - GD_LO + 1),
                                    pred_gd_pmf.mean(axis=0)), 5),
        "w1_gd_floor95": floors["gd"],
    }

    # ── proper scores + baselines on identical rows ───────────────────────
    prior, _y_from_prior = league_prior_probs(train, ev)
    mv, mkt_mask = market_probs(ev)
    y_btts = obs_masks["btts"].astype(int)
    y_o25 = obs_masks["over25"].astype(int)
    y_o35 = obs_masks["over35"].astype(int)
    p_btts = ev_pred["btts"]
    p_o25 = ev_pred["over25"]
    p_o35 = ev_pred["over35"]

    scores = {
        "n": n,
        "sim": {
            "ll_1x2": round(ll_multiclass(probs, y), 5),
            "brier_1x2": round(float(((probs - np.eye(3)[y]) ** 2).mean()), 5),
            "rps_1x2": round(rps_mean(probs, y), 5),
            "ll_btts": round(ll_binary(p_btts, y_btts), 5),
            "ll_o25": round(ll_binary(p_o25, y_o25), 5),
            "ll_o35": round(ll_binary(p_o35, y_o35), 5),
        },
        "league_prior": {
            "ll_1x2": round(ll_multiclass(prior, y), 5),
            "brier_1x2": round(float(((prior - np.eye(3)[y]) ** 2).mean()), 5),
            "rps_1x2": round(rps_mean(prior, y), 5),
        },
    }
    if int(mkt_mask.sum()) > 50:
        scores["market"] = {
            "n": int(mkt_mask.sum()),
            "ll_1x2": round(ll_multiclass(mv[mkt_mask], y[mkt_mask]), 5),
            "brier_1x2": round(float(((mv[mkt_mask] - np.eye(3)[y[mkt_mask]]) ** 2).mean()), 5),
            "rps_1x2": round(rps_mean(mv[mkt_mask], y[mkt_mask]), 5),
        }
        d = -np.log(np.clip(probs[np.arange(n), y], EPS, 1.0)) \
            + np.log(np.clip(mv[np.arange(n), y], EPS, 1.0))
        d = d[mkt_mask]
        scores["ll_diff_sim_minus_market_ci90"] = [
            round(x, 5) for x in bootstrap_mean_ci(d, n_boot=n_boot, seed=seed)]
    d_prior = -np.log(np.clip(probs[np.arange(n), y], EPS, 1.0)) \
        + np.log(np.clip(prior[np.arange(n), y], EPS, 1.0))
    scores["ll_diff_sim_minus_prior_ci90"] = [
        round(x, 5) for x in bootstrap_mean_ci(d_prior, n_boot=n_boot, seed=seed + 2)]
    scores["ece_1x2_conf"] = round(_ece_conf(probs, y), 4)
    scores["ece_btts"] = ece_binary(p_btts, y_btts)
    scores["ece_o25"] = ece_binary(p_o25, y_o25)

    # ── conditional groups ────────────────────────────────────────────────
    groups = {
        "league": _group_by_league(ev, obs_masks, ev_pred, min_n=100),
        "pred_total_tercile": _group_by_tercile(
            mom["lam_t"], ev, obs_masks, ev_pred, "pred_total"),
        "favourite_tercile": _group_by_tercile(
            probs[:, 0] - probs[:, 2], ev, obs_masks, ev_pred, "favourite_gap"),
    }

    # ── half-time structure (only where the data exists) ──────────────────
    halves = _half_time_section(worlds, train, ev)

    # ── data facts the static generator does not model ────────────────────
    facts = _data_facts(ev)

    # ── R1..R4 verdicts ───────────────────────────────────────────────────
    r1 = all(abs(global_tbl[e]["z"]) <= 3.0 for e in HEADLINE_EVENTS)
    r2 = (distances["w1_total"] <= distances["w1_total_floor95"]
          and distances["w1_gd"] <= distances["w1_gd_floor95"])
    r3 = scores["sim"]["ll_1x2"] < scores["league_prior"]["ll_1x2"]
    r4 = True
    worst_group_z = 0.0
    for gt in groups.values():
        for row in gt:
            if row["n"] >= 100:
                for e in CONDITIONAL_EVENTS:
                    worst_group_z = max(worst_group_z, abs(row.get(f"z_{e}", 0.0)))
    r4 = worst_group_z <= 4.0

    return {
        "name": name,
        "model_source": model_source,
        "n_eval": n,
        "n_train": len(train),
        "n_unknown_team_rows": n_unknown,
        "unknown_team_rate": round(n_unknown / max(n, 1), 4),
        "global": global_tbl,
        "moments": moments,
        "distances": distances,
        "scores": scores,
        "groups": groups,
        "halves": halves,
        "data_facts": facts,
        "verdict": {"R1_calibration": bool(r1), "R2_shape": bool(r2),
                    "R3_beats_prior": bool(r3), "R4_conditional": bool(r4),
                    "worst_group_abs_z": round(worst_group_z, 3),
                    "window_pass": bool(r1 and r2 and r4)},
        "runtime_s": round(time.time() - t0, 1),
    }


def _ece_conf(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    conf = probs.max(axis=1)
    correct = (probs.argmax(axis=1) == y).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for b in range(bins):
        m = (conf >= edges[b]) & (conf < edges[b + 1] if b < bins - 1
                                  else conf <= edges[b + 1])
        if m.any():
            ece += (int(m.sum()) / len(conf)) * abs(float(correct[m].mean())
                                                    - float(conf[m].mean()))
    return float(ece)


_TOTAL_PMF_ROWS: np.ndarray | None = None
_GD_PMF_ROWS: np.ndarray | None = None


def _total_pmf_rows() -> np.ndarray:
    """(G*G, 8) row-lookup: scoreline cell -> total-goals bin (0..6, 7+)."""
    global _TOTAL_PMF_ROWS
    if _TOTAL_PMF_ROWS is None:
        idx = np.arange(G)
        ii, jj = np.meshgrid(idx, idx, indexing="ij")
        tot = (ii + jj).ravel()
        rows = np.zeros((G * G, N_TOTAL))
        # unit-width bins 0..6, last bin folds everything >= 7 (7+)
        rows[np.arange(G * G), np.minimum(tot, N_TOTAL - 1)] = 1.0
        _TOTAL_PMF_ROWS = rows
    return _TOTAL_PMF_ROWS


def _gd_pmf_rows() -> np.ndarray:
    """(G*G, 9) row-lookup: scoreline cell -> goal-difference bin."""
    global _GD_PMF_ROWS
    if _GD_PMF_ROWS is None:
        idx = np.arange(G)
        ii, jj = np.meshgrid(idx, idx, indexing="ij")
        gd = np.clip((ii - jj).ravel(), GD_LO, GD_HI) - GD_LO
        rows = np.zeros((G * G, GD_HI - GD_LO + 1))
        rows[np.arange(G * G), gd] = 1.0
        _GD_PMF_ROWS = rows
    return _GD_PMF_ROWS


# ── conditional grouping ───────────────────────────────────────────────────
def _group_rows(labels: np.ndarray, ev: pd.DataFrame, obs_masks: dict,
                ev_pred: dict, key: str) -> list[dict]:
    rows = []
    for lab in sorted(pd.unique(labels)):
        m = labels == lab
        n_m = int(m.sum())
        if n_m < 30:
            continue
        row = {"group": key, "value": str(lab), "n": n_m}
        for e in CONDITIONAL_EVENTS:
            k = int(obs_masks[e][m].sum())
            p = ev_pred[e][m]
            row[f"obs_{e}"] = round(k / n_m, 4)
            row[f"pred_{e}"] = round(float(p.mean()), 4)
            row[f"z_{e}"] = round(z_cal(obs_masks[e][m].astype(float), p), 3)
        rows.append(row)
    return rows


def _group_by_league(ev, obs_masks, ev_pred, min_n: int) -> list[dict]:
    rows = _group_rows(ev["league_slug"].astype(str).to_numpy(), ev,
                       obs_masks, ev_pred, "league")
    return [r for r in rows if r["n"] >= min_n]


def _group_by_tercile(score: np.ndarray, ev, obs_masks, ev_pred,
                      key: str) -> list[dict]:
    q = np.quantile(score, [1 / 3, 2 / 3])
    labels = np.digitize(score, q)
    names = {0: "low", 1: "mid", 2: "high"}
    lab = np.array([names[x] for x in labels])
    # keep a stable order low -> mid -> high
    rows = _group_rows(lab, ev, obs_masks, ev_pred, key)
    order = {"low": 0, "mid": 1, "high": 2}
    return sorted(rows, key=lambda r: order.get(r["value"], 9))


# ── half-time & data facts ─────────────────────────────────────────────────
def _num(df: pd.DataFrame, col: str) -> pd.Series:
    """Numeric column or all-NaN when the frame does not carry it."""
    if col not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _half_time_section(worlds: list[StrengthModel], train: pd.DataFrame,
                       ev: pd.DataFrame) -> dict:
    """First/second-half structure — only where halftime scores exist.

    The static generator has no match-state model; halves are derived with a
    defensible assumption: Poisson thinning (each goal independently lands in
    the first half with rate p1, estimated on TRAIN rows only).  The
    prediction averages the per-world thinned grids exactly (no
    average-lambda shortcut — E[f(λ)] ≠ f(E[λ]) for market probabilities).
    Coverage and the assumption are both reported — this is never presented
    as a full event-by-event simulation.
    """
    ht = _num(ev, "halftime_home")
    ha = _num(ev, "halftime_away")
    mask = (ht.notna() & ha.notna()).to_numpy()
    cov = float(mask.mean())
    out: dict = {"coverage": round(cov, 4), "n": int(mask.sum())}
    if int(mask.sum()) < 100:
        out["note"] = "insufficient halftime coverage in this window"
        return out

    ht_tr = _num(train, "halftime_home")
    ha_tr = _num(train, "halftime_away")
    m_tr = (ht_tr.notna() & ha_tr.notna()).to_numpy()
    n_tr = int(m_tr.sum())
    if n_tr:
        ft_tr = float((train["home_goals"] + train["away_goals"]).to_numpy()[m_tr].mean())
        ht_goals_tr = float((ht_tr.to_numpy()[m_tr] + ha_tr.to_numpy()[m_tr]).mean())
        p1 = float(np.clip(ht_goals_tr / ft_tr, 0.1, 0.9)) if ft_tr > 0 else 0.45
    else:
        p1 = 0.45
    out["p_first_half"] = round(p1, 4)
    out["p1_source"] = "train rows with halftime present" if n_tr else "default 0.45"

    i = ev["home_goals"].to_numpy(dtype=int)[mask]
    j = ev["away_goals"].to_numpy(dtype=int)[mask]
    h1 = ht.to_numpy(dtype=float)[mask]
    a1 = ha.to_numpy(dtype=float)[mask]
    m = mask.sum()

    # observed first-half facts
    y1 = np.where(h1 > a1, 0, np.where(h1 == a1, 1, 2))
    g1, g2 = h1 + a1, (i + j - h1 - a1).astype(float)
    out["obs"] = {
        "mean_1h_goals": round(float(g1.mean()), 4),
        "mean_2h_goals": round(float(g2.mean()), 4),
        "ht_home_win": round(float((y1 == 0).mean()), 4),
        "ht_draw": round(float((y1 == 1).mean()), 4),
        "ht_away_win": round(float((y1 == 2).mean()), 4),
        "corr_1h_2h_goals": round(float(np.corrcoef(g1, g2)[0, 1]), 4),
    }

    # predicted: thinning per world, then EXACT averaging over worlds.
    h_idx = np.flatnonzero(mask)
    h_arr, a_arr, lg_arr, _ = map_teams(ev.iloc[h_idx], worlds)
    idx = np.arange(G)
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    p_ht = np.zeros((len(h_idx), 3))
    lam_t_sum = np.zeros(len(h_idx))
    for m_world in worlds:
        lh, la = m_world.rates(h_arr, a_arr, lg_arr)
        gh = pois_pmf(p1 * lh)
        ga = pois_pmf(p1 * la)
        grid1 = gh[:, :, None] * ga[:, None, :]
        grid1 /= grid1.sum(axis=(1, 2), keepdims=True)
        p_ht[:, 0] += (grid1 * (ii > jj)).sum(axis=(1, 2))
        p_ht[:, 1] += (grid1 * (ii == jj)).sum(axis=(1, 2))
        p_ht[:, 2] += (grid1 * (ii < jj)).sum(axis=(1, 2))
        lam_t_sum += lh + la
    p_ht /= len(worlds)
    lam_t_rows = lam_t_sum / len(worlds)          # per-row world-averaged total rate
    lam_t_mean = float(lam_t_rows.mean())
    pred_1h = p1 * lam_t_mean
    pred_2h = (1 - p1) * lam_t_mean
    out["pred"] = {
        "mean_1h_goals": round(float(pred_1h), 4),
        "mean_2h_goals": round(float(pred_2h), 4),
        "ht_home_win": round(float(p_ht[:, 0].mean()), 4),
        "ht_draw": round(float(p_ht[:, 1].mean()), 4),
        "ht_away_win": round(float(p_ht[:, 2].mean()), 4),
        "assumption": "Poisson thinning of predictive lambdas "
                      "(world-averaged; no match-state model)",
    }
    se_1h = max(float(g1.std()) / np.sqrt(m), EPS)
    se_2h = max(float(g2.std()) / np.sqrt(m), EPS)
    out["z"] = {
        "mean_1h_goals": round(float((g1.mean() - pred_1h) / se_1h), 3),
        "mean_2h_goals": round(float((g2.mean() - pred_2h) / se_2h), 3),
        "ht_home_win": round(z_cal(y1 == 0, p_ht[:, 0]), 3),
        "ht_draw": round(z_cal(y1 == 1, p_ht[:, 1]), 3),
        "ht_away_win": round(z_cal(y1 == 2, p_ht[:, 2]), 3),
    }
    return out


def _data_facts(ev: pd.DataFrame) -> dict:
    """Quantifies what the static score generator does NOT model."""
    facts: dict = {}
    reds = _num(ev, "reds_home").fillna(0) + _num(ev, "reds_away").fillna(0)
    m = (reds > 0).to_numpy()
    tot = ev["home_goals"].to_numpy(dtype=float) + ev["away_goals"].to_numpy(dtype=float)
    facts["red_cards"] = {
        "share_matches_with_red": round(float(m.mean()), 4),
        "n_with_red": int(m.sum()),
        "mean_goals_with_red": round(float(tot[m].mean()), 3) if m.any() else None,
        "mean_goals_no_red": round(float(tot[~m].mean()), 3),
        "modelled": False,
        "note": "static generator cannot condition on red cards — the gap "
                "between these means is the size of the unmodelled effect",
    }
    if "is_neutral" in ev.columns:
        neutral = (_num(ev, "is_neutral").fillna(0) > 0).to_numpy()
        y = _y1x2(ev)
        facts["neutral_venue"] = {
            "share_neutral": round(float(neutral.mean()), 4),
            "home_win_rate_neutral": round(float((y[neutral] == 0).mean()), 4)
            if neutral.any() else None,
            "home_win_rate_regular": round(float((y[~neutral] == 0).mean()), 4),
            "note": "home advantage is applied uniformly; neutral rows are "
                    "rare in club data but this quantifies the exposure",
        }
    return facts


# ── report assembly ────────────────────────────────────────────────────────
def run_windows(df: pd.DataFrame, *, holdout_days: int, n_windows: int,
                recipe_path: str | None, epochs: int, capdown_worlds: int,
                capdown_steps: int, seed: int, n_boot: int,
                min_train: int = 5000, min_eval: int = 100,
                log=print) -> list[dict]:
    from sim.sampler import MatchData, cap_down

    windows = make_windows(df, holdout_days, n_windows,
                           min_train=min_train, min_eval=min_eval)
    recipe_worlds, rmeta = _load_recipe(recipe_path) if recipe_path else (None, None)
    r_split = (rmeta or {}).get("split_at")
    out = []
    for w in windows:
        name = f"W{w['k']}_{w['start'].date()}_{w['end'].date()}"
        worlds: list[StrengthModel] | None = None
        source = "fresh_fit"
        if recipe_worlds is not None and r_split:
            # recipe may only score windows that START after its training cut
            if pd.Timestamp(r_split) <= w["start"]:
                worlds, source = recipe_worlds, f"recipe(split_at={r_split})"
            else:
                log(f"  {name}: recipe split {r_split} >= window start "
                    f"{w['start'].date()} — would leak, refitting instead")
        if worlds is None:
            m = fit_strengths(w["train"], epochs=epochs, seed=seed + w["k"])
            log(f"  {name}: fresh fit on {m.n_matches:,} rows "
                f"(home_adv={np.exp(m.home_adv):.3f}, xi={m.xi:.3f})")
            if capdown_worlds > 0:
                data = MatchData.from_frame(w["train"], m)
                worlds = []
                for c in range(capdown_worlds):
                    rng = np.random.default_rng(seed + 1000 * (w["k"] + 1) + c)
                    start = m.sample_strengths(rng, scale=0.02)
                    final, _chain, meta = cap_down(
                        start, data, steps=capdown_steps, eta=0.02, sigma=0.01,
                        warmup=min(80, capdown_steps // 3),
                        seed=int(rng.integers(1 << 31)), keep_every=10)
                    worlds.append(final)
                    log(f"    cap-down world {c}: acceptance={meta['acceptance']:.2f}")
                source = f"fresh_fit+capdown({capdown_worlds}w,{capdown_steps}s)"
            else:
                worlds = [m]
        rep = evaluate_window(name, worlds, w["train"], w["eval"],
                              model_source=source, seed=seed, n_boot=n_boot)
        rep["window_start"] = str(w["start"].date())
        rep["window_end"] = str(w["end"].date())
        log(f"  {name}: n={rep['n_eval']} R1={rep['verdict']['R1_calibration']} "
            f"R2={rep['verdict']['R2_shape']} R3={rep['verdict']['R3_beats_prior']} "
            f"R4={rep['verdict']['R4_conditional']} "
            f"(w1_tot={rep['distances']['w1_total']}/"
            f"{rep['distances']['w1_total_floor95']}) "
            f"ll={rep['scores']['sim']['ll_1x2']} "
            f"prior={rep['scores']['league_prior']['ll_1x2']}")
        out.append(rep)
    return out


def _load_recipe(path: str):
    import json as _json
    from sim.merge import recipe_models
    with open(path, encoding="utf-8") as fh:
        recipe = _json.load(fh)
    if not recipe.get("models"):
        raise SystemExit(f"recipe {path} has no models")
    return recipe_models(recipe), dict(recipe.get("meta") or {})


def build_report(*, tag: str, windows: list[dict], df_len: int,
                 holdout_days: int, seed: int, epochs: int,
                 capdown_worlds: int, capdown_steps: int,
                 recipe_path: str | None, duration_s: float) -> dict:
    overall = all(w["verdict"]["window_pass"] for w in windows)
    report = {
        "schema": REALISM_SCHEMA,
        "meta": {
            "tag": tag,
            "generated_at": pd.Timestamp.now("UTC").isoformat(),
            "data_rows": df_len,
            "holdout_days": holdout_days,
            "seed": seed,
            "epochs": epochs,
            "capdown_worlds": capdown_worlds,
            "capdown_steps": capdown_steps,
            "recipe": recipe_path,
            "duration_s": round(duration_s, 1),
            "thresholds": {
                "R1": "|z| <= 3 on headline events (~2% family-wise false "
                      "fail for 7 events under a calibrated simulator)",
                "R2": "W1 <= 95th pct of split-half real-vs-real W1 at the "
                      "same sample size (measured, not chosen)",
                "R3": "point-estimate 1X2 LL beats the league prior (same "
                      "bar as the publishable gate; reported, not required "
                      "for window_pass)",
                "R4": "|z| <= 4 for every group with n >= 100",
            },
        },
        "windows": windows,
        # 0 usable windows is a FAIL, not a vacuous pass (data too thin or
        # every window filtered out — the suite proved nothing)
        "verdict": {"all_windows_pass": bool(overall and windows),
                    "n_windows": len(windows),
                    "passed": sum(1 for w in windows
                                  if w["verdict"]["window_pass"])},
    }
    return report


def find_previous(out_dir: str, tag: str) -> str | None:
    cands = sorted(glob.glob(os.path.join(out_dir, "realism_*.json")))
    cands = [c for c in cands
             if not c.endswith(f"realism_{tag}.json")]
    return cands[-1] if cands else None


def compare_reports(prev: dict, cur: dict) -> dict:
    """Headline deltas vs the previous simulator version's report."""
    def _flat(rep):
        rows = {}
        for w in rep.get("windows", []):
            rows[w["name"]] = {
                "ll_1x2": w["scores"]["sim"]["ll_1x2"],
                "rps_1x2": w["scores"]["sim"]["rps_1x2"],
                "w1_total": w["distances"]["w1_total"],
                "w1_gd": w["distances"]["w1_gd"],
                "pass": w["verdict"]["window_pass"],
            }
        return rows
    a, b = _flat(prev), _flat(cur)
    common = sorted(set(a) & set(b))
    return {
        "previous_tag": prev.get("meta", {}).get("tag"),
        "previous_generated_at": prev.get("meta", {}).get("generated_at"),
        "deltas": {k: {m: round(b[k][m] - a[k][m], 5)
                       if isinstance(a[k][m], (int, float))
                       and isinstance(b[k][m], (int, float)) else None
                       for m in a[k]} for k in common},
    }


def render_md(report: dict, comparison: dict | None) -> str:
    m = report["meta"]
    lines = [
        f"# Realism validation — `{m['tag']}`",
        "",
        f"> generated {m['generated_at']} · schema {report['schema']} · "
        f"data rows {m['data_rows']:,} · seed {m['seed']} · "
        f"runtime {m['duration_s']}s",
        "",
        f"**Overall: {'PASS' if report['verdict']['all_windows_pass'] else 'FAIL'}** "
        f"({report['verdict']['passed']}/{report['verdict']['n_windows']} windows; "
        "rules R1/R2/R4 must all hold, R3 reported)",
        "",
        "## Thresholds (derived, not chosen)",
    ]
    for k, v in m["thresholds"].items():
        lines.append(f"- **{k}**: {v}")
    for w in report["windows"]:
        v = w["verdict"]
        s = w["scores"]
        lines += [
            "",
            f"## {w['name']} — {'PASS' if v['window_pass'] else 'FAIL'} "
            f"(model: {w['model_source']}; n={w['n_eval']:,})",
            "",
            "| event | observed (95% CI) | simulator | z |",
            "|---|---|---|---|",
        ]
        for e, row in w["global"].items():
            lo, hi = row["obs_ci"]
            lines.append(f"| {e} | {row['obs']:.3f} [{lo:.3f}, {hi:.3f}] "
                         f"| {row['pred']:.3f} | {row['z']:+.2f} |")
        d = w["distances"]
        lines += [
            "",
            f"- moments: " + ", ".join(
                f"{k} obs {v2['obs_mean']:.3f} vs pred {v2['pred_mean']:.3f} "
                f"(z {v2['z']:+.2f})" for k, v2 in w["moments"].items()),
            f"- W1 total: **{d['w1_total']:.4f}** (noise floor "
            f"{d['w1_total_floor95']:.4f}) · W1 goal-diff: "
            f"**{d['w1_gd']:.4f}** (floor {d['w1_gd_floor95']:.4f})",
            f"- scores: sim LL {s['sim']['ll_1x2']:.5f} / RPS "
            f"{s['sim']['rps_1x2']:.5f} · league prior "
            f"{s['league_prior']['ll_1x2']:.5f} · market "
            f"{s.get('market', {}).get('ll_1x2', 'n/a')} · "
            f"LL(sim−prior) CI90 {s.get('ll_diff_sim_minus_prior_ci90')}"
            + (f" · LL(sim−market) CI90 "
               f"{s.get('ll_diff_sim_minus_market_ci90')}"
               if s.get("market") else ""),
            f"- BTTS LL {s['sim']['ll_btts']:.5f} (ECE {s['ece_btts']}) · "
            f"O2.5 LL {s['sim']['ll_o25']:.5f} (ECE {s['ece_o25']})",
            f"- verdict: R1={v['R1_calibration']} R2={v['R2_shape']} "
            f"R3={v['R3_beats_prior']} R4={v['R4_conditional']} "
            f"(worst group |z| {v['worst_group_abs_z']})",
        ]
        lines.append("")
        lines.append("| group | n | " + " | ".join(
            f"{e} obs/pred (z)" for e in CONDITIONAL_EVENTS) + " |")
        lines.append("|---|---|" + "---|" * len(CONDITIONAL_EVENTS))
        for g in (w["groups"]["league"] + w["groups"]["pred_total_tercile"]
                  + w["groups"]["favourite_tercile"]):
            cells = " | ".join(
                f"{g[f'obs_{e}']:.3f}/{g[f'pred_{e}']:.3f} "
                f"({g[f'z_{e}']:+.1f})" for e in CONDITIONAL_EVENTS)
            lines.append(f"| {g['group']}={g['value']} | {g['n']} | {cells} |")
        h = w["halves"]
        lines.append("")
        if h.get("n"):
            lines.append(f"- half-time (coverage {h['coverage']:.0%}, "
                         f"n={h['n']}): obs 1H goals "
                         f"{h['obs']['mean_1h_goals']:.3f} vs pred "
                         f"{h['pred']['mean_1h_goals']:.3f}; HT H/D/A obs "
                         f"{h['obs']['ht_home_win']:.3f}/"
                         f"{h['obs']['ht_draw']:.3f}/"
                         f"{h['obs']['ht_away_win']:.3f} vs pred "
                         f"{h['pred']['ht_home_win']:.3f}/"
                         f"{h['pred']['ht_draw']:.3f}/"
                         f"{h['pred']['ht_away_win']:.3f} "
                         f"(z {h['z']['ht_home_win']:+.1f}/"
                         f"{h['z']['ht_draw']:+.1f}/"
                         f"{h['z']['ht_away_win']:+.1f}) — "
                         f"**{h['pred']['assumption']}**")
        else:
            lines.append(f"- half-time: {h.get('note', 'n/a')}")
        rf = w["data_facts"]["red_cards"]
        lines.append(f"- data fact (unmodelled): red cards in "
                     f"{rf['share_matches_with_red']:.1%} of matches; mean "
                     f"goals {rf['mean_goals_with_red']} with red vs "
                     f"{rf['mean_goals_no_red']} without")
        if w["n_unknown_team_rows"]:
            lines.append(f"- unknown-team rows mapped to league average: "
                         f"{w['n_unknown_team_rows']} "
                         f"({w['unknown_team_rate']:.1%})")
    if comparison:
        lines += ["", "## Comparison vs previous simulator version",
                  "", f"previous: `{comparison['previous_tag']}` "
                      f"({comparison['previous_generated_at']})", "",
                  "| window | ΔLL | ΔRPS | ΔW1 total | ΔW1 gd | Δpass |",
                  "|---|---|---|---|---|---|"]
        for k, row in comparison["deltas"].items():
            lines.append(f"| {k} | {row['ll_1x2']} | {row['rps_1x2']} | "
                         f"{row['w1_total']} | {row['w1_gd']} | {row['pass']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-glob", default=os.path.join(
        os.path.dirname(HERE), "data", "clean", "????_matches.csv"))
    ap.add_argument("--recipe", default=os.path.join(
        os.path.dirname(HERE), "data", "sim", "worlds.json"))
    ap.add_argument("--tag", default="v0")
    ap.add_argument("--windows", type=int, default=4)
    ap.add_argument("--holdout-days", type=int, default=180)
    ap.add_argument("--epochs", type=int, default=240)
    ap.add_argument("--capdown-worlds", type=int, default=0)
    ap.add_argument("--capdown-steps", type=int, default=300)
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261028)
    ap.add_argument("--min-train", type=int, default=5000)
    ap.add_argument("--min-eval", type=int, default=100)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when any window fails (CI gate)")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "reports"))
    args = ap.parse_args()

    t0 = time.time()
    df = load_frame(args.data_glob)
    print(f"loaded {len(df):,} production-aligned rows "
          f"({df['date'].min().date()} .. {df['date'].max().date()})", flush=True)
    windows = run_windows(
        df, holdout_days=args.holdout_days, n_windows=args.windows,
        recipe_path=args.recipe if os.path.exists(args.recipe) else None,
        epochs=args.epochs, capdown_worlds=args.capdown_worlds,
        capdown_steps=args.capdown_steps, seed=args.seed, n_boot=args.n_boot,
        min_train=args.min_train, min_eval=args.min_eval)
    report = build_report(
        tag=args.tag, windows=windows, df_len=len(df),
        holdout_days=args.holdout_days, seed=args.seed, epochs=args.epochs,
        capdown_worlds=args.capdown_worlds, capdown_steps=args.capdown_steps,
        recipe_path=args.recipe if os.path.exists(args.recipe) else None,
        duration_s=time.time() - t0)

    prev_path = find_previous(args.out_dir, args.tag)
    comparison = None
    if prev_path:
        with open(prev_path, encoding="utf-8") as fh:
            comparison = compare_reports(json.load(fh), report)

    os.makedirs(args.out_dir, exist_ok=True)
    jpath = os.path.join(args.out_dir, f"realism_{args.tag}.json")
    with open(jpath, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    mpath = os.path.join(args.out_dir, f"realism_{args.tag}.md")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write(render_md(report, comparison))
    print(f"wrote {jpath} and {mpath} "
          f"({report['verdict']['passed']}/{report['verdict']['n_windows']} "
          f"windows pass)", flush=True)
    if args.strict and not report["verdict"]["all_windows_pass"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

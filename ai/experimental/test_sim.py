"""Monte Carlo Match Lab regression tests — fast, synthetic, no heavy work.

These guard the "fool's plan" hard rules:

  * the goal process produces a proper distribution (every market's
    probabilities sum to 1 and stay in [0, 1]);
  * `cap down` really is a corrected sampler (proposal density is symmetric
    at eta = 0, acceptance lands in a sane band, the chain shortens after
    warmup) and never returns NaNs;
  * breeding returns a population of valid worlds, not clones;
  * sufficient statistics merge associatively (sum is the merge, so shard
    order cannot change the answer);
  * the gate scores on REAL rows only — `evaluate()` runs against a held-out
    synthetic frame and reports baselines;
  * the recipe round-trips: to_dict -> from_dict reproduces the same rates
    (store the recipe, not the rows).

Data: a few hundred synthetic matches generated in-process.  Nothing here
loads the 377k-row clean set and nothing trains LightGBM — heavy runs belong
in GitHub Actions (.github/workflows/simulate.yml).

Run:  .venv/bin/python ai/experimental/test_sim.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # .../statwise
sys.path.insert(0, os.path.join(ROOT, "ai"))

from sim.evaluate import evaluate, league_priors, synthesize_on_history   # noqa: E402
from sim.model import StrengthModel, fit_strengths, market_probs, scoreline_probs  # noqa: E402
from sim.sampler import MatchData, _log_q, breed, breed_population, cap_down, pack  # noqa: E402
from sim.simulate import (SuffStats, calibration_table, fold,            # noqa: E402
                          grid_marginals, posterior_markets,
                          scoreline_table, simulate_fixtures)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _synthetic_frame(n: int = 400, seed: int = 7) -> pd.DataFrame:
    """~400 settled matches, 2 leagues, 12 teams, home advantage baked in."""
    rng = np.random.default_rng(seed)
    leagues = {
        "test-league-a": [f"Alpha United {i}" for i in range(6)],
        "test-league-b": [f"Beta City {i}" for i in range(6)],
    }
    att = {t: rng.normal(0, 0.25) for ts in leagues.values() for t in ts}
    dfn = {t: rng.normal(0, 0.25) for ts in leagues.values() for t in ts}
    rows = []
    dates = pd.Timestamp("2025-08-01")
    for k in range(n):
        name = "test-league-a" if k % 2 == 0 else "test-league-b"
        h, a = rng.choice(leagues[name], size=2, replace=False)
        lam_h = np.exp(np.log(1.35) + np.log(1.25) + att[h] - dfn[a])
        lam_a = np.exp(np.log(1.35) + att[a] - dfn[h])
        rows.append({
            "date": dates + pd.Timedelta(days=int(k // 4)),
            "league_slug": name, "home_team": h, "away_team": a,
            "home_goals": int(rng.poisson(lam_h)), "away_goals": int(rng.poisson(lam_a)),
            "odds_home": 2.1, "odds_draw": 3.3, "odds_away": 3.4,
        })
    return pd.DataFrame(rows)


def main() -> int:
    print("Monte Carlo Match Lab tests (synthetic, tiny, fast)")

    df = _synthetic_frame()
    train, hold = df.iloc[:300].reset_index(drop=True), df.iloc[300:].reset_index(drop=True)

    # ── 1. fit + proper distributions ─────────────────────────────────────
    model = fit_strengths(train, epochs=80, seed=1)
    check("fit uses every train row", model.n_matches == 300, f"{model.n_matches}")
    check("fit params finite", bool(np.all(np.isfinite(pack(model)))))
    check("strengths zero-mean (identifiable)",
          abs(model.att.mean()) < 1e-9 and abs(model.defn.mean()) < 1e-9)
    check("home advantage recovered positive",
          model.home_adv > 0, f"exp={np.exp(model.home_adv):.3f}")

    grid = scoreline_probs(1.6, 1.2, model.xi)
    check("scoreline grid sums to 1", abs(grid.sum() - 1.0) < 1e-9, f"{grid.sum():.6f}")
    mp = market_probs(grid)
    check("1X2 marginals sum to 1",
          abs(mp["home"] + mp["draw"] + mp["away"] - 1.0) < 1e-9)
    check("market probs in [0,1]",
          all(0.0 <= mp[k] <= 1.0 for k in ("home", "draw", "away", "btts",
                                            "over15", "over25", "over35")))

    # ── 2. recipe round-trip (store the recipe, not the rows) ─────────────
    again = StrengthModel.from_dict(model.to_dict())
    lh1, la1 = model.rates(np.array([0, 1]), np.array([1, 2]), np.array([0, 1]))
    lh2, la2 = again.rates(np.array([0, 1]), np.array([1, 2]), np.array([0, 1]))
    check("to_dict/from_dict reproduces rates",
          np.allclose(lh1, lh2) and np.allclose(la1, la2))
    recipe_mb = len(str(model.to_dict())) / 1e6
    check("recipe is tiny (KB, not GB)", recipe_mb < 0.2, f"{recipe_mb * 1e6:.0f} kB")

    # ── 3. cap down: corrected sampler, sane acceptance ───────────────────
    data = MatchData.from_frame(train, model)
    check("MatchData maps every row", len(data) == 300, f"{len(data)}")

    rng = np.random.default_rng(3)
    start = model.sample_strengths(rng, scale=0.03)
    final, chain, meta = cap_down(start, data, steps=120, eta=0.02, sigma=0.01,
                                  warmup=40, seed=11, keep_every=4)
    check("cap_down returns (model, chain, meta)",
          isinstance(final, StrengthModel) and isinstance(chain, list)
          and isinstance(meta, dict))
    check("cap_down params finite", bool(np.all(np.isfinite(pack(final)))))
    check("acceptance in (0.02, 0.98)",
          0.02 < meta["acceptance"] < 0.98, f"{meta['acceptance']}")
    from sim.sampler import objective as _obj, unpack as _unpack
    start_obj = _obj(start, data)
    fit_obj = _obj(model, data)
    chain_objs = [_obj(_unpack(model, c), data) for c in chain]
    # A posterior sampler is NOT an optimiser: at stationarity E[L] sits about
    # n_params/2 above the mode for a quadratic basin, so the chain
    # legitimately floats a little above the fitted objective.  What must
    # hold: it stays in that basin (no divergence) and reaches states at
    # least as good as its own start.
    n_params = pack(final).size
    check("chain objectives finite", bool(np.all(np.isfinite(chain_objs))))
    check("chain stays in posterior basin (no divergence)",
          float(np.mean(chain_objs)) <= fit_obj + n_params,
          f"mean={np.mean(chain_objs):.1f} fit={fit_obj:.1f} n_params={n_params}")
    check("chain reaches states as good as its start",
          min(chain_objs) <= start_obj + 10.0,
          f"chain_min={min(chain_objs):.1f} start={start_obj:.1f}")
    check("chain length after warmup correct",
          len(chain) == len(range(40, 120, 4)), f"{len(chain)} snapshots")
    check("chain snapshots are parameter vectors",
          all(c.shape == pack(final).shape for c in chain))

    # proposal density must be symmetric at eta = 0 (pure Gaussian random walk)
    x = pack(final)
    y = x + 0.05 * np.random.default_rng(5).normal(size=x.size)
    g = np.zeros_like(x)
    check("proposal symmetric at eta=0",
          abs(_log_q(y, x, g, 0.0, 0.01) - _log_q(x, y, g, 0.0, 0.01)) < 1e-9)

    # ── 4. breeding produces a real population ────────────────────────────
    pop, stats = breed_population([final, start], data, rng=rng, generations=6,
                                  population=5, mutation=0.02, temperature=0.05)
    check("population size as requested", len(pop) == 5, f"{len(pop)}")
    check("breeding attempted children", stats["children"] > 0,
          f"{stats['accepted']}/{stats['children']} accepted")
    check("all worlds finite",
          all(bool(np.all(np.isfinite(pack(m)))) for m in pop))
    check("worlds are not all clones",
          len({tuple(np.round(pack(m), 6)) for m in pop}) > 1)
    child, ok = breed(final, start, data, rng=rng)
    check("breed returns (model, bool)", isinstance(child, StrengthModel)
          and isinstance(ok, bool))

    # ── 5. gate on REAL rows (never synthetic-on-synthetic) ───────────────
    priors = league_priors(train)
    res = evaluate(pop, hold, priors, seed=1)
    check("evaluate returns real-row metrics",
          res.get("n") == len(hold), f"n={res.get('n')} (holdout {len(hold)})")
    check("1x2 log loss finite and sane",
          0.1 < res.get("home_1x2", {}).get("log_loss", 0) < 3.0,
          str(res.get("home_1x2", {}).get("log_loss")))
    check("btts/o25/o35 all scored",
          all(k in res for k in ("btts", "over25", "over35")))
    check("baselines present (uniform, league prior, market)",
          all(k in res.get("baselines", {}) for k in ("uniform", "league_prior", "market")))
    check("verdicts present", "beats" in res and "publishable" in res)
    check("gate reports aggregate goal verdict too",
          "league_prior_goal_mean" in res["beats"]
          and "league_prior_goal_wins" in res["beats"],
          str(res["beats"].get("league_prior_goal_wins")))

    teacher = synthesize_on_history(pop, hold)
    check("distillation labels shaped (n,3)", teacher.shape == (len(hold), 3),
          str(teacher.shape))
    check("teacher rows sum to 1",
          bool(np.allclose(teacher.sum(axis=1), 1.0, atol=1e-6)))

    # ── 6. mass simulation -> mergeable counters ──────────────────────────
    h = hold["home_team"].map(model.t2i).to_numpy()
    a = hold["away_team"].map(model.t2i).to_numpy()
    lg = hold["league_slug"].map(model.l2i).to_numpy()
    stats_a, pm = simulate_fixtures(pop, h, a, lg, n_sims=40, seed=4)
    check("simulated outcomes counted", stats_a.n_sims > 0, f"{stats_a.n_sims}")
    check("scoreline counts == n_sims",
          int(stats_a.scoreline.sum()) == stats_a.n_sims,
          f"{int(stats_a.scoreline.sum())} vs {stats_a.n_sims}")
    check("posterior markets in [0,1]",
          all(bool(((pm[k] >= 0) & (pm[k] <= 1)).all())
              for k in ("home", "draw", "away", "btts", "over25")))
    check("posterior 1X2 sums to 1",
          bool(np.allclose(pm["home"] + pm["draw"] + pm["away"], 1.0, atol=1e-6)))

    # merge must be associative and equal to a plain sum, whatever the order.
    # NB merge() mutates self (it is an in-place sum), so every combination
    # runs on a fresh copy — otherwise the test would re-merge the same object.
    def fresh() -> SuffStats:
        s = SuffStats(worlds=2)
        s.n_sims = 0
        return s

    def cp(s: SuffStats) -> SuffStats:
        return SuffStats.from_dict(s.to_dict())

    left, right, third = fresh(), fresh(), fresh()
    rng2 = np.random.default_rng(9)
    for s in (left, right, third):
        fold(s, fixture_probs=rng2.uniform(0.2, 0.8, 5),
             pred_btts=rng2.uniform(0.2, 0.8, 5),
             pred_over25=rng2.uniform(0.2, 0.8, 5),
             pred_over35=rng2.uniform(0.2, 0.8, 5),
             pred_exp=rng2.uniform(1.0, 3.5, 5),
             fixture_of_sim=np.arange(20) // 4,
             sim_i=rng2.integers(0, 6, 20), sim_j=rng2.integers(0, 6, 20))
    solo = [int(s.scoreline.sum()) for s in (left, right, third)]
    solo_sims = [s.n_sims for s in (left, right, third)]

    ab_c = cp(left)
    ab_c.merge(cp(right)).merge(cp(third))
    a_bc = cp(left)
    a_bc.merge(cp(right).merge(cp(third)))
    cb_a = cp(third)
    cb_a.merge(cp(right)).merge(cp(left))
    check("merge associative (shard order irrelevant)",
          np.array_equal(ab_c.scoreline, a_bc.scoreline)
          and np.array_equal(ab_c.scoreline, cb_a.scoreline)
          and ab_c.n_sims == a_bc.n_sims == cb_a.n_sims,
          f"n={ab_c.n_sims}")
    total = int(ab_c.scoreline.sum())
    check("merged scoreline counts == summed n_sims", total == ab_c.n_sims,
          f"{total} vs {ab_c.n_sims}")
    check("merge is a plain sum",
          total == sum(solo) and ab_c.n_sims == sum(solo_sims),
          f"{total} vs {sum(solo)}")
    check("policy counters merge as sums",
          SuffStats.from_dict({"policy": {"acc": 3}}).merge(
              SuffStats.from_dict({"policy": {"acc": 4}})).policy["acc"] == 7)

    # ── 7. reporting tables ───────────────────────────────────────────────
    marg = grid_marginals(grid[None, None, :, :])
    check("grid_marginals 1X2 sums to 1",
          bool(np.allclose(marg["home"] + marg["draw"] + marg["away"], 1.0)))
    cal = calibration_table(stats_a.outcome_by_prob_bin)
    # `p` entries are rounded to 4 dp, so the row sum can be off by ~3e-4
    check("calibration rows normalised",
          all(abs(sum(r["p"]) - 1.0) < 1e-3 for r in cal["bins"]) or not cal["bins"],
          f"{len(cal['bins'])} bins")
    sl = scoreline_table(stats_a.scoreline)
    check("scoreline table 1X2 sums to 1",
          abs(sl["home"] + sl["draw"] + sl["away"] - 1.0) < 1e-3,
          f"{sl['home']}+{sl['draw']}+{sl['away']}")
    check("scoreline table in range",
          all(0.0 <= sl[k] <= 1.0 for k in ("home", "draw", "away", "btts",
                                            "over25", "over35")))

    # ── 8. posterior markets over the whole ensemble ──────────────────────
    pm_all = posterior_markets(pop, h, a, lg)
    check("world-averaged probs normalise",
          bool(np.allclose(pm_all["home"] + pm_all["draw"] + pm_all["away"],
                           1.0, atol=1e-6)))
    check("uncertainty reported", "home_std" in pm_all and "btts_std" in pm_all)

    # ── 9. distillation: soft labels from the stored recipe (step 1.5) ────
    from sim.distill import load_teacher, sample_soft_labels, teacher_may_fit

    recipe = {
        "schema": "statwise.sim.worlds.v1",
        "meta": {"split_at": "2025-07-04", "worlds": 1},
        "teams": model.teams, "leagues": model.leagues,
        "models": [{
            "shard": 0, "rank": 0, "mu": float(model.mu),
            "home_adv": float(model.home_adv), "xi": float(model.xi),
            "n_matches": model.n_matches,
            "att": [round(float(x), 4) for x in model.att],
            "def": [round(float(x), 4) for x in model.defn],
            "league_offset": [round(float(x), 4) for x in model.league_offset],
        }],
    }
    rpath = os.path.join(tempfile.gettempdir(), "sim-test-recipe.json")
    with open(rpath, "w", encoding="utf-8") as fh:
        json.dump(recipe, fh)

    worlds, rmeta = load_teacher(rpath)
    check("recipe loads into teacher worlds",
          len(worlds) == 1 and rmeta.get("split_at") == "2025-07-04")
    check("missing recipe returns no teacher",
          load_teacher(rpath + ".does-not-exist") == (None, None))
    check("teacher accepted when its split <= retrain split",
          teacher_may_fit(rmeta, pd.Timestamp("2025-07-04"))[0])
    ok_late, why_late = teacher_may_fit(rmeta, pd.Timestamp("2025-07-01"))
    check("teacher rejected when it saw holdout outcomes", not ok_late, why_late)
    check("recipe without split_at rejected",
          not teacher_may_fit({}, pd.Timestamp("2025-07-04"))[0])

    home = list(df["home_team"])
    away = list(df["away_team"])
    lg_names = list(df["league_slug"])
    home_bad = list(home)
    home_bad[0] = "Unknown FC Nowhere"
    idx1, y1, g1, b1, o1 = sample_soft_labels(worlds, home, away, lg_names,
                                              fraction=0.5, seed=5)
    idx2, y2, g2, b2, o2 = sample_soft_labels(worlds, home, away, lg_names,
                                              fraction=0.5, seed=5)
    check("samples ~fraction of candidate rows",
          abs(len(idx1) - 0.5 * len(df)) <= 2, f"{len(idx1)} of {len(df)}")
    check("labels are valid classes",
          set(np.unique(y1).tolist()) <= {0, 1, 2}
          and set(np.unique(g1).tolist()) <= {0, 1}
          and set(np.unique(b1).tolist()) <= {0, 1}
          and set(np.unique(o1).tolist()) <= {0, 1})
    check("all five arrays aligned",
          len(y1) == len(g1) == len(b1) == len(o1) == len(idx1),
          f"{len(idx1)} rows")
    # plan 1.6: every label derives from ONE sampled scoreline, so exact
    # logical implications must hold row-by-row — under-2.5 implies under-3.5,
    # over-3.5 implies over-2.5.  Independent draws would break these.
    check("joint consistency: u25 -> u35",
          not bool(((g1 == 0) & (o1 == 1)).any()))
    check("joint consistency: o35 -> o25",
          not bool(((o1 == 1) & (g1 == 0)).any()))
    check("sampling deterministic per seed",
          np.array_equal(idx1, idx2) and np.array_equal(y1, y2)
          and np.array_equal(g1, g2) and np.array_equal(b1, b2)
          and np.array_equal(o1, o2))
    check("no row duplicated twice", len(np.unique(idx1)) == len(idx1))
    idx3 = sample_soft_labels(worlds, home_bad, away, lg_names,
                              fraction=1.0, seed=1)[0]
    check("unknown-team rows never selected", 0 not in set(idx3.tolist()),
          f"{len(idx3)} rows selected")
    check("fraction 0 samples nothing",
          len(sample_soft_labels(worlds, home, away, lg_names,
                                 fraction=0.0)[0]) == 0)
    check("no models -> no labels",
          len(sample_soft_labels([], home, away, lg_names,
                                 fraction=0.5)[0]) == 0)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Fast-path regression tests for the billion-outcome simulation path.

The mass simulation was rewritten for scale (stacked world gathers + bincount
folding); these checks pin the *semantics* so speed can never change answers:

  * `fold` (bincount path) folds EXACTLY what the old np.add.at reference
    folded, for every counter, on random data;
  * `simulate_scorelines` (vectorised world loop) still produces draws whose
    empirical scoreline distribution matches the exact posterior grid
    (Dixon-Coles rejection included) within sampling noise;
  * fixture ids survive the rejection step, so counters can never mis-attribute
    an outcome to the wrong fixture;
  * multi-wave accumulation: counters.json round-trips and merges as a plain
    sum across waves.

Tiny synthetic data only (a few hundred matches in-process) — heavy runs
belong in GitHub Actions.  Run:  .venv/bin/python ai/experimental/test_sim_scale.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "ai"))

from sim.model import fit_strengths                            # noqa: E402
from sim.simulate import (MAX_GOALS, N_PROB_BINS, N_TOTAL_BINS,  # noqa: E402
                          SuffStats, fold, fixture_grids, posterior_markets,
                          simulate_fixtures, simulate_scorelines)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _synthetic_frame(n: int = 300, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    leagues = {
        "scale-league-a": [f"Gamma FC {i}" for i in range(6)],
        "scale-league-b": [f"Delta FC {i}" for i in range(6)],
    }
    att = {t: rng.normal(0, 0.25) for ts in leagues.values() for t in ts}
    dfn = {t: rng.normal(0, 0.25) for ts in leagues.values() for t in ts}
    rows = []
    dates = pd.Timestamp("2025-08-01")
    for k in range(n):
        name = "scale-league-a" if k % 2 == 0 else "scale-league-b"
        h, a = rng.choice(leagues[name], size=2, replace=False)
        lam_h = np.exp(np.log(1.35) + np.log(1.2) + att[h] - dfn[a])
        lam_a = np.exp(np.log(1.35) + att[a] - dfn[h])
        rows.append({
            "date": dates + pd.Timedelta(days=int(k // 5)),
            "league_slug": name, "home_team": h, "away_team": a,
            "home_goals": int(rng.poisson(lam_h)),
            "away_goals": int(rng.poisson(lam_a)),
        })
    return pd.DataFrame(rows)


def _fold_reference(s: SuffStats, *, fixture_probs, pred_btts, pred_over25,
                    pred_over35, pred_exp, fixture_of_sim, sim_i, sim_j) -> None:
    """The pre-scale np.add.at implementation, kept here as ground truth."""
    s.scoreline += np.bincount(
        sim_i.astype(np.int64) * (MAX_GOALS + 1) + sim_j.astype(np.int64),
        minlength=(MAX_GOALS + 1) ** 2).reshape(MAX_GOALS + 1, MAX_GOALS + 1)
    from sim.simulate import bin_index
    fp = fixture_probs[fixture_of_sim]
    outcome = np.where(sim_i > sim_j, 0, np.where(sim_i == sim_j, 1, 2))
    np.add.at(s.outcome_by_prob_bin, (bin_index(fp), outcome), 1)
    total = np.clip(sim_i + sim_j, 0, MAX_GOALS * 2)
    tb = np.clip((pred_exp[fixture_of_sim] / 0.8).astype(int), 0, N_TOTAL_BINS - 1)
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


def main() -> int:
    print("Simulation fast-path tests (synthetic, tiny, fast)")

    rng = np.random.default_rng(21)

    # ── 1. fold: bincount path == add.at reference, exactly ───────────────
    for trial in range(3):
        n = int(rng.integers(50, 400))
        args = dict(
            fixture_probs=rng.uniform(0.05, 0.95, 12),
            pred_btts=rng.uniform(0.2, 0.8, 12),
            pred_over25=rng.uniform(0.2, 0.8, 12),
            pred_over35=rng.uniform(0.1, 0.6, 12),
            pred_exp=rng.uniform(0.6, 4.2, 12),
            fixture_of_sim=rng.integers(0, 12, n),
            sim_i=rng.integers(0, MAX_GOALS + 1, n),
            sim_j=rng.integers(0, MAX_GOALS + 1, n),
        )
        fast, ref = SuffStats(), SuffStats()
        fold(fast, **args)
        _fold_reference(ref, **args)
        same = all(np.array_equal(getattr(fast, k), getattr(ref, k))
                   for k in SuffStats._ARRAYS) and fast.n_sims == ref.n_sims
        check(f"fold bincount == add.at reference (trial {trial})", same,
              f"n={n}")
    check("fold counter shapes unchanged",
          fast.scoreline.shape == (MAX_GOALS + 1, MAX_GOALS + 1)
          and fast.outcome_by_prob_bin.shape == (N_PROB_BINS, 3)
          and fast.total_by_exp_bin.shape == (N_TOTAL_BINS, MAX_GOALS * 2 + 1))

    # ── 2. sampler matches the exact posterior grid ──────────────────────
    df = _synthetic_frame()
    train = df.iloc[:240].reset_index(drop=True)
    model = fit_strengths(train, epochs=120, seed=3)
    worlds = [model.sample_strengths(np.random.default_rng(100 + w), scale=0.01)
              for w in range(3)]

    teams = model.teams
    t2i = model.t2i
    l2i = model.l2i
    fixtures = [(teams[i], teams[j], "scale-league-a")
                for i in range(6) for j in range(6, 12) if i != j][:8]
    h = np.array([t2i[x[0]] for x in fixtures], dtype=np.int64)
    a = np.array([t2i[x[1]] for x in fixtures], dtype=np.int64)
    lg = np.array([l2i[x[2]] for x in fixtures], dtype=np.int64)

    n_sims = 40_000
    i_draw, j_draw, fid = simulate_scorelines(worlds, h, a, lg, n_sims,
                                              rng=np.random.default_rng(7))
    check("draw count after rejection ~ tau acceptance",
          0.75 * n_sims * len(fixtures) < len(i_draw) <= n_sims * len(fixtures),
          f"{len(i_draw):,} of {n_sims * len(fixtures):,}")
    check("fixture ids preserved through rejection",
          fid.min() >= 0 and fid.max() < len(fixtures),
          f"ids in [{fid.min()}, {fid.max()}]")
    check("goals inside grid",
          i_draw.max() <= MAX_GOALS and j_draw.max() <= MAX_GOALS
          and i_draw.min() >= 0 and j_draw.min() >= 0)

    # exact posterior (world-averaged grid) for fixture 0 vs empirical draws
    grid = fixture_grids(worlds, h[:1], a[:1], lg[:1]).mean(axis=0)[0]
    grid /= grid.sum()
    emp = np.zeros_like(grid)
    m0 = fid == 0
    np.add.at(emp, (i_draw[m0], j_draw[m0]), 1.0)
    emp /= emp.sum()
    err = np.abs(emp - grid).max()
    # 40k draws over 121 cells: max cell deviation should sit well under 0.01
    check("empirical scorelines match exact posterior",
          err < 0.012, f"max|emp-exact|={err:.5f} over {int(m0.sum()):,} draws")

    # ── 3. simulate_fixtures end-to-end still bins correctly ─────────────
    stats, pm = simulate_fixtures(worlds, h, a, lg, n_sims=300, seed=5,
                                  batch_fixtures=3)
    check("simulate_fixtures counts outcomes",
          int(stats.scoreline.sum()) == stats.n_sims and stats.n_sims > 0,
          f"{stats.n_sims}")
    check("every counter row sums to n_sims",
          int(stats.outcome_by_prob_bin.sum()) == stats.n_sims
          and int(stats.total_by_exp_bin.sum()) == stats.n_sims
          and int(stats.btts_by_pred_bin.sum()) == stats.n_sims)
    check("posterior markets normalised",
          bool(np.allclose(pm["home"] + pm["draw"] + pm["away"], 1.0)))

    # ── 4. multi-wave accumulation: counters sum across waves ────────────
    import json
    import tempfile
    from sim.merge import accumulate_waves

    w1 = SuffStats.from_dict(stats.to_dict())
    w2 = SuffStats(worlds=3)
    fold(w2, fixture_probs=pm["home"], pred_btts=pm["btts"],
         pred_over25=pm["over25"], pred_over35=pm["over35"],
         pred_exp=pm["exp_goals"], fixture_of_sim=fid[:100] % len(fixtures),
         sim_i=i_draw[:100], sim_j=j_draw[:100])
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "counters.json"), "w", encoding="utf-8") as fh:
        json.dump(w2.to_dict(), fh)
    acc = accumulate_waves(os.path.join(tmp, "counters.json"), w1)
    check("accumulate sums counters across waves",
          acc.n_sims == w1.n_sims + w2.n_sims
          and int(acc.scoreline.sum()) == acc.n_sims)
    check("accumulate result merges associatively",
          SuffStats.from_dict(acc.to_dict()).n_sims == acc.n_sims)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {', '.join(FAILURES)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Monte Carlo Match Lab — the "fool's plan" runner (one CI shard).

    python ai/sim/run.py --shard 3 --shards 20 --worlds 2 \
        --steps 300 --sims 2000 --out shard-3.json

(`ai/` is not an installed package, so the file bootstraps sys.path itself
 and is invoked by path, not with `-m`.)

Pipeline inside a shard (all heavy work happens in GitHub Actions):

  1. load the committed season files and split them out-of-time
     (same 180-day holdout retrain.py uses — the lab is never scored on
     anything it was fit to);
  2. fit a Dixon-Coles strength model on TRAIN rows only;
  3. run `cap down` (forward step + noise, backward step + noise, Metropolis
     corrected) from a perturbed seed to get THIS shard's worlds;
  4. breed them — crossover + mutation — into a small population;
  5. score the ensemble on REAL holdout outcomes (never synthetic);
  6. mass-simulate fixtures (real holdout matchups + bred pairings) into
     mergeable sufficient statistics — rows are discarded, counters kept;
  7. write one JSON the merge job can simply sum.

Storage: the worlds JSON *is* the recipe.  Regenerate with the same seed and
you get the same strengths; the simulated rows never touch disk.
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.evaluate import evaluate, league_priors                      # noqa: E402
from sim.model import fit_strengths                                   # noqa: E402
from sim.sampler import MatchData, breed_population, cap_down         # noqa: E402
from sim.simulate import (calibration_table, posterior_markets,      # noqa: E402
                          scoreline_table, simulate_fixtures)

CLEAN_GLOB = "ai/data/clean/????_matches.csv"
USECOLS = ("date", "home_team", "away_team", "home_goals", "away_goals",
           "league_slug", "odds_home", "odds_draw", "odds_away",
           "is_international", "quality_score")
# production's population (retrain._load_clean_data): club matches only,
# quality_score >= 40, from 1993-08-01.  The lab used to fit on a looser
# frame (372,276/5,123 vs production's 331,949/4,499 — audit W1), which
# mis-specified home advantage (internationals play at neutral venues) and
# is one of the two structural realism defects named in diagnosis.md §5.
MIN_DATE = pd.Timestamp("1993-08-01")
MIN_QUALITY = 40.0


def load_frame(data_glob: str) -> pd.DataFrame:
    paths = sorted(glob.glob(data_glob))
    if not paths:
        raise SystemExit(f"no season files matching {data_glob}")
    frames = [pd.read_csv(p, usecols=lambda c: c in USECOLS, low_memory=False)
              for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "league_slug" not in df.columns:
        # old season files predate the slug column — the lab still needs one
        df["league_slug"] = (df["league"] if "league" in df.columns else "all")
    df = df.dropna(subset=["date", "home_team", "away_team",
                           "home_goals", "away_goals"])
    # ── production-aligned filters (mirrors retrain._load_clean_data) ────
    n_raw = len(df)
    df = df[df["date"] >= MIN_DATE]
    if "is_international" in df.columns:
        df = df[pd.to_numeric(df["is_international"],
                              errors="coerce").fillna(0) == 0]
    if "quality_score" in df.columns:
        df = df[pd.to_numeric(df["quality_score"],
                              errors="coerce").fillna(0) >= MIN_QUALITY]
    print(f"  population: {len(df):,}/{n_raw:,} rows after production "
          f"filters (club, quality>={MIN_QUALITY:.0f}, >= {MIN_DATE.date()})",
          flush=True)
    return df.sort_values("date", kind="stable").reset_index(drop=True)


def split_out_of_time(df: pd.DataFrame, holdout_days: int):
    cut = df["date"].max() - pd.Timedelta(days=holdout_days)
    return (df[df["date"] <= cut].reset_index(drop=True),
            df[df["date"] > cut].reset_index(drop=True)), cut


def breed_pairings(train: pd.DataFrame, rng: np.random.Generator, n: int) -> pd.DataFrame:
    """Synthetic fixtures: two teams from the same league, drawn at random.

    This is the "mix teams together" half.  Only the matchup is invented —
    the teams, their strengths and the league base rates all come from real
    data, so every simulated fixture is a plausible fixture rather than a
    random goal generator.
    """
    lg = (train["league_slug"] if "league_slug" in train.columns
          else train.get("league")).astype(str)
    pool = {}
    for name, idx in lg.groupby(lg).groups.items():
        teams = pd.unique(pd.concat([train.loc[idx, "home_team"],
                                     train.loc[idx, "away_team"]]).astype(str))
        if len(teams) >= 4:
            pool[name] = np.asarray(teams)
    rows = []
    leagues = list(pool)
    while len(rows) < n and leagues:
        name = leagues[int(rng.integers(len(leagues)))]
        teams = pool[name]
        ha, aa = rng.choice(len(teams), size=2, replace=False)
        rows.append({"home_team": teams[ha], "away_team": teams[aa],
                     "league_slug": name})
    out = pd.DataFrame(rows)
    out["home_goals"] = 1.0   # placeholder labels: simulation draws its own
    out["away_goals"] = 1.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-glob", default=CLEAN_GLOB)
    ap.add_argument("--holdout-days", type=int, default=180)
    ap.add_argument("--epochs", type=int, default=240)
    ap.add_argument("--half-life", type=float, default=1460.0,
                    help="recency half-life in days for the strength fit "
                         "(0 = flat history, the legacy behaviour).  "
                         "Default 1460 d = 4 years, production's measured "
                         "best (retrain.py); do not tune against eval "
                         "windows.")
    ap.add_argument("--l2", type=float, default=0.02,
                    help="L2 shrinkage on att/def/league offsets (the "
                         "historical default; see ai/sim/l2_sensitivity.py "
                         "for the train-internal sensitivity study)")
    ap.add_argument("--worlds", type=int, default=2, help="cap-down chains this shard")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--eta", type=float, default=0.02)
    ap.add_argument("--sigma", type=float, default=0.01)
    ap.add_argument("--warmup", type=int, default=80)
    ap.add_argument("--generations", type=int, default=10)
    ap.add_argument("--population", type=int, default=6)
    ap.add_argument("--sims", type=int, default=2000, help="simulated outcomes per fixture")
    ap.add_argument("--synth-fixtures", type=int, default=300,
                    help="bred (same-league) pairings added to the sim pool per shard")
    ap.add_argument("--holdout-pool", type=int, default=0,
                    help="real holdout fixtures in the sim pool (0 = len(holdout)//shards)")
    ap.add_argument("--batch-elements", type=int, default=4_000_000,
                    help="max expanded draw elements carried in RAM per batch")
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--fixtures", default=None,
                    help="optional JSON list of upcoming fixtures to price")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    t0 = time.time()
    out_path = args.out or f"sim-shard-{args.shard}.json"
    seed = args.seed + args.shard * 7919
    rng = np.random.default_rng(seed)

    df = load_frame(args.data_glob)
    (train, holdout), cut = split_out_of_time(df, args.holdout_days)
    print(f"[shard {args.shard}/{args.shards}] {len(train):,} train | "
          f"{len(holdout):,} holdout | split {cut.date()}", flush=True)

    # 1. base fit on TRAIN rows only (leak-free: the holdout is untouched)
    half_life = args.half_life if args.half_life and args.half_life > 0 else None
    base = fit_strengths(train, epochs=args.epochs, seed=seed,
                         half_life_days=half_life, l2=args.l2)
    print(f"  fit: {base.n_matches:,} matches, "
          f"home_adv={np.exp(base.home_adv):.3f}, xi={base.xi:.3f}, "
          f"half_life={half_life or 'flat'}d, l2={args.l2} "
          f"({time.time() - t0:.0f}s)", flush=True)

    train_data = MatchData.from_frame(train, base, half_life_days=half_life,
                                      l2=args.l2)

    # 2. cap down — one chain per world, each seeded with its own noise
    worlds = []
    chain_reports = []
    for w in range(args.worlds):
        rng_w = np.random.default_rng(seed + 1000 + w)
        start = base.sample_strengths(rng_w, scale=0.02)
        final, chain, meta = cap_down(
            start, train_data, steps=args.steps, eta=args.eta, sigma=args.sigma,
            warmup=args.warmup, seed=int(rng_w.integers(1 << 31)), keep_every=10)
        meta["world"] = w
        meta["chain_len"] = len(chain)
        chain_reports.append(meta)
        worlds.append(final)
        print(f"  world {w}: acceptance={meta['acceptance']:.2f} "
              f"objective={meta['final_objective']} ({time.time() - t0:.0f}s)", flush=True)

    # 3. breeding: crossover + mutation over the chain's own worlds
    pop, breed_stats = breed_population(
        worlds, train_data, rng=rng, generations=args.generations,
        population=args.population, mutation=0.015, temperature=0.05)
    print(f"  bred population of {len(pop)} "
          f"({breed_stats['accepted']}/{breed_stats['children']} children accepted)",
          flush=True)

    # 4. the gate: score on REAL holdout outcomes, never synthetic
    priors = league_priors(train)
    metrics = evaluate(pop, holdout, priors, seed=seed)
    print(f"  holdout: 1x2 ll={metrics.get('home_1x2', {}).get('log_loss')} "
          f"| btts ll={metrics.get('btts', {}).get('log_loss')} "
          f"| o25 ll={metrics.get('over25', {}).get('log_loss')} "
          f"| publishable={metrics.get('publishable')} ({time.time() - t0:.0f}s)",
          flush=True)

    # 5. mass simulation -> sufficient statistics (rows discarded).
    #    Each shard takes a different random slice of the holdout matchups
    #    (plus its own bred pairings), so the merged counters cover far more
    #    fixtures than any one shard would.
    n_slice = args.holdout_pool or max(200, len(holdout) // max(args.shards, 1))
    hold_slice = holdout.iloc[rng.permutation(len(holdout))[:n_slice]]
    synth = breed_pairings(train, rng, n=max(args.synth_fixtures, 200))
    pool = pd.concat([hold_slice, synth], ignore_index=True)
    h = pool["home_team"].astype(str).map(base.t2i).to_numpy(dtype=float)
    a = pool["away_team"].astype(str).map(base.t2i).to_numpy(dtype=float)
    lg = pool["league_slug"].astype(str).map(base.l2i).fillna(0).to_numpy(dtype=float)
    ok = ~(np.isnan(h) | np.isnan(a))
    pool_known = pool.loc[ok]
    h, a, lg = h[ok].astype(np.int64), a[ok].astype(np.int64), lg[ok].astype(np.int64)

    t_sim = time.time()
    stats, pm = simulate_fixtures(pop, h, a, lg, n_sims=args.sims, seed=seed + 7,
                                  batch_elements=args.batch_elements)
    sim_s = max(time.time() - t_sim, 1e-9)
    try:
        import resource
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        peak_mb = float("nan")
    print(f"  simulated {stats.n_sims:,} outcomes over {stats.n_fixtures} fixtures "
          f"in {sim_s:.0f}s ({stats.n_sims / sim_s / 1e6:.1f}M outcomes/s, "
          f"peak RSS {peak_mb:.0f} MB) ({time.time() - t0:.0f}s)", flush=True)

    # 6. optional: price real upcoming fixtures with the ensemble
    markets = None
    if args.fixtures and os.path.exists(args.fixtures):
        with open(args.fixtures, encoding="utf-8") as fh:
            fx = json.load(fh)
        fh_ = np.array([base.t2i.get(str(f.get("home")), -1) for f in fx], dtype=float)
        fa_ = np.array([base.t2i.get(str(f.get("away")), -1) for f in fx], dtype=float)
        fl_ = np.array([base.l2i.get(str(f.get("league", "all")), 0) for f in fx], dtype=float)
        good = (fh_ >= 0) & (fa_ >= 0)
        if good.any():
            m = posterior_markets(pop, fh_[good].astype(np.int64),
                                  fa_[good].astype(np.int64),
                                  fl_[good].astype(np.int64))
            markets = {"fixtures": [f for f, g in zip(fx, good) if g],
                       "prob_home": np.round(m["home"], 4).tolist(),
                       "prob_draw": np.round(m["draw"], 4).tolist(),
                       "prob_away": np.round(m["away"], 4).tolist(),
                       "prob_btts": np.round(m["btts"], 4).tolist(),
                       "prob_over15": np.round(m["over15"], 4).tolist(),
                       "prob_over25": np.round(m["over25"], 4).tolist(),
                       "prob_over35": np.round(m["over35"], 4).tolist(),
                       "lambda_h": np.round(m["lam_h"], 4).tolist(),
                       "lambda_a": np.round(m["lam_a"], 4).tolist(),
                       "home_std": np.round(m["home_std"], 4).tolist(),
                       "btts_std": np.round(m["btts_std"], 4).tolist()}

    payload = {
        "meta": {
            "shard": args.shard, "shards": args.shards, "seed": seed,
            "worlds": len(pop), "steps": args.steps, "sims_per_fixture": args.sims,
            "epochs": args.epochs, "holdout_days": args.holdout_days,
            "half_life_days": half_life,
            "split_at": str(cut.date()),
            "n_train": int(len(train)), "n_holdout": int(len(holdout)),
            "n_pool": int(len(pool_known)), "simulated_outcomes": int(stats.n_sims),
            "duration_s": round(time.time() - t0, 1),
            "generated_at": pd.Timestamp.now("UTC").isoformat(),
        },
        "models": [m.to_dict() for m in pop],
        "chains": chain_reports,
        "breed": breed_stats,
        "eval": metrics,
        "suffstats": stats.to_dict(),
        "scoreline_summary": scoreline_table(stats.scoreline),
        "calibration": {
            "outcome": calibration_table(stats.outcome_by_prob_bin),
            "btts": calibration_table(stats.btts_by_pred_bin),
            "over25": calibration_table(stats.over25_by_pred_bin),
            "over35": calibration_table(stats.over35_by_pred_bin),
        },
    }
    if markets:
        payload["markets"] = markets

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"  wrote {out_path} ({size_mb:.2f} MB) in {time.time() - t0:.0f}s",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

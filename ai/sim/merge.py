"""Merge shard payloads into the recipe + report — the only things we store.

    python ai/sim/merge.py --pattern 'sim-shard-*.json' --out-dir ai/data/sim

What a merge does, and deliberately does NOT do:

  * sufficient statistics are **summed** (merge is associative, so shard
    order cannot change any published number — test_sim.py proves it);
  * simulated scoreline ROWS are never collected — they never existed on
    disk, only their counters did;
  * the worlds from every shard become one `worlds.json` *recipe*: a shared
    team/league header plus per-world strength vectors, a few MB total and
    regenerable from (seed, data);
  * the combined ensemble is re-scored on the REAL out-of-time holdout with
    the same split the shards used — the gate is evaluated once, globally,
    not trusted from any single shard.

Exit code: 0 when the ensemble passes the publishable gate, 1 when it does
not (files are still written first, so a red job keeps its evidence).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim.evaluate import evaluate, league_priors          # noqa: E402
from sim.model import StrengthModel                       # noqa: E402
from sim.run import CLEAN_GLOB, load_frame, split_out_of_time  # noqa: E402
from sim.simulate import SuffStats, calibration_table, scoreline_table  # noqa: E402

RECIPE_SCHEMA = "statwise.sim.worlds.v1"
REPORT_SCHEMA = "statwise.sim.report.v3"   # v3: PROPER binary log losses (audit fix)


# ── loading ───────────────────────────────────────────────────────────────
def load_shards(pattern: str) -> list[dict]:
    paths = sorted(glob.glob(pattern))
    if not paths:
        raise SystemExit(f"no shard payloads matching {pattern}")
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            out.append(json.load(fh))
    out.sort(key=lambda d: d.get("meta", {}).get("shard", 0))
    return out


# ── multi-wave accumulation ───────────────────────────────────────────────
def load_counters(path: str) -> SuffStats | None:
    """Previous waves' merged counters, or None when there are none yet."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return SuffStats.from_dict(json.load(fh))


def accumulate_waves(prev_counters_path: str, new_stats: SuffStats) -> SuffStats:
    """Sum this wave's counters into the persisted ones (waves accumulate).

    Merge stays a plain element-wise sum, so accumulating wave-by-wave gives
    exactly the same counters as running every wave's shards at once —
    test_sim_scale.py proves the round-trip.
    """
    prev = load_counters(prev_counters_path)
    if prev is None:
        return new_stats
    prev.merge(new_stats)
    return prev


def accumulate_worlds(prev_recipe: dict | None, new_recipe: dict,
                      max_worlds: int) -> tuple[list[dict], int]:
    """Worlds from earlier waves, prepended to this wave's, capped at max.

    Old strength vectors are only meaningful against the SAME team/league
    header (they are positional), so a header change refuses accumulation
    loudly instead of silently mis-aligning every old att/def entry.
    """
    old = list((prev_recipe or {}).get("models") or [])
    if not old:
        return new_recipe["models"], 0
    if prev_recipe.get("teams") != new_recipe["teams"] or \
            prev_recipe.get("leagues") != new_recipe["leagues"]:
        raise SystemExit(
            "accumulate: the team/league universe changed between waves — "
            "old world vectors would mis-align. Rerun without --accumulate "
            "to start a fresh recipe.")
    keep = max(0, int(max_worlds) - len(new_recipe["models"]))
    kept = old[-keep:] if keep else []
    return kept + new_recipe["models"], len(kept)


def _check_consistency(shards: list[dict]) -> dict:
    """Every shard must have split the same data the same way."""
    keys = ("split_at", "n_train", "n_holdout", "holdout_days", "epochs")
    ref = shards[0]["meta"]
    for s in shards[1:]:
        for k in keys:
            if s["meta"].get(k) != ref.get(k):
                raise SystemExit(
                    f"shard {s['meta'].get('shard')} disagrees on {k}: "
                    f"{s['meta'].get(k)} != {ref.get(k)} — rerun all shards "
                    "with the same inputs before merging")
    # all shards fit on the identical team/league universe
    ref_t, ref_l = shards[0]["models"][0]["teams"], shards[0]["models"][0]["leagues"]
    for s in shards:
        for m in s["models"]:
            if m["teams"] != ref_t or m["leagues"] != ref_l:
                raise SystemExit("shards were built from different frames "
                                 "(team/league universe mismatch)")
    return ref


# ── recipe (the stored artifact) ──────────────────────────────────────────
def build_recipe(shards: list[dict], meta: dict) -> dict:
    """Shared header + every world's strengths, rounded to 4 dp.

    teams/leagues are written ONCE at the top: 2,208 team names repeated per
    world would triple the file for zero information.
    """
    head = shards[0]["models"][0]
    models = []
    for s in shards:
        shard_id = s["meta"].get("shard", 0)
        for rank, m in enumerate(s["models"]):
            models.append({
                "shard": shard_id, "rank": rank,
                "mu": round(float(m["mu"]), 6),
                "home_adv": round(float(m["home_adv"]), 6),
                "xi": round(float(m["xi"]), 6),
                "n_matches": int(m.get("n_matches", 0)),
                "att": [round(float(x), 4) for x in m["att"]],
                "def": [round(float(x), 4) for x in m["def"]],
                "league_offset": [round(float(x), 4) for x in m.get("league_offset", [])],
            })
    return {
        "schema": RECIPE_SCHEMA,
        "meta": meta,
        "teams": head["teams"],
        "leagues": head["leagues"],
        "models": models,
    }


def recipe_models(recipe: dict) -> list[StrengthModel]:
    """Rehydrate worlds from the stored recipe (this is the whole point)."""
    out = []
    for m in recipe["models"]:
        out.append(StrengthModel.from_dict({
            "teams": recipe["teams"], "leagues": recipe["leagues"],
            "att": m["att"], "def": m["def"], "mu": m["mu"],
            "league_offset": m.get("league_offset", []),
            "home_adv": m["home_adv"], "xi": m["xi"],
            "n_matches": m.get("n_matches", 0),
        }))
    return out


# ── merge ─────────────────────────────────────────────────────────────────
def merge_suffstats(shards: list[dict]) -> SuffStats:
    total = SuffStats.from_dict(shards[0]["suffstats"])
    for s in shards[1:]:
        total.merge(SuffStats.from_dict(s["suffstats"]))
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pattern", default="sim-shard-*.json")
    ap.add_argument("--out-dir", default="ai/data/sim")
    ap.add_argument("--data-glob", default=CLEAN_GLOB)
    ap.add_argument("--no-gate", action="store_true",
                    help="write the report but always exit 0")
    ap.add_argument("--skip-reval", action="store_true",
                    help="trust shard evals instead of re-scoring (debug only)")
    ap.add_argument("--accumulate", action="store_true",
                    help="sum this wave's counters into the persisted ones and "
                         "keep previous waves' worlds (multi-wave runs)")
    ap.add_argument("--max-worlds", type=int, default=240,
                    help="world cap when accumulating (newest waves kept)")
    args = ap.parse_args()
    t0 = time.time()

    shards = load_shards(args.pattern)
    ref = _check_consistency(shards)
    print(f"merged {len(shards)} shards | split {ref['split_at']} | "
          f"{ref['n_train']:,} train / {ref['n_holdout']:,} holdout", flush=True)

    # 1. counters: sum, never collect rows
    wave_stats = merge_suffstats(shards)
    stats = SuffStats.from_dict(wave_stats.to_dict())
    print(f"  this wave: {wave_stats.n_sims:,} simulated outcomes, "
          f"scoreline mass {int(wave_stats.scoreline.sum()):,}", flush=True)

    counters_path = os.path.join(args.out_dir, "counters.json")
    recipe_path = os.path.join(args.out_dir, "worlds.json")
    prev_recipe = None
    if args.accumulate:
        stats = accumulate_waves(counters_path, stats)
        if os.path.exists(recipe_path):
            with open(recipe_path, encoding="utf-8") as fh:
                prev_recipe = json.load(fh)
        print(f"  accumulated across waves: {stats.n_sims:,} outcomes "
              f"({len((prev_recipe or {}).get('models') or [])} previous worlds)",
              flush=True)

    # 2. recipe: shared header + all worlds
    from datetime import datetime, timezone
    recipe_meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": shards[0]["meta"].get("seed"),
        "shards": len(shards),
        "split_at": ref["split_at"],
        "holdout_days": ref["holdout_days"],
        "n_train": ref["n_train"],
        "n_holdout": ref["n_holdout"],
        "epochs": ref["epochs"],
        "simulated_outcomes": int(stats.n_sims),
        "wave_outcomes": int(wave_stats.n_sims),
    }
    recipe = build_recipe(shards, recipe_meta)
    n_prev_worlds = 0
    if args.accumulate and prev_recipe:
        models, n_prev_worlds = accumulate_worlds(prev_recipe, recipe,
                                                  args.max_worlds)
        recipe["models"] = models
        recipe_meta["previous_worlds"] = n_prev_worlds
        recipe["meta"] = recipe_meta
        print(f"  recipe now carries {len(models)} worlds "
              f"({n_prev_worlds} from previous waves)", flush=True)

    # 3. the gate, recomputed ONCE over the combined ensemble on REAL rows
    if args.skip_reval:
        eval_res = {"skipped": True,
                    "publishable": all(s["eval"].get("publishable") for s in shards)}
    else:
        df = load_frame(args.data_glob)
        (train, holdout), cut = split_out_of_time(df, ref["holdout_days"])
        if str(cut.date()) != ref["split_at"]:
            raise SystemExit(
                f"re-eval split {cut.date()} != shard split {ref['split_at']} "
                "— pass the same --data-glob the shards used, or the gate "
                "would score a different holdout than the one reported")
        worlds = recipe_models(recipe)
        eval_res = evaluate(worlds, holdout, league_priors(train),
                            seed=recipe_meta["seed"] or 0)
        print(f"  combined ensemble ({len(worlds)} worlds) on real holdout "
              f"n={eval_res.get('n')}: 1x2 ll={eval_res.get('home_1x2', {}).get('log_loss')} "
              f"| btts ll={eval_res.get('btts', {}).get('log_loss')} "
              f"| o25 ll={eval_res.get('over25', {}).get('log_loss')} "
              f"| publishable={eval_res.get('publishable')}", flush=True)

    # 4. write everything BEFORE judging, so a red job keeps its evidence
    os.makedirs(args.out_dir, exist_ok=True)
    with open(recipe_path, "w", encoding="utf-8") as fh:
        json.dump(recipe, fh)
    recipe_mb = os.path.getsize(recipe_path) / 1e6

    # the merged counters themselves — the ONLY thing the next wave needs to
    # accumulate into (still counters, never rows: ~40 KB of int64s).
    with open(counters_path, "w", encoding="utf-8") as fh:
        json.dump(stats.to_dict(), fh)

    report = {
        "schema": REPORT_SCHEMA,
        "meta": {**recipe_meta,
                 "recipe_mb": round(recipe_mb, 2),
                 "worlds": len(recipe["models"]),
                 "accumulated": bool(args.accumulate),
                 "previous_worlds": n_prev_worlds,
                 "duration_s": round(time.time() - t0, 1)},
        "eval": eval_res,
        "scoreline_summary": scoreline_table(stats.scoreline),
        "calibration": {
            "outcome": calibration_table(stats.outcome_by_prob_bin),
            "btts": calibration_table(stats.btts_by_pred_bin),
            "over25": calibration_table(stats.over25_by_pred_bin),
            "over35": calibration_table(stats.over35_by_pred_bin),
        },
        "chains": [c for s in shards for c in s.get("chains", [])],
        "breed": {k: sum(s.get("breed", {}).get(k, 0) for s in shards)
                  for k in ("children", "accepted")},
        "shard_evals": [{"shard": s["meta"].get("shard"),
                         "publishable": s["eval"].get("publishable"),
                         "log_loss_1x2": s["eval"].get("home_1x2", {}).get("log_loss"),
                         "simulated": s["meta"].get("simulated_outcomes")}
                        for s in shards],
    }
    report_path = os.path.join(args.out_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    print(f"  wrote {recipe_path} ({recipe_mb:.2f} MB, "
          f"{len(recipe['models'])} worlds) and {report_path}", flush=True)

    acc = [c.get("acceptance", 0) for c in report["chains"]]
    if acc:
        print(f"  cap-down acceptance across {len(acc)} chains: "
              f"min={min(acc):.2f} mean={float(np.mean(acc)):.2f} max={max(acc):.2f}",
              flush=True)

    if args.no_gate:
        print("gate skipped (--no-gate)")
        return 0
    if eval_res.get("publishable"):
        b = eval_res.get("beats", {})
        print(f"GATE PASS — beats the league prior on 1X2 "
              f"({b.get('league_prior_1x2')}), goal-market mean, and "
              f"{b.get('league_prior_goal_wins', '?')} goal markets "
              "(real out-of-time holdout)")
        return 0
    print("GATE FAIL — the combined ensemble does not beat the league prior "
          "on the real holdout; nothing was published as a winner")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Push AI model predictions to Supabase, safely replacing the heuristic rows.

Flow
----
1. --backup        dump the whole predictions table to ai/data/predictions_backup_<ts>.json
2. run             generate predictions with the trained model, normalize match ids
                   (the JS fetch action stores bare football-data.org ids; the AI
                   loader prefixes fdorg_ — strip it so upserts REPLACE instead of
                   duplicating), skip rows that would duplicate an existing fixture
                   under a different id, upsert, then read back tier/prob stats.
3. --dry-run       stop after the pre-flight report (no writes).

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY + FOOTBALL_API_TOKEN from repo .env
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
sys.path.insert(0, BASE)


def _load_dotenv() -> None:
    try:
        for line in open(os.path.join(REPO, ".env")):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()

from enrich_match_details import _norm_team, _rest  # noqa: E402


def backup() -> str:
    rows = _rest("GET", "predictions", params={"select": "*", "limit": "10000"}) or []
    os.makedirs(os.path.join(BASE, "data"), exist_ok=True)
    path = os.path.join(BASE, "data",
                        f"predictions_backup_{dt.datetime.utcnow():%Y%m%dT%H%M%SZ}.json")
    with open(path, "w") as f:
        json.dump(rows, f)
    print(f"backup: {len(rows)} rows -> {path}")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", action="store_true", help="backup table and exit")
    ap.add_argument("--dry-run", action="store_true", help="generate + report, no writes")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    if args.backup:
        backup()
        return 0

    backup()

    from model.predictor import PredictionEngine

    engine = PredictionEngine().load_or_train()
    if engine.predictor is None:
        print("no trained model available — aborting")
        return 2

    preds = engine.run(days_ahead=args.days)
    print(f"model generated {len(preds)} predictions")

    existing = _rest("GET", "predictions",
                     params={"select": "match_id,home_team,away_team,match_date"}) or []
    by_id = {r["match_id"]: r for r in existing}

    # same-fixture index under a DIFFERENT id (heuristic bare id vs fdorg_ etc.)
    fixtures: dict[tuple, str] = {}
    for r in existing:
        key = (_norm_team(r.get("home_team", "")), _norm_team(r.get("away_team", "")),
               str(r.get("match_date", ""))[:10])
        fixtures[key] = r["match_id"]

    out, skipped, replaced, fresh = [], [], 0, 0
    for p in preds:
        mid = str(p.get("match_id", ""))
        if mid.startswith("fdorg_") and mid[6:] in by_id:
            mid = mid[6:]          # align with the JS fetcher's bare ids
        p["match_id"] = mid
        key = (_norm_team(p.get("home_team", "")), _norm_team(p.get("away_team", "")),
               str(p.get("match_date", ""))[:10])
        other = fixtures.get(key)
        if other is not None and other != mid:
            skipped.append((mid, other, p.get("home_team"), p.get("away_team")))
            continue
        if mid in by_id:
            replaced += 1
        else:
            fresh += 1
        probs = (p.get("stats") or {}).get("details", {})
        out.append(p)

    print(f"pre-flight: {len(out)} rows ({replaced} replace, {fresh} new), "
          f"{len(skipped)} skipped as cross-id duplicates")
    for s in skipped[:10]:
        print(f"   skip {s[2]} vs {s[3]}: ai={s[0]} existing={s[1]}")

    if args.dry_run:
        return 0
    if not out:
        print("nothing to push")
        return 0

    saved = engine.push_to_supabase(out)
    print(f"pushed {saved}/{len(out)}")

    # verify: read back upcoming rows, tier spread + prob sanity
    back = _rest("GET", "predictions",
                 params={"select": "match_id,prediction,confidence,tier,status,stats",
                         "status": "eq.upcoming", "order": "match_date.asc"}) or []
    tiers: dict[str, int] = {}
    bad = 0
    for r in back:
        tiers[r.get("tier") or "?"] = tiers.get(r.get("tier") or "?", 0) + 1
        d = ((r.get("stats") or {}).get("details")) or {}
        hp = d.get("prob_home", r.get("confidence"))
    print(f"read-back: {len(back)} upcoming rows | tiers: {json.dumps(tiers, sort_keys=True)}")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())

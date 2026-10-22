#!/usr/bin/env python3
"""
Purge duplicate prediction rows for the same fixture.

Why this exists
---------------
The JS fixture fetcher and the Python live fetcher used to store the same match
under two different id shapes (``561893`` vs ``fdorg_561893``).  Because
Supabase's ``onConflict: 'match_id'`` cannot see those as the same match, every
fixture ended up stored twice — 72 rows for 36 real Saturday fixtures — which
double-counted the app and would have put each game into the booking code twice.

The id scheme is now canonical everywhere (``fdorg_<id>``), so no new duplicates
appear; this script clears the backlog and can be re-run safely at any time.

Keeps the row that carries the most real data:
  1. the row with model output attached (stats.prob_home present),
  2. otherwise the canonical ``fdorg_`` id,
  3. otherwise the lexicographically smallest id.

Usage:
    python ai/prune_duplicates.py            # delete duplicates
    python ai/prune_duplicates.py --dry-run  # report only
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_loader import load_dotenv  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("prune")

SELECT = "id,match_id,home_team,away_team,match_date,status,stats"


def _norm(value: str) -> str:
    """Fold a team name to a comparable key ('Wolverhampton Wanderers FC' -> ...)."""
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _has_model_output(row: dict) -> bool:
    stats = row.get("stats") or {}
    if isinstance(stats, str):
        return '"prob_home"' in stats
    return bool(stats.get("prob_home") is not None)


def pick_survivor(group: list[dict]) -> dict:
    return min(
        group,
        key=lambda r: (
            not _has_model_output(r),          # model output wins
            not str(r.get("match_id", "")).startswith("fdorg_"),
            str(r.get("match_id", "")),
        ),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove duplicate rows for the same fixture.")
    ap.add_argument("--dry-run", action="store_true", help="report only, delete nothing")
    ap.add_argument("--status", default="upcoming",
                    help="only consider rows with this status (default: upcoming)")
    ap.add_argument("--normalise-ids", action="store_true",
                    help="rewrite bare football-data.org ids to the canonical fdorg_<id> form")
    args = ap.parse_args()

    try:
        from supabase import create_client
    except ImportError:
        logger.error("The `supabase` package is not installed.")
        return 1

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_ANON_KEY", ""))
    if not url or not key:
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_KEY is not set.")
        return 1
    sb = create_client(url, key)

    try:
        resp = sb.table("predictions").select(SELECT).eq("status", args.status).execute()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not read predictions: %s", exc)
        return 1

    rows = resp.data or []
    logger.info("Loaded %d '%s' row(s).", len(rows), args.status)

    # Older writers stored a bare football-data.org id ('561893'). The canonical
    # shape is 'fdorg_561893'; anything still bare would be re-created as a
    # duplicate the next time the fixture fetcher runs, so rewrite it in place.
    known = {str(r.get("match_id", "")) for r in rows}
    renames = [(r, f"fdorg_{r['match_id']}") for r in rows
               if str(r.get("match_id", "")).isdigit()
               and f"fdorg_{r['match_id']}" not in known]
    if renames:
        verb = "Would rewrite" if args.dry_run else "Rewriting"
        logger.info("%s %d bare id(s) to the canonical fdorg_ form.", verb, len(renames))
        for row, new_id in renames:
            logger.info("  %s -> %s  (%s vs %s)", row["match_id"], new_id,
                        row.get("home_team"), row.get("away_team"))
            if args.dry_run:
                continue
            try:
                sb.table("predictions").update({"match_id": new_id}).eq("id", row["id"]).execute()
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not rewrite %s: %s", row["match_id"], exc)
    else:
        logger.info("Every match_id already uses the canonical fdorg_ form.")

    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key_ = (_norm(row.get("home_team", "")), _norm(row.get("away_team", "")),
                str(row.get("match_date", ""))[:10])
        groups.setdefault(key_, []).append(row)

    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    if not duplicates:
        logger.info("No duplicate fixtures found.")
        return 0

    stale: list[str] = []
    logger.info("%d fixture(s) are stored more than once:", len(duplicates))
    for _, group in sorted(duplicates.items(), key=lambda kv: kv[1][0].get("home_team", "")):
        keep = pick_survivor(group)
        drop = [r for r in group if r["id"] != keep["id"]]
        stale.extend(r["id"] for r in drop)
        logger.info("  %s vs %s — keeping %s, dropping %s",
                    keep.get("home_team"), keep.get("away_team"),
                    keep.get("match_id"), ", ".join(str(r.get("match_id")) for r in drop))

    if args.dry_run:
        logger.info("Dry run — would delete %d row(s).", len(stale))
        return 0

    deleted = 0
    for i in range(0, len(stale), 50):
        chunk = stale[i:i + 50]
        try:
            sb.table("predictions").delete().in_("id", chunk).execute()
            deleted += len(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not delete a chunk of %d: %s", len(chunk), exc)
    logger.info("Deleted %d duplicate row(s).", deleted)
    return 0


if __name__ == "__main__":
    sys.exit(main())

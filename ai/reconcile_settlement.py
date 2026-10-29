#!/usr/bin/env python3
"""
Reconcile stuck settlement rows (IMPROVEMENT_PLAN Step 2.4).

The scheduled settle run (settle.yml, every 2h) tries football-data.org ->
ESPN -> TheSportsDB and leaves a row untouched when no source has a score.
That is correct for a match whose feed is merely lagging — but a handful of
rows never resolve (renamed clubs, abandoned fixtures, feed gaps) and sit
as status='upcoming' forever, polluting the accuracy screen and re-appearing
in every settle run.

This manual script is the fix-it tool:

  1. Re-attempt every unresolved row with ALL sources and verbose per-row
     logging (which source answered, which didn't).
  2. Rows unresolvable for more than --void-after hours (default 72) can be
     marked with --mark-void: status='void' + actual_result='Unresolved'.
     The feed filters on status IN ('upcoming','live','completed'), so a
     voided row disappears from the app and from future settle runs while
     remaining queryable for audits.

Usage:
    python ai/reconcile_settlement.py                    # report + settle what we can
    python ai/reconcile_settlement.py --dry-run          # report only, write nothing
    python ai/reconcile_settlement.py --mark-void        # also void rows >72h stuck
    python ai/reconcile_settlement.py --mark-void --void-after 48
    python ai/reconcile_settlement.py --match "Santa Clara"     # narrow to one row
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_loader import load_dotenv  # noqa: E402

load_dotenv()

from settle_predictions import (  # noqa: E402
    _has_settlement_columns,
    _hours_past,
    espn_league_of,
    logger as settle_logger,
    resolve_score,
)
from model.live_fetcher import (  # noqa: E402
    fetch_espn_results_for_dates,
    fetch_match_result,
    fetch_results_for_dates,
    result_label,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("reconcile")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)


def _client():
    """Supabase client or None (with a clear message)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot run.")
        return None
    try:
        from supabase import create_client  # noqa: PLC0415
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:  # noqa: BLE001
        logger.error("Could not create Supabase client: %s", e)
        return None


def fetch_unresolved(sb, match_filter: str = "") -> list[dict]:
    """Rows past kickoff still marked upcoming/live, oldest first."""
    now = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    q = (
        sb.table("predictions")
        .select(
            "id, match_id, match_title, home_team, away_team, "
            "league_slug, kickoff_time, prediction, status, actual_result"
        )
        .in_("status", ["upcoming", "live"])
        .lt("kickoff_time", now)
        .order("kickoff_time", desc=False)
    )
    if match_filter:
        q = q.ilike("match_title", f"%{match_filter}%")
    return (q.execute().data or [])


def retry_sources(p: dict, bulk: dict, espn_by_id: dict,
                  espn_by_names: dict):
    """All-source retry for one row. Returns (score, source) or (None, None)."""
    # Same order as the scheduled run — resolve_score is the shared brain.
    score, source = resolve_score(p, bulk, espn_by_id, espn_by_names)
    if score:
        return score, source
    result = fetch_match_result(
        match_id=p.get("match_id", ""),
        home_team=p.get("home_team", ""),
        away_team=p.get("away_team", ""),
        kickoff_time=p.get("kickoff_time", ""),
    )
    if result and result.get("home_score") is not None:
        return (result["home_score"], result["away_score"]), "thesportsdb"
    return None, None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    ap.add_argument("--mark-void", action="store_true",
                    help="set status='void' on rows stuck beyond --void-after")
    ap.add_argument("--void-after", type=int, default=72,
                    help="hours past kickoff before a row may be voided (72)")
    ap.add_argument("--match", default="",
                    help="only rows whose match_title contains this text")
    args = ap.parse_args(argv)

    sb = _client()
    if sb is None:
        return 2

    rows = fetch_unresolved(sb, args.match)
    if not rows:
        logger.info("No unresolved rows — everything is settled. 🎉")
        return 0
    logger.info("Found %d unresolved row(s) past kickoff.", len(rows))

    if not args.dry_run and not _has_settlement_columns(sb):
        return 2

    # One bulk fetch for every match-day involved, plus every ESPN league
    # day — this script always widens; that's its whole point.
    days = sorted({
        (p.get("kickoff_time") or "")[:10] for p in rows if p.get("kickoff_time")
    })
    bulk = fetch_results_for_dates(days)
    espn_days = sorted({
        d for p in rows if (d := (p.get("kickoff_time") or "")[:10])
    })
    espn_by_id, espn_by_names = fetch_espn_results_for_dates(espn_days)
    logger.info("Sources: football-data=%d espn=%d (days=%s)",
                len(bulk), len(espn_by_id), ",".join(days))

    settled, voided, still_stuck = 0, 0, []
    now = dt.datetime.now(dt.timezone.utc)
    for p in rows:
        hours = _hours_past(p.get("kickoff_time"))
        hours_txt = f"{hours:.0f}h" if hours is not None else "?h"
        score, source = retry_sources(p, bulk, espn_by_id, espn_by_names)

        if score:
            hs, as_ = score
            label = result_label(hs, as_)
            logger.info("  RESOLVED [%s] %s vs %s -> %s-%s (%s) via %s",
                        hours_txt, p.get("home_team"), p.get("away_team"),
                        hs, as_, label, source)
            if args.dry_run:
                settled += 1
                continue
            try:
                # Status-guarded, idempotent (audit M-16): only flip rows still
                # awaiting settlement; a concurrent run that already completed
                # the row makes this a no-op instead of a duplicate write.
                res = (
                    sb.table("predictions")
                    .update(
                        {
                            "actual_result": label,
                            "home_score": hs,
                            "away_score": as_,
                            "status": "completed",
                            "settled_at": now.replace(microsecond=0).isoformat()
                            .replace("+00:00", "Z"),
                        }
                    )
                    .eq("id", p["id"])
                    .in_("status", ["upcoming", "live"])
                    .execute()
                )
                if res.data:
                    settled += 1
                else:
                    logger.info("  %s vs %s already settled concurrently — skipped",
                                p.get("home_team"), p.get("away_team"))
            except Exception as e:  # noqa: BLE001
                logger.warning("  update failed for %s: %s",
                               p.get("match_title"), e)
            continue

        can_void = (
            hours is not None and hours >= args.void_after
        ) or hours is None  # unparseable kickoff = permanently stuck
        if args.mark_void and can_void:
            logger.warning("  VOID [%s] %s vs %s — no source has a result "
                           "(likely abandoned/renamed fixture)",
                           hours_txt, p.get("home_team"), p.get("away_team"))
            if args.dry_run:
                voided += 1
                continue
            try:
                # Status-guarded (audit M-16): only void rows still awaiting
                # settlement — never overwrite a row a concurrent run resolved.
                res = (
                    sb.table("predictions")
                    .update(
                        {
                            "status": "void",
                            "actual_result": "Unresolved",
                        }
                    )
                    .eq("id", p["id"])
                    .in_("status", ["upcoming", "live"])
                    .execute()
                )
                if res.data:
                    voided += 1
                else:
                    logger.info("  %s vs %s already settled concurrently — not voided",
                                p.get("home_team"), p.get("away_team"))
            except Exception as e:  # noqa: BLE001
                logger.warning("  void failed for %s: %s",
                               p.get("match_title"), e)
            continue

        logger.info("  STILL STUCK [%s] %s vs %s (%s)%s",
                    hours_txt, p.get("home_team"), p.get("away_team"),
                    p.get("league_slug"),
                    "" if can_void else "  — within void window, retry later")
        still_stuck.append(p)

    logger.info(
        "Done. resolved=%d voided=%d still_stuck=%d%s",
        settled, voided, len(still_stuck),
        "  (dry-run: nothing written)" if args.dry_run else "",
    )
    for p in still_stuck[:10]:
        logger.info("  stuck: %s vs %s (kickoff %s, %s)",
                    p.get("home_team"), p.get("away_team"),
                    p.get("kickoff_time"), p.get("league_slug"))
    # Non-zero exit when rows remain stuck so CI/cron wrappers can alert.
    return 0 if not still_stuck else 3


if __name__ == "__main__":
    # settle_logger is imported for the shared helpers; silence its DEBUG
    # noise unless the caller raised the root level.
    settle_logger.setLevel(logging.WARNING)
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
Settle finished predictions with real results.

Deliberately model-free: it only needs the Supabase client and HTTP, so it
can run on a fresh CI runner (no LightGBM, no .pkl download) on its own
schedule.

Sources (in order of preference):
  1. football-data.org  (/v4/matches) — has the score for every match we predict
  2. TheSportsDB        — fallback

Writes `actual_result`, `home_score`, `away_score`, `status` and `settled_at`
onto the `predictions` row so the accuracy screen and the backtest have real
data to work with.

Usage:
    python ai/settle_predictions.py            # settle everything due
    python ai/settle_predictions.py --dry-run  # show what would change
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

import requests  # noqa: E402

from model.live_fetcher import (  # noqa: E402
    fetch_match_result,
    fetch_results_for_dates,
    normalize_team_name,
    result_label,
    team_names_match,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("settle")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fwpriiugfjhtoymcbmso.supabase.co")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    os.environ.get("SUPABASE_ANON_KEY", ""),
)

#: columns the settlement writer needs — probed so a half-run migration
#: produces a clear message instead of a 400 from PostgREST.
_STATS_PROBE = ("actual_result", "home_score", "away_score", "settled_at")


def get_client():
    """Create a Supabase client (None when credentials or the lib are missing)."""
    try:
        from supabase import create_client
    except ImportError:
        logger.error("The `supabase` package is not installed (pip install -r ai/requirements.txt).")
        return None
    if not SUPABASE_KEY:
        logger.error("SUPABASE_SERVICE_KEY / SUPABASE_ANON_KEY is not set.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:  # noqa: BLE001
        logger.error("Cannot create Supabase client: %s", e)
        return None


def _has_settlement_columns(sb) -> bool:
    """True when every column the writer needs exists."""
    try:
        sb.table("predictions").select(",".join(_STATS_PROBE)).limit(1).execute()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(
            "Cannot settle predictions: settlement column missing (%s). "
            "Run statwise/supabase-settlement-migration.sql in the Supabase SQL editor.",
            e,
        )
        return False


def settle(sb, dry_run: bool = False) -> int:
    """Settle every prediction whose kickoff has passed. Returns rows written."""
    if sb is None:
        return 0

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        resp = (
            sb.table("predictions")
            .select("id, match_id, match_title, home_team, away_team, "
                    "kickoff_time, prediction, status, actual_result")
            .in_("status", ["upcoming", "live"])
            .lt("kickoff_time", now)
            .execute()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not fetch past predictions: %s", e)
        return 0

    past = resp.data or []
    if not past:
        logger.info("No past predictions to settle.")
        return 0

    if not dry_run and not _has_settlement_columns(sb):
        return 0

    logger.info("Attempting to settle %d past prediction(s)…", len(past))

    # One bulk request per match-day (note: football-data.org's dateTo is
    # EXCLUSIVE — fetch_results_for_dates handles that).
    days = sorted({(p.get("kickoff_time") or "")[:10] for p in past if p.get("kickoff_time")})
    bulk = fetch_results_for_dates(days)
    logger.info("football-data.org returned %d finished match(es).", len(bulk))

    settled = 0
    for p in past:
        home, away = p.get("home_team", ""), p.get("away_team", "")
        score = bulk.get((normalize_team_name(home), normalize_team_name(away)))
        if score is None:
            for (nh, na), sc in bulk.items():
                if team_names_match(home, nh) and team_names_match(away, na):
                    score = sc
                    break

        result = None
        if score:
            hs, as_ = score
            result = {
                "home_score": hs,
                "away_score": as_,
                "actual_result": result_label(hs, as_),
            }
        else:
            # Fall back to the per-match source (TheSportsDB).
            result = fetch_match_result(
                match_id=p.get("match_id", ""),
                home_team=home,
                away_team=away,
                kickoff_time=p.get("kickoff_time", ""),
            )

        if not result:
            # Never flip status without a scoreline — that would lose the row
            # (and it is what used to make yesterday's games look "settled
            # with no result").
            logger.info("  no result yet for %s vs %s", home, away)
            continue

        label = result["actual_result"]
        correct = label == p.get("prediction")
        logger.info(
            "  %s vs %s -> %s-%s (%s)%s",
            home, away, result["home_score"], result["away_score"], label,
            "  [pico correct]" if correct else "  [pico wrong]",
        )

        if dry_run:
            settled += 1
            continue

        try:
            sb.table("predictions").update(
                {
                    "actual_result": label,
                    "home_score": result.get("home_score"),
                    "away_score": result.get("away_score"),
                    "status": "completed",
                    "settled_at": dt.datetime.now(dt.timezone.utc)
                    .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                }
            ).eq("id", p["id"]).execute()
            settled += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to settle prediction %s: %s", p.get("match_title"), e)

    if dry_run:
        logger.info("Dry run — %d of %d would be settled; nothing written.",
                    settled, len(past))
    else:
        logger.info("Settled %d/%d predictions.", settled, len(past))
    return settled


def main() -> int:
    ap = argparse.ArgumentParser(description="Settle finished predictions with real results.")
    ap.add_argument("--dry-run", action="store_true", help="show what would change")
    args = ap.parse_args()

    sb = get_client()
    if sb is None:
        return 1
    settle(sb, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

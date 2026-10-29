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
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from env_loader import load_dotenv  # noqa: E402

load_dotenv()

import requests  # noqa: E402

from enrich_match_details import ESPN_SLUGS  # noqa: E402
from model.live_fetcher import (  # noqa: E402
    fetch_espn_results_for_dates,
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

#: a row this many hours past kickoff with no result from ANY source is
#: logged as an alert (plan 2.4) — 6h comfortably covers extra time + a
#: delayed feed write, without page-worthiness for normal lag.
ALERT_AFTER_HOURS = 6


# ── Pure resolution helpers (unit tested in ai/experimental/test_settle.py) ──

def pick_from_map(p: dict, by_key: dict, keyed_by_day: bool = False):
    """Best (score, source) for one prediction from a name-keyed result map.

    `by_key` is keyed by (norm_home, norm_away) — football-data bulk — or by
    (day, norm_home, norm_away) — ESPN by-names — when `keyed_by_day`.
    Exact normalized names win; fuzzy matching is the fallback.
    """
    home, away = p.get("home_team", ""), p.get("away_team", "")
    day = (p.get("kickoff_time") or "")[:10]
    exact = ((day,) if keyed_by_day else ()) + (
        normalize_team_name(home), normalize_team_name(away))
    score = by_key.get(exact)
    if score is not None:
        return score
    for key, sc in by_key.items():
        if keyed_by_day and key[0] != day:
            continue
        kh, ka = key[-2:]
        if team_names_match(home, kh) and team_names_match(away, ka):
            return sc
    return None


def resolve_score(p: dict, bulk: dict, espn_by_id: dict, espn_by_names: dict):
    """Source-ordered score lookup. Returns (score, source) or (None, None).

    Order:
      1. football-data.org bulk (exact names, then fuzzy)
      2. ESPN by event id — exact, immune to feed naming differences
      3. ESPN by names (exact, then fuzzy, same day only)
    """
    score = pick_from_map(p, bulk)
    if score is not None:
        return score, "football-data.org"
    if (p.get("match_id") or "").startswith("espn_"):
        score = espn_by_id.get(p["match_id"][5:])
        if score is not None:
            return score, "espn"
    score = pick_from_map(p, espn_by_names, keyed_by_day=True)
    if score is not None:
        return score, "espn"
    return None, None


def espn_league_of(p: dict) -> str | None:
    """ESPN result league for a prediction, or None for fd.org-only leagues.

    Cups live on ESPN only (football-data.org 403s them); the big domestic
    leagues settle from football-data.org first, so they're only needed as a
    fallback — the caller widens the ESPN fetch when primary bulk comes back
    empty.
    """
    slug = p.get("league_slug") or ""
    if (p.get("match_id") or "").startswith("espn_") or slug in (
            "europa-league", "conference-league", "copa-libertadores"):
        return ESPN_SLUGS.get(slug)
    return None


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
    # Bounded, paginated read: the old single `.execute()` pulled EVERY
    # un-settled row ever written into memory at once (it grows forever if a
    # settlement ever fails), which is how the job OOM'd/timeout'd.
    past: list[dict] = []
    try:
        page, page_size, max_rows = 0, 1000, 20_000
        while len(past) < max_rows:
            resp = (
                sb.table("predictions")
                .select("id, match_id, match_title, home_team, away_team, "
                        "league_slug, kickoff_time, prediction, status, actual_result")
                .in_("status", ["upcoming", "live"])
                .lt("kickoff_time", now)
                .order("kickoff_time", desc=False)
                .range(page * page_size, (page + 1) * page_size - 1)
                .execute()
            )
            chunk = resp.data or []
            past.extend(chunk)
            if len(chunk) < page_size:
                break
            page += 1
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not fetch past predictions: %s", e)
        return 0

    if not past:
        logger.info("No past predictions to settle.")
        return 0

    if len(past) >= 20_000:
        logger.warning("Settlement backlog capped at %d rows this run — the "
                       "rest will be handled on the next run.", len(past))

    if not dry_run and not _has_settlement_columns(sb):
        return 0

    logger.info("Attempting to settle %d past prediction(s)…", len(past))

    # One bulk request per match-day (note: football-data.org's dateTo is
    # EXCLUSIVE — fetch_results_for_dates handles that).
    days = sorted({(p.get("kickoff_time") or "")[:10] for p in past if p.get("kickoff_time")})
    bulk = fetch_results_for_dates(days)
    logger.info("football-data.org returned %d finished match(es).", len(bulk))

    # ESPN is the only source for the UEFA cup competitions (football-data.org
    # returns 403 for them) and it is more complete for the Libertadores, so
    # fetch its scoreboards for the days where those rows live.
    espn_days = sorted({
        (p.get("kickoff_time") or "")[:10]
        for p in past
        if espn_league_of(p)
    })
    # Fallback improvement (plan 2.4): when football-data.org came back empty
    # (token outage, plan limits), widen ESPN to every league we have a row
    # for instead of only the cups — the run degrades gracefully instead of
    # silently settling nothing.
    widened = False
    if not bulk and days:
        espn_days = sorted(set(espn_days) | set(days))
        widened = True
        logger.warning(
            "football-data.org returned no results — widening ESPN fallback "
            "to %d match day(s).", len(espn_days))
    espn_by_id, espn_by_names = (
        fetch_espn_results_for_dates(espn_days) if espn_days else ({}, {})
    )
    if espn_days:
        logger.info("ESPN returned %d finished match(es).", len(espn_by_id))

    settled = 0
    by_source: dict[str, int] = {}
    unresolved: list[dict] = []
    for p in past:
        home, away = p.get("home_team", ""), p.get("away_team", "")
        score, source = resolve_score(p, bulk, espn_by_id, espn_by_names)

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
            if result:
                source = "thesportsdb"

        if not result:
            # Never flip status without a scoreline — that would lose the row
            # (and it is what used to make yesterday's games look "settled
            # with no result").
            hours = _hours_past(p.get("kickoff_time"))
            if hours is not None and hours >= ALERT_AFTER_HOURS:
                logger.warning(
                    "UNRESOLVED %.0fh past kickoff, no result from any source: %s vs %s (%s)",
                    hours, home, away, p.get("league_slug"))
                unresolved.append({**p, "hours_past": round(hours)})
            else:
                logger.info("  no result yet for %s vs %s", home, away)
            continue

        label = result["actual_result"]
        correct = label == p.get("prediction")
        logger.info(
            "  %s vs %s -> %s-%s (%s, via %s)%s",
            home, away, result["home_score"], result["away_score"], label, source,
            "  [pico correct]" if correct else "  [pico wrong]",
        )
        by_source[source] = by_source.get(source, 0) + 1

        if dry_run:
            settled += 1
            continue

        try:
            # Status-guarded, idempotent write (audit M-16): the SELECT above
            # already filters to upcoming/live, but between SELECT and UPDATE a
            # concurrent settlement run (settle.yml vs. a predict cycle's inline
            # scheduler settle) may have completed the row. The .in_() guard
            # makes that second write a no-op instead of overwriting a fresh
            # final score with a stale live one, and we only count rows we
            # actually flipped (returned data non-empty).
            res = (
                sb.table("predictions")
                .update(
                    {
                        "actual_result": label,
                        "home_score": result.get("home_score"),
                        "away_score": result.get("away_score"),
                        "status": "completed",
                        "settled_at": dt.datetime.now(dt.timezone.utc)
                        .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    }
                )
                .eq("id", p["id"])
                .in_("status", ["upcoming", "live"])
                .execute()
            )
            if res.data:
                settled += 1
            else:
                logger.info(
                    "  %s vs %s already settled by a concurrent run — skipped",
                    home, away)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to settle prediction %s: %s", p.get("match_title"), e)

    if dry_run:
        logger.info("Dry run — %d of %d would be settled; nothing written.",
                    settled, len(past))
    else:
        logger.info("Settled %d/%d predictions (sources: %s).",
                    settled, len(past), by_source or "{}")
        _write_stats(sb, settled, by_source, unresolved, widened)

    if unresolved:
        logger.warning(
            "ALERT: %d prediction(s) still unresolved after every source — "
            "run ai/reconcile_settlement.py for a manual pass.", len(unresolved))
    return settled


def _hours_past(kickoff_iso: str | None) -> float | None:
    """Hours between kickoff and now (None when unparseable)."""
    if not kickoff_iso:
        return None
    try:
        ko = dt.datetime.fromisoformat(str(kickoff_iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - ko).total_seconds() / 3600.0


def _write_stats(sb, settled: int, by_source: dict, unresolved: list,
                 widened: bool) -> None:
    """Rolling settlement counters in app_settings (plan 2.4 — visibility).

    Same pattern as booking_code_stats: no DDL, readable by the app role.
    """
    stamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        cur = (
            sb.table("app_settings")
            .select("setting_value")
            .eq("setting_key", "settlement_stats")
            .limit(1)
            .execute()
        )
        prev = {}
        if cur.data:
            val = cur.data[0].get("setting_value")
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:  # noqa: BLE001
                    val = {}
            prev = val or {}
        stats = {
            "attempts": int(prev.get("attempts") or 0) + 1,
            "settled_total": int(prev.get("settled_total") or 0) + settled,
            "last_run_at": stamp,
            "last_settled": settled,
            "last_sources": by_source,
            "unresolved_last": len(unresolved),
            "unresolved_examples": [
                {"match": u.get("match_title"), "league": u.get("league_slug"),
                 "hours_past": u.get("hours_past")}
                for u in unresolved[:5]
            ],
            "fallback_widened": widened,
        }
        row = {
            "setting_value": stats,
            "description": "Settlement run stats (settled counts, sources, unresolved)",
            "is_active": True,
        }
        if cur.data:
            sb.table("app_settings").update(row).eq(
                "setting_key", "settlement_stats").execute()
        else:
            sb.table("app_settings").insert(
                {"setting_key": "settlement_stats", **row}).execute()
        logger.info("Settlement stats updated: last_settled=%d unresolved=%d.",
                    settled, len(unresolved))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not write settlement stats: %s", e)


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

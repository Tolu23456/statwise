#!/usr/bin/env python3
"""Live-window enrichment: lineups, key events and match stats (plan 2.2).

Why this exists next to enrich_match_details.py:
  * enrich_match_details is one-shot — it skips any row that already has
    stats.details (which every row gets from the odds/weather writers within
    minutes), and it runs every 3h, so lineups (~1h before kickoff), live
    cards/goals and in-play stats were never fetched. Every prediction row in
    production today only carries details.market + details.weather.
  * The detail modal already renders Goal Scorers / Cards / Match Statistics /
    Lineups sections from details.* — they were just always empty.

This script runs every 5 minutes (live-enrich.yml) and re-fetches the ESPN
summary for fixtures inside the enrichment window:

    kickoff - 2h  <=  now  <=  kickoff + 4h

so lineups appear as soon as ESPN publishes them, goals/cards/substitutions
and possession/shots/corners update while the match is in play, and final
stats land after full time — merged into stats.details alongside the
existing market/weather keys (never clobbering them).

Rate discipline: the scoreboard is fetched once per league (cached), the
summary only when it can actually add something (see should_fetch_summary).

Pure helpers (no I/O) live at the top so
ai/experimental/test_live_feed.py can test them on tiny synthetic payloads.

Usage:
    python3 ai/live_enricher.py [--dry-run] [--limit N] [--league SLUG]

Env: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (CI secrets; .env locally).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

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

from enrich_match_details import (  # noqa: E402
    ESPN_SLUGS,
    _find_espn_event,
    _http_json,
    _parse_summary,
    _scoreboard_cache,
    _rest,
    update_stats,
)

# ── window policy ──────────────────────────────────────────────────────────
PRE_WINDOW = timedelta(hours=2)      # lineups start dropping ~1h before kickoff
POST_WINDOW = timedelta(hours=4)     # final stats settle well within this
LINEUP_FETCH_BEFORE = timedelta(minutes=80)  # when the summary can hold lineups
CLOCK_STALE = timedelta(minutes=10)  # live_state older than this is not "live"


# ── pure helpers (no I/O — unit tested) ────────────────────────────────────

def parse_minute(display) -> int | None:
    """ESPN clock text -> minute: \"67'\"→67, \"45'+1'\"→45, \"\"→None."""
    if not display or not isinstance(display, str):
        return None
    m = re.search(r"(\d+)", display)
    return int(m.group(1)) if m else None


def parse_iso(raw) -> datetime | None:
    """Parse an ISO-8601 timestamp (Z suffix ok) to aware UTC, else None."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def event_state(ev) -> dict | None:
    """Live state from a scoreboard event: {status, minute, score}.

    status: 'pre' | 'in' | 'post'.  Score only when both sides parsed.
    """
    if not isinstance(ev, dict):
        return None
    st = ev.get("status") or {}
    typ = st.get("type") or {}
    raw = str(st.get("state") or typ.get("state") or "").lower()
    name = str(typ.get("name") or "").upper()
    if raw not in ("pre", "in", "post"):
        if "FULL_TIME" in name or "POST_GAME" in name or name.endswith("FINAL"):
            raw = "post"
        elif "SCHEDULED" in name or "PRE_GAME" in name:
            raw = "pre"
        elif name.startswith("STATUS_"):
            raw = "in"
        else:
            return None

    minute = parse_minute(st.get("displayClock"))
    if minute is None and isinstance(st.get("clock"), (int, float)) and st["clock"] > 0:
        minute = int(st["clock"] // 60)

    home = away = None
    comps = ((ev.get("competitions") or [{}])[0]).get("competitors") or []
    for c in comps:
        try:
            score = int(c.get("score"))
        except (TypeError, ValueError):
            continue
        if str(c.get("homeAway", "")).lower() == "home":
            home = score
        else:
            away = score
    score = {"home": home, "away": away} if home is not None and away is not None else None
    return {"status": raw, "minute": minute, "score": score}


def in_window(kickoff_raw, now: datetime) -> bool:
    """True while now is within [kickoff - 2h, kickoff + 4h]."""
    ko = parse_iso(kickoff_raw)
    if ko is None:
        return False
    return ko - PRE_WINDOW <= now <= ko + POST_WINDOW


def should_fetch_summary(state: dict, kickoff_raw, now: datetime, details: dict) -> bool:
    """Can the (heavier) ESPN summary call still add anything?

    - in play: always — goals/cards/stats keep changing;
    - pre-match: only within 80 min of kickoff (before that ESPN has no
      lineups yet, so the call would be wasted);
    - post-match: only until final stats/lineups/scorers are stored.
    """
    status = (state or {}).get("status")
    if status == "in":
        return True
    if status == "pre":
        ko = parse_iso(kickoff_raw)
        if ko is None:
            return True
        return now >= ko - LINEUP_FETCH_BEFORE
    # post — one summary fetch is enough once the final data is stored.
    d = details or {}
    return not (d.get("team_stats") and d.get("lineups") and d.get("scorers"))


def merge_details(existing, parsed, state, as_of: str) -> dict:
    """Merge fresh summary details + live_state into a copy of the details.

    Existing keys (market, weather, …) are preserved; non-empty parsed keys
    (lineups, scorers, cards, substitutions, team_stats, venue, referee, …)
    replace older versions; live_state is always (re)stamped with as_of.
    """
    out = dict(existing or {})
    for key, val in (parsed or {}).items():
        if val:
            out[key] = val
    out["live_state"] = {
        "status": (state or {}).get("status"),
        "minute": (state or {}).get("minute"),
        "score": (state or {}).get("score"),
        "as_of": as_of,
    }
    return out


def needs_write(existing, merged) -> bool:
    """True when merged differs from existing, ignoring as_of freshness stamps."""
    a = dict(existing or {})
    b = dict(merged or {})
    la = a.pop("live_state", None)
    lb = b.pop("live_state", None)
    if a != b:
        return True

    def strip(ls):
        if not isinstance(ls, dict):
            return ls
        return {k: v for k, v in ls.items() if k != "as_of"}

    return strip(la) != strip(lb)


# ── I/O layer ──────────────────────────────────────────────────────────────

def fetch_candidates(now: datetime) -> list[dict]:
    """Rows that could possibly be inside the window (yesterday → ahead)."""
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return _rest("GET", "predictions", params={
        "select": (
            "id,match_id,home_team,away_team,league_slug,match_date,"
            "kickoff_time,status,stats"
        ),
        "match_date": f"gte.{yesterday}",
        "order": "kickoff_time.asc",
    }) or []


def _scoreboard_event(league_slug: str, month: str, ev_id: str):
    slug = ESPN_SLUGS.get(league_slug)
    if not slug:
        return None
    for ev in _scoreboard_cache.get((slug, month), []):
        if ev.get("id") == ev_id:
            return ev
    return None


def run(args) -> int:
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_candidates(now)
    candidates = [r for r in rows if in_window(r.get("kickoff_time"), now)]
    print(f"rows={len(rows)} in-window={len(candidates)}")
    if not candidates:
        print("No fixtures inside the enrichment window — nothing to do.")
        return 0
    if args.limit:
        candidates = candidates[: args.limit]
    if args.league:
        candidates = [r for r in candidates
                      if (r.get("league_slug") or "") == args.league]

    written = unchanged = no_mapping = no_event = summaries = 0
    for r in candidates:
        league = r.get("league_slug") or ""
        home = r.get("home_team") or ""
        away = r.get("away_team") or ""
        label = f"{home} vs {away}"
        if league not in ESPN_SLUGS:
            no_mapping += 1
            continue
        kickoff = r.get("kickoff_time") or f"{r.get('match_date', '')}T12:00:00Z"
        month = kickoff[:7].replace("-", "")
        ev_id = _find_espn_event(league, month, home, away)
        if not ev_id:
            no_event += 1
            continue
        ev = _scoreboard_event(league, month, ev_id)
        state = event_state(ev)
        if state is None:
            no_event += 1
            continue

        stats = r.get("stats")
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except Exception:
                stats = {}
        if not isinstance(stats, dict):
            stats = {}
        details = stats.get("details")
        if not isinstance(details, dict):
            details = {}

        parsed = {}
        if should_fetch_summary(state, kickoff, now, details):
            summary = _http_json(
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                f"{ESPN_SLUGS[league]}/summary?event={ev_id}"
            )
            summaries += 1
            if summary:
                parsed = _parse_summary(summary) or {}

        merged = merge_details(details, parsed, state, now_iso)
        merged["espn_event_id"] = ev_id

        if not needs_write(details, merged):
            unchanged += 1
            print(f"  · {label} — {state['status']} unchanged")
            continue
        if args.dry_run:
            print(f"  would write {label} ({state['status']}, "
                  f"{len(parsed)} summary key(s): {sorted(parsed)})")
            written += 1
            continue
        stats["details"] = merged
        update_stats(r["id"], stats)
        written += 1
        extra = ",".join(k for k in ("lineups", "scorers", "cards",
                                     "substitutions", "team_stats") if parsed.get(k))
        minute_s = f"{state['minute']}'" if state.get("minute") is not None else ""
        print(f"  ✓ {label} [{state['status']}] {minute_s} {extra}".rstrip())

    print(
        f"\nDone. window={len(candidates)} written={written} "
        f"unchanged={unchanged} summaries={summaries} "
        f"no_mapping={no_mapping} no_event={no_event}"
        + ("  (dry-run: nothing persisted)" if args.dry_run else "")
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Enrich in-window fixtures with live ESPN data")
    ap.add_argument("--dry-run", action="store_true", help="show writes, persist nothing")
    ap.add_argument("--limit", type=int, default=None, help="max rows to process")
    ap.add_argument("--league", type=str, default=None, help="only this league_slug")
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Checks for settlement improvements (IMPROVEMENT_PLAN Step 2.4).

What silently breaks settlement:

  1. Source-order bugs — the score must come football-data.org -> ESPN
     (by exact event id) -> ESPN (by name, same day) -> TheSportsDB, and
     the winning source must be tracked so a broken feed is visible.
  2. Day leakage — ESPN name matching must never pick yesterday's fixture
     of the same two teams (cups do that: group stage + replay).
  3. Alerting — a row hours past kickoff with no result from ANY source is
     the real failure signal (a feed is broken); without it, stuck rows
     rot silently as status='upcoming' forever.
  4. _hours_past parses ISO with/without tz, rejects garbage.

Everything here is tiny synthetic payloads — no network, no Supabase.

Run:  .venv/bin/python ai/experimental/test_settle.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

# settle_predictions imports enrich_match_details, which needs Supabase env
# at import time; dummies keep the pure helpers importable identically on
# dev machines and CI.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)

import settle_predictions as sp  # noqa: E402
from model.live_fetcher import normalize_team_name, result_label  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


P = {
    "home_team": "Arsenal",
    "away_team": "Chelsea",
    "kickoff_time": "2026-10-25T15:00:00Z",
    "match_id": "espn_401234",
    "league_slug": "premier-league",
}
DAY = "2026-10-25"
NH, NA = normalize_team_name("Arsenal"), normalize_team_name("Chelsea")

# ── 1. pick_from_map: exact normalized keys win, fuzzy is the fallback ─────
m = {(NH, NA): (2, 1)}
check("exact normalized names -> score", sp.pick_from_map(P, m) == (2, 1))
m2 = {(NH, "chelsea fc"): (3, 0)}          # fuzzy on the away side only
check("fuzzy fallback finds renamed club", sp.pick_from_map(P, m2) == (3, 0))
check("no match -> None", sp.pick_from_map(P, {("x", "y"): (1, 0)}) is None)
check("empty map -> None", sp.pick_from_map(P, {}) is None)

# day-keyed variant (ESPN by-names)
dm = {(DAY, NH, NA): (1, 1)}
check("day-keyed exact match", sp.pick_from_map(P, dm, keyed_by_day=True) == (1, 1))
dm_other_day = {("2026-10-24", NH, NA): (5, 5)}
check("day-keyed ignores other days",
      sp.pick_from_map(P, dm_other_day, keyed_by_day=True) is None)
check("day-keyed fuzzy stays in-day",
      sp.pick_from_map(P, {("2026-10-25", NH, "chelsea fc"): (0, 2)},
                       keyed_by_day=True) == (0, 2))
# a same-day *and* other-day entry: same-day must win
dm_both = {("2026-10-24", NH, NA): (5, 5), (DAY, NH, NA): (1, 1)}
check("same-day entry preferred over other-day",
      sp.pick_from_map(P, dm_both, keyed_by_day=True) == (1, 1))

# ── 2. resolve_score: strict source order, per-source exactness ───────────
bulk = {(NH, NA): (2, 1)}
espn_id = {"401234": (9, 9)}
espn_names = {(DAY, NH, NA): (0, 0)}
check("football-data bulk wins when present",
      sp.resolve_score(P, bulk, espn_id, espn_names) == ((2, 1), "football-data.org"))
check("ESPN by-id used when bulk empty",
      sp.resolve_score(P, {}, espn_id, espn_names) == ((9, 9), "espn"))
check("ESPN by-names last",
      sp.resolve_score(P, {}, {}, espn_names) == ((0, 0), "espn"))
check("nothing anywhere -> (None, None)",
      sp.resolve_score(P, {}, {}, {}) == (None, None))

# non-ESPN rows must NOT consult the ESPN by-id map (ids aren't shared)
p_fd = dict(P, match_id="fdorg_88", league_slug="premier-league")
check("fdorg row never reads ESPN id map",
      sp.resolve_score(p_fd, {}, espn_id, {}) == (None, None))
check("fdorg row still settles via names",
      sp.resolve_score(p_fd, {}, {}, espn_names) == ((0, 0), "espn"))

# ── 3. espn_league_of: cups on ESPN only, big leagues as fd.org-first ─────
check("europa cup -> ESPN slug",
      sp.espn_league_of({"league_slug": "europa-league", "match_id": "fdorg_1"})
      == "uefa.europa")
check("conference league uses the real ESPN spelling",
      sp.espn_league_of({"league_slug": "conference-league", "match_id": "x"})
      == "uefa.europa.conf")
check("libertadores -> ESPN slug (note ESPN spells it CONMEBOL.*)",
      sp.espn_league_of({"league_slug": "copa-libertadores", "match_id": "x"})
      == "CONMEBOL.libertadores")
check("espn_ row in an unmapped league -> None (id map still settles it)",
      sp.espn_league_of({"league_slug": "obscure-cup", "match_id": "espn_5"})
      is None)
check("premier-league fdorg row -> None (fd.org is primary)",
      sp.espn_league_of({"league_slug": "premier-league", "match_id": "fdorg_1"})
      is None)
check("espn_ row in premier-league -> ESPN slug (id source)",
      sp.espn_league_of({"league_slug": "premier-league", "match_id": "espn_5"})
      == "eng.1")

# ── 4. _hours_past: tz-aware, tz-naive, garbage ───────────────────────────
now = datetime.now(timezone.utc)
recent = (now - timedelta(hours=3)).isoformat()
h = sp._hours_past(recent)
check("_hours_past ~3h", h is not None and 2.9 <= h <= 3.1, f"h={h}")
check("Z suffix handled", sp._hours_past("2026-10-26T12:00:00Z") is not None)
check("+00:00 handled", sp._hours_past("2026-10-26T12:00:00+00:00") is not None)
check("naive ISO treated as UTC",
      sp._hours_past("2026-10-26T12:00:00") is not None)
check("None -> None", sp._hours_past(None) is None)
check("garbage -> None", sp._hours_past("yesterday") is None)
check("empty -> None", sp._hours_past("") is None)

# ── 5. result_label consistency with the writer's expectations ────────────
check("2-1 is Home Win", result_label(2, 1) == "Home Win")
check("1-1 is Draw", result_label(1, 1) == "Draw")
check("0-2 is Away Win", result_label(0, 2) == "Away Win")

# ── 6. alert threshold is sane ────────────────────────────────────────────
check("ALERT_AFTER_HOURS == 6 (extra time + feed lag, same day)",
      sp.ALERT_AFTER_HOURS == 6)

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)

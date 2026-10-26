"""Checks for live-window enrichment (IMPROVEMENT_PLAN Step 2.2).

What silently broke live enrichment before:

  1. enrich_match_details skips any row that already has stats.details — and
     every row gets details.market/details.weather within minutes — so
     lineups / goals / cards / stats were never fetched, for any fixture,
     ever. live_enricher.py owns the time window instead.
  2. _parse_summary keyed team_stats by the ESPN *name* (possessionPct,
     shotsOnTarget, wonCorners…) while the detail modal reads label keys
     (possession, on_goal, corner_kicks…) — every stat row rendered "—".
  3. team_stats/lineups iteration order was ESPN's, not fixture home-first,
     so the modal's home | away columns could be swapped.

Everything here is tiny synthetic payloads — no network, no Supabase.

Run:  .venv/bin/python ai/experimental/test_live_feed.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

# enrich_match_details raises at import time without Supabase config, and
# live_enricher loads .env on import — set harmless dummies FIRST so the pure
# helpers import identically on dev machines and CI.
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)

import enrich_match_details as emd  # noqa: E402
import live_enricher as le  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


NOW = datetime(2026, 10, 26, 14, 0, 0, tzinfo=timezone.utc)
KICKOFF = "2026-10-26T15:00:00Z"


def at(h, m, s=0):
    return datetime(2026, 10, 26, h, m, s, tzinfo=timezone.utc)


# ── 1. clock / time parsing ────────────────────────────────────────────────
clock_67 = le.parse_minute("67'")
check("minute 67'", clock_67 == 67, f"got={clock_67}")
check("minute 45'+1' keeps base", le.parse_minute("45'+1'") == 45)
check("minute 90'+9' keeps base", le.parse_minute("90'+9'") == 90)
check("empty clock -> None", le.parse_minute("") is None)
check("None clock -> None", le.parse_minute(None) is None)
check("non-string clock -> None", le.parse_minute(67) is None)

check("ISO Z parsed to aware UTC",
      le.parse_iso(KICKOFF) == datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc))
check("naive ISO treated as UTC",
      le.parse_iso("2026-10-26T15:00:00") == datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc))
check("garbage ISO -> None", le.parse_iso("not-a-time") is None)
check("None ISO -> None", le.parse_iso(None) is None)

# ── 2. scoreboard event -> live state ──────────────────────────────────────
ev_pre = {
    "status": {"displayClock": "0'",
               "type": {"name": "STATUS_SCHEDULED", "state": "pre", "completed": False}},
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "0"},
        {"homeAway": "away", "score": "0"},
    ]}],
}
st = le.event_state(ev_pre)
check("pre state from type.state", st == {"status": "pre", "minute": 0,
                                           "score": {"home": 0, "away": 0}}, str(st))

ev_in = {
    "status": {"clock": 4020.0, "displayClock": "67'",
               "type": {"name": "STATUS_SECOND_HALF", "state": "in", "completed": False}},
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "1"},
        {"homeAway": "away", "score": "0"},
    ]}],
}
st = le.event_state(ev_in)
check("in-play state + score", st == {"status": "in", "minute": 67,
                                      "score": {"home": 1, "away": 0}}, str(st))

ev_post = {
    # No top-level state: falls back to type.name.
    "status": {"type": {"name": "STATUS_FULL_TIME", "completed": True}},
    "competitions": [{"competitors": [
        {"homeAway": "home", "score": "2"},
        {"homeAway": "away", "score": "1"},
    ]}],
}
st = le.event_state(ev_post)
check("post state from FULL_TIME name", st == {"status": "post", "minute": None,
                                               "score": {"home": 2, "away": 1}}, str(st))

ev_clock_only = {"status": {"clock": 2700.0, "type": {"name": "STATUS_FIRST_HALF"}},
                 "competitions": [{"competitors": [
                     {"homeAway": "home", "score": "0"},
                     {"homeAway": "away", "score": "0"}]}]}
st = le.event_state(ev_clock_only)
check("minute falls back to clock/60", st and st["minute"] == 45, str(st))

check("None event -> None", le.event_state(None) is None)
check("unrecognised status -> None", le.event_state({"status": {"type": {"name": "?"}}}) is None)

# ── 3. enrichment window  [kickoff-2h, kickoff+4h] ─────────────────────────
check("1h before kickoff is in window", le.in_window(KICKOFF, at(14, 0)) is True)
check("1h59m before kickoff is in window", le.in_window(KICKOFF, at(13, 1)) is True)
check("2h1m before kickoff is out", le.in_window(KICKOFF, at(12, 59)) is False)
check("exactly kickoff+4h is in window", le.in_window(KICKOFF, at(19, 0)) is True)
check("kickoff+4h1m is out", le.in_window(KICKOFF, at(19, 1)) is False)
check("missing kickoff is out", le.in_window("", NOW) is False)

# ── 4. summary-fetch policy ────────────────────────────────────────────────
full = {"team_stats": {"A": {}, "B": {}}, "lineups": {"A": {}, "B": {}},
        "scorers": [{"player": "x"}]}
check("in play always fetches", le.should_fetch_summary({"status": "in"}, KICKOFF, NOW, full) is True)
check("pre 2h out skips summary", le.should_fetch_summary({"status": "pre"}, KICKOFF, at(13, 0), {}) is False)
check("pre within 80 min fetches", le.should_fetch_summary({"status": "pre"}, KICKOFF, at(13, 50), {}) is True)
check("pre without kickoff fetches (safe)", le.should_fetch_summary({"status": "pre"}, "", NOW, {}) is True)
check("post with final data stored skips",
      le.should_fetch_summary({"status": "post"}, KICKOFF, NOW, full) is False)
check("post without team_stats fetches",
      le.should_fetch_summary({"status": "post"}, KICKOFF, NOW,
                              {"lineups": {"A": {}}, "scorers": []}) is True)

# ── 5. merge + write decision ──────────────────────────────────────────────
existing = {
    "market": {"probs": {"home": 0.5}},
    "weather": {"temp_c": 12.0},
    "lineups": {"Old FC": {"starting": [{"name": "Stale"}], "bench": []}},
    "live_state": {"status": "pre", "minute": None, "score": None,
                   "as_of": "2026-10-26T13:30:00Z"},
}
parsed = {
    "lineups": {"New FC": {"starting": [{"name": "Fresh"}], "bench": []}},
    "team_stats": {"New FC": {"possession": "61%"}},
    "venue": None,  # empty values must not clobber anything
}
state_in = {"status": "in", "minute": 67, "score": {"home": 1, "away": 0}}
merged = le.merge_details(existing, parsed, state_in, "2026-10-26T14:00:00Z")
check("market preserved", merged.get("market") == existing["market"])
check("weather preserved", merged.get("weather") == existing["weather"])
check("lineups replaced by fresh copy", list(merged["lineups"]) == ["New FC"])
check("team_stats written", merged["team_stats"] == {"New FC": {"possession": "61%"}})
check("empty parsed value skipped", "venue" not in merged)
check("live_state stamped", merged["live_state"] == {
    "status": "in", "minute": 67, "score": {"home": 1, "away": 0},
    "as_of": "2026-10-26T14:00:00Z"}, str(merged["live_state"]))

check("real change -> write", le.needs_write(existing, merged) is True)

# as_of freshness stamps alone must never count as a change.
fresh_old = {
    "market": {"probs": {"home": 0.5}},
    "live_state": {"status": "in", "minute": 67, "score": {"home": 1, "away": 0},
                   "as_of": "2026-10-26T13:59:00Z"},
}
fresh_new = le.merge_details(fresh_old, {}, state_in, "2026-10-26T14:00:00Z")
check("as_of-only difference -> no write", le.needs_write(fresh_old, fresh_new) is False)
check("minute tick -> write", le.needs_write(fresh_old, le.merge_details(
    fresh_old, {}, {"status": "in", "minute": 68, "score": {"home": 1, "away": 0}},
    "2026-10-26T14:00:00Z")) is True)
check("score change -> write", le.needs_write(fresh_old, le.merge_details(
    fresh_old, {}, {"status": "in", "minute": 67, "score": {"home": 2, "away": 0}},
    "2026-10-26T14:00:00Z")) is True)
check("first ever write (no details) -> write",
      le.needs_write({}, le.merge_details({}, {}, state_in, "2026-10-26T14:00:00Z")) is True)

# ── 6. _parse_summary: the modal's data contract ───────────────────────────
def roster(side, team, starters=11, bench=2):
    players = [
        {"starter": True, "jersey": str(i), "position": {"abbreviation": "MF"},
         "athlete": {"fullName": f"Starter {i}"}}
        for i in range(starters)
    ]
    players += [
        {"starter": False, "jersey": str(90 + i), "position": {"abbreviation": "FW"},
         "athlete": {"fullName": f"Bench {i}"}}
        for i in range(bench)
    ]
    return {"homeAway": side, "team": {"displayName": team}, "roster": players,
            "formation": "4-3-3"}


def stat(label, name, value):
    return {"label": label, "name": name, "displayValue": value}


# Real ESPN label/name pairs from the Newcastle @ Crystal Palace summary.
HOME_STATS = [
    stat("Possession", "possessionPct", "52.6"),
    stat("SHOTS", "totalShots", "17"),
    stat("ON GOAL", "shotsOnTarget", "4"),
    stat("Offsides", "offsides", "4"),
    stat("Corner Kicks", "wonCorners", "7"),
    stat("Fouls", "foulsCommitted", "12"),
    stat("Yellow Cards", "yellowCards", "3"),
    stat("Saves", "saves", "0"),
]
AWAY_STATS = [
    stat("Possession", "possessionPct", "47.4"),
    stat("SHOTS", "totalShots", "11"),
    stat("ON GOAL", "shotsOnTarget", "6"),
    stat("Offsides", "offsides", "1"),
    stat("Corner Kicks", "wonCorners", "3"),
    stat("Fouls", "foulsCommitted", "10"),
    stat("Yellow Cards", "yellowCards", "3"),
    stat("Saves", "saves", "4"),
]

summary = {
    "gameInfo": {"venue": {"fullName": "Selhurst Park",
                           "address": {"city": "London", "country": "England"}},
                 "attendance": 25101},
    "header": {"competitions": [{"details": [
        {"type": {"id": "17"}, "displayName": "Referee Name"}]}]},
    "keyEvents": [
        {"type": {"type": "kickoff"}, "scoringPlay": False,
         "clock": {"displayValue": ""}, "text": "First Half begins."},
        {"type": {"type": "yellow-card"}, "scoringPlay": False,
         "clock": {"displayValue": "36'"}, "team": {"displayName": "Newcastle United"},
         "text": "Nico González (Newcastle United) is shown the yellow card for a bad foul."},
        {"type": {"type": "goal"}, "scoringPlay": True,
         "clock": {"displayValue": "47'"}, "team": {"displayName": "Newcastle United"},
         "text": "Goal! Crystal Palace 0, Newcastle United 1. William Osula "
                 "(Newcastle United) left footed shot from very close range."},
        {"type": {"type": "substitution"}, "scoringPlay": False,
         "clock": {"displayValue": "62'"}, "team": {"displayName": "Crystal Palace"},
         "text": "Substitution, Crystal Palace. Eddie Nketiah replaces Jean-Philippe Mateta."},
    ],
    # AWAY listed first on purpose — parser must put home first.
    "rosters": [
        roster("away", "Newcastle United"),
        roster("home", "Crystal Palace"),
    ],
    "boxscore": {"teams": [
        {"homeAway": "away", "team": {"displayName": "Newcastle United"},
         "statistics": list(reversed(AWAY_STATS))},
        {"homeAway": "home", "team": {"displayName": "Crystal Palace"},
         "statistics": HOME_STATS},
    ]},
}

d = emd._parse_summary(summary)

check("team_stats home first (modal home|away columns)",
      list(d.get("team_stats", {}))[0] == "Crystal Palace", str(list(d.get("team_stats", {}))))
check("lineups home first",
      list(d.get("lineups", {}))[0] == "Crystal Palace", str(list(d.get("lineups", {}))))

# The exact keys the modal's Match Statistics rows read, both sides.
UI_STAT_KEYS = ["possession", "shots", "on_goal", "offsides",
                "corner_kicks", "fouls", "yellow_cards", "saves"]
for side in ("Crystal Palace", "Newcastle United"):
    missing = [k for k in UI_STAT_KEYS if k not in d["team_stats"][side]]
    check(f"modal stat keys present for {side}", not missing, f"missing={missing}")
check("possession value kept", d["team_stats"]["Crystal Palace"]["possession"] == "52.6")
check("corner_kicks from reversed list", d["team_stats"]["Newcastle United"]["corner_kicks"] == "3")

lu = d["lineups"]["Crystal Palace"]
check("11 starters in lineups", len(lu["starting"]) == 11, f"got={len(lu['starting'])}")
check("bench separated", len(lu["bench"]) == 2, f"got={len(lu['bench'])}")

check("goal scorer parsed", d["scorers"] == [{
    "player": "William Osula", "team": "Newcastle United",
    "minute": "47'", "type": "goal"}], str(d.get("scorers")))
check("card parsed with label type",
      d["cards"] == [{"player": "Nico González", "team": "Newcastle United",
                      "minute": "36'", "type": "yellow_card"}], str(d.get("cards")))
check("substitution parsed both ways",
      d["substitutions"] == [{"player_in": "Eddie Nketiah",
                              "player_out": "Jean-Philippe Mateta",
                              "team": "Crystal Palace", "minute": "62'"}],
      str(d.get("substitutions")))
check("venue parsed", d.get("venue") == "Selhurst Park")
check("referee parsed", d.get("referee") == "Referee Name")

# Empty summary must not invent anything (pre-lineup fetches are common).
empty = emd._parse_summary({})
check("empty summary -> no lineups", "lineups" not in empty)
check("empty summary -> no team_stats", "team_stats" not in empty)
check("empty summary -> no scorers", "scorers" not in empty)

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)

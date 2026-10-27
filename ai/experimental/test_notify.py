"""Checks for web push notifications (IMPROVEMENT_PLAN Step 3.2).

What silently breaks notifications:

  1. Double-sends — every run must consult the dedupe state
     (app_settings.notify_state) and only send unseen keys; the
     new_keys/mark_sent pair is the whole contract.
  2. Message quality — a push with an empty body or missing title is
     worse than no push (users disable the site). All three kinds must
     produce title+body+tag.
  3. Dead subscriptions — browsers unsubscribe silently; 404/410 rows
     must be purged so they never block future sends.

Tiny synthetic payloads only — no network, no Supabase.

Run:  .venv/bin/python ai/experimental/test_notify.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-key")
os.environ.setdefault("VAPID_PRIVATE_KEY", "test-key")

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)

import notify  # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


# ── 1. dedupe: new_keys / mark_sent ───────────────────────────────────────
state = {}
keys = ["a", "b", "c"]
check("fresh state -> all keys new", notify.new_keys("picks", keys, state) == keys)
s1 = notify.mark_sent("picks", ["a"], state)
check("after marking a -> b,c new",
      notify.new_keys("picks", keys, s1) == ["b", "c"])
check("mark_sent returns a NEW state (input untouched)", "picks" not in state)
check("already-sent keys stay sent",
      notify.new_keys("picks", ["a"], s1) == [])
check("kinds are independent",
      notify.new_keys("kickoff", keys, s1) == keys)
check("mark_sent stamps last_run_at", "last_run_at" in s1["picks"])
NOW = dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=dt.timezone.utc)
s2 = notify.mark_sent("settled", ["x"], s1, now=NOW)
check("mark_sent honours an injected clock",
      s2["settled"]["last_run_at"] == "2026-10-26T12:00:00Z")
check("empty key list still stamps the run",
      "last_run_at" in notify.mark_sent("picks", [], s2, now=NOW)["picks"])
# rolling cap: old keys drop off, newest stay
fat = notify.mark_sent("picks", [f"k{i}" for i in range(notify.MAX_SENT_KEYS + 50)], {})
check("sent list capped at MAX_SENT_KEYS",
      len(fat["picks"]["sent"]) == notify.MAX_SENT_KEYS)
check("newest keys survive the cap",
      f"k{notify.MAX_SENT_KEYS + 49}" in fat["picks"]["sent"])

# ── 2. message builders: title+body+tag always present ────────────────────
km = notify.kickoff_message(
    {"home_team": "Arsenal", "away_team": "Chelsea", "prediction": "Home Win",
     "confidence": 72, "match_id": "espn_1"}, 27)
check("kickoff title has the minutes", "27 min" in km["title"], km["title"])
check("kickoff body names teams + pick",
      "Arsenal vs Chelsea" in km["body"] and "Home Win" in km["body"], km["body"])
check("kickoff tag is per-match (dedupe-safe)", km["tag"] == "kickoff:espn_1")
check("kickoff message complete",
      all(km.get(f) for f in ("title", "body", "tag", "url")))

km0 = notify.kickoff_message(
    {"home_team": "A", "away_team": "B", "prediction": "Draw"}, 6)
check("kickoff without confidence omits the %",
      "%" not in km0["body"], km0["body"])

pm = notify.picks_message(
    [{"home_team": "Arsenal", "away_team": "Chelsea", "prediction": "Draw",
      "confidence": 61}])
check("single pick title is singular", pm["title"] == "New AI Pick", pm["title"])
check("single pick body has both teams + pick + conf",
      "Arsenal vs Chelsea" in pm["body"] and "61%" in pm["body"], pm["body"])

pm5 = notify.picks_message(
    [{"home_team": f"Team{i}", "away_team": "Rival"} for i in range(5)])
check("multi pick title is plural", pm5["title"] == "New AI Picks", pm5["title"])
check("multi pick body counts + caps at 3 + ellipsis",
      "5 new AI Pick(s)" in pm5["body"] and "…" in pm5["body"], pm5["body"])
check("multi pick tag groups the batch (one push for all)",
      pm5["tag"] == "picks")

sm = notify.settled_message(
    {"home_team": "Arsenal", "away_team": "Chelsea", "prediction": "Home Win",
     "actual_result": "Home Win", "home_score": 2, "away_score": 0,
     "match_id": "espn_9"})
check("correct settle celebrated", "✅" in sm["title"], sm["title"])
check("settle body shows the real scoreline", "2-0" in sm["body"], sm["body"])
sm2 = notify.settled_message(
    {"home_team": "A", "away_team": "B", "prediction": "Home Win",
     "actual_result": "Draw", "home_score": 1, "away_score": 1})
check("missed settle marked", "❌" in sm2["title"], sm2["title"])

# ── 3. lead window constants are sane ─────────────────────────────────────
check("kickoff lead is 25 min (matches the plan's '30min before')",
      notify.KICKOFF_LEAD_MINUTES == 25)
check("under-way guard is positive",
      0 < notify.KICKOFF_MIN_MINUTES < notify.KICKOFF_LEAD_MINUTES)

# ── 4. sender refuses without a key (no silent fake-delivery) ─────────────
orig = notify.VAPID_PRIVATE_KEY
notify.VAPID_PRIVATE_KEY = ""
ok_count, dead = notify.send_payload(
    [{"endpoint": "e", "p256dh": "p", "auth": "a"}], {"title": "t"}, dry_run=False)
check("no VAPID key -> zero delivered", ok_count == 0)
notify.VAPID_PRIVATE_KEY = orig

# dry-run must never touch the network
ok_count, dead = notify.send_payload(
    [], {"title": "t"}, dry_run=True)
check("dry-run with no subs is a clean no-op", ok_count == 0 and dead == [])

# ── 5. gathering: window bounds + jsonb AI-Pick filter ─────────────────────
# These guard two real bugs found by running against production:
#   a) gather_kickoff queried only `gte` — fixtures days away got alerted;
#   b) `is_ai_pick=eq.true` 400'd because the flag is jsonb, not a column.
check("is_ai_pick reads the stats blob",
      notify.is_ai_pick({"stats": {"is_ai_pick": True}}) is True)
check("is_ai_pick false without the flag",
      notify.is_ai_pick({"stats": {}}) is False)
check("is_ai_pick survives a missing stats blob",
      notify.is_ai_pick({}) is False and notify.is_ai_pick({"stats": None}) is False)

FAKE_NOW = dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=dt.timezone.utc)


def _iso(minutes: int) -> str:
    return (FAKE_NOW + dt.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


_rows: list[dict] = []
_orig_rest = notify._rest


def _fake_rest(method, path, payload=None, params=None):
    return _rows


def _clock(now):  # freeze gather_kickoff's clock
    class _M(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    return _M


notify._rest = _fake_rest
_orig_dt_now = notify.dt.datetime
try:
    # kickoff: far-future row must be dropped, in-window row kept
    _rows = [
        {"match_id": "far", "home_team": "A", "away_team": "B",
         "prediction": "Draw", "confidence": 50, "kickoff_time": _iso(6250)},
        {"match_id": "soon", "home_team": "C", "away_team": "D",
         "prediction": "Home Win", "confidence": 70, "kickoff_time": _iso(10)},
        {"match_id": "underway", "home_team": "E", "away_team": "F",
         "prediction": "Draw", "confidence": 40, "kickoff_time": _iso(2)},
    ]
    notify.dt.datetime = _clock(FAKE_NOW)
    got = notify.gather_kickoff(30)
    check("kickoff window drops days-away fixtures (upper bound)",
          [k for k, _ in got] == ["kickoff:soon"], str([k for k, _ in got]))
    check("kickoff window drops under-way fixtures (lower bound)",
          all(k != "kickoff:underway" for k, _ in got))
    check("kickoff minutes computed against the frozen clock",
          "10 min" in got[0][1]["title"], got[0][1]["title"])

    # picks: jsonb filter, grouped into a single payload
    _rows = [
        {"match_id": "ai1", "home_team": "Arsenal", "away_team": "Chelsea",
         "prediction": "Draw", "confidence": 70, "stats": {"is_ai_pick": True}},
        {"match_id": "plain", "home_team": "A", "away_team": "B",
         "prediction": "Draw", "confidence": 30, "stats": {}},
        {"match_id": "ai2", "home_team": "Inter", "away_team": "Juve",
         "prediction": "Home Win", "confidence": 80, "stats": {"is_ai_pick": True}},
    ]
    got = notify.gather_picks(None)
    check("picks: only stats.is_ai_pick rows are announced",
          len(got) == 1 and got[0][0] == "picks:ai1,ai2", str(got[0][0] if got else None))
    _rows = []
    check("picks: empty result is an empty list (no empty push)",
          notify.gather_picks(None) == [])

    # settled: only correct AI Picks are celebrated
    def _row(mid, hit, ai):
        return {"match_id": mid, "home_team": "A", "away_team": "B",
                "prediction": "Home Win",
                "actual_result": "Home Win" if hit else "Draw",
                "home_score": 1, "away_score": 0,
                "stats": {"is_ai_pick": ai}}
    _rows = [_row("hit-ai", True, True), _row("miss-ai", False, True),
             _row("hit-plain", True, False)]
    got = notify.gather_settled(None)
    check("settled: correct AI Picks only (no misses, no non-AI)",
          [k for k, _ in got] == ["settled:hit-ai"], str([k for k, _ in got]))
finally:
    notify._rest = _orig_rest
    notify.dt.datetime = _orig_dt_now

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)

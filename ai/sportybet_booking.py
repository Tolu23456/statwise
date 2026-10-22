#!/usr/bin/env python3
"""
SportyBet Nigeria booking-code generator.

Builds ONE accumulator from the AI Picks and asks SportyBet for a real
booking code, then stores it in Supabase so the app can display it.

How it works
------------
SportyBet's public API sits behind CloudFront, so a bare HTTP client is
rejected.  A headless Chromium session is opened once to obtain the site's
cookies, after which Playwright's request context (which shares those cookies)
talks to the JSON API directly:

  1. GET  /api/ng/factsCenter/pcUpcomingEvents  -> eventId per fixture
  2. POST /api/ng/orders/share                  -> {"shareCode": "VXN2NB"}

Molten DOM selectors are deliberately avoided: the only browser work is
loading the homepage for its cookies.

Usage:
    python ai/sportybet_booking.py            # generate + store
    python ai/sportybet_booking.py --dry-run  # print, do not store

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY (repo-root .env).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AI_DIR = os.path.dirname(os.path.abspath(__file__))
if AI_DIR not in sys.path:
    sys.path.insert(0, AI_DIR)

from env_loader import load_dotenv  # noqa: E402

load_dotenv()

try:                                        # optional dependency
    from playwright.sync_api import sync_playwright
except ImportError:                         # pragma: no cover
    sync_playwright = None

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://fwpriiugfjhtoymcbmso.supabase.co")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_ANON_KEY", ""))

BOOKMAKER = "sportybet-ng"
BASE = "https://www.sportybet.com"
EVENTS_URL = f"{BASE}/api/ng/factsCenter/pcUpcomingEvents"
SHARE_URL = f"{BASE}/api/ng/orders/share"
LOAD_URL = "https://www.sportybet.com/ng/?shareCode={code}"

# SportyBet's 1X2 outcome ids.
OUTCOME_BY_PICK = {"Home Win": "1", "Draw": "2", "Away Win": "3"}


# ── Supabase helpers ─────────────────────────────────────────────────────────

def _rest(method: str, path: str, payload=None, prefer: str | None = None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=60) as r:
        body = r.read().decode()
        return json.loads(body) if body.strip() else None


def get_ai_picks() -> list[dict]:
    """Upcoming AI Picks, soonest first."""
    try:
        rows = _rest(
            "GET",
            "predictions?select=*&order=kickoff_time.asc&limit=200",
        ) or []
    except Exception as e:
        print(f"Could not read predictions: {e}")
        return []

    now = datetime.now(timezone.utc)
    picks = []
    for p in rows:
        stats = p.get("stats") or {}
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except Exception:
                stats = {}
        if not stats.get("is_ai_pick"):
            continue
        if (p.get("status") or "upcoming") != "upcoming":
            continue
        kick = p.get("kickoff_time")
        if kick:
            try:
                when = datetime.fromisoformat(str(kick).replace("Z", "+00:00"))
                if when <= now:
                    continue
            except Exception:
                pass
        picks.append(p)
    return picks


# ── Team-name reconciliation ─────────────────────────────────────────────────

def _norm(name: str) -> str:
    s = (name or "").lower()
    for suffix in (" fc", " cf", " sc", " afc", " united", " city", " club"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return " ".join(s.replace("-", " ").split())


def _similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    score = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        score = max(score, 0.9)
    return score


def _match_event(pick: dict, events: list[dict]):
    """Find the SportyBet event for one of our fixtures.

    Names are matched fuzzily and kickoff times must be within 18 hours, so a
    fixture between similarly named clubs in another competition cannot win.
    """
    home, away = pick.get("home_team", ""), pick.get("away_team", "")
    try:
        kick = datetime.fromisoformat(
            str(pick.get("kickoff_time", "")).replace("Z", "+00:00"))
    except Exception:
        kick = None

    best, best_score = None, 0.0
    for ev in events:
        if (ev.get("matchStatus") or "").lower() not in ("not start", ""):
            continue
        h = _similarity(home, ev.get("homeTeamName", ""))
        a = _similarity(away, ev.get("awayTeamName", ""))
        if h < 0.7 or a < 0.7:
            continue
        score = (h + a) / 2
        if kick and ev.get("estimateStartTime"):
            try:
                ev_dt = datetime.fromtimestamp(
                    ev["estimateStartTime"] / 1000, tz=timezone.utc)
                if abs((ev_dt - kick).total_seconds()) > 18 * 3600:
                    continue
                score += 0.05
            except Exception:
                pass
        if score > best_score:
            best, best_score = ev, score
    return best


# ── SportyBet API ────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{BASE}/ng/sport/football",
        "Origin": BASE,
    }


def fetch_events(ctx) -> list[dict]:
    """All upcoming football events, paging until exhausted."""
    events: list[dict] = []
    seen: set[str] = set()
    for page in range(1, 41):
        qs = urlencode({
            "sportId": "sr:sport:1",
            "marketId": "1",
            "pageSize": 100,
            "pageNum": page,
            "option": 1,
        })
        r = ctx.request.get(f"{EVENTS_URL}?{qs}", headers=_headers(), timeout=45000)
        if r.status != 200:
            print(f"events page {page}: HTTP {r.status}")
            break
        try:
            data = r.json().get("data") or {}
        except Exception as e:
            print(f"events page {page}: bad JSON ({e})")
            break
        added = 0
        for tour in data.get("tournaments") or []:
            for ev in tour.get("events") or []:
                eid = ev.get("eventId")
                if eid and eid not in seen:
                    seen.add(eid)
                    ev["_tournament"] = tour.get("name")
                    events.append(ev)
                    added += 1
        total = int(data.get("totalNum") or 0)
        if added == 0 or (total and len(events) >= total):
            break
        time.sleep(0.2)
    print(f"SportyBet events available: {len(events)}")
    return events


def create_share_code(ctx, selections: list[dict]) -> dict:
    r = ctx.request.post(
        SHARE_URL,
        headers={**_headers(), "Content-Type": "application/json"},
        data=json.dumps({"selections": selections}),
        timeout=45000,
    )
    if r.status != 200:
        raise RuntimeError(f"share endpoint HTTP {r.status}: {r.text()[:200]}")
    body = r.json()
    if body.get("bizCode") != 10000:
        raise RuntimeError(f"share rejected: {body.get('message') or body}")
    return body.get("data") or {}


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv

    picks = get_ai_picks()
    if not picks:
        print("No upcoming AI Picks — nothing to book.")
        return 0
    print(f"AI Picks: {len(picks)}")
    for p in picks:
        print(f"  • {p.get('match_title')} -> {p.get('prediction')}")

    if sync_playwright is None:
        print("Playwright is not installed; cannot reach SportyBet.")
        return 2

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
            locale="en-NG",
        )
        try:
            # Cookies/session are established by loading the real page once.
            page = ctx.new_page()
            page.goto(f"{BASE}/ng/sport/football",
                      wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)

            events = fetch_events(ctx)
            if not events:
                print("SportyBet returned no events (blocked?).")
                return 2

            selections, mapped, skipped = [], [], []
            for p in picks:
                ev = _match_event(p, events)
                outcome = OUTCOME_BY_PICK.get(p.get("prediction"))
                if not ev or not outcome:
                    skipped.append(p.get("match_title") or "?")
                    continue
                market = None
                for m in ev.get("markets") or []:
                    if str(m.get("id")) == "1":
                        market = m
                        break
                if not market:
                    skipped.append(p.get("match_title") or "?")
                    continue
                if any(str(o.get("id")) == outcome for o in market.get("outcomes") or []):
                    selections.append({
                        "eventId": ev["eventId"],
                        "marketId": "1",
                        "specifier": None,
                        "outcomeId": outcome,
                    })
                    mapped.append({
                        "match": p.get("match_title"),
                        "pick": p.get("prediction"),
                        "eventId": ev["eventId"],
                        "home": ev.get("homeTeamName"),
                        "away": ev.get("awayTeamName"),
                        "tournament": ev.get("_tournament"),
                        "kickoff": (datetime.fromtimestamp(
                            ev["estimateStartTime"] / 1000, tz=timezone.utc).isoformat()
                            if ev.get("estimateStartTime") else None),
                        "odds": next((o.get("odds") for o in market["outcomes"]
                                      if str(o.get("id")) == outcome), None),
                    })

            print(f"\nmatched on SportyBet: {len(selections)}/{len(picks)}")
            for s in mapped:
                print(f"  ✓ {s['match']} [{s['pick']}] "
                      f"({s['home']} vs {s['away']}) @ {s['odds']}")
            for s in skipped:
                print(f"  ✗ not found: {s}")

            if not selections:
                print("No selections could be mapped — no code generated.")
                return 0

            data = create_share_code(ctx, selections)
            code = data.get("shareCode")
            if not code:
                print("SportyBet did not return a share code.")
                return 2
            share_url = data.get("shareURL") or LOAD_URL.format(code=code)
            deadline = data.get("deadline")
            expires_at = None
            if deadline:
                try:
                    expires_at = datetime.fromtimestamp(
                        deadline / 1000, tz=timezone.utc).isoformat()
                except Exception:
                    expires_at = None

            print(f"\nBOOKING CODE: {code}")
            print(f"Load: {share_url}")

            if dry_run:
                return 0

            # Expire previous codes, then publish the fresh one.
            try:
                _rest("PATCH",
                      f"prediction_codes?bookmaker=eq.{BOOKMAKER}&status=eq.active",
                      {"status": "expired"})
            except Exception as e:
                print(f"Could not expire old codes: {e}")

            tier_order = ["free", "premium", "vip", "vvip"]
            tiers = [p.get("tier") or "free" for p in picks]
            tier = max(tiers, key=lambda t: tier_order.index(t)
                       if t in tier_order else 0)

            row = {
                "code": code,
                "bookmaker": BOOKMAKER,
                "league_name": f"{len(selections)}-fold accumulator",
                "market": "1X2",
                "pick": f"{len(selections)} AI Picks",
                "confidence": max((int(p.get("confidence") or 0) for p in picks),
                                  default=None),
                "notes": json.dumps({
                    "share_url": share_url,
                    "selections": mapped,
                }),
                "tier": tier,
                "status": "active",
                "usage_limit": None,
                "usage_count": 0,
                "expires_at": expires_at,
            }
            _rest("POST", "prediction_codes", row, prefer="return=minimal")
            print(f"Stored booking code {code} in Supabase.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

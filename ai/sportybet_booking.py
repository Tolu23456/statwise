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

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
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

# ── Pure helpers (unit tested in ai/experimental/test_booking.py) ───────────
#
# Plan step 2.3: retry, caching, friendly errors, fallback and logging all
# hang off these five functions, so they live free of I/O.

#: SportyBet share codes are regenerated when fewer than this remains on them.
REGENERATE_MARGIN_HOURS = 6


def selection_signature(mapped: list[dict]) -> str:
    """Stable fingerprint of WHAT the accumulator contains.

    Two runs that book the same SportyBet eventIds with the same outcomeIds
    produce the same signature regardless of order — that's what lets the
    cache reuse an existing code instead of churning a fresh one every cycle.
    """
    parts = sorted(
        f"{s.get('eventId')}:{s.get('outcomeId')}" for s in (mapped or [])
    )
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def should_regenerate(payload: dict | None, signature: str,
                      now: datetime,
                      margin_hours: int = REGENERATE_MARGIN_HOURS) -> bool:
    """Can we reuse the stored booking code instead of asking SportyBet again?

    Reuse only when the pick set is identical AND the stored code's SportyBet
    deadline is comfortably in the future. Anything unknown (missing payload,
    missing signature, unparseable expiry) regenerates — a stale or wrong
    code is worse than an extra API call.
    """
    if not payload or not payload.get("code"):
        return True
    if payload.get("signature") != signature:
        return True
    expires_raw = payload.get("expires_at")
    if not expires_raw:
        return True
    try:
        expires = datetime.fromisoformat(str(expires_raw).replace("Z", "+00:00"))
    except ValueError:
        return True
    return now >= expires - timedelta(hours=margin_hours)


def friendly_error(kind: str, detail: str = "") -> str:
    """Map an internal failure kind to a user-facing sentence.

    The app shows these verbatim when the last generation attempt failed, so
    they must read like something a user can act on — not a stack trace.
    """
    base = {
        "no_picks": "No AI Picks are open right now — the next prediction cycle will try again.",
        "playwright": "Booking-code generation isn't available right now (browser runtime missing).",
        "blocked": "SportyBet refused our request (likely rate limiting). It usually clears within the hour.",
        "no_events": "SportyBet didn't return its fixture list. The copyable selections below stay current.",
        "no_match": "None of today's AI Picks were found on SportyBet. The copyable selections below stay current.",
        "share_rejected": "SportyBet rejected the accumulator. The copyable selections below stay current.",
        "no_code": "SportyBet answered but didn't include a booking code. Please try again later.",
        "publish": "The code was generated but couldn't be published. The next run will retry.",
    }.get(kind, "Booking-code generation failed. The copyable selections below stay current.")
    if detail:
        return f"{base} ({detail[:120]})"
    return base


def bump_stats(stats: dict | None, ok: bool, kind: str = "",
               code: str | None = None,
               now: datetime | None = None) -> dict:
    """Rolling success/failure counters stored in app_settings.

    Pure: returns the new stats dict. Written under
    setting_key='booking_code_stats' so success/failure rates can be read
    (and displayed) without a database.
    """
    out = dict(stats or {})
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    out["attempts"] = int(out.get("attempts") or 0) + 1
    if ok:
        out["successes"] = int(out.get("successes") or 0) + 1
        out["last_success_at"] = stamp
        if code:
            out["last_code"] = code
        out.pop("last_error", None)
        out.pop("last_error_at", None)
    else:
        out["failures"] = int(out.get("failures") or 0) + 1
        out["last_error"] = friendly_error(kind)
        out["last_error_at"] = stamp
    out["last_attempt_at"] = stamp
    return out


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
        # ── Audit C-5 ────────────────────────────────────────────────────
        # The published accumulator lands in app_settings, which the ANON
        # key can read (supabase-schema.sql GRANT SELECT ON app_settings TO
        # anon).  Every selection in it must therefore be FREE tier — as soon
        # as a premium/vip/vvip AI Pick enters the payload, the paywall's
        # core value is handed to anonymous users for free.  `tier` is
        # assigned by model/predictor.py (TIER_DB: 'free' | 'premium' |
        # 'vip' | 'vvip'); missing tier is treated as free.
        if (p.get("tier") or "free") != "free":
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


def load_stored_payload() -> dict | None:
    """The booking_code payload currently published in app_settings (cache)."""
    try:
        rows = _rest(
            "GET",
            "app_settings?select=setting_value&setting_key=eq.booking_code"
            "&is_active=true&limit=1",
        ) or []
    except Exception:
        return None
    if not rows:
        return None
    val = (rows[0] or {}).get("setting_value")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return None
    return val if isinstance(val, dict) else None


def write_stats(stats: dict, dry_run: bool = False) -> None:
    """Persist rolling success/failure counters (setting_key='booking_code_stats')."""
    if dry_run:
        return
    body = {
        "setting_key": "booking_code_stats",
        "setting_value": stats,
        "description": "SportyBet booking-code generation success/failure rates",
        "is_active": True,
    }
    try:
        updated = _rest("PATCH",
                        "app_settings?setting_key=eq.booking_code_stats",
                        body, prefer="return=representation")
        if not updated:
            _rest("POST", "app_settings", body, prefer="return=minimal")
    except Exception as e:
        print(f"Could not write booking stats: {e}")


def _load_stats() -> dict:
    """Current counters, or {} when never written."""
    try:
        rows = _rest(
            "GET",
            "app_settings?select=setting_value&setting_key=eq.booking_code_stats"
            "&limit=1",
        ) or []
    except Exception:
        return {}
    val = (rows[0] or {}).get("setting_value") if rows else None
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return {}
    return val if isinstance(val, dict) else {}


#: Last non-200 status seen while paging events (None = no HTTP failure).
#: Lets main() distinguish "blocked" (403/429) from "empty list".
_last_events_http: int | None = None


def fetch_events(ctx) -> list[dict]:
    """All upcoming football events, paging until exhausted (3 tries/page)."""
    global _last_events_http
    _last_events_http = None
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
        r = None
        for attempt in range(1, 4):
            try:
                r = ctx.request.get(f"{EVENTS_URL}?{qs}", headers=_headers(), timeout=45000)
            except Exception:
                time.sleep(attempt * 2)
                continue
            if r.status >= 500:
                time.sleep(attempt * 2)
                continue
            break
        if r is None or r.status != 200:
            _last_events_http = r.status if r is not None else 0
            print(f"events page {page}: HTTP {_last_events_http}")
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
    """POST the accumulator to SportyBet. Retries transient HTTP failures
    (5xx / network hiccups) before giving up — plan step 2.3."""
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            r = ctx.request.post(
                SHARE_URL,
                headers={**_headers(), "Content-Type": "application/json"},
                data=json.dumps({"selections": selections}),
                timeout=45000,
            )
        except Exception as e:                       # network hiccup
            last_err = e
            time.sleep(attempt * 2)
            continue
        if r.status >= 500:                          # transient server error
            last_err = RuntimeError(f"share endpoint HTTP {r.status}")
            time.sleep(attempt * 2)
            continue
        if r.status != 200:
            raise RuntimeError(f"share endpoint HTTP {r.status}: {r.text()[:200]}")
        body = r.json()
        if body.get("bizCode") != 10000:
            raise RuntimeError(f"share rejected: {body.get('message') or body}")
        return body.get("data") or {}
    raise RuntimeError(f"share endpoint unreachable after 3 attempts: {last_err}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    dry_run = "--dry-run" in argv
    now = datetime.now(timezone.utc)

    def fail(kind: str, detail: str = "", code: int = 2) -> int:
        """Log a user-friendly failure into the stats row, then exit."""
        print(f"FAIL[{kind}]: {friendly_error(kind, detail)}")
        if not dry_run:
            write_stats(bump_stats(_load_stats(), False, kind, now=now), dry_run)
        return code

    picks = get_ai_picks()
    if not picks:
        print("No upcoming AI Picks — nothing to book.")
        return fail("no_picks", code=0)
    print(f"AI Picks: {len(picks)}")
    for p in picks:
        print(f"  • {p.get('match_title')} -> {p.get('prediction')}")

    if sync_playwright is None:
        print("Playwright is not installed; cannot reach SportyBet.")
        return fail("playwright")

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
            try:
                page.goto(f"{BASE}/ng/sport/football",
                          wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(1500)
            except Exception as e:
                # Page load failures are usually transient; the API calls
                # below still work with whatever cookies were set.
                print(f"homepage load issue (continuing): {e}")

            events = fetch_events(ctx)
            if not events:
                print("SportyBet returned no events (blocked?).")
                return fail("blocked" if _last_events_http else "no_events",
                            str(_last_events_http or ""))

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
                return fail("no_match", code=0)

            # ── Plan 2.3 cache: same picks + still-fresh code → reuse ──────
            signature = selection_signature(mapped)
            stored = load_stored_payload()
            if not should_regenerate(stored, signature, now):
                print(f"Cache hit — reusing booking code {stored['code']} "
                      f"(same {len(mapped)} picks, deadline "
                      f"{stored.get('expires_at')}).")
                if not dry_run:
                    write_stats(bump_stats(_load_stats(), True,
                                           code=stored["code"], now=now), dry_run)
                return 0

            try:
                data = create_share_code(ctx, selections)
            except Exception as e:
                detail = str(e)
                kind = ("share_rejected" if "rejected" in detail
                        else "no_code" if "did not" in detail
                        else "blocked")
                print(f"share call failed: {detail}")
                # Graceful fallback: the previously published code (if any)
                # stays active — a failed run never clears a good code.
                return fail(kind, detail)
            code = data.get("shareCode")
            if not code:
                print("SportyBet did not return a share code.")
                return fail("no_code")
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

            tier_order = ["free", "premium", "vip", "vvip"]
            tiers = [p.get("tier") or "free" for p in picks]
            tier = max(tiers, key=lambda t: tier_order.index(t)
                       if t in tier_order else 0)

            payload = {
                "code": code,
                "bookmaker": BOOKMAKER,
                "share_url": share_url,
                "selections": mapped,
                "signature": signature,
                "expires_at": expires_at,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

            # The app role (anon) can read app_settings but not
            # prediction_codes, so this row is what the UI actually shows.
            try:
                updated = _rest(
                    "PATCH", "app_settings?setting_key=eq.booking_code",
                    {
                        "setting_value": payload,
                        "description": "SportyBet Nigeria accumulator generated from AI Picks",
                        "is_active": True,
                    },
                    prefer="return=representation",
                )
                if not updated:
                    _rest("POST", "app_settings", {
                        "setting_key": "booking_code",
                        "setting_value": payload,
                        "description": "SportyBet Nigeria accumulator generated from AI Picks",
                        "is_active": True,
                    }, prefer="return=minimal")
                print("Published booking code to app_settings.")
            except Exception as e:
                print(f"Could not publish to app_settings: {e}")

            # Audit trail. Also keeps any admin tooling that reads
            # prediction_codes working.
            try:
                _rest("PATCH",
                      f"prediction_codes?bookmaker=eq.{BOOKMAKER}&status=eq.active",
                      {"status": "expired"})
                _rest("POST", "prediction_codes", {
                    "code": code,
                    "bookmaker": BOOKMAKER,
                    "league_name": f"{len(selections)}-fold accumulator",
                    "market": "1X2",
                    "pick": f"{len(selections)} AI Picks",
                    "confidence": max((int(p.get("confidence") or 0) for p in picks),
                                      default=None),
                    "notes": json.dumps({"share_url": share_url,
                                         "selections": mapped}),
                    "tier": tier,
                    "status": "active",
                    "usage_limit": None,
                    "usage_count": 0,
                    "expires_at": expires_at,
                }, prefer="return=minimal")
            except Exception as e:
                print(f"Could not write audit row: {e}")

            print(f"Stored booking code {code} in Supabase.")
            write_stats(bump_stats(_load_stats(), True, code=code))
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

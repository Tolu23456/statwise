#!/usr/bin/env python3
"""
Live odds sync (The Odds API) + in-play state engine.

Modes
-----
prematch  Pull upcoming odds for all in-season soccer leagues, match events to
          Supabase predictions, de-vig a bookmaker consensus, store a snapshot
          (odds_snapshots) and merge stats.details.market into the prediction.
inplay    For predictions whose matches are live on ESPN: pull live odds,
          conditional-Poisson score-state probabilities (model/inplay.py),
          blended final probs + edge, store inplay_states + stats.details.live.

Credits: The Odds API charges markets × regions per request, free when no
events match. The script stops if remaining credits fall below the floor.

Usage
-----
  python3 odds_sync.py prematch [--window-days 7] [--dry-run]
  python3 odds_sync.py inplay   [--alpha 0.5] [--dry-run]

Env: ODDS_API_KEY (required), SUPABASE_URL / SUPABASE_SERVICE_KEY (from .env)
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

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
    _scoreboard_cache,
    _rest,
    _teams_match,
    fetch_predictions,
)
from model.inplay import blend, conditional_win_probs, edge, fit_lambdas_to_probs  # noqa: E402

ODDS_HOST = "https://api.the-odds-api.com/v4"
CREDITS = {"remaining": None}
CREDIT_FLOOR = int(os.environ.get("ODDS_CREDIT_FLOOR", "50"))
CLOCK = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(CLOCK)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── The Odds API ───────────────────────────────────────────────────────────────

def _odds_get(path: str, params: dict, api_key: str):
    params = {k: v for k, v in params.items() if v is not None}
    params["apiKey"] = api_key
    url = f"{ODDS_HOST}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": "StatWise/1.0", "Accept-Encoding": "gzip"})
    last_err = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                rem = r.headers.get("x-requests-remaining")
                if rem is not None:
                    CREDITS["remaining"] = int(rem)
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            if e.code in (429, 500, 502, 503) and attempt < 3:
                last_err = f"HTTP {e.code}: {detail}"
                time.sleep(8 * (attempt + 1))
                continue
            raise SystemExit(f"The Odds API error {e.code}: {detail}")
        except Exception as e:  # network hiccups
            last_err = str(e)
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"The Odds API unreachable: {last_err}")


def _soccer_keys(api_key: str) -> list[str]:
    """All active soccer/UEFA/FIFA sport keys (this call is free)."""
    sports = _odds_get("/sports", {}, api_key)
    if not isinstance(sports, list):
        raise SystemExit("Unexpected /sports response")
    return [s["key"] for s in sports
            if s.get("active")
            and s["key"].startswith(("soccer_", "uefa_", "fifa_"))]


def fetch_odds(api_key: str, window: tuple[dt.datetime, dt.datetime] | None) -> list[dict]:
    if CREDITS["remaining"] is not None and CREDITS["remaining"] < CREDIT_FLOOR:
        raise SystemExit(f"Credit floor hit ({CREDITS['remaining']} remaining) — skipping run.")
    keys = _soccer_keys(api_key)
    if not keys:
        return []
    params = {
        "regions": os.environ.get("ODDS_REGIONS", "eu"),
        "markets": os.environ.get("ODDS_MARKETS", "h2h,totals"),
        "oddsFormat": "decimal",
    }
    if window:
        params["commenceTimeFrom"] = _iso(window[0])
        params["commenceTimeTo"] = _iso(window[1])
    events = _odds_get(f"/sports/{','.join(keys)}/odds", params, api_key)
    return events if isinstance(events, list) else []


# ── consensus / de-vig ─────────────────────────────────────────────────────────

def _classify(name: str, event: dict) -> str | None:
    if name.strip().lower() == "draw":
        return "draw"
    if _teams_match(name, event.get("home_team", "")):
        return "home"
    if _teams_match(name, event.get("away_team", "")):
        return "away"
    return None


def consensus(event: dict, market: str) -> dict | None:
    """De-vig each bookmaker, average across books -> consensus probs/odds."""
    per_book = []
    best: dict[str, float] = {}
    for book in event.get("bookmakers") or []:
        for mkt in book.get("markets") or []:
            if mkt.get("key") != market:
                continue
            outcomes = mkt.get("outcomes") or []
            if market == "h2h":
                imp: dict[str, float] = {}
                ok = True
                for o in outcomes:
                    role = _classify(str(o.get("name", "")), event)
                    price = float(o.get("price", 0) or 0)
                    if role is None or price <= 1.001:
                        ok = False
                        break
                    imp[role] = 1.0 / price
                    best[role] = max(best.get(role, 0.0), price)
                if ok and len(imp) == 3:
                    s = sum(imp.values())
                    per_book.append({k: v / s for k, v in imp.items()})
            elif market == "totals":
                imp = {}
                ok = True
                for o in outcomes:
                    nm = str(o.get("name", "")).lower()
                    price = float(o.get("price", 0) or 0)
                    role = "over" if nm.startswith("over") else ("under" if nm.startswith("under") else None)
                    if role is None or price <= 1.001:
                        ok = False
                        break
                    imp[role] = 1.0 / price
                    best[role] = max(best.get(role, 0.0), price)
                if ok and len(imp) == 2:
                    s = sum(imp.values())
                    per_book.append({k: v / s for k, v in imp.items()})
        # only first book carrying the market matters? no — keep scanning all books
    if not per_book:
        return None
    n = len(per_book)
    probs = {k: sum(b[k] for b in per_book) / n for k in per_book[0]}
    out = {"probs": probs, "odds": {k: round(1.0 / v, 3) for k, v in probs.items()},
           "best_odds": {k: round(v, 3) for k, v in best.items()}, "books": n}
    if market == "totals":
        out["totals"] = {"over": probs.get("over"), "under": probs.get("under"),
                         "over_odds": best.get("over"), "under_odds": best.get("under")}
    return out


def match_events(preds: list[dict], events: list[dict]) -> list[tuple[dict, dict, dict]]:
    """Pair predictions with odds events by fuzzy teams + date within ±1 day."""
    pairs = []
    for ev in events:
        h, a = ev.get("home_team", ""), ev.get("away_team", "")
        if not h or not a:
            continue
        ct = ev.get("commence_time", "")[:10]
        for p in preds:
            pd = str(p.get("match_date", ""))[:10]
            if pd and ct and abs(_days(pd, ct)) > 1:
                continue
            if _teams_match(p.get("home_team", ""), h) and _teams_match(p.get("away_team", ""), a):
                pairs.append((p, ev, {"commence_time": ev.get("commence_time"),
                                      "sport_key": ev.get("sport_key")}))
                break
    return pairs


def _days(a: str, b: str) -> int:
    try:
        da = dt.date.fromisoformat(a)
        db = dt.date.fromisoformat(b)
        return (da - db).days
    except ValueError:
        return 99


# ── prediction helpers ─────────────────────────────────────────────────────────

def _set_details(pred: dict, key: str, value: dict) -> None:
    stats = pred.get("stats") or {}
    details = stats.get("details") or {}
    details[key] = value
    stats["details"] = details
    pred["stats"] = stats
    _rest("PATCH", "predictions", body={"stats": stats}, params={"id": f"eq.{pred['id']}"})


def _upcoming(preds: list[dict], days: int) -> list[dict]:
    now = _now()
    out = []
    for p in preds:
        ko = str(p.get("kickoff_time") or "")
        try:
            t = dt.datetime.fromisoformat(ko.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=CLOCK)
        except ValueError:
            continue
        if now - dt.timedelta(hours=3) <= t <= now + dt.timedelta(days=days):
            out.append(p)
    return out


def _insert(table: str, rows: list[dict]) -> None:
    if rows:
        _rest("POST", table, body=rows)


# ── mode: prematch ─────────────────────────────────────────────────────────────

def cmd_prepredict(args) -> int:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY not set — get a free key at the-odds-api.com and add it to .env")
        return 2
    preds = _upcoming(fetch_predictions(None, None), args.window_days)
    if not preds:
        print("No upcoming predictions in window — no API call made.")
        return 0
    events = fetch_odds(api_key, (_now(), _now() + dt.timedelta(days=args.window_days)))
    print(f"{len(events)} odds events, {len(preds)} upcoming predictions")
    pairs = match_events(preds, events)
    print(f"matched {len(pairs)} fixtures")
    if args.dry_run:
        for p, ev, _ in pairs[:5]:
            c = consensus(ev, "h2h")
            print(f"  {p['home_team']} vs {p['away_team']} -> {c}")
        return 0

    rows, updated = [], 0
    for p, ev, meta in pairs:
        h2h = consensus(ev, "h2h")
        if not h2h:
            continue
        tot = consensus(ev, "totals")
        rows.append({
            "match_id": p.get("match_id"),
            "market": "h2h",
            "live": False,
            "commence_time": meta.get("commence_time"),
            "bookmakers": h2h["books"],
            "odds": h2h["odds"],
            "best_odds": h2h["best_odds"],
            "probs": h2h["probs"],
            "totals": (tot or {}).get("totals"),
            "sport_key": meta.get("sport_key"),
        })
        _set_details(p, "market", {
            "as_of": _iso(_now()),
            "commence_time": meta.get("commence_time"),
            "probs": h2h["probs"], "odds": h2h["odds"],
            "best_odds": h2h["best_odds"],
            "totals": (tot or {}).get("totals"),
            "bookmakers": h2h["books"],
            "source": "the-odds-api",
        })
        updated += 1
    _insert("odds_snapshots", rows)
    print(f"stored {len(rows)} snapshots, updated {updated} predictions "
          f"(credits remaining: {CREDITS['remaining']})")
    return 0


# ── mode: inplay ───────────────────────────────────────────────────────────────

def _parse_minute(st: dict) -> float | None:
    for cand in (st.get("clock"), st.get("displayValue")):
        if isinstance(cand, dict):
            cand = cand.get("displayValue")
        if isinstance(cand, str):
            m = re.search(r"(\d+)", cand)
            if m:
                return float(m.group(1))
    clk = st.get("clock")
    if isinstance(clk, (int, float)) and clk > 0:
        return round(clk / 60.0, 1)
    return None


def _espn_live_state(league_slug: str, kickoff_iso: str, home: str, away: str) -> dict | None:
    """Live minute/score from the ESPN scoreboard (summary as fallback)."""
    month = (kickoff_iso or _iso(_now()))[:7].replace("-", "")
    ev_id = _find_espn_event(league_slug, month, home, away)
    if not ev_id:
        return None
    slug = ESPN_SLUGS.get(league_slug)
    ev = next((e for e in _scoreboard_cache.get((slug, month), [])
               if e.get("id") == ev_id), None)
    if not ev:
        return None
    st = ev.get("status") or {}
    if st.get("state") != "in":
        return None
    minute = _parse_minute(st)
    sh = sa = None
    comps = ev.get("competitors") or []
    for c in comps:
        try:
            sc = int(c.get("score"))
        except (TypeError, ValueError):
            continue
        if str(c.get("homeAway", "")).lower() == "home":
            sh = sc
        else:
            sa = sc
    if sh is None or sa is None:  # fallback: summary header
        summ = _http_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={ev_id}")
        hdr = (summ or {}).get("header", {})
        hst = hdr.get("status") or {}
        minute = minute or _parse_minute(hst)
        for c in hdr.get("competitors") or []:
            try:
                sc = int(c.get("score"))
            except (TypeError, ValueError):
                continue
            if str(c.get("homeAway", "")).lower() == "home":
                sh = sc
            else:
                sa = sc
    if sh is None or sa is None or minute is None:
        return None
    return {"minute": minute, "score_home": sh, "score_away": sa, "event_id": ev_id}


def _lambda_for(pred: dict, market_probs: dict) -> tuple[float, float, str]:
    """Model lambdas if exported, else grid-fit to the market, else default."""
    stats = pred.get("stats") or {}
    details = stats.get("details") or {}
    lh = details.get("lambda_home",
         stats.get("lambda_home", stats.get("lambda_h")))  # trainer exports lambda_h/lambda_a
    la = details.get("lambda_away",
         stats.get("lambda_away", stats.get("lambda_a")))
    try:
        if lh is not None and la is not None:
            return float(lh), float(la), "model"
    except (TypeError, ValueError):
        pass
    lh = details.get("lambda_fit_home")
    la = details.get("lambda_fit_away")
    try:
        if lh is not None and la is not None:
            return float(lh), float(la), "fit-cached"
    except (TypeError, ValueError):
        pass
    if market_probs:
        lh, la = fit_lambdas_to_probs(market_probs)
        _set_details(pred, "lambda", {"lambda_fit_home": lh, "lambda_fit_away": la,
                                      "fitted_at": _iso(_now())})
        return lh, la, "fit"
    return 1.35, 1.15, "default"


def cmd_inplay(args) -> int:
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY not set — in-play odds unavailable.")
        return 2
    now = _now()
    preds = [p for p in fetch_predictions(None, None) if p.get("status") == "upcoming"]
    candidates = _upcoming(preds, 0)  # kickoff within last 3h .. now
    if not candidates:
        print("No in-play candidates — no API call made.")
        return 0

    states = {}
    for p in candidates:
        st = _espn_live_state(p.get("league_slug", ""),
                              str(p.get("kickoff_time") or ""),
                              p.get("home_team", ""), p.get("away_team", ""))
        if st:
            states[p["id"]] = st
    if not states:
        print(f"{len(candidates)} candidates but none live on ESPN — no API call made.")
        return 0
    print(f"{len(states)} matches live on ESPN — fetching in-play odds…")

    events = fetch_odds(api_key, (now - dt.timedelta(hours=3), now + dt.timedelta(hours=1)))
    pairs = match_events([p for p in candidates if p["id"] in states], events)
    print(f"matched {len(pairs)} live fixtures ({len(events)} events, "
          f"credits left {CREDITS['remaining']})")

    rows, updated = [], 0
    for p, ev, _ in pairs:
        st = states[p["id"]]
        mkt = consensus(ev, "h2h")
        if not mkt:
            continue
        lh, la, src = _lambda_for(p, mkt["probs"])
        ip = conditional_win_probs(lh, la, st["score_home"], st["score_away"], st["minute"])
        if not ip:
            continue
        final = blend(ip, mkt["probs"], args.alpha)
        e = edge(final, mkt["probs"])
        rows.append({
            "match_id": p.get("match_id"),
            "minute": st["minute"], "score_home": st["score_home"],
            "score_away": st["score_away"],
            "model_probs": ip, "market_probs": mkt["probs"],
            "final_probs": final, "edge": {k: round(v, 4) for k, v in e.items()},
            "lambda_home": lh, "lambda_away": la,
        })
        _set_details(p, "live", {
            "as_of": _iso(_now()), "minute": st["minute"],
            "score": {"home": st["score_home"], "away": st["score_away"]},
            "model_probs": ip, "market_probs": mkt["probs"],
            "final_probs": final,
            "edge": {k: round(v, 4) for k, v in e.items()},
            "lambda": {"home": lh, "away": la, "source": src},
            "alpha": args.alpha,
        })
        updated += 1
    if args.dry_run:
        print(json.dumps(rows[:3], indent=1))
        return 0
    _insert("inplay_states", rows)
    print(f"stored {len(rows)} in-play states, updated {updated} predictions")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pm = sub.add_parser("prematch", help="upcoming odds snapshots")
    pm.add_argument("--window-days", type=int, default=7)
    pm.add_argument("--dry-run", action="store_true")
    ip = sub.add_parser("inplay", help="live score-state + live odds")
    ip.add_argument("--alpha", type=float, default=0.5,
                    help="model weight in final blend (0=market, 1=model)")
    ip.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.cmd == "prematch":
        return cmd_prepredict(args)
    return cmd_inplay(args)


if __name__ == "__main__":
    sys.exit(main())

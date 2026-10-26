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
CREDIT_FLOOR = int(os.environ.get("ODDS_CREDIT_FLOOR", "50"))
CLOCK = dt.timezone.utc

# Repo league slug -> The Odds API sport key(s).
# Only the leagues we actually publish predictions for are requested: the
# Odds API prices a request per (market x region) and rejects the very long
# "all 49 soccer leagues" URL with a CloudFront 403.
LEAGUE_ODDS_KEYS: dict[str, list[str]] = {
    "premier-league":   ["soccer_epl"],
    "championship":     ["soccer_efl_champ"],
    "efl-championship": ["soccer_efl_champ"],
    "la-liga":          ["soccer_spain_la_liga"],
    "bundesliga":       ["soccer_germany_bundesliga"],
    "serie-a":          ["soccer_italy_serie_a"],
    "ligue1":           ["soccer_france_ligue_one"],
    "eredivisie":       ["soccer_netherlands_eredivisie"],
    "primeira-liga":    ["soccer_portugal_primeira_liga"],
    "champions-league": ["soccer_uefa_champs_league"],
    "europa-league":    ["soccer_uefa_europa_league"],
    "conference-league": ["soccer_uefa_europa_conference_league"],
    "copa-libertadores": ["soccer_conmebol_copa_libertadores"],
    "brasileirao":      ["soccer_brazil_campeonato"],
    "mls":              ["soccer_usa_mls"],
    "super-lig":        ["soccer_turkey_super_league"],
    "belgian-pro":      ["soccer_belgium_first_div"],
    "swiss-super":      ["soccer_switzerland_superleague"],
    "austrian-bl":      ["soccer_austria_bundesliga"],
    "danish-superliga": ["soccer_denmark_superliga"],
    "norwegian-elit":   ["soccer_norway_eliteserien"],
    "swedish-allsv":    ["soccer_sweden_allsvenskan"],
    "greek-super":      ["soccer_greece_super_league"],
    "polish-ekstraklasa": ["soccer_poland_ekstraklasa"],
    "j-league":         ["soccer_japan_j_league"],
    "liga-mx":          ["soccer_mexico_ligamx"],
    "chinese-super":    ["soccer_china_superleague"],
    "russian-premier":  ["soccer_russia_premier_league"],
    "premier-league-russia": ["soccer_russia_premier_league"],
    "primera-division": ["soccer_argentina_primera_division"],
    "irish-prem":      ["soccer_league_of_ireland"],
    "finnish-veikkaus": ["soccer_finland_veikkausliiga"],
    "liga-2":           ["soccer_spain_segunda_division"],
    "bundesliga-2":     ["soccer_germany_bundesliga2"],
    "serie-b":          ["soccer_italy_serie_b"],
    "copa-sudamericana": ["soccer_conmebol_copa_sudamericana"],
    # Live fixture-feed spellings with no entry of their own.  These five
    # slugs are what ai/model/live_fetcher.py actually writes into
    # predictions.league_slug, so without them the Odds API was never asked
    # for their prices — part of why odds coverage fell 76% -> 38% and why
    # only 41 of 97 settled fixtures carried odds at all.
    "turkish-super-lig":  ["soccer_turkey_super_league"],
    "brazilian-serie-a":  ["soccer_brazil_campeonato"],
    "argentina-primera":  ["soccer_argentina_primera_division"],
    "saudi-pro-league":   ["soccer_saudi_pro_league"],
    "scottish-prem":      ["soccer_scotland_premier_league"],
}

# NOTE: unlike every MODEL-facing lookup (model/leagues.py), odds keys are
# deliberately NOT canonicalised.  canon_league() maps europa-league and
# conference-league onto champions-league so the model sees one European
# bucket — but the market prices those competitions separately, so
# canonicalising here would fetch Champions League odds for a Thursday
# fixture.  This table keys on the REAL competition.


def _odds_keys(slug) -> list[str]:
    """The Odds API sport key(s) for a league slug (empty if unmapped)."""
    return LEAGUE_ODDS_KEYS.get(str(slug or "").strip(), [])


def _now() -> dt.datetime:
    return dt.datetime.now(CLOCK)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── fetch cache (credits are precious: 500/month on the free tier) ────────────

CACHE_FILE = os.path.join(BASE, "data", "odds_cache.json")


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2)
    except OSError as e:
        print(f"could not write odds cache: {e}")


def _fresh_enough(cache: dict, league: str, max_age_hours: float) -> bool:
    """True when we already fetched this league recently enough."""
    if max_age_hours <= 0:
        return False
    stamp = (cache.get(league) or {}).get("at")
    if not stamp:
        return False
    try:
        t = dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=CLOCK)
    return (_now() - t) < dt.timedelta(hours=max_age_hours)


_DB_FRESH_CACHE: dict[str, dt.datetime] = {}


def _fresh_in_db(sport_key: str, max_age_hours: float) -> bool:
    """True when Supabase already holds a snapshot for this sport that is
    younger than ``max_age_hours``.

    The local JSON cache is useless on CI (every run gets a fresh checkout),
    so the snapshot table is the shared source of truth between runs.
    """
    if max_age_hours <= 0 or not sport_key:
        return False
    if sport_key in _DB_FRESH_CACHE:
        t = _DB_FRESH_CACHE[sport_key]
        return (_now() - t) < dt.timedelta(hours=max_age_hours)
    try:
        rows = _rest("GET", "odds_snapshots", params={
            "select": "created_at", "sport_key": f"eq.{sport_key}",
            "order": "created_at.desc", "limit": "1",
        }) or []
    except Exception:
        return False
    if not rows:
        return False
    try:
        t = dt.datetime.fromisoformat(str(rows[0]["created_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=CLOCK)
    _DB_FRESH_CACHE[sport_key] = t
    return (_now() - t) < dt.timedelta(hours=max_age_hours)


# ── The Odds API ───────────────────────────────────────────────────────────────
#
# Several free accounts are rotated: each key gets 500 credits/month, so a
# pool keeps the sync running without one key becoming a single point of
# failure.  The key with the most remaining credits is used first and a key
# is retired for the rest of the run as soon as it errors out.

_KEYS: list[str] = []
_KEY_STATE: dict[str, dict] = {}          # key -> {"remaining": int|None, "bad": str|None}
CREDITS = {"remaining": None, "key": None}


def _init_keys() -> list[str]:
    """Read the key pool from the environment (order preserved, dupes dropped)."""
    raw: list[str] = []
    bulk = os.environ.get("ODDS_API_KEYS", "")
    if bulk.strip():
        raw.extend(p.strip() for p in re.split(r"[,\s;]+", bulk) if p.strip())
    for name in ("ODDS_API_KEY",) + tuple(f"ODDS_API_KEY_{i}" for i in range(2, 9)):
        v = os.environ.get(name, "").strip()
        if v:
            raw.append(v)
    keys, seen = [], set()
    for k in raw:
        if k not in seen:
            seen.add(k)
            keys.append(k)
    for k in keys:
        _KEY_STATE.setdefault(k, {"remaining": None, "bad": None})
    global _KEYS
    _KEYS = keys
    return keys


def _pick_key() -> str:
    """Best available key: most credits left, untried first, never a bad one."""
    if not _KEYS:
        _init_keys()
    live = [k for k in _KEYS if not _KEY_STATE.get(k, {}).get("bad")]
    if not live:
        raise SystemExit(
            "Every ODDS_API_KEY returned an error — check the key pool in .env "
            "(ODDS_API_KEY, ODDS_API_KEY_2 … or ODDS_API_KEYS=a,b,c)."
        )

    def rank(k: str):
        rem = _KEY_STATE.get(k, {}).get("remaining")
        # Unknown balance: probe it first (the /sports call is free).
        return (0, k) if rem is None else (1, -rem, k)

    return min(live, key=rank)


def _note_credits(key: str, remaining) -> None:
    if remaining is None:
        return
    try:
        _KEY_STATE[key]["remaining"] = int(remaining)
    except (TypeError, ValueError, KeyError):
        return
    total = [_KEY_STATE[k]["remaining"] for k in _KEYS
             if _KEY_STATE.get(k, {}).get("remaining") is not None]
    CREDITS["remaining"] = max(total) if total else None


def _odds_get(path: str, params: dict, api_key: str | None = None):
    """GET against the Odds API, transparently rotating keys on failure."""
    params = {k: v for k, v in params.items() if v is not None}

    tried: list[str] = []
    last_err = None
    while True:
        key = api_key if api_key and not tried else _pick_key()
        if key in tried:
            raise SystemExit(f"The Odds API error: {last_err}")
        tried.append(key)

        q = dict(params, apiKey=key)
        url = f"{ODDS_HOST}{path}?{urllib.parse.urlencode(q)}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "StatWise/1.0", "Accept-Encoding": "gzip"})

        rotate = False
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    _note_credits(key, r.headers.get("x-requests-remaining"))
                    raw = r.read()
                    if raw[:2] == b"\x1f\x8b":
                        raw = gzip.decompress(raw)
                    CREDITS["key"] = key
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:300]
                except Exception:
                    pass
                # 401/402/403 = bad or exhausted key -> fall through to another one.
                if e.code in (401, 402, 403):
                    _KEY_STATE.setdefault(key, {"remaining": None, "bad": None})["bad"] = \
                        f"HTTP {e.code}"
                    last_err = f"{key[:10]}… HTTP {e.code}: {detail}"
                    rotate = True
                    break
                if e.code == 429 and attempt < 3:
                    last_err = f"HTTP 429: {detail}"
                    time.sleep(8 * (attempt + 1))
                    continue
                if e.code in (500, 502, 503) and attempt < 3:
                    last_err = f"HTTP {e.code}: {detail}"
                    time.sleep(8 * (attempt + 1))
                    continue
                raise SystemExit(f"The Odds API error {e.code}: {detail}")  # 404 etc.
            except Exception as e:  # network hiccups
                last_err = str(e)
                if attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                rotate = True
                break
        if not rotate:
            continue

    raise SystemExit(f"The Odds API unreachable: {last_err}")


def _soccer_keys(api_key: str | None = None, wanted: list[str] | None = None) -> list[str]:
    """Active soccer/UEFA/FIFA sport keys (this call is free).

    When ``wanted`` is given the result is narrowed to those keys so the
    odds URL stays short (the full 49-league list is rejected with 403).
    """
    sports = _odds_get("/sports", {}, api_key)
    if not isinstance(sports, list):
        raise SystemExit("Unexpected /sports response")
    keys = [s["key"] for s in sports
            if s.get("active")
            and s["key"].startswith(("soccer_", "uefa_", "fifa_"))]
    if wanted:
        allowed = set(wanted)
        keys = [k for k in keys if k in allowed]
        missing = allowed - set(keys)
        if missing:
            print(f"  (odds API has no market for: {', '.join(sorted(missing))})")
    return keys


def fetch_odds(api_key: str | None, window: tuple[dt.datetime, dt.datetime] | None,
               sport_keys: list[str] | None = None) -> list[dict]:
    """Fetch h2h odds.

    NOTE: this API plan only accepts ONE sport key per request — a
    comma-joined list is answered with 404 "Unknown sport". Each request
    therefore costs one credit (1 market x 1 region).
    """
    if CREDITS["remaining"] is not None and CREDITS["remaining"] < CREDIT_FLOOR:
        raise SystemExit(f"Credit floor hit ({CREDITS['remaining']} remaining) — skipping run.")

    keys = sport_keys or _soccer_keys(api_key)
    if not keys:
        return []

    params = {
        "regions": os.environ.get("ODDS_REGIONS", "eu"),
        "markets": os.environ.get("ODDS_MARKETS", "h2h"),
        "oddsFormat": "decimal",
    }
    if window:
        params["commenceTimeFrom"] = _iso(window[0])
        params["commenceTimeTo"] = _iso(window[1])

    events: list[dict] = []
    for key in keys:
        try:
            got = _odds_get(f"/sports/{key}/odds", params, api_key)
        except SystemExit as e:
            print(f"  {key}: {e}")
            continue
        if isinstance(got, list):
            for ev in got:
                ev.setdefault("sport_key", key)
            events.extend(got)
    return events


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


def _pick_index(prediction: str) -> int:
    return {"Home Win": 0, "Draw": 1, "Away Win": 2}.get(prediction, 0)


def _apply_market_odds(pred: dict, h2h: dict, meta: dict) -> bool:
    """Write the de-vigged market odds onto the prediction row.

    * ``stats.odds_home / odds_draw / odds_away`` — consensus prices
    * ``stats.value_edge``                   — model prob vs implied prob
    * ``odds`` column                        — the price of *our* pick

    Returns True when the row was updated.
    """
    stats = pred.get("stats") or {}
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            stats = {}

    odds = h2h.get("odds") or {}
    probs = h2h.get("probs") or {}
    o_h, o_d, o_a = odds.get("home"), odds.get("draw"), odds.get("away")
    if not (o_h and o_d and o_a):
        return False

    stats["odds_home"] = float(o_h)
    stats["odds_draw"] = float(o_d)
    stats["odds_away"] = float(o_a)

    # Value edge: our model's probability for the pick minus the market's
    # implied probability (both vig-free, because `probs` is de-vigged).
    idx = _pick_index(pred.get("prediction", "Home Win"))
    model_prob = pred.get("prob_home") or pred.get("prob_draw") or pred.get("prob_away")
    if model_prob is None:
        stats_ = stats
        model_prob = {
            0: stats_.get("prob_home"),
            1: stats_.get("prob_draw"),
            2: stats_.get("prob_away"),
        }.get(idx)
    if model_prob is not None:
        market_p = probs.get(["home", "draw", "away"][idx])
        if market_p:
            stats["value_edge"] = round(float(model_prob) - float(market_p) * 100.0, 1)

    details = stats.get("details") or {}
    details["market"] = {
        "as_of": _iso(_now()),
        "commence_time": meta.get("commence_time"),
        "probs": probs, "odds": odds,
        "best_odds": h2h.get("best_odds"),
        "totals": (h2h.get("totals") or {}).get("totals"),
        "bookmakers": h2h.get("books"),
        "source": "the-odds-api",
    }
    stats["details"] = details
    pred["stats"] = stats

    pick_odds = [o_h, o_d, o_a][idx]
    body = {"stats": stats}
    if pick_odds:
        body["odds"] = float(pick_odds)
    _rest("PATCH", "predictions", body=body, params={"id": f"eq.{pred['id']}"})
    pred["odds"] = float(pick_odds) if pick_odds else pred.get("odds")
    return True


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


_COLUMN_CACHE: dict[str, set[str]] = {}

# Candidate columns per table; probed once so a schema that lags behind the
# migration file (stub tables with only id/match_id exist) degrades gracefully.
_TABLE_COLUMNS = {
    "odds_snapshots": (
        "match_id", "market", "live", "commence_time", "bookmakers",
        "odds", "best_odds", "probs", "totals", "sport_key", "created_at",
    ),
    # Live production schema (probed 2026-10-26 via the OpenAPI spec):
    #   id, match_id, fixture_key, status, minute, home_score, away_score,
    #   last_update, raw
    # The rich model/market payload goes into `raw` (jsonb) — the earlier
    # column set (score_home/score_away/model_probs/…) never existed in
    # production, so _insert's required-column check skipped EVERY row.
    "inplay_states": (
        "match_id", "fixture_key", "status", "minute", "home_score",
        "away_score", "last_update", "raw",
    ),
}


def _columns_of(table: str) -> set[str] | None:
    """Column names of `table` that actually exist, or None if unprobed."""
    if table in _COLUMN_CACHE:
        return _COLUMN_CACHE[table]
    if table not in _TABLE_COLUMNS:
        return None
    have: set[str] = set()
    for c in _TABLE_COLUMNS[table]:
        try:
            _rest("GET", table, params={"select": c, "limit": "1"})
            have.add(c)
        except Exception:
            pass
    _COLUMN_CACHE[table] = have
    return have


def _insert(table: str, rows: list[dict]) -> None:
    if not rows:
        return
    # Drop keys the table does not have so one missing column cannot reject
    # the whole batch.
    have = _columns_of(table)
    if have is not None:
        if not have:
            print(f"skip {table}: none of the expected columns exist yet — "
                  f"run statwise/supabase-settlement-migration.sql")
            return
        missing = set(_TABLE_COLUMNS[table]) - have
        # A row without its payload columns is useless — better to skip than
        # to write hundreds of near-empty rows we then have to clean up.
        if table == "odds_snapshots":
            required = {"odds", "probs"}
        else:  # inplay_states: the live state itself is the payload
            required = {"minute", "home_score"}
        useless = required - have
        if useless:
            print(f"skip {table}: missing essential column(s) "
                  f"{', '.join(sorted(useless))} — run "
                  f"statwise/supabase-settlement-migration.sql in the Supabase SQL editor")
            return
        if missing:
            print(f"warning: {table} is missing optional column(s) "
                  f"{', '.join(sorted(missing))} — run "
                  f"statwise/supabase-settlement-migration.sql")
        rows = [{k: v for k, v in r.items() if k in have} for r in rows]
    try:
        _rest("POST", table, body=rows)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:400]
        except Exception:
            detail = str(e)
        print(f"insert into {table} failed: HTTP {e.code} {detail}")
        # Retry row-by-row so one bad row does not lose the whole batch.
        for r in rows:
            try:
                _rest("POST", table, body=[r])
            except urllib.error.HTTPError as e2:
                try:
                    d2 = e2.read().decode()[:200]
                except Exception:
                    d2 = str(e2)
                print(f"  row {r.get('match_id')} failed: HTTP {e2.code} {d2}")
                break


# ── mode: prematch ─────────────────────────────────────────────────────────────

def cmd_prepredict(args) -> int:
    if not _init_keys():
        print("No Odds API key found — add ODDS_API_KEY (or ODDS_API_KEYS=a,b,c) "
              "to .env / the ODDS_API_KEYS repo secret.")
        return 2
    print(f"Key pool: {len(_KEYS)} key(s)")
    preds = _upcoming(fetch_predictions(None, None), args.window_days)
    if not preds:
        print("No upcoming predictions in window — no API call made.")
        return 0

    # Only ask for the leagues we actually publish, one request per league:
    # this API plan rejects a comma-joined sport list (404 "Unknown sport")
    # and charges one credit per request.
    slugs = sorted({str(p.get("league_slug") or "") for p in preds})
    wanted: list[str] = []
    slug_for_key: dict[str, str] = {}
    for s in slugs:
        for k in _odds_keys(s):
            if k not in wanted:
                wanted.append(k)
                slug_for_key[k] = s
    unmapped = [s for s in slugs if not _odds_keys(s)]
    if unmapped:
        print(f"no Odds API mapping for: {', '.join(unmapped)} (these fixtures get no odds)")
    if not wanted:
        print("No Odds API mapping for our leagues — nothing to fetch.")
        return 0
    print(f"Leagues: {', '.join(slugs)}")
    print(f"Odds API sports: {', '.join(wanted)}")

    cache = _load_cache()
    max_age = float(args.max_age_hours)
    # Local JSON cache covers repeated runs on one machine; odds_snapshots in
    # Supabase is the shared source of truth across CI runs (fresh checkout).
    due = [k for k in wanted
           if not (_fresh_in_db(k, max_age)
                   or _fresh_enough(cache, slug_for_key.get(k, k), max_age))]
    skipped = [k for k in wanted if k not in due]
    if skipped:
        print(f"skipping {len(skipped)} league(s) fetched within {max_age:g}h: "
              f"{', '.join(slug_for_key.get(k, k) for k in skipped)}")
    if not due:
        print("All leagues have fresh odds — nothing to fetch.")
        return 0

    events: list[dict] = []
    window = (_now(), _now() + dt.timedelta(days=args.window_days))
    for key in due:
        try:
            got = fetch_odds(None, window, sport_keys=[key])
        except SystemExit as e:
            print(f"  {key}: {e}")
            continue
        events.extend(got)
        cache[slug_for_key.get(key, key)] = {
            "sport_key": key,
            "at": _iso(_now()),
            "events": len(got),
        }
        print(f"  {slug_for_key.get(key, key):20s} {len(got):3d} event(s), "
              f"credits left {CREDITS['remaining']}")
    _save_cache(cache)
    if not events:
        print("No odds events returned.")
        return 0

    pairs = match_events(preds, events)
    print(f"matched {len(pairs)} fixtures")
    if args.dry_run:
        # Dry runs must not consume the cache window.
        for k in due:
            cache.pop(slug_for_key.get(k, k), None)
        _save_cache(cache)
        for p, ev, _ in pairs[:8]:
            c = consensus(ev, "h2h")
            print(f"  {p['home_team']} vs {p['away_team']} -> {c and c['odds']}")
        return 0

    rows, updated = [], 0
    for p, ev, meta in pairs:
        h2h = consensus(ev, "h2h")
        if not h2h:
            continue
        tot = consensus(ev, "totals")
        if tot:
            h2h["totals"] = tot.get("totals") or {}
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
        if _apply_market_odds(p, h2h, meta):
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


def _competitors_of(ev_or_hdr: dict) -> list[dict]:
    """The competitor list of a raw ESPN event / summary header.

    Both nest competitors under competitions[0] — reading a top-level
    "competitors" key silently returns [] and every score looked missing
    (plan 2.4 fix).
    """
    comps = ev_or_hdr.get("competitions") or []
    if not comps:
        return []
    return comps[0].get("competitors") or []


def _espn_live_state(league_slug: str, kickoff_iso: str, home: str, away: str) -> dict | None:
    """Live minute/score from the ESPN scoreboard (summary as fallback).

    Plan 2.4 bug fixes (the old version never matched a live game):
      - the in-play flag lives at status.type.state, NOT status.state
        (raw scoreboard events nest it under "type")
      - competitors live at competitions[0].competitors, NOT top-level
        "competitors" — in both the scoreboard event and the summary header
    """
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
    state = ((st.get("type") or {}).get("state") or "").lower()
    if state != "in":
        return None
    minute = _parse_minute(st)
    sh = sa = None
    for c in _competitors_of(ev):
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
        for c in _competitors_of(hdr):
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
    if not _init_keys():
        print("No Odds API key found — in-play odds unavailable.")
        return 2
    print(f"Key pool: {len(_KEYS)} key(s)")
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

    wanted: list[str] = []
    for p in candidates:
        for k in _odds_keys(p.get("league_slug", "")):
            if k not in wanted:
                wanted.append(k)
    if not wanted:
        print("No Odds API mapping for the live leagues — no API call made.")
        return 0

    events: list[dict] = []
    window = (now - dt.timedelta(hours=3), now + dt.timedelta(hours=1))
    for key in wanted:
        try:
            events.extend(fetch_odds(None, window, sport_keys=[key]))
        except SystemExit as e:
            print(f"  {key}: {e}")
    if not events:
        print("No in-play odds events returned.")
        return 0
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
        # Production schema: home_score/away_score (not score_home/score_away),
        # plus fixture_key/status/last_update; the model payload lives in raw.
        rows.append({
            "match_id": p.get("match_id"),
            "fixture_key": st.get("event_id") or p.get("match_id"),
            "status": "in",
            "minute": st["minute"],
            "home_score": st["score_home"],
            "away_score": st["score_away"],
            "last_update": _iso(_now()),
            "raw": {
                "model_probs": ip, "market_probs": mkt["probs"],
                "final_probs": final, "edge": {k: round(v, 4) for k, v in e.items()},
                "lambda_home": lh, "lambda_away": la,
            },
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
    pm.add_argument("--max-age-hours", type=float, default=24.0,
                    help="skip a league when its odds were fetched less than "
                         "this many hours ago (saves Odds API credits)")
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

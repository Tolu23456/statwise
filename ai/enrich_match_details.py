#!/usr/bin/env python3
"""
Enrich Supabase predictions with rich match details:

  venue / stadium, city, attendance
  weather (temperature, wind, precipitation, condition) — historical or forecast
  goal scorers with minutes and teams
  cards (yellow/red) with player + minute
  substitutions (player in/out + minute)
  starting lineups
  referee

Sources (both free & keyless):
  ESPN site API  — scoreboard + summary endpoints
  Open-Meteo     — geocoding + historical archive + forecast

The script is idempotent: it skips rows whose stats.details already exist
unless --force is given. Every request goes through a small rate limiter.

Usage:
    python3 ai/enrich_match_details.py [--force] [--limit N] [--league SLUG]
"""
from __future__ import annotations

import argparse
import difflib
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ── Supabase config ───────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://fwpriiugfjhtoymcbmso.supabase.co"
)
SUPABASE_SERVICE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1NjkwNzcxOCwiZXhwIjoyMDcyNDgzNzE4fQ.VHHC38yn7tPu_PCUHx0O65P6F87LeOT9EUaYHPIRZIo",
)
REST = f"{SUPABASE_URL}/rest/v1"

HEADERS_JSON = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# ── ESPN league slug mapping (repo slug → ESPN slug) ─────────────────────────
# Same mapping as backfill_espn.py — extend when new leagues appear.
ESPN_SLUGS = {
    "premier-league": "eng.1",
    "la-liga": "esp.1",
    "bundesliga": "ger.1",
    "serie-a": "ita.1",
    "ligue1": "fra.1",
    "eredivisie": "ned.1",
    "primeira-liga": "por.1",
    "champions-league": "uefa.champions",
    "europa-league": "uefa.europa",
    "mls": "usa.1",
    "premier-league-russia": "rus.1",
    "super-lig": "tur.1",
    "chinese-super": "chn.1",
    "j-league": "jpn.1",
    "liga-mx": "mex.1",
    "primera-division": "arg.1",
    "brasileirao": "bra.1",
    "austrian-bl": "aut.1",
    "swiss-super": "sui.1",
    "danish-superliga": "den.1",
    "norwegian-elit": "nor.1",
    "swedish-allsv": "swe.1",
    "belgian-pro": "bel.1",
    "greek-super": "gre.1",
    "romanian-liga1": "rou.1",
    "polish-ekstraklasa": "pol.1",
    "russian-premier": "rus.1",
    "irish-prem": "irl.1",
    "finnish-veikkaus": "fin.1",
    "championship": "eng.2",
    "league-one": "eng.3",
    "league-two": "eng.4",
    "bundesliga-2": "ger.2",
    "serie-b": "ita.2",
    "ligue-2": "fra.2",
    "segunda-division": "esp.2",
    "eredivisie-2": "ned.2",
    "copa-libertadores": "CONMEBOL.libertadores",
    "copa-sudamericana": "CONMEBOL.sudamericana",
    "world-cup": "fifa.world",
    "euro": "uefa.euro",
}

# team-name noise to strip before fuzzy matching
_TEAM_NOISE = re.compile(
    r"\b(fc|cf|sc|ac|cd|sd|rc|afc|bsc|ss|us|vfl|vfb|sv|tsg|1\.\s*fc|sk|fk|nk|hk|if|bk|ais|ca|club)\b",
    re.IGNORECASE,
)
_SUFFIX = re.compile(
    r"\b(fc|united|city|town|athletic|sporting|deportivo|real|cf|sc|ac)\b\.?$", re.IGNORECASE
)

_min_delay = 0.0  # global rate limiter (seconds between ESPN calls)
_scoreboard_cache: dict = {}  # (league_slug, month) → events list
_geocode_cache: dict = {}     # (city, country) → (lat, lon) | None


def _norm_team(name: str) -> str:
    """Aggressively normalise a team name for fuzzy matching."""
    if not isinstance(name, str):
        return ""
    s = name.lower().strip()
    s = s.replace("’", "'")
    s = _TEAM_NOISE.sub(" ", s)
    # only strip a trailing suffix when something meaningful remains
    stripped = _SUFFIX.sub(" ", s)
    if re.sub(r"[^a-z0-9]", "", stripped):
        s = stripped
    s = re.sub(r"[^a-z0-9' ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _teams_match(a: str, b: str, thresh: float = 0.72) -> bool:
    """Fuzzy-match two team names after normalisation."""
    na, nb = _norm_team(a), _norm_team(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= thresh


def _http_json(url: str, retries: int = 3, timeout: int = 30):
    """GET url, auto-decompress gzip, return parsed JSON (or None)."""
    global _min_delay
    for attempt in range(retries):
        # rate-limit
        wait = _min_delay - time.time()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (StatWise enricher)",
                "Accept-Encoding": "gzip",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                _min_delay = time.time() + 0.25  # ~4 req/s ceiling
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited — back off harder
                time.sleep(2 * (attempt + 1))
                continue
            if e.code == 404:
                return None
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


# ── ESPN helpers ──────────────────────────────────────────────────────────────

def _find_espn_event(league_slug: str, month: str, home: str, away: str):
    """Find an ESPN event ID for a fixture by league + month + fuzzy team match."""
    slug = ESPN_SLUGS.get(league_slug)
    if not slug:
        return None
    key = (slug, month)
    if key not in _scoreboard_cache:
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
            f"?dates={month}&limit=300"
        )
        data = _http_json(url)
        _scoreboard_cache[key] = (data or {}).get("events", [])
    for ev in _scoreboard_cache[key]:
        name = ev.get("name", "")  # e.g. "Leeds United at Manchester United"
        if " at " not in name:
            continue
        away_name, home_name = name.split(" at ", 1)
        if _teams_match(home_name, home) and _teams_match(away_name, away):
            return ev.get("id")
    return None


def _parse_summary(summary: dict) -> dict:
    """Extract rich details from an ESPN summary payload."""
    out: dict = {}

    # venue + attendance
    gi = summary.get("gameInfo", {})
    venue = gi.get("venue", {})
    if venue:
        out["venue"] = venue.get("fullName")
        addr = venue.get("address", {})
        out["city"] = addr.get("city")
        out["country"] = addr.get("country")
    if gi.get("attendance"):
        out["attendance"] = gi["attendance"]

    # referee — usually in header.competitions[0].details[].officials or venue staff
    for comp in summary.get("header", {}).get("competitions", []):
        for d in comp.get("details", []):
            if d.get("type", {}).get("id") == "17":  # referee official type
                out["referee"] = d.get("displayName")
                break

    # key events → scorers, cards, substitutions
    scorers, cards, subs = [], [], []
    for ev in summary.get("keyEvents", []):
        etype = (ev.get("type") or {}).get("type", "")
        text = ev.get("text", "")
        minute = (ev.get("clock") or {}).get("displayValue", "")
        team = (ev.get("team") or {}).get("displayName", "")
        scoring = ev.get("scoringPlay", False)

        if scoring and etype.startswith("goal"):
            # extract scorer name from text: "Goal! X 1, Y 2. Player Name (Team) ..."
            m = re.search(r"\.\s+([A-Z][^\(]+?)\s+\(", text)
            player = m.group(1).strip() if m else text[:60]
            scorers.append(
                {"player": player, "team": team, "minute": minute, "type": etype}
            )
        elif etype in ("yellow-card", "red-card", "yellow-card-second"):
            m = re.search(r"^(.+?)\s+\(", text)
            player = m.group(1).strip() if m else text[:60]
            cards.append(
                {
                    "player": player,
                    "team": team,
                    "minute": minute,
                    "type": etype.replace("-", "_"),
                }
            )
        elif etype == "substitution":
            m = re.search(
                r"(?:Substitution(?:\(.*?\))?|.*?)[,:\s]+(.+?)\s+(?:for|replaces)\s+(.+?)(?:\.|$)",
                text,
            )
            if m:
                subs.append(
                    {
                        "player_in": m.group(1).strip(),
                        "player_out": m.group(2).strip(),
                        "team": team,
                        "minute": minute,
                    }
                )
            else:
                subs.append({"text": text, "team": team, "minute": minute})

    if scorers:
        out["scorers"] = scorers
    if cards:
        out["cards"] = cards
    if subs:
        out["substitutions"] = subs[:20]

    # lineups (rosters) — starters + bench per team
    lineups = {}
    for roster in summary.get("rosters", []):
        team_name = (roster.get("team") or {}).get("displayName", "?")
        starters, bench = [], []
        for p in roster.get("roster", []):
            athlete = p.get("athlete", {})
            entry = {
                "name": athlete.get("fullName") or athlete.get("displayName", "?"),
                "jersey": p.get("jersey"),
                "position": (p.get("position") or {}).get("abbreviation"),
            }
            if p.get("starter"):
                starters.append(entry)
            else:
                bench.append(entry)
        lineups[team_name] = {"starting": starters, "bench": bench[:12]}
    if lineups:
        out["lineups"] = lineups

    # possession / shots / passes / offsides / corners etc from boxscore team stats
    # NOTE: ESPN uses the key "statistics" (not "stats") inside boxscore.teams[]
    team_stats = {}
    for t in summary.get("boxscore", {}).get("teams", []):
        name = (t.get("team") or {}).get("displayName", "?")
        st = {}
        for s in t.get("statistics", t.get("stats", [])):
            key = s.get("name") or s.get("label")
            if key:
                st[key.lower().replace(" ", "_")] = s.get("displayValue")
        if st:
            team_stats[name] = st
    if team_stats:
        out["team_stats"] = team_stats

    return out


# ── Open-Meteo helpers ────────────────────────────────────────────────────────

_WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    61: "Slight rain", 63: "Rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ hail",
}


def _geocode(city: str, country: str | None = None):
    """Return (lat, lon) for a city via Open-Meteo geocoding (free, keyless)."""
    if not city:
        return None
    key = (city.lower(), (country or "").lower())
    if key in _geocode_cache:
        return _geocode_cache[key]
    q = urllib.parse.quote(f"{city}, {country}" if country else city)
    url = (
        f"https://geocoding-api.open-meteo.com/v1/search"
        f"?name={q}&count=1&language=en&format=json"
    )
    data = _http_json(url)
    if not data:
        _geocode_cache[key] = None
        return None
    results = data.get("results") or []
    if results:
        _geocode_cache[key] = (results[0].get("latitude"), results[0].get("longitude"))
        return _geocode_cache[key]
    _geocode_cache[key] = None
    return None


def _weather_at(lat: float, lon: float, iso_dt: str):
    """
    Weather for a specific datetime.
    Uses the archive API for past dates, the forecast API for future ones.
    Returns dict with temperature_c, wind_kmh, precipitation_mm, humidity, condition.
    """
    dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    date_str = dt.strftime("%Y-%m-%d")
    hour = dt.hour

    if dt < now - timedelta(days=5):
        # historical — archive API
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&start_date={date_str}&end_date={date_str}"
            f"&hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code"
            f"&timezone=UTC"
        )
    else:
        # forecast (works for ~past 7 days too via forecast API's past_days)
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code"
            f"&past_days=7&forecast_days=7&timezone=UTC"
        )
    data = _http_json(url)
    if not data:
        return None
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    # find the hour index closest to the match kickoff
    target = f"{date_str}T{hour:02d}:00"
    idx = None
    for i, t in enumerate(times):
        if t == target:
            idx = i
            break
    if idx is None:
        # nearest
        for i, t in enumerate(times):
            if t.startswith(date_str):
                idx = i
                break
    if idx is None:
        return None

    def _get(key):
        arr = hourly.get(key)
        return arr[idx] if arr and idx < len(arr) else None

    code = _get("weather_code")
    return {
        "temperature_c": _get("temperature_2m"),
        "humidity_pct": _get("relative_humidity_2m"),
        "precipitation_mm": _get("precipitation"),
        "wind_kmh": _get("wind_speed_10m"),
        "condition": _WMO_CODES.get(code, f"code {code}") if code is not None else None,
    }


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _rest(method: str, path: str, body=None, params=None):
    url = f"{REST}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=HEADERS_JSON)
    with urllib.request.urlopen(req, timeout=60) as r:
        text = r.read().decode()
        return json.loads(text) if text.strip() else None


def fetch_predictions(limit: int | None, league: str | None):
    params = {
        "select": "id,match_id,match_title,home_team,away_team,league_slug,match_date,kickoff_time,status,stats",
        "order": "match_date.desc",
    }
    if limit:
        params["limit"] = str(limit)
    if league:
        params["league_slug"] = f"eq.{league}"
    return _rest("GET", "predictions", params=params) or []


def update_stats(pred_id: str, new_stats: dict):
    _rest("PATCH", "predictions", body={"stats": new_stats}, params={"id": f"eq.{pred_id}"})


# ── main ──────────────────────────────────────────────────────────────────────

def enrich_one(pred: dict, force: bool = False) -> str:
    """Enrich a single prediction. Returns 'ok' | 'skip' | 'fail'."""
    stats = pred.get("stats") or {}
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except Exception:
            stats = {}

    if stats.get("details") and not force:
        return "skip"

    home = pred.get("home_team", "")
    away = pred.get("away_team", "")
    league = pred.get("league_slug", "")
    date = pred.get("match_date", "")
    kickoff = pred.get("kickoff_time") or f"{date}T15:00:00Z"
    month = (date or "")[:7].replace("-", "")

    if not month:
        return "fail"

    # 1. locate the ESPN event
    espn_id = _find_espn_event(league, month, home, away)
    details: dict = {}
    if espn_id:
        details["espn_event_id"] = espn_id
        summary = _http_json(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
            f"{ESPN_SLUGS[league]}/summary?event={espn_id}"
        )
        if summary:
            details.update(_parse_summary(summary))

    # 2. weather via Open-Meteo (needs a city — fall back to team-name-less geocode of league country)
    city = details.get("city")
    country = details.get("country")
    if not city:
        # try geocoding the venue string itself
        city = details.get("venue")
    coords = _geocode(city, country) if city else None
    if coords:
        w = _weather_at(coords[0], coords[1], kickoff)
        if w:
            details["weather"] = w
            details["weather_geo"] = {"lat": coords[0], "lon": coords[1], "city": city}

    if not details:
        return "fail"

    stats["details"] = details
    update_stats(pred["id"], stats)
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="Enrich Supabase predictions with match details")
    ap.add_argument("--force", action="store_true", help="re-enrich rows that already have details")
    ap.add_argument("--limit", type=int, default=None, help="max rows to process")
    ap.add_argument("--league", type=str, default=None, help="only this league_slug")
    args = ap.parse_args()

    preds = fetch_predictions(args.limit, args.league)
    print(f"Fetched {len(preds)} predictions")

    ok = skip = fail = 0
    for i, p in enumerate(preds, 1):
        label = p.get("match_title") or f"{p.get('home_team')} vs {p.get('away_team')}"
        try:
            res = enrich_one(p, force=args.force)
        except Exception as e:
            print(f"  [{i}/{len(preds)}] ERROR {label}: {e}")
            fail += 1
            continue
        if res == "ok":
            ok += 1
            print(f"  [{i}/{len(preds)}] OK    {label}")
        elif res == "skip":
            skip += 1
        else:
            fail += 1
            print(f"  [{i}/{len(preds)}] FAIL  {label}")

    print(f"\nDone. ok={ok} skip={skip} fail={fail}")


if __name__ == "__main__":
    main()

"""
Fetches upcoming football matches from live APIs.
Supports:
  1. football-data.org  (needs FOOTBALL_API_KEY env var, free tier)
  2. The Sports DB      (free, no key needed)
  3. API-Football       (needs X_RAPIDAPI_KEY env var)

Returns a list of match dicts ready for the prediction engine.
"""
from __future__ import annotations
import os, logging, datetime, json
import requests

logger = logging.getLogger(__name__)

FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
RAPIDAPI_KEY     = os.environ.get("X_RAPIDAPI_KEY", "")

FDORG_BASE   = "https://api.football-data.org/v4"
TSDB_BASE    = "https://www.thesportsdb.com/api/v1/json/3"
RAPID_BASE   = "https://api-football-v1.p.rapidapi.com/v3"

FDORG_LEAGUE_IDS = {
    "premier-league":    "PL",
    "la-liga":           "PD",
    "bundesliga":        "BL1",
    "serie-a":           "SA",
    "ligue1":            "FL1",
    "champions-league":  "CL",
    "eredivisie":        "DED",
    "primeira-liga":     "PPL",
}

SLUG_TO_TSDB_LEAGUE = {
    "premier-league":   "4328",
    "la-liga":          "4335",
    "bundesliga":       "4331",
    "serie-a":          "4332",
    "ligue1":           "4334",
    "champions-league": "4480",
}


def _headers_fdorg():
    return {"X-Auth-Token": FOOTBALL_API_KEY}


def _fetch_fdorg(league_slugs: list[str], days_ahead: int = 7) -> list[dict]:
    matches = []
    today = datetime.date.today()
    date_to = today + datetime.timedelta(days=days_ahead)

    for slug in league_slugs:
        lid = FDORG_LEAGUE_IDS.get(slug)
        if not lid:
            continue
        url = (f"{FDORG_BASE}/competitions/{lid}/matches"
               f"?dateFrom={today}&dateTo={date_to}&status=SCHEDULED")
        try:
            r = requests.get(url, headers=_headers_fdorg(), timeout=10)
            r.raise_for_status()
            for m in r.json().get("matches", []):
                home = m["homeTeam"]["name"]
                away = m["awayTeam"]["name"]
                kickoff = m.get("utcDate", "")
                matches.append({
                    "home_team":   home,
                    "away_team":   away,
                    "league_slug": slug,
                    "kickoff_time": kickoff,
                    "match_id":    f"fdorg_{m['id']}",
                    "match_title": f"{home} vs {away}",
                    "league_name": slug.replace('-', ' ').title(),
                })
        except Exception as e:
            logger.warning(f"football-data.org error ({slug}): {e}")
    return matches


def _fetch_tsdb(league_slugs: list[str]) -> list[dict]:
    matches = []
    for slug in league_slugs:
        lid = SLUG_TO_TSDB_LEAGUE.get(slug)
        if not lid:
            continue
        url = f"{TSDB_BASE}/eventsnextleague.php?id={lid}"
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            events = r.json().get("events") or []
            for e in events:
                home = e.get("strHomeTeam", "")
                away = e.get("strAwayTeam", "")
                kickoff = e.get("dateEvent", "") + "T" + (e.get("strTime") or "00:00:00") + "Z"
                eid = e.get("idEvent", "")
                matches.append({
                    "home_team":   home,
                    "away_team":   away,
                    "league_slug": slug,
                    "kickoff_time": kickoff,
                    "match_id":    f"tsdb_{eid}",
                    "match_title": f"{home} vs {away}",
                    "league_name": e.get("strLeague", slug.replace('-', ' ').title()),
                })
        except Exception as e:
            logger.warning(f"TheSportsDB error ({slug}): {e}")
    return matches


def _fetch_rapid(league_slugs: list[str], days_ahead: int = 7) -> list[dict]:
    if not RAPIDAPI_KEY:
        return []
    RAPID_LEAGUE_IDS = {
        "premier-league": 39, "la-liga": 140, "bundesliga": 78,
        "serie-a": 135, "ligue1": 61, "champions-league": 2,
        "mls": 253, "eredivisie": 88,
    }
    matches = []
    today = datetime.date.today()
    headers = {
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        "x-rapidapi-key":  RAPIDAPI_KEY,
    }
    for slug in league_slugs:
        lid = RAPID_LEAGUE_IDS.get(slug)
        if not lid:
            continue
        for d in range(days_ahead):
            day = today + datetime.timedelta(days=d)
            url = f"{RAPID_BASE}/fixtures?league={lid}&date={day}&season={today.year}"
            try:
                r = requests.get(url, headers=headers, timeout=10)
                r.raise_for_status()
                for fx in r.json().get("response", []):
                    teams = fx.get("teams", {})
                    home = teams.get("home", {}).get("name", "")
                    away = teams.get("away", {}).get("name", "")
                    kickoff = fx.get("fixture", {}).get("date", "")
                    fid = fx.get("fixture", {}).get("id", "")
                    odds_h = odds_d = odds_a = None
                    for o in fx.get("odds", []):
                        for b in o.get("bookmakers", [])[:1]:
                            for v in b.get("bets", []):
                                if v["name"] == "Match Winner":
                                    vals = {x["value"]: float(x["odd"]) for x in v["values"]}
                                    odds_h = vals.get("Home")
                                    odds_d = vals.get("Draw")
                                    odds_a = vals.get("Away")
                    matches.append({
                        "home_team":   home,
                        "away_team":   away,
                        "league_slug": slug,
                        "kickoff_time": kickoff,
                        "match_id":    f"rapid_{fid}",
                        "match_title": f"{home} vs {away}",
                        "league_name": slug.replace('-', ' ').title(),
                        "odds_home":  odds_h,
                        "odds_draw":  odds_d,
                        "odds_away":  odds_a,
                    })
            except Exception as ex:
                logger.warning(f"API-Football error ({slug}): {ex}")
    return matches


def _generate_mock_fixtures() -> list[dict]:
    """
    Generates plausible fixtures using well-known teams, so the system
    can produce predictions even without any API key.
    """
    today = datetime.date.today()
    fixture_templates = [
        ("Arsenal", "Chelsea",       "premier-league", "Premier League"),
        ("Manchester City", "Liverpool", "premier-league", "Premier League"),
        ("Real Madrid", "Barcelona",  "la-liga",         "La Liga"),
        ("Bayern Munich", "Borussia Dortmund", "bundesliga", "Bundesliga"),
        ("Juventus", "Inter Milan",  "serie-a",         "Serie A"),
        ("PSG", "Marseille",         "ligue1",          "Ligue 1"),
        ("Manchester United", "Tottenham", "premier-league", "Premier League"),
        ("Atletico Madrid", "Valencia", "la-liga",       "La Liga"),
        ("AC Milan", "AS Roma",       "serie-a",         "Serie A"),
        ("Ajax", "PSV",              "eredivisie",      "Eredivisie"),
        ("Real Madrid", "Atletico Madrid", "la-liga",   "La Liga"),
        ("Liverpool", "Manchester City", "premier-league", "Premier League"),
        ("Napoli", "Juventus",        "serie-a",         "Serie A"),
        ("Dortmund", "Leipzig",       "bundesliga",      "Bundesliga"),
        ("Newcastle", "Aston Villa",  "premier-league", "Premier League"),
    ]
    fixtures = []
    for idx, (home, away, slug, league) in enumerate(fixture_templates):
        day = today + datetime.timedelta(days=(idx % 7) + 1)
        kickoff = f"{day}T{15 + (idx % 6)}:00:00Z"
        fixtures.append({
            "home_team":   home,
            "away_team":   away,
            "league_slug": slug,
            "kickoff_time": kickoff,
            "match_id":    f"mock_{home.lower().replace(' ', '_')}_{away.lower().replace(' ', '_')}_{day}",
            "match_title": f"{home} vs {away}",
            "league_name": league,
        })
    return fixtures


ALL_LEAGUES = list(FDORG_LEAGUE_IDS.keys())


def fetch_upcoming_matches(leagues: list[str] | None = None, days_ahead: int = 7) -> list[dict]:
    if leagues is None:
        leagues = ALL_LEAGUES

    matches: list[dict] = []

    # Priority 1: football-data.org (best data quality, needs free API key)
    if FOOTBALL_API_KEY:
        logger.info("Fetching from football-data.org…")
        matches = _fetch_fdorg(leagues, days_ahead)

    # Priority 2: API-Football via RapidAPI
    if not matches and RAPIDAPI_KEY:
        logger.info("Fetching from API-Football (RapidAPI)…")
        matches = _fetch_rapid(leagues, days_ahead)

    # Priority 3: The Sports DB (free, no key)
    if not matches:
        logger.info("Fetching from TheSportsDB (free)…")
        matches = _fetch_tsdb(leagues)

    # Fallback: generated fixtures
    if not matches:
        logger.info("No API data – using generated fixture list")
        matches = _generate_mock_fixtures()

    # Deduplicate by match_id
    seen, unique = set(), []
    for m in matches:
        mid = m.get("match_id", "")
        if mid and mid not in seen:
            seen.add(mid)
            unique.append(m)

    logger.info(f"Total upcoming matches: {len(unique)}")
    return unique

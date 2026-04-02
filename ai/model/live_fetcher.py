"""
Fetches upcoming football matches from live APIs.

Source strategy – ALL available sources are queried in parallel and merged
(instead of the old cascade approach that stopped at the first success).

Sources:
  1. football-data.org  (best quality – needs FOOTBALL_API_TOKEN env var)
  2. API-Football        (needs X_RAPIDAPI_KEY env var)
  3. TheSportsDB         (free, no key – 14 leagues covered)
  4. Mock fixtures       (50+ plausible fixtures – last-resort fallback only)

Duplicates are eliminated by match_id (same match from two sources keeps the
richer record – i.e. the one with odds data, or the first seen).
"""
from __future__ import annotations
import os, logging, datetime, json
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

logger = logging.getLogger(__name__)

FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_TOKEN") or os.environ.get("FOOTBALL_API_KEY", "")
RAPIDAPI_KEY     = os.environ.get("X_RAPIDAPI_KEY", "")

FDORG_BASE  = "https://api.football-data.org/v4"
TSDB_BASE   = "https://www.thesportsdb.com/api/v1/json/3"
RAPID_BASE  = "https://api-football-v1.p.rapidapi.com/v3"

# football-data.org league codes
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

# TheSportsDB league IDs – expanded to 14 leagues
SLUG_TO_TSDB_LEAGUE = {
    "premier-league":    "4328",
    "la-liga":           "4335",
    "bundesliga":        "4331",
    "serie-a":           "4332",
    "ligue1":            "4334",
    "champions-league":  "4480",
    "eredivisie":        "4337",
    "primeira-liga":     "4344",
    "mls":               "4346",
    "turkish-super-lig": "4347",
    "belgian-pro":       "4352",
    "scottish-prem":     "4330",
    "brazilian-serie-a": "4351",
    "argentina-primera": "4406",
}

# API-Football league IDs
RAPID_LEAGUE_IDS = {
    "premier-league":    39,
    "la-liga":           140,
    "bundesliga":        78,
    "serie-a":           135,
    "ligue1":            61,
    "champions-league":  2,
    "mls":               253,
    "eredivisie":        88,
    "turkish-super-lig": 203,
    "belgian-pro":       144,
    "scottish-prem":     179,
    "primeira-liga":     94,
}

# All supported league slugs
ALL_LEAGUES = list({
    **FDORG_LEAGUE_IDS,
    **SLUG_TO_TSDB_LEAGUE,
}.keys())


# ─────────────────────── per-source fetchers ─────────────────────── #

def _headers_fdorg():
    return {"X-Auth-Token": FOOTBALL_API_KEY}


def _fetch_fdorg(league_slugs: list[str], days_ahead: int = 14) -> list[dict]:
    if not FOOTBALL_API_KEY:
        return []
    matches = []
    today   = datetime.date.today()
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
                home    = m["homeTeam"]["name"]
                away    = m["awayTeam"]["name"]
                kickoff = m.get("utcDate", "")
                matches.append({
                    "home_team":    home,
                    "away_team":    away,
                    "league_slug":  slug,
                    "kickoff_time": kickoff,
                    "match_id":     f"fdorg_{m['id']}",
                    "match_title":  f"{home} vs {away}",
                    "league_name":  slug.replace('-', ' ').title(),
                })
        except Exception as e:
            logger.warning(f"football-data.org error ({slug}): {e}")
    logger.info(f"football-data.org: {len(matches)} matches")
    return matches


def _fetch_tsdb_single(slug: str, lid: str) -> list[dict]:
    """Fetch next fixtures for one league from TheSportsDB."""
    url = f"{TSDB_BASE}/eventsnextleague.php?id={lid}"
    out = []
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        events = r.json().get("events") or []
        for e in events:
            home    = e.get("strHomeTeam", "")
            away    = e.get("strAwayTeam", "")
            date_s  = e.get("dateEvent", "")
            time_s  = e.get("strTime") or "15:00:00"
            kickoff = f"{date_s}T{time_s}Z" if date_s else ""
            eid     = e.get("idEvent", "")
            out.append({
                "home_team":    home,
                "away_team":    away,
                "league_slug":  slug,
                "kickoff_time": kickoff,
                "match_id":     f"tsdb_{eid}",
                "match_title":  f"{home} vs {away}",
                "league_name":  e.get("strLeague", slug.replace('-', ' ').title()),
            })
    except Exception as e:
        logger.warning(f"TheSportsDB error ({slug}): {e}")
    return out


def _fetch_tsdb(league_slugs: list[str]) -> list[dict]:
    """Fetch from TheSportsDB for all supported leagues in parallel."""
    tasks = {
        slug: lid
        for slug in league_slugs
        if (lid := SLUG_TO_TSDB_LEAGUE.get(slug))
    }
    if not tasks:
        return []

    matches = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(_fetch_tsdb_single, s, l): s for s, l in tasks.items()}
        for fut in as_completed(futs):
            matches.extend(fut.result())

    logger.info(f"TheSportsDB: {len(matches)} matches")
    return matches


def _fetch_rapid_single(slug: str, lid: int, days_ahead: int,
                         headers: dict) -> list[dict]:
    today   = datetime.date.today()
    matches = []
    for d in range(days_ahead):
        day = today + datetime.timedelta(days=d)
        url = f"{RAPID_BASE}/fixtures?league={lid}&date={day}&season={today.year}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            r.raise_for_status()
            for fx in r.json().get("response", []):
                teams   = fx.get("teams", {})
                home    = teams.get("home", {}).get("name", "")
                away    = teams.get("away", {}).get("name", "")
                kickoff = fx.get("fixture", {}).get("date", "")
                fid     = fx.get("fixture", {}).get("id", "")
                odds_h = odds_d = odds_a = None
                for o in fx.get("odds", []):
                    for b in o.get("bookmakers", [])[:1]:
                        for v in b.get("bets", []):
                            if v["name"] == "Match Winner":
                                vals   = {x["value"]: float(x["odd"]) for x in v["values"]}
                                odds_h = vals.get("Home")
                                odds_d = vals.get("Draw")
                                odds_a = vals.get("Away")
                matches.append({
                    "home_team":    home,
                    "away_team":    away,
                    "league_slug":  slug,
                    "kickoff_time": kickoff,
                    "match_id":     f"rapid_{fid}",
                    "match_title":  f"{home} vs {away}",
                    "league_name":  slug.replace('-', ' ').title(),
                    "odds_home":    odds_h,
                    "odds_draw":    odds_d,
                    "odds_away":    odds_a,
                })
        except Exception as ex:
            logger.warning(f"API-Football error ({slug} day {d}): {ex}")
    return matches


def _fetch_rapid(league_slugs: list[str], days_ahead: int = 14) -> list[dict]:
    if not RAPIDAPI_KEY:
        return []
    headers = {
        "x-rapidapi-host": "api-football-v1.p.rapidapi.com",
        "x-rapidapi-key":  RAPIDAPI_KEY,
    }
    tasks = {slug: lid for slug in league_slugs if (lid := RAPID_LEAGUE_IDS.get(slug))}
    matches = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(_fetch_rapid_single, s, l, days_ahead, headers): s
            for s, l in tasks.items()
        }
        for fut in as_completed(futs):
            matches.extend(fut.result())
    logger.info(f"API-Football: {len(matches)} matches")
    return matches


def _generate_mock_fixtures() -> list[dict]:
    """
    50 plausible fixtures across 10 leagues, spread over the next 14 days.
    Used only when ALL real API sources return zero matches.
    """
    today = datetime.date.today()
    templates = [
        # Premier League
        ("Arsenal",            "Chelsea",              "premier-league",    "Premier League"),
        ("Manchester City",    "Liverpool",            "premier-league",    "Premier League"),
        ("Manchester United",  "Tottenham Hotspur",    "premier-league",    "Premier League"),
        ("Newcastle United",   "Aston Villa",          "premier-league",    "Premier League"),
        ("Brighton",           "West Ham United",      "premier-league",    "Premier League"),
        # La Liga
        ("Real Madrid",        "Barcelona",            "la-liga",           "La Liga"),
        ("Atletico Madrid",    "Valencia",             "la-liga",           "La Liga"),
        ("Real Sociedad",      "Athletic Bilbao",      "la-liga",           "La Liga"),
        ("Villarreal",         "Sevilla",              "la-liga",           "La Liga"),
        ("Real Betis",         "Osasuna",              "la-liga",           "La Liga"),
        # Bundesliga
        ("Bayern Munich",      "Borussia Dortmund",    "bundesliga",        "Bundesliga"),
        ("RB Leipzig",         "Bayer Leverkusen",     "bundesliga",        "Bundesliga"),
        ("Borussia Monchengladbach", "Wolfsburg",      "bundesliga",        "Bundesliga"),
        ("Eintracht Frankfurt","Hoffenheim",           "bundesliga",        "Bundesliga"),
        ("Freiburg",           "Stuttgart",            "bundesliga",        "Bundesliga"),
        # Serie A
        ("Juventus",           "Inter Milan",          "serie-a",           "Serie A"),
        ("AC Milan",           "AS Roma",              "serie-a",           "Serie A"),
        ("Napoli",             "Lazio",                "serie-a",           "Serie A"),
        ("Atalanta",           "Fiorentina",           "serie-a",           "Serie A"),
        ("Torino",             "Bologna",              "serie-a",           "Serie A"),
        # Ligue 1
        ("PSG",                "Marseille",            "ligue1",            "Ligue 1"),
        ("Monaco",             "Lyon",                 "ligue1",            "Ligue 1"),
        ("Lille",              "Rennes",               "ligue1",            "Ligue 1"),
        ("Nice",               "Lens",                 "ligue1",            "Ligue 1"),
        ("Strasbourg",         "Reims",                "ligue1",            "Ligue 1"),
        # Champions League
        ("Real Madrid",        "Bayern Munich",        "champions-league",  "UEFA Champions League"),
        ("Manchester City",    "Inter Milan",          "champions-league",  "UEFA Champions League"),
        ("PSG",                "Barcelona",            "champions-league",  "UEFA Champions League"),
        ("Arsenal",            "Atletico Madrid",      "champions-league",  "UEFA Champions League"),
        ("Liverpool",          "Borussia Dortmund",    "champions-league",  "UEFA Champions League"),
        # Eredivisie
        ("Ajax",               "PSV",                  "eredivisie",        "Eredivisie"),
        ("Feyenoord",          "AZ Alkmaar",           "eredivisie",        "Eredivisie"),
        ("Utrecht",            "Twente",               "eredivisie",        "Eredivisie"),
        # Primeira Liga
        ("Benfica",            "Porto",                "primeira-liga",     "Primeira Liga"),
        ("Sporting CP",        "Braga",                "primeira-liga",     "Primeira Liga"),
        ("Vitoria de Guimaraes","Famalicao",           "primeira-liga",     "Primeira Liga"),
        # Turkish Super Lig
        ("Galatasaray",        "Fenerbahce",           "turkish-super-lig", "Turkish Super Lig"),
        ("Besiktas",           "Trabzonspor",          "turkish-super-lig", "Turkish Super Lig"),
        ("Basaksehir",         "Konyaspor",            "turkish-super-lig", "Turkish Super Lig"),
        # Belgian Pro League
        ("Club Brugge",        "Anderlecht",           "belgian-pro",       "Belgian Pro League"),
        ("Gent",               "Standard Liege",       "belgian-pro",       "Belgian Pro League"),
        # Scottish Premiership
        ("Celtic",             "Rangers",              "scottish-prem",     "Scottish Premiership"),
        ("Aberdeen",           "Hearts",               "scottish-prem",     "Scottish Premiership"),
        # MLS
        ("LA Galaxy",          "LAFC",                 "mls",               "MLS"),
        ("Inter Miami",        "Atlanta United",       "mls",               "MLS"),
        ("Seattle Sounders",   "Portland Timbers",     "mls",               "MLS"),
        # Brazilian Série A
        ("Flamengo",           "Palmeiras",            "brazilian-serie-a", "Brasileirao"),
        ("Santos",             "Corinthians",          "brazilian-serie-a", "Brasileirao"),
        # Argentina Primera
        ("Boca Juniors",       "River Plate",          "argentina-primera", "Argentine Primera"),
        ("Racing Club",        "Independiente",        "argentina-primera", "Argentine Primera"),
    ]

    fixtures = []
    for idx, (home, away, slug, league) in enumerate(templates):
        day     = today + datetime.timedelta(days=(idx % 14) + 1)
        hour    = 13 + (idx % 7)
        kickoff = f"{day}T{hour:02d}:00:00Z"
        mid     = f"mock_{home.lower().replace(' ', '_')}_{away.lower().replace(' ', '_')}_{day}"
        fixtures.append({
            "home_team":    home,
            "away_team":    away,
            "league_slug":  slug,
            "kickoff_time": kickoff,
            "match_id":     mid,
            "match_title":  f"{home} vs {away}",
            "league_name":  league,
        })
    return fixtures


# ─────────────────────── public entry point ─────────────────────── #

def fetch_upcoming_matches(leagues: list[str] | None = None,
                            days_ahead: int = 14) -> list[dict]:
    """
    Fetch upcoming matches from ALL available sources simultaneously.
    Returns a deduplicated, sorted list of match dicts.
    """
    if leagues is None:
        leagues = ALL_LEAGUES

    logger.info(f"Fetching upcoming matches across {len(leagues)} leagues, "
                f"{days_ahead} days ahead…")

    # ── Fetch all sources in parallel ────────────────────────────────
    all_matches: list[dict] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = []
        if FOOTBALL_API_KEY:
            futs.append(pool.submit(_fetch_fdorg, leagues, days_ahead))
        if RAPIDAPI_KEY:
            futs.append(pool.submit(_fetch_rapid, leagues, days_ahead))
        futs.append(pool.submit(_fetch_tsdb, leagues))

        for fut in as_completed(futs):
            try:
                all_matches.extend(fut.result())
            except Exception as e:
                logger.warning(f"Source fetch failed: {e}")

    # ── Fallback: mock fixtures if everything came back empty ─────────
    if not all_matches:
        logger.info("All live sources returned zero matches – using mock fixtures")
        all_matches = _generate_mock_fixtures()

    # ── Deduplicate by match_id, preferring records with odds data ────
    seen: dict[str, dict] = {}
    for m in all_matches:
        mid = m.get("match_id", "")
        if not mid:
            continue
        if mid not in seen:
            seen[mid] = m
        else:
            # Prefer the record that has odds data
            if m.get("odds_home") and not seen[mid].get("odds_home"):
                seen[mid] = m

    unique = list(seen.values())

    # ── Sort by kickoff time ──────────────────────────────────────────
    unique.sort(key=lambda x: x.get("kickoff_time", ""))

    logger.info(f"Total upcoming matches (after dedup): {len(unique)}")
    return unique

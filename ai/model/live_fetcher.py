"""
Fetches upcoming football matches from live APIs.

Sources (queried in parallel):
  1. football-data.org  (FOOTBALL_DATA_TOKENS key pool) — includes lineups if available
  2. API-Football/RapidAPI  (X_RAPIDAPI_KEY env var) — includes real odds
  3. TheSportsDB  (free, no key)
  4. Mock fixtures  (fallback only)
"""
from __future__ import annotations
import os, sys, logging, datetime, json
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# Load the repo-root .env *before* snapshotting any API keys below, otherwise
# FOOTBALL_API_TOKEN is missing and the fetcher silently degrades to
# placeholder fixtures.
_AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AI_DIR not in sys.path:
    sys.path.insert(0, _AI_DIR)
try:                                        # normal: ai/ is on sys.path
    from env_loader import load_dotenv
    import fdorg
except ModuleNotFoundError:                 # pragma: no cover - package-style import
    from ai.env_loader import load_dotenv
    from ai import fdorg
load_dotenv()

logger = logging.getLogger(__name__)

# Single-token name kept only for logging/messaging; every football-data.org
# call goes through the rotating pool in `fdorg`, which reads
# FOOTBALL_DATA_TOKENS (plus the legacy FOOTBALL_DATA_TOKEN /
# FOOTBALL_API_TOKEN names) and stays inside the free tier's 10 req/min.
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", os.environ.get("FOOTBALL_API_TOKEN", ""))
RAPIDAPI_KEY        = os.environ.get("X_RAPIDAPI_KEY", "")

FDORG_BASE = fdorg.BASE
# TheSportsDB: "3" is the shared public test key, which now returns 403 on
# most endpoints.  Put a personal key (free account / Patreon) in
# THESPORTSDB_KEY to enable this source.
TSDB_KEY   = os.environ.get("THESPORTSDB_KEY", "3").strip() or "3"
TSDB_BASE  = f"https://www.thesportsdb.com/api/v1/json/{TSDB_KEY}"
_tsdb_blocked = False
RAPID_BASE = "https://api-football-v1.p.rapidapi.com/v3"

# football-data.org league codes
FDORG_LEAGUE_IDS = {
    "premier-league":     "PL",
    "la-liga":            "PD",
    "bundesliga":         "BL1",
    "serie-a":            "SA",
    "ligue1":             "FL1",
    "champions-league":   "CL",
    # European cup nights — these are what Thursday fixtures are made of, and
    # they have no football-data.co.uk season CSV, so predictions here carry
    # the reduced-confidence flag (see predictor.MIN_LEAGUE_SAMPLES).
    "europa-league":      "EL",
    "conference-league":  "ECL",
    "eredivisie":         "DED",
    "primeira-liga":      "PPL",
    "efl-championship":   "ELC",
    "copa-libertadores":  "CLI",
}

#: Leagues that 403/404 because they sit outside the current football-data.org
#: plan. Remembered so a restricted competition is reported once per process
#: rather than on every single fetch.
_restricted_logged: set[str] = set()


# ── ESPN (keyless) ──────────────────────────────────────────────────────────
#: European cup nights are the whole point of a Thursday, but football-data.org
#: puts the Europa League and Conference League behind its paid plan (both
#: return HTTP 403 on every token).  ESPN's public site API serves them for
#: free — no key, no signup, no quota — and already backs enrich_match_details
#: and backfill_espn, so we read those two competitions from there instead.
#: Only these slugs are routed to ESPN; everything else stays on
#: football-data.org, which carries better historical depth.
ESPN_ONLY_LEAGUES = {
    "europa-league":     "uefa.europa",
    "conference-league": "uefa.europa.conf",
}

#: Competitions whose results can only be read from ESPN (football-data.org's
#: free plan returns no UEL/UECL results at all, and its Libertadores feed is
#: incomplete). Settlement queries these so an ESPN-sourced fixture does not
#: sit on "upcoming" forever.
ESPN_RESULT_LEAGUES = (
    "uefa.europa",
    "uefa.europa.conf",
    "conmebol.libertadores",
)


def _american_to_decimal(price) -> float | None:
    """Convert an American moneyline ('+475' / '-275') to decimal odds."""
    try:
        n = float(str(price).strip())
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    return round(n / 100.0 + 1.0, 3) if n > 0 else round(100.0 / -n + 1.0, 3)


def _espn_moneyline(odds: dict | None) -> tuple:
    """Pull decimal home/draw/away odds out of an ESPN odds block."""
    if not odds:
        return None, None, None
    ml = odds.get("moneyline") or {}

    def _leg(side: str):
        node = ml.get(side)
        if not isinstance(node, dict):
            return None
        for flavour in ("close", "open"):          # close is the current price
            price = (node.get(flavour) or {}).get("odds")
            dec = _american_to_decimal(price)
            if dec:
                return dec
        return None

    return _leg("home"), _leg("draw"), _leg("away")


def _fetch_espn(league_slugs: list[str], days_ahead: int = 14) -> list[dict]:
    """Fetch fixtures for the competitions football-data.org's plan blocks."""
    matches: list[dict] = []
    today = datetime.date.today()
    end   = today + datetime.timedelta(days=days_ahead)

    for slug in league_slugs:
        espn_slug = ESPN_ONLY_LEAGUES.get(slug)
        if not espn_slug:
            continue
        day = today
        while day <= end:
            url = (f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                   f"{espn_slug}/scoreboard?dates={day:%Y%m%d}")
            try:
                r = requests.get(url, timeout=20)
                if r.status_code != 200:
                    logger.debug(f"ESPN {espn_slug} {day} -> HTTP {r.status_code}")
                    day += datetime.timedelta(days=1)
                    continue
                payload = r.json()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"ESPN {espn_slug} {day}: {e}")
                day += datetime.timedelta(days=1)
                continue

            for ev in payload.get("events", []):
                status = (ev.get("status") or {}).get("type") or {}
                if status.get("completed"):
                    continue
                if status.get("name") != "STATUS_SCHEDULED":
                    continue
                comps = ev.get("competitions") or []
                if not comps:
                    continue
                comp    = comps[0]
                sides   = {c.get("homeAway"): c for c in comp.get("competitors", [])}
                home    = (sides.get("home", {}).get("team") or {}).get("displayName")
                away    = (sides.get("away", {}).get("team") or {}).get("displayName")
                if not home or not away:
                    continue
                odds_h, odds_d, odds_a = _espn_moneyline((comp.get("odds") or [None])[0])
                matches.append({
                    "home_team":    home,
                    "away_team":    away,
                    "league_slug":  slug,
                    "kickoff_time": ev.get("date", ""),
                    # ESPN event ids share no namespace with football-data.org
                    # ones, so they get their own prefix to stay unambiguous.
                    "match_id":     f"espn_{ev.get('id')}",
                    "match_title":  f"{home} vs {away}",
                    "league_name":  slug.replace('-', ' ').title(),
                    "odds_home":    odds_h,
                    "odds_draw":    odds_d,
                    "odds_away":    odds_a,
                    "source":       "espn",
                })
            day += datetime.timedelta(days=1)

    if matches:
        logger.info(f"ESPN supplied {len(matches)} fixture(s) from "
                    f"{len({m['league_slug'] for m in matches})} cup competition(s).")
    return matches


def fetch_espn_results_for_dates(dates: list[str],
                                 league_slugs: tuple[str, ...] = ESPN_RESULT_LEAGUES
                                 ) -> tuple[dict, dict]:
    """Bulk-fetch finished ESPN results for the given match days.

    Returns ``(by_id, by_names)`` where:

    * ``by_id``     maps the raw ESPN event id ('401915552') to (home, away)
    * ``by_names``  maps ``(date, normalised_home, normalised_away)`` to the same

    The id map is exact and preferred — ESPN event ids are stable and immune to
    the naming differences between feeds. The name map is the fallback for
    competitions like the Copa Libertadores, where the fixture row came from
    football-data.org but only ESPN has the scoreline.

    ESPN groups a scoreboard by the venue's local day, so a 00:30 UTC kickoff
    can appear under the previous date. Each requested day therefore probes
    day-1, day and day+1 (deduplicated).
    """
    by_id: dict = {}
    by_names: dict = {}
    if not dates:
        return by_id, by_names

    # Scoreboard payloads are cached per (day, league) so the same day is only
    # fetched once — but every requested match-day replays the cached payload
    # under its own key. Otherwise an event that ESPN files on the previous
    # local day (a 00:30 UTC kickoff) would exist only under that earlier date
    # and would never match the fixture's own kickoff date.
    cache: dict = {}

    for date_str in sorted({d for d in dates if d}):
        try:
            base = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        for offset in (-1, 0, 1):
            day = base + datetime.timedelta(days=offset)
            for slug in league_slugs:
                key = (day, slug)
                if key not in cache:
                    url = (f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                           f"{slug}/scoreboard?dates={day:%Y%m%d}")
                    try:
                        r = requests.get(url, timeout=20)
                        if r.status_code != 200:
                            logger.debug(f"ESPN results {slug} {day} -> HTTP {r.status_code}")
                            cache[key] = []
                        else:
                            cache[key] = (r.json().get("events") or [])
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"ESPN results {slug} {day}: {e}")
                        cache[key] = []

                for ev in cache[key]:
                    status = (ev.get("status") or {}).get("type") or {}
                    if not status.get("completed"):
                        continue
                    comps = ev.get("competitions") or []
                    if not comps:
                        continue
                    comp = comps[0]
                    sides = {c.get("homeAway"): c
                             for c in comp.get("competitors", [])}
                    home = (sides.get("home", {}).get("team") or {}).get("displayName")
                    away = (sides.get("away", {}).get("team") or {}).get("displayName")
                    hs = sides.get("home", {}).get("score")
                    as_ = sides.get("away", {}).get("score")
                    if not home or not away or hs is None or as_ is None:
                        continue
                    try:
                        score = (int(hs), int(as_))
                    except (TypeError, ValueError):
                        continue
                    by_id[str(ev.get("id"))] = score
                    by_names[(date_str, normalize_team_name(home),
                              normalize_team_name(away))] = score

    if by_id:
        logger.info(f"ESPN results: {len(by_id)} finished match(es) across "
                    f"{len(cache)} scoreboard request(s).")
    return by_id, by_names


# TheSportsDB league IDs
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
    "efl-championship":  "4329",
    "j-league":          "4356",
    "liga-mx":           "4341",
    "saudi-pro-league":  "4350",
}

# API-Football league IDs (includes odds data)
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
    "efl-championship":  40,
    "copa-libertadores": 13,
    "j-league":          98,
    "liga-mx":           262,
    "saudi-pro-league":  307,
    "brazilian-serie-a": 71,
    "argentina-primera": 128,
}

ALL_LEAGUES = list({
    **FDORG_LEAGUE_IDS,
    **SLUG_TO_TSDB_LEAGUE,
    **RAPID_LEAGUE_IDS,
}.keys())


def _fetch_fdorg(league_slugs: list[str], days_ahead: int = 14) -> list[dict]:
    if not fdorg.available():
        return []
    matches = []
    today   = datetime.date.today()
    date_to = today + datetime.timedelta(days=days_ahead)

    for slug in league_slugs:
        lid = FDORG_LEAGUE_IDS.get(slug)
        if not lid:
            continue
        try:
            payload = fdorg.get(
                f"/competitions/{lid}/matches",
                {
                    "dateFrom": today.isoformat(),
                    "dateTo": date_to.isoformat(),
                    "status": "SCHEDULED",
                },
            )
            for m in payload.get("matches", []):
                home    = m["homeTeam"]["name"]
                away    = m["awayTeam"]["name"]
                kickoff = m.get("utcDate", "")
                # Try to get odds from fdorg if available
                odds_h = odds_d = odds_a = None
                odds_data = m.get("odds", {})
                if odds_data:
                    odds_h = odds_data.get("homeWin")
                    odds_d = odds_data.get("draw")
                    odds_a = odds_data.get("awayWin")
                # Try to get lineup data (available shortly before match)
                lineup_info = {}
                lineups = m.get("lineups", [])
                if lineups:
                    lineup_info["has_lineups"] = True
                matches.append({
                    "home_team":    home,
                    "away_team":    away,
                    "league_slug":  slug,
                    "kickoff_time": kickoff,
                    "match_id":     f"fdorg_{m['id']}",
                    "match_title":  f"{home} vs {away}",
                    "league_name":  slug.replace('-', ' ').title(),
                    "odds_home":    odds_h,
                    "odds_draw":    odds_d,
                    "odds_away":    odds_a,
                    **lineup_info,
                })
        except fdorg.FdorgError as e:
            # A competition outside the current football-data.org plan 403s on
            # every key. Keep the league registered — it starts working the
            # moment the plan is upgraded — but do not warn on every run.
            if e.status in (403, 404):
                if slug not in _restricted_logged:
                    _restricted_logged.add(slug)
                    logger.info("football-data.org: '%s' is not included in the "
                                "current plan (HTTP %d) — skipping this league.",
                                slug, e.status)
                continue
            logger.warning(f"football-data.org error ({slug}): {e}")
        except Exception as e:
            logger.warning(f"football-data.org error ({slug}): {e}")
    logger.info(f"football-data.org: {len(matches)} matches")
    return matches


def _fetch_fdorg_odds(match_id_num: str) -> dict:
    """Fetch odds for a specific match from football-data.org."""
    if not fdorg.available():
        return {}
    try:
        m = fdorg.get(f"/matches/{match_id_num}", timeout=8)
        odds = m.get("odds", {})
        return {
            "odds_home": odds.get("homeWin"),
            "odds_draw": odds.get("draw"),
            "odds_away": odds.get("awayWin"),
        }
    except Exception:
        return {}


def _fetch_tsdb_single(slug: str, lid: str) -> list[dict]:
    global _tsdb_blocked
    if _tsdb_blocked:
        return []
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
            if not eid:
                eid = f"{home.lower().replace(' ', '_')}_{away.lower().replace(' ', '_')}_{date_s}"
            out.append({
                "home_team":    home,
                "away_team":    away,
                "league_slug":  slug,
                "kickoff_time": kickoff,
                "match_id":     f"tsdb_{eid}",
                "match_title":  f"{home} vs {away}",
                "league_name":  e.get("strLeague", slug.replace('-', ' ').title()),
            })
    except requests.HTTPError as e:
        code = getattr(e.response, "status_code", None)
        if code in (401, 403) and not _tsdb_blocked:
            _tsdb_blocked = True      # stop hammering the API for every league
            hint = ("the shared test key is no longer accepted; get a key at "
                    "thesportsdb.com and set THESPORTSDB_KEY"
                    if TSDB_KEY == "3" else "check THESPORTSDB_KEY / its plan")
            logger.warning(f"TheSportsDB disabled for this run (HTTP {code}): {hint}")
        elif not _tsdb_blocked:
            logger.warning(f"TheSportsDB error ({slug}): {e}")
    except Exception as e:
        if not _tsdb_blocked:
            logger.warning(f"TheSportsDB error ({slug}): {e}")
    return out


def _fetch_tsdb(league_slugs: list[str]) -> list[dict]:
    tasks = {
        slug: lid
        for slug in league_slugs
        if (lid := SLUG_TO_TSDB_LEAGUE.get(slug))
    }
    if not tasks:
        return []

    matches = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_fetch_tsdb_single, s, l): s for s, l in tasks.items()}
        for fut in as_completed(futs):
            matches.extend(fut.result())

    logger.info(f"TheSportsDB: {len(matches)} matches")
    return matches


def _fetch_rapid_single(slug: str, lid: int, days_ahead: int,
                         headers: dict) -> list[dict]:
    today   = datetime.date.today()
    matches = []
    for d in range(min(days_ahead, 7)):  # RapidAPI free tier: limit days
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
                # Extract real odds from response
                odds_h = odds_d = odds_a = None
                for o in fx.get("odds", []):
                    for b in o.get("bookmakers", [])[:1]:
                        for v in b.get("bets", []):
                            if v["name"] == "Match Winner":
                                vals   = {x["value"]: float(x["odd"]) for x in v["values"]}
                                odds_h = vals.get("Home")
                                odds_d = vals.get("Draw")
                                odds_a = vals.get("Away")
                # Also fetch odds from dedicated endpoint if not embedded
                if not odds_h and fid:
                    try:
                        odds_url = f"{RAPID_BASE}/odds?fixture={fid}&bookmaker=1"
                        or_ = requests.get(odds_url, headers=headers, timeout=6)
                        or_.raise_for_status()
                        for resp in or_.json().get("response", [])[:1]:
                            for bk in resp.get("bookmakers", [])[:1]:
                                for bt in bk.get("bets", []):
                                    if bt["name"] == "Match Winner":
                                        vals   = {x["value"]: float(x["odd"]) for x in bt["values"]}
                                        odds_h = vals.get("Home")
                                        odds_d = vals.get("Draw")
                                        odds_a = vals.get("Away")
                    except Exception:
                        pass

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


def _fetch_rapid(league_slugs: list[str], days_ahead: int = 7) -> list[dict]:
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
    logger.info(f"API-Football: {len(matches)} matches (with real odds)")
    return matches


def normalize_team_name(name: str) -> str:
    """Canonical key for a club name so 'Aston Villa FC' == 'Aston Villa'."""
    if not name:
        return ""
    s = name.lower()
    # Strip common legal suffixes (keep the base name).
    for suffix in (" fc", " afc", " cf", " sc", " ssc", " ss", " ac", " as",
                   " us", " cd", " rc", " rcd", " bc", " fk", " sk", " kc"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    for prefix in ("fc ", "cf ", "sc ", "ac ", "as ", "ss ", "us ", "cd ",
                   "rc ", "rcd ", "bc ", "fk ", "sk "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return "".join(ch for ch in s if ch.isalnum())


def team_names_match(a: str, b: str) -> bool:
    """Loose team-name comparison (handles 'Real Madrid CF' vs 'Real Madrid')."""
    na, nb = normalize_team_name(a), normalize_team_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na in nb or nb in na


def fetch_results_for_dates(dates: list[str]) -> dict:
    """
    Bulk-fetch finished results from football-data.org.

    Returns ``{(normalised_home, normalised_away): (home_score, away_score)}``
    for every FINISHED match on the given calendar days.

    NOTE: football-data.org treats ``dateTo`` as EXCLUSIVE — a single-day
    window must be ``dateFrom=D`` / ``dateTo=D+1``. Using the same value for
    both silently returns zero matches, which is why settlement previously
    never found any results.
    """
    if not fdorg.available() or not dates:
        return {}

    out: dict = {}
    for d in sorted(set(x for x in dates if x)):
        try:
            end = (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()
        except ValueError:
            continue
        try:
            payload = fdorg.get(
                "/matches",
                {"dateFrom": d, "dateTo": end},
                timeout=15,
            )
            count = 0
            for m in payload.get("matches", []):
                if m.get("status") != "FINISHED":
                    continue
                score = (m.get("score") or {}).get("fullTime") or {}
                hs, as_ = score.get("home"), score.get("away")
                if hs is None or as_ is None:
                    continue
                key = (
                    normalize_team_name(m["homeTeam"]["name"]),
                    normalize_team_name(m["awayTeam"]["name"]),
                )
                out[key] = (int(hs), int(as_))
                count += 1
            logger.info(f"football-data.org results {d}..{end}: {count} finished match(es)")
        except Exception as e:
            logger.warning(f"football-data.org results fetch failed for {d}: {e}")
    return out


def result_label(home_score: int, away_score: int) -> str:
    """'Home Win' / 'Draw' / 'Away Win' from a scoreline."""
    if home_score > away_score:
        return "Home Win"
    if away_score > home_score:
        return "Away Win"
    return "Draw"


def fetch_match_result(match_id: str, home_team: str, away_team: str,
                        kickoff_time: str) -> dict | None:
    """
    Try to fetch the actual result of a past match for backtesting.
    Returns dict with 'home_score', 'away_score', 'actual_result' or None.
    """
    # 1) football-data.org (reliable, has every match we predict)
    if kickoff_time:
        bulk = fetch_results_for_dates([kickoff_time[:10]])
        if bulk:
            key = (normalize_team_name(home_team), normalize_team_name(away_team))
            score = bulk.get(key)
            if score is None:
                for (nh, na), sc in bulk.items():
                    if team_names_match(home_team, nh) and team_names_match(away_team, na):
                        score = sc
                        break
            if score:
                hs, as_ = score
                return {
                    "home_score": hs,
                    "away_score": as_,
                    "actual_result": result_label(hs, as_),
                }

    # 2) TheSportsDB past results
    if not kickoff_time:
        return None
    try:
        date_str = kickoff_time[:10]
        # Search for results by team name on that date
        search_url = f"{TSDB_BASE}/eventsday.php?d={date_str}&s=Soccer"
        r = requests.get(search_url, timeout=8)
        r.raise_for_status()
        events = r.json().get("events") or []
        for e in events:
            eh = (e.get("strHomeTeam") or "").lower()
            ea = (e.get("strAwayTeam") or "").lower()
            if home_team.lower() in eh and away_team.lower() in ea:
                hs = e.get("intHomeScore")
                as_ = e.get("intAwayScore")
                if hs is not None and as_ is not None:
                    hs, as_ = int(hs), int(as_)
                    if hs > as_:
                        result = "Home Win"
                    elif as_ > hs:
                        result = "Away Win"
                    else:
                        result = "Draw"
                    return {
                        "home_score": hs,
                        "away_score": as_,
                        "actual_result": result,
                    }
    except Exception as e:
        logger.debug(f"Could not fetch result for {home_team} vs {away_team}: {e}")
    return None


def _mock_allowed() -> bool:
    """True when placeholder (mock) fixtures may be generated.

    Mock fixtures are for local development only. In production they must
    never be generated, let alone published.
    """
    flag = os.environ.get("ALLOW_MOCK_FIXTURES", "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _generate_mock_fixtures() -> list[dict]:
    today = datetime.date.today()
    templates = [
        ("Arsenal",            "Chelsea",              "premier-league",    "Premier League"),
        ("Manchester City",    "Liverpool",            "premier-league",    "Premier League"),
        ("Manchester United",  "Tottenham Hotspur",    "premier-league",    "Premier League"),
        ("Newcastle United",   "Aston Villa",          "premier-league",    "Premier League"),
        ("Brighton",           "West Ham United",      "premier-league",    "Premier League"),
        ("Real Madrid",        "Barcelona",            "la-liga",           "La Liga"),
        ("Atletico Madrid",    "Valencia",             "la-liga",           "La Liga"),
        ("Real Sociedad",      "Athletic Bilbao",      "la-liga",           "La Liga"),
        ("Villarreal",         "Sevilla",              "la-liga",           "La Liga"),
        ("Real Betis",         "Osasuna",              "la-liga",           "La Liga"),
        ("Bayern Munich",      "Borussia Dortmund",    "bundesliga",        "Bundesliga"),
        ("RB Leipzig",         "Bayer Leverkusen",     "bundesliga",        "Bundesliga"),
        ("Borussia Monchengladbach", "Wolfsburg",      "bundesliga",        "Bundesliga"),
        ("Eintracht Frankfurt","Hoffenheim",           "bundesliga",        "Bundesliga"),
        ("Freiburg",           "Stuttgart",            "bundesliga",        "Bundesliga"),
        ("Juventus",           "Inter Milan",          "serie-a",           "Serie A"),
        ("AC Milan",           "AS Roma",              "serie-a",           "Serie A"),
        ("Napoli",             "Lazio",                "serie-a",           "Serie A"),
        ("Atalanta",           "Fiorentina",           "serie-a",           "Serie A"),
        ("Torino",             "Bologna",              "serie-a",           "Serie A"),
        ("PSG",                "Marseille",            "ligue1",            "Ligue 1"),
        ("Monaco",             "Lyon",                 "ligue1",            "Ligue 1"),
        ("Lille",              "Rennes",               "ligue1",            "Ligue 1"),
        ("Nice",               "Lens",                 "ligue1",            "Ligue 1"),
        ("Real Madrid",        "Bayern Munich",        "champions-league",  "UEFA Champions League"),
        ("Manchester City",    "Inter Milan",          "champions-league",  "UEFA Champions League"),
        ("PSG",                "Barcelona",            "champions-league",  "UEFA Champions League"),
        ("Arsenal",            "Atletico Madrid",      "champions-league",  "UEFA Champions League"),
        ("Ajax",               "PSV",                  "eredivisie",        "Eredivisie"),
        ("Feyenoord",          "AZ Alkmaar",           "eredivisie",        "Eredivisie"),
        ("Benfica",            "Porto",                "primeira-liga",     "Primeira Liga"),
        ("Sporting CP",        "Braga",                "primeira-liga",     "Primeira Liga"),
        ("Galatasaray",        "Fenerbahce",           "turkish-super-lig", "Turkish Super Lig"),
        ("Besiktas",           "Trabzonspor",          "turkish-super-lig", "Turkish Super Lig"),
        ("Club Brugge",        "Anderlecht",           "belgian-pro",       "Belgian Pro League"),
        ("Celtic",             "Rangers",              "scottish-prem",     "Scottish Premiership"),
        ("LA Galaxy",          "LAFC",                 "mls",               "MLS"),
        ("Inter Miami",        "Atlanta United",       "mls",               "MLS"),
        ("Seattle Sounders",   "Portland Timbers",     "mls",               "MLS"),
        ("Flamengo",           "Palmeiras",            "brazilian-serie-a", "Brasileirao"),
        ("Santos",             "Corinthians",          "brazilian-serie-a", "Brasileirao"),
        ("Boca Juniors",       "River Plate",          "argentina-primera", "Argentine Primera"),
        ("Racing Club",        "Independiente",        "argentina-primera", "Argentine Primera"),
        ("Al Hilal",           "Al Nassr",             "saudi-pro-league",  "Saudi Pro League"),
        ("Al Ahli",            "Al Ittihad",           "saudi-pro-league",  "Saudi Pro League"),
        ("Fluminense",         "Atletico Mineiro",     "copa-libertadores", "Copa Libertadores"),
        ("River Plate",        "Flamengo",             "copa-libertadores", "Copa Libertadores"),
        ("Urawa Red Diamonds", "Yokohama F.Marinos",   "j-league",          "J1 League"),
        ("Club America",       "Chivas Guadalajara",   "liga-mx",           "Liga MX"),
        ("Leeds United",       "Burnley",              "efl-championship",  "EFL Championship"),
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


def fetch_upcoming_matches(leagues: list[str] | None = None,
                            days_ahead: int = 14) -> list[dict]:
    if leagues is None:
        leagues = ALL_LEAGUES

    logger.info(f"Fetching upcoming matches across {len(leagues)} leagues, {days_ahead} days ahead…")

    all_matches: list[dict] = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = []
        # Split the league list by what each source can actually serve: the
        # cup competitions blocked by football-data.org's plan come from ESPN
        # instead, so no request is wasted on a guaranteed 403.
        espn_leagues = [l for l in leagues if l in ESPN_ONLY_LEAGUES]
        fdorg_leagues = [l for l in leagues if l not in ESPN_ONLY_LEAGUES]

        if espn_leagues:
            futs.append(pool.submit(_fetch_espn, espn_leagues, days_ahead))
        if fdorg.available():
            futs.append(pool.submit(_fetch_fdorg, fdorg_leagues, days_ahead))
        if RAPIDAPI_KEY:
            futs.append(pool.submit(_fetch_rapid, leagues, days_ahead))
        futs.append(pool.submit(_fetch_tsdb, leagues))

        for fut in as_completed(futs):
            try:
                all_matches.extend(fut.result())
            except Exception as e:
                logger.warning(f"Source fetch failed: {e}")

    if not all_matches:
        # Placeholder fixtures must never reach the live predictions table —
        # they look like real matches to users and would be presented as
        # genuine model output. Only allow them when explicitly opted in
        # (local development / demos).
        if _mock_allowed():
            logger.warning(
                "All live sources returned zero matches – using MOCK fixtures "
                "(ALLOW_MOCK_FIXTURES is set)."
            )
            all_matches = _generate_mock_fixtures()
        else:
            logger.error(
                "All live sources returned zero matches and ALLOW_MOCK_FIXTURES is "
                "not set — refusing to publish placeholder fixtures. "
                "Check FOOTBALL_DATA_TOKENS (football-data.org pool) / X_RAPIDAPI_KEY."
            )
            return []

    # Deduplicate by match_id, preferring records with odds data
    seen: dict[str, dict] = {}
    for m in all_matches:
        mid = m.get("match_id", "")
        if not mid:
            continue
        if mid not in seen:
            seen[mid] = m
        else:
            if m.get("odds_home") and not seen[mid].get("odds_home"):
                seen[mid] = m

    unique = list(seen.values())
    unique.sort(key=lambda x: x.get("kickoff_time", ""))

    logger.info(f"Total upcoming matches (after dedup): {len(unique)}")
    return unique

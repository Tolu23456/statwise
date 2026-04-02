"""
Open-source football data fetchers (no API keys required).

Sources:
  1. football-data.co.uk  — handled by downloader.py
  2. StatsBomb open data  — GitHub raw JSON API
  3. OpenFootball         — openfootball/football.json on GitHub
  4. FiveThirtyEight SPI  — GitHub raw CSV (soccer-spi)
  5. Club Elo             — clubelo.com HTTP API (Elo ratings → synthetic rows)
"""
from __future__ import annotations
import io, logging, os, time
import requests
import pandas as pd

logger = logging.getLogger(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "StatWise-Trainer/1.0"


def _get(url: str, timeout: int = 20, **kw):
    try:
        r = _SESSION.get(url, timeout=timeout, **kw)
        r.raise_for_status()
        return r
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
        return None


# ─── 2. StatsBomb open data (GitHub API) ──────────────────────────────────────

SB_API = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
SB_CACHE = os.path.join(DATA_DIR, "statsbomb_open.csv")


def fetch_statsbomb() -> pd.DataFrame:
    if os.path.exists(SB_CACHE) and (time.time() - os.path.getmtime(SB_CACHE)) < 86400:
        try:
            return pd.read_csv(SB_CACHE, low_memory=False)
        except Exception:
            pass

    r = _get(f"{SB_API}/competitions.json")
    if r is None:
        return pd.DataFrame()

    competitions = r.json()
    rows = []
    for comp in competitions[:30]:  # limit to avoid rate-limit
        cid, sid = comp["competition_id"], comp["season_id"]
        r2 = _get(f"{SB_API}/matches/{cid}/{sid}.json")
        if r2 is None:
            continue
        for m in r2.json():
            hs = m.get("home_score")
            aw = m.get("away_score")
            if hs is None or aw is None:
                continue
            rows.append({
                "home_team": m["home_team"]["home_team_name"],
                "away_team": m["away_team"]["away_team_name"],
                "home_goals": int(hs),
                "away_goals": int(aw),
                "date": m.get("match_date"),
                "league_slug": f"statsbomb-{comp['competition_name'].lower().replace(' ', '-')}",
            })

    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(SB_CACHE, index=False)
        logger.info(f"StatsBomb: {len(df)} matches fetched")
    return df


# ─── 3. OpenFootball (openfootball/football.json) ────────────────────────────

OF_COMPETITIONS = {
    "eng-england/2023-24": "openfootball-premier-league",
    "de-bundesliga/2023-24": "openfootball-bundesliga",
    "es-liga/2023-24": "openfootball-la-liga",
    "it-seriea/2023-24": "openfootball-serie-a",
    "fr-ligue1/2023-24": "openfootball-ligue1",
}
OF_BASE = "https://raw.githubusercontent.com/openfootball/football.json/master"
OF_CACHE = os.path.join(DATA_DIR, "openfootball.csv")


def fetch_openfootball() -> pd.DataFrame:
    if os.path.exists(OF_CACHE) and (time.time() - os.path.getmtime(OF_CACHE)) < 86400:
        try:
            return pd.read_csv(OF_CACHE, low_memory=False)
        except Exception:
            pass

    rows = []
    for path, slug in OF_COMPETITIONS.items():
        r = _get(f"{OF_BASE}/{path}/teams.json")  # check if season exists
        # Try the matches file
        r2 = _get(f"{OF_BASE}/{path}/matches.json")
        if r2 is None:
            continue
        data = r2.json()
        for rd in data.get("rounds", []):
            for m in rd.get("matches", []):
                score = m.get("score", {})
                ft = score.get("ft")
                if not ft or len(ft) < 2:
                    continue
                rows.append({
                    "home_team": m["team1"],
                    "away_team": m["team2"],
                    "home_goals": int(ft[0]),
                    "away_goals": int(ft[1]),
                    "date": m.get("date"),
                    "league_slug": slug,
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(OF_CACHE, index=False)
        logger.info(f"OpenFootball: {len(df)} matches fetched")
    return df


# ─── 4. FiveThirtyEight SPI (soccer-spi) ────────────────────────────────────

FTE_URL = "https://projects.fivethirtyeight.com/soccer-api/club/spi_matches.csv"
FTE_CACHE = os.path.join(DATA_DIR, "fte_spi.csv")


def fetch_fivethirtyeight() -> pd.DataFrame:
    if os.path.exists(FTE_CACHE) and (time.time() - os.path.getmtime(FTE_CACHE)) < 86400:
        try:
            return pd.read_csv(FTE_CACHE, low_memory=False)
        except Exception:
            pass

    r = _get(FTE_URL, timeout=30)
    if r is None:
        return pd.DataFrame()

    raw = pd.read_csv(io.StringIO(r.text), low_memory=False)
    # Only completed matches
    raw = raw.dropna(subset=["score1", "score2"])
    df = pd.DataFrame({
        "home_team": raw["team1"],
        "away_team": raw["team2"],
        "home_goals": raw["score1"].astype(int),
        "away_goals": raw["score2"].astype(int),
        "date": raw.get("date"),
        "league_slug": "fte-" + raw["league"].str.lower().str.replace(" ", "-", regex=False),
    })
    df.to_csv(FTE_CACHE, index=False)
    logger.info(f"FiveThirtyEight: {len(df)} matches fetched")
    return df


# ─── 5. Club Elo API ─────────────────────────────────────────────────────────
# clubelo.com exposes historical match records via simple HTTP

CELO_CLUBS = [
    "ManCity", "Arsenal", "Liverpool", "Chelsea", "Tottenham",
    "Bayern", "Dortmund", "RealMadrid", "Barcelona", "Atletico",
    "Juventus", "Milan", "Inter", "PSG", "Lyon",
]
CELO_CACHE = os.path.join(DATA_DIR, "clubelo.csv")


def fetch_clubelo() -> pd.DataFrame:
    """Fetch Elo rating history; derive synthetic match rows from club pages."""
    if os.path.exists(CELO_CACHE) and (time.time() - os.path.getmtime(CELO_CACHE)) < 86400:
        try:
            return pd.read_csv(CELO_CACHE, low_memory=False)
        except Exception:
            pass

    frames = []
    for club in CELO_CLUBS:
        r = _get(f"http://api.clubelo.com/{club}", timeout=15)
        if r is None:
            continue
        try:
            df = pd.read_csv(io.StringIO(r.text))
            df["club"] = club
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    elo_df = pd.concat(frames, ignore_index=True)
    # Convert Elo history into match-like rows by pairing consecutive home/away entries
    # We only need this for Elo context, so output a minimal schema for the trainer
    rows = []
    elo_df["From"] = pd.to_datetime(elo_df["From"], errors="coerce")
    for _, row in elo_df.iterrows():
        if pd.isna(row.get("Elo")) or pd.isna(row.get("From")):
            continue
        # Create a pseudo-row: the trainer uses Elo as a feature input,
        # so we expose a reference row that seeds Elo correctly.
        rows.append({
            "home_team": str(row["Club"]) if "Club" in row else club,
            "away_team": "Reference",
            "home_goals": 1,
            "away_goals": 0,
            "date": row["From"],
            "elo_home": row["Elo"],
            "league_slug": "clubelo-reference",
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(CELO_CACHE, index=False)
        logger.info(f"ClubElo: {len(df)} Elo reference rows fetched")
    return df


# ─── Combined loader ──────────────────────────────────────────────────────────

def load_all_open_sources() -> pd.DataFrame:
    """
    Fetch from all 4 supplementary open sources and return a combined DataFrame
    with the standard schema (home_team, away_team, home_goals, away_goals, date, league_slug).
    Source 1 (football-data.co.uk) is handled separately by downloader.py.
    """
    sources = [
        ("StatsBomb",        fetch_statsbomb),
        ("OpenFootball",     fetch_openfootball),
        ("FiveThirtyEight",  fetch_fivethirtyeight),
        ("ClubElo",          fetch_clubelo),
    ]
    frames = []
    for name, fn in sources:
        try:
            df = fn()
            if not df.empty:
                logger.info(f"  {name}: {len(df)} rows")
                frames.append(df)
        except Exception as e:
            logger.warning(f"  {name} failed: {e}")

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    # Ensure required columns exist
    for col in ["home_team", "away_team", "home_goals", "away_goals"]:
        if col not in combined.columns:
            return pd.DataFrame()

    combined = combined.dropna(subset=["home_team", "away_team", "home_goals", "away_goals"])
    combined["home_goals"] = pd.to_numeric(combined["home_goals"], errors="coerce").fillna(0).astype(int)
    combined["away_goals"] = pd.to_numeric(combined["away_goals"], errors="coerce").fillna(0).astype(int)
    for col in ["odds_home", "odds_draw", "odds_away"]:
        if col not in combined.columns:
            combined[col] = float("nan")
    if "date" in combined.columns:
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        combined = combined.sort_values("date")

    return combined.reset_index(drop=True)

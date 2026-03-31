"""
Downloads historical football match data from football-data.co.uk
(completely free, no API key needed) and caches it locally.
"""
from __future__ import annotations
import os, logging, io
import requests
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR  = os.path.join(os.path.dirname(__file__), '..', 'data')
BASE_URL  = "https://www.football-data.co.uk/mmz4281"

LEAGUE_CODES = {
    "premier-league":    ("E0", "England"),
    "championship":      ("E1", "England"),
    "la-liga":           ("SP1", "Spain"),
    "la-liga-2":         ("SP2", "Spain"),
    "bundesliga":        ("D1", "Germany"),
    "bundesliga-2":      ("D2", "Germany"),
    "serie-a":           ("I1", "Italy"),
    "serie-b":           ("I2", "Italy"),
    "ligue1":            ("F1", "France"),
    "ligue2":            ("F2", "France"),
    "eredivisie":        ("N1", "Netherlands"),
    "primeira-liga":     ("P1", "Portugal"),
    "scottish-prem":     ("SC0", "Scotland"),
    "belgian-pro":       ("B1", "Belgium"),
    "super-lig":         ("T1", "Turkey"),
    "greek-super":       ("G1", "Greece"),
}

SEASONS = ["2324", "2223", "2122", "2021", "1920", "1819", "1718", "1617", "1516"]

os.makedirs(DATA_DIR, exist_ok=True)


def _season_url(code: str, season: str) -> str:
    return f"{BASE_URL}/{season}/{code}.csv"


def download_season(league_slug: str, season: str, force: bool = False) -> Optional[pd.DataFrame]:
    if league_slug not in LEAGUE_CODES:
        logger.warning(f"Unknown league slug: {league_slug}")
        return None
    code, _ = LEAGUE_CODES[league_slug]
    cache_path = os.path.join(DATA_DIR, f"{league_slug}_{season}.csv")

    if not force and os.path.exists(cache_path):
        try:
            return pd.read_csv(cache_path, encoding='latin-1', low_memory=False)
        except Exception:
            pass

    url = _season_url(code, season)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), encoding='latin-1', low_memory=False)
        df.to_csv(cache_path, index=False)
        logger.info(f"Downloaded {league_slug} {season} → {len(df)} rows")
        return df
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return None


def download_all(seasons=None, leagues=None) -> pd.DataFrame:
    if seasons is None: seasons = SEASONS[:6]
    if leagues is None: leagues = list(LEAGUE_CODES.keys())

    frames = []
    for league in leagues:
        for season in seasons:
            df = download_season(league, season)
            if df is not None:
                df['league_slug'] = league
                frames.append(df)

    if not frames:
        logger.error("No data downloaded!")
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise column names across different CSV formats."""
    # Build rename map with priority (B365 beats BW, apply only unmapped columns)
    rename: dict[str, str] = {}
    priority = [
        ('HomeTeam', 'home_team'), ('AwayTeam', 'away_team'),
        ('FTHG', 'home_goals'),    ('FTAG', 'away_goals'),
        ('FTR',  'result'),        ('Date', 'date'),
        ('Div',  'division'),
        ('HS',   'shots_home'),    ('AS', 'shots_away'),
        ('HST',  'shots_on_target_home'), ('AST', 'shots_on_target_away'),
        ('HC',   'corners_home'),  ('AC', 'corners_away'),
        ('HF',   'fouls_home'),    ('AF', 'fouls_away'),
        ('HY',   'yellows_home'),  ('AY', 'yellows_away'),
        ('HR',   'reds_home'),     ('AR', 'reds_away'),
        # Odds – B365 first, BW fallback
        ('B365H', 'odds_home'),    ('B365D', 'odds_draw'),  ('B365A', 'odds_away'),
        ('BWH',   'odds_home'),    ('BWD',   'odds_draw'),  ('BWA',   'odds_away'),
    ]
    mapped_targets: set[str] = set()
    for src, tgt in priority:
        if src in df.columns and tgt not in mapped_targets:
            rename[src] = tgt
            mapped_targets.add(tgt)

    df = df.rename(columns=rename)

    required = ['home_team', 'away_team', 'home_goals', 'away_goals']
    for col in required:
        if col not in df.columns:
            return pd.DataFrame()

    df = df.dropna(subset=required)
    df['home_goals'] = pd.to_numeric(df['home_goals'], errors='coerce').fillna(0).astype(int)
    df['away_goals'] = pd.to_numeric(df['away_goals'], errors='coerce').fillna(0).astype(int)

    for col in ['odds_home', 'odds_draw', 'odds_away']:
        if col not in df.columns:
            df[col] = float('nan')
        else:
            try:
                df[col] = pd.to_numeric(df[col].astype(str), errors='coerce')
            except Exception:
                df[col] = float('nan')

    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'], errors='coerce', dayfirst=True)
        df = df.sort_values('date')

    return df.reset_index(drop=True)


def load_training_data(seasons=None, leagues=None) -> pd.DataFrame:
    raw = download_all(seasons=seasons, leagues=leagues)
    if raw.empty:
        return pd.DataFrame()
    return normalize_columns(raw)

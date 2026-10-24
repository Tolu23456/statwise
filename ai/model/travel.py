"""
Travel / fatigue proxies (feature slots 141-145).

For every match we measure how far each side travelled into the game:
the distance from the venue of the team's previous match to the venue of
this match.  Venue coordinates come from the shared Open-Meteo geocode
cache (ai/data/team_venues.json, team -> {city, country, lat, lon}).

Slots written (replacing pad_141..pad_145):
    141  travel_home     log-scaled km the home side travelled  [0..1]
    142  travel_away     log-scaled km the away side travelled  [0..1]
    143  travel_diff     travel_home - travel_away
    144  fatigue_diff    travel/(1 + rest_days/7) difference, home - away
    145  travel_present  fraction of sides with known venues (0 / .5 / 1)

Unknown venues yield 0 km with presence 0, so the model can ignore them.
Training and inference share `compute_travel_block`, which keeps the two
paths bit-identical.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENUE_CACHE = os.path.join(BASE, "data", "team_venues.json")

SLOT_TRAVEL_H = 141
SLOT_TRAVEL_A = 142
SLOT_TRAVEL_DIFF = 143
SLOT_FATIGUE_DIFF = 144
SLOT_TRAVEL_PRESENT = 145
TRAVEL_SLICE = slice(SLOT_TRAVEL_H, SLOT_TRAVEL_PRESENT + 1)  # 141:146
N_TRAVEL = 5

_MAX_KM = 10_000.0            # log-normalisation ceiling (~antipodal)
_LOG_MAX = math.log1p(_MAX_KM)
_DEFAULT_REST = 7.0           # days assumed when a team has no history

_venues_cache: dict | None = None
_norm_map: dict | None = None


def load_venues() -> dict:
    """team -> {city, country, lat, lon} (cached in memory)."""
    global _venues_cache
    if _venues_cache is None:
        try:
            with open(VENUE_CACHE, encoding="utf-8") as f:
                _venues_cache = json.load(f)
        except Exception:
            _venues_cache = {}
    return _venues_cache


def _key_map() -> dict:
    """case-folded team name -> entry, for tolerant lookups."""
    global _norm_map
    if _norm_map is None:
        _norm_map = {" ".join(str(k).split()).casefold(): v
                     for k, v in load_venues().items()}
    return _norm_map


def venue_coords(team: str) -> tuple[float, float]:
    """(lat, lon) for a team's home city, or NaNs when unknown."""
    venues = load_venues()
    entry = venues.get(team)
    if entry is None:
        entry = venues.get(str(team).strip())
    if entry is None:
        entry = _key_map().get(" ".join(str(team).split()).casefold())
    if isinstance(entry, dict):
        lat, lon = entry.get("lat"), entry.get("lon")
        try:
            if lat is not None and lon is not None:
                return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    return math.nan, math.nan


def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in km between two (arrays of) points."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=np.float64))
                              for x in (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 6371.0088 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _travel_norm(km: np.ndarray) -> np.ndarray:
    """log1p-scaled distance clamped to [0, 1]."""
    return np.minimum(np.log1p(np.maximum(km, 0.0)) / _LOG_MAX, 1.0)


def _coords_array(teams: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lats = np.empty(len(teams), dtype=np.float64)
    lons = np.empty(len(teams), dtype=np.float64)
    for i, t in enumerate(teams):
        lats[i], lons[i] = venue_coords(t)
    return lats, lons


def compute_travel_block(df: pd.DataFrame) -> np.ndarray:
    """(n, 5) travel/fatigue features aligned with df's row order.

    Expects columns home_team, away_team, date.  Uses each side's previous
    match venue as the journey origin; first-seen teams get rest=7d and
    travel=0 (no previous venue to measure from).
    """
    n = len(df)
    out = np.zeros((n, N_TRAVEL), dtype=np.float64)
    if n == 0:
        return out

    home = df["home_team"].astype(str).to_numpy()
    away = df["away_team"].astype(str).to_numpy()
    dates = pd.to_datetime(df["date"], errors="coerce")

    # Every match is played at the home side's venue.
    h_lat, h_lon = _coords_array(home)

    long = pd.DataFrame({
        "team": np.concatenate([home, away]),
        "date": np.concatenate([dates, dates]),
        "lat": np.concatenate([h_lat, h_lat]),
        "lon": np.concatenate([h_lon, h_lon]),
        "row": np.concatenate([np.arange(n), np.arange(n)]),
        "is_away_side": np.concatenate([np.zeros(n, dtype=np.int8),
                                        np.ones(n, dtype=np.int8)]),
    })
    long = long.sort_values(["team", "date"], kind="stable")

    long["p_lat"] = long.groupby("team", sort=False)["lat"].shift(1)
    long["p_lon"] = long.groupby("team", sort=False)["lon"].shift(1)
    long["p_date"] = long.groupby("team", sort=False)["date"].shift(1)

    known = long["p_lat"].notna() & long["lat"].notna()
    km = np.zeros(len(long), dtype=np.float64)
    if known.any():
        km[known.to_numpy()] = haversine_km(
            long["p_lat"][known], long["p_lon"][known],
            long["lat"][known], long["lon"][known])

    rest = (long["date"] - long["p_date"]).dt.total_seconds().to_numpy() / 86400.0
    rest = np.where(np.isfinite(rest) & (rest >= 0.0), rest, _DEFAULT_REST)

    travel = _travel_norm(km) * known.to_numpy()
    fatigue = travel / (1.0 + rest / _DEFAULT_REST)
    kn = known.to_numpy().astype(np.float64)

    rows = long["row"].to_numpy()
    is_away = long["is_away_side"].to_numpy().astype(bool)
    home_side, away_side = ~is_away, is_away

    # Each input row appears exactly once per side, so the scattered
    # assignment writes each target cell exactly once.
    travel_h = np.zeros(n); travel_a = np.zeros(n)
    fatigue_h = np.zeros(n); fatigue_a = np.zeros(n)
    pres_h = np.zeros(n); pres_a = np.zeros(n)

    travel_h[rows[home_side]] = travel[home_side]
    travel_a[rows[away_side]] = travel[away_side]
    fatigue_h[rows[home_side]] = fatigue[home_side]
    fatigue_a[rows[away_side]] = fatigue[away_side]
    pres_h[rows[home_side]] = kn[home_side]
    pres_a[rows[away_side]] = kn[away_side]

    out[:, 0] = travel_h
    out[:, 1] = travel_a
    out[:, 2] = travel_h - travel_a
    out[:, 3] = fatigue_h - fatigue_a
    out[:, 4] = (pres_h + pres_a) / 2.0
    return out

"""Shared data loader for the pick-policy analysis (analysis/pick_policy.md).

Cheap by design: reads ONLY the columns the curves need (odds + result +
filters), never the ~161-column feature matrix.  First call builds a pickle
cache in the temp dir; later calls reuse it.

    from pick_policy_data import load_frame
    df = load_frame()          # 377k rows x 14 cols, ~150 MB RSS
"""
from __future__ import annotations

import glob
import os

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.join(REPO, "ai", "data", "clean")
CACHE = os.path.join(os.environ.get("TMPDIR", "/tmp/opencode"), "pp_df.pkl")

COLS = [
    "date", "home_team", "away_team", "home_goals", "away_goals", "league_slug",
    "odds_home", "odds_draw", "odds_away",
    "avg_odds_home", "avg_odds_draw", "avg_odds_away",
    "quality_score", "is_international",
]


def load_frame() -> pd.DataFrame:
    if os.path.exists(CACHE):
        return pd.read_pickle(CACHE)
    frames = [
        pd.read_csv(p, usecols=lambda c: c in COLS, low_memory=False)
        for p in sorted(glob.glob(os.path.join(CLEAN, "????_matches.csv")))
    ]
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    df.to_pickle(CACHE)
    return df


def analysis_frame(df: pd.DataFrame | None = None, since: str = "2015-08-01") -> pd.DataFrame:
    """Quality/international/dedup/odds filters + result label + de-vigged market."""
    if df is None:
        df = load_frame()
    f = df[df["date"] >= since].copy()
    f = f[pd.to_numeric(f["is_international"], errors="coerce").fillna(0) == 0]
    f = f[pd.to_numeric(f["quality_score"], errors="coerce").fillna(0) >= 20]
    f = f.dropna(subset=["home_goals", "away_goals", "date"])
    f = f.drop_duplicates(
        subset=["date", "home_team", "away_team", "home_goals", "away_goals"])
    f = f[(f.odds_home > 1.01) & (f.odds_draw > 1.01) & (f.odds_away > 1.01)]
    f = f.sort_values("date").reset_index(drop=True)
    hg = pd.to_numeric(f.home_goals).astype(int).values
    ag = pd.to_numeric(f.away_goals).astype(int).values
    f["res"] = __import__("numpy").where(hg > ag, 0, __import__("numpy").where(hg == ag, 1, 2))
    imp = 1.0 / f[["odds_home", "odds_draw", "odds_away"]].values
    mkt = imp / imp.sum(axis=1, keepdims=True)
    f["mkt"] = [row for row in mkt]
    f["fav"] = mkt.argmax(axis=1)
    f["pmax"] = mkt.max(axis=1)
    f["pdraw"] = mkt[:, 1]
    return f

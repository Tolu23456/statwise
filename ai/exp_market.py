"""
E4 market features for the plan-5.16 experiment program (EXPERIMENT ONLY).

Two jobs, both read-only against production code:

1. `repair_matrix` — rebuilds the odds-derived feature SLOTS the C++ bulk
   engine corrupts on the training path (see ai/exp_plan.md "Grounding
   finding"): `df[[...]].fillna(0).values.astype(float64)` hands the engine
   an F-contiguous (transposed) pandas view, which it reads as flat
   row-major, so slots 62-65 (odds_implied_home/draw/away, market_overround)
   and the derived 104-105 come from odds_home of UNRELATED matches (rows
   3k/3k+1/3k+2) and, later in the frame, from out-of-bounds memory.  This
   module recomputes exactly what the single-ROW inference path computes
   (devig with the same 1.0001 floor), so training and serving agree for the
   first time.  ai/model/features.py is NOT modified — the repair is applied
   to the experiment's copy of the matrix only.

2. `build_extra` — per-match cross-book features derived from the raw
   football-data.co.uk season CSVs in ai/data/*.csv (which carry ~10
   bookmakers' OPENING odds and closing odds), joined onto the clean corpus
   through the SAME team-alias table the cleaner uses (parsed at runtime
   from ai/tools/dataset_cleaner.cpp — read-only).  Everything here is
   pre-match data: opening/closing prices, book counts, dispersion, steam.
   No outcome ever enters a feature.
"""
from __future__ import annotations

import glob
import os
import re
import unicodedata
import warnings

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Slot map of the 160-wide continuous block (ai/model/features.py
# FEATURE_NAMES); the appended book columns start at 161 after `league_id`.
SLOT_IMPLIED = 62          # odds_implied_home / draw / away = 62,63,64
SLOT_OVERROUND = 65        # market_overround
SLOT_H_WIN_MKT = 104       # h_win_mkt_diff = 1 - implied_home
SLOT_A_WIN_MKT = 105       # a_win_mkt_diff = 1 - implied_away

BOOK_PREFIXES = ["B365", "BW", "IW", "LB", "PS", "WH", "VC", "BF", "1XB", "BFE"]
OPENING_AVG = ["AvgH", "AvgD", "AvgA"]
CLOSING_CANDIDATES = [["AvgCH", "AvgCD", "AvgCA"],
                      ["PSCH", "PSCD", "PSCA"],
                      ["B365CH", "B365CD", "B365CA"]]

EXTRA_COLUMNS = [
    "cons_h", "cons_d", "cons_a", "cons_overround", "disp_maxavg",
    "n_books", "open_std", "open_range",
    "steam_h", "steam_d", "steam_a", "steam_l1", "e4_raw_hit",
]
N_EXTRA = len(EXTRA_COLUMNS)


def _devig3(o) -> tuple[np.ndarray, np.ndarray]:
    """(devigged probs, raw implied sum) with the C++ engine's 1.0001 floor."""
    imp = 1.0 / np.maximum(np.asarray(o, dtype=np.float64), 1.0001)
    return imp / np.maximum(imp.sum(axis=1, keepdims=True), 1e-9), imp.sum(axis=1)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Slot repair
# ─────────────────────────────────────────────────────────────────────────────
def repair_matrix(X: np.ndarray, odds: np.ndarray) -> np.ndarray:
    """Return a copy of X with the odds-derived slots computed correctly.

    `odds` is meta['odds'] (the row's own closing-preferring odds, aligned to
    X) — the same prices the serving path receives.  Values for invalid rows
    reproduce the engine's own fallback (implied -> uniform 1/3).
    """
    Xr = np.array(X, dtype=np.float32, copy=True)
    imp, overround = _devig3(odds)
    Xr[:, SLOT_IMPLIED:SLOT_IMPLIED + 3] = imp.astype(np.float32)
    Xr[:, SLOT_OVERROUND] = overround.astype(np.float32)
    Xr[:, SLOT_H_WIN_MKT] = (1.0 - imp[:, 0]).astype(np.float32)
    Xr[:, SLOT_A_WIN_MKT] = (1.0 - imp[:, 2]).astype(np.float32)
    return Xr


# ─────────────────────────────────────────────────────────────────────────────
# 2. Multi-book join
# ─────────────────────────────────────────────────────────────────────────────
def _norm_name(s) -> str:
    s = "" if s is None else str(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _load_aliases() -> dict:
    """Exact-match alias table the C++ cleaner applies to raw team names."""
    path = os.path.join(BASE_DIR, "tools", "dataset_cleaner.cpp")
    try:
        src = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return {}
    blk = src.split("TEAM_ALIASES = {", 1)[1].split("};", 1)[0]
    return {m.group(1).strip(): m.group(2).strip()
            for m in re.finditer(r'\{"((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\}', blk)}


def _canon_series(s: pd.Series, aliases: dict) -> pd.Series:
    return s.map(lambda x: aliases.get(str(x).strip(), str(x).strip())).map(_norm_name)


def _raw_frame() -> pd.DataFrame:
    """Every football-data-format season CSV in ai/data/, normalised."""
    aliases = _load_aliases()
    frames = []
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "data", "*.csv"))):
        try:
            head = open(path, encoding="utf-8-sig", errors="ignore").readline()
        except OSError:
            continue
        if "HomeTeam" not in head or "AwayTeam" not in head or "Date" not in head:
            continue
        try:
            r = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
        except Exception:
            continue
        if not {"Date", "HomeTeam", "AwayTeam"}.issubset(r.columns):
            continue
        r = r[["Date", "HomeTeam", "AwayTeam"] + [
            c for c in r.columns if c not in ("Date", "HomeTeam", "AwayTeam")]].copy()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            r["d"] = pd.to_datetime(r["Date"], dayfirst=True,
                                    errors="coerce").dt.strftime("%Y-%m-%d")
        r = r.dropna(subset=["d"])
        # Exact alias lookup first (the same mapping the cleaner applied to
        # the clean corpus), then a symmetric normalisation on both sides so
        # residual spelling differences still line up.
        r["h"] = _canon_series(r["HomeTeam"], aliases)
        r["a"] = _canon_series(r["AwayTeam"], aliases)
        frames.append(r)
    if not frames:
        return pd.DataFrame()
    raw = pd.concat(frames, ignore_index=True, sort=False)
    return raw.drop_duplicates(subset=["d", "h", "a"], keep="first").reset_index(drop=True)


def _num(raw: pd.DataFrame, cols: list) -> np.ndarray:
    """(n x 3) float matrix of the requested columns (NaN where absent/invalid)."""
    out = np.full((len(raw), 3), np.nan)
    if all(c in raw.columns for c in cols):
        v = raw[list(cols)].apply(pd.to_numeric, errors="coerce").values.astype(float)
        ok = np.isfinite(v) & (v > 1.01) & (v < 60)
        v = np.where(ok, v, np.nan)
        v[~np.all(np.isfinite(v), axis=1)] = np.nan
        out = v
    return out


def _devig_masked(o: np.ndarray) -> np.ndarray:
    imp = 1.0 / np.maximum(o, 1.0001)
    s = np.nansum(imp, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        return imp / np.where(s > 0, s, np.nan)


def _book_block(raw: pd.DataFrame) -> np.ndarray:
    """(n x 7): n_books, open_std, open_range, steam_h/d/a, hit (raw frame rows)."""
    n = len(raw)
    out = np.zeros((n, 7), dtype=np.float64)
    book_probs = []
    n_books = np.zeros(n)
    for pref in BOOK_PREFIXES:
        o = _num(raw, [f"{pref}H", f"{pref}D", f"{pref}A"])
        valid = np.all(np.isfinite(o), axis=1)
        p = _devig_masked(o)
        p[~valid] = np.nan
        book_probs.append(p)
        n_books += valid.astype(float)
    P = np.stack(book_probs)                      # (B, n, 3)
    import warnings
    with warnings.catch_warnings():
        # all-NaN slices (matches with no book data) are expected, not loud
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(invalid="ignore"):
            any_book = n_books > 0
            out[:, 0] = n_books / 8.0
            out[:, 1] = np.nanstd(P, axis=0).mean(axis=1)
            out[:, 2] = (np.nanmax(P, axis=0) - np.nanmin(P, axis=0)).mean(axis=1)
            out[~any_book, 1:3] = 0.0

    open_cons = _num(raw, OPENING_AVG)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        open_dev = _devig_masked(open_cons)
        # fall back to the mean of the individual opening books' devigged probs
        fb = np.nanmean(P, axis=0)
    open_dev = np.where(np.all(np.isfinite(open_dev), axis=1, keepdims=True),
                        open_dev, fb)

    close_dev = None
    close_used = np.zeros(n)
    for cols in CLOSING_CANDIDATES:
        cand = _devig_masked(_num(raw, cols))
        ok = np.all(np.isfinite(cand), axis=1)
        if close_dev is None:
            close_dev = np.full((n, 3), np.nan)
        close_dev = np.where(np.isnan(close_dev) & ok[:, None], cand, close_dev)
        close_used = np.maximum(close_used, ok.astype(float))

    with np.errstate(invalid="ignore"):
        steam = np.abs(open_dev - close_dev)
    steam[~(np.all(np.isfinite(steam), axis=1))] = 0.0
    out[:, 3:6] = steam
    out[:, 6] = (n_books > 0) & (close_used > 0)
    return out


def build_extra(df: pd.DataFrame) -> tuple[np.ndarray, dict]:
    """The 13 appended E4 columns, aligned to df rows (see EXTRA_COLUMNS)."""
    avg = df[["avg_odds_home", "avg_odds_draw", "avg_odds_away"]].apply(
        pd.to_numeric, errors="coerce").fillna(0).values.astype(np.float64)
    single = df[["odds_home", "odds_draw", "odds_away"]].apply(
        pd.to_numeric, errors="coerce").fillna(0).values.astype(np.float64)
    cons = np.where(avg > 1.01, avg, single)
    cons_d, cons_overround = _devig3(cons)

    mx = df[["max_odds_home", "max_odds_draw", "max_odds_away"]].apply(
        pd.to_numeric, errors="coerce").fillna(0).values.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        disp = np.where((mx > 1.01) & (cons > 1.01),
                        (mx - cons) / np.maximum(cons, 1e-9), 0.0)
    disp_maxavg = disp.mean(axis=1)

    out = np.zeros((len(df), N_EXTRA), dtype=np.float64)
    out[:, 0:3] = cons_d
    out[:, 3] = cons_overround
    out[:, 4] = disp_maxavg

    stats = {"raw_rows": 0, "joined": 0}
    raw = _raw_frame()
    if raw.empty:
        return out, stats
    stats["raw_rows"] = int(len(raw))
    blk = _book_block(raw)
    lut = pd.concat([raw[["d", "h", "a"]].reset_index(drop=True),
                     pd.DataFrame(blk, columns=["n_books", "open_std", "open_range",
                                                "steam_h", "steam_d", "steam_a",
                                                "hit"])], axis=1)
    lut = lut.set_index(["d", "h", "a"])

    aliases = _load_aliases()
    keys = pd.DataFrame({
        "d": pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "h": _canon_series(df["home_team"], aliases),
        "a": _canon_series(df["away_team"], aliases),
    })
    j = keys.join(lut, on=["d", "h", "a"], how="left")
    cols = ["n_books", "open_std", "open_range", "steam_h", "steam_d",
            "steam_a", "hit"]
    vals = np.column_stack([pd.to_numeric(j[c], errors="coerce").fillna(0.0).values
                            for c in cols])
    out[:, 5] = vals[:, 0]        # n_books (already /8)
    out[:, 6] = vals[:, 1]        # open_std
    out[:, 7] = vals[:, 2]        # open_range
    out[:, 8:11] = vals[:, 3:6]   # steam_h/d/a
    out[:, 11] = vals[:, 3:6].sum(axis=1)   # steam_l1
    out[:, 12] = vals[:, 6]       # e4_raw_hit
    stats["joined"] = int((vals[:, 6] > 0).sum())
    stats["joined_frac"] = round(float(vals[:, 6].mean()), 4)
    stats["cons_coverage"] = round(float((out[:, 3] > 0).mean()), 4)
    return out, stats

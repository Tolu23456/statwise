#!/usr/bin/env python3
"""Accuracy autopsy — light-weight baselines on ai/data/clean/*.csv.

Reads only a handful of columns, never builds the feature matrix, never trains.
Run:  .venv/bin/python analysis/accuracy_autopsy_analysis.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.join(REPO, "ai", "data", "clean")

COLS = [
    "date", "home_team", "away_team", "home_goals", "away_goals",
    "league_slug", "country",
    "odds_home", "odds_draw", "odds_away",
    "avg_odds_home", "avg_odds_draw", "avg_odds_away",
    "league_tier", "is_neutral",
]


def load(year: int) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(CLEAN, f"{year}_matches.csv"), usecols=COLS)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "home_goals", "away_goals"]).reset_index(drop=True)
    hg = df["home_goals"].astype(int)
    ag = df["away_goals"].astype(int)
    df["outcome"] = np.where(hg > ag, "H", np.where(hg < ag, "A", "D"))

    # market odds: prefer the single-book column, fall back to the average book
    for side in ("home", "draw", "away"):
        a = df[f"odds_{side}"]
        b = df[f"avg_odds_{side}"]
        df[f"mkt_{side}"] = a.where(a.notna() & (a > 1.01), b)
    ok = df[["mkt_home", "mkt_draw", "mkt_away"]].notna().all(axis=1)
    df["has_odds"] = ok
    # market favourite = lowest decimal odds (highest implied probability)
    m = df[["mkt_home", "mkt_draw", "mkt_away"]]
    fav = m.fillna(np.inf).idxmin(axis=1).str.replace("mkt_", "", regex=False)
    df["mkt_fav"] = fav.map({"home": "H", "draw": "D", "away": "A"})
    df.loc[~ok, "mkt_fav"] = None
    df["mkt_fav_odds"] = np.where(ok, m.min(axis=1), np.nan)
    # de-vigged market probabilities
    imp = 1.0 / m
    df["p_home"] = imp["mkt_home"] / imp.sum(axis=1)
    df["p_draw"] = imp["mkt_draw"] / imp.sum(axis=1)
    df["p_away"] = imp["mkt_away"] / imp.sum(axis=1)
    # the outcome the market prices second/third — used for "pick = market fav?"
    df["home_is_fav"] = df["mkt_fav"] == "H"
    return df


def baseline_block(df: pd.DataFrame, label: str) -> dict:
    n = len(df)
    out = {"window": label, "n": n}
    vc = df["outcome"].value_counts()
    out["H"] = int(vc.get("H", 0))
    out["D"] = int(vc.get("D", 0))
    out["A"] = int(vc.get("A", 0))
    out["home_win_pct"] = round(100 * out["H"] / n, 1)
    out["draw_pct"] = round(100 * out["D"] / n, 1)
    out["away_win_pct"] = round(100 * out["A"] / n, 1)
    out["always_home_acc"] = round(100 * out["H"] / n, 1)
    out["always_draw_acc"] = round(100 * out["D"] / n, 1)
    out["always_away_acc"] = round(100 * out["A"] / n, 1)
    out["random_acc"] = 33.3

    sub = df[df["has_odds"]]
    out["n_with_odds"] = len(sub)
    if len(sub):
        out["market_fav_acc"] = round(100 * (sub["mkt_fav"] == sub["outcome"]).mean(), 1)
        fav = sub["mkt_fav"].value_counts()
        out["fav_is_home_pct"] = round(100 * fav.get("H", 0) / len(sub), 1)
        out["fav_is_draw_pct"] = round(100 * fav.get("D", 0) / len(sub), 1)
        out["fav_is_away_pct"] = round(100 * fav.get("A", 0) / len(sub), 1)
        out["home_underdog_share_pct"] = round(100 * (~sub["home_is_fav"]).mean(), 1)
        ud = sub[~sub["home_is_fav"]]
        out["always_home_acc_when_home_dog"] = round(100 * (ud["outcome"] == "H").mean(), 1) if len(ud) else None
        favrows = sub[sub["home_is_fav"]]
        out["always_home_acc_when_home_fav"] = round(100 * (favrows["outcome"] == "H").mean(), 1) if len(favrows) else None
        out["draw_occur_when_fav_is_draw_pct"] = round(
            100 * (sub[sub["mkt_fav"] == "D"]["outcome"] == "D").mean(), 1)
        # random-style baseline that ignores odds entirely
        out["market_vs_home_delta_pp"] = round(out["market_fav_acc"] - out["always_home_acc"], 1)
    return out


def league_table(df: pd.DataFrame) -> pd.DataFrame:
    sub = df[df["has_odds"]].copy()
    g = sub.groupby("league_slug")
    t = pd.DataFrame({
        "n": g.size(),
        "home_win_pct": g.apply(lambda x: 100 * (x["outcome"] == "H").mean()),
        "draw_pct": g.apply(lambda x: 100 * (x["outcome"] == "D").mean()),
        "always_home_acc": g.apply(lambda x: 100 * (x["outcome"] == "H").mean()),
        "market_fav_acc": g.apply(lambda x: 100 * (x["mkt_fav"] == x["outcome"]).mean()),
        "fav_is_home_pct": g.apply(lambda x: 100 * (x["mkt_fav"] == "H").mean()),
        "fav_is_draw_pct": g.apply(lambda x: 100 * (x["mkt_fav"] == "D").mean()),
    })
    t["fav_minus_home_pp"] = t["market_fav_acc"] - t["always_home_acc"]
    return t.sort_values("n", ascending=False).round(1)


def odds_band_table(df: pd.DataFrame) -> pd.DataFrame:
    sub = df[df["has_odds"]].copy()
    bands = [1.0, 1.30, 1.60, 2.00, 2.50, 3.00, 4.00, 100.0]
    labels = ["<1.30", "1.30-1.59", "1.60-1.99", "2.00-2.49", "2.50-2.99", "3.00-3.99", "4.00+"]
    sub["band"] = pd.cut(sub["mkt_fav_odds"], bands, labels=labels, right=False)
    g = sub.groupby("band", observed=True)
    t = pd.DataFrame({
        "n": g.size(),
        "market_fav_acc": g.apply(lambda x: 100 * (x["mkt_fav"] == x["outcome"]).mean()),
        "always_home_acc": g.apply(lambda x: 100 * (x["outcome"] == "H").mean()),
        "share_of_matches_pct": g.size() / len(sub) * 100,
    })
    return t.round(1)


def simulate_invented_picks() -> dict:
    """Replicate .github/scripts/fetch_football_data.js generatePrediction()."""
    def picks(match_id: str) -> str:
        seed = sum(ord(c) for c in match_id)
        s = seed % 2147483647

        def rand():
            nonlocal s
            s = (s * 16807 + 0) % 2147483647
            return (s - 1) / 2147483646

        r = rand()
        if r < 0.38:
            return "Home Win"
        if r < 0.62:
            return "Away Win"
        if r < 0.74:
            return "Draw"
        if r < 0.84:
            return "Over 2.5 Goals"
        if r < 0.93:
            return "Both Teams to Score"
        return "Home Win"

    # football-data.org match ids are 6-digit numbers (e.g. 559663, 564722)
    ids = [str(i) for i in range(555000, 575000)]
    c = pd.Series([picks(i) for i in ids]).value_counts(normalize=True)
    # plus espn_ / fdorg_ prefixed ids the model pipeline uses
    ids2 = [f"fdorg_{i}" for i in range(555000, 575000)]
    c2 = pd.Series([picks(i) for i in ids2]).value_counts(normalize=True)
    ids3 = [f"espn_{i}" for i in range(401800000, 401900000)]
    c3 = pd.Series([picks(i) for i in ids3]).value_counts(normalize=True)
    return {
        "bare_fdorg_ids": (c * 100).round(1).to_dict(),
        "fdorg_prefixed_ids": (c2 * 100).round(1).to_dict(),
        "espn_prefixed_ids": (c3 * 100).round(1).to_dict(),
    }


def main() -> None:
    res = {"windows": {}, "leagues": {}, "odds_bands": {}, "invented_pick_sim": simulate_invented_picks()}

    y26 = load(2026)
    y25 = load(2025)

    # recent window: last 30 days present in the data (mirrors the app's
    # backtesting screen, which queries match_date >= now-30d)
    mx = y26["date"].max()
    recent = y26[y26["date"] >= mx - pd.Timedelta(days=30)]
    last97 = y26.sort_values("date").tail(97)

    for lbl, d in [
        ("2026 YTD (all clean 2026 rows)", y26),
        ("2026 last 30 days of data", recent),
        ("2026 most recent 97 rows", last97),
        ("2025 full year", y25),
    ]:
        res["windows"][lbl] = baseline_block(d, lbl)

    res["leagues"]["2026 YTD"] = league_table(y26).to_dict(orient="index")
    res["odds_bands"]["2026 YTD"] = odds_band_table(y26).to_dict(orient="index")
    res["odds_bands"]["2026 last 30 days"] = odds_band_table(recent).to_dict(orient="index")
    res["meta"] = {
        "clean_2026_rows": int(len(y26)),
        "clean_2026_max_date": str(mx.date()),
        "recent30_rows": int(len(recent)),
        "data_coverage_with_odds_2026_pct": round(100 * y26["has_odds"].mean(), 1),
    }

    out = os.path.join(REPO, "analysis", "accuracy_autopsy_numbers.json")
    with open(out, "w") as f:
        json.dump(res, f, indent=2, default=str)

    # ── console report ────────────────────────────────────────────────
    print("== invented-pick simulation (old fetch_football_data.js generator) ==")
    print(json.dumps(res["invented_pick_sim"], indent=1))
    print("\n== windows ==")
    hdr = ["window", "n", "home_win_pct", "draw_pct", "away_win_pct",
           "always_home_acc", "random_acc", "n_with_odds", "market_fav_acc",
           "fav_is_home_pct", "fav_is_draw_pct", "home_underdog_share_pct",
           "always_home_acc_when_home_dog"]
    w = pd.DataFrame([res["windows"][k] for k in res["windows"]])
    print(w[hdr].to_string(index=False))
    print("\n== league table (2026 YTD, matches with odds) ==")
    print(pd.DataFrame(res["leagues"]["2026 YTD"]).to_string())
    print("\n== odds bands (2026 YTD) ==")
    print(pd.DataFrame(res["odds_bands"]["2026 YTD"]).to_string())
    print("\n== odds bands (last 30 days) ==")
    print(pd.DataFrame(res["odds_bands"]["2026 last 30 days"]).to_string())
    print(f"\nwrote {out}")


if __name__ == "__main__":
    sys.exit(main())

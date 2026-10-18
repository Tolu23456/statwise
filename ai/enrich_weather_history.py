"""
Historical weather enrichment for the training set (2000 -> today).

Resolves each home team to a city (seed map + geocoding, cached once per team),
fetches Open-Meteo archive daily weather per city (one call per city for the
whole 2000->today range, cached), and writes a sidecar lookup:

  ai/data/weather_by_match.csv   date, home_team, temp_c, precip_mm, wind_kmh, weather_code

retrain.py::_load_clean_data() left-joins this file at training time, so the
cleaner can regenerate the year CSVs freely — weather survives.

Incremental: only (team, date) rows missing from the sidecar are computed.
Run after every pipeline run (run_pipeline.sh calls it) and for new matches.

Usage:
  python enrich_weather_history.py            # backfill missing rows
  python enrich_weather_history.py --stats    # coverage report only
"""
from __future__ import annotations
import glob
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from model.team_venues import resolve_venue, fetch_archive_weather  # noqa: E402

CLEAN_DIR = os.path.join(BASE, "data", "clean")
SIDECAR = os.path.join(BASE, "data", "weather_by_match.csv")


def _load_existing() -> pd.DataFrame:
    if os.path.exists(SIDECAR):
        try:
            return pd.read_csv(SIDECAR, dtype={"date": str, "home_team": str})
        except Exception:
            pass
    return pd.DataFrame(columns=["date", "home_team", "temp_c", "precip_mm", "wind_kmh", "weather_code"])


def stats() -> None:
    ex = _load_existing()
    paths = sorted(glob.glob(os.path.join(CLEAN_DIR, "????_matches.csv")))
    total = 0
    for p in paths:
        d = pd.read_csv(p, usecols=["date"], low_memory=False)
        total += len(d)
    have = len(ex) - int(ex["temp_c"].isna().sum()) if len(ex) else 0
    print(f"matches in clean set : {total:,}")
    print(f"weather rows in side : {len(ex):,} ({have:,} with data, {total - have:,} missing)")


def main() -> None:
    paths = sorted(glob.glob(os.path.join(CLEAN_DIR, "????_matches.csv")))
    if not paths:
        print("No clean data found.")
        return

    print(f"Loading {len(paths)} year files...")
    usecols = ["date", "home_team", "country"]
    frames = []
    for p in paths:
        try:
            frames.append(pd.read_csv(p, usecols=lambda c: c in usecols, low_memory=False))
        except Exception as e:
            print(f"  skip {p}: {e}")
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["date", "home_team"])
    print(f"{len(df):,} matches, {df['home_team'].nunique():,} unique home teams")

    ex = _load_existing()
    done = set(zip(ex["date"].astype(str), ex["home_team"].astype(str))) if len(ex) else set()
    todo = df[[(d, t) not in done for d, t in zip(df["date"], df["home_team"])]]
    print(f"{len(todo):,} match-rows still need weather ({len(done):,} cached)")

    # 1. Resolve venues (cached per team, geocodes only unmapped teams)
    teams = todo[["home_team", "country"]].drop_duplicates("home_team")
    print(f"Resolving {len(teams)} team venues...")
    venues: dict[str, dict | None] = {}
    for i, (_, r) in enumerate(teams.iterrows(), 1):
        t = r["home_team"]
        venues[t] = resolve_venue(t, r.get("country"))
        if i % 200 == 0:
            print(f"  {i}/{len(teams)} resolved "
                  f"({sum(1 for v in venues.values() if v)} ok)")
    n_ok = sum(1 for v in venues.values() if v)
    print(f"Venue resolution: {n_ok}/{len(teams)} teams mapped "
          f"({n_ok / max(len(teams), 1):.0%})")

    # 2. Fetch archive weather per unique city (cached; one call covers 2000->today)
    cities = {}
    for t, v in venues.items():
        if v and v.get("lat") is not None and v.get("lon") is not None:
            cities[(round(v["lat"], 1), round(v["lon"], 1))] = (v["lat"], v["lon"])
    print(f"Fetching archive weather for {len(cities)} unique cities...")
    wx: dict[tuple, dict[str, dict]] = {}
    for i, (k, (lat, lon)) in enumerate(sorted(cities.items()), 1):
        days = fetch_archive_weather(lat, lon)
        if days:
            wx[k] = days
        if i % 50 == 0:
            print(f"  {i}/{len(cities)} cities fetched")

    # 3. Build sidecar rows
    new_rows = []
    miss_city = miss_day = filled = 0
    for _, r in todo.iterrows():
        v = venues.get(r["home_team"])
        if not v or v.get("lat") is None:
            miss_city += 1
            continue
        k = (round(v["lat"], 1), round(v["lon"], 1))
        day = wx.get(k, {}).get(r["date"])
        if not day:
            miss_day += 1
            continue
        new_rows.append({
            "date": r["date"], "home_team": r["home_team"],
            "temp_c": day["temp_c"], "precip_mm": day["precip_mm"],
            "wind_kmh": day["wind_kmh"], "weather_code": day["weather_code"],
        })
        filled += 1

    out = pd.concat([ex, pd.DataFrame(new_rows)], ignore_index=True) if new_rows else ex
    out = out.drop_duplicates(subset=["date", "home_team"], keep="last")
    out.to_csv(SIDECAR, index=False)
    print(f"\nWrote {SIDECAR}")
    print(f"  filled : {filled:,}")
    print(f"  no city: {miss_city:,}")
    print(f"  no data: {miss_day:,}")
    print(f"  total  : {len(out):,} rows")


if __name__ == "__main__":
    if "--stats" in sys.argv:
        stats()
    else:
        main()

"""
Historical weather enrichment for the training set (2000 -> today).

Resolves each home team to a city (seed map + geocoding, cached once per team),
fetches Open-Meteo archive daily weather per city (one call per city for the
whole 2000->today range, cached), and writes a sidecar lookup:

  ai/data/weather_by_match.csv.gz   date, home_team, temp_c, precip_mm, wind_kmh, weather_code

retrain.py::_load_clean_data() left-joins this file at training time, so the
cleaner can regenerate the year CSVs freely — weather survives.

Incremental: only (team, date) rows missing from the sidecar are computed.
Run after every pipeline run (run_pipeline.sh calls it) and for new matches.
Heavy lifting (941 cities of archive fetches) belongs in GitHub Actions —
see .github/workflows/weather-backfill.yml, which runs the fetch as N shard
jobs (Open-Meteo throttles per IP, so one job stalls) and a merge job that
builds and commits the sidecar from the shared fetch cache.

Usage:
  python enrich_weather_history.py                 # fetch + backfill missing rows
  python enrich_weather_history.py --stats         # coverage report only
  python enrich_weather_history.py --shards 20 --shard 3 --rows-out rows/s3.csv
  python enrich_weather_history.py --merge-rows rows/   # combine shards (CI merge job)
  python enrich_weather_history.py --build-only    # sidecar from the fetch cache only
"""
from __future__ import annotations
import glob
import json
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from model.team_venues import (  # noqa: E402
    WX_CACHE_DIR,
    resolve_many,
    fetch_archive_weather_many,
)

CLEAN_DIR = os.path.join(BASE, "data", "clean")
SIDECAR = os.path.join(BASE, "data", "weather_by_match.csv.gz")
SIDECAR_LEGACY = os.path.join(BASE, "data", "weather_by_match.csv")


def _sidecar_path() -> str:
    return SIDECAR if os.path.exists(SIDECAR) else SIDECAR_LEGACY


def _load_existing() -> pd.DataFrame:
    path = _sidecar_path()
    if os.path.exists(path):
        try:
            return pd.read_csv(path, dtype={"date": str, "home_team": str})
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


def _load_cached_days(coords) -> dict[str, dict]:
    """Whatever the on-disk fetch cache already holds — no network.

    The merge job uses this: sharding exists precisely so that no single run
    re-hits the archive API's per-IP throttle.
    """
    out: dict[str, dict] = {}
    for lat, lon in coords:
        key = f"{round(lat, 1):.1f}_{round(lon, 1):.1f}"
        path = os.path.join(WX_CACHE_DIR, key + ".json")
        if not os.path.exists(path):
            continue
        try:
            with open(path) as fh:
                obj = json.load(fh)
        except Exception:
            continue
        if obj.get("days"):
            out[key] = obj["days"]
    return out


def _merge_rows(rows_dir: str) -> int:
    """Combine shard row files into the committed sidecar (no network, no cache).

    Each shard job writes the rows it could fill; merging them by filename lets
    the fan-out be any size without the merge job restoring N caches.
    """
    cols = ["date", "home_team", "temp_c", "precip_mm", "wind_kmh", "weather_code"]
    files = sorted(glob.glob(os.path.join(rows_dir, "**", "*.csv"), recursive=True))
    if not files:
        print(f"no shard row files under {rows_dir}/")
        return 1
    parts = []
    for f in files:
        try:
            d = pd.read_csv(f, dtype={"date": str, "home_team": str})
        except Exception as e:
            print(f"  skip {f}: {e}")
            continue
        parts.append(d.reindex(columns=cols))
        print(f"  {os.path.basename(f)}: {len(d):,} rows")
    ex = _load_existing()
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    out = pd.concat([ex, new], ignore_index=True)
    out = out.drop_duplicates(subset=["date", "home_team"], keep="last")
    out.to_csv(SIDECAR, index=False, compression="gzip")
    if os.path.exists(SIDECAR_LEGACY):
        os.remove(SIDECAR_LEGACY)
    print(f"\nmerged {len(new):,} shard rows into {SIDECAR}")
    print(f"  before: {len(ex):,} rows -> after: {len(out):,} rows")
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="weather sidecar backfill")
    ap.add_argument("--stats", action="store_true", help="coverage report only")
    ap.add_argument("--shards", type=int, default=1,
                    help="split the city list across N parallel CI jobs")
    ap.add_argument("--shard", type=int, default=None,
                    help="fetch only cities where index %% shards == shard")
    ap.add_argument("--rows-out", default=None,
                    help="shard mode: write filled rows here instead of the sidecar")
    ap.add_argument("--merge-rows", default=None, metavar="DIR",
                    help="combine shard row CSVs in DIR into the sidecar (no fetch)")
    ap.add_argument("--build-only", action="store_true",
                    help="never hit the network; build the sidecar from the fetch cache")
    args = ap.parse_args(argv)
    if args.stats:
        stats()
        return 0
    if args.merge_rows:
        return _merge_rows(args.merge_rows)
    if args.shard is not None and not 0 <= args.shard < max(args.shards, 1):
        ap.error(f"--shard must be in [0, {args.shards})")

    paths = sorted(glob.glob(os.path.join(CLEAN_DIR, "????_matches.csv")))
    if not paths:
        print("No clean data found.")
        return 1

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

    # 1. Resolve venues (cached per team, geocodes only unmapped teams; threaded)
    teams = todo[["home_team", "country"]].drop_duplicates("home_team")
    print(f"Resolving {len(teams)} team venues...")
    venues = resolve_many([(r["home_team"], r.get("country")) for _, r in teams.iterrows()], workers=4)
    n_ok = sum(1 for v in venues.values() if v)
    print(f"Venue resolution: {n_ok}/{len(teams)} teams mapped "
          f"({n_ok / max(len(teams), 1):.0%})")

    # 2. Unique cities (0.5° grid — ~50km; the row lookup below rounds the same way)
    cities: dict[tuple, tuple] = {}
    for t, v in venues.items():
        if v and v.get("lat") is not None and v.get("lon") is not None:
            rlat = round(v["lat"] * 2) / 2
            rlon = round(v["lon"] * 2) / 2
            cities[(rlat, rlon)] = (rlat, rlon)

    # Shard over the SORTED city list: every shard job checks out the same
    # commit with the same sidecar, so all of them derive an identical list and
    # `index % shards` partitions it with no coordination step.
    n_total = len(cities)
    if args.shard is not None:
        ordered = sorted(cities)
        keep = {k for i, k in enumerate(ordered) if i % max(args.shards, 1) == args.shard}
        cities = {k: cities[k] for k in keep}
        print(f"shard {args.shard}/{args.shards}: {len(cities)}/{n_total} cities "
              f"(sorted-index partition)")

    wx: dict[tuple, dict[str, dict]] = {}
    if args.build_only:
        for key, days in _load_cached_days(list(cities.values())).items():
            lat_s, lon_s = key.split("_", 1)
            wx[(round(float(lat_s), 1), round(float(lon_s), 1))] = days
        print(f"  cache-only: {len(wx)}/{len(cities)} cities on disk (no network)")
    else:
        print(f"Fetching archive weather for {len(cities)} unique cities...")
        out = fetch_archive_weather_many(list(cities.values()), batch=30, pause=3.0)
        for k in cities:
            days = out.get(f"{k[0]:.1f}_{k[1]:.1f}")
            if days:
                wx[k] = days
        print(f"  {sum(1 for d in wx.values() if d)}/{len(cities)} cities fetched")

    # 3. Build sidecar rows
    new_rows = []
    miss_city = miss_day = filled = 0
    for _, r in todo.iterrows():
        v = venues.get(r["home_team"])
        if not v or v.get("lat") is None:
            miss_city += 1
            continue
        # must use the same 0.5° grid the weather was fetched on — rounding to
        # 0.1° here silently missed almost every lookup
        k = (round(v["lat"] * 2) / 2, round(v["lon"] * 2) / 2)
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

    if args.shard is not None:
        # A shard only fetches. Writing a partial sidecar here would replace the
        # committed one with a subset — the merge job concatenates these files.
        rows_path = args.rows_out or f"rows/shard-{args.shard}.csv"
        parent = os.path.dirname(rows_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        pd.DataFrame(new_rows, columns=["date", "home_team", "temp_c",
                                        "precip_mm", "wind_kmh", "weather_code"]
                     ).to_csv(rows_path, index=False)
        print(f"\nshard {args.shard}/{args.shards} done: {len(new_rows):,} rows -> {rows_path} "
              f"({miss_day:,} rows had no weather for this shard's cities)")
        return 0

    out = pd.concat([ex, pd.DataFrame(new_rows)], ignore_index=True) if new_rows else ex
    out = out.drop_duplicates(subset=["date", "home_team"], keep="last")
    out.to_csv(SIDECAR, index=False, compression="gzip")
    # a legacy uncompressed sidecar would shadow nothing (we always write .gz),
    # but remove it so the two can never disagree
    if os.path.exists(SIDECAR_LEGACY):
        os.remove(SIDECAR_LEGACY)
    print(f"\nWrote {SIDECAR}")
    print(f"  filled : {filled:,}")
    print(f"  no city: {miss_city:,}")
    print(f"  no data: {miss_day:,}")
    print(f"  total  : {len(out):,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

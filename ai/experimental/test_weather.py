"""Checks for weather integration (IMPROVEMENT_PLAN Step 1.2).

Three things can silently break weather:

  1. weather_features() mis-building the 8 slots (133-140)
  2. the forecast cache never being hit (repeated API calls per prediction run)
  3. retrain.py not joining the gzipped sidecar, so training sees blank weather
     while live inference feeds real forecasts

Run:  .venv/bin/python ai/experimental/test_weather.py
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)

import model.features as features          # noqa: E402
import model.team_venues as tv             # noqa: E402

ok = True


def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)


# ── 1. weather feature block ──────────────────────────────────────────────
w = features.weather_features(
    {"temp_c": 22.0, "precip_mm": 0.0, "wind_kmh": 12.0, "weather_code": 1})
check("weather block has 8 slots", len(w) == 8, f"len={len(w)}")
check("w_present=1 when temp given", w[7] == 1.0, f"w={w}")
check("warm temp -> w_heat/warm not cold", w[4] == 0.0 and w[5] == 0.0, f"w={w}")
check("dry day -> w_rain=0", w[4] == 0.0, f"w={w}")

cold = features.weather_features(
    {"temp_c": -3.0, "precip_mm": 8.0, "wind_kmh": 40.0, "weather_code": 61})
check("cold+rainy flags set", cold[5] == 1.0 and cold[4] == 1.0, f"w={cold}")

missing = features.weather_features({})
check("absent weather -> all zeros except presence", missing[7] == 0.0
      and float(np.abs(missing[:7]).sum()) == 0.0, f"w={missing}")

check("weather slice is 133:141", features.FEATURE_NAMES[133:141] ==
      ["w_temp", "w_precip", "w_wind", "w_code", "w_rain", "w_cold", "w_heat", "w_present"],
      str(features.FEATURE_NAMES[133:141]))

# ── 2. forecast cache ─────────────────────────────────────────────────────
tmp = tempfile.mkdtemp(prefix="wx_test_")
real_dir = tv.FC_CACHE_DIR
real_get = tv._get
try:
    tv.FC_CACHE_DIR = tmp
    calls = {"n": 0}

    def fake_get(url):
        calls["n"] += 1
        return {"daily": {"time": ["2026-11-02"],
                          "temperature_2m_mean": [11.5],
                          "precipitation_sum": [2.4],
                          "wind_speed_10m_max": [19.0],
                          "weather_code": [61]}}

    tv._get = fake_get
    future = (dt.date.today() + dt.timedelta(days=9)).isoformat()

    first = tv.fetch_forecast_weather(51.5, -0.12, future)
    second = tv.fetch_forecast_weather(51.5, -0.12, future)
    check("forecast returns data", first is not None and first["temp_c"] == 11.5, str(first))
    check("second call served from cache (no API hit)", calls["n"] == 1, f"calls={calls['n']}")
    check("cached value identical", first == second)

    tv.fetch_forecast_weather(-10.0, -75.0, future)
    check("different fixture is a different cache entry", calls["n"] == 2, f"calls={calls['n']}")

    # TTLs: far-out forecasts expire sooner than near-term ones
    ttl_far = tv._forecast_ttl((dt.date.today() + dt.timedelta(days=20)).isoformat())
    ttl_near = tv._forecast_ttl((dt.date.today() + dt.timedelta(days=1)).isoformat())
    ttl_past = tv._forecast_ttl((dt.date.today() - dt.timedelta(days=3)).isoformat())
    check("far forecast TTL < near TTL", ttl_far > ttl_near, f"far={ttl_far}s near={ttl_near}s")
    check("played match TTL is long (observed weather)", ttl_past >= 7 * 86400, f"{ttl_past}s")
finally:
    tv._get = real_get
    tv.FC_CACHE_DIR = real_dir
    shutil.rmtree(tmp, ignore_errors=True)

# ── 2b. archive batching adapts to the API's 400 ──────────────────────────
tmp = tempfile.mkdtemp(prefix="wx_batch_")
real_dir_wx = tv.WX_CACHE_DIR
try:
    tv.WX_CACHE_DIR = tmp
    real_ga = tv._get_archive
    calls = {"n": 0}

    def picky_get_archive(url):
        """Reject any request with more than 2 coordinates (like the real API
        does when locations x days is too large)."""
        calls["n"] += 1
        lats = url.split("latitude=")[1].split("&")[0].split(",")
        if len(lats) > 2:
            return None, 400
        times = ["2025-06-01", "2025-06-02"]
        items = [{"latitude": float(la), "longitude": float(lo),
                  "daily": {"time": times,
                            "temperature_2m_mean": [20.0, 21.0],
                            "precipitation_sum": [0.0, 0.5],
                            "wind_speed_10m_max": [10.0, 12.0],
                            "weather_code": [0, 1]}}
                 for la, lo in zip(lats, url.split("longitude=")[1].split("&")[0].split(","))]
        return items, 200

    tv._get_archive = picky_get_archive
    coords = [(round(40.0 + i * 0.5, 1), round(3.0 + i * 0.5, 1)) for i in range(8)]
    got = tv.fetch_archive_weather_many(coords, batch=8, pause=0.0)
    check("all cities fetched despite 400-on-large-batch",
          len(got) == len(coords), f"{len(got)}/{len(coords)}")
    check("splits were retried (more requests than cities/batch)",
          calls["n"] >= 4, f"requests={calls['n']}")
    one = next(iter(got.values()))
    check("split responses still parse into daily weather",
          one.get("2025-06-01", {}).get("temp_c") == 20.0, str(one)[:120])
finally:
    tv._get_archive = real_ga
    tv.WX_CACHE_DIR = real_dir_wx
    shutil.rmtree(tmp, ignore_errors=True)

# ── 3. retrain joins the gzipped sidecar ──────────────────────────────────
tmp = tempfile.mkdtemp(prefix="wx_join_")
try:
    clean = os.path.join(tmp, "data", "clean")
    os.makedirs(clean, exist_ok=True)
    rows = pd.DataFrame([
        {"date": "2025-03-01", "home_team": "Arsenal FC", "away_team": "Chelsea FC",
         "home_goals": 2, "away_goals": 1, "quality_score": 80, "is_international": 0},
        {"date": "2025-03-02", "home_team": "Real Madrid CF", "away_team": "Sevilla FC",
         "home_goals": 3, "away_goals": 0, "quality_score": 80, "is_international": 0},
    ])
    rows.to_csv(os.path.join(clean, "2025_matches.csv"), index=False)

    side = pd.DataFrame([
        {"date": "2025-03-01", "home_team": "Arsenal FC",
         "temp_c": 9.0, "precip_mm": 1.2, "wind_kmh": 22.0, "weather_code": 63},
        {"date": "2025-03-02", "home_team": "Real Madrid CF",
         "temp_c": 17.5, "precip_mm": 0.0, "wind_kmh": 8.0, "weather_code": 1},
    ])
    side_path = os.path.join(tmp, "data", "weather_by_match.csv.gz")
    with gzip.open(side_path, "wt") as f:
        side.to_csv(f, index=False)

    import retrain
    real_base = retrain.BASE_DIR
    try:
        retrain.BASE_DIR = tmp
        df = retrain._load_clean_data()
    finally:
        retrain.BASE_DIR = real_base

    check("sidecar columns joined", "temp_c" in df.columns, str(list(df.columns)))
    cov = pd.to_numeric(df.get("temp_c"), errors="coerce").notna().mean() if "temp_c" in df else 0
    check("weather coverage on joined rows", cov == 1.0, f"coverage={cov:.0%}")
    got = df.loc[df["home_team"] == "Arsenal FC", "temp_c"]
    check("joined values are the sidecar's", len(got) == 1 and float(got.iloc[0]) == 9.0,
          str(list(got)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)

#!/usr/bin/env python3
"""Backfill matches from the football-data.org v4 API into raw/football_data/*.csv.

Covers competitions that are NOT available from the file sources
(football-data.co.uk, xgabora, openfootball, martj42):
  - Champions League     (CL)
  - Copa Libertadores     (CLI)
  - Brazilian Serie A     (BSA, only through Dec 2024 in xgabora)

Output files use the football-data.co.uk column format so the C++ cleaner
ingests them in Phase 2 with the league slug derived from the filename:
  ai/data/raw/football_data/{slug}_{season}.csv

Free-tier API limits: 10 requests/min → one request per competition-season
with a 7s pause, retrying on 429.

Usage:  python3 ai/backfill_fdorg.py [--force]
"""

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.football-data.org/v4"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "ai", "data", "raw", "football_data")

# (competition code, league slug, seasons to fetch)
JOBS = [
    ("CL", "champions-league", [2020, 2021, 2022, 2023, 2024, 2025, 2026]),
    ("BSA", "serie-a-br", [2023, 2024, 2025, 2026]),
    ("CLI", "copa-libertadores", [2023, 2024, 2025, 2026]),
]


def get_token() -> str:
    env_path = os.path.join(ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("FOOTBALL_API_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("FOOTBALL_API_TOKEN", "")


def fetch(token: str, code: str, season: int, tries: int = 3):
    """Return the /matches payload for a competition-season, or None."""
    url = f"{API}/competitions/{code}/matches?season={season}"
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"X-Auth-Token": token})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 65
                print(f"    rate-limited (429), waiting {wait}s …", flush=True)
                time.sleep(wait)
                continue
            print(f"    HTTP {e.code}: {e.read()[:120]!r}")
            return None
        except Exception as e:  # noqa: BLE001 - network hiccup → retry
            print(f"    error: {e} (attempt {attempt + 1}/{tries})")
            time.sleep(10)
    return None


def to_csv(payload: dict, out_path: str) -> int:
    """Write finished matches in football-data.co.uk column format."""
    n = 0
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Div", "Date", "HomeTeam", "AwayTeam",
                    "FTHG", "FTAG", "FTR", "HTHG", "HTAG", "HTR"])
        for m in payload.get("matches", []):
            if m.get("status") != "FINISHED":
                continue
            ft = m["score"]["fullTime"]
            if ft.get("home") is None or ft.get("away") is None:
                continue
            hg, ag = int(ft["home"]), int(ft["away"])
            ht = m["score"].get("halfTime", {}) or {}
            hth, hta = ht.get("home"), ht.get("away")
            ftr = "H" if hg > ag else ("A" if ag > hg else "D")
            htr = ("H" if (hth or 0) > (hta or 0)
                   else "A" if (hta or 0) > (hth or 0) else "D") if hth is not None else ""
            date = m["utcDate"][:10]                      # ISO → normalise_date handles it
            dd, mm, yyyy = date[8:10], date[5:7], date[:4]
            w.writerow(["", f"{dd}/{mm}/{yyyy}",
                        m["homeTeam"]["name"], m["awayTeam"]["name"],
                        hg, ag, ftr, hth if hth is not None else "",
                        hta if hta is not None else "", htr])
            n += 1
    return n


def main() -> int:
    force = "--force" in sys.argv
    token = get_token()
    if not token:
        print("FOOTBALL_API_TOKEN not set (check .env) — aborting")
        return 1

    os.makedirs(RAW, exist_ok=True)
    total = 0
    for code, slug, seasons in JOBS:
        for season in seasons:
            out_path = os.path.join(RAW, f"{slug}_{season}.csv")
            if not force and os.path.exists(out_path) and os.path.getsize(out_path) > 500:
                print(f"  {code} {season} → exists, skipping")
                continue
            print(f"  {code} {season} → fetching …", end="", flush=True)
            payload = fetch(token, code, season)
            if payload is None:
                print(" FAILED")
                continue
            n = to_csv(payload, out_path)
            total += n
            print(f" {n} matches written")
            time.sleep(7)  # free tier: 10 req/min
    print(f"Backfill done: {total} rows written to {RAW}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

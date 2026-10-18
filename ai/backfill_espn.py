#!/usr/bin/env python3
"""Backfill 2025 + 2026 matches for the non-European leagues from ESPN's
keyless site API into raw/football_data/*.csv (football-data.co.uk format).

These leagues stop at Dec 2024 in xgabora (their per-division data ends there)
and are absent from openfootball / football-data.co.uk, so ESPN fills:
  MLS, Argentina, Japan, Mexico, China, Denmark, Norway, Sweden, Finland,
  Ireland, Austria, Switzerland, Poland, Romania, Russia

ESPN quirks handled here:
  - responses are gzip-compressed regardless of Accept header
  - month windows work as ?dates=YYYYMM (day and range formats 400)
  - finished matches: status.type.completed == true

Output files are named {league_slug}_{year}.csv so the C++ cleaner derives
the league from the filename in Phase 2 (Div column left empty).

Usage:  python3 ai/backfill_espn.py [--force]
"""

import csv
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "ai", "data", "raw", "football_data")

# ESPN league slug → our league slug (must match DIVISION_MAP slugs)
LEAGUES = {
    "usa.1": "mls",
    "arg.1": "primera-division",
    "jpn.1": "j-league",
    "mex.1": "liga-mx",
    "chn.1": "chinese-super",
    "den.1": "danish-superliga",
    "nor.1": "norwegian-elit",
    "swe.1": "swedish-allsv",
    "fin.1": "finnish-veikkaus",
    "irl.1": "irish-prem",
    "aut.1": "austrian-bl",
    "sui.1": "swiss-super",
    "pol.1": "polish-ekstraklasa",
    "rou.1": "romanian-liga1",
    "rus.1": "russian-premier",
}

# calendar months to fetch (gap = Dec 2024 → today)
MONTHS = [(2025, m) for m in range(1, 13)] + [(2026, m) for m in range(1, 11)]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


def fetch(url: str, tries: int = 3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    return None


def month_rows(espn_slug: str, year: int, month: int) -> list[dict]:
    """Finished matches for one league-month."""
    d = fetch(f"{API}/{espn_slug}/scoreboard?dates={year}{month:02d}&limit=300")
    if not d:
        return []
    out = []
    for ev in d.get("events", []):
        st = ev.get("status", {}).get("type", {})
        if not st.get("completed"):
            continue
        comp = (ev.get("competitions") or [{}])[0]
        cands = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        home, away = cands.get("home"), cands.get("away")
        if not home or not away:
            continue
        try:
            hg = int(home.get("score"))
            ag = int(away.get("score"))
        except (TypeError, ValueError):
            continue
        date = ev.get("date", "")[:10]           # 2026-03-02
        if not date or date[:4] not in ("2025", "2026"):
            continue
        dd, mm, yyyy = date[8:10], date[5:7], date[:4]
        out.append({
            "date": f"{dd}/{mm}/{yyyy}",
            "home": (home.get("team") or {}).get("displayName", ""),
            "away": (away.get("team") or {}).get("displayName", ""),
            "hg": hg, "ag": ag,
            "ftr": "H" if hg > ag else ("A" if ag > hg else "D"),
        })
    return out


def main() -> int:
    force = "--force" in sys.argv
    os.makedirs(RAW, exist_ok=True)

    # probe which ESPN slugs actually resolve (some names shift)
    working = {}
    for espn_slug, our_slug in LEAGUES.items():
        d = fetch(f"{API}/{espn_slug}/scoreboard?limit=1")
        if d is not None:
            working[espn_slug] = our_slug
        else:
            print(f"  ! {espn_slug} ({our_slug}) not available on ESPN, skipping")
    print(f"  {len(working)}/{len(LEAGUES)} ESPN leagues available")

    total = 0
    for espn_slug, our_slug in working.items():
        by_year: dict[int, list[dict]] = {2025: [], 2026: []}
        for year, month in MONTHS:
            rows = month_rows(espn_slug, year, month)
            by_year[year].extend(rows)
            time.sleep(0.4)                      # be polite
        for year, rows in by_year.items():
            out_path = os.path.join(RAW, f"{our_slug}_{year}.csv")
            if rows and not force and os.path.exists(out_path) \
                    and os.path.getsize(out_path) > 500:
                continue
            if not rows:
                continue
            # de-dup identical rows (ESPN can repeat events across windows)
            seen = set()
            with open(out_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"])
                for r in rows:
                    key = (r["date"], r["home"], r["away"], r["hg"], r["ag"])
                    if key in seen:
                        continue
                    seen.add(key)
                    w.writerow(["", r["date"], r["home"], r["away"], r["hg"], r["ag"], r["ftr"]])
            total += len(seen)
            print(f"  {our_slug}_{year}.csv → {len(seen)} matches")
    print(f"ESPN backfill done: {total} rows written to {RAW}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Weather / venues sidecar coverage + effective training-row filter counts."""
import glob, json, os, sys
import numpy as np
import pandas as pd

BASE = "/home/t85491005/statwise"
CLEAN = os.path.join(BASE, "ai/data/clean")

USECOLS = ["date", "home_team", "away_team", "home_goals", "away_goals",
           "league_slug", "is_international", "quality_score"]

frames = []
for f in sorted(glob.glob(os.path.join(CLEAN, "????_matches.csv"))):
    frames.append(pd.read_csv(f, usecols=USECOLS, dtype=str, keep_default_na=False))
d = pd.concat(frames, ignore_index=True)
del frames
d["dt"] = pd.to_datetime(d["date"], errors="coerce")
d["hg"] = pd.to_numeric(d["home_goals"], errors="coerce")
d["ag"] = pd.to_numeric(d["away_goals"], errors="coerce")
d["is_intl"] = pd.to_numeric(d["is_international"], errors="coerce").fillna(0)
d["qs"] = pd.to_numeric(d["quality_score"], errors="coerce").fillna(0)

# ---- effective training filter (mirrors retrain._load_clean_data) ----
m = d["hg"].notna() & d["ag"].notna()
m &= d["dt"] >= pd.Timestamp("1993-08-01")
m &= d["is_intl"] == 0
m &= d["qs"] >= 40
train = d[m].copy()
print(f"raw rows                : {len(d)}")
print(f"after retrain filters   : {len(train)}  (dropped {len(d)-len(train)})")
print(f"  dropped pre-1993      : {int((d['dt'] < pd.Timestamp('1993-08-01')).sum())}")
print(f"  dropped international : {int(((d['dt'] >= pd.Timestamp('1993-08-01')) & (d['is_intl'] == 1)).sum())}")
print(f"  dropped quality<40    : {int(((d['dt'] >= pd.Timestamp('1993-08-01')) & (d['is_intl'] != 1) & (d['qs'] < 40)).sum())}")

# ---- weather sidecar ----
wx = pd.read_csv(os.path.join(BASE, "ai/data/weather_by_match.csv.gz"))
wx["date"] = pd.to_datetime(wx["date"], errors="coerce")
wx = wx.dropna(subset=["date"]).drop_duplicates(subset=["date", "home_team"])
print(f"\nweather sidecar rows (dedup): {len(wx)}  unique dates: {wx['date'].nunique()}")
print(f"weather date range: {wx['date'].min().date()} .. {wx['date'].max().date()}")

key_tr = train["dt"].astype(str) + "||" + train["home_team"].astype(str)
key_wx = wx["date"].astype(str) + "||" + wx["home_team"].astype(str)
wx_keys = set(key_wx)
train["wx"] = key_tr.isin(wx_keys).values

print("\n-- weather coverage of TRAINING rows (post-filter) --")
def cov(sub, label):
    if len(sub) == 0:
        print(f"{label:38s} n=0"); return
    print(f"{label:38s} n={len(sub):7d}  wx={100*sub['wx'].mean():5.1f}%")

cov(train, "ALL training rows")
for y0, y1, lab in [(1993,2000,"1993-1999"),(2000,2010,"2000-2009"),
                    (2010,2020,"2010-2019"),(2020,2025,"2020-2024"),
                    (2025,2026,"2025"),(2026,2027,"2026")]:
    sub = train[(train["dt"].dt.year >= y0) & (train["dt"].dt.year < y1)]
    cov(sub, f"  {lab}")
cov(train[train["dt"] >= "2025-10-25"], "  last 12 months")
cov(train[train["dt"] >= "2026-08-01"], "  current season (2026-08+)")
cov(train[train["dt"] >= "2026-10-01"], "  Oct 2026 (latest month)")

# monthly for last 18 months
recent = train[train["dt"] >= "2025-04-01"].copy()
recent["ym"] = recent["dt"].dt.to_period("M").astype(str)
g = recent.groupby("ym").agg(rows=("wx","size"), wx=("wx","sum"))
g["wx_pct"] = (100*g["wx"]/g["rows"]).round(1)
print("\n-- weather coverage by month (training rows, 2025-04+) --")
print(g.to_string())

# raw (unfiltered) recent coverage too
d["wx"] = (d["dt"].astype(str) + "||" + d["home_team"]).isin(wx_keys).values
dr = d[d["dt"] >= "2025-10-25"]
print(f"\nRAW rows last 12m: n={len(dr)} wx={100*dr['wx'].mean():.1f}%")

# ---- venues ----
with open(os.path.join(BASE, "ai/data/team_venues.json")) as fh:
    ven = json.load(fh)
print(f"\nteam_venues.json entries: {len(ven)}")
sample_keys = list(ven)[:3]
for k in sample_keys:
    print(f"  sample: {k} -> {ven[k]}")

def stem(s):
    import re
    s = str(s).lower()
    toks = re.split(r"[^0-9a-zÀ-ÿ]+", s.strip())
    noise = {"fc","cf","afc","bsc","ssc","ac","sc","us","sv","vfb","vfl",
             "tsg","fk","pfc","sk","ca","cfc","aafc","ifk","sp","ssp","csc","pk","rc"}
    toks = [t for t in toks if t and t not in noise]
    return " ".join(toks)

vkeys = set()
for k in ven:
    vkeys.add(k.lower()); vkeys.add(stem(k))

teams_tr = set(train["home_team"]) | set(train["away_team"])
teams_recent = set(train[train["dt"] >= "2026-01-01"]["home_team"]) | set(train[train["dt"] >= "2026-01-01"]["away_team"])
hit_all = sum(1 for t in teams_tr if t.lower() in vkeys or stem(t) in vkeys)
hit_rec = sum(1 for t in teams_recent if t.lower() in vkeys or stem(t) in vkeys)
print(f"\ntraining teams: {len(teams_tr)}, venue-resolvable: {hit_all} ({100*hit_all/max(len(teams_tr),1):.1f}%)")
print(f"2026 teams    : {len(teams_recent)}, venue-resolvable: {hit_rec} ({100*hit_rec/max(len(teams_recent),1):.1f}%)")

# row-level venue coverage for recent training rows
def resolve(t):
    return t.lower() in vkeys or stem(t) in vkeys
train["venue_ok"] = train["home_team"].map(resolve)
print("\n-- venue resolution (home side) --")
print(f"  all training rows : {100*train['venue_ok'].mean():.1f}%")
r26 = train[train["dt"] >= "2026-01-01"]
print(f"  2026 rows         : {100*r26['venue_ok'].mean():.1f}%  (n={len(r26)})")
r12 = train[train["dt"] >= "2025-10-25"]
print(f"  last 12m rows     : {100*r12['venue_ok'].mean():.1f}%  (n={len(r12)})")

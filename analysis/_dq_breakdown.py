#!/usr/bin/env python3
"""Weather/odds/league breakdowns on recent vs historical slices."""
import glob, os, sys
import numpy as np
import pandas as pd

BASE = "/home/t85491005/statwise"
CLEAN = os.path.join(BASE, "ai/data/clean")
USECOLS = ["date", "home_team", "away_team", "home_goals", "away_goals",
           "league_slug", "is_international", "quality_score",
           "odds_home", "odds_draw", "odds_away"]

wx = pd.read_csv(os.path.join(BASE, "ai/data/weather_by_match.csv.gz"))
wx["date"] = pd.to_datetime(wx["date"], errors="coerce")
wx_keys = set((wx["date"].dropna().astype(str) + "||" + wx["home_team"]).unique())
del wx

frames = []
for f in sorted(glob.glob(os.path.join(CLEAN, "????_matches.csv"))):
    frames.append(pd.read_csv(f, usecols=USECOLS, dtype=str, keep_default_na=False))
d = pd.concat(frames, ignore_index=True); del frames
d["dt"] = pd.to_datetime(d["date"], errors="coerce")
d["hg"] = pd.to_numeric(d["home_goals"], errors="coerce")
d["ag"] = pd.to_numeric(d["away_goals"], errors="coerce")
d["ii"] = pd.to_numeric(d["is_international"], errors="coerce").fillna(0)
d["q"] = pd.to_numeric(d["quality_score"], errors="coerce").fillna(0)
for c in ("odds_home", "odds_draw", "odds_away"):
    d[c] = pd.to_numeric(d[c], errors="coerce")
d["odds_ok"] = (d["odds_home"] > 1.01) & (d["odds_draw"] > 1.01) & (d["odds_away"] > 1.01)
d["wx"] = (d["dt"].astype(str) + "||" + d["home_team"]).isin(wx_keys).values
d["res"] = np.where(d["hg"] > d["ag"], "H", np.where(d["hg"] < d["ag"], "A", "D"))

tr = d[(d["dt"] >= "1993-08-01") & (d["ii"] == 0) & (d["q"] >= 40) & d["hg"].notna()].copy()
rec = tr[tr["dt"] >= "2025-10-25"].copy()
old = tr[tr["dt"] < "2025-10-25"].copy()
print(f"training rows: {len(tr)}  last12m: {len(rec)}  older: {len(old)}")

# ---- odds coverage by league, recent vs historical ----
print("\n-- odds coverage by league: last 12m vs pre-2025 (training rows) --")
a = rec.groupby("league_slug").agg(rows=("odds_ok", "size"), odds=("odds_ok", "sum"))
b = old.groupby("league_slug").agg(rows_old=("odds_ok", "size"), odds_old=("odds_ok", "sum"))
t = a.join(b, how="outer").fillna(0).astype(int)
t["recent_pct"] = (100 * t["odds"] / t["rows"].clip(lower=1)).round(1)
t["hist_pct"] = (100 * t["odds_old"] / t["rows_old"].clip(lower=1)).round(1)
t = t.sort_values("rows", ascending=False)
print(t.head(45).to_string())

# ---- weather by league (recent) ----
print("\n-- weather coverage by league, last 12m (training rows) --")
w = rec.groupby("league_slug").agg(rows=("wx", "size"), wx=("wx", "sum"))
w["wx_pct"] = (100 * w["wx"] / w["rows"]).round(1)
print(w.sort_values("rows", ascending=False).head(30).to_string())

print("\n-- weather coverage: top 15 home teams overall (training rows) --")
wt = tr.groupby("home_team").agg(rows=("wx", "size"), wx=("wx", "sum"))
wt["wx_pct"] = (100 * wt["wx"] / wt["rows"]).round(1)
big = wt[wt["rows"] > 150].sort_values("wx_pct", ascending=False)
print("highest:")
print(big.head(8).to_string())
print("lowest:")
print(big.tail(8).to_string())
print(f"teams with >150 rows: {len(big)}, of which 0% weather: {int((big['wx_pct']==0).sum())}, 100%: {int((big['wx_pct']==100).sum())}")

# weather by year for training rows
print("\n-- weather coverage by year (training rows) --")
tr["yr"] = tr["dt"].dt.year
gy = tr.groupby("yr").agg(rows=("wx", "size"), wx=("wx", "sum"))
gy["wx_pct"] = (100 * gy["wx"] / gy["rows"]).round(1)
print(gy.loc[2000:].to_string())

# ---- class balance by league: history vs last 12m ----
print("\n-- class balance by league (big leagues): history vs last 12m --")
def bal(df):
    n = len(df)
    if n == 0: return (0, 0, 0, 0)
    vc = df["res"].value_counts(normalize=True)
    return (n, vc.get("H", 0), vc.get("D", 0), vc.get("A", 0))
rows = []
for lg in t[t["rows"] >= 100].index:
    o = old[old["league_slug"] == lg]; r = rec[rec["league_slug"] == lg]
    no, ho, do, ao = bal(o); nr, hr, dr, ar = bal(r)
    rows.append((lg, no, ho, do, ao, nr, hr, dr, ar))
out = pd.DataFrame(rows, columns=["league", "n_hist", "H_hist", "D_hist", "A_hist",
                                  "n_12m", "H_12m", "D_12m", "A_12m"])
for c in ["H_hist", "D_hist", "A_hist", "H_12m", "D_12m", "A_12m"]:
    out[c] = (100 * out[c]).round(1)
out["dH"] = (out["H_12m"] - out["H_hist"]).round(1)
out["dD"] = (out["D_12m"] - out["D_hist"]).round(1)
out["dA"] = (out["A_12m"] - out["A_hist"]).round(1)
print(out.sort_values("n_12m", ascending=False).to_string(index=False))

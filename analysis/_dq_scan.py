#!/usr/bin/env python3
"""Data-quality scan over ai/data/clean/*.csv — streaming, low memory.

Aggregates only; never holds more than one file's usecols subset at a time.
"""
import glob
import os
import sys
import numpy as np
import pandas as pd

CLEAN = "/home/t85491005/statwise/ai/data/clean"
USECOLS = [
    "date", "home_team", "away_team", "home_goals", "away_goals",
    "league_slug", "country", "source",
    "odds_home", "odds_draw", "odds_away",
    "avg_odds_home", "avg_odds_draw", "avg_odds_away",
    "quality_score", "score_conflict", "is_neutral", "tournament",
    "elo_home", "elo_away",
]

files = sorted(glob.glob(os.path.join(CLEAN, "????_matches.csv")))
print(f"files: {len(files)}", file=sys.stderr)

frames = []
goal_bad = 0
goal_empty = 0
for i, f in enumerate(files):
    df = pd.read_csv(f, usecols=USECOLS, dtype=str, keep_default_na=False, na_filter=False)
    for c in ("home_goals", "away_goals"):
        bad = ~df[c].str.fullmatch(r"-?\d+")
        goal_bad += int(bad.sum())
        goal_empty += int((df[c].str.strip() == "").sum())
    df["file"] = os.path.basename(f)
    frames.append(df)
    del df

d = pd.concat(frames, ignore_index=True)
del frames
print(f"rows: {len(d)}  goal_bad(non-int): {goal_bad}  goal_empty: {goal_empty}", file=sys.stderr)

# ---------- dates ----------
d["dt"] = pd.to_datetime(d["date"], errors="coerce")
print(f"date parse failures: {int(d['dt'].isna().sum())}")
print(f"min date: {d['dt'].min()}  max date: {d['dt'].max()}")
print(f"future-dated (> 2026-10-25): {int((d['dt'] > '2026-10-25').sum())}")

# ---------- goals / labels ----------
hg = pd.to_numeric(d["home_goals"], errors="coerce")
ag = pd.to_numeric(d["away_goals"], errors="coerce")
d["hg"] = hg
d["ag"] = ag
n_unparsed = int(hg.isna().sum() + ag.isna().sum())
print(f"unparsed goals (rows w/ NaN in either): {int((hg.isna() | ag.isna()).sum())}")
neg = int(((hg < 0) | (ag < 0)).sum())
print(f"negative goals: {neg}")
extreme = int(((hg > 20) | (ag > 20)).sum())
print(f"goals > 20 (suspicious): {extreme}")
if extreme:
    print(d.loc[(hg > 20) | (ag > 20), ["date", "home_team", "away_team", "home_goals", "away_goals", "league_slug"]].head(20).to_string())

# label classes (only rows with parsed goals)
ok = hg.notna() & ag.notna()
res = np.where(hg > ag, "H", np.where(hg < ag, "A", "D"))
d["res"] = res
d["played"] = ok

# 0-0 rows
print(f"0-0 rows: {int(((hg == 0) & (ag == 0)).sum())}")
# duplicate fixtures
key = d["date"].str.cat([d["league_slug"], d["home_team"], d["away_team"]], sep="|")
h = pd.util.hash_pandas_object(key, index=False).values
uniq, counts = np.unique(h, return_counts=True)
dup_keys = uniq[counts > 1]
dup_rows = int(counts[counts > 1].sum())
print(f"duplicate fixture keys (date|league|home|away): {len(dup_keys)} keys, {dup_rows} rows involved")
# exact dupes (same score too)
key2 = key + "|" + d["home_goals"] + "-" + d["away_goals"]
h2 = pd.util.hash_pandas_object(key2, index=False).values
u2, c2 = np.unique(h2, return_counts=True)
print(f"exact duplicate rows (incl. score): {int((c2[c2 > 1] - 1).sum())} extra rows")

# loose dupes: same date+teams regardless of league
key3 = d["date"] + "|" + d["home_team"] + "|" + d["away_team"]
h3 = pd.util.hash_pandas_object(key3, index=False).values
u3, c3 = np.unique(h3, return_counts=True)
print(f"same date+teams (any league) dupes: {int((c3[c3 > 1] - 1).sum())} extra rows")

d["yr"] = d["dt"].dt.year
d["ym"] = d["dt"].dt.to_period("M").astype(str)

# ---------- class balance ----------
def balance(sub, label):
    s = sub[sub["played"]]
    if len(s) == 0:
        return
    vc = s["res"].value_counts(normalize=True)
    print(f"{label}: n={len(s)} H={vc.get('H',0):.3f} D={vc.get('D',0):.3f} A={vc.get('A',0):.3f}")

print("\n-- class balance --")
balance(d, "FULL HISTORY")
for y in sorted(d["yr"].dropna().unique()):
    balance(d[d["yr"] == y], f"  year {int(y)}")
balance(d[d["dt"] >= "2025-10-25"], "LAST 12 MONTHS (2025-10-25..)")
balance(d[d["dt"] >= "2026-01-01"], "2026 YTD")
balance(d[d["dt"] >= "2026-08-01"], "2026-08-01.. (current season)")

# ---------- odds coverage ----------
print("\n-- odds coverage (all 3 of odds_home/draw/away > 1.01) --")
for c in ("odds_home", "odds_draw", "odds_away"):
    d[c] = pd.to_numeric(d[c], errors="coerce")
    d[c + "_avg"] = pd.to_numeric(d[c.replace("odds_", "avg_odds_")], errors="coerce")
d["has_book"] = (d["odds_home"] > 1.01) & (d["odds_draw"] > 1.01) & (d["odds_away"] > 1.01)
d["has_avg"] = (d["odds_home_avg"] > 1.01) & (d["odds_draw_avg"] > 1.01) & (d["odds_away_avg"] > 1.01)
d["has_any_odds"] = d["has_book"] | d["has_avg"]

odds_tbl = (d[d["dt"] >= "2024-01-01"]
            .groupby("ym")
            .agg(rows=("date", "size"),
                 book=("has_book", "sum"),
                 avg=("has_avg", "sum"),
                 any_odds=("has_any_odds", "sum")))
odds_tbl["book_pct"] = (100 * odds_tbl["book"] / odds_tbl["rows"]).round(1)
odds_tbl["any_pct"] = (100 * odds_tbl["any_odds"] / odds_tbl["rows"]).round(1)
print(odds_tbl.to_string())

print("\n-- odds coverage by year (pre-2024 for context) --")
oy = (d.groupby(d["yr"])
      .agg(rows=("date", "size"), book=("has_book", "sum"), any_odds=("has_any_odds", "sum")))
oy["book_pct"] = (100 * oy["book"] / oy["rows"]).round(1)
oy["any_pct"] = (100 * oy["any_odds"] / oy["rows"]).round(1)
print(oy.tail(15).to_string())

# ---------- league coverage ----------
print("\n-- leagues, full history vs recent --")
lg_all = d["league_slug"].value_counts()
lg_26 = d[d["dt"] >= "2026-01-01"]["league_slug"].value_counts()
lg_12m = d[d["dt"] >= "2025-10-25"]["league_slug"].value_counts()
cmp = pd.DataFrame({"all": lg_all, "2026": lg_26, "last12m": lg_12m}).fillna(0).astype(int)
cmp = cmp.sort_values("2026", ascending=False)
print("top 30 by 2026 rows:")
print(cmp.head(30).to_string())
print(f"\ntotal league slugs: all={len(lg_all)} 2026={len(lg_26)} last12m={len(lg_12m)}")
print("\nleagues in 2026 data (all):")
print(cmp[cmp["2026"] > 0].to_string())

# score_conflict
d["score_conflict"] = pd.to_numeric(d["score_conflict"], errors="coerce")
print(f"\nscore_conflict=1 rows: {int((d['score_conflict'] == 1).sum())}")

# per-month rows last 18 months
print("\n-- rows per month (2025-04+) --")
mm = d[d["dt"] >= "2025-04-01"].groupby("ym").size()
print(mm.to_string())

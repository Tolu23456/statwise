# Part of analysis/pick_policy.md — see that file for the deliverable tables.
# Data comes from analysis/pick_policy_data.py (odds+result columns only).
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pick_policy_data import load_frame as _load_frame

#!/usr/bin/env python3
"""Accuracy-vs-coverage curves from market odds only (no model, no features)."""
import pandas as pd, numpy as np

df = _load_frame()

# --- frame used for all curves -------------------------------------------
f = df.copy()
f = f[(f['date'] >= '2015-08-01') & (f['date'] <= '2026-10-18')]
if 'is_international' in f.columns:
    f = f[pd.to_numeric(f['is_international'], errors='coerce').fillna(0) == 0]
if 'quality_score' in f.columns:
    f = f[pd.to_numeric(f['quality_score'], errors='coerce').fillna(0) >= 20]
f = f.dropna(subset=['home_goals', 'away_goals', 'date'])
f = f.drop_duplicates(subset=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])
valid = (f.odds_home > 1.01) & (f.odds_draw > 1.01) & (f.odds_away > 1.01)
f = f[valid].copy()
hg = pd.to_numeric(f.home_goals).astype(int)
ag = pd.to_numeric(f.away_goals).astype(int)
f['res'] = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))   # 0 home 1 draw 2 away
imp = 1.0 / f[['odds_home', 'odds_draw', 'odds_away']].values
mkt = imp / imp.sum(axis=1, keepdims=True)
f['mkt'] = [row for row in mkt]
f['fav'] = mkt.argmax(axis=1)
f['pmax'] = mkt.max(axis=1)
f['pdraw'] = mkt[:, 1]
f['fav_is_home'] = f['fav'] == 0
print(f"FRAME: n={len(f):,}  {f.date.min().date()} -> {f.date.max().date()}")
print(f"outcome mix: home={float((f.res==0).mean()):.3f} draw={float((f.res==1).mean()):.3f} away={float((f.res==2).mean()):.3f}")
print()

def acc(mask, picks):
    p = picks[mask]
    return float((p == f['res'].values[mask]).mean()), int(mask.sum())

res = f['res'].values
fav = f['fav'].values

print("=" * 78)
print("BASELINES (100% coverage, full frame)")
print("=" * 78)
print(f"  always HOME                 : {float((res==0).mean())*100:5.1f}%")
print(f"  always DRAW                 : {float((res==1).mean())*100:5.1f}%")
print(f"  always AWAY                 : {float((res==2).mean())*100:5.1f}%")
print(f"  market favourite (de-vigged): {float((fav==res).mean())*100:5.1f}%")
# favourite by raw lowest odds (same as de-vigged usually)
raw_fav = f[['odds_home','odds_draw','odds_away']].values.argmin(axis=1)
print(f"  market favourite (min odds) : {float((raw_fav==res).mean())*100:5.1f}%")
# pick highest home only vs max
print(f"  predicted-draw share (market fav = draw): {float((fav==1).mean())*100:5.1f}%")
print(f"  hit rate when market fav = draw: {float((res[fav==1]==1).mean())*100:5.1f}% (n={int((fav==1).sum()):,})")
print(f"  hit rate when market fav = home: {float((res[fav==0]==0).mean())*100:5.1f}% (n={int((fav==0).sum()):,})")
print(f"  hit rate when market fav = away: {float((res[fav==2]==2).mean())*100:5.1f}% (n={int((fav==2).sum()):,})")
print()

print("=" * 78)
print("CURVE 1 — pick market favourite ONLY when its implied prob >= X")
print("  (abstain otherwise; coverage = share of matches picked)")
print("=" * 78)
print(f"{'X':>6} {'coverage':>9} {'picks':>8} {'accuracy':>9} {'home%':>7} {'draw%':>7} {'away%':>7}")
Xs = [0.35, 0.38, 0.40, 0.42, 0.44, 0.45, 0.46, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65, 0.70, 0.75]
pmax = f['pmax'].values
for X in Xs:
    m = pmax >= X
    if m.sum() == 0:
        continue
    a = float((fav[m] == res[m]).mean())
    mix = [float((fav[m] == k).mean()) * 100 for k in (0, 1, 2)]
    print(f"{X:6.2f} {m.mean()*100:8.1f}% {int(m.sum()):8,} {a*100:8.1f}% {mix[0]:6.1f}% {mix[1]:6.1f}% {mix[2]:6.1f}%")
print()

print("=" * 78)
print("CURVE 1b — same, but on the most recent era (date >= 2024-08-01)")
print("=" * 78)
f2 = f[f.date >= '2024-08-01']
res2 = f2['res'].values; fav2 = f2['fav'].values; pmax2 = f2['pmax'].values
print(f"n={len(f2):,}  market-fav acc={float((fav2==res2).mean())*100:.1f}%  always-home={float((res2==0).mean())*100:.1f}%")
print(f"{'X':>6} {'coverage':>9} {'picks':>8} {'accuracy':>9}")
for X in Xs:
    m = pmax2 >= X
    if m.sum() < 30:
        continue
    a = float((fav2[m] == res2[m]).mean())
    print(f"{X:6.2f} {m.mean()*100:8.1f}% {int(m.sum()):8,} {a*100:8.1f}%")
print()

print("=" * 78)
print("CURVE 2 — pick ONLY when the favourite's edge over 2nd is >= G pp")
print("=" * 78)
srt = np.sort(mkt, axis=1)
gap = srt[:, -1] - srt[:, -2]
print(f"{'gap':>6} {'coverage':>9} {'picks':>8} {'accuracy':>9}")
for G in [0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]:
    m = gap >= G
    if m.sum() < 30:
        continue
    a = float((fav[m] == res[m]).mean())
    print(f"{G*100:5.0f}p {m.mean()*100:8.1f}% {int(m.sum()):8,} {a*100:8.1f}%")
print()

print("=" * 78)
print("CURVE 3 — DRAW handling: only call Draw when odds_draw <= Z")
print("  policy: draw is eligible iff odds_draw <= Z; else pick the best non-draw")
print("=" * 78)
oh, od, oa = f.odds_home.values, f.odds_draw.values, f.odds_away.values
base = fav.copy()
print(f"{'Z':>6} {'p_implied>=':>11} {'draw_picks':>11} {'draw_hit%':>10} {'accuracy':>9} {'vs base':>8}")
for Z in [2.40, 2.60, 2.75, 2.90, 3.00, 3.10, 3.25, 3.50, 3.75, 4.00, 99]:
    elig = od <= Z
    pick = base.copy()
    # draw currently the market favourite -> keep it only if eligible, else best non-draw
    is_draw_fav = base == 1
    nd = np.where(oh >= oa, 0, 2)          # best non-draw side by implied prob
    # (higher odds? no: lower odds = higher implied) -> pick lower odds of home/away
    nd = np.where(oh <= oa, 0, 2)
    pick[is_draw_fav & ~elig] = nd[is_draw_fav & ~elig]
    # ALSO: allow a draw pick when draw is NOT favourite but is short-priced? no — keep rule simple
    a = float((pick == res).mean())
    dp = pick == 1
    dh = float((res[dp] == 1).mean()) if dp.sum() else float('nan')
    print(f"{Z:6.2f} {1/Z*100:10.1f}% {int(dp.sum()):11,} {dh*100:9.1f}% {a*100:8.1f}% { (a-float((fav==res).mean()))*100:+7.1f}p")
print()

print("=" * 78)
print("CURVE 3b — DRAW as a *called* pick only when implied p_draw >= T")
print("  policy: if market p_draw >= T -> pick Draw, else pick favourite among 1X2 excluding draw")
print("=" * 78)
pdraw = f['pdraw'].values
print(f"{'T':>6} {'coverage':>9} {'draw_picks':>11} {'draw_hit%':>10} {'accuracy':>9}")
for T in [0.20, 0.24, 0.26, 0.28, 0.30, 0.32, 0.35, 0.40]:
    pick = np.where(pdraw >= T, 1, np.where(oh <= oa, 0, 2))
    a = float((pick == res).mean())
    dp = pick == 1
    dh = float((res[dp] == 1).mean()) if dp.sum() else float('nan')
    print(f"{T:6.2f} {100.0:8.1f}% {int(dp.sum()):11,} {dh*100:9.1f}% {a*100:8.1f}%")
print()

print("=" * 78)
print("CURVE 3c — combined: draw eligible when p_draw>=T AND favourite-ish")
print("=" * 78)
for T in [0.24, 0.28, 0.30, 0.32]:
    for X in [0.0, 0.45, 0.50]:
        # pick draw if pdraw>=T and pdraw is within X of pmax; else best non-draw
        elig = (pdraw >= T) & (pdraw >= pmax - X)
        pick = np.where(elig, 1, np.where(oh <= oa, 0, 2))
        a = float((pick == res).mean())
        dp = pick == 1
        dh = float((res[dp] == 1).mean()) if dp.sum() else float('nan')
        print(f"  T={T:.2f} X={X:.2f}  draw_picks={int(dp.sum()):6,} draw_hit={dh*100:5.1f}%  acc={a*100:5.1f}%")

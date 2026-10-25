# Part of analysis/pick_policy.md — see that file for the deliverable tables.
# Data comes from analysis/pick_policy_data.py (odds+result columns only).
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pick_policy_data import load_frame as _load_frame

#!/usr/bin/env python3
"""(1) Draw-promotion sweep — big market-only frame.
   (2) Draw promotion with MODEL probs on the sampled holdout.
   (3) Certainty quantiles -> coverage mapping (for parameterising the gate).
   (4) Accumulator success math for the booking-code logic."""
import numpy as np, pandas as pd

# ── (1) big frame ─────────────────────────────────────────────────────────
df = _load_frame()
f = df[(df.date >= '2015-08-01')].copy()
f = f[pd.to_numeric(f['is_international'], errors='coerce').fillna(0) == 0]
f = f[pd.to_numeric(f['quality_score'], errors='coerce').fillna(0) >= 20]
f = f.dropna(subset=['home_goals', 'away_goals', 'date'])
f = f.drop_duplicates(subset=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])
f = f[(f.odds_home > 1.01) & (f.odds_draw > 1.01) & (f.odds_away > 1.01)]
hg = pd.to_numeric(f.home_goals).astype(int).values
ag = pd.to_numeric(f.away_goals).astype(int).values
y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))
o = f[['odds_home', 'odds_draw', 'odds_away']].values
imp = 1.0 / o
mkt = imp / imp.sum(axis=1, keepdims=True)
fav = mkt.argmax(1); pmax = mkt.max(1); pdraw = mkt[:, 1]
base_acc = float((fav == y).mean())
print(f"BIG FRAME n={len(y):,}  market-fav acc={base_acc*100:.1f}%  "
      f"draw rate={float((y==1).mean())*100:.1f}%")

print()
print("=" * 84)
print("DRAW SWEEP (market probs) — promote Draw when p_draw >= pmax - delta")
print("=" * 84)
print(f"{'delta':>6} {'draw_picks':>10} {'draw_hit%':>10} {'accuracy':>9} {'vs argmax':>10} {'draw share':>10}")
for d in [0.0, 0.02, 0.03, 0.05, 0.07, 0.10, 0.12]:
    if d == 0:
        pick = fav.copy()
        # delta 0 = draw only when it IS the favourite
    else:
        promote = (pdraw >= pmax - d) & (fav != 1)
        pick = fav.copy(); pick[promote] = 1
    a = float((pick == y).mean())
    dp = pick == 1
    dh = float((y[dp] == 1).mean()) if dp.sum() else float('nan')
    print(f"{d:6.2f} {int(dp.sum()):10,} {dh*100:9.1f}% {a*100:8.1f}% {(a-base_acc)*100:+9.1f}p {dp.mean()*100:9.1f}%")

print()
print("=" * 84)
print("DRAW SWEEP (market probs) — Draw only if odds_draw <= Z AND p_draw >= pmax - delta")
print("=" * 84)
od = o[:, 1]
print(f"{'Z':>5} {'delta':>6} {'draw_picks':>10} {'draw_hit%':>10} {'accuracy':>9} {'vs argmax':>10}")
for Z in [2.80, 3.00, 3.25, 3.50]:
    for d in [0.03, 0.05, 0.07, 0.10]:
        promote = (pdraw >= pmax - d) & (fav != 1) & (od <= Z)
        pick = fav.copy(); pick[promote] = 1
        a = float((pick == y).mean())
        dp = pick == 1
        dh = float((y[dp] == 1).mean()) if dp.sum() else float('nan')
        print(f"{Z:5.2f} {d:6.2f} {int(dp.sum()):10,} {dh*100:9.1f}% {a*100:8.1f}% {(a-base_acc)*100:+9.1f}p")

print()
print("=" * 84)
print("ABSTAIN variant — only count a pick when we are willing to call it")
print("  policy: pick = favourite; if draw would be picked only when short-priced")
print("=" * 84)
# task wording: 'draws only predicted when the draw odds are below Z'
print(f"{'Z':>5} {'draw_picks':>10} {'draw_hit%':>10} {'accuracy(100% cov)':>19} {'note':>28}")
for Z in [2.40, 2.60, 2.75, 3.00, 3.25, 3.50, 4.00]:
    allow = od <= Z
    # draw is the favourite but too long -> REPLACE with best 1X2 side (never abstain)
    repl = np.where(o[:, 0] <= o[:, 2], 0, 2)
    pick = np.where((fav == 1) & ~allow, repl, fav)
    a = float((pick == y).mean())
    dp = pick == 1
    dh = float((y[dp] == 1).mean()) if dp.sum() else float('nan')
    print(f"{Z:5.2f} {int(dp.sum()):10,} {dh*100:9.1f}% {a*100:18.1f}%  {'draw suppressed->side pick' if dp.sum()==0 else 'draws kept':>28}")

# ── (2) model-side draw sweep on the sample ───────────────────────────────
arr = np.load('/tmp/opencode/pp_p.npy')
p_raw, p_cal, mkt_s, p_blend, ys = arr[:, 0:3], arr[:, 3:6], arr[:, 6:9], arr[:, 9:12], arr[:, 12].astype(int)
odds_s = np.load('/tmp/opencode/pp_odds.npy')
print()
print("=" * 84)
print(f"SAMPLE n={len(ys):,} — draw promotion with MODEL / BLEND probabilities")
print("=" * 84)
for tag, pp in (("model ", p_cal), ("blend ", p_blend)):
    base = pp.argmax(1)
    a0 = float((base == ys).mean())
    print(f"-- {tag} argmax acc={a0*100:.1f}%  draw picks={int((base==1).sum())}")
    for d in [0.0, 0.02, 0.03, 0.05, 0.07, 0.10]:
        pm = pp.max(1); pdw = pp[:, 1]
        if d == 0:
            pick = base.copy()
        else:
            promote = (pdw >= pm - d) & (base != 1)
            pick = base.copy(); pick[promote] = 1
        a = float((pick == ys).mean())
        dp = pick == 1
        dh = float((ys[dp] == 1).mean()) if dp.sum() else float('nan')
        print(f"     delta={d:4.2f}  acc={a*100:5.1f}% ({(a-a0)*100:+5.1f}p)  "
              f"draw_picks={int(dp.sum()):4,} draw_hit={dh*100 if dp.sum() else float('nan'):5.1f}%")

# ── (3) certainty quantiles ──────────────────────────────────────────────
p = p_blend
eps = 1e-12
ent = -np.sum(np.clip(p, eps, 1.0) * np.log(np.clip(p, eps, 1.0)), axis=1)
ent_n = ent / float(-np.log(1/3))
hd = np.maximum(np.mean(np.abs(p_raw - p_cal), axis=1), np.mean(np.abs(p_cal - p), axis=1) * 0.5)
unc = (ent_n >= 0.92) | (hd >= 0.08) | (p.max(1) * 100 < 52.0)
s = np.sort(p, axis=1)[:, ::-1]
mr = (s[:, 0] - s[:, 1]) / np.maximum(s[:, 0], 1e-9)
agr = 1.0 - np.minimum(1.0, hd * 4.0)
cert = np.clip(100.0 * (0.55 * s[:, 0] + 0.45 * mr) * (0.85 + 0.15 * agr), 0, 100)
pick = p.argmax(1)
print()
print("=" * 84)
print("CERTAINTY THRESHOLD -> coverage / accuracy (current scoring formula)")
print("=" * 84)
print(f"{'cert>=':>7} {'coverage':>9} {'n':>6} {'accuracy':>9} {'(excl. uncertain)':>18}")
for c in [45, 48, 50, 52, 55, 58, 60, 62, 65, 70]:
    m = cert >= c
    m2 = m & (~unc)
    a = (ys[m] == pick[m]).mean() if m.sum() else float('nan')
    a2 = (ys[m2] == pick[m2]).mean() if m2.sum() else float('nan')
    print(f"{c:7d} {m.mean()*100:8.1f}% {int(m.sum()):6,} {a*100:8.1f}%  "
          f"cov={m2.mean()*100:4.1f}% acc={a2*100:5.1f}% n={int(m2.sum())}")

# ── (4) acca math ────────────────────────────────────────────────────────
print()
print("=" * 84)
print("ACCUMULATOR (booking code) — P(all legs correct), independent legs")
print("=" * 84)
for legs in [3, 4, 5, 6]:
    line = f"  {legs} legs: " + "  ".join(
        f"@{a*100:.0f}%={a**legs*100:5.1f}%" for a in [0.55, 0.60, 0.65, 0.70, 0.75, 0.81])
    print(line)

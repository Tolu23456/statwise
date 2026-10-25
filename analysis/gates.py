# Part of analysis/pick_policy.md — see that file for the deliverable tables.
# Data comes from analysis/pick_policy_data.py (odds+result columns only).
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pick_policy_data import load_frame as _load_frame

#!/usr/bin/env python3
"""Reproduce the CURRENT production pick gate (certainty / is_uncertain /
is_ai_pick from ai/model/trainer.py + ai/model/predictor.py) on the sampled
holdout, and compare its accuracy-vs-coverage against market-confidence gates.
Also: draw-promotion sweep on the big frame with pp-tight tolerances."""
import os, sys
import numpy as np, pandas as pd

BASE = '/home/t85491005/statwise'
sys.path.insert(0, os.path.join(BASE, 'ai'))

arr = np.load('/tmp/opencode/pp_p.npy')
p_raw, p_cal, mkt, p_blend, y = arr[:, 0:3], arr[:, 3:6], arr[:, 6:9], arr[:, 9:12], arr[:, 12].astype(int)
odds = np.load('/tmp/opencode/pp_odds.npy')
n = len(y)

# rebuild the row frame with the identical filters to recover league_slug
df = _load_frame()
f = df[(df.date >= '2026-04-21')].copy()
f = f[pd.to_numeric(f['is_international'], errors='coerce').fillna(0) == 0]
f = f[pd.to_numeric(f['quality_score'], errors='coerce').fillna(0) >= 20]
f = f.dropna(subset=['home_goals', 'away_goals', 'date'])
f = f.drop_duplicates(subset=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])
f = f[(f.odds_home > 1.01) & (f.odds_draw > 1.01) & (f.odds_away > 1.01)]
f = f.sort_values('date').reset_index(drop=True).iloc[:n]
leagues = f['league_slug'].fillna('all').astype(str).values

from model.trainer import FootballPredictor
model = FootballPredictor.load(os.path.join(BASE, 'ai/models/football_predictor.pkl'))
league_n = dict(model._league_counts or {})
print(f"league_counts entries: {len(league_n)}  alphas default={model._alphas.get('_default')}")

# ── exact certainty / uncertainty formula (trainer.predict_match) ─────────
p = p_blend
eps = 1e-12
ent = -np.sum(np.clip(p, eps, 1.0) * np.log(np.clip(p, eps, 1.0)), axis=1)
entropy_norm = ent / float(-np.log(1.0 / 3.0))
head_dis = np.mean(np.abs(p_cal - p_blend), axis=1) * 0.5     # calibs are None in this pickle
head_dis = np.maximum(np.mean(np.abs(p_raw - p_cal), axis=1), head_dis)
is_uncertain = (entropy_norm >= 0.92) | (head_dis >= 0.08) | (p.max(1) * 100.0 < 52.0)
sorted_p = np.sort(p, axis=1)[:, ::-1]
p1, p2 = sorted_p[:, 0], sorted_p[:, 1]
margin_ratio = (p1 - p2) / np.maximum(p1, 1e-9)
agreement = 1.0 - np.minimum(1.0, head_dis * 4.0)
certainty = 100.0 * (0.55 * p1 + 0.45 * margin_ratio) * (0.85 + 0.15 * agreement)
certainty = np.clip(certainty, 0.0, 100.0)

# league-support reduction (predictor.run): pick_bar = 65 (+10 if thin league)
samples = np.array([league_n.get(lg, -1) for lg in leagues])
reduced = (samples >= 0) & (samples < 200)
pick_bar = np.where(reduced, 75.0, 65.0)
is_ai_pick = (certainty >= pick_bar) & (~is_uncertain)

pick = p.argmax(1)
pmax_blend = p.max(1)
pmax_mkt = mkt.max(1)
agree = (p.argmax(1) == mkt.argmax(1))

print()
print("=" * 78)
print(f"SAMPLE n={n:,}  production-path inference, current gate reproduced exactly")
print("=" * 78)
print(f"  is_uncertain share      : {is_uncertain.mean()*100:.1f}%")
print(f"  certainty mean/median   : {certainty.mean():.1f} / {np.median(certainty):.1f}")
print(f"  is_ai_pick (current)    : coverage={is_ai_pick.mean()*100:.1f}%  "
      f"accuracy={(y[is_ai_pick]==pick[is_ai_pick]).mean()*100:.1f}%  "
      f"n={int(is_ai_pick.sum())}")
print(f"  current AI-pick mix home/draw/away = "
      f"{np.bincount(pick[is_ai_pick], minlength=3) / max(is_ai_pick.sum(),1)}")
print(f"  (all-match argmax acc   : {(y==pick).mean()*100:.1f}%)")

def row(label, mask):
    if mask.sum() == 0:
        return f"  {label:<44} cov=  0.0%  n=    0"
    a = (y[mask] == pick[mask]).mean()
    return f"  {label:<44} cov={mask.mean()*100:5.1f}%  n={int(mask.sum()):5,}  acc={a*100:5.1f}%"

print()
print("=" * 78)
print("GATE COMPARISON at matched coverage (accuracy of the DISPLAYED pick)")
print("=" * 78)
targets = [0.60, 0.50, 0.40, 0.33, 0.30, 0.25, 0.20, 0.15, 0.10]
print(f"{'cov target':>11} | {'current certainty gate':>24} | {'market pmax gate':>18} | "
      f"{'blend pmax gate':>16} | {'agree+market pmax':>18}")
for T in targets:
    out = [f"{T*100:9.0f}%"]
    for name, score in (("cert", certainty), ("mkt", pmax_mkt), ("blend", pmax_blend)):
        thr = np.quantile(score, 1.0 - T)
        m = score >= thr
        out.append(f"{(y[m]==pick[m]).mean()*100:5.1f}% (n={int(m.sum())})")
    thr = np.quantile(pmax_mkt, 1.0 - T)
    m = agree & (pmax_mkt >= thr)
    out.append(f"{(y[m]==pick[m]).mean()*100:5.1f}% (n={int(m.sum())})" if m.sum() else "  n/a")
    print(" | ".join(out))

print()
print("=" * 78)
print("PROPOSED GATE — explicit rule set (readable, tunable)")
print("=" * 78)
def report(label, mask):
    if mask.sum() == 0:
        print(f"  {label:<58} cov=  0.0% n=    0")
        return
    a = (y[mask] == pick[mask]).mean()
    mix = np.bincount(pick[mask], minlength=3) / mask.sum()
    print(f"  {label:<58} cov={mask.mean()*100:5.1f}% n={int(mask.sum()):5,} "
          f"acc={a*100:5.1f}%  pick mix {mix[0]*100:.0f}/{mix[1]*100:.0f}/{mix[2]*100:.0f}")

for X in [0.46, 0.48, 0.50, 0.52, 0.55, 0.58]:
    for name, extra in (("agree only", agree),
                        ("model-vs-mkt edge>=0.03", p_cal.max(1) - pmax_mkt >= 0.03),
                        ("no filter", np.ones(n, bool))):
        report(f"market pmax>={X:.2f} + {name}", (pmax_mkt >= X) & extra)

print()
print("=" * 78)
print("WHAT THE CURRENT GATE IS SELECTING ON (correlation check)")
print("=" * 78)
print(f"  corr(certainty, market pmax)      = {np.corrcoef(certainty, pmax_mkt)[0,1]:.3f}")
print(f"  corr(certainty, blend top prob)   = {np.corrcoef(certainty, p1)[0,1]:.3f}")
print(f"  accuracy of certainty>=65 & !unc  = {(y[(certainty>=65)&~is_uncertain]==pick[(certainty>=65)&~is_uncertain]).mean()*100:.1f}%  n={int(((certainty>=65)&~is_uncertain).sum())}")

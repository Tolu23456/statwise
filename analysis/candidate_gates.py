# Part of analysis/pick_policy.md — see that file for the deliverable tables.
# Data comes from analysis/pick_policy_data.py (odds+result columns only).
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pick_policy_data import load_frame as _load_frame

#!/usr/bin/env python3
"""Candidate production gates: coverage / accuracy / leg yield on the sample."""
import numpy as np, pandas as pd

arr = np.load('/tmp/opencode/pp_p.npy')
p_raw, p_cal, mkt, p = arr[:, 0:3], arr[:, 3:6], arr[:, 6:9], arr[:, 9:12]
y = arr[:, 12].astype(int)
n = len(y)

# certainty / uncertainty (exact production formula)
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
pmax = mkt.max(1)
agree = (p.argmax(1) == mkt.argmax(1))
acc_all = (y == pick).mean()

print(f"n={n:,}  all-match argmax acc={acc_all*100:.1f}%   "
      f"sample window ~{n/176:.1f} odds-covered matches/day")
print()
print(f"{'gate':<62} {'cov':>6} {'n':>5} {'acc':>6} {'per-week legs':>13}")
cands = [
    ("CURRENT: cert>=65 & !uncertain", (cert >= 65) & ~unc),
    ("cert>=55 & !uncertain", (cert >= 55) & ~unc),
    ("cert>=52 & !uncertain", (cert >= 52) & ~unc),
    ("cert>=50 & !uncertain", (cert >= 50) & ~unc),
    ("cert>=55 & agree & !uncertain", (cert >= 55) & agree & ~unc),
    ("market pmax>=0.50 & agree & !unc", (pmax >= 0.50) & agree & ~unc),
    ("market pmax>=0.52 & agree & !unc", (pmax >= 0.52) & agree & ~unc),
    ("market pmax>=0.55 & agree & !unc", (pmax >= 0.55) & agree & ~unc),
    ("market pmax>=0.60 & agree & !unc", (pmax >= 0.60) & agree & ~unc),
    ("market pmax>=0.50 & agree & !unc & cert>=48", (pmax >= 0.50) & agree & ~unc & (cert >= 48)),
    ("market pmax>=0.50 & agree & !unc & cert>=52", (pmax >= 0.50) & agree & ~unc & (cert >= 52)),
    ("market pmax>=0.55 & agree & !unc & cert>=52", (pmax >= 0.55) & agree & ~unc & (cert >= 52)),
    ("market pmax>=0.52 & agree & !unc & cert>=55", (pmax >= 0.52) & agree & ~unc & (cert >= 55)),
]
for label, m in cands:
    if m.sum() == 0:
        print(f"{label:<62}   0.0%     0    n/a")
        continue
    a = (y[m] == pick[m]).mean()
    mix = np.bincount(pick[m], minlength=3) / m.sum()
    legs = m.mean() * (n / 176.0) * 7
    print(f"{label:<62} {m.mean()*100:5.1f}% {int(m.sum()):5,} {a*100:5.1f}% "
          f"{legs:11.0f}   mix {mix[0]*100:.0f}/{mix[1]*100:.0f}/{mix[2]*100:.0f}")

print()
print("Top-N-per-week-by-certainty (accumulator shape):")
f = _load_frame()
f = f[(f.date >= '2026-04-21')].copy()
f = f[pd.to_numeric(f['is_international'], errors='coerce').fillna(0) == 0]
f = f[pd.to_numeric(f['quality_score'], errors='coerce').fillna(0) >= 20]
f = f.dropna(subset=['home_goals', 'away_goals', 'date'])
f = f.drop_duplicates(subset=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])
f = f[(f.odds_home > 1.01) & (f.odds_draw > 1.01) & (f.odds_away > 1.01)]
f = f.sort_values('date').reset_index(drop=True).iloc[:n]
week = ((f['date'] - f['date'].min()).dt.days // 7).values

for gate_label, gate in (("no gate", np.ones(n, bool)),
                         ("pmax>=0.50 & agree & !unc", (pmax >= 0.50) & agree & ~unc),
                         ("cert>=55 & !unc", (cert >= 55) & ~unc)):
    for N in [3, 4, 5]:
        hits, weeks_used = 0, 0
        per_week = []
        for w in np.unique(week):
            idx = np.flatnonzero((week == w) & gate)
            if len(idx) < N:
                continue
            idx = idx[np.argsort(-cert[idx])][:N]
            h = int((y[idx] == pick[idx]).sum())
            hits += h; weeks_used += 1
            per_week.append(h)
        if weeks_used:
            acc = hits / (weeks_used * N)
            full = float(np.mean([h == N for h in per_week]))
            print(f"  {gate_label:<30} top {N}/week: legs={weeks_used*N:4,} "
                  f"acc={acc*100:5.1f}%  share of weeks with ALL {N} right={full*100:4.1f}%")

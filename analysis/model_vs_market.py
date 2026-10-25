# Part of analysis/pick_policy.md — see that file for the deliverable tables.
# Data comes from analysis/pick_policy_data.py (odds+result columns only).
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pick_policy_data import load_frame as _load_frame

#!/usr/bin/env python3
"""Sampled model-vs-market comparison on a recent holdout (no training).

Mimics PRODUCTION inference exactly: pickle loaded, empty history (what
PredictionEngine.run() does after a plain load), isotonic calibrators if the
pickle has them, then the alpha market blend from ai/model/trainer.py.
"""
import sys, os, time, resource
import numpy as np, pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
BASE = '/home/t85491005/statwise'
sys.path.insert(0, os.path.join(BASE, 'ai'))

t0 = time.time()
from model.trainer import FootballPredictor, OUTCOME_LABELS

model = FootballPredictor.load(os.path.join(BASE, 'ai/models/football_predictor.pkl'))
print(f"model loaded in {time.time()-t0:.1f}s, "
      f"alphas={model._alphas}, calibrators={'yes' if model._calibrators else 'no'}")
print(f"calibration_info: { {k:v for k,v in (model._calibration_info or {}).items() if k in ('holdout_days','raw_log_loss','isotonic_log_loss','calibration_used','market_log_loss','alpha_default')} }")

df = _load_frame()
f = df[(df.date >= '2026-04-21')].copy()
f = f[pd.to_numeric(f['is_international'], errors='coerce').fillna(0) == 0]
f = f[pd.to_numeric(f['quality_score'], errors='coerce').fillna(0) >= 20]
f = f.dropna(subset=['home_goals', 'away_goals', 'date'])
f = f.drop_duplicates(subset=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])
f = f[(f.odds_home > 1.01) & (f.odds_draw > 1.01) & (f.odds_away > 1.01)]
f = f.sort_values('date').reset_index(drop=True)
print(f"holdout rows with odds: {len(f):,}  ({f.date.min().date()} -> {f.date.max().date()})")

N = int(os.environ.get('N_ROWS', '0')) or len(f)
f = f.iloc[:N]

matches = [dict(home=r.home_team, away=r.away_team, league=(r.league_slug or 'all'),
                date=r.date, odds_home=r.odds_home, odds_draw=r.odds_draw,
                odds_away=r.odds_away) for r in f.itertuples()]
empty = pd.DataFrame(columns=['date', 'home_team', 'away_team', 'home_goals', 'away_goals'])

t1 = time.time()
X = model.feature_pipe.build_features(matches, empty)
X = np.nan_to_num(np.asarray(X, dtype=np.float64), nan=0.0, posinf=1e6, neginf=-1e6)
print(f"features built: {X.shape} in {time.time()-t1:.1f}s  "
      f"RSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024} MB")

p_raw = model._outcome_pipe.predict_proba(X)
p_cal = p_raw.copy()
if model._calibrators:
    p_cal = np.array([iso.predict(p_raw[:, i]) for i, iso in enumerate(model._calibrators)]).T
    p_cal = np.clip(p_cal, 1e-6, None)
    p_cal = p_cal / p_cal.sum(axis=1, keepdims=True)

o = f[['odds_home', 'odds_draw', 'odds_away']].values.astype(float)
imp = 1.0 / o
mkt = imp / imp.sum(axis=1, keepdims=True)

alphas = model._alphas or {}
use_a = np.array([alphas.get(str(lg), alphas.get('_default', 0.35)) for lg in f['league_slug'].fillna('all')])
p_blend = use_a[:, None] * p_cal + (1.0 - use_a[:, None]) * mkt
p_blend = np.clip(p_blend, 1e-12, None)
p_blend = p_blend / p_blend.sum(axis=1, keepdims=True)

hg = pd.to_numeric(f.home_goals).astype(int).values
ag = pd.to_numeric(f.away_goals).astype(int).values
y = np.where(hg > ag, 0, np.where(hg == ag, 1, 2))

def ll(p):
    return float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1.0)).mean())
def acc(p):
    return float((p.argmax(1) == y).mean())

print()
print("=" * 78)
print(f"MODEL vs MARKET on {len(y):,} sampled holdout rows (production inference path)")
print("=" * 78)
print(f"  model(raw argmax)   acc={acc(p_raw)*100:5.1f}%  ll={ll(p_raw):.5f}")
print(f"  model(calibrated)   acc={acc(p_cal)*100:5.1f}%  ll={ll(p_cal):.5f}   (calibrators attached: {bool(model._calibrators)})")
print(f"  market (de-vigged)  acc={acc(mkt)*100:5.1f}%  ll={ll(mkt):.5f}")
print(f"  production blend    acc={acc(p_blend)*100:5.1f}%  ll={ll(p_blend):.5f}   (alpha={np.unique(use_a)[:5]})")
print(f"  always home         acc={float((y==0).mean())*100:5.1f}%")
print(f"  outcome mix home/draw/away = {float((y==0).mean()):.3f}/{float((y==1).mean()):.3f}/{float((y==2).mean()):.3f}")
print(f"  pick mix   model    = {np.bincount(p_cal.argmax(1), minlength=3)/len(y)}")
print(f"  pick mix   blend    = {np.bincount(p_blend.argmax(1), minlength=3)/len(y)}")
print(f"  pick mix   market   = {np.bincount(mkt.argmax(1), minlength=3)/len(y)}")

am, ab, ak = p_cal.argmax(1), p_blend.argmax(1), mkt.argmax(1)
print(f"  agreement model vs market: {(am==ak).mean()*100:.1f}%   blend vs market: {(ab==ak).mean()*100:.1f}%")
print(f"  accuracy | model==market agree : {(y[am==ak]==am[am==ak]).mean()*100:.1f}% (n={(am==ak).sum()})")
print(f"  accuracy | model!=market       : {(y[am!=ak]==am[am!=ak]).mean()*100:.1f}% (n={(am!=ak).sum()})")
print(f"  accuracy | market on disagree  : {(y[am!=ak]==ak[am!=ak]).mean()*100:.1f}%")
print(f"  accuracy | model   on disagree : {(y[am!=ak]==am[am!=ak]).mean()*100:.1f}%")
print(f"  accuracy | blend   on disagree : {(y[am!=ak]==ab[am!=ak]).mean()*100:.1f}%")

np.save('/tmp/opencode/pp_p.npy', np.hstack([p_raw, p_cal, mkt, p_blend, y.reshape(-1, 1)]))
np.save('/tmp/opencode/pp_odds.npy', o)
print(f"\nsaved arrays; total {time.time()-t0:.1f}s  "
      f"RSS={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024} MB")

# ── agreement / edge curves ──────────────────────────────────────────────
pmax = mkt.max(1)
mkt_pick = ak
model_pick = am

def curve(title, masks_fn, label):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"{'param':>8} {'coverage':>9} {'picks':>7} {'accuracy':>9} {'model mix(h/d/a)':>20}")
    return None

print()
print("=" * 78)
print("CURVE A — pick only when MODEL and MARKET AGREE, market pmax >= X")
print("=" * 78)
agree = (am == ak)
print(f"{'X':>6} {'coverage':>9} {'picks':>7} {'accuracy':>9}  (agree-only)")
for X in [0.0, 0.40, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65]:
    m = agree & (pmax >= X)
    if m.sum() < 20:
        continue
    print(f"{X:6.2f} {m.mean()*100:8.1f}% {int(m.sum()):7,} {float((y[m]==am[m]).mean())*100:8.1f}%")

print()
print("=" * 78)
print("CURVE B — pick only when model prob beats market implied by >= Y (model's own pick)")
print("=" * 78)
pick_idx = np.arange(len(y))
model_prob_at_pick = p_cal[pick_idx, am]
mkt_prob_at_pick = mkt[pick_idx, am]
edge = model_prob_at_pick - mkt_prob_at_pick
print(f"{'Y':>6} {'coverage':>9} {'picks':>7} {'accuracy':>9} {'hit@model_pick':>14}")
for Y in [0.00, 0.02, 0.03, 0.05, 0.07, 0.10, 0.12, 0.15, 0.20]:
    m = edge >= Y
    if m.sum() < 20:
        continue
    print(f"{Y:6.2f} {m.mean()*100:8.1f}% {int(m.sum()):7,} {float((y[m]==am[m]).mean())*100:8.1f}%")

print()
print("=" * 78)
print("CURVE C — production blend pick: pmax_blend >= X (what production would report)")
print("=" * 78)
bpmax = p_blend.max(1)
for X in [0.40, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60, 0.65, 0.70]:
    m = bpmax >= X
    if m.sum() < 20:
        continue
    print(f"{X:6.2f} {m.mean()*100:8.1f}% {int(m.sum()):7,} {float((y[m]==ab[m]).mean())*100:8.1f}%")

print()
print("=" * 78)
print("CURVE D — hybrid: agree AND market pmax >= X (candidate production policy)")
print("=" * 78)
print(f"{'X':>6} {'coverage':>9} {'picks':>7} {'accuracy':>9}")
for X in [0.40, 0.44, 0.46, 0.48, 0.50, 0.52, 0.55, 0.58, 0.60]:
    m = agree & (pmax >= X)
    if m.sum() < 20:
        continue
    print(f"{X:6.2f} {m.mean()*100:8.1f}% {int(m.sum()):7,} {float((y[m]==ab[m]).mean())*100:8.1f}%")

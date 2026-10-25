#!/usr/bin/env python3
"""Measure the train/serve feature gap caused by an EMPTY history frame at inference.

Live path: PredictionEngine.history is only populated when a retrain happens in
the same process. scheduler.py loads the pickle -> history stays empty ->
predict_match() replaces it with an empty frame -> FeaturePipeline.build_features()
computes form/H2H/streak/temporal/travel blocks from NO history.

This script fits a pipeline on real recent data, then builds the same fixture's
feature vector twice: with real history (training-equivalent) and with the empty
history the live path actually passes.
"""
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/t85491005/statwise/ai")
from model.features import FeaturePipeline, FEATURE_NAMES  # noqa: E402

USECOLS = ["date", "home_team", "away_team", "home_goals", "away_goals",
           "league_slug", "odds_home", "odds_draw", "odds_away",
           "avg_odds_home", "avg_odds_draw", "avg_odds_away"]

frames = []
for f in sorted(glob.glob("/home/t85491005/statwise/ai/data/clean/202[4-6]_matches.csv")):
    frames.append(pd.read_csv(f, usecols=USECOLS, dtype=str, keep_default_na=False))
df = pd.concat(frames, ignore_index=True); del frames
df["date"] = pd.to_datetime(df["date"], errors="coerce")
for c in ("home_goals", "away_goals"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
df = df.dropna(subset=["home_goals", "away_goals"]).sort_values("date").reset_index(drop=True)
for c in ("odds_home", "odds_draw", "odds_away",
          "avg_odds_home", "avg_odds_draw", "avg_odds_away"):
    df[c] = pd.to_numeric(df[c], errors="coerce")
df["home_goals"] = df["home_goals"].astype(int)
df["away_goals"] = df["away_goals"].astype(int)
print(f"fit frame: {len(df):,} rows  {df['date'].min().date()}..{df['date'].max().date()}")

pipe = FeaturePipeline()
pipe.fit(df)

# a real fixture from the tail of the data
row = df.iloc[-5]
def _o(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0

fixture = {"home": row["home_team"], "away": row["away_team"],
           "league": row["league_slug"],
           "odds_home": _o(row["odds_home"]),
           "odds_draw": _o(row["odds_draw"]),
           "odds_away": _o(row["odds_away"])}
print(f"fixture: {fixture['home']} vs {fixture['away']} ({fixture['league']})")

X_real = pipe.build_features([fixture], df)[0]
X_empty = pipe.build_features([fixture], pd.DataFrame(columns=["date", "home_team", "away_team", "home_goals", "away_goals"]))[0]

# typical training-row values for the same feature block (alignment reference)
X_train, y, _, _ = pipe.build_training_set(df, max_samples=4000)
med = np.median(X_train, axis=0)

names = FEATURE_NAMES + ["league_id"] if len(FEATURE_NAMES) == 161 else FEATURE_NAMES
while len(names) < 161:
    names.append(f"f{len(names)}")

diff = np.abs(X_real - X_empty)
changed = np.where(diff > 1e-9)[0]
print(f"\nfeatures that change when history is empty: {len(changed)} / 161")
print(f"{'idx':>4} {'name':28s} {'with history':>14s} {'empty history':>14s} {'train median':>13s}")
for i in changed:
    print(f"{i:4d} {names[i]:28s} {X_real[i]:14.3f} {X_empty[i]:14.3f} {med[i]:13.3f}")

# how many features sit at their 'no data' default at inference
same = np.where(np.isclose(X_real, X_empty, atol=1e-9))[0]
print(f"\nfeatures identical either way: {len(same)}")
# of those, how many differ from the training median by a lot -> not informative
print("\n-- inference vector vs training distribution (with-history case) --")
lo, hi = np.percentile(X_train, 5, axis=0), np.percentile(X_train, 95, axis=0)
out_real = [i for i in range(161) if X_real[i] < lo[i] - 1e-9 or X_real[i] > hi[i] + 1e-9]
out_empty = [i for i in range(161) if X_empty[i] < lo[i] - 1e-9 or X_empty[i] > hi[i] + 1e-9]
print(f"outside [p5,p95] of training WITH history   : {len(out_real)}")
print(f"outside [p5,p95] of training EMPTY history  : {len(out_empty)}")
print("empty-history outliers:", [(i, names[i], round(float(X_empty[i]), 3), round(float(med[i]), 3)) for i in out_empty][:40])

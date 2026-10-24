"""Sanity checks for ai/model/travel.py (Step 1.1: travel/fatigue proxies)."""
import glob
import os
import math
import sys
import time

import numpy as np
import pandas as pd

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)
REPO = os.path.dirname(AI_DIR)
from model.travel import (compute_travel_block, venue_coords, haversine_km,
                          N_TRAVEL, TRAVEL_SLICE, load_venues)

ok = True
def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)

# ── venue lookups ────────────────────────────────────────────────────────
v = load_venues()
check("venue cache loaded", len(v) > 1000, f"({len(v)} teams)")
lat, lon = venue_coords("Manchester United FC")
check("coords for Man Utd", math.isfinite(lat) and math.isfinite(lon), f"({lat:.3f},{lon:.3f})")
check("case-insensitive lookup", venue_coords("manchester united fc") == (lat, lon))
check("unknown team -> NaN", all(math.isnan(x) for x in venue_coords("Totally Unknown Team 999")))

km = float(haversine_km(51.5074, -0.1278, 53.4809, -2.2374))
check("haversine London-Manchester ~262km", 250 < km < 275, f"({km:.1f})")

# ── semantics on a small frame ───────────────────────────────────────────
# Man Utd (MCR) hosts Chelsea (London).  Round trip: both sides cross the
# same MCR<->London gap whenever the venue flips.
df = pd.DataFrame([
    {"home_team": "Manchester United FC", "away_team": "Chelsea FC", "date": "2025-01-01"},
    {"home_team": "Chelsea FC",           "away_team": "Manchester United FC", "date": "2025-01-08"},
    {"home_team": "Manchester United FC", "away_team": "Chelsea FC", "date": "2025-01-15"},
    {"home_team": "Chelsea FC",           "away_team": "Totally Unknown Team 999", "date": "2025-01-22"},
])
blk = compute_travel_block(df)
check("block shape", blk.shape == (len(df), N_TRAVEL), str(blk.shape))
check("all finite", np.isfinite(blk).all())
check("cols 0,1,4 within [0,1]",
      (blk[:, [0, 1, 4]] >= 0).all() and (blk[:, [0, 1, 4]] <= 1).all())
check("row0 first appearance -> 0 travel / presence 0",
      np.allclose(blk[0], 0), str(blk[0].round(3)))
# venue flips MCR->London: BOTH sides travelled into match 2
check("row1 both sides travelled", blk[1, 0] > 0 and blk[1, 1] > 0,
      f"h={blk[1,0]:.3f} a={blk[1,1]:.3f}")
check("row1 identical distance both ways", abs(blk[1, 0] - blk[1, 1]) < 1e-9)
check("row1 travel_diff = h - a", abs(blk[1, 2]) < 1e-12, str(blk[1, 2]))
# unknown opponent: presence counts only the resolved side
check("row3 partial presence = 0.5", abs(blk[3, 4] - 0.5) < 1e-12, str(blk[3].round(3)))
check("travel_diff consistency", np.allclose(blk[:, 2], blk[:, 0] - blk[:, 1]))

# ── real dataset: shape, coverage, non-degeneracy, speed ─────────────────
paths = sorted(glob.glob(os.path.join(AI_DIR, "data", "clean", "????_matches.csv")))
if paths:
    df = pd.concat([pd.read_csv(p, usecols=["home_team", "away_team", "date"],
                                low_memory=False) for p in paths],
                   ignore_index=True).sort_values("date").reset_index(drop=True)
    t0 = time.time()
    big = compute_travel_block(df)
    secs = time.time() - t0
    check("real block shape", big.shape == (len(df), N_TRAVEL), str(big.shape))
    check("real block all finite", np.isfinite(big).all())
    nz = (big[:, 0] + big[:, 1]) > 0
    print(f"      coverage: {nz.mean()*100:.1f}% of matches show travel; "
          f"mean travel_h={big[:,0].mean():.3f} std={big[:,0].std():.3f}; "
          f"travel_diff std={big[:,2].std():.3f}; fatigue_diff std={big[:,3].std():.3f}; "
          f"{secs:.1f}s for {len(df):,} rows")
    check("travel non-degenerate (std > 0)", big[:, 0].std() > 0.01 and big[:, 2].std() > 0.01)
    check("travel not saturated (mean < 0.5)", big[:, 0].mean() < 0.5)
    check("reasonable runtime (< 120s)", secs < 120, f"({secs:.1f}s)")
    # first row of history must be zero (no previous venue)
    check("first match row is zero", np.allclose(big[0], 0), str(big[0].round(3)))
else:
    check("clean dataset present", False)

check("TRAVEL_SLICE = 141:146", (TRAVEL_SLICE.start, TRAVEL_SLICE.stop) == (141, 146))

print("\nALL PASS" if ok else "\nFAILURES PRESENT")
sys.exit(0 if ok else 1)

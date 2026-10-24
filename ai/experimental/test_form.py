"""Checks for ai/model/form.py (Step 1.1: form differentials) + feature-name integrity."""
import os
import sys
import numpy as np
import pandas as pd

AI_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, AI_DIR)
REPO = os.path.dirname(AI_DIR)
from model.form import compute_form_block, N_FORM, FORM_SLICE

ok = True
def check(name, cond, extra=""):
    global ok
    print(f"{'PASS' if cond else 'FAIL'}  {name} {extra}")
    ok = ok and bool(cond)

# ── explicit tiny history ────────────────────────────────────────────────
# Arsenal: W(3-0), W(2-1), L(0-1), D(1-1), W(4-0)  -> last-5 before match 6
df = pd.DataFrame([
    {"home_team": "Arsenal", "away_team": "Chelsea", "home_goals": 3, "away_goals": 0, "date": "2025-01-01"},
    {"home_team": "Arsenal", "away_team": "Chelsea", "home_goals": 2, "away_goals": 1, "date": "2025-01-08"},
    {"home_team": "Chelsea", "away_team": "Arsenal", "home_goals": 1, "away_goals": 0, "date": "2025-01-15"},
    {"home_team": "Arsenal", "away_team": "Chelsea", "home_goals": 1, "away_goals": 1, "date": "2025-01-22"},
    {"home_team": "Arsenal", "away_team": "Chelsea", "home_goals": 4, "away_goals": 0, "date": "2025-01-29"},
    {"home_team": "Arsenal", "away_team": "Chelsea", "home_goals": 9, "away_goals": 9, "date": "2025-02-05"},
])
blk = compute_form_block(df)
check("shape", blk.shape == (len(df), N_FORM), str(blk.shape))
check("all finite", np.isfinite(blk).all())
check("first appearances are zero", np.allclose(blk[0], 0), str(blk[0].round(3)))

# Arsenal's form entering match 6 (index 5): results W W L D W = 3,3,0,1,3
# PPG = 10/5 = 2.0 -> normalised 2/3 = 0.6667
check("Arsenal PPG (row5, home)", abs(blk[5, 0] - (10 / 5) / 3) < 1e-9,
      f"{blk[5,0]:.4f} vs {(10/5)/3:.4f}")
# Arsenal GD over those 5: +3, +1, -1, 0, +4 = +7 -> mean 1.4 -> /4 = 0.35
check("Arsenal GD (row5, home)", abs(blk[5, 3] - (7 / 5) / 4) < 1e-9,
      f"{blk[5,3]:.4f} vs {(7/5)/4:.4f}")
# Chelsea entering match 6: L(0-3 as away? no - chelsea was away) ...
# Chelsea's prior matches: row0 away L 0-3 (gd -3), row1 away L 1-2 (-1),
# row2 home W 1-0 (+1), row3 away D 1-1 (0), row4 away L 0-4 (-4)
# PPG = 0+0+3+1+0 = 4 -> 4/5/3 = 0.2667 ; GD = -7 -> mean -1.4 -> -0.35
check("Chelsea PPG (row5, away)", abs(blk[5, 1] - (4 / 5) / 3) < 1e-9,
      f"{blk[5,1]:.4f} vs {(4/5)/3:.4f}")
check("Chelsea GD (row5, away)", abs(blk[5, 4] - (-7 / 5) / 4) < 1e-9,
      f"{blk[5,4]:.4f} vs {(-7/5)/4:.4f}")
check("ppg_diff = home - away", abs(blk[5, 2] - (blk[5, 0] - blk[5, 1])) < 1e-12)
check("form_present = 1 for both (>=5 priors)", abs(blk[5, 8] - 1.0) < 1e-12,
      str(blk[5, 8]))

# no leakage: change the CURRENT match score drastically -> row must not move
df2 = df.copy(); df2.loc[5, ["home_goals", "away_goals"]] = [0, 10]
blk2 = compute_form_block(df2)
check("no leakage from current result", np.allclose(blk[5], blk2[5]))

# ── cross-check vs an independent loop implementation ────────────────────
rng = np.random.default_rng(7)
m = 60
teams = [f"T{i}" for i in range(8)]
rows = []
for i in range(m):
    h, a = rng.choice(teams, 2, replace=False)
    rows.append({"home_team": h, "away_team": a,
                 "home_goals": int(rng.integers(0, 5)),
                 "away_goals": int(rng.integers(0, 5)),
                 "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i * 3)})
rnd = pd.DataFrame(rows)
fast = compute_form_block(rnd)

def ref_form(frame, team, upto):
    """naive: prior matches of `team` strictly before row `upto`"""
    pts, gds = [], []
    for j in range(upto):
        r = frame.iloc[j]
        if team == r["home_team"]:
            gf, ga = r["home_goals"], r["away_goals"]
        elif team == r["away_team"]:
            gf, ga = r["away_goals"], r["home_goals"]
        else:
            continue
        pts.append(3 if gf > ga else (1 if gf == ga else 0))
        gds.append(gf - ga)
    last5p, last5g = pts[-5:], gds[-5:]
    ppg = (sum(last5p) / len(last5p) / 3) if last5p else 0.0
    gd = (sum(last5g) / len(last5g) / 4) if last5g else 0.0
    trend = 0.0
    if len(last5g) == 5:
        x = np.arange(5.0)
        trend = float(np.polyfit(x, last5g, 1)[0]) / 2.0
    return ppg, gd, trend, (1.0 if len(pts) >= 5 else 0.0)

max_err = 0.0
for i in range(m):
    hp = ref_form(rnd, rnd.iloc[i]["home_team"], i)
    ap = ref_form(rnd, rnd.iloc[i]["away_team"], i)
    exp = np.array([hp[0], ap[0], hp[0] - ap[0],
                    hp[1], ap[1], hp[1] - ap[1],
                    hp[2], ap[2], (hp[3] + ap[3]) / 2.0])
    max_err = max(max_err, float(np.max(np.abs(exp - fast[i]))))
check("matches independent reference impl", max_err < 1e-9, f"max_err={max_err:.3e}")

# ── feature names / index integrity in features.py ──────────────────────
import re
src = open(os.path.join(AI_DIR, "model", "features.py")).read()
m2 = re.search(r"FEATURE_NAMES = \[(.*?)\n\]", src, re.S)
names = re.findall(r'"([^"]+)"', m2.group(1))
check("FEATURE_NAMES count = 161", len(names) == 161, str(len(names)))
check("N_FEATURES = 161", "N_FEATURES = 161" in src)
check("slots 141-145 are travel", names[141:146] ==
      ["travel_home", "travel_away", "travel_diff", "fatigue_diff", "travel_present"],
      str(names[141:146]))
check("slots 146-154 are form", names[146:155] ==
      ["form5_ppg_home", "form5_ppg_away", "form5_ppg_diff",
       "form5_gd_home", "form5_gd_away", "form5_gd_diff",
       "gd_trend_home", "gd_trend_away", "form_present"], str(names[146:155]))
check("no duplicate feature names", len(set(names)) == len(names),
      str([n for n in set(names) if names.count(n) > 1]))
check("FORM_SLICE = 146:155", (FORM_SLICE.start, FORM_SLICE.stop) == (146, 155))

print("\nALL PASS" if ok else "\nFAILURES PRESENT")
sys.exit(0 if ok else 1)

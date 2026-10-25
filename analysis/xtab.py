# Part of analysis/pick_policy.md — see that file for the deliverable tables.
# Data comes from analysis/pick_policy_data.py (odds+result columns only).
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from pick_policy_data import load_frame as _load_frame

#!/usr/bin/env python3
"""Cross-tab: where do model and market disagree, and who is right there?"""
import numpy as np

arr = np.load('/tmp/opencode/pp_p.npy')
p_cal, mkt, p_blend, y = arr[:, 3:6], arr[:, 6:9], arr[:, 9:12], arr[:, 12].astype(int)
n = len(y)
am, ak, ab = p_cal.argmax(1), mkt.argmax(1), p_blend.argmax(1)
pmax = mkt.max(1)
agree = am == ak

print(f"n={n:,}   overall agreement model vs market = {agree.mean()*100:.1f}%")
print()
print(f"{'market pmax bucket':>22} {'share':>7} {'agree':>7} {'n':>6} | "
      f"{'acc model':>9} {'acc blend':>9} {'acc mkt':>8}")
edges = [0.0, 0.40, 0.45, 0.48, 0.50, 0.52, 0.55, 0.60, 1.01]
for lo, hi in zip(edges[:-1], edges[1:]):
    m = (pmax >= lo) & (pmax < hi)
    if m.sum() == 0:
        continue
    print(f"{lo*100:5.0f}-{hi*100:5.0f}%          {m.mean()*100:6.1f}% "
          f"{agree[m].mean()*100:6.1f}% {int(m.sum()):6,} | "
          f"{(y[m]==am[m]).mean()*100:8.1f}% {(y[m]==ab[m]).mean()*100:8.1f}% "
          f"{(y[m]==ak[m]).mean()*100:7.1f}%")

print()
print("DISAGREEMENT rows only (n=%d):" % int((~agree).sum()))
d = ~agree
print(f"  market right {float((y[d]==ak[d]).mean())*100:.1f}% | "
      f"model right {float((y[d]==am[d]).mean())*100:.1f}% | "
      f"blend right {float((y[d]==ab[d]).mean())*100:.1f}%")
print(f"  market pmax on disagreements: mean={pmax[d].mean():.3f} "
      f"median={np.median(pmax[d]):.3f} (vs {pmax[~d].mean():.3f} when agreeing)")
print(f"  draw outcomes in disagreements: {float((y[d]==1).mean())*100:.1f}% "
      f"(base draw rate {float((y==1).mean())*100:.1f}%)")

print()
print("What the market-favourite pick does on the disagreements by bucket:")
for lo, hi in [(0.40, 0.48), (0.48, 0.55), (0.55, 1.01)]:
    m = (pmax >= lo) & (pmax < hi) & d
    if m.sum() < 10:
        continue
    print(f"  pmax {lo:.2f}-{hi:.2f}: n={int(m.sum()):4,} "
          f"market acc={float((y[m]==ak[m]).mean())*100:5.1f}% "
          f"model acc={float((y[m]==am[m]).mean())*100:5.1f}%")

# Plan 5.16 — model-improvement experiment program: PRE-REGISTRATION

Written and committed BEFORE any experiment run (audit trail = git history of
this file). Harness: `ai/exp_audit.py` + `ai/exp_lib.py` + `ai/exp_market.py`,
driven by `.github/workflows/exp-audit.yml` (workflow_dispatch, CI-only heavy
training). Every arm is a PAIRED comparison against the production recipe on
the IDENTICAL 100k out-of-time audit split, reusing the `ai/audit_100k.py`
protocol (train <= 2019-08-10 = 232,017 rows; holdout 99,912; calibrate on the
first 49,956 holdout rows, evaluate on the later **49,956 eval rows**).

## Honest targets (unchanged from plan 5.14 evidence)

- Market closing prices get 50.81% top-1 on this split; our blend 51.15%.
- Realistic: blended acc 53-54%, blended LL 0.9952 -> <= 0.990.
- Milestone: STANDALONE model LL beating the market's 0.9979 (today 1.0158).
- Any claimed 70%+ accuracy would be leakage, not prediction.

## Grounding finding made BEFORE the first run (code recon, proven locally)

`ai/model/features.py::build_training_set` passes the odds matrix to the C++
bulk engine as `df[[...]].fillna(0).values.astype(np.float64)`.  Under pandas
>= 2 that expression yields an **F-contiguous (transposed) view**, not a
C-contiguous copy (verified at runtime: `C_CONTIGUOUS=False`).  The C engine
reads the odds pointer as flat row-major, so on the training path the
market-implied feature slots are computed from **odds_home values of
unrelated matches** (rows 3k, 3k+1, 3k+2 of the frame) and, for later rows,
from **out-of-bounds memory**.  Proven three ways: (1) constant odds
[2,3,5] on every row yields implied [1/3,1/3,1/3] + overround 1.5 = the exact
misread signature; (2) a C-contiguous copy of the same matrix yields the
correct devig; (3) with random per-row odds the C++ implied of target row k
reproduces exactly 1/odds_home of rows 3k, 3k+1, 3k+2 (values matched to
3 decimals).  The single-ROW inference path (`compute_all_features_v6`)
computes them correctly from a fresh contiguous array -> the deployed model
also has a train/serve skew on these slots.  Slots affected: 62-65
(`odds_implied_home/draw/away`, `market_overround`) and their derived
104-105 (`h_win_mkt_diff`, `a_win_mkt_diff`).  The market BLEND side is
unaffected (meta odds/odds_closing are numpy-built, contiguous).

Consequence for this program: E4 is re-framed as "repair + extend" with two
arms so the repair's effect is separately attributable.  The one-line
production fix (`np.ascontiguousarray`) is the main agent's to integrate in
`ai/model/features.py` — this subagent never edits it.

## Common protocol (all experiments)

- Arms trained in ONE process on the identical split; outcome head ONLY
  (3-class LightGBM, same params/seed/early-stopping as production
  `_lgbm_single` + `_fit_with_early_stopping`; the audit's other heads are
  independent fits and cannot affect the outcome head).
- Recency weights: production 4y half-life (floor 0.02) unless the arm
  overrides it.
- PRIMARY metric per experiment (below) = paired delta log loss of the
  per-class-isotonic probabilities on the 49,956 eval rows, normal-approx
  95% CI + z/p; SECONDARY = accuracy delta with McNemar p; ECE reported.
- Decision rule: ADOPT-CANDIDATE iff primary paired delta LL < 0 with
  p < 0.01, or p < 0.05 with the same sign in >= 3 independent
  members/variants AND no significant accuracy regression (McNemar
  p > 0.01 for a negative accuracy delta).  Otherwise REJECT.  Multiple
  comparisons: p < 0.01 or cross-member consistency; no metric shopping.
- Baseline guard: every job asserts its baseline arm reproduces the audit's
  1.01581 +/- 0.002 and records the actual value.

## Experiments (pre-registered, in execution order)

### E1 — seed/HP ensemble (suite `ensemble`)
Members (all share production n_estimators/max_bin/min_child_samples/
subsample/reg_alpha/reg_lambda/early-stopping):
m1 = baseline (seed 42, production HPs); m2 = seed 101, num_leaves 63,
lr 0.05; m3 = seed 202, num_leaves 155, lr 0.02; m4 = seed 303,
num_leaves 96, lr 0.035, colsample 0.5; m5 = seed 404, num_leaves 127,
lr 0.03, colsample 0.85, min_child_samples 100.
Arms: ens2 = mean(m1,m2), ens3 = mean(m1..m3), ens5 = mean(m1..m5), each
average followed by per-class isotonic on the averaged calibration probs.
**Primary: ens5 paired dLL vs baseline.** Secondary: ens2/ens3
(ensemble-size curve), member-vs-member consistency.

### E2 — per-league hierarchical blend alpha (no retrain; post-hoc on every arm)
Global alpha fitted on the CALIBRATION window only (grid 0.01), then
per-league alpha for leagues with >= 60 valid calibration rows, shrunk
toward the global alpha with w = n/(n+300) (production SHRINK_K).
**Primary: paired dLL (per-league blend vs global blend, both alpha fitted
on the calibration window) on the eval rows that carry closing odds.**
Audit-protocol blend (alpha fitted on the eval window, for comparability
with the published 0.9952) reported as secondary only.

### E3 — recency sample weighting (suite `recency_market`)
Baseline = 4y half-life.  Arms: 1y (365d), 2y (730d).  `none` is not run as
an arm: retrain.py already measured none = 1.01275 vs 4y = 1.00999 on the
production holdout.
**Primary: hl_365 and hl_730 paired dLL vs baseline (two pre-registered
tests, each judged at p < 0.01 or consistency across both).**

### E4 — market features: repair + multi-book extension (suite `recency_market`)
Arm `mktfix`: baseline recipe with slots 62-65 and 104-105 REPAIRED to what
the single-row path computes (devigged implied probs from the row's own
closing-preferring odds + overround; 1-imp_h / 1-imp_a).  No new columns.
Arm `mktfix_books`: `mktfix` + 13 appended columns (raw per-book data joined
from `ai/data/*.csv` via the same team-alias table parsed from
`ai/tools/dataset_cleaner.cpp`):
cons_h/cons_d/cons_a (devigged closing consensus from avg_odds_*),
cons_overround, disp_maxavg (mean cross-book closing dispersion),
n_books, open_std, open_range (cross-book OPENING dispersion, devigged),
steam_h/d/a, steam_l1 (|opening consensus - closing consensus|),
e4_raw_hit (coverage indicator).  All pre-match data; zero leakage risk.
Known coverage limit: raw per-book files cover 2015 -> May 2025 only, so the
book-level columns exist for ~10% of train rows and ~55% of eval rows;
cons_* columns have near-full odds coverage.
**Primary: mktfix paired dLL vs baseline.  Co-primary: mktfix_books paired
dLL vs baseline.**  If both win, a follow-up `combo` suite re-tests them
jointly with the E1 winner.

### E5 — calibration upgrade (post-hoc on every arm; no retrain)
Variants fitted on the calibration window only: per-class isotonic (current,
PRIMARY comparator), raw (none), per-class Platt, vector scaling
(a_k*log p_k + b_k, scipy L-BFGS, weak L2), Dirichlet/matrix scaling
(multinomial logistic on log probs, C=1), per-league isotonic shrunk toward
the global isotonic with w = n/(n+200).
**Primary: vector-scaling vs isotonic paired dLL on the baseline arm's raw
probs (consistency check replicated on the E1/E4 winners).**  All variants
are reported transparently regardless of outcome.

### E6 — deeper-season backfill for the worst leagues (OPTIONAL — deferred)
Decision made in advance: E6 runs only if E1-E5 leave the standalone model
>= 0.010 behind the market AND the backfill can be staged inside
`ai/data/exp/**` without touching shared data paths.  Backfilling
`ai/data/*.csv` in place is a guarded-path violation for this subagent, and
football-data archives would have to be merged through an experiment-only
cleaner, which changes the corpus the baseline is measured on (breaking
paired comparability with 1.01581).  Not run unless the first four
experiments demand it.

## Honest expectation (stated before running)

E1: -0.003 to -0.010 standalone LL.  E2: -0.001 to -0.004 blended.  E3:
within +/- 0.003.  E4: the largest upside because the current market slots
are junk - realistic -0.005 to -0.020 standalone if the repair restores the
intended feature; the appended book columns add less because of coverage.
E5: -0.001 to -0.005.  Stacked best-case: standalone 1.0158 -> ~0.995-1.005
(market 0.9979 reachable but not promised), blended 0.9952 -> ~0.990-0.993.
The <= 0.990 blended target is plausible but NOT promised.

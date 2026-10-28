# Stage 2 diagnosis — why the Monte Carlo lab never improved the 1X2

> Date: 2026-10-28 · Author: sim/attn subagent · Every number below comes from a
> CI run log, a committed report (`ai/sim/reports/`), or a local run; run IDs
> are cited so any claim can be re-verified. Evidence base: audit.md (Stage 1),
> realism v0 (run 40640342798), compare v0 (run 40642853705), distillation
> (run 40242465027), scale runs 40241860854 (522 M) / 40368849776 (8.97 B).

---

## 0. The question, and why it could not be answered before this stage

Three separate sim-derived attempts to move the production 1X2 failed at the
gate or failed to move at all:

| attempt | evidence | outcome |
|---|---|---|
| distill the sim teacher into the LightGBM | run 40242465027 | **gate REJECTED** +0.00774 LL |
| scale the Monte Carlo 16× (522 M → 8.97 B outcomes) | runs 40241860854 / 40368849776 | teacher 1.06151 → 1.06326 — **no gain** |
| attention student on top of the pipeline | runs 40544347161, 40636199336 | attn raw 1.29781 / cal 1.05830 — far behind `single` 1.01129 |

But until this stage there was **no apples-to-apples measurement** of the lab
against production (different row populations, a non-proper binary log loss in
`evaluate.py`, no sim-vs-real realism test at all — audit §2.4/§2.5/W7). Any
story about *why* was speculation. Stages 1–5 of this mission produced the two
instruments that make the diagnosis evidence-based:

1. **compare v0** (`ai/sim/reports/compare_v0.{json,md}`, run 40642853705) —
   one frame, one eval split (second half of the 180 d holdout, split
   2026-04-21, 2,250 rows), proper 1X2 log loss for every system, paired
   bootstrap CIs. **Fidelity check passed**: the production-protocol blend
   reproduces all three stored v7 numbers to the printed digit (eval 1.01129,
   market 0.99796, blend 0.99491, delta −0.00000) — so whatever this report
   says about the lab is measured against the real deployed model, not an
   approximation of it.
2. **realism v0** (`ai/sim/reports/realism_v0.{json,md}`, run 40640342798) —
   mandatory sim-vs-real suite: 4 chronological windows, production row
   filters, model trained only on rows before each window, acceptance rules
   R1/R2/R4 derived in the docstring and never relaxed. **Overall FAIL, 0/4
   windows.**

---

## 1. The lab's deficit is large, and it is not sampling noise

`compare_v0`, identical 2,250 eval rows for every system:

| system | 1X2 log loss | note |
|---|---|---|
| closing market (de-vigged) | **0.99796** | best system, 1,282 odds rows |
| deployed LightGBM v7 | **1.01129** | raw probs, reproduces stored gate |
| **lab ensemble (80 worlds)** | **1.07091** | known-teams-only 1.07161 |
| uniform | 1.09861 | ln 3 |

- **H1 (lab is simply a weaker predictor): CONFIRMED.** Paired difference
  lab − lgbm = **+0.05962**, CI90 [+0.04820, +0.07151], P(lab better) = 0.0.
  The gap is ~50× the production gate tolerance (0.0005) and its CI excludes
  zero by a wide margin. This is not a tie broken by noise; it is a large,
  consistent deficit on identical rows.
- The lab does beat uniform (1.0709 < 1.0986) and passes the league-prior bar
  (R3 in every realism window) — it carries *some* signal, exactly as the
  publishable gate has always claimed. The question this stage answers is
  whether that signal is **incremental** to production. It is not (§2).
- Unknown-team padding costs nothing: known-only LL 1.07161 vs all-rows
  1.07091 (3.6 % unknown rows) — the padding rule is not the cause.

## 2. The lab's signal is a weaker shadow of what production already has

**H7 (lab carries no signal beyond the production feature matrix): CONFIRMED.**

- The honest stacking test (alpha fit on the first eval half, scored on the
  second, damped toward the prior **1.0** so the lab must *earn* weight):
  the fit half assigned the lab **alpha = 1.000 → exactly zero weight**;
  blend LL = lgbm LL = 1.00608 on the scoring half, difference 0.00000 with a
  degenerate CI. Stacking the 80-world ensemble on the deployed model gains
  nothing, on any row.
- Blending the lab with the market is also a **net negative**: honest
  lab+market 1.00119 vs market alone 0.99614 on the same 616 rows —
  difference +0.00505, CI90 [+0.00063, +0.00980] (P(lab blend better)=0.033).
  The lab drags even the market down.
- Correlation structure says *why*: corr(lgbm, lab) = 0.58 on home prob,
  **0.62 on the outcome margin**, 0.64 on per-row log loss. The lab is highly
  correlated with production but strictly worse calibrated per row — its
  information is a lower-resolution version of what the 161-feature matrix
  already encodes (audit W4: the features literally carry per-row DC score
  matrices, λ_h/a, p_over from att/def Elo). There is no orthogonal axis for
  the ensemble to contribute along.

**Conclusion H1+H7:** the DC ensemble is not a complementary model that needed
more sims or a distillation bridge — it is a *dominated* model. Its every
output direction is already produced at higher accuracy by the deployed
features. No blending or stacking protocol can extract value from a dominated
forecaster on the same information.

## 3. The distillation failure is the same fact, seen from the student's side

Run 40242465027, verified log lines: `SYNTH_LABELS=1 SYNTH_FRACTION=0.5`
duplicated 163,715 teacher-labeled rows into training (327,430 → 491,145) and
the result was `raw_ll=1.06351 cal_ll=1.01903` vs baseline 1.01129 →
**GATE REJECTED (+0.00774)**, α collapsed 0.35 → 0.05 (damped 0.1815), blend
degraded 0.99491 → 0.99828.

Given §1–§2 this outcome is *expected, not unlucky*:

1. The teacher (1.06151 on its own holdout) is **~0.05 LL weaker than the
   student's raw output** (1.01129). Distillation can only help when the
   teacher is better or carries complementary signal; here it is worse on
   both axes.
2. `distill.py` samples **one scoreline per row** — a one-hot draw from a
   ~1.06-LL distribution. One-hot sampling from an imperfect distribution is
   label-noise injection: the student is pulled toward the teacher's *errors*
   with full confidence, over 33 % of the training mass.
3. The isotonic calibrator absorbed part of the damage (1.06351 → 1.01903) and
   the market-α collapse shows the system correctly detecting the student had
   drifted toward the lab's information regime — the gate did its job.

**Verdict: distillation of the current sim teacher is refuted as a path, and
the mechanism is understood.** (Soft-target distillation — copying the
teacher's full distribution instead of a one-hot draw — would remove the
noise-injection mechanism, but §2 shows there is nothing in the teacher's
*distribution* worth imitating either. Not worth a run at the current gap.)

## 4. Scale was never the bottleneck — measured, not assumed

The 16× scale-up (run 40241860854: 522 M outcomes, ll 1.06151 → run
40368849776: 8.97 B outcomes, ll 1.06326) moved the teacher's real-holdout
1X2 LL by **+0.00175 — the wrong way, and far inside the ±0.002 noise band**
of a 5,123-row holdout. Meanwhile the benchmark (5.7 M outcomes/s/shard × 20
shards ≈ 114 M/s) says 10 B outcomes is one ~10-minute wave — the scale target
is already trivially reachable and *already demonstrated to not help*.

MC volume reduces only **sampling error**. What the lab suffers is
**model-form + parameter error** (§5): a misspecified, undecayed, wrong-
population goal process. More draws from a biased distribution converge
precisely to the biased answer — the 8.97 B run is the empirical proof.

## 5. Why the teacher is weak: the realism baseline names the mechanisms

`realism_v0` (run 40640342798): **Overall FAIL, 0/4 windows** on rules
R1 (headline |z|≤3), R2 (W1 within split-half noise floor), R4 (group |z|≤4
for n≥100). Systematic, same-signed biases across all four windows:

| bias | direction | worst evidence (W0, n=4,499) |
|---|---|---|
| **away goals underpredicted** | sim too pessimistic on away attack | obs 1.317 vs pred 1.145, **z +10.73** |
| **total goals underpredicted** | sim underdispersed in the mean | obs 2.927 vs pred 2.670, **z +10.47** |
| **home advantage too high** | favourite-gap: low-quality home favourites win less than simulated | home obs 0.341 vs pred 0.437, **z −7.5**; away obs 0.392 vs pred 0.290, **z +8.7** |
| **draws / 0-0 overpredicted** | too much low-scoring mass | score_0_0 obs 0.055 vs pred 0.075, z −5.05 |
| **over-2.5 / over-3.5 underpredicted** | thin scoring tail | over25 z +8.20, over35 z +8.51, high_scoring z +7.80 |
| league heterogeneity | eredivisie home strongest, z −3.7 | single global home-advantage term |

All four windows pass R3 (beats the league prior) — the ensemble is a real
if weak predictor — but no window passes R1+R2+R4. Two of the three causes
are structural and fixable in `model.py` without new data:

- **no time decay** (audit W2): strengths fit over 1993–2026 with equal
  weight; production measured a 4-year half-life strictly best
  (1.00999 < 1.01275 with none). A 33-year average of club strengths is
  stale by construction — this alone blurs every matchup and flattens the
  simulated tail (over25/over35/high_scoring deficits) and the favourite
  gap.
- **wrong population** (audit W1): `run.py`'s own load keeps internationals
  (neutral venues, home advantage mis-specified) and quality<40 rows —
  372,276/5,123 train/holdout vs production's 331,949/4,499. The home-
  advantage bias points the right way for exactly this artefact.
- the remaining gap (0-0 mass, eredivisie-specific home strength) points at
  the global home-advantage term and the fixed DC rho/xi — Stage 6 material,
  tested only *after* the first two fixes move the needle.

**Chain of the diagnosis:** stale + wrong-population fit → biased goal process
(measurable, z≈+10 on means/tails) → ensemble probabilities that are a
dominated shadow of the production features (H1+H7) → distillation injects
that bias as label noise (gate rejects) → scaling only sharpens the bias
(8.97 B flat). Every failed experiment is the same root cause viewed from a
different angle.

## 6. Hypothesis ledger

| # | hypothesis | verdict | decisive evidence |
|---|---|---|---|
| H1 | the DC ensemble is simply a weaker predictor than the deployed LightGBM | **CONFIRMED** | paired lab−lgbm +0.05962 [+0.04820, +0.07151], compare_v0 |
| H2 | the earlier failures were measurement artefacts (non-proper loss, row mismatch) | **REFUTED** | compare_v0 reproduces stored v7 to the digit (delta −0.00000) under a proper score; the lab still trails by 0.06 |
| H3 | distillation could transfer sim knowledge into the student | **REFUTED** | gate REJECTED +0.00774 (run 40242465027); teacher dominated on both axes (§3) |
| H4 | more simulation volume would improve the teacher | **REFUTED** | 522 M → 8.97 B outcomes: 1.06151 → 1.06326, no gain (§4); 10 B ≈ one 10-min wave |
| H5 | the lab's weakness is dominated by fixable realism defects (time decay, population) | **OPEN — Stage 3 tests this** | realism_v0 names both, same-signed z≈+10 biases (§5) |
| H6 | the lab ensemble could beat the closing market | **REFUTED at current state** | 1.07091 vs 0.99796; even blended honestly it loses to the market alone (+0.00505 [+0.00063, +0.00980]) |
| H7 | the lab's information is already in the production feature matrix | **CONFIRMED** | stacking fit assigns alpha 1.000 (zero lab weight); corr(margin) 0.62; blend difference 0.00000 |

## 7. What follows (Stage 3 plan, falsifiable)

If H5 is right, fixing the two structural defects must move realism_v1's
window pass-rate and the lab's paired gap vs lgbm — measured on the *same*
instruments, with the same rules:

1. **Time-decayed `fit_strengths`** — row weights by match age, with the
   mu/home_adv/xi terms refit under the same decay (a decayed att/def with an
   undecayed home_adv would be internally inconsistent). Half-life: use the
   production-measured 4 years as the default, no tuning against the eval
   windows.
2. **Production-aligned row population** in the lab's load path — club
   matches only, quality ≥ 40, date ≥ 1993-08-01, exactly
   `retrain._load_clean_data`'s filters (audit W1).
3. Re-dispatch **realism v1** with the same tag flow vs the committed v0
   baseline; success criterion: strictly more passing windows and no new
   R1/R4 violation — not a cherry-picked metric.
4. Re-run **compare v1**; the honest bar for "the lab improved" is the paired
   CI of lab−lgbm shrinking from [+0.048, +0.072] toward zero. Even a
   successful Stage 3 does **not** re-open H3 (distillation) or H4 (scale):
   a better-but-still-dominated teacher must still not be imitated or
   mass-produced. The lab's value, if any, is as a *realism-validated
   simulator* (scenario analysis, scoreline distributions, injury shocks) —
   production 1X2 stays with the LightGBM + market blend.
5. Production gate invariants untouched: tolerance 0.0005, no FORCE_REPLACE,
   synthetic mass never trains the raw LightGBM.

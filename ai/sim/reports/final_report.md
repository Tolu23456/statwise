# StatWise simulation programme — final report (Stages 1–8)

> schema `statwise.sim.final.v1` · written 2026-10-28 · branch `main`
> scope: `ai/sim/**`, `ai/experimental/test_*.py`, `ai/attn/**`, workflows
> `simulate.yml` / `train-attention-student.yml` / `train-attention-experiment.yml`
>
> One-line summary: **the lab's weakness was diagnosed, its realism defects
> were fixed with measured gains at every step, and the honest bottom line
> remains that the DC/Poisson Monte-Carlo teacher is still ~+0.047 log loss
> behind the deployed LightGBM and behind the closing market — so nothing in
> this programme justifies touching the production model, and it doesn't.**

---

## 0. Invariants held throughout

| invariant | status |
|---|---|
| production gate untouched (tolerance 0.0005, `FORCE_REPLACE` never) | ✅ `ai/retrain.py` never edited |
| no production replacement without an apples-to-apples holdout win | ✅ no replacement made; the lab *lost* the apples-to-apples (see §5) |
| realism rules R1/R2/R4 (R3 reported) derived, never relaxed | ✅ derived in `realism.py` docstring, unchanged across v0–v3 |
| 0 windows = FAIL, `--strict` CI gate | ✅ every realism run (v0–v3) exited red — by design |
| synthetic mass never trains LightGBM raw | ✅ lab weights only reach LightGBM through stacking, scored honestly |
| ownership: only `ai/**` + 3 workflows edited | ✅ `IMPROVEMENT_PLAN.md`, `statwise/**`, root docs untouched |
| git discipline: explicit paths, rebase before push, no force-push | ✅ every commit below |
| heavy runs only in GitHub Actions; local runs tiny | ✅ local runs = unit suites + 2–3 min sensitivity study |
| no fabricated metrics | ✅ every number below traces to a committed report or run ID |

---

## 1. Stage 1 — Audit (`ai/sim/reports/audit.md`, commit `4f8f33a`)

- Fixed `_log_loss` properness bug in the sim scoring path; schema v3 for sim reports; `test_sim.py` suite.
- Found structural issues W1 (population mismatch: lab fit on 372,276/5,123 rows vs production's 331,949/4,499 — internationals at neutral venues distort home advantage) and W2 (no time decay — flat history since 1993).
- **Output**: a scoring path that cannot silently reward miscalibrated probabilities.

## 2. Stage 2 — Diagnose (`ai/sim/reports/diagnosis.md`, commits `82f1d33`, `1bb76fe`)

Faithful apples-to-apples comparison (`compare_production.py`, run **40642853705**) reproduces the stored v7 gate to the digit (eval 1.01129, Δ −0.00000; market 0.99796; blend 0.99491), so the measurement itself is trustworthy.

Hypothesis ledger as established then (full table in `diagnosis.md` §6):

| # | hypothesis | verdict | evidence |
|---|---|---|---|
| H1 | lab is simply a weaker predictor | **CONFIRMED** | lab − lgbm +0.05962 [+0.04820, +0.07151] |
| H2 | earlier failures were measurement artefacts | **REFUTED** | stored v7 reproduced to −0.00000 under a proper score |
| H3 | distillation could transfer sim knowledge | **REFUTED** | student gate REJECTED +0.00774 |
| H4 | more simulation volume would fix it | **REFUTED** | 522 M → 8.97 B outcomes: 1.06151 → 1.06326 |
| H5 | weakness dominated by fixable realism defects | **OPEN → tested at Stages 3/6/7** | realism_v0: same-signed z ≈ +10 biases |
| H6 | lab could beat the closing market | **REFUTED** | 1.07091 vs 0.99796; honest blend also worse |
| H7 | lab carries no signal beyond production features | **CONFIRMED (v0)** | stacking alpha 1.000, blend diff 0.00000 |

## 3. Stage 3 — Improve realism (commit `dd626c8`)

Two structural fixes, both from the audit, applied consistently:

1. **Time decay** — exponential recency weights `0.5**(age/1460d)` threaded through *every* likelihood term (mu, home_adv, league offsets, att/def, xi grid search) **and** the cap-down objective/gradient (`MatchData.w`, `w_sum()`, subsample mass rescale) so one clock governs the whole goal process. Default 1460 d = production's measured best, never tuned on an eval window; `half_life_days=None` reproduces legacy bit-for-bit (tested).
2. **Production-aligned population** — `run.load_frame` applies club-only / `quality>=40` / `>=1993-08-01` filters, mirroring `retrain._load_clean_data` (audit W1).

`test_decay.py` (32 checks): weight arithmetic, collapse-regime refit monotonicity, deviance identity, subsample mass conservation, finite-difference gradient agreement **4.29e-08**, fixture-frame shard run, legacy-file survival. All five suites green.

**Measurement** (realism v1, run **40649741733**, still strict FAIL 0/4 — honest): every fresh window improved vs v0; W1 shape distance 0.08266 → 0.0543 (below its noise floor → R2 flipped True); W3 moments went z ≈ +3 to z ≈ 0 (R1+R2 True, first time).

## 4. Stage 4 — Validate (realism suite, runs v0→v3)

The suite is the programme's compass: window-fits train only on rows before the window start; comparison is against real held-out outcomes; rules derived, never touched.

Remaining defect after Stage 3: **favourite-gap terciles fail R4 at |z| 7–9, same pattern both directions** = simulated outcomes systematically less extreme than real ones.

## 5. Stage 6 — Optimise (`l2_sensitivity.py`, commit `51f8d4b`)

Pre-registered, leak-proof design: validation slice = 180 d before the earliest eval window start (2024-05-01…2024-10-28, disjoint from every eval window); decision rule written *before* results (win on both overall LL and favourite-gap worst |z|, with monotone spread growth as mechanism check).

Result: mechanism confirmed (spread 0.0112 → 0.0215 monotone), LL 1.05925 → 1.05181, favgap |z| 10.05 → 8.98 → **RECOMMEND l2=0.0**, explicitly gated on full-suite verification.

**Verification on the real suite** (realism v2, run **40653986545**, vs committed v1): predicted effect reproduced — LL −0.0067…−0.0077 in all three fresh windows, RPS improved everywhere, worst-group |z| improved (10.24→9.09, 7.86→7.66, 9.17→7.70), **no verdict regressed**. Recommendation adopted: `l2=0.0` is now the CLI default (commit `d84a728`).

Honest caveat recorded there and still true: the favourite-gap defect *persisted* at |z| ≈ 7.7 — shrinkage was a contributor, not the cause. The residual is **model form**: the static Poisson generator has no match-state, red-card or half-time structure (red cards occur in 9.7% of matches).

## 6. Stage 7 — Scale + closed loop (runs **40655818340**, **40657654315**, **40657758407**, commit `4297a81`)

Regenerated `ai/data/sim/worlds.json` with the adopted configuration (production population + 4-year decay + l2=0.0 — the shard job passes no override, so CLI defaults apply). Publishable gate passed (1.05634 < prior 1.07542 on the real holdout); CI committed the recipe as `bcb9a28`.

**Realism v3 (vs v2):**

| window | ΔLL | ΔW1 | ΔW1 gd | verdict |
|---|---|---|---|---|
| W0 (new recipe) | **−0.01136** | −0.0358 | −0.05439 | worst group \|z\| 8.69 → 7.56 |
| W1–W3 (fresh fits, config unchanged) | 0.0 | 0.0 | 0.0 | identical config → identical result (expected) |

W0's improvement is the single largest one-window gain of the programme. Suite still strict **FAIL 0/4** — R4 (favourite-gap) fails in all four windows; W3 passes R1+R2, W1 keeps R2.

**Compare v1 (lab vs deployed, same 2,250 eval rows, stored-v7 reproduction still faithful):**

| system | v0 | v1 | Δ |
|---|---|---|---|
| lab ensemble | 1.07091 | **1.05800** | −0.01291 |
| deployed LightGBM | 1.01129 | 1.01129 | (reproduced exactly) |
| H1 gap (lab − lgbm) | +0.05962 [+0.048, +0.072] | **+0.04671 [+0.036, +0.057]** | narrowed |
| lab+market honest blend vs market alone | +0.00505 [+0.00063, +0.00980] | +0.00366 [−0.00066, +0.00804] | still worse, now not significant at 90% |

**Updated verdicts (superseding the v0 ledger where noted):**

- **H1: CONFIRMED** (narrowed, not closed) — the lab is still significantly weaker than the deployed model; no deployment claim is made.
- **H5: PARTIALLY CONFIRMED** — every fixable realism defect we could name was fixed and *each one measurably helped* (v1, v2, v3 all negative ΔLL), but they explain only a third of the gap; the remainder is model form (DC/Poisson vs gradient-boosted features) plus the market/feature advantage H7 describes.
- **H6: REFUTED** — lab 1.058 vs market 0.998; honest blend still worse than market alone.
- **H7: INCONCLUSIVE at v1** (was CONFIRMED at v0) — stacking gap +0.00009 [−0.00042, +0.00055] with alpha 0.974; the CI touching zero means neither "lab adds nothing" nor "lab adds something" is earned at this sample size. Reported as-is rather than carrying the stronger old claim.

## 7. Stage 8 — Deploy safely: **not taken, by design**

There is no apples-to-apples holdout win for the lab; the deployed model stands unchanged (v7, eval 1.01129, gate code untouched). The simulator's deliverable is instead a **validated, gated pipeline**: every change to it must pass the same strict realism suite, on the same pre-derived rules, before it can inform anything downstream.

Also verified in passing: attention-student run 40636199336 completed **success** (separate track, no action required).

---

## 8. What remains open (honest backlog)

1. **Favourite-gap R4 failure, |z| ≈ 7.6–9.1 in all four windows** — the one defect that survived every fix. Evidence points at generator form, not parameters: real outcomes are more margin-extreme than the static Poisson generator produces. Candidate work (NOT done, would need its own pre-registered design): match-state/red-card term, half-time coupling, or a heavier-tailed scoreline model. The rule stays: fix, then let v4 measure.
2. **H7 at ~1,125 stacking rows is under-powered** — a bigger stacked sample or repeated splits would decide it.
3. **Lab vs market gap (+0.047 … +0.059)** — no plausible parameter-level lever found; the market and the production feature matrix are simply ahead of a league-average Poisson ensemble.
4. **Attention track** — student gate outcome owned by that workflow; not part of this report's claims.

## 9. Artefact index

| evidence | file / run |
|---|---|
| audit | `ai/sim/reports/audit.md` |
| diagnosis + hypothesis ledger | `ai/sim/reports/diagnosis.md` |
| realism baselines v0–v3 | `ai/sim/reports/realism_v{0..3}.{json,md}` |
| compare v0 → v1 | `ai/sim/reports/compare_v{0,1}.{json,md}` |
| L2 sensitivity (pre-registered) | `ai/sim/l2_sensitivity.py` |
| decay/population tests | `ai/experimental/test_decay.py` |
| suites | `test_sim`, `test_sim_scale`, `test_realism`, `test_compare`, `test_decay` — all green |
| CI runs | compare 40642853705 · realism v1 40649741733 · v2 40653986545 · lab+recipe 40655818340 (→ `bcb9a28`) · realism v3 40657654315 · compare v1 40657758407 |
| key commits | `4f8f33a` `82f1d33` `1bb76fe` `dd626c8` `f184119` `51f8d4b` `49f570b` `d84a728` `4297a81` |

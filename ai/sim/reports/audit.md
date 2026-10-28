# StatWise — Architecture Audit (Stage 1)

> Date: 2026-10-28 · Author: sim/attn subagent · Verified against **code and CI
> artifacts**, not documentation. Every claim below carries its evidence.

---

## 1. Production prediction pipeline

### 1.1 Current model (verified in `ai/data/model_info.json` + run 40544347161)

- **v7**, LightGBM "single" mode: 2 outcome heads (3-class 1X2 + binary O/U 2.5)
  plus 2 auxiliary binary heads (BTTS, O/U 3.5); `joint` mode (6-class) races
  `single` on the same train slice and loses (v3–v7 all shipped `single`).
- **161 features** (160 continuous + 1 categorical league id), built by
  `ai/model/features.py` + C++ kernel `ai/core/features.cpp` (`libstatwise.so`).
- Training rows **331,949** from 377,399 raw clean rows: filters are
  dropna(teams/scores), date ≥ 1993-08-01, **drop internationals (45,450 rows)**,
  quality_score ≥ 40 (drops nothing extra — verified by local count).
  *(Note: `MODEL_REPORT.md` says "377,472 clean rows"; the actual on-disk total
  is 377,399 today — the fetch cadence moves it.)*
- Holdout: last 180 days, split_at **2026-04-21**; earlier holdout half fits
  per-class isotonic + market α; later half is THE eval set (**2,250 rows**,
  1,282 with odds). Eval LL **1.01129**, acc 0.4827, brier 0.20191, ECE 0.0275.
- **Gate** (`ai/retrain.py:733-826`): candidate replaces production only if
  eval LL is not worse than the deployed baseline by > `MAX_LL_REGRESSION`
  (0.0005) on an apples-to-apples split (same HOLDOUT_DAYS, split drift ≤ 7 d);
  otherwise rejected and the deployed model stays. `FORCE_REPLACE` is read
  (line 746) but the documented policy is never to set it. Verified in
  `model_info.json.baseline_comparison` (v6→v7: delta 0.0, accepted).
- Market blend: per-league α between model and de-vigged **closing consensus**,
  damped toward prior 0.35 → 0.99491 blended LL on odds rows.
- Anti-bias post-rules (draw floor, away boost, confidence cap) apply at
  serving time; they are **not** part of the gate's LL metric.

### 1.2 Leakage audit (question 11 & 12 of the brief)

| Check | Result | Evidence |
|---|---|---|
| Duplicate fixtures crossing the split | **None** — 0 dup `(date, home, away)` groups, 0 conflicting scores, 0 straddling the 180 d split | local pandas check over all 73 clean files |
| Per-row Elo/ratings | **Chronological** — `pre_elo_*` snapshots are recorded *before* each match's update | `features.py:144-163` |
| League-level aggregates (avg goals, home-win rate, draw rate) | **Small documented leak** — fit on the FULL frame incl. holdout (<1% of signal) | `retrain.py:291-293`, `features.py:173-182` |
| Rolling form/H2H/travel blocks | prior matches only; self-leak guard tested | `ai/experimental/test_leak_fixes.py` |
| Odds timing | **Evaluation-time mismatch risk**: gate/α are fitted and scored on *closing* odds, but production predictions are made ~4 h pre-kickoff against odds available then (odds_sync every 2 h). The eval-blend ablations showed closing is the best *source*, but a closing-odds eval is optimistic relative to a pre-kickoff information state. | `retrain.py:385-405`, `MODEL_REPORT.md` §2.4 |
| Distillation leakage guards | teacher split must be ≤ retrain split; verified tests exist | `sim/distill.py:68-91`, run 40242465027 logs `[PASS] teacher rejected when it saw holdout outcomes` |
| Calibrator/α fitting | first half of holdout only; evaluated on second half | `retrain.py:397, 431-457` |

**No evidence of train/eval contamination through duplicates or feature
normalisation was found.** The two known, documented exceptions are the
league-aggregate leak and the closing-vs-predict-time odds mismatch.

### 1.3 Prediction heads (question 4)

- `single`: `_outcome_pipe` (3-class LGBM) + `_goals_pipe` (binary O/U 2.5),
  plus `_btts_pipe` and `_o35_pipe` auxiliaries; `joint`: one 6-class LGBM
  whose 1X2/O-U are marginals. `trainer.py:383-440` — verified.
- There is **no exact-score head**: exact-score probs are only available from
  a scoreline grid (features carry a DC score matrix, `features.cpp:265`).

---

## 2. Monte Carlo Match Lab (`ai/sim/**`)

### 2.1 Algorithm (verified in source)

1. `run.py` loads committed season files with **its own, looser filters**
   (dropna only — *keeps internationals and quality < 40 rows*: 372,276 train /
   5,123 holdout vs production's 331,949 / 4,499).
2. Fits a Dixon-Coles goal process (`model.py`): penalised MLE (L2 = 0.02) by
   gradient descent, 240 epochs, **no time decay** (1993–2026 weighted
   equally), global home advantage, league offsets, xi grid-searched
   (typical fitted xi −0.06, home_adv 1.334 — from shard-0 log).
3. `cap_down` (sampler.py): symmetric-mixture MH proposal (forward + noise,
   backward + noise) → a *spread* of plausible strength vectors; breeding =
   crossover + mutation with elitist acceptance.
4. `simulate.py`: exact posterior-predictive grids per fixture (`posterior_markets`
   — averages over worlds, no MC error) and a mass-simulation path
   (Poisson draws + DC tau by rejection, folded into mergeable sufficient
   statistics via `bincount`). 7× fast path: stacked world gathers, LUT tau,
   sub-chunked expansion.
5. `merge.py`: sums counters, stores `worlds.json` recipe (3 MB, 80 worlds,
   never rows), re-scores the combined ensemble on the real holdout, gates on
   `publishable`.

### 2.2 Measured performance (run 40368849776 logs — real benchmark)

- Fit on 372k rows: **3 s**; cap-down chain ~11 s/world (acceptance 0.17–0.34,
  mean 0.25).
- Mass simulation: **446.8 M outcomes in 78 s = 5.7 M outcomes/s per shard**,
  peak RSS 552 MB; full wave = **8,965,779,106 outcomes across 20 shards**
  (one ~2-min wave), merge re-eval 2.4 s.
- → **~114 M outcomes/s aggregate on GitHub runners; 10 B ≈ one wave at
  current inputs (~10 min wall).** The scale target is *not* the bottleneck;
  realism and information content are.

### 2.3 What the lab's gates actually test

- `evaluate.py` scores the ensemble against uniform / league-prior / market
  baselines on the **real** holdout (never synthetic) — good design.
- But the gate is *much weaker* than the production gate: it only requires
  beating a **league constant**, not the deployed model. The ensemble's 1X2 LL
  (**1.06326**, n=5,019) was never compared apples-to-apples with
  LightGBM's 1.01129 (n=2,250, different row population).

### 2.4 BUG (confirmed): binary-market log losses in `ai/sim/evaluate.py` are not log losses

`_log_loss(p, y)` (line 58) **ignores `y` entirely**:
`-mean(log(clip(p)))`. 1X2 is saved only because callers pre-index
`p1[arange, y]`; every binary market passes the raw probability, so

- `btts/over25/over35 log_loss` = `-mean(log p_positive)` — the score of a
  hypothetical all-"over" world, not a proper score;
- proof: `baselines.uniform.over35_log_loss = 1.10461 > ln 2` — a proper
  binary log loss can never exceed ln 2 (observed on the actual report);
- recomputation from the clean files: holdout rates btts 0.5755 / o25 0.5579 /
  o35 0.3361 → proper constant-at-rate entropies 0.6817 / 0.6864 / 0.6384,
  while the report shows 0.5772 / 0.5915 / 1.10461 = `-ln(rate)` exactly.

Consequence: the `publishable` gate's goal-market legs (`beats.league_prior_btts,
league_prior_over25/over35`, mean) have been decided on a **non-proper score**;
model and prior are both scored `-mean(log p)`, which rewards/penalises
confidence in the *positive* class only. The 1X2 leg and market baseline are
correct. This must be fixed before any further gate decisions (done in a
follow-up commit + tests).

### 2.5 Other observed weaknesses of the simulator

| # | Weakness | Evidence |
|---|---|---|
| W1 | **Population mismatch with production**: keeps internationals (neutral venues → home advantage mis-specified) and quality < 40 rows; no date floor | `run.py:51-64` vs `retrain.py:74-129`; 5,123 vs 4,499 holdout rows |
| W2 | **No time decay**: strengths fit over 1993–2026 equally; production measured 4-year half-life strictly best (1.00999 < 1.01275 none) | `model.py:fit_strengths`, `retrain.py:348-351` |
| W3 | **Ensemble diversity collapses**: breeding is elitist (`sorted[:population]` every generation); reported `mean_home_std` only **0.0187** — the "80 worlds" carry little parameter uncertainty beyond a point fit | `sampler.py:244-267`, `report.json` |
| W4 | **Overlap with production features**: the feature matrix already carries per-row DC score matrices, λ_h/a, p_over*, p_btts from att/def Elo (`rho = −0.13` fixed). The lab's teacher ≈ a cleaner version of information LightGBM already sees | `features.cpp:109-150, 265` |
| W5 | No red-card/lineup/event-time modelling; half-time scores exist for a subset of rows but are unused | clean schema (halftime cols present), `ai/sim/**` never reads them |
| W6 | `fit_strengths` L2=0.02 shrinkage is global — no explicit small-sample shrinkage for recently promoted teams (they inherit prior-uniform att/def = 0 = league average, which is actually a reasonable default, but not uncertainty-aware) | `model.py:194` |
| W7 | Realism validation vs held-out real matches **does not exist** — the report's scoreline distribution is *simulated* mass, and calibration tables are self-consistency (sim probs vs sim outcomes), never sim-vs-real | `merge.py:272-296` |

---

## 3. Prior simulation experiments (Stage 2 primary evidence)

### 3.1 522 M run (workflow 40241860854, 2026-10-25) — verified from run logs

`combined ensemble (80 worlds) on real holdout n=5019: 1x2 ll=1.06151 |
btts ll=0.62146 | o25 ll=0.69923 | publishable=True`
(the btts/o25 numbers are the broken metric of §2.4; 1X2 is valid).

### 3.2 8.97 B run (workflow 40368849776, 2026-10-26) — verified from run logs + `report.json`

`1x2 ll=1.06326 | publishable=True` — **16× more simulated outcomes and the
teacher's real-holdout 1X2 LL did not improve (1.06151 → 1.06326)**. Direct
evidence that MC volume reduces only sampling error; model-form/parameter
error dominates. (`MODEL_REPORT.md` §6.1 still quotes the 522 M numbers.)

### 3.3 Distillation attempt (retrain run 40242465027, verified log lines)

- `SYNTH_LABELS=1 SYNTH_FRACTION=0.5` → `+163,715 teacher-labeled duplicates
  (327,430 → 491,145 rows, 80 worlds, teacher split 2026-04-21)`
- `[OOT:single] raw_ll=1.06351 cal_ll=1.01903 use_cal=True` vs baseline raw
  **1.01129** → `[GATE] WORSE: 1.01129 -> 1.01903 (+0.00774 > 0.0005)` →
  **REJECTED**, deployed model kept; α fit collapsed 0.35 → 0.05 (damped
  0.1815) and blended LL degraded 0.99491 → 0.99828.
- Mechanism supported by evidence: `distill.py` **samples one scoreline per
  row** (a one-hot draw from a ~1.06-LL teacher), duplicating 50% of the train
  rows — injecting the noise of a substantially weaker model into 33% of the
  training mass. The teacher (1.063 on its own holdout) is ~0.05 LL weaker
  than LightGBM raw (1.01129), so imitating it can only pull the student away
  from its better optimum. The isotonic calibrator rescued part of the damage
  (1.06351 → 1.01903) but not all.

### 3.4 Attention student (existing state)

- Prior experiment 48.31% / LL 1.03378 — underperformed; run 40503960706 killed
  by runner shutdown (exit 143); run 40544347161 trained OK (single 1.01129,
  joint 1.01118, attn raw 1.29781 / calibrated 1.05830 → `single` selected)
  then went **red on a one-character f-string SyntaxError** in the gate
  summary step, which under `bash -e` skipped the publish steps.
  **Fixed** in commit `b13fd0f` (snippet verified with python3; summary moved
  to its own `if: always()` + `continue-on-error` step *after* publish).
  A validation re-dispatch was started this session (run 40636199336).

---

## 4. Strengths / limitations / top opportunities

**Strengths** — honest real-holdout gating for both production and the lab;
recipe-not-rows storage; mergeable sufficient statistics; deterministic seeds;
a genuinely fast simulation path (5.7 M/s/shard); chronological splits
throughout; a working 0.0005 production gate that has demonstrably rejected
worse candidates twice (distillation, isotonic).

**Limitations** — (1) the lab's goal-market gate metric is broken (§2.4);
(2) no apples-to-apples sim-vs-production evaluation exists yet; (3) no
sim-vs-real *realism* validation exists at all (W7); (4) simulator fits the
wrong population with no time decay (W1/W2); (5) ensemble uncertainty is
near-degenerate (W3); (6) 8.97 B outcomes already ran without any measurable
benefit (§3.2) — scale is solved, information is not.

**Top opportunities, in order**
1. Fix the evaluation bug; make every gate number a proper score.
2. Build the sim-vs-real realism suite on held-out chronological windows
   (mandatory deliverable §4 of the brief) — first against the current recipe
   to establish the baseline.
3. Apples-to-apples 1X2 comparison on *identical* rows: LightGBM vs lab
   ensemble vs market vs blends (tests hypotheses H1/H7 directly).
4. Smallest realism-supported simulator changes: production-aligned row
   population, time-decayed fit, then test alternatives (bivariate/NB/
   league-specific home advantage) by chronological OOS scoreline LL.
5. Only then: simulation-derived features / better market blends — never raw
   synthetic rows (the distillation evidence stands).

# StatWise — probability quality & evaluation-metrics audit

**Question asked:** is the reported ~48 % accuracy (47 / 97 correct) caused by bad
probabilities / bad evaluation metrics, or by something else?
**Date:** 2026-10-25 · **Repo:** `/home/t85491005/statwise` · **HEAD:** `39482cd`

**Constraints honoured:** no training, no long-running jobs, no git writes, no production
code modified. Model inference on ≤ 2,249 sampled rows only, memory freed afterwards
(peak RSS ≈ 200 MB; the box has 2.7 GiB). Everything heavy (retraining, full feature
builds, ablations) is delegated to existing GitHub Actions workflows — see §5.

**Artifacts produced by this audit** (all new files, nothing existing was touched):

| File | What it is |
|---|---|
| `analysis/serve_path_reliability.py` (+ `_v2.py`) | run the *deployed* `.pkl` through the *serving* feature path on a sample; v2 uses retrain's exact eval window |
| `analysis/serve_path_reliability_v2.json`, `serve_path_reliability.json` | accuracy / log loss / ECE / reliability tables per condition |
| `analysis/serve_probs.npz` | raw probability matrices (2,249 rows × conditions) for follow-up analysis without re-running the model |
| `analysis/live_predictions_reliability.py` (+ `.json`) | reliability of the probabilities actually published to Supabase (the 97 scored rows) |
| `analysis/baselines_eval_window.json` | base rates + market baselines on retrain's eval window |

---

## 1. What probabilities actually reach the user (Q1)

### 1.1 The serving trace, end to end

```
predict.yml (every 4 h) → actions/cache restore of ai/models/football_predictor.pkl
  → ai/scheduler.py --once → build_engine()            # loads .pkl, history stays EMPTY
  → PredictionEngine.run()                             # ai/model/predictor.py:134
      predict_match(..., history=self.history)         # predictor.py:183-198
        → feature_pipe.build_features(matches, EMPTY history)   # trainer.py:513
        → _outcome_pipe.predict_proba                 # trainer.py:519  (raw LightGBM)
        → isotonic `self._calibrators`  if not None    # trainer.py:523-529
        → alpha market blend           if alphas+odds  # trainer.py:533-544
        → argmax → `prediction` + `confidence`         # trainer.py:579, 627
```

| Question | Answer | Evidence |
|---|---|---|
| **Is calibration applied?** | **No — the deployed artifact carries `calibrators = None`.** The code path exists and is exercised, but isotonic was fitted and then **rejected out-of-sample**: `raw_ll=1.01159 cal_ll=1.01936 use_cal=False` (joint: `1.01149 → 1.03990`). | `retrain.py:329 use_cal = ll_cal < ll_raw`; CI log of run `40176564382`: `selected mode='single' (eval_ll=1.01159, calibration=off)`; `model_info.json → calibration.calibration_used = false` |
| **Is the market alpha blend applied?** | **Yes.** `alpha_default = 0.35` (deployed) + 8 per-league alphas, blended against the **de-vigged closing consensus** whenever the fixture has three valid prices. Unpriced fixtures keep pure model probabilities. | `trainer.py:535-544`; `model_info.json → alpha_default 0.35, alpha_leagues 8` |
| **What features are used?** | `PredictionEngine.history` is **an empty DataFrame** on every load-from-pickle path (`build_engine`, `load_or_train` early-return, `push_ai_predictions`). So the model is served with **no match history at all** — h2h, form, streaks, momentum, rest-days, `lambda_*`, etc. are all zero/default at inference, although the holdout metrics were computed on rich chronological features. | `predictor.py:106-114` (returns before `self.history` is ever set), `scheduler.py:146-168`, `trainer.py:474-476`, `features.py:282-311` |
| **Is the local `.pkl` stale?** | **Yes — the local workspace is internally inconsistent.** See table below. | — |
| **What does production serve?** | The **CI cache**, which is the same run that wrote `model_info.json` **version 2**. Verified from Actions logs: the latest `StatWise AI Predictions` run restored `statwise-model-v2-40176564382-…` (the 2026-10-25 08:25Z retrain run) and logged `Loaded existing trained model.` → 89 predictions pushed. | Actions run `40222830493` log |

### 1.2 Artifact inventory

| Artifact | mode | eval log loss | accuracy | α | calibration | data through | when |
|---|---|---|---|---|---|---|---|
| **local** `ai/models/football_predictor.pkl` (mtime 10-22 18:28) | joint | **1.00999** | **0.4867** | **0.40** | none | 2026-10-18 | 3 days / 3 runs behind `model_info.json` |
| `model_info.json` **v1** (CI) | joint | 1.00857 (90 d window) | 0.4944 | – | – | 2026-10-18 | replaced |
| `model_info.json` **v2 = deployed** (CI cache) | single | **1.01159** (180 d) | **0.4849** | **0.35** | none | 2026-10-18 | current |
| model_info history 10-24 | joint | 1.01093 | 0.4933 | 0.35 | none | – | replaced |

Consequences:

* The `.pkl` is **gitignored** (`.gitignore:20 ai/models/`) and is published only via
  actions/cache + artifact + Supabase storage; `model_info.json` is the only part that is
  committed. So **`model_info.json` in git is the model's metadata, not its weights** —
  and locally the two disagree.
* A prediction run started **on this box** would serve the stale 10-22 joint/α=0.40 model
  while every metric anyone reads (`model_info.json`) describes the 10-25 single/α=0.35
  model. CI runs are consistent; local runs are not.
* Also note the deployed version is the **worst of the three** recent runs on *both*
  metrics (1.01159 / 0.4849 vs 1.00999 / 0.4867 and 1.01093 / 0.4933) — and it was
  deployed with the gate **skipped** (§4).

---

## 2. Is 48 % actually poor? (Q2)

Window = **exactly retrain.py's eval half**: second half (by row count) of the 180-day
out-of-time holdout, i.e. **2026-08-17 → 2026-10-18, n = 2,249** after the same
`quality_score ≥ 40` / non-international filters (`retrain.py:121-127, 211-215, 282`).

| Baseline | Accuracy | Notes |
|---|---|---|
| Always **Home** | **42.55 %** (n=2,249) | also the majority class |
| Always Draw | 26.46 % | draw rate = 26.5 % |
| Always Away | 30.99 % | |
| **Market favourite** (de-vigged closing, priced rows only) | **50.23 %** (n=1,282, 57 % coverage) | log loss **0.9980**; picks home 67 % / draw 0.1 % / away 33 % |
| Market favourite — same 1,282 rows, always-home | 42.98 % | so the book beats the trivial baseline by +7.3 pp |
| **Model (holdout metric, all 2,249 rows)** | **48.49 %** | log loss **1.0116**, ECE 0.026 |
| Model, joint mode (same run, not selected) | 48.93 % | log loss 1.01149 |
| **Live screen (the user's number)** | **47/97 = 48.45 %** | always-home on the same 97 = **43.3 %**; market favourite on the 31 priced rows = **51.6 %** |

**Gaps**

* **Model vs trivial baselines: +5.1 … +5.9 pp** (48.5 % vs 42.6 % always-home /
  43.3 % live). The model is *not* random and not worse than "always home".
* **Model vs market: −1.7 pp** on the holdout's priced rows (48.5 % over *all* rows vs
  50.2 % over *priced* rows — note the denominators differ, see caveat) and **−3.2 pp**
  live (51.6 % vs 48.5 %, n=31). Log-loss gap: **1.0116 vs 0.9980 = +0.0136 nats**.
  The bookmaker is better than the model, but only slightly, and the market is not
  available on 43 % of the fixtures the model scores.
* **47/97 is statistically meaningless as a quality signal**: 95 % CI is
  ±1.96·√(0.485·0.515/97) ≈ **±10 pp → [38 %, 58 %]**. The same production model could
  be 44 % or 54 % and this screen would not tell them apart. A four-digit accuracy
  screen on ~25 settled rows/week will always look like a crisis.
* Distribution check on those 97 rows: **74 Home / 23 Away / 0 Draw** picks against
  43 % / 32 % / 25 % actual labels — the model over-picks home by ~30 pp (the market
  favourite is home 67 % of the time, and the α-blend pulls the pick toward it).
  8 of the 97 rows carry **no** model probabilities at all (legacy all-Home rows,
  6/8 correct); the 89 rows with probabilities score **41/89 = 46.07 %**.

**Verdict:** 48 % is *not* a poor number in isolation — it is ~6 pp above every trivial
baseline and ~2 pp below the bookmakers. The real defects are (a) the number is gated
nowhere, (b) it is measured on a feature path that is not the one that ships (§3), and
(c) the screen the user sees is far too small to diagnose anything.

---

## 3. Calibration audit (Q3)

### 3.1 Is log loss 1.0116 @ 48.5 % accuracy over- or under-confident?

Convert log loss to the geometric-mean probability the model assigns to the **true**
outcome (`exp(−ll)`):

| | log loss | geometric-mean P(true) | ECE |
|---|---|---|---|
| Uniform (1/3,1/3,1/3) | 1.0986 | 0.3333 | – |
| **Model (holdout)** | **1.0116** | **0.3636** | **0.026** |
| Market (closing, priced) | 0.9980 | 0.3686 | 0.028 |
| Market (single book) | 0.9991 | 0.3682 | – |
| Model + α=0.35 blend | 0.9961 | 0.3693 | – |

* The model is **mildly sharp, not over-confident**: it moves the true-class probability
  from 0.333 (uniform) to 0.364, i.e. it captures ~52 % of the sharpness the market has
  (0.368).
* **ECE = 0.026** on the holdout and the honest isotonic attempt *made log loss worse*
  (1.0194 vs 1.0116 single, 1.0399 vs 1.0115 joint) → the raw model is already
  well-calibrated; per-class isotonic on ~1,125 calibration rows overfits.
* **Conclusion for the holdout numbers: calibration is NOT the problem.** The deficit
  vs the market is **resolution/discrimination** (the model cannot separate winners from
  losers much better than base rates), not miscalibration. Chasing isotonic/Platt on the
  training path will not raise accuracy — it is already rejected by the code that fits it.

### 3.2 But the probabilities that *ship* are poorly calibrated

Serving path (empty history) on the same 2,249 eval rows, and the rows actually pushed
to Supabase:

| Condition | n | accuracy | log loss | **ECE** | mean max-prob | pred Draw |
|---|---|---|---|---|---|---|
| Holdout metric (training feature path) | 2,249 | 0.4849 | 1.0116 | **0.026** | ≈0.485 | – |
| **Serving path, model only** | 2,249 | 0.5451 | 1.0025 | **0.109** | **0.436** | **0.0 %** |
| Serving path, model + α blend | 2,249 | 0.5260 | 1.0187 | 0.097 | 0.429 | 0.0 % |
| Market only (priced rows) | 1,282 | 0.5023 | 0.9980 | **0.028** | 0.499 | 0.1 % |
| **Published rows (live, with probs)** | 89 | **0.4607** | 1.0272 | **0.107** | 0.435 | **0.0 %** |

Reliability table — probabilities actually served (n=2,249, serving path):

| bucket | n | mean p | empirical acc | gap |
|---|---|---|---|---|
| 0.30–0.40 | 562 | 0.377 | 0.384 | +0.008 |
| 0.40–0.50 | 1,461 | 0.438 | 0.559 | **+0.121** |
| 0.50–0.60 | 154 | 0.539 | 0.851 | **+0.311** |
| 0.60–0.70 | 72 | 0.637 | 0.861 | +0.224 |

Reliability table — published rows (n=89): 0.30–0.40 (n=37) 0.377 → 0.405; 0.40–0.50
(n=36) 0.434 → 0.333 (−0.100); 0.50–0.60 (n=12) 0.546 → 0.833 (+0.288);
0.60–0.70 (n=4) 0.656 → 1.000. ECE 0.107, small-n bins.

So the served probabilities are **systematically under-confident in the 0.40–0.60 band**
(what the app displays as "40–55 %") while the model actually lands 56–85 % there —
and the displayed confidence is what drives `is_uncertain` (`max p < 52 %`,
`trainer.py:561`) and the AI-Pick/tier gates. **This is a serving problem, not a
training problem**: the holdout's ECE of 0.026 was measured on features the model never
sees at inference.

### 3.3 Why the serving path differs (the biggest structural finding)

Measured directly by running the deployed `.pkl` twice on the same 1,200 rows, changing
only the history frame passed to `build_features`:

| | rich history | **empty history (= production)** |
|---|---|---|
| feature columns that change (of 161) | **111** | – |
| argmax flips | – | **496 / 1,200 = 41.3 %** |
| mean max-prob | 0.588 | **0.432** |
| pick Home / Draw / Away | 48 % / 19 % / 33 % | **63 % / 0 % / 37 %** |
| accuracy (same rows) | 0.4275 | 0.5451 |
| log loss | 1.1520 | **1.0025** |

Three different feature paths give three different answers on ~the same window:
training path **48.5 %**, reconstructed rich-history serve **42.8 %**, empty-history
serve **54.5 %** — a **~12 pp spread**. None of these is exactly the live process, but
the spread proves the evaluated path and the shipped path are not the same object, so
**the 1.0116 / 48.49 % pair does not describe what users receive.**

*Caveats for §3.3 numbers: (i) the backtest uses the pickle's final Elo ratings, which
contain matches inside the eval window — this inflates absolute accuracy for *all*
conditions equally (so within-table comparisons hold, absolute levels do not); (ii) the
"rich history" condition only has 2024-2026 as context and uses the per-row C++ feature
engine rather than the bulk one, so it approximates — but is not — the training path;
(iii) `pd.Timestamp.now()` (used as `cts` at inference) was shown to have **zero** effect
on features (identical outputs with the clock forced to match date).*

---

## 4. What is actually gated (Q4)

| Stage | Optimised metric | Accuracy used? |
|---|---|---|
| LightGBM objective / early stopping (`trainer.py:103-125, 163-192`) | default multiclass **log loss**, `early_stopping(100)` on a chronological tail slice | no |
| Model selection between `single` and `joint` (`retrain.py:337-348`) | log loss, **ties (±0.0005) broken by *goals* Brier** | **no** |
| Isotonic on/off (`retrain.py:329`) | log loss only | no |
| Market α (`retrain.py:405-451`) | log loss only, grid 0.05 | no |
| **Deployment gate** (`retrain.py:500-561`) | `eval_log_loss`, tolerance **0.0005**, only if `holdout_days` equal **and** `split_at` moved ≤ 7 d | **no** — `delta_accuracy` is *recorded* (line 540) but never consulted |
| `model_info.json` | stores `accuracy` as an inert field | no |

Concrete evidence that this trades accuracy away:

1. **The tie-break picked the less accurate model.** In the run that produced the
   deployed version: `single acc=0.4849 ll=1.01159` vs `joint acc=0.4893 ll=1.01149`.
   Log loss differs by 0.0001 (inside the ±0.0005 tie band) → goals Brier decided
   (`0.24116` vs `0.24170`) → **`single` shipped, costing −0.44 pp top-1** on that very
   holdout.
2. **The gate never ran for the deployed model.** CI log:
   `[GATE] skipped — not apples-to-apples: holdout_days 90 -> 180; split_at moved 90d …
   The new model is deployed unverified`. v1 (1.00857 / 0.4944) was replaced by
   v2 (1.01159 / **0.4849**) with **−0.95 pp accuracy and +0.003 log loss**, unverified.
3. **The α it reports is fitted in-sample.** The just-run CI ablation
   (`Blending Ablation` run `40220597570`) says: `BLEND current_insample ll=0.99559
   alpha=0.40 fitted on eval (in-sample)` but `BLEND honest_closing ll=0.99796
   alpha=0.00 fitted on cal half`, while the *rolling* validation favours a **fixed
   α=0.35 with closing odds** (`ROLL roll_alpha_closing035 ll=0.99961`, best of 12
   variants incl. `roll_model 1.01106`). So the number in `model_info` is optimistic and
   the honest fit says "pure market"; the deployable truth is a damped prior, not the fit.
4. **Blended and served accuracy are reported nowhere.** `retrain.py` prints
   `blended_market_log_loss` but never `blended accuracy`, and never the model's accuracy
   *on the priced subset* — i.e. the one comparison (model vs market, same rows, top-1)
   that decides what the user sees has never been computed by the pipeline.

---

## 5. Ranked recommendations

Ranked by expected effect on **top-1 accuracy of what the user sees**. Every item states
its expected size and how to validate it in **GitHub Actions** (heavy work never locally).

### R1 — Make one feature path: evaluate the path that ships (and ship the path you evaluate) ★ biggest
**What:** `PredictionEngine.run()` should pass a real history (the pickle's
`recent_history`, or a bounded tail of the clean CSVs) into `predict_match`, *or* the
model must be trained on history-free features — but training and serving must be the
same function. Immediately after, add a CI job that scores **both** paths on the same
holdout and prints the difference.
**Why:** 111/161 features change, 41 % of argmax decisions flip, ECE jumps 0.026 → 0.109,
draw picks drop to 0 %, and the three paths disagree by ~12 pp of accuracy on the same
window (§3.3). No gate can work while the metric is computed on inputs the server never
uses.
**Expected effect:** unknown in sign, large in magnitude — the spread says **up to ~10 pp**
of top-1 is at stake; direction must be established empirically before deploying.
**Validate in Actions:** new workflow `.github/workflows/eval-serve.yml` (pattern-copy of
`eval-blend.yml`, `workflow_dispatch`, 90 min, ubuntu-latest): restore the model from
cache, rebuild features via `build_training_set` *and* `build_features`, report
`acc / ll / ECE / pick distribution / per-class recall` for both into
`$GITHUB_STEP_SUMMARY` + upload `serve_probs.npz`. Locally only the ≤2 k-row sample
scripts in `analysis/` are feasible (2.7 GiB).

### R2 — Gate on the accuracy of the *deployed* (blended) probabilities, on a pinned window
**What:** (a) pin the eval window by **dates** (e.g. "last 90 calendar days ending at
run time") instead of a sliding row-count half, so the comparability condition
(`holdout_days` equal, `split_at` ≤ 7 d drift) stops skipping — the gate has not yet
verified a single deployment; (b) make the gate a two-key gate: reject if
`eval_log_loss` worsens by > 0.0005 **or** `deployed_accuracy` (model+α, argmax) drops
by > 0.5 pp; (c) replace the mode tie-break with `goals_brier` only if accuracy is equal,
and always log `model acc on priced rows`, `blended acc`, `market fav acc` on those same
rows; (d) fit α honestly (cal-half → eval-half) or keep the damped 0.35 prior, but stop
reporting the in-sample fit.
**Why:** §4 — accuracy is never consulted; v2 shipped −0.95 pp unverified; the
`single`/`joint` tie-break cost −0.44 pp; the reported α=0.40 is in-sample.
**Expected effect:** prevents ~0.5–1 pp regressions per release (measured: 0.95 pp and
0.44 pp already lost this way); one-off **+0.44 pp** available immediately by re-selecting
the mode on accuracy; plus the ability to *see* the true model-vs-market gap (currently
unmeasured, estimated at −1.7 pp).
**Validate in Actions:** add the accuracy assertions to the existing `Unit tests` step of
`retrain-weekly.yml` (it already runs `ai/experimental/test_alpha_blend.py`) — assert the
gate dict contains `delta_accuracy`, `deployed_accuracy` and that `decision == rejected`
fires on a synthetic accuracy drop; then `workflow_dispatch` `train-model.yml` and read
the summary table. Small unit tests may run locally (seconds, no training).

### R3 — Decide α for top-1 accuracy, not only for log loss
**What:** report, on the honest holdout, the accuracy curve `α ∈ {0, .1, …, 1}` on priced
rows (mirror the `BLEND`/`DIAG` lines that currently print log loss only), then deploy
either the accuracy-maximising α or a constrained choice: *maximise accuracy subject to
`ll ≤ best_ll + 0.005`*. Optionally make the blend conditional (blend only when the
market's implied probability exceeds the model's by > X), which is the same knob with a
per-row switch.
**Why:** measured on the serving path over the 1,282 priced rows (57 % of fixtures):

| α | accuracy | log loss |
|---|---|---|
| 0.00 (pure market) | 50.23 % | **0.99796** |
| **0.35 (deployed)** | **51.79 %** | 0.99341 |
| 0.40 | 52.18 % | **0.99331** |
| 0.70 | 54.21 % | 0.99536 |
| 1.00 (pure model) | **55.54 %** | 1.00190 |

Accuracy and log loss want **different α** (accuracy ↑ with α, log loss bottoms at
α≈0.4). The pipeline only ever optimises the latter, i.e. it knowingly gives up
top-1 hits.
**Expected effect:** **+1.5 to +3.5 pp top-1 on the 57 % of fixtures that have odds**
(≈ +0.9 to +2.0 pp overall), costing ~0.005–0.008 nats of log loss. Caveat: the serving
backtest contains final-Elo leakage, so the *level* is inflated — the *shape* (monotone
accuracy in α) must be re-measured in CI before changing α.
**Validate in Actions:** extend `ai/experimental/eval_blending.py` to print
`BLEND … acc=…` next to each `ll` and re-run the existing `eval-blend.yml`
(`workflow_dispatch`) — no new workflow needed.

### R4 — Recalibrate **after** R1, on the serving path (for display + AI-Pick gates, ≈ small top-1 effect)
**What:** once train/serve features agree, fit a calibrator on *serve-path* holdout
probabilities (vector scaling or per-class isotonic with ≥ 5 k rows) and re-point
`is_uncertain` / `certainty` / `AI_PICK_CERTAINTY` at calibrated numbers.
**Why:** served ECE 0.107 vs holdout 0.026; mean displayed confidence 43.5 % vs realized
46–55 %; `is_uncertain` fires whenever `max p < 52 %`, i.e. for the overwhelming majority
of picks, which suppresses AI Picks and forces picks into low tiers.
**Expected effect on top-1:** **≤ 1 pp, likely neutral** — a shared monotone map cannot
change argmax, and only per-class maps can. Measured draw-promotion (the only class-
conditional shift available) is *negative*: live 46.07 % → 43.82 % at δ=0.02, serving
path 54.51 % → 54.11 %, so **do not promote draws** — argmax is already accuracy-optimal
for these probabilities. The real payoff is calibration of the *displayed* confidence and
of the tier/AI-Pick thresholds.
**Validate in Actions:** `eval-draw.yml` already exists and prints
`argmax accuracy / best promoted` — re-run it after R1 and require the promotion deltas to
be ≤ 0 before shipping any threshold change.

### R5 — Ops hygiene (no accuracy effect, but it invalidates measurements)
1. Reconcile the local `.pkl` with `model_info.json` (either re-fetch the CI artifact or
   delete the stale local pickle) — a local run currently serves joint/α=0.40 while every
   reported number describes single/α=0.35.
2. Write `version`/`git_sha` **inside** the pickle and have `FootballPredictor.load()`
   compare it with `model_info.json`, warning loudly on mismatch (mirrors the existing
   stale-`league_counts` guard in `trainer.py:450-453`).
3. Record the model version in each published prediction row so the 47/97 screen can be
   attributed to a specific artifact (today rows created 10-20/10-21/10-22 are mixed).
4. The 97-row screen should show a Wilson interval and a 30-day rolling window; ±10 pp
   noise on a headline number invites exactly this kind of misdiagnosis.

---

## 6. Bottom line

* **Stale/missing calibration/alpha?** The artifact that production serves is *not*
  stale (CI cache = `model_info` v2, verified from Actions logs) and the **α blend is
  applied (0.35)**; **isotonic calibration is absent by design** — it was fitted and
  rejected out-of-sample (1.0194 vs 1.0116), which is correct. The **local** `.pkl`
  *is* stale and inconsistent with `model_info.json` (joint/1.00999/α=0.40 vs
  single/1.01159/α=0.35), and the model is served with an **empty feature history**,
  i.e. 111/161 inputs differ from the ones the metrics were computed on.
* **Baselines:** always-home 42.6 % (live 43.3 %), draw rate 26.5 %, away 31 %,
  market favourite 50.2 % on priced rows (live 51.6 %), model 48.5 % (live 48.45 %,
  CI ±10 pp). Model = **+5.9 pp over the best trivial baseline, −1.7 pp vs the market**;
  log loss 1.0116 vs 0.9980.
* **Calibration:** the *holdout* probabilities are **not** over- or under-confident
  (ECE 0.026, isotonic makes it worse, geometric-mean P(true) 0.364 vs market 0.368) —
  the model's problem is resolution, not calibration. The *served* probabilities **are**
  miscalibrated (ECE 0.107, under-confident in the 0.40–0.60 band) because they are
  produced from inputs the model never saw in training.
* **Metrics gated:** **log loss only** (tol 0.0005, and only when the window happens to
  be comparable — it has never been so far). Accuracy is logged but never enforced;
  the mode tie-break uses *goals* Brier and cost 0.44 pp; the α is fitted in-sample for
  log loss and costs ~3 pp of top-1 on priced rows.

**Top 3:** ① unify the train/serve feature path and add a serve-path holdout score in CI;
② gate on *deployed* (blended, served) top-1 accuracy on a pinned date window, and log
model/market accuracy on the priced subset; ③ re-tune α for accuracy
(expected +1.5–3.5 pp on priced rows, validate by adding `acc=` lines to
`eval_blending.py` and re-running `eval-blend.yml`).

---

### Appendix — how each number was produced

* Base rates / market favourite: pandas over `ai/data/clean/{2025,2026}_matches.csv`
  with retrain's filters and its exact row-count split (`analysis/baselines_eval_window.json`).
* Serving-path accuracy / reliability: `analysis/serve_path_reliability_v2.py`
  (`cd ai && ../.venv/bin/python -u ../analysis/serve_path_reliability_v2.py`,
  608 s, ≤ 200 MB RSS, no training) — loads the deployed `.pkl`, rebuilds features via
  `FeaturePipeline.build_features` on an empty history (exactly what
  `PredictionEngine.run()` passes), scores 2,249 rows, dumps `analysis/serve_probs.npz`.
* Live reliability: read-only Supabase REST query of `predictions` (97 completed rows),
  scored from the stored `stats.prob_*` (`analysis/live_predictions_reliability.py`).
* CI evidence: Actions API run list + logs for `40222830493` (predict → cache hit on the
  v2 retrain run) and `40176564382` (retrain → `[OOT:single] … acc=0.4849`, gate skip),
  plus `40220597570` (blending ablation → honest α = 0.00, rolling α=0.35 best).
* Everything above is reproducible without retraining; **retraining/feature-build-scale
  experiments must run in `.github/workflows/`** (`train-model.yml`,
  `retrain-weekly.yml`, `eval-blend.yml`, `eval-draw.yml`, or a new `eval-serve.yml`).

# Pick Policy Analysis — accuracy vs. coverage curves and a concrete policy change

**Goal:** raise top‑1 1X2 accuracy far beyond the current ~48% (47/97 settled production picks).
**Verdict up front:** ~48% is *not* a model bug — it is the cost of argmaxing a 3‑class outcome for
**every** match. Every market-based curve in §3 says the ceiling for "pick something every match" is
~50–55%. The only honest lever is **selective confidence**: pick less often, pick when both sources
agree and the market is lopsided, and stop reporting accuracy over matches we never claimed to know.
Doing that mechanically moves the reported set from 48% → **71–74%** (at 18–23% coverage) or
**81%** (at 6% coverage), with no retraining.

All numbers below are measured, not estimated. Reproduction scripts: `analysis/pick_policy_*.py`
(data loader `analysis/pick_policy_data.py` — reads only odds + result columns, never the feature matrix).

---

## 1. How the user-facing pick is produced today (exact decision rule)

### 1.1 Model side — `ai/model/trainer.py::predict_match` (lines 468–741)

| Step | Code | Effect |
|---|---|---|
| 1 | `p_outcome_raw = self._outcome_pipe.predict_proba(X)` (L519) | LightGBM 3‑class probabilities |
| 2 | isotonic calibrators (L524–529) | **inactive** — this pickle has `calibrators=None` and `model_info.calibration_used=false` (isotonic made log loss *worse*: 1.0207 vs 1.0100) |
| 3 | **market blend** (L533–544): `p = α·model + (1−α)·de-vigged(odds)` | α = per‑league alpha, `_default = 0.4` in the deployed pickle (0.319–0.50 per league). **60% market weight** |
| 4 | `is_uncertain = entropy_norm ≥ 0.92 or head_disagreement ≥ 0.08 or max(p)·100 < 52` (L561) | flags 76.6% of sampled matches |
| 5 | `certainty = 100·(0.55·p1 + 0.45·(p1−p2)/p1)·(0.85 + 0.15·agreement)` (L568–577) | 0–100 score, **corr 0.979 with the blended top probability, corr 0.951 with market pmax** |
| 6 | **`idx = np.argmax(p_outcome)` (L579)** | **the pick — unconditional argmax, no threshold, ever** |

### 1.2 Badge / tier side — `ai/model/predictor.py` (L215–226)

```python
pick_bar = AI_PICK_CERTAINTY + (10.0 if reduced else 0.0)   # 65.0, +10 if league < 200 samples
is_ai_pick = bool(certainty >= pick_bar and not is_uncertain)
# tier_score = raw_conf if uncertain (capped at 39 → free tier), else max(certainty, raw_conf*0.6)
```

`TIER_THRESHOLDS` (L32): 0–39 Free, 40–55 Premium, 56–65 VIP, 66+ VVIP.

### 1.3 Accumulator / booking code — `ai/sportybet_booking.py`

* `get_ai_picks()` (L84–117): **every** upcoming row with `stats.is_ai_pick == true`. No cap on the
  number of legs, no market-confidence check, no agreement check, no per-league/day spread.
* `main()` (L279–326) maps all of them into one SportyBet accumulator and publishes it to
  `app_settings.booking_code`.
* `statwise/components/BookingCodeModal.tsx` (L55) mirrors the same filter for display.

### 1.4 Headline accuracy — `statwise/app/backtesting.tsx` (L50–51)

```ts
const completed = results.filter(r => r.actual_result);
const correct   = completed.filter(r => r.prediction === r.actual_result).length;
```
Accuracy is computed over **all** settled predictions — *not* filtered by `is_ai_pick`, and not
segmented by confidence at all (though `byLeague` / `byTier` segmentation already exists). That is
where "47/97 ≈ 48%" comes from: it mixes the 6% of matches the gate calls locks with the 94% it does not.

**Summary of today's rule:** `argmax(0.4·model + 0.6·market)` for every match → display; a separate
`certainty ≥ 65 ∧ ¬uncertain` badge decides what counts as an "AI Pick" and what feeds the acca.

---

## 2. Frames, method and caveats

Two independent frames (odds + goals columns only; **no feature matrix was built**):

* **Frame M (market-only, large):** `ai/data/clean/????_matches.csv`, date ≥ 2015‑08‑01,
  `quality_score ≥ 20`, non‑international, deduped, all three odds > 1.01 → **n = 126,523**.
  Odds columns prefer closing prices (cleaner reads `*CH`/`AvgCH`), i.e. a *strong* market benchmark.
* **Frame S (sampled model, production path):** the last holdout window (2026‑04‑21 → 2026‑10‑14,
  same filters, all three odds valid) → **n = 2,269**, ~12.9 odds‑covered matches/day.
  Features built with an **empty history frame — exactly what `PredictionEngine.run()` does after a
  plain pickle load** — then isotonic (off) + the deployed per‑league α blend, i.e. byte‑for‑byte the
  production inference path. Runtime 467 s, peak RSS 202 MB.

**Caveats (stated, not hidden):**

1. Frame S's `feature_pipe` was fit on data that overlaps the holdout, and `build_features` uses the
   **frozen end‑of‑dataset Elo ratings** → the model's numbers in Frame S are an **upper bound**
   (look‑ahead). The market-only curves in §3.1–3.3 are immune to this (odds + results only).
2. Frame M uses closing-ish book odds; production inference odds come from `odds_sync` (same kind of
   price, slightly noisier).
3. All comparisons inside Frame S are **like‑for‑like on the same rows**, so the *ordering*
   model/blend/market is meaningful even if the level is optimistic.
4. **Data inconsistency found:** the deployed pickle (`mode=joint`, `eval_log_loss=1.00999`,
   `alpha_default=0.4`, `accuracy=0.4867`) does **not** match the latest `ai/data/model_info.json`
   entry (`mode=single`, `1.01159`, `0.35`, `0.4849`). Worth reconciling before trusting either as
   "the model's reported accuracy" (probably a predict.yml cache‑restore of a different run).

---

## 3. THE CURVES (the evidence)

### 3.1 Baselines — Frame M, 100% coverage, n = 126,523

Outcome mix: home 43.8% / draw 26.2% / away 30.0%.

| Policy | Top‑1 accuracy |
|---|---|
| always Home | 43.8% |
| always Draw | 26.2% |
| always Away | 30.0% |
| random (uniform) | 33.3% |
| **market favourite (de‑vigged implied prob)** | **50.5%** |
| market favourite (raw min odds) | 50.5% |

Market‑favourite pick composition: home 70.1% (n=88,692), away 29.7% (n=37,622), **draw 0.16% (n=209)**.
Hit rate by chosen side: fav=home 51.6%, fav=away 48.1%, fav=draw 39.7%.

→ **Market consensus ≈ 50.5%, always‑home ≈ 43.8%.** The market favourite is +6.7pp over always‑home
and is the floor any pick policy must beat. Note also: *no* argmax policy ever picks Draw (0.2% of
matches), yet draws are 26.2% of outcomes — that alone caps any always‑pick policy near 74%.

### 3.2 ★ KEY TABLE — accuracy vs. coverage: pick the market favourite only when its implied probability ≥ X (abstain otherwise)

**Frame M, n = 126,523 (2015‑08 → 2026‑10)**

| X (implied prob) | coverage | picks | **top‑1 accuracy** | picks: home/draw/away |
|---:|---:|---:|---:|---|
| 0.35 | 98.9% | 125,091 | 50.7% | 70/0/30 |
| 0.38 | 88.3% | 111,678 | 52.5% | 72/0/28 |
| 0.40 | 78.5% | 99,362 | 54.2% | 73/0/27 |
| 0.42 | 69.9% | 88,490 | 55.7% | 75/0/25 |
| 0.44 | 61.4% | 77,660 | 57.4% | 76/0/24 |
| 0.45 | 57.5% | 72,757 | 58.2% | 76/0/24 |
| 0.46 | 53.9% | 68,176 | 59.0% | 76/0/24 |
| 0.48 | 46.4% | 58,736 | 60.9% | 77/0/23 |
| **0.50** | **39.8%** | **50,417** | **62.9%** | 78/0/22 |
| 0.52 | 34.7% | 43,933 | 64.5% | 78/0/22 |
| **0.55** | **27.4%** | **34,700** | **67.1%** | 78/0/22 |
| 0.58 | 21.2% | 26,786 | 70.0% | 79/0/21 |
| **0.60** | **17.5%** | **22,180** | **71.8%** | 79/0/21 |
| 0.65 | 11.1% | 13,991 | 76.3% | 79/0/21 |
| 0.70 | 6.4% | 8,079 | 80.5% | 81/0/19 |
| 0.75 | 3.6% | 4,517 | 84.2% | 84/0/16 |

**Recent era only (date ≥ 2024‑08‑01, n = 17,641; market fav 50.8%, always‑home 43.5%) — the curve is stable:**

| X | coverage | picks | accuracy |
|---:|---:|---:|---:|
| 0.40 | 80.2% | 14,141 | 54.1% |
| 0.45 | 58.5% | 10,321 | 58.1% |
| 0.48 | 48.5% | 8,562 | 60.2% |
| 0.50 | 41.1% | 7,242 | 62.5% |
| 0.52 | 35.4% | 6,249 | 64.4% |
| 0.55 | 27.9% | 4,929 | 67.4% |
| 0.58 | 21.5% | 3,784 | 70.3% |
| 0.60 | 17.9% | 3,150 | 72.2% |
| 0.65 | 11.0% | 1,933 | 76.5% |
| 0.70 | 6.5% | 1,145 | 81.0% |
| 0.75 | 3.6% | 640 | 83.6% |

**Read‑out:** ~40% coverage → **62.9%**; ~27% → **67.1%**; ~17.5% → **71.8%**; ~6% → **80.5%**.
Expected hits per 100 picks = the accuracy column. This is achievable with *zero model involvement*.

### 3.3 Same idea on the favourite's margin over the runner‑up (Frame M)

| margin ≥ | coverage | picks | accuracy |
|---:|---:|---:|---:|
| 2pp | 95.2% | 120,417 | 51.2% |
| 5pp | 87.8% | 111,144 | 52.5% |
| 8pp | 79.7% | 100,873 | 54.0% |
| 10pp | 74.2% | 93,887 | 54.9% |
| 12pp | 68.8% | 86,991 | 55.9% |
| 15pp | 60.2% | 76,190 | 57.6% |
| 20pp | 47.2% | 59,694 | 60.7% |
| 25pp | 36.0% | 45,517 | 64.1% |
| 30pp | 26.9% | 34,081 | 67.3% |

Slightly weaker than the raw‑confidence curve at equal coverage (36% cov / 64.1% vs 39.8% / 62.9% is
roughly a wash), so **use the absolute probability threshold, not the margin**, as the primary knob.

### 3.4 Agreement curve — pick only when model and market AGREE (Frame S, n = 2,269)

Displayed pick = production blend argmax; "agree" = model argmax == market favourite.

| market pmax ≥ | coverage | picks | **accuracy** |
|---:|---:|---:|---:|
| 0.00 (agree only) | 82.8% | 1,879 | 55.9% |
| 0.40 | 73.3% | 1,663 | 56.9% |
| 0.44 | 60.1% | 1,364 | 58.9% |
| 0.46 | 54.1% | 1,228 | 59.9% |
| 0.48 | 48.9% | 1,110 | 61.4% |
| **0.50** | **42.1%** | **955** | **64.0%** |
| 0.52 | 37.5% | 851 | 65.1% |
| 0.55 | 30.5% | 693 | 67.5% |
| 0.58 | 24.4% | 553 | 69.8% |
| 0.60 | 20.2% | 459 | 71.5% |
| 0.65 | 12.8% | 291 | 72.9% |

Agreement alone is worth **+3.6pp** over the all‑match argmax (55.9% vs 52.3%) and costs nothing
extra once you also require market confidence.

**Where the disagreements live (Frame S cross‑tab):**

| market pmax bucket | share of matches | agreement rate | acc model | acc blend | acc market |
|---|---:|---:|---:|---:|---:|
| < 0.40 | 17.9% | 53.1% | 47.2% | 46.2% | 36.4% |
| 0.40–0.45 | 21.3% | 75.2% | 47.8% | 42.0% | 42.2% |
| 0.45–0.48 | 9.7% | 86.8% | 46.1% | 45.7% | 45.7% |
| 0.48–0.50 | 7.7% | 88.6% | 46.9% | 43.4% | 43.4% |
| 0.50–0.52 | 5.1% | 90.4% | 53.9% | 52.2% | 52.2% |
| 0.52–0.55 | 7.3% | 95.8% | 54.5% | 53.3% | 53.3% |
| 0.55–0.60 | 10.7% | 96.3% | 59.3% | 58.8% | 58.8% |
| ≥ 0.60 | 20.4% | 99.4% | 71.4% | 71.2% | 71.2% |

→ Disagreement is almost entirely a **low‑confidence** phenomenon (mean market pmax 0.415 on
disagreement rows vs 0.525 otherwise). Requiring agreement at pmax ≥ 0.55 costs ~1–4% of coverage;
at pmax ≥ 0.60 it costs 0.6%. **Agreement is a free safety net above 0.55, not a real filter.**

### 3.5 "Model beats market implied by ≥ Y" curve — the value filter (Frame S)

Pick = the model's own argmax; keep only when `model_prob(pick) − market_implied(pick) ≥ Y`:

| Y | coverage | picks | accuracy |
|---:|---:|---:|---:|
| 0.00 | 33.2% | 754 | 50.1% |
| 0.02 | 26.2% | 595 | 50.1% |
| 0.03 | 23.3% | 528 | 50.0% |
| 0.05 | 17.9% | 407 | 47.9% |
| 0.07 | 13.2% | 300 | 50.3% |
| 0.10 | 7.1% | 162 | 51.9% |
| 0.12 | 4.7% | 106 | 50.0% |
| 0.15 | 2.2% | 50 | 48.0% |

**Plainly: there is no hit‑rate edge in "the model likes it more than the book".** Value picks land at
~50% — no better than a coin flip on which side, and *worse* than just following market confidence.
(Surprising variant: on rows where model and market *disagree*, the model was right 46.4% vs the
market's 24.9%, n = 390 — but that's still sub‑50% territory, and Frame S's look‑ahead caveat bites
hardest exactly there. Do not build a policy on it.)

### 3.6 Production‑blend confidence curve (Frame S) — what a confidence gate on the *displayed* pick gives

| blended pmax ≥ | coverage | picks | accuracy |
|---:|---:|---:|---:|
| 0.40 | 77.9% | 1,768 | 55.7% |
| 0.44 | 57.9% | 1,314 | 60.3% |
| 0.46 | 48.7% | 1,105 | 62.6% |
| 0.48 | 40.0% | 907 | 65.4% |
| 0.50 | 33.2% | 753 | 66.9% |
| 0.52 | 27.3% | 620 | 69.5% |
| 0.55 | 19.6% | 444 | 72.5% |
| 0.58 | 13.4% | 305 | 75.4% |
| 0.60 | 10.3% | 233 | 77.3% |
| 0.65 | 5.6% | 128 | 82.0% |

At matched coverage the **blend/certainty gate beats the pure‑market gate by ~2–4pp** (e.g. 40%
coverage: blend 65.4% vs market 64.1%; 15%: 74.2% vs 70.7%). The model *does* add value as a
**confidence sharpener** — just not as a value hunter.

### 3.7 The CURRENT gate, measured exactly (certainty formula reproduced, Frame S)

| `certainty ≥` | coverage (no uncertainty veto) | acc | coverage (`∧ ¬uncertain`) | acc | n |
|---:|---:|---:|---:|---:|---:|
| 45 | 40.9% | 65.4% | 23.4% | 70.9% | 530 |
| 50 | 29.8% | 68.8% | 23.4% | 70.9% | 530 |
| 52 | 25.2% | 70.6% | 22.9% | 71.3% | 520 |
| **55** | 18.2% | 73.9% | **18.2%** | **73.9%** | **414** |
| 58 | 13.0% | 75.3% | 13.0% | 75.3% | 295 |
| 60 | 10.5% | 76.6% | 10.5% | 76.6% | 239 |
| 62 | 8.4% | 79.6% | 8.4% | 79.6% | 191 |
| **65 (today)** | 5.9% | 81.2% | **5.9%** | **81.2%** | **133** |
| 70 | 3.2% | 81.9% | 3.2% | 81.9% | 72 |

Notes:
* The `¬uncertain` veto alone = **23.4% coverage / 70.9% accuracy** — it already does the job of a
  `market pmax ≥ 0.50 ∧ agree` filter (verified: adding both guards to `¬uncertain ∧ certainty ≥ 55`
  removes **0** rows — they are implied by the existing machinery).
* **`AI_PICK_CERTAINTY = 65` is the only parameter that actually binds**, and it is why only ~6% of
  matches ever qualify: 5 AI‑pick fixtures/week vs ~16 at 55 or ~21 at 52.

**Gate head‑to‑head at matched coverage (Frame S):**

| coverage target | current certainty gate | market pmax gate | blend pmax gate | agree + market pmax |
|---:|---:|---:|---:|---:|
| 60% | 60.0% | 57.9% | 59.7% | 57.9% |
| 50% | 62.2% | 60.3% | 62.0% | 60.3% |
| 40% | 65.4% | 64.1% | 65.4% | 64.1% |
| 33% | 66.8% | 66.2% | 67.0% | 66.2% |
| 30% | 68.4% | 67.3% | 68.4% | 67.3% |
| 25% | 70.6% | 68.8% | 70.6% | 68.8% |
| 20% | 72.5% | 71.4% | 72.5% | 71.4% |
| 15% | 74.5% | 70.7% | 74.2% | 70.7% |
| 10% | 77.1% | 75.8% | 77.1% | 75.8% |

→ The existing certainty score is a *good* selector (≈ blend‑confidence, ≥ market‑confidence
everywhere). **Don't replace it — re‑threshold it, and add the market guard as insurance.**

### 3.8 Draw handling — three ways, all measured

**(a) Suppress draws (draw eligible only if `odds_draw ≤ Z`; else pick the better side) — Frame M:**

| Z (odds ≤) | implied ≥ | draw picks kept | draw hit rate | overall accuracy |
|---:|---:|---:|---:|---:|
| 2.40 | 41.7% | 29 | 58.6% | 50.5% (±0.0pp) |
| 2.75 | 36.4% | 176 | 40.9% | 50.5% (±0.0pp) |
| 3.00 | 33.3% | 209 | 39.7% | 50.5% (±0.0pp) |
| 3.50 | 28.6% | 209 | 39.7% | 50.5% (±0.0pp) |

The market only ever makes Draw the favourite on **0.16%** of matches, so any rule of the form
"only call Draw when it's short" **changes essentially nothing** — the draw question is not where
accuracy lives.

**(b) Promote draws — "call Draw when `p_draw ≥ pmax − δ`" — Frame M (market probs):**

| δ | draw picks | draw hit | accuracy | vs argmax |
|---:|---:|---:|---:|---:|
| 0.00 | 209 | 39.7% | 50.5% | +0.0pp |
| 0.03 | 1,421 | 37.2% | 50.5% | +0.0pp |
| 0.05 | 3,997 | 34.9% | 50.5% | +0.0pp |
| 0.07 | 8,564 | 33.4% | 50.4% | −0.1pp |
| 0.10 | 20,756 | 31.3% | 49.6% | −0.9pp |
| 0.12 | 30,670 | 30.6% | 48.7% | −1.7pp |

Adding the `odds_draw ≤ Z` condition (2.80/3.00/3.25/3.50 × δ 0.03–0.10) never lifts accuracy above
50.5% — best case ±0.0pp, worst −0.9pp.

**(c) Same promotion with the MODEL's probabilities (Frame S):**

| δ | model argmax acc | Δ | draw picks | draw hit | blend acc | Δ |
|---:|---:|---:|---:|---:|---:|---:|
| 0.00 | 54.3% | — | 0 | — | 52.3% | — |
| 0.03 | 54.1% | −0.2pp | 14 | 28.6% | 52.3% | 0.0pp |
| 0.05 | 54.0% | −0.3pp | 32 | 28.1% | 52.4% | +0.0pp |
| 0.07 | 53.7% | −0.6pp | 140 | 30.0% | 52.4% | +0.0pp |
| 0.10 | 51.3% | −3.0pp | 425 | 28.5% | 51.2% | −1.1pp |

**Verdict on draws:** promoting draws **never** improves top‑1 accuracy (best case a rounding error,
worst −3pp), because the promoted draws hit at 28–38% while the outcomes they replace hit at 45–55%.
The model picks Draw **0 times in 2,269 matches** — that's not a bug to fix with a promotion rule;
it's a statement that argmax 1X2 and "Draw" rarely coexist. If product wants draw coverage, show it
as a **secondary "draw lean" annotation** (e.g. `p_draw ≥ 0.30`), never as the scored pick.

### 3.9 Accumulator math (why the current acca shape is the real problem)

Probability that *all* legs land (independent assumption — real correlation makes it worse):

| legs | @55% | @60% | @65% | @70% | @75% | @81% |
|---:|---:|---:|---:|---:|---:|---:|
| 3 | 16.6% | 21.6% | 27.5% | 34.3% | 42.2% | 53.1% |
| 4 | 9.2% | 13.0% | 17.9% | 24.0% | 31.6% | 43.0% |
| 5 | 5.0% | 7.8% | 11.6% | 16.8% | 23.7% | 34.9% |
| 6 | 2.8% | 4.7% | 7.5% | 11.8% | 17.8% | 28.2% |

Empirical, taking the **top‑N by certainty per calendar week** (Frame S, 25 weeks, legs only weeks
with N qualifying fixtures):

| gate | selection | legs | leg accuracy | weeks with ALL N right |
|---|---|---:|---:|---:|
| none | top 3/week | 54 | 87.0% | 66.7% |
| none | top 4/week | 72 | 83.3% | 50.0% |
| none | top 5/week | 90 | 78.9% | 22.2% |
| `cert≥55 ∧ ¬unc` | top 3/week | 48 | 87.5% | 68.8% |
| `cert≥55 ∧ ¬unc` | top 4/week | 60 | **85.0%** | **53.3%** |
| `cert≥55 ∧ ¬unc` | top 5/week | 70 | 80.0% | 28.6% |
| `pmax≥0.50 ∧ agree ∧ ¬unc` | top 4/week | 72 | 81.9% | 44.4% |

→ **4 legs, chosen as the week's highest‑certainty qualifiers, is the sweet spot** (~85% per leg,
>50% of weeks land all four). Today `get_ai_picks()` books *every* AI Pick with no cap — at a 6%
gate that's ~5 legs/week (fine), but if the gate is loosened to 55 without a cap it becomes 16 legs
(0.85¹⁶ ≈ 8%). **A leg cap is mandatory if coverage goes up.**

---

## 4. Does the model have an edge over the market at all?

Frame S, same 2,269 rows, production inference path:

| source | top‑1 accuracy | log loss |
|---|---:|---:|
| model (raw argmax) | **54.3%** | 1.00580 |
| production blend (α≈0.4) | 52.3% | **0.99519** |
| market (de‑vigged) | 50.6% | 0.99926 |
| always home | 43.4% | — |

Stored metrics in `ai/data/model_info.json` (the honest out‑of‑time holdout, n = 2,250 / 1,282 with odds):

| metric | model | market | blend |
|---|---:|---:|---:|
| log loss | 1.01159 | **0.99908** | **0.99610** |
| top‑1 accuracy | 0.4849 (all holdout rows) | — | — |
| isotonic calibration | 1.01936 → **not deployed** (made it worse) | | |

**What this means, plainly:**

1. **On probabilities, the market wins** (0.999 vs 1.006–1.012 in both my sample and retrain's own
   eval). The blend with α ≈ 0.4 (60% market) is the best calibrated object we have — keep it for
   anything that shows a percentage to a user.
2. **On argmax accuracy, the model edges ahead on odds‑covered rows** (54.3% vs 50.6%) — but that
   number is inflated by the look‑ahead described in §2, and retrain's honest holdout says 48.5%.
   Treat "model ≥ market on accuracy" as *suggestive*, to be re‑measured in CI (§6), not proven.
3. **There is no "model beats market" value edge** (§3.5: those picks land at ~50%). Stop treating
   `value_edge` (predictor.py L250–266) as a hit‑rate signal; it's a *betting* signal at best.
4. **Where to trust whom:** trust the **market for the gate** (is this match knowable? pmax, and the
   agreement rate that follows it) and for displayed probabilities; trust the **model only as a
   tie‑break/sharpener inside the confident set** (it's worth ~2pp at matched coverage, §3.6/§3.7);
   **never** let the model talk us out of a high‑confidence market favourite — the model is 65%
   home/35% away and picks Draw zero times, so it contributes ranking, not coverage.

---

## 5. Recommended policy change (concrete, with parameters)

### 5.1 The policy

```python
# ai/model/predictor.py  (replaces L218-219)
AI_PICK_CERTAINTY   = 55.0   # was 65.0  → 3.1× more qualifying fixtures (5.9% → 18.2% coverage)
AI_PICK_MARKET_PMIN = 0.50   # NEW safety net: de-vigged favourite must imply ≥ 50%
AI_PICK_REQUIRE_AGREE = True # NEW safety net: model argmax == market favourite
MIN_LEAGUE_SAMPLES  = 200    # unchanged (+10 certainty bar for thin leagues)

market_pmax, market_fav = _market_implied(fix)      # already computed for value_edge
odds_ok = market_pmax is not None

is_ai_pick = bool(
    not is_uncertain                                  # veto unchanged (this is the real gate)
    and certainty >= pick_bar                          # pick_bar = 55 (+10 thin league)
    and (not odds_ok or market_pmax >= AI_PICK_MARKET_PMIN)   # guard, costs 0 rows today
    and (not odds_ok or model_pick == market_fav)              # guard, costs 0 rows today
)
```

Plus, **store for every prediction** (cheap — the implied probs are already computed):
`stats.market_pmax`, `stats.market_fav`, `stats.model_market_agree`, `stats.pick_eligible`.

**Expected effect (measured, Frame S):**

| | today (`cert≥65`) | proposed (`cert≥55` + guards) | more conservative (`cert≥60`) |
|---|---:|---:|---:|
| coverage of matches | 5.9% (133) | **18.2% (414)** | 10.5% (239) |
| top‑1 accuracy of that set | 81.2% | **73.9%** | 76.6% |
| qualifying fixtures / week | ~5 | **~16** | ~10 |
| pick mix H/D/A | 83/0/17 | 77/0/23 | 77/0/23 |
| all‑match argmax (what the app reports today) | 52.3% | 52.3% (unchanged) | 52.3% |

Guards are free: inside `¬uncertain ∧ certainty ≥ 55`, `market_pmax ≥ 0.50` and agreement each
reject **0 rows** today — they exist to catch stale/missing odds and future model drift.

### 5.2 Accumulator / booking code (`ai/sportybet_booking.py`)

```python
MAX_LEGS      = 4      # NEW — empirical optimum, §3.9
MIN_LEGS      = 3      # NEW — publish nothing below this rather than padding
MIN_MARKET_P  = 0.50   # NEW — same guard as the badge

picks = get_ai_picks()                       # + filter: stats.market_pmax >= MIN_MARKET_P if present
picks.sort(key=lambda p: -(p["stats"].get("certainty") or 0))
picks = picks[:MAX_LEGS]                     # top-certainty first
# + at most 1 leg per (league, match_date) so legs don't all share a kick-off window
if len(picks) < MIN_LEGS: publish nothing this cycle
```
Expected: ~85% per leg, ~53% of weeks with all 4 landing (§3.9) — versus an uncapped acca whose
success probability collapses with every added leg.

### 5.3 Reporting (`statwise/app/backtesting.tsx`)

Segment the headline instead of one diluted number (mirrors the existing `byLeague`/`byTier` code):
`all | aiPick | uncertain`, computed the same way as `byTier`. The product's honest headline becomes
"73% on AI Picks (n=…), 52% across all predictions" rather than a single 48%.
Optional: add `byMarketConfidence` buckets (pmax ≥ 0.60 / 0.50–0.60 / < 0.50) — the buckets in §3.4.

### 5.4 Draws

No change to the pick rule — three independent sweeps (market probs, model probs, blend probs, with
and without an `odds_draw ≤ Z` cap) show promotion is neutral‑to‑negative (§3.8). If product wants
draw visibility, add a non‑scored `"draw_lean": p_draw >= 0.30` annotation in
`predict_match`'s output and render it as a chip on `PredictionCard.tsx`.

### 5.5 Files to edit (proposal only — nothing was modified)

| File | Change |
|---|---|
| `ai/model/predictor.py` | L40 `AI_PICK_CERTAINTY = 55.0`; add `AI_PICK_MARKET_PMIN` / `AI_PICK_REQUIRE_AGREE`; rewrite `is_ai_pick` (L218–219); write the four new `stats` fields (near L229–236) |
| `ai/model/trainer.py` | `predict_match`: return `market_pmax` / `market_fav` / `agree` in the `uncertainty` dict (L734–740) so predictor.py stops recomputing them for `value_edge` (L250–266) |
| `ai/sportybet_booking.py` | `get_ai_picks()` (L84–117): add `market_pmax` guard, certainty sort, `MAX_LEGS=4`, `MIN_LEGS=3`, 1 leg per league‑day |
| `statwise/components/BookingCodeModal.tsx` | L55: apply the same cap/sort when rendering selections |
| `statwise/app/backtesting.tsx` | L40–75: segment accuracy by `stats.is_ai_pick` (and optionally market‑confidence buckets) |
| `statwise/components/PredictionCard.tsx` | optional: render `stats.model_market_agree` / `draw_lean` chips |
| `.github/workflows/eval-pick-policy.yml` + `ai/experimental/eval_pick_policy.py` | new — §6 |

---

## 6. Honest validation plan (GitHub Actions only — never locally)

Heavy runs belong in CI; the local box has 2.7 GiB / 2 CPUs and every full feature build blows past
that (the existing workflows say so in their own comments).

**A. Offline gate sweep (primary).** New `ai/experimental/eval_pick_policy.py` modelled exactly on
`ai/experimental/eval_draw_picks.py` / `eval_blending.py` (train once with the production protocol,
split the holdout into calibration half + untouched eval half, never refit on the eval half):

* sweep `certainty ∈ {45,50,52,55,58,60,62,65}` × `market_pmax ∈ {0,0.45,0.48,0.50,0.55,0.60}` ×
  `agree ∈ {on,off}` × draw‑promotion `δ ∈ {0,0.03,0.05,0.07}`;
* print the accuracy‑vs‑coverage table per variant, plus market‑only and always‑home baselines on
  the *same rows*, plus log loss (so we don't win accuracy by wrecking calibration);
* exit non‑zero if the chosen parameters are dominated (a neighbouring cell has ≥ coverage and
  ≥ accuracy) — that's the pre‑registered "is the parameter still on the Pareto front" check.
* Workflow: `.github/workflows/eval-pick-policy.yml`, `workflow_dispatch` with `holdout_days`
  (default 180) and `model_mode` (default `joint`, matching the deployed pickle), `timeout-minutes: 90`,
  `grep -E "CURVE|gate |acc="` into `$GITHUB_STEP_SUMMARY`, `actions/upload-artifact` for the log —
  same shape as `eval-draw.yml`.

**B. Forward, leak‑free check (the one that actually counts).** A small model‑free job appended to
`.github/workflows/settle.yml` (it already runs on a schedule and needs no LightGBM): read
`predictions where status='completed'`, compute hit rate for `stats.is_ai_pick` vs the rest, and by
`stats.market_pmax` bucket, and post it to the step summary. This is the only measurement with **no
feature look‑ahead and no holdout reuse** — it's how we confirm the 73.9%/81.2% numbers transfer.

**Success criteria (pre‑registered, so we can't talk ourselves into it):**

1. CI gate sweep: proposed parameters within ±3pp of the Pareto frontier on **two consecutive**
   weekly runs (`retrain-weekly.yml` cadence), n ≥ 300 per reported bucket.
2. Forward screen: over ≥ 200 settled AI Picks, gated accuracy ≥ 65% and ≥ +10pp vs the
   ungated rate on the same rows; alert (don't fail) if it drops below.
3. Log loss of displayed probabilities must not degrade > 0.002 vs the current blend (the gate
   changes *selection*, never the probabilities).

---

## 7. What cannot be concluded here

* Frame S's model accuracies (54.3% / 52.3%) carry the frozen‑Elo look‑ahead; the market curves and
  the gate/coverage mechanics do not. Re‑measure the model side in CI before quoting it externally.
* Coverage figures are on **odds‑covered** rows (66% of the clean dataset, ~13/day). Matches without
  odds fail `odds_ok` and would fall back to the model‑only path — count them separately in the app
  rather than silently inflating the gated set's accuracy.
* Leg‑accuracy → acca success uses both an independence assumption (too optimistic) and an empirical
  weekly count (n≈25 weeks, too noisy). Report both; never advertise the independence number.
* The 48%/47‑of‑97 headline mixes gated and ungated matches (§1.4); the fix there is segmentation,
  not a different argmax.

---

### Appendix — reproduction

```bash
# market-only curves (fast, no model): ~5 s
.venv/bin/python analysis/market_curves.py
.venv/bin/python analysis/draw_and_gates.py        # draw sweeps + certainty ladder + acca math

# production-path model sample (~8 min, 200 MB RSS) — CI-sized, run only if needed:
.venv/bin/python analysis/model_vs_market.py       # writes pp_p.npy / pp_odds.npy
.venv/bin/python analysis/gates.py                 # reproduces the current gate exactly
.venv/bin/python analysis/candidate_gates.py       # candidate gate table + weekly leg yield
.venv/bin/python analysis/xtab.py                  # agreement cross-tab
```
Data loader: `analysis/pick_policy_data.py` (14 columns, cached pickle, never the feature matrix).

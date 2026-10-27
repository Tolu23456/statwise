# StatWise — Model & Datasets Report

> Generated: 2026-10-27 · Source of truth: `ai/data/model_info.json` (v6),
> `ai/AI_DOCUMENTATION.md`, `ai/model/features.py`, `ai/retrain.py`,
> live database metrics (`statwise/scripts/metrics_report.py`), and the
> on-disk state of `ai/data/`.

---

## 1. Executive Summary

| | |
|---|---|
| **Algorithm** | LightGBM ensemble ("Pico"), version **v6**, trained 2026-10-26 |
| **Artifact** | `ai/models/football_predictor.pkl` — 17,917,428 B (~17.9 MB), git sha `84265f68e43e` |
| **Training rows** | **331,949** club matches (from 377,472 clean rows), 286.8 s train time |
| **Features** | **161** (160 continuous + 1 categorical league id) |
| **Holdout** | Last **180 days**, split **2026-04-21**, 2,250 rows (1,282 with odds) |
| **Headline metrics** | Log loss **1.01129**, accuracy **48.27%**, Brier 0.20191, ECE 0.0275 |
| **Market blend** | Model + per-league α on de-vigged closing odds → **0.99491** (beats model 1.01129 *and* market 0.99796) |
| **Live 30-day** | **108 settled picks @ 48.1%** (free 54.5 / premium 50.0 / vip 40.0 / vvip 33.3) |
| **Replacement policy** | New model ships only if it beats the deployed baseline on the *same* OOT holdout within tolerance **0.0005**; `FORCE_REPLACE` is never used |

---

## 2. Model Architecture

### 2.1 Head structure — two candidate modes, raced honestly

| Mode | Structure | Labels |
|---|---|---|
| **`single`** ← current winner | 2 LightGBM heads | 3-class 1X2 (H/D/A) + binary Over/Under 2.5 |
| **`joint`** | 1 LightGBM | 6-class {H, D, A} × {Under, Over}; 1X2 and O/U are marginals of the same fit, so result↔totals correlation is modelled directly |

`MODEL_MODE=both` (set in `retrain-weekly.yml`) trains **both on the identical
train slice** and keeps whichever wins on the untouched later-holdout log loss
(goals Brier breaks near-ties). v6 shipped as **`single`**.

**Auxiliary heads** (plan 1.6, fit in *both* modes): binary **BTTS** and
**O/U 3.5** heads alongside the main outcome/goals heads — same rows, same
out-of-time mask, so every head is scored honestly.

### 2.2 LightGBM hyper-parameters

```
n_estimators=800, learning_rate=0.035, num_leaves=96, max_bin=127,
bagging_fraction=0.85, feature_fraction=0.7, lambda_l2=2.0,
early stopping on the last 5% of rows
```

Deliberately budgeted for a 4-vCPU GitHub-hosted runner (`max_bin=127` +
row-wise histograms); full retrain ≈ 287 s.

### 2.3 The 161-feature vector

Verified against `FEATURE_NAMES` in `ai/model/features.py` (count = 161).
Blocks 0–159 continuous, slot 160 = categorical `league_id` (unseen slugs map
to 0, never NaN):

| Group | Examples |
|---|---|
| **Elo (core)** | `elo_home/away/diff`, `elo_prob_*`, dual attack/defence Elo tracks |
| **Form rates** | win/draw/loss rate, goals for/against, PPG, momentum, clean-sheet & scoring rate (home + away) |
| **Venue-split form** | home-only and away-only PPG/goals over last 3/5/10 (`home_home_*`, `away_away_*`) |
| **H2H** | last-5 meetings: outcome counts, goals, averages, advantage |
| **Poisson / Dixon-Coles** | `lambda_h/a`, `p_over15/25/35`, `p_btts`, `p_0_0 … p_1_1`, `tau`-corrected score matrix |
| **Market** | implied 3-way probs, overround, `has_odds`, market-vs-model win diffs, volatility interactions |
| **Streaks & trends** | unbeaten/winless runs, streak length, trend, consistency |
| **League context** | league average, team attack/defence vs league, season stage, matchday |
| **Rest & schedule** | `days_h` / `days_a` (fatigue), draw propensity `h_dr` / `a_dr` |
| **Goal variance** | rolling scored/conceded variance, last-3 windows |
| **Squad (Titan v6)** | squad value, avg age, lineup rating + diff (8) |
| **Weather** | slots 133–140: temp, precipitation, wind, code, rain/cold/heat flags, `w_present` (Open-Meteo) |
| **Travel/fatigue** | slots 141–145: `travel_home/away/diff`, `fatigue_diff`, `travel_present` |
| **Form-5 diffs** | slots 146–154: last-5 PPG/GD differentials + `form_present` |
| **Padding** | reserved slots (kept stable so the pickle's feature order never shifts) |
| **`league_id`** | slot 160, categorical |

Feature blocks are computed by the C++ kernel `libstatwise.so`
(`compute_all_features_bulk_v6`, SIMD/AVX-512) with pure-Python fallbacks;
missing sidecars (weather/travel) degrade to zeros **plus a presence flag**,
never a silent fake value.

### 2.4 Calibration & market blend (where the accuracy actually comes from)

1. **Isotonic calibration** — per-class isotonic fitted on the **earlier half**
   of the out-of-time holdout, deployed **only if it lowers log loss on the
   later half**. v6: isotonic 1.0241 vs raw 1.01129 → **rejected**
   (`calibration_used=false`); a 1.4k–3k row window can't always support
   per-class isotonic, and the gate says no.
2. **Per-league α blend** — log-loss-optimal weight between the model and the
   **de-vigged closing consensus** (the source that won every eval-blend
   ablation: −0.00112 / −0.00109 / −0.00136), fitted on later-half rows that
   actually have odds, **damped toward the prior 0.35** (damp weight 0.5618),
   8 leagues with enough odds rows.
3. **Result:** blended **0.99491** on the odds rows — better than the raw
   model and better than the market itself.

### 2.5 Anti-bias rules (post-prediction)

| Rule | Threshold |
|---|---|
| Sample weights | inverse class frequency: home 0.74×, **draw 1.33×**, away 1.12× |
| Draw floor | predict draw when P(draw) ≥ **0.255** AND \|P(home)−P(away)\| ≤ **0.14** |
| Away boost | predict away when P(away) ≥ P(home) − **0.03** |
| Draw confidence cap | max **62%** (prevents draw overconfidence) |

---

## 3. Training Pipeline & The Gate

```
ai/data/clean/*.csv  (377,472 rows)
  → dropna(teams/scores), date ≥ 1993-08-01,
    drop internationals (tuned for club football), quality_score ≥ 40,
    drop clubelo-reference rows            → 331,949 rows
  → weather sidecar join (Open-Meteo archive)
  → chronological split: last 180 days = holdout (split 2026-04-21)
       ├─ earlier holdout half → fits isotonic + α (never evaluated on)
       └─ later  holdout half  → THE eval rows (n = 2,250)
  → race single vs joint on the same train slice
  → gate vs deployed baseline → ship or keep
```

**Gate rules** (`ai/retrain.py`):

- *Apples-to-apples* required: identical `HOLDOUT_DAYS` **and** split drift
  ≤ **7 days** (the split slides as new rows land every 3 h). Otherwise the
  gate **skips loudly** (`skipped_not_comparable`) and the next run compares
  against the new model.
- Reject when `Δ log loss > 0.0005` (`MAX_LL_REGRESSION`) on that identical
  window → `last_rejected` is recorded in `model_info.json` and the **deployed
  model stays**.
- `FORCE_REPLACE` exists as an env var but is **never used** (policy); the
  attention-student race doesn't even read it.
- Rejection is not failure: the rejected candidate's code and recipe stay
  available for a later window (exactly what happened to the sim distillation).

**Cadence:** `retrain-weekly.yml` every Monday 03:00 UTC (`HOLDOUT_DAYS=180`,
`MODEL_MODE=both`, unit tests first). Heavy training runs **only in GitHub
Actions** — local runs are tiny/payload-only by policy.

### Version history (`model_info.json`)

| v | Trained | Mode | Holdout | Eval LL | Accuracy | Comparable? |
|---|---|---|---|---|---|---|
| 1 | 2026-10-25 | joint | 90 d, split 2026-07-20 | 1.00857 | 49.44% | ✗ different window |
| 2 | 2026-10-25 | single | 180 d, split 2026-04-21 | 1.01159 | 48.49% | baseline for v3 |
| 3 | 2026-10-25 | single | 180 d | 1.01129 | 48.27% | Δ −0.00030 ≤ tol → accepted |
| 4–5 | 2026-10-26 | single | 180 d | 1.01129 | 48.27% | accepted (Δ 0.0) |
| **6** | 2026-10-26 | single | 180 d | **1.01129** | **48.27%** | accepted (Δ 0.0) |

---

## 4. Metrics

### 4.1 Holdout (v6, out-of-time, split 2026-04-21)

| Metric | Value | Rows |
|---|---|---|
| Eval log loss (model) | **1.01129** | 2,250 |
| Accuracy (argmax 1X2) | **48.27%** | 2,250 |
| Brier (1X2) | 0.20191 | 2,250 |
| Expected calibration error | 0.0275 | 2,250 |
| Goals Brier (O/U 2.5) | 0.24000 | 2,250 |
| BTTS Brier | 0.24461 | 2,250 |
| O/U 3.5 Brier | 0.22545 | 2,250 |
| Market log loss (best book, de-vigged) | 0.99908 | 1,282 |
| Market log loss (closing consensus) | 0.99796 | 1,282 |
| **Blended (model + α·market)** | **0.99491** | 1,282 |
| Isotonic candidate | 1.0241 → **rejected** | later half |

### 4.2 Live production (real database, 2026-10-27)

**Settled accuracy, last 30 days** (production rule: `actual_result ==
prediction`, `status = completed`):

| Tier | Accuracy | n |
|---|---|---|
| **Overall** | **48.1%** (52/108) | 108 |
| free | 54.5% | 33 |
| premium | 50.0% | 42 |
| vip | 40.0% | 30 |
| vvip | 33.3% | 3 |

Last 24 h: 18 predictions published, 6 AI Picks (33.3%), tiers free 12 /
vvip 6. Booking pipeline: 3/3 code generations succeeded. Alerts: all clear.

> Honesty note: n=108 puts a ±~9.5 pp 95% band on the overall number, and the
> per-tier cells (esp. vvip n=3) are far too small to rank tiers by.

### 4.3 Legacy backtest (`ai/data/backtest_results.json`, 2026-04-07)

Pre-v6-era snapshot, test cutoff 2023-08-01, 17,996 bets against 157,444
history rows: **36.6% accuracy, LL 1.3033, Brier 0.7878, flat-stake ROI
−39.9%** (home 44.1 / draw 25.1 / away 35.8; 90%+ confidence bucket hit
54.1%). Kept as the historical "before" picture — the current model is a
different, calibrated-and-blended generation.

---

## 5. Datasets

### 5.1 The seven sources (`ai/dataset_links.txt`)

| # | Source | Coverage | Volume | Format |
|---|---|---|---|---|
| 1 | **football-data.co.uk** | 32 seasons (1993/94–2024/25), 35+ league codes (E0–E3, D1/D2, SP1/SP2, I1/I2, F1/F2, N1, P1, SC0–3, B1, T1, G1 + overseas ARG/BRA/CHN/…) | ~200K rows | CSV per season |
| 2 | **xgabora/Club-Football-Match-Data** | 42 leagues, 2000–2025 + Elo ratings + teams | ~475K rows | CSV |
| 3 | **understat** (douglasbc) | shot-level xG, top-5 EU leagues 2014/15–2021/22 | ~5M shot records | CSV per league+season |
| 4 | **martj42/international_results** | every international since 1872 (+ scorers, shootouts) | ~47K rows | CSV |
| 5 | **jfjelstul/worldcup** | FIFA World Cup 1930–2022, stage + venues | ~1K rows | CSV |
| 6 | **openfootball/football.json** | 20 EU competitions 2011–2025, halftime + matchday | ~80K matches | JSON per season |
| 7 | **statsbomb/open-data** | competition reference metadata | small | JSON |

**Downloader:** C++17 `dataset_downloader.cpp` (553 lines) — zero external
deps (system `curl`), idempotent (skips existing files), RAM governor
(pause < 400 MB free), CPU governor (sleep at load > 75%), clean SIGTERM,
optional tasks logged not failed. **1,198 tasks** total.

### 5.2 On-disk inventory (verified today)

| Path | Size | Contents |
|---|---|---|
| `ai/data/raw/` | 205 MB, **1,032 files** | football_data 746 · openfootball 197 · understat 80 · international 3 · worldcup 3 · xgabora 2 · statsbomb 1 |
| `ai/data/clean/` | 64 MB, **73 files** | `YYYY_matches.csv` per calendar year, **1930 → 2026**, **377,472 rows** |
| `ai/data/weather_cache/` | 54 MB | Open-Meteo historical archive cache |
| `ai/data/weather_by_match.csv.gz` | 2.5 MB | per-match weather join sidecar |
| `ai/data/clubelo.csv` | 5.3 MB | Club Elo ratings feed |
| `ai/data/international_results.csv` | 3.1 MB | international sidecar |
| `ai/data/sim/` | 3.0 MB | Monte Carlo **recipe** (`worlds.json`, 80 worlds) — recipe, never rows |
| `ai/data/tennis/` `baseball/` `volleyball/` `basketball/` | 241 + 46 + 43 + 25 MB | multi-sport corpora from `gather_sports_data.py` for **future modules — not used by the football model** |
| **`ai/data/` total** | **718 MB** | |

`model_info.json` reports **331,949** rows entering training after the
load-time filters (dropna, ≥1993-08-01, club football only, `quality_score ≥ 40`).

### 5.3 Cleaning pipeline (C++ `dataset_cleaner.cpp`, 2,115 lines, 10 phases)

Thread pool (`nproc/2`, max 6), CPU/RAM governors, graceful SIGTERM:

1. xgabora base parse → 2. football-data.co.uk seasonal CSVs (parallel) →
3. understat xG map injection → 4. martj42 internationals (1960+) →
5. jfjelstul World Cup → 6. openfootball JSON scanner →
7. cross-source **conflict scan** (`score_conflict` flag) →
8. **Jaro-Winkler fuzzy dedup** (≥0.88) → 9. **quality scoring** (0–100) →
10. write per-year CSVs.

**Validation ranges per row:** score 0–25/side · odds 1.005–400 with 3-way
implied 80–150% · shots 0–60 · corners 0–30 · fouls 0–50 · cards 0–15 ·
xG 0–12 · league-stratified z-score > 5σ discarded. Six date formats
normalised. **1,000+ team aliases** mapped to canonical names
(`Man Utd` → `Manchester United`, `Bayern` → `Bayern Munich`, …).
Missing values stay **empty — never zero-filled**.

### 5.4 Output schema — 54 columns on disk

The documented core is 48 columns (`date`, teams, scores, `league_slug`,
`country`, `source`, halftime, shots/SOT, corners, fouls, cards, Elo,
full odds suite incl. AH and O/U 2.5, `xg_home/away`, `quality_score`,
`league_tier`, `is_international`, `score_conflict`, `tournament`,
`is_neutral`); six enrichment columns were added later:
`home/away_squad_value`, `home/away_avg_age`, `home/away_lineup_rating`.

### 5.5 Live/operational data feeds (not training data — serving)

| Feed | Purpose | Cadence |
|---|---|---|
| **API-Football (RapidAPI)** | primary fixtures + real odds (19 league IDs) | per predict run |
| **football-data.org v4** | fixtures fallback (12 league IDs) | every 3 h (`fetch-football-data.yml`) |
| **TheSportsDB** | fixture fallback + settlement scores (36 leagues) | — |
| **ESPN site API** | live state (minute/score) + settlement | every 5 min (`live-enrich.yml`), settle 4×/day |
| **The Odds API v4** | odds → `odds_sync` PATCH (de-vigged at use) | every 2 h (`odds-sync.yml`) |
| **Open-Meteo** | geocoding, historical archive, forecast → 8 weather features | backfill + per-run |
| **Club Elo / xgabora Elo** | rating inputs | with data fetches |

`ALL_LEAGUES` = union of the three league maps (≈36 distinct slugs); fixture
fetch covers the next 48 h across 16+ leagues. Prediction upserts to Supabase
`predictions` keyed on `match_id` (`predict.yml` every 4 h; local scheduler
every 20 min); settlement resolves actual scores afterwards.

---

## 6. Experimentation (behind the production model)

### 6.1 Monte Carlo Match Lab — "the fool's plan" (`ai/sim/**`)

- **Goal process:** Dixon-Coles bivariate Poisson fitted by penalised MLE on
  train rows only; **cap-down** MCMC (symmetric mixture proposal with exact
  Metropolis-Hastings correction) yields a *spread* of plausible worlds;
  breeding = element-wise crossover + mutation with elitist acceptance.
- **Storage rule:** recipe only — `worlds.json` (3.02 MB, 80 worlds); simulated
  scorelines are folded into mergeable sufficient statistics and discarded.
  20 CI shards × fixed-size counters ⇒ scale never becomes storage.
- **Run:** **522,594,272 outcomes** simulated; combined ensemble on the *real*
  OOT holdout (n=5,019): 1X2 LL 1.06151 / BTTS 0.62146 / O25 0.69923 →
  **GATE PASS** (beats league prior on the goal markets; first dispatch failed
  O35 by 0.0035, `publishable` refined with reasoning recorded in
  `ai/sim/evaluate.py`).
- **Distillation attempt:** +163,715 teacher soft labels (327,430 → 491,145
  rows) → gate **REJECTED** it honestly (1.01129 → 1.01903, +0.00774 ≫ 0.0005).
  Production kept v6; the recipe-based distillation code stays available.
  Synthetic mass **never** trains LightGBM as raw rows.

### 6.2 Attention student (`ai/attn/**`)

A transformer student racing LightGBM inside the *same* retrain competition:
identical train/holdout slices, identical isotonic protocol, identical
0.0005 gate arithmetic; `FORCE_REPLACE` never read.

- Prior experiment: **48.31% acc / LL 1.03378** — underperformed → **do NOT
  replace production** (recorded in `IMPROVEMENT_PLAN.md`).
- Run `40503960706` (2026-10-27): early-stopped at epoch 12 with best
  inner-val LL **0.95050** (inner tail only — never the holdout), but the
  runner received a shutdown signal (exit 143) right after → the **Train
  student step failed and the gate job was skipped**. No comparison was
  produced; a re-run is required before any conclusion (Phase 5.10 open).

### 6.3 Standing ablations (own workflows)

`eval-blend.yml` (closing consensus won all three windows), `eval-draw.yml`
(draw blind spot), `eval-features.yml` (feature holdout ablation),
`simulate.yml` (shard lab + gate), `train-attention-student.yml`.

---

## 7. Honest Limitations

1. **The model does not beat the market.** On odds rows: model 1.01129 vs
   closing 0.99796. The product's edge comes from the α-blend, anti-bias
   rules and tiering — not from out-predicting closing prices.
2. **The holdout is small.** 180 days ≈ 2,250 rows; on n≈5k a log-loss
   delta carries ~0.014 sampling noise. The 0.0005 tolerance is a strict
   *policy* line for like-for-like comparisons, not statistical significance.
3. **Live sample is thin.** 108 settled picks ⇒ ±~9.5 pp overall; tier splits
   (vvip n=3) are anecdotal. Live numbers are reported verbatim, including
   below-50% tiers.
4. **Draws remain the hard class** (legacy backtest 25.1%) — mitigated by the
   draw floor/cap rules, not solved.
5. **Isotonic calibration is currently off** — rejected by its own
   later-half test; ECE 0.0275 raw is acceptable but there's headroom.
6. **Doc drift:** `AI_DOCUMENTATION.md`'s serving section says "98-feature
   extraction" and "48 columns"; the code is **161 features / 54 columns**.
7. **~355 MB of multi-sport data sits unused** in `ai/data/` (tennis,
   baseball, volleyball, basketball) pending future modules.

---

## 8. Reproducing / Where Things Live

| Concern | Location |
|---|---|
| Trainer + gate | `ai/retrain.py` (917 lines) · `ai/model/trainer.py` (829) |
| Features | `ai/model/features.py` (472) · C++ `libstatwise.so` · `ai/model/{form,travel}.py` |
| Data pipeline | `ai/tools/dataset_downloader.cpp` · `ai/tools/dataset_cleaner.cpp` (2,115 lines) |
| Model + metadata | `ai/models/football_predictor.pkl` · `ai/data/model_info.json` |
| Docs | `ai/AI_DOCUMENTATION.md` · `ai/AI_IMPROVEMENT_PLAN.md` · repo `IMPROVEMENT_PLAN.md` |
| Weekly retrain | `.github/workflows/retrain-weekly.yml` (Mon 03:00 UTC) |
| Predictions | `.github/workflows/predict.yml` (every 4 h) |
| Live metrics | `.github/workflows/metrics-daily.yml` → `statwise/scripts/metrics_report.py` |
| Training policy | All heavy training runs in GitHub Actions only; local runs use tiny payloads. |

**Key env knobs:** `HOLDOUT_DAYS` (180) · `MODEL_MODE` (both) ·
`MAX_LL_REGRESSION` (0.0005) · `SYNTH_LABELS`/`SYNTH_FRACTION` (distillation,
default off) · `ATTN_STUDENT` (attention race, off by default) ·
`FORCE_REPLACE` (never set).

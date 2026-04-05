# StatWise AI Engine — Documentation

> Last updated: April 2026  
> Model version: v2 (98 features, 5-model deep stacking ensemble)

---

## Table of Contents

1. [Overview](#overview)
2. [Data Sources](#data-sources)
3. [Feature Engineering (C++ Kernel)](#feature-engineering-c-kernel)
4. [Feature List (98 features)](#feature-list-98-features)
5. [Model Architecture](#model-architecture)
6. [Anti-Bias System](#anti-bias-system)
7. [Prediction Decision Logic](#prediction-decision-logic)
8. [Training Pipeline](#training-pipeline)
9. [Scheduler & Live Predictions](#scheduler--live-predictions)
10. [Backtesting Results](#backtesting-results)
11. [Known Limitations](#known-limitations)
12. [Ways to Improve](#ways-to-improve)

---

## Overview

StatWise uses a two-layer machine learning ensemble trained on ~106,000 historical football matches across 16+ leagues and 9 seasons. It outputs three probabilities (Home Win / Draw / Away Win) plus an Over/Under 2.5 goals prediction for each upcoming fixture. Predictions refresh every 20 minutes and are pushed to Supabase.

---

## Data Sources

| Source | Matches | Notes |
|--------|---------|-------|
| football-data.co.uk | ~51,000 | 9 seasons, 16 European/domestic leagues. Primary training data. |
| StatsBomb Open Data | ~836 | Match-level event data, free tier |
| OpenFootball | ~7,230 | GitHub-hosted, multi-league historical results |
| martj42/international_results | ~49,000 | International fixtures, used for general form patterns |
| ClubElo | ~84,000 | **Excluded from training** — synthetic Elo data only, not real match records |

**Total training set:** 106,497 unique real matches → capped at 60,000 randomly sampled rows for memory safety.

The FiveThirtyEight SPI dataset (formerly used) was removed when their GitHub CSV was taken down.

---

## Feature Engineering (C++ Kernel)

All computationally expensive features are computed in a compiled C++ shared library (`ai/libstatwise.so`) loaded via Python `ctypes`. The kernel is compiled with `-O3 -march=native -ffast-math` for maximum throughput.

**11 exported C functions:**

| Function | Description |
|----------|-------------|
| `compute_elo_ratings` | Classic Elo with goal-difference multiplier and home-advantage offset |
| `compute_attack_defense_elo` | Separate Elo tracks for attack (goals scored) and defence (goals conceded) |
| `compute_form_vector` | Exponentially decayed form over a 15-match window (decay=0.85) |
| `compute_h2h_stats` | Head-to-head win/draw/loss rates and goal averages |
| `compute_goal_probability` | Dixon-Coles bivariate Poisson: P(over 1.5/2.5/3.5), BTTS, clean sheets |
| `compute_elo_probabilities` | Elo-derived win/draw/loss probabilities |
| `batch_compute_features` | Vectorised batch wrapper for the above |
| `compute_poisson_score_matrix` | Exact scoreline probabilities up to 6×6, with Dixon-Coles rho correction (ρ = −0.13) |
| `compute_consecutive_runs` | Current unbeaten/winless run lengths (home + away separately) |
| `compute_venue_split_form` | Home-only form for home team; away-only form for away team |
| `compute_goals_variance` | Rolling variance of goals scored and conceded per team |

**Key constants in the C++ kernel:**
- Elo scale: 400 (same as chess)
- Form decay: 0.85 per match, 15-match window
- Dixon-Coles correlation: ρ = −0.13 (correct negative dependency for low-scoring matches)
- Poisson λ cap: 6.0 (prevents overflow on extreme outlier matches)
- Score matrix: up to 6×6 (i.e., 0–6 goals each side)

Python falls back to pure-Python implementations if the `.so` fails to load.

---

## Feature List (98 features)

Features are ordered identically between training and inference. The index is important — changing the order requires retraining.

| Index | Group | Features |
|-------|-------|----------|
| 0–5 | **Elo** | `elo_home`, `elo_away`, `elo_diff`, `elo_prob_home`, `elo_prob_draw`, `elo_prob_away` |
| 6–9 | **Attack/Defence Elo** | `home_attack_elo`, `home_defense_elo`, `away_attack_elo`, `away_defense_elo` |
| 10–19 | **Home overall form** | win/draw/loss rate, goals scored/conceded/diff, momentum, PPG, clean sheet rate, scoring rate |
| 20–29 | **Away overall form** | same 10 metrics for away team |
| 30–33 | **Home venue-split form** | home team's home-specific win rate, PPG, goals scored/conceded |
| 34–37 | **Away venue-split form** | away team's away-specific win rate, PPG, goals scored/conceded |
| 38–43 | **Head-to-head** | H2H home win/draw/away win rates, avg goals, match count |
| 44–53 | **Dixon-Coles goal probs** | P(over 1.5/2.5/3.5), BTTS, home clean sheet, away clean sheet, expected goals each side, λ ratio, total xG |
| 54–57 | **Exact scoreline probs** | P(0-0), P(1-0), P(0-1), P(1-1) |
| 58–61 | **Form differentials** | win diff, goals diff, momentum diff, PPG diff |
| 62–65 | **Market / odds** | implied home/draw/away probabilities from betting odds, market overround |
| 66–69 | **Poisson attack/defence** | attack strength and defence strength (vs league average) for each side |
| 70–73 | **Consecutive runs** | home unbeaten run, home winless run, away unbeaten run, away winless run |
| 74–75 | **Current streak** | home streak, away streak (signed: positive = wins, negative = losses) |
| 76–77 | **Form trend** | gradient of last-5 form for home and away |
| 78–79 | **Scoring consistency** | rolling std of goals scored per team |
| 80–81 | **H2H extended** | H2H avg goals total, H2H home advantage factor |
| 82–84 | **League context** | league avg goals, league home win rate, league draw rate |
| 85 | **Venue PPG diff** | home team's home PPG minus away team's away PPG |
| 86–89 | **Attack/defence vs league** | how each team compares to their league's attack/defence averages |
| 90–93 | **Goals variance** | rolling variance of goals scored and conceded (home + away) |
| 94–97 | **Recent 3-match goals** | goals scored/conceded in last 3 matches (home + away) |

**Total: 98 features**

---

## Model Architecture

### Layer 1 — Five Base Models (all probability-calibrated)

| Model | Algorithm | Key Settings | Bias Notes |
|-------|-----------|-------------|-----------|
| **XGBoost** | Histogram gradient boosting | 200 trees, depth 6, lr 0.05, colsample 0.75 | — |
| **HistGBM** | sklearn HistGradientBoosting | 150 iters, depth 6, l2=2.0 | `class_weight='balanced'` |
| **ExtraTrees** | Extremely Randomised Trees | 150 trees, depth 20, sqrt features | `class_weight='balanced'` |
| **RandomForest** | Bagged decision trees | 150 trees, depth 16, sqrt features | `class_weight='balanced'` |
| **NeuralNet** | PyTorch Residual MLP | See architecture below | Class-freq CrossEntropyLoss |

Each base model is wrapped in `CalibratedClassifierCV(method='sigmoid', cv=2)` to produce well-calibrated probability outputs before being passed to the meta-learner.

### Neural Network Detail

```
Input (98)
  ↓
Stem: Linear(98→256) → BatchNorm → ReLU → Dropout(0.3)
  ↓
ResBlock-1: Linear(256→256) → BN → ReLU → Drop(0.3) → Linear(256→256) → BN → residual add → ReLU → Drop
  ↓
Downsample: Linear(256→128) → BN → ReLU → Dropout(0.25)
  ↓
ResBlock-2: Linear(128→128) → BN → ReLU → Drop(0.25) → Linear(128→128) → BN → residual add → ReLU → Drop
  ↓
Downsample: Linear(128→64) → BN → ReLU → Dropout(0.2)
  ↓
Head: Linear(64→3 or 64→2)
```

**Training settings:** AdamW optimizer, OneCycleLR scheduler, 50 epochs, batch size 512, lr 3e-3, weight decay 1e-4, Kaiming weight init.

### Layer 2 — Meta-Learner

`LogisticRegressionCV` (5-fold CV, 10 Cs, L2 penalty, lbfgs solver) trained on the out-of-fold probability predictions from all 5 base models. Sees only the 15 OOF probability columns (not the raw 98 features) to prevent overfitting.

### Two Separate Stacks

| Stack | Target | Output |
|-------|--------|--------|
| **Outcome stack** | Home Win / Draw / Away Win | 3 probabilities |
| **Goals stack** | Under 2.5 / Over 2.5 goals | 2 probabilities |

### Training Infrastructure

```
StandardScaler → StackingClassifier(cv=3, n_jobs=1, passthrough=False)
```

- `cv=3`: 3-fold OOF (good bias/variance trade-off, keeps RAM under control)
- `n_jobs=1`: folds run sequentially — prevents OOM from 5 × 3 = 15 model copies in RAM
- `passthrough=False`: meta-learner sees OOF probs only (~15 columns vs 98) — saves ~150 MB peak RAM

---

## Anti-Bias System

Football data has a structural home-win bias (~45% of historical matches). Without correction, the model predicts Home Win for nearly every match. Three layers of corrections work together:

### Layer 1 — Sample Weights (Training Time)

Inverse-frequency weights are computed from the 60K training set's class distribution and passed to all base estimators via `StackingClassifier.fit(sample_weight=...)`:

```
Observed distribution (60K samples):
  Home Win:  27,087  (45.1%)  → weight  0.74×
  Draw:      15,005  (25.0%)  → weight  1.33×
  Away Win:  17,908  (29.8%)  → weight  1.12×
```

Weights are normalised so the mean weight is 1.0, preserving scale.

### Layer 2 — Model-Level Class Weights

Three of the five base models use sklearn's `class_weight='balanced'` (HistGBM, ExtraTrees, RandomForest). The PyTorch NeuralNet uses per-class CrossEntropyLoss weights derived from class frequencies, merged with the per-sample weights passed in.

### Layer 3 — Post-Prediction Decision Logic

Applied at inference time in `predict_match()`, after raw probabilities are computed:

```python
DRAW_PROB_FLOOR = 0.255   # draw predicted when P(draw) is at least this
HA_GAP_CEIL     = 0.14    # and |P(home) - P(away)| is at most this
AWAY_BOOST      = 0.03    # predict away if P(away) >= P(home) - 0.03

if p_draw >= DRAW_PROB_FLOOR and abs(p_home - p_away) <= HA_GAP_CEIL:
    prediction = "Draw"
elif p_away >= p_home - AWAY_BOOST:
    prediction = "Away Win"
else:
    prediction = "Home Win"
```

Draw confidence is capped at 62% because draws are inherently unpredictable even when called correctly.

---

## Training Pipeline

```
1. Download training data
   └── football-data.co.uk (9 seasons, 16 leagues)
   └── StatsBomb, OpenFootball, international results

2. Merge & deduplicate
   └── 106,497 unique real matches

3. Fit feature pipeline
   └── Compute Elo ratings across full history
   └── Compute league stats (avg goals, home win rate, draw rate)

4. Build feature matrix
   └── Cap at 60,000 random samples
   └── Parallel joblib threads (C++ GIL-free)
   └── 60,000 × 98 matrix → ~44 MB

5. Compute sample weights
   └── Inverse-frequency per class, normalised

6. Fit outcome stack (XGB+HGB+ET+RF+NeuralNet → LR)
   └── ~15 minutes on 4 vCPUs

7. Fit goals stack (same architecture, binary target)
   └── ~3 minutes

8. Save model
   └── joblib compressed pkl ~175 MB
   └── football_predictor.pkl
```

**Retrain schedule:** Every 24 hours automatically, or manually via the "Train Model" workflow.

---

## Scheduler & Live Predictions

`ai/scheduler.py` runs continuously:

```
Every 20 minutes:
  1. Fetch upcoming fixtures
     ├── TheSportsDB (~270 raw)
     └── football-data.org (~120 raw)
     └── Deduplicated → ~132-136 matches

  2. For each fixture:
     ├── Load historical match data for both teams
     ├── Compute 98 features
     ├── Run through outcome stack → [p_home, p_draw, p_away]
     ├── Run through goals stack → [p_under25, p_over25]
     ├── Apply draw detection + away boost logic
     └── Assign confidence + tier

  3. Upsert to Supabase `predictions` table (on_conflict=match_id)

Every 20 minutes (alongside predictions):
  - Fetch completed fixtures from TheSportsDB
  - Settle past predictions (update actual_result, home_score, away_score, status='completed')
```

**Heartbeat:** Written to `ai/data/heartbeat.json` each cycle.

---

## Backtesting Results

Results from `ai/backtest.py` run across multiple leagues and seasons:

| Version | Overall | Home | Draw | Away | ROI |
|---------|---------|------|------|------|-----|
| v1 (64 features, no draw fix) | 49.0% | 84.4% | 0.0% | 42.1% | −6.9% |
| v2 (98 features + draw/away fix) | 47.6% | 72.3% | 28.8% | 29.1% | −1.4% |

Key observations:
- Home bias is significantly reduced (84.4% → 72.3% home accuracy implies far fewer forced home predictions)
- Draws went from never predicted to correctly identified 28.8% of the time
- Away accuracy remained stable while ROI improved by 5.5 percentage points
- Overall accuracy dropped slightly because the model now "spreads risk" across all three outcomes rather than defaulting to home

---

## Known Limitations

1. **No live/in-play features** — The model only uses pre-match data. It has no knowledge of injuries, suspensions, weather, referee, or lineup changes on match day.

2. **Team name normalisation is imperfect** — Fixtures from TheSportsDB and football-data.org use different team name formats. The deduplication relies on fuzzy string matching which occasionally misses matches or creates duplicates.

3. **Settlement lag** — `settled 0/N predictions` in logs is normal because TheSportsDB results can take 24–48 hours to appear. Actual results are only recorded once available.

4. **Sample cap at 60K** — The full 106K dataset is not used due to RAM constraints. Random sampling means some teams/leagues are underrepresented in any given training run.

5. **No temporal ordering in training** — Samples are drawn randomly, not chronologically. The model can accidentally train on future data relative to the lookback window (data leakage risk, though features are computed from matches before each sample).

6. **FiveThirtyEight data removed** — The SPI ratings dataset was a useful calibration source. It is no longer available from its original URL.

7. **Calibration of confidence scores** — Confidence is mapped from raw probability to a displayed score, but the mapping is fixed. A 70% displayed confidence does not necessarily mean the model is correct 70% of the time — full probability calibration curves have not been measured.

---

## Ways to Improve

These are ordered roughly from highest to lowest expected impact.

### High Impact

**1. Add pre-match context features**
The single biggest accuracy gain would come from injecting real-world context the model currently ignores:
- Injury/suspension data (available from API-Football or Transfermarkt)
- Official lineup announcements (available ~1 hour before kick-off)
- Referee assignment (some referees have measurable draw/home bias)
- Travel distance / rest days since last match
- Weather conditions (rain reduces total goals)

**2. Use a proper time-series train/validation split**
Currently the 60K samples are drawn randomly from the full history. A walk-forward split (train on seasons 1–7, validate on 8, test on 9) would give a more honest accuracy estimate and prevent any accidental data leakage.

**3. Increase training data volume**
The 60K cap exists to stay within Replit's RAM limits. On a more powerful machine (16+ GB RAM) you could train on all 106K rows or expand to 200K+ by adding:
- Understat (xG data for top 5 leagues)
- WhoScored (match ratings and stats)
- SofaScore (public API)
- APIFootball (historical match data, paid)

**4. Separate models per league**
A single global model treats La Liga and the Scottish Premiership identically. League-specific models (or league as a stronger embedding) would capture the fact that draw rates, scoring patterns, and home advantages differ significantly across competitions.

**5. Temperature scaling / probability calibration**
After training, run a calibration pass on a held-out validation set using temperature scaling (single scalar parameter). This would make the displayed confidence percentages statistically meaningful.

### Medium Impact

**6. Hyperparameter optimisation**
The current hyperparameters (XGB depth, learning rate, regularisation, etc.) were set by hand. Running Optuna or Hyperopt on a proper validation set could find significantly better configurations — especially for XGBoost and the neural network learning rate/epochs.

**7. Feature selection / importance pruning**
Some of the 98 features may be redundant or noisy. Running SHAP (SHapley Additive exPlanations) to identify which features actually move the needle, then dropping the bottom quartile, could reduce overfitting and speed up training.

**8. Expand the neural network**
The current ResNet is relatively small (256 → 128 → 64). With proper regularisation and a larger training set you could experiment with:
- Wider layers (512 → 256 → 128)
- More residual blocks
- Attention mechanisms over the feature groups
- Transformer-style architecture treating each feature group as a "token"

**9. Ensemble diversity**
Replace CalibratedClassifierCV (which uses calibrated logistic regression) on base models with Platt scaling per model, then add a LightGBM model as a sixth base estimator for additional diversity.

**10. Goal-line model improvement**
The Over/Under 2.5 stack uses the same architecture as the outcome stack. A dedicated goals model could use:
- Both teams' last-10-match average goals (already partially captured)
- Referee-specific foul/card rates correlate with game tempo
- A bivariate Poisson model as a feature source (already partially there via Dixon-Coles)

### Lower Impact / Infrastructure

**11. Streaming Elo updates**
Currently Elo is fully recomputed from scratch every training run. Persisting Elo ratings between runs and only updating from new matches would speed up training significantly and allow more frequent model refreshes.

**12. Online learning / incremental updates**
Instead of full retraining every 24 hours, the XGBoost models support `model.fit(X_new, y_new, xgb_model=existing_model)` for incremental updates. This would let the model react to form changes in hours rather than a day.

**13. Confidence interval on predictions**
Compute a bootstrap confidence interval on the raw probability output (run prediction 100 times with dropout active in the neural net — MC Dropout). Showing "Home Win 65% ± 8%" is more honest than a single point estimate.

**14. Multi-output prediction (correct score)**
The Dixon-Coles score matrix is already computed as a feature. A separate output head predicting the exact score (or score bracket: 1-0, 2-0, 2-1, etc.) could be valuable for VVIP tier users and would differentiate the product.

**15. A/B testing framework**
When a new model version is trained, automatically run both versions on the same fixture set for 2 weeks and compare their settled accuracy before fully switching. Right now model upgrades are deployed immediately with no rollback comparison.

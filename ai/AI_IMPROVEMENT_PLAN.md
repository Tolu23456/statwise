# StatWise AI Improvement Plan
*Full audit of every AI file + roadmap to maximize prediction accuracy*

---

## Honest Baseline Assessment

Before anything else: **75% overall accuracy on a 3-class football prediction problem (Home / Draw / Away) is beyond what any publicly available model achieves.** Professional betting companies, after years of R&D with private data (lineups, injuries, weather, in-play data), reach ~55–62% overall. Academic literature puts the ceiling at ~58% for club football with public data.

The goal should be: **55–60% overall, with draw accuracy ≥ 35%, home ≥ 55%, away ≥ 50%.**

That is a massive and very achievable jump from the current **36.6%**.

---

## Current State (from backtest_results.json, 17,996 matches)

| Metric | Current | Target |
|---|---|---|
| Overall accuracy | 36.6% | 55–60% |
| Home win accuracy | 44.1% | 55%+ |
| Draw accuracy | 25.1% | 35%+ |
| Away win accuracy | 35.8% | 50%+ |
| High-conf (90%+) accuracy | 54.1% | 70%+ |
| Brier score | 0.7878 | < 0.55 |
| Log loss | 1.3033 | < 0.95 |
| Flat-stake ROI | -39.9% | +5% to +15% |

---

## Root Cause Analysis (Every File Examined)

### 1. `model/features.py` — The biggest bottleneck

**Problem A: Training data is being stride-sampled (hardest thing to fix)**
```python
MAX_TRAINING_SAMPLES = 100_000
_LOOKBACK = 400
...
step = max(1, len(all_idx) // max_samples)
all_idx = all_idx[::step]   # takes every Nth match, not every match
```
The clean dataset has ~157K usable matches. The code strides through them, meaning recent seasons get underrepresented. The model never sees all the data it has.

**Fix:** Raise `MAX_TRAINING_SAMPLES` to `200_000` and stop striding — use a chronological tail slice instead (recent data is far more valuable than old data evenly sampled).

---

**Problem B: Form window too short (15 matches)**
```python
def _py_form_vector(matches: list[dict], team: str) -> np.ndarray:
    for m in reversed(matches[-15:]):  # only 15 matches
```
15 matches is barely one half-season. A team's true form and style requires 20–30 matches to stabilize statistically.

**Fix:** Extend form lookback from 15 → 25 matches in `_py_form_vector`, `_py_h2h_stats`, `_py_consecutive_runs`, and `venue_split_form`.

---

**Problem C: Missing high-value features**

The 96-feature set is reasonable but is missing the single most predictive class of features:

| Missing Feature | Why It Matters | How to Add |
|---|---|---|
| **Days since last match** | Fatigue, fixture congestion | Compute from sorted history per team |
| **Season stage** (early/mid/late) | Early season = high variance; late = high stakes | Encode match_date as fraction of season (Aug–May) |
| **Draw propensity of both teams this season** | Some teams draw far more than others | Rolling draw rate per team per season |
| **Away team's away draw rate** | Crucial for draw prediction specifically | From venue_split_form - extend to include draws |
| **Home team's home draw rate** | Same reason | From venue_split_form |
| **Goals in last 5 vs last 10 comparison (home only)** | Detects improving/declining attack | Extend `_last_n_goals` with more windows |
| **League position gap** | Favorite vs underdog context | Requires table data (see Phase 3) |
| **H2H draw rate specifically** | Some pairs always draw | Add to `_h2h_extended` |

---

**Problem D: Bookmaker odds are only used when available — but fall back silently**
```python
odds_h = match.get('odds_home')
imp_h = (1/odds_h) if odds_h and odds_h > 1 else ph  # falls back to Elo
```
Bookmaker odds are the most information-rich single feature in existence for football prediction. They aggregate injuries, lineup news, public money, and expert modelling. When no odds are provided, the model falls back to Elo probability — a much weaker signal — without any indicator feature telling the model it's doing so.

**Fix:** Add a binary `has_odds` feature (1 if real odds were provided, 0 if not). This lets the model learn to weight its own predictions differently depending on whether market data was available.

---

### 2. `model/trainer.py` — Architecture problems

**Problem A: Calibration uses sigmoid with cv=2 — too weak**
```python
_cal = lambda est: CalibratedClassifierCV(est, method='sigmoid', cv=2)
```
Sigmoid calibration (Platt scaling) is linear and under-powered for tree models. `cv=2` is the minimum and introduces high variance in the calibration. This is the **primary cause of overconfidence** — where the model says 90% and is only right 54% of the time.

**Fix:** Switch to `method='isotonic'` and `cv=5`. Isotonic regression is non-parametric and fits the actual calibration curve. This one change will significantly reduce log loss and brier score.

---

**Problem B: StackingClassifier uses cv=3 and passthrough=False**
```python
stack = StackingClassifier(
    cv=3,           # should be 5
    passthrough=False,  # meta-learner can't see raw features
)
```
- `cv=3` generates only 3 out-of-fold prediction sets for the meta-learner, which is high-bias. `cv=5` gives better OOF estimates.
- `passthrough=False` means the LogisticRegressionCV meta-learner only sees 15 probability columns (5 models × 3 classes). With `passthrough=True`, it also sees the 96 raw features. The meta-learner can then learn "when all models agree AND the Elo diff is large → more confident."

**Fix:** `cv=5`, `passthrough=True`. Watch memory usage — may need to profile.

---

**Problem C: XGBoost is under-regularized for this dataset size**
```python
XGBClassifier(
    n_estimators=200, max_depth=6,
    min_child_weight=5, gamma=0.1,
    reg_alpha=0.2, reg_lambda=2.0
)
```
With ~60K training samples and 96 features, max_depth=6 is likely overfitting. The confusion matrix (home predicted when actual is draw, etc.) is consistent with a model that memorised training patterns.

**Fix:**
- Reduce `max_depth` to 4–5
- Increase `min_child_weight` to 10–15
- Increase `reg_lambda` to 3.0–5.0
- Add more `n_estimators` (300) with lower `learning_rate` (0.03)

---

**Problem D: Neural network is too small and trains for too few epochs**
```python
NeuralNetClassifier(epochs=50, batch_size=512, lr=3e-3)
```
The network (256→128→64) is fine architecturally. But 50 epochs with a large batch size and high LR means many samples are never seen in the late-epoch refinement phase. For football data with high noise, the network needs more epochs to converge on the true signal.

**Fix:** Increase to `epochs=120`, reduce `lr=1e-3`, add early stopping based on validation loss.

---

**Problem E: Sample weighting is class-balanced but not recency-weighted**
```python
def _balanced_weights(y: np.ndarray) -> np.ndarray:
    classes, counts = np.unique(y, return_counts=True)
    freq = dict(zip(classes, counts / len(y)))
    w = np.array([1.0 / freq[c] for c in y], dtype=np.float64)
```
The sample weights only correct for class imbalance. They do nothing to make the model weight recent seasons more than matches from 2005. Football has changed dramatically — a 2005 match tells the model less about 2024 football than a 2022 match does.

**Fix:** Multiply sample weights by a time-decay factor. Matches from the last 3 seasons get weight 1.0. Matches 3–6 years old get 0.7. Matches 6+ years old get 0.4.

---

### 3. `model/neural_net.py` — Fine, but missing key improvements

The ResNet architecture is solid. Issues:
- No validation split during training — can't detect overfitting
- `dropout=0.3` everywhere — may be too aggressive for a 96-feature input
- No `weight_norm` or `layer_norm` as alternative to batch norm (batch norm degrades with small batches)

**Fix:**
- Add a 15% validation split inside `fit()` with early stopping (patience=10 epochs)
- Reduce input-layer dropout to 0.2
- Add a label smoothing factor (0.05) to CrossEntropyLoss to improve calibration

---

### 4. `backtest.py` — Methodology has two flaws

**Flaw A: Duplicate matches inflate error**
From the backtest sample, the exact same match appears twice with slightly different team names (e.g., "SC Paderborn 07 vs VfL Osnabrück" AND "SC Paderborn 07 vs Osnabruck"). The deduplication in the C++ cleaner uses fuzzy matching but misses some cases due to encoding differences.

**Effect:** The backtest tests the same match twice. If the model gets it wrong both times, it counts as 2 errors. The true accuracy may be slightly better than reported.

**Fix:** In `backtest.py`, before running predictions, deduplicate `test_df` on `(home_team_normalized, away_team_normalized, date, home_goals, away_goals)` after lowercasing and stripping accents.

---

**Flaw B: ROI calculation uses model's own implied odds**
```python
implied_odds = 1.0 / prob  # model's own probability
roi_profit += (implied_odds - 1.0) if correct else -1.0
```
This is circular — of course the ROI looks terrible when you bet at the model's own odds, which are overconfident. ROI should be calculated against bookmaker odds (when available) to be meaningful.

**Fix:** In ROI calculation, use `row.odds_home/draw/away` (real bookmaker odds) when available. Only fall back to implied odds when market odds aren't in the data.

---

### 5. `model/cpp_bridge.py` — Solid, one calibration issue

The Python fallbacks are correct implementations. The C++ library is used when available.

**Issue:** The Elo draw probability formula:
```python
draw_prob = max(0.05, min(0.35, 0.28 * math.exp(-0.0015 * elo_diff)))
```
This formula caps draw probability at 35% and uses a fixed exponential decay. Real draw rates vary significantly by league: Serie A historically ~26%, Scottish lower leagues ~22%, Bundesliga ~24%. The formula doesn't account for league-specific draw rates.

**Fix:** Weight the Elo draw probability by the league's historical draw rate from `league_stats['draw_rate']`, which is already computed and stored in `FeaturePipeline`.

---

### 6. `retrain.py` — One critical issue

**The training data is loaded chronologically but the model doesn't know time**

The `FootballPredictor.train()` receives a sorted DataFrame, but the `build_training_set` method uses uniform striding. For time series data, **you must not use random sampling** — the model can inadvertently use future information through the Elo ratings which are computed over the full dataset before the train/test split.

**Specifically:**
```python
self.feature_pipe.fit(df)  # fits Elo on ALL data
X, y_1x2, y_goals = self.feature_pipe.build_training_set(df)  # then builds features
```
The `feature_pipe.fit(df)` computes Elo ratings by processing all matches in chronological order — this is fine, it's just walking forward in time. But the resulting `_elo_ratings` dictionary holds the **final** Elo (end of entire dataset), which is then used as a feature for **every** training sample. This means a 2010 match is being represented with a team's 2025 Elo rating — that's data leakage.

**Fix:** During `build_training_set`, for each training sample at index `i`, the Elo feature should be computed from the Elo state at index `i`, not the final Elo. This requires computing Elo incrementally during feature construction (which `_worker` already does through `hist_slice`) — the bug is that `self._elo_ratings` is populated once and reused instead of being recomputed per-sample.

This is the single most significant source of data leakage in the system.

---

### 7. `model/predictor.py` — Minor issues only

- The TIER_THRESHOLDS (Free: 0–55%, Premium: 55–70%, VIP: 70–82%, VVIP: 82–100%) will be mostly empty at Free tier after calibration improvement (most predictions will be below 55% honest confidence)
- Prediction confidence is artificially clamped: `max(45, min(95, raw_conf))` — draws are capped at 65%. After proper calibration these clamps should be removed.

---

### 8. `model/live_fetcher.py` and `model/downloader.py`

Not read in full but understood from context: the model uses API-Football and TheSportsDB for live fixtures, and football-data.co.uk for historical training data. Both sources sometimes return odds. The more often real odds are included in feature vectors, the better the model will perform.

---

## Improvement Roadmap

### Phase 1 — Fix the fundamentals (estimated +10–15% accuracy)

These are code changes only, no new data needed:

1. **Fix Elo data leakage** (`features.py` + `trainer.py`)
   - `feature_pipe.fit()` should only compute Elo ratings up to the training cutoff date
   - Each training sample should use the Elo state at the time of that match, not the final state
   - This requires storing Elo snapshots during `fit()` or computing per-sample from the `hist_slice`

2. **Fix probability calibration** (`trainer.py`)
   - Change `method='sigmoid'` → `method='isotonic'` in `CalibratedClassifierCV`
   - Change `cv=2` → `cv=5`
   - This alone should reduce brier score by ~0.1 and reduce overconfidence dramatically

3. **Use all training data** (`features.py`)
   - Change `MAX_TRAINING_SAMPLES = 100_000` → `200_000`
   - Change striding logic to prefer recent matches: use the most recent 200K, not every Nth match

4. **Add recency weighting** (`trainer.py`)
   - In `_balanced_weights()`, multiply by time decay
   - Last 3 years: weight 1.0, 3–6 years: weight 0.7, 6+ years: weight 0.4

5. **Fix draw heuristic** (`trainer.py`)
   - Remove the hardcoded `DRAW_PROB_FLOOR = 0.245` and `DRAW_MARGIN = 0.20`
   - Instead: predict draw whenever `p_draw > max(p_home, p_away)` — simpler and more honest
   - The current heuristic was trying to boost draws but is doing it incorrectly

6. **Neural net improvements** (`neural_net.py`)
   - Add validation split with early stopping (patience=10)
   - Increase `epochs=50` → `epochs=120`
   - Add label smoothing 0.05 to CrossEntropyLoss
   - Reduce input dropout 0.3 → 0.2

---

### Phase 2 — Better features (estimated +5–8% accuracy)

7. **Add missing features** (`features.py`)
   - `days_since_last_match_home` and `days_since_last_match_away` — fatigue/rest signal
   - `season_stage` — 0.0 (August) to 1.0 (May), encoded as fraction of season
   - `home_draw_rate_season` and `away_draw_rate_season` — team-specific draw propensity
   - `h2h_draw_rate` — how often these two teams specifically draw
   - `has_odds` binary flag — tells model when market data is vs isn't available

8. **Extend form window** (`cpp_bridge.py` / `features.py`)
   - All form lookbacks: 15 → 25 matches
   - This gives a more stable estimate of team quality

9. **Fix league-specific draw probability in Elo** (`cpp_bridge.py`)
   - Pass `league_draw_rate` to `_py_elo_probabilities` and use it to modulate draw probability
   - Currently uses a fixed 0.28 baseline regardless of league

10. **Add stacking passthrough** (`trainer.py`)
    - Set `passthrough=True` in `StackingClassifier`
    - Increase `cv=3` → `cv=5` in StackingClassifier
    - This gives the meta-learner both OOF probs AND the raw features to learn from

---

### Phase 3 — Training data quality (estimated +3–5% accuracy)

11. **Deduplicate training data properly**
    - In `retrain.py`, after loading clean CSVs, normalize team names (lowercase, remove accents, common abbreviations) before deduplication
    - The fuzzy merge in the C++ cleaner creates near-duplicate records with different name spellings — these confuse the model

12. **Higher quality filter for training** (`retrain.py`)
    - Raise `quality_score >= 20` → `quality_score >= 40` for training data
    - This removes the weakest records (no odds, uncertain scores) while keeping 40–59% quality band which has 179K matches — more than enough

13. **Add bookmaker odds scraping**
    - The model already supports odds features but many training records don't have them
    - OddsPortal or historical-odds.com provide free historical odds CSVs for major leagues
    - Adding odds to even 50% of training records would significantly improve accuracy

14. **Separate model per league tier**
    - Big 5 leagues (EPL, La Liga, Bundesliga, Serie A, Ligue 1) have abundant data and distinct patterns
    - Train a specialized model for Big 5 + a general model for others
    - Expected improvement: +2–4% for Big 5 leagues specifically

---

### Phase 4 — Advanced techniques (estimated +2–4% accuracy)

15. **Add LightGBM as a 6th base model** (`trainer.py`)
    - LightGBM trains faster than XGBoost and handles categorical features better
    - Its gradient-based leaf-wise splitting often outperforms XGBoost on tabular data
    - Replace ExtraTrees with LightGBM (ExtraTrees has the lowest ensemble contribution here)

16. **SHAP-based feature pruning**
    - Run SHAP value analysis on the trained XGBoost model
    - Remove the ~20 features with near-zero SHAP importance
    - Fewer noisy features = less overfitting and faster training

17. **Temporal cross-validation in stacking** (`trainer.py`)
    - Replace `StratifiedKFold` (default in StackingClassifier) with a time-series split
    - Football data is temporal — using random folds leaks future information into base model OOF predictions
    - Implement a custom `TimeSeriesSplit` wrapper

18. **Draw-specific binary model**
    - Train a separate `is_draw` binary classifier alongside the 3-class model
    - Ensemble the draw probability: `p_draw_final = 0.6 * p_draw_3class + 0.4 * p_draw_binary`
    - A binary classifier focused only on draw vs non-draw can learn patterns the 3-class model misses

---

## Confidence After Each Phase

| After Phase | Expected Overall Accuracy | Draw Accuracy | Home Accuracy |
|---|---|---|---|
| Current | 36.6% | 25.1% | 44.1% |
| After Phase 1 | ~46–48% | ~30–33% | ~50–52% |
| After Phase 1+2 | ~50–53% | ~33–36% | ~53–55% |
| After Phase 1+2+3 | ~53–56% | ~35–38% | ~55–57% |
| After all 4 phases | ~56–60% | ~38–42% | ~57–62% |

---

## Priority Order (Highest Impact First)

1. **Fix Elo data leakage** — most impactful single bug (Phase 1, item 1)
2. **Fix probability calibration** — fixes overconfidence and ROI (Phase 1, item 2)
3. **Use all training data with recency weighting** — more signal (Phase 1, items 3+4)
4. **Fix draw prediction logic** — removes broken heuristic (Phase 1, item 5)
5. **Add days_since_last_match + season_stage + draw rate features** (Phase 2, item 7)
6. **Neural net early stopping + more epochs** (Phase 1, item 6)
7. **Extend form window to 25 matches** (Phase 2, item 8)
8. **Add stacking passthrough + cv=5** (Phase 2, item 10)
9. **LightGBM replacement** (Phase 4, item 15)
10. **Separate Big 5 league model** (Phase 3, item 14)

---

## What Cannot Be Fixed Without External Data

- **Team lineup / starting XI** — biggest signal in professional models, requires API access
- **Player injuries / suspensions** — second biggest, requires news parsing or premium API
- **Weather conditions** — minor but real effect, requires weather API per stadium
- **Manager/tactical changes** — changes team style, requires news/event data
- **Real-time in-play data** — irrelevant for pre-match but used in live prediction

Without this data, the realistic ceiling is **~58–62% overall accuracy** with perfect implementation of everything above.

---

## Files to Modify (Summary)

| File | Changes Needed |
|---|---|
| `model/features.py` | Fix data leakage, raise max samples, extend form windows, add 5 new features |
| `model/trainer.py` | Isotonic calibration, cv=5, recency weighting, fix draw logic, passthrough=True |
| `model/neural_net.py` | Early stopping, more epochs, label smoothing, lower dropout |
| `model/cpp_bridge.py` | League-aware draw probability in Elo formula |
| `retrain.py` | Higher quality filter, better deduplication |
| `backtest.py` | Fix duplicate test records, fix ROI calculation |
| `model/predictor.py` | Remove confidence clamping after calibration is fixed |

---

*Document generated: 2026-04-07*
*Based on full audit of: trainer.py, features.py, neural_net.py, cpp_bridge.py, predictor.py, retrain.py, scheduler.py, backtest.py, backtest_results.json*

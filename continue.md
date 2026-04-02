# Agent Continuation Notes — StatWise AI

## Current State (as of last session)

Training is actively running inside the **"Train Model"** workflow. Watch its console tab for progress. When training finishes the `.pkl` will be at `ai/models/football_predictor.pkl`.

---

## What Was Built This Session

### 1. Multi-Source Data Pipeline (`ai/model/open_sources.py`)
Fetches from 4 supplementary open datasets (in addition to football-data.co.uk which lives in `ai/model/downloader.py`):
- Source 2: StatsBomb open data (GitHub raw JSON) — **working** (836 matches fetched)
- Source 3: OpenFootball (openfootball/football.json GitHub) — **404 errors, needs URL fix**
- Source 4: FiveThirtyEight SPI CSV — **CSV parse error, needs fix**
- Source 5: Club Elo API — **timing out on some clubs, acceptable fallback**

### 2. Background Retrain Script (`ai/retrain.py`)
Merges all 5 sources, trains the ensemble, atomically replaces the `.pkl`. Run with:
```
python3 ai/retrain.py --force
```

### 3. TabTransformer Neural Network (`ai/model/neural_net.py`)
A PyTorch transformer model for tabular football data:
- Each feature → 64-dim token embedding
- 3 layers of multi-head self-attention (4 heads)
- MLP head (128 → 64 → output)
- sklearn-compatible wrapper (`TabTransformerClassifier`) with `predict_proba()`

### 4. Updated Ensemble in `ai/model/trainer.py`
The `FootballPredictor` now blends 3 models:
- XGBoost: 35% (outcome), 40% (goals)
- HistGradientBoosting: 35% (outcome), 40% (goals)
- TabTransformer: 30% (outcome), 20% (goals)

Falls back gracefully to 2-model blend if PyTorch isn't available.

### 5. Scheduler Updated (`ai/scheduler.py`)
`retrain_engine()` now calls `retrain.run()` (the multi-source pipeline) instead of the old single-source retrain.

---

## Immediate Next Steps (do these in order)

### Step A — Fix broken data sources in `ai/model/open_sources.py`

**OpenFootball** — The URL paths are wrong. The repo uses a different structure. Fix the `OF_COMPETITIONS` dict and URL pattern. Correct URLs look like:
```
https://raw.githubusercontent.com/openfootball/football.json/master/2023-24/en.1.json
```
The season folder comes first, then `en.1.json` (England top flight), `de.1.json` (Bundesliga), `es.1.json` (La Liga), `it.1.json` (Serie A), `fr.1.json` (Ligue 1). The JSON schema uses `"rounds"` → `"matches"` → `"score"` with `"ft": [h, a]`.

**FiveThirtyEight** — The CSV URL has changed. Use the GitHub raw file instead:
```
https://raw.githubusercontent.com/fivethirtyeight/data/master/soccer-spi/spi_matches.csv
```
The columns are: `date`, `league_id`, `league`, `team1`, `team2`, `spi1`, `spi2`, `prob1`, `prob2`, `probtie`, `proj_score1`, `proj_score2`, `importance1`, `importance2`, `score1`, `score2`, `xg1`, `xg2`, `nsxg1`, `nsxg2`, `adj_score1`, `adj_score2`.

### Step B — Wait for user to confirm training is done

The user said: *"I will tell you when the AI is done training"*. Do NOT retrain again until they confirm. When they do:

1. Check the "Train Model" workflow logs to confirm it finished without error
2. Check `ai/data/model_info.json` for `trained_at` timestamp and `total_matches`
3. Check if the `.pkl` includes neural net by looking at `use_neural_net` in the saved dict

### Step C — If neural net was NOT included in the finished .pkl

The current training run was started while the `trainer.py` edits were being applied. If the finished `.pkl` doesn't include the TabTransformer (check `ai/data/model_info.json` or the scheduler logs for `[XGBoost + HistGB + TabTransformer]`), immediately trigger a second retrain:
```
# Remove the Train Model workflow first, then re-add and run it
```
Or restart the Train Model workflow.

### Step D — After neural net training is confirmed

Once the `.pkl` includes the TabTransformer, restart the **AI Scheduler** workflow so it reloads the new model. Verify in scheduler logs that it says:
```
Model loaded from .../football_predictor.pkl  [XGBoost + HistGB + TabTransformer]
```

### Step E — Fix the Supabase `odds_away` column error

Every prediction push is failing with:
```
Could not find the 'odds_away' column of 'predictions' in the schema cache
```
This is a pre-existing Supabase schema mismatch. The predictor is trying to push `odds_away` but that column doesn't exist in the `predictions` table. Fix options:
1. Add `odds_away`, `odds_home`, `odds_draw` columns to the Supabase `predictions` table (run the SQL in `statwise/supabase-schema.sql` or `statwise/database_schema.sql`)
2. Or strip those columns from the upsert payload in `ai/model/predictor.py` before pushing

Check `ai/model/predictor.py` → `push_to_supabase()` method and compare against the actual Supabase table schema.

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `ai/model/trainer.py` | Main model class — XGBoost + HistGB + TabTransformer ensemble |
| `ai/model/neural_net.py` | TabTransformer architecture (PyTorch) |
| `ai/model/open_sources.py` | 4 supplementary data fetchers (fix URLs here) |
| `ai/model/downloader.py` | football-data.co.uk fetcher (source 1, working) |
| `ai/retrain.py` | Full multi-source retrain script |
| `ai/scheduler.py` | Runs every 20 min (predictions) + 24 hr (retrain) |
| `ai/models/football_predictor.pkl` | The live model file |
| `ai/data/model_info.json` | Metadata about last training run |
| `ai/data/retrain.log` | Log from background retrain runs |
| `statwise/` | Expo/React Native frontend |

## Workflows

| Workflow | Purpose |
|----------|---------|
| `Start application` | Expo web frontend on port 5000 |
| `AI Scheduler` | Prediction cycle every 20 min, retrain every 24 hr |
| `Train Model` | One-shot training — **currently running** |

After training finishes, remove the `Train Model` workflow (it's one-shot, not persistent):
```javascript
await removeWorkflow({ name: "Train Model" });
```

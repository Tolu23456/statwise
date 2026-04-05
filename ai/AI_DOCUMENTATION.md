# StatWise AI Documentation

> Last updated: April 2026  
> Pipeline version: v3.0 (7 data sources, 48-column schema, 10-phase cleaner)  
> Model version: v2 (98 features, 5-model deep stacking ensemble)

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Data Sources](#2-data-sources)
3. [Data Pipeline (C++ Tools)](#3-data-pipeline-c-tools)
4. [Output Schema](#4-output-schema)
5. [AI Model](#5-ai-model)
6. [Prediction Engine](#6-prediction-engine)
7. [Running the Pipeline](#7-running-the-pipeline)
8. [File Layout](#8-file-layout)

---

## 1. System Overview

StatWise uses a five-model deep-stacking ensemble to predict football (soccer) match outcomes.
Training data comes from seven independent free sources and is processed by a two-stage C++17
pipeline before the Python trainer sees it.

```
Raw Sources (7)
      │
      ▼
[dataset_downloader]  ←── C++17, idempotent, CPU/RAM-throttled
      │
      ▼
ai/data/raw/           ─── per-source subdirectories
      │
      ▼
[dataset_cleaner]     ←── C++17, 10-phase pipeline, Jaro-Winkler dedup
      │
      ▼
ai/data/clean/         ─── YYYY_matches.csv, 48-column unified schema
      │
      ▼
[trainer.py]          ←── Python, 5-model ensemble
      │
      ▼
ai/models/football_predictor.pkl
      │
      ▼
[scheduler.py]        ←── Runs every 20 min, pushes predictions to Supabase
```

---

## 2. Data Sources

All sources are freely available and require no authentication or API keys.

| # | Source | Description | Size | Format |
|---|--------|-------------|------|--------|
| 1 | **football-data.co.uk** | 32 leagues × 32 seasons (1993/94–2024/25) | ~200K rows | CSV per season |
| 2 | **xgabora** (GitHub) | Club matches 2000–2025, 42 leagues + Elo ratings | ~475K rows | Single CSV |
| 3 | **understat** (douglasbc) | Shot-level xG, top 5 EU leagues, 2014–2022 | ~5M shots | CSV per league+season |
| 4 | **martj42/international_results** | Every international match since 1872 | ~47K rows | Single CSV |
| 5 | **jfjelstul/worldcup** | FIFA World Cup 1930–2022, every match + stage | ~1K rows | CSV |
| 6 | **openfootball/football.json** | JSON fixtures, 20 EU competitions, 2011–2025 | ~80K matches | JSON per season |
| 7 | **StatsBomb open-data** | Competition reference metadata | Small | JSON |

**Total downloader tasks:** 1,198 (most are optional — missing season/league combos are logged and skipped gracefully).

---

## 3. Data Pipeline (C++ Tools)

### 3.1 Dataset Downloader v3.0

**Binary:** `build/dataset_downloader`  
**Source:** `ai/tools/dataset_downloader.cpp` (553 lines)

#### Design Principles
- Zero external library dependencies (uses system `curl` via `popen`)
- **Idempotent:** skips files that already exist and meet minimum byte threshold
- **RAM-aware:** pauses if free RAM < 400 MB (reads `/proc/meminfo`)
- **CPU-aware:** sleeps 2s if load > 75% before each file
- **Graceful shutdown:** SIGTERM/SIGINT stops cleanly after the current file
- **Optional tasks:** `football-data.co.uk` season gaps and `openfootball` missing combos are `optional=true` — logged at INFO, not ERROR

#### Source Builders

| Builder function | Output location |
|---|---|
| `build_xgabora_tasks()` | `raw/xgabora/Matches.csv`, `EloRatings.csv`, `Teams.csv` |
| `build_understat_tasks()` | `raw/understat/shots_*.csv`, `players_*.csv` |
| `build_international_tasks()` | `raw/international/results.csv`, `goalscorers.csv`, `shootouts.csv` |
| `build_worldcup_tasks()` | `raw/worldcup/matches.csv`, `goals.csv`, `teams.csv` |
| `build_football_data_tasks()` | `raw/football_data/{slug}_{season}.csv` |
| `build_openfootball_tasks()` | `raw/openfootball/{season}_{comp}.json` |
| `build_statsbomb_tasks()` | `raw/statsbomb/competitions.json` |

#### football-data.co.uk Coverage

**Seasons:** `9394` → `2425` (32 seasons; historic seasons available for main EU leagues only)

| Region | Codes |
|--------|-------|
| England | E0, E1, E2, E3 |
| Spain | SP1, SP2 |
| Germany | D1, D2 |
| Italy | I1, I2 |
| France | F1, F2 |
| Netherlands/Portugal/Scotland/Belgium/Turkey/Greece | N1, P1, SC0-3, B1, T1, G1 |
| Overseas (from ~2012) | ARG, BRA, CHN, DEN, AUT, FIN, IRL, NOR, SWE, SWI, USA, JPN |

---

### 3.2 Dataset Cleaner v3.0

**Binary:** `build/dataset_cleaner`  
**Source:** `ai/tools/dataset_cleaner.cpp` (2,115 lines)

#### Performance Design
- **Thread pool:** `nproc/2` workers (max 6) — parallel file processing
- **CPU governor:** `/proc/stat` sample; sleeps 500ms if load > 70%
- **RAM governor:** `/proc/meminfo`; pauses 8s if free RAM < 400 MB
- **SIGTERM/SIGINT:** finishes current phase cleanly, then writes output

#### 10-Phase Pipeline

| Phase | Action |
|-------|--------|
| **1** | Parse xgabora `Matches.csv` (base dataset, ~475K rows) |
| **2** | Parse all football-data.co.uk seasonal CSVs in parallel |
| **3** | Build understat xG map; inject xG into existing records |
| **4** | Parse martj42 international results (1960–present) |
| **5** | Parse jfjelstul FIFA World Cup matches |
| **6** | Parse openfootball JSON files (custom C++ scanner, no external libs) |
| **7** | Cross-source conflict scan — flag score disagreements |
| **8** | Jaro-Winkler fuzzy dedup — merge near-identical team spellings (≥0.88) |
| **9** | Quality scoring — compute `quality_score` (0–100) per match |
| **10** | Write `YYYY_matches.csv` per calendar year to `ai/data/clean/` |

#### Cleaning Rules Per Row

| Rule | Threshold |
|------|-----------|
| Score | 0–25 goals per side; both sides required |
| Odds | 1.005–400; 3-way implied probability 80–150% |
| Shots | 0–60 |
| Corners | 0–30 |
| Fouls | 0–50 |
| Cards | 0–15 |
| xG | 0.0–12.0 per side |
| Outlier | Z-score on total goals per league stratum; \|z\|>5σ or any side>20 → discard |
| Date formats | DD/MM/YY, DD/MM/YYYY, DD.MM.YYYY, YYYY-MM-DD, YYYY/MM/DD, MM/DD/YYYY |

#### Team Name Normalisation
1,000+ aliases mapped to canonical names. Examples:
- `"Man Utd"`, `"Man United"`, `"Manchester United FC"` → `"Manchester United"`
- `"Bayern"`, `"FC Bayern Munchen"`, `"Bayern Munich FC"` → `"Bayern Munich"`
- `"Internazionale"`, `"FC Internazionale Milano"` → `"Inter Milan"`
- Full country name aliases for international matches

#### Supported Parsers

| Parser | Source | Key fields |
|--------|--------|-----------|
| `parse_xgabora()` | xgabora CSV | date, home/away team, goals, league, country, elo |
| `parse_football_data()` | fd.co.uk CSV | goals, halftime, shots, corners, fouls, cards, full odds suite |
| `parse_understat_shots()` | understat CSV | xG aggregated per match |
| `parse_international()` | martj42 CSV | date, teams, score, tournament, neutral flag |
| `parse_worldcup()` | jfjelstul CSV | date, teams, score, stage, tournament |
| `parse_openfootball_json()` | JSON | date, teams, FT score, halftime score |

---

## 4. Output Schema

Each `ai/data/clean/YYYY_matches.csv` file has **48 columns**:

| Column | Type | Notes |
|--------|------|-------|
| `date` | YYYY-MM-DD | ISO 8601 |
| `home_team` | string | Canonical name |
| `away_team` | string | Canonical name |
| `home_goals` | int | Full-time |
| `away_goals` | int | Full-time |
| `league_slug` | string | e.g. `premier-league`, `world-cup`, `international` |
| `country` | string | |
| `source` | string | `xgabora`, `football_data`, `international`, `worldcup`, `openfootball` |
| `halftime_home` | int | Blank if unknown |
| `halftime_away` | int | |
| `shots_home` | int | |
| `shots_away` | int | |
| `shots_on_target_home` | int | |
| `shots_on_target_away` | int | |
| `corners_home` | int | |
| `corners_away` | int | |
| `fouls_home` | int | |
| `fouls_away` | int | |
| `yellows_home` | int | |
| `yellows_away` | int | |
| `reds_home` | int | |
| `reds_away` | int | |
| `elo_home` | float | |
| `elo_away` | float | |
| `odds_home` | float | Best bookmaker |
| `odds_draw` | float | |
| `odds_away` | float | |
| `max_odds_home` | float | Market maximum |
| `max_odds_draw` | float | |
| `max_odds_away` | float | |
| `avg_odds_home` | float | Closing average |
| `avg_odds_draw` | float | |
| `avg_odds_away` | float | |
| `asian_handicap_line` | float | |
| `asian_handicap_home` | float | |
| `asian_handicap_away` | float | |
| `over25_odds` | float | Over 2.5 goals |
| `under25_odds` | float | Under 2.5 goals |
| `max_over25` | float | |
| `max_under25` | float | |
| `xg_home` | float | From understat shot data |
| `xg_away` | float | |
| `quality_score` | int | **0–100** data completeness score |
| `league_tier` | int | **1**=elite, **2**=second, **3**=third, **4**=lower, **0**=international |
| `is_international` | int | 1 = national teams match |
| `score_conflict` | int | 1 = two sources disagree on FT score |
| `tournament` | string | e.g. `"FIFA World Cup"`, `"UEFA Euro"`, `"Friendly"` |
| `is_neutral` | int | 1 = neutral venue |

Empty cells = value absent or failed validation (never filled with `0`).

---

## 5. AI Model

**File:** `ai/models/football_predictor.pkl`  
**Trainer:** `ai/model/trainer.py`

### Architecture: 5-Model Deep Stacking Ensemble

**Base Learners (Level 1)**

| Model | Library | Key params |
|-------|---------|-----------|
| XGBoost | xgboost | `n_estimators=500, max_depth=6, lr=0.05` |
| HistGradientBoosting | scikit-learn | `max_iter=400, max_depth=8` |
| ExtraTrees | scikit-learn | `n_estimators=300, class_weight=balanced` |
| RandomForest | scikit-learn | `n_estimators=300, class_weight=balanced` |
| PyTorch NeuralNet | pytorch | 4 hidden layers, BatchNorm, Dropout 0.3, class-weighted CrossEntropy |

**Meta-Learner (Level 2)**  
`LogisticRegressionCV` (5-fold CV) trained on out-of-fold base learner probability outputs.

### Features (98 total)

| Group | Count | Description |
|-------|-------|-------------|
| Elo | 4 | Home/away Elo, difference, product |
| Attack/Defence Elo | 4 | Separate Elo tracks for goals scored/conceded |
| Dixon-Coles | 6 | Score matrix with Poisson + rho correction |
| Poisson goal probs | 10 | P(0), P(1), P(2), P(3), P(4) each side |
| H2H history | 8 | Last 5 meetings: results, goals, win% |
| Venue-split form | 12 | Last 3/5/10 home results; last 3/5/10 away results |
| Goals variance | 4 | Rolling variance: scored and conceded |
| Consecutive runs | 6 | Win/draw/loss streak length |
| Odds features | 16 | All odds types + implied probability + AH |
| xG features | 8 | xG per side, difference, over-performance index |
| League/context | 4 | Tier, is_international, is_neutral, matchday |
| Calendar | 4 | Month, weekday, is_weekend, days_since_last_match |
| Shot map | 6 | Shots, SOT, corner ratio |
| Discipline | 6 | Cards, fouls metrics |

### Anti-Bias Rules (applied post-prediction)
- **Sample weights:** inverse class frequency — home 0.74×, draw 1.33×, away 1.12×
- **Draw floor:** predict draw when P(draw)≥0.255 AND |P(home)−P(away)|≤0.14
- **Away boost:** predict away when P(away) ≥ P(home)−0.03
- **Draw confidence cap:** maximum 62% (prevent overconfidence)

---

## 6. Prediction Engine

**Scheduler:** `ai/scheduler.py` — every 20 minutes  
**Predictor:** `ai/model/predictor.py`  
**Live fetcher:** `ai/model/live_fetcher.py`

### Fixture APIs (priority order)
1. **API-Football (RapidAPI)** — `FOOTBALL_API_TOKEN` env var; 129 fixtures fetched per run
2. **football-data.org** — fallback
3. **TheSportsDB** — free fallback

### Flow
```
Fetch fixtures (next 48h, 16+ leagues)
      ↓
98-feature extraction (C++ libstatwise.so + Python fallbacks)
      ↓
5 base learners → out-of-fold probabilities
      ↓
LogisticRegressionCV meta-learner → [P(H), P(D), P(A)]
      ↓
Anti-bias rules → final prediction + confidence
      ↓
Upsert to Supabase `predictions` table (keyed on match_id)
      ↓
Settle past predictions (TheSportsDB actual scores)
```

### C++ Kernel (libstatwise.so) — 11 Exported Functions
- `dixon_coles_matrix` — Poisson + rho-correction score probability matrix
- `attack_defence_elo` — dual-track Elo for attack/defence strength
- `goals_variance` — rolling variance of goals over N matches
- `venue_split_form` — home-only / away-only recent form vectors
- `consecutive_run` — streak length counters
- + 6 more feature calculation functions

---

## 7. Running the Pipeline

### Compile the tools (already compiled in `build/`)
```bash
cd ai/tools && make all
```

### Step 1 — Download all data
```bash
# Runs in background, idempotent, ~2–4 hours on first run
./build/dataset_downloader ai/data/raw

# Force re-download of everything:
./build/dataset_downloader ai/data/raw --force
```

Progress is logged every 50 tasks. Press Ctrl+C to stop gracefully (resumes next run).

### Step 2 — Clean and merge
```bash
./build/dataset_cleaner ai/data/raw ai/data/clean
# Verbose (logs every file):
./build/dataset_cleaner ai/data/raw ai/data/clean --verbose
```

Takes 30–90 seconds after download completes.

### Step 3 — Retrain the model
```bash
python3 -u ai/retrain.py --force
```

### Full pipeline (sequential)
```bash
./build/dataset_downloader ai/data/raw && \
./build/dataset_cleaner ai/data/raw ai/data/clean && \
python3 -u ai/retrain.py --force
```

### Inspecting output
```bash
# Rows per year:
wc -l ai/data/clean/*.csv

# Check 2024 schema:
head -1 ai/data/clean/2024_matches.csv | tr ',' '\n' | nl

# Quality score distribution for 2024:
awk -F',' 'NR>1 {print $44}' ai/data/clean/2024_matches.csv | sort -n | uniq -c
```

---

## 8. File Layout

```
ai/
├── AI_DOCUMENTATION.md          ← this file
├── dataset_links.txt            ← all 7 source URLs with descriptions
├── scheduler.py                 ← live prediction scheduler (every 20 min)
├── retrain.py                   ← full model retrain script
├── libstatwise.so               ← C++ feature kernel (ctypes)
├── model/
│   ├── trainer.py               ← FootballPredictor ML model class
│   ├── predictor.py             ← PredictionEngine (push/settle)
│   └── live_fetcher.py          ← fixture fetching from 3 APIs
├── models/
│   └── football_predictor.pkl   ← serialised trained ensemble
├── data/
│   ├── raw/
│   │   ├── xgabora/             ← Matches.csv, EloRatings.csv, Teams.csv
│   │   ├── football_data/       ← {slug}_{season}.csv
│   │   ├── understat/           ← shots_*.csv, players_*.csv
│   │   ├── international/       ← results.csv, goalscorers.csv, shootouts.csv
│   │   ├── worldcup/            ← matches.csv, goals.csv, teams.csv
│   │   ├── openfootball/        ← {season}_{comp}.json
│   │   └── statsbomb/           ← competitions.json
│   └── clean/
│       └── YYYY_matches.csv     ← 48-column unified schema, one file per year
└── tools/
    ├── dataset_downloader.cpp   ← C++17 downloader (553 lines)
    ├── dataset_cleaner.cpp      ← C++17 cleaner (2,115 lines)
    └── Makefile

build/
├── dataset_downloader           ← ./build/dataset_downloader ai/data/raw
└── dataset_cleaner              ← ./build/dataset_cleaner ai/data/raw ai/data/clean
```

# StatWise data-quality & label-freshness audit

Scope: `ai/data/clean/*.csv` (73 files, 377,399 rows), the refresh pipeline, odds/weather
sidecars, league coverage vs. the live app, and look-ahead in `ai/model/features.py`.
All numbers below were measured with the scripts in `analysis/_dq_*.py`
(sampled / `usecols=` streaming, ≤2.8 GiB RSS, no training, no code changes).
Audit date: **2026-10-25**.

Headline: **labels are clean, but the live feature vector is built from an empty
history frame (103 of 161 features collapse to "no data" defaults), odds coverage
has halved since 2024, and 7 of the 21 leagues the live app fetches are keyed
differently (or not at all) in the training data.**

---

## 1. Freshness — how stale is the training set?

| measurement | value |
|---|---|
| latest match date present | **2026-10-18** |
| age vs today (2026-10-25) | **7 days** |
| rows in 2026 file | 10,007 (`2026_matches.csv`) |
| mtime of all 73 clean files | **2026-10-18 16:50** (identical) |
| future-dated rows (> 2026-10-25) | 0 |
| date-parse failures | 0 |

### Refresh cadence: manual, no cron touches the clean CSVs

`git log -- ai/data/clean` (whole history of the training set):

| commit date | note |
|---|---|
| 2026-04-06 … 2026-04-08 | 5 commits ("Update historical match data…") |
| **2026-10-18** | `c602d42` weather enrichment + regenerated CSVs |
| *(nothing since)* | 7 days and counting |

So the set was last regenerated **193 days** after the previous regeneration, and
`grep -rn "run_pipeline|dataset_cleaner|dataset_downloader" .github/` returns
**nothing** — no scheduled workflow ever runs the data pipeline.

What the scheduled workflows actually refresh:

| workflow | cron | refreshes | feeds training? |
|---|---|---|---|
| `fetch-football-data.yml` | `0 */3 * * *` | fixtures → **Supabase only** (JS writes no CSV) | ✗ |
| `odds-sync.yml` | `0 5,11,17,23 * * *` + `*/5 12-22` | pre-match odds → **Supabase only** | ✗ |
| `settle.yml` | `20 */2 * * *` | result settlement of predictions → Supabase | ✗ |
| `predict.yml` | `0 */4 * * *` | predictions → Supabase | ✗ |
| `retrain-weekly.yml` | `0 3 * * 1` | trains from **committed CSVs**, commits `model_info.json` | trains, doesn't refresh |
| `train-model.yml` | `0 10 * * 1` | same, `make -C ai/core` + train | trains, doesn't refresh |
| `weather-backfill.yml` | `0 4 * * 0` | weather sidecar (20 shards) | sidecar only |

Only `ai/run_pipeline.sh` / `ai/tools/run_pipeline.sh` regenerate `ai/data/clean/`,
and they are run by hand (Replit run-button `.replit` workflow `Data Pipeline`).

**Consequence:** between 2026-04-08 and 2026-10-18 every retrain and every holdout
number was produced from a 6-month-old snapshot; today it is 7 days old, and it will
not move again until somebody runs the pipeline manually.

---

## 2. Label integrity — clean, no action needed

| check | result |
|---|---|
| rows | 377,399 (1930-07-13 → 2026-10-18) |
| `home_goals`/`away_goals` non-integer or empty | **0** |
| negative goals / goals > 20 | 0 / 0 |
| duplicate fixtures (`date\|league\|home\|away`) | **0 keys, 0 rows** |
| exact duplicate rows (incl. score) | 0 |
| same `date+teams` across leagues | 0 |
| rows with future dates | 0 |
| 0–0 rows | 30,578 = 8.1 % (natural rate) |
| 0–0 full-time but non-zero half-time | **0** (no void/postponed leakage) |
| `score_conflict = 1` | 37 rows (0.01 %) |
| status/void column | none — the cleaner only emits finished matches |

0–0 rate by source (a voided-match bug would show up here as an outlier):

| source | rows | 0-0 % | odds % |
|---|---|---|---|
| xgabora | 186,958 | 8.0 | **99.7** |
| football_data | 116,072 | 8.1 | 55.0 |
| international | 44,843 | 8.6 | 0.0 |
| openfootball | 28,958 | 7.8 | 0.0 |
| worldcup | 568 | 4.6 | 0.0 |

### Class balance: mild drift, away wins gaining

| slice | n | H % | D % | A % |
|---|---|---|---|---|
| full history | 377,399 | 45.4 | 26.3 | 28.3 |
| 2024 | 15,154 | 43.9 | 26.5 | 29.6 |
| 2025 | 14,905 | 44.2 | 25.1 | 30.7 |
| 2026 YTD | 10,007 | 44.2 | 25.7 | 30.1 |
| **last 12 months** | 12,900 | **44.4** | **25.4** | **30.2** |
| 2026-08-01 → (current season) | 3,098 | 43.3 | 25.6 | 31.1 |

League-level moves (last 12m vs. history, percentage points):

| league | Δ home-win | Δ draw | Δ away-win |
|---|---|---|---|
| premier-league | **−6.1** | +4.9 | +1.2 |
| serie-a | −4.7 | −4.9 | **+9.6** |
| eredivisie | **−7.0** | +3.8 | +3.2 |
| la-liga-2 | +2.1 | **−7.8** | +5.8 |
| greek-super | −6.8 | +2.6 | +4.1 |
| belgian-pro | −4.6 | −1.1 | +5.7 |
| copa-libertadores | −6.2 | +2.2 | +4.0 |
| ligue1 | −0.8 | −5.1 | +5.9 |

The meta has drifted ~1 pt overall (H −1.0, A +1.9) but **5–10 pts in the marquee
leagues the app publishes most**. Real, but a smaller lever than findings 1–3.

---

## 3. Odds coverage — collapsed by ~26 points since 2024

Fraction of rows with **all three** of `odds_home/draw/away > 1.01`
(`avg_odds_*` is identical in practice — the cleaner prefers closing consensus):

| year | rows | odds % |
|---|---|---|
| 2012–2019 | ~11–16k/yr | 72 – 86 |
| 2020–2023 | ~13–17k/yr | 76 – 85 |
| 2024 | 15,154 | **75.8** |
| 2025 | 14,905 | **49.5** |
| 2026 | 10,007 | **54.1** |

Recent months:

| month | rows | odds % | | month | rows | odds % |
|---|---|---|---|---|---|---|
| 2025-03 | 1,645 | 47.7 | | 2026-04 | 1,501 | 57.8 |
| 2025-05 | 1,370 | 39.7 | | 2026-05 | 1,126 | 46.8 |
| 2025-06 | 386 | **2.6** | | 2026-06 | 225 | **0.0** |
| 2025-07 | 393 | **2.0** | | 2026-07 | 251 | **0.4** |
| 2025-09 | 1,514 | 47.1 | | 2026-08 | 1,172 | 51.6 |
| 2025-12 | 1,017 | 74.9 | | 2026-09 | 1,313 | 50.3 |
| 2026-01 | 1,087 | 75.6 | | **2026-10** | 613 | **38.2** |

### By league, last 12 months vs. history (training rows)

| league | last 12m % | history % |
|---|---|---|
| league-two / conference-national / scottish-prem / league-one / championship | 98 – 100 | 77 – 99 |
| premier-league | 92.1 | 75.0 |
| serie-a, bundesliga, ligue1, eredivisie, serie-b, la-liga | 67 – 87 | 65 – 75 |
| **mls, primera-division (ARG), serie-a-br, j-league, liga-mx, chinese-super, russian-premier, norwegian-elit, swedish-allsv, austrian-bl, danish-superliga, irish-prem** | **0.0** | 78 – 94 |
| **champions-league** | **0.0** | **0.0 (0/539 ever)** |
| **copa-libertadores** | **0.0** | **0.0 (0/613 ever)** |

### Root cause (source composition)

| source | ≤2015 | 2016-2024 | 2025 | 2026 |
|---|---|---|---|---|
| xgabora (99.7 % odds) | 117,387 | 68,958 | **295** | **318** |
| football_data (55 % odds) | 56,215 | 40,235 | 11,419 | 8,203 |
| openfootball (0 % odds) | 6,539 | 19,545 | 2,189 | 685 |
| international (0 % odds) | 20,691 | 8,661 | 1,002 | 801 |

The odds-rich source (`xgabora/Matches.csv`, 475k rows upstream, ends 2025) stopped
contributing after early 2025; recent rows come from football-data.org / season CSVs
that only carry odds for the English + a few football-data.co.uk leagues.
There is **no odds backfill into the training set** — `odds_sync.py` writes odds to
Supabase for live use only.

This matters twice over: (a) `has_odds` + `odds_implied_*` + `market_*` (features
40/62-65/103-105) are zero for half the recent training rows while live rows almost
always have odds; (b) the holdout measured `market_rows = 1,282 / 2,250` (57 %) and
the alpha blend (`alpha_default = 0.35`, only **8 leagues** have a fitted alpha) was
tuned on that non-representative slice.

---

## 4. Sidecar joins

### `weather_by_match.csv.gz`

| metric | value |
|---|---|
| sidecar rows (dedup on `date+home_team`) | 37,888 (10.0 % of 377,399) |
| distinct home teams in sidecar | **261** (vs 1,415 home teams in the 2024-26 files) |
| sidecar date range | 2000-01-03 → 2026-10-18 |
| **coverage of training rows (post-filters)** | **7.7 %** |
| coverage, last 12 months | **8.4 %** |
| coverage, current season (2026-08+) | 7.4 % |
| coverage, Oct 2026 | 6.5 % |

Coverage is **league-clustered**, not random (last 12 months, training rows):

| league | wx % | league | wx % |
|---|---|---|---|
| greek-super | 63.6 | serie-a | 30.6 |
| belgian-pro | 44.7 | scottish-prem | 26.9 |
| serie-b | 23.1 | danish-superliga | 27.9 |
| primeira-liga | 29.6 | norwegian-elit | 23.9 |
| **premier-league / la-liga / bundesliga / ligue1 / championship / mls / j-league / serie-a-br / liga-mx** | **0.0** | | |

Of 693 home teams with >150 training rows: **624 have 0 % weather, 25 have 100 %**.
The 20-shard `weather-backfill.yml` (added 2026-10-25, cron Sundays) is mid-flight —
weather exists for only **261 home teams** of the ~1,415 home teams in the recent files
(the workflow targets ~941 cities in total).

### Slot 140 `w_present`

Mechanically correct in both paths:
* training `features.py::build_training_set` line 240-241 sets `wb[:,7] = ~isnan(temp)`
  and zeroes the whole weather block when temp is missing;
* inference `features.py::weather_features` returns zeros (incl. `w_present=0`) when no
  forecast is supplied.

But it is **not distributionally sensible**:

| | `w_present=1` |
|---|---|
| training (all) | 7.7 % |
| training, Premier League / La Liga / Bundesliga / Ligue 1 / MLS last 12m | **0 %** |
| live inference (venue resolved 99.8-100 %, forecast fetched every cycle) | **~100 %** |

So at live time every fixture looks like the 8 % of rows that, in training, were almost
exclusively Greek/Belgian/Italian clubs — `w_present` has effectively learned to be a
league identifier, and `w_temp/w_precip/w_wind/w_code` are non-zero where training saw
zeros for those leagues.

### `team_venues.json`

| metric | value |
|---|---|
| entries | 2,253 |
| training teams resolvable | 1,870 / 1,874 = **99.8 %** |
| 2026 teams resolvable | 757 / 758 = 99.9 % |
| training rows with resolvable home venue | **100.0 %** (last 12m too) |

Venue data is in good shape — `travel_present` (145) is 1.0 in training and would be
1.0 live, **except** that at live inference the travel block is computed from an empty
history (finding 1), which forces it to 0 anyway.

---

## 5. Look-ahead / leakage review

| site | verdict |
|---|---|
| `features.cpp:312,315` team-history windows | **safe** — `if (mid < idx)` filters to strictly prior rows |
| `features.cpp:325` `mg` (current goals) passed into `compute_all_features_v6` | **safe** — `mg` is never read by v3/v4/v6 |
| `features.py::fit` pre-match Elo loop (lines 127-142) | **safe** — pre-match snapshot taken before the update, df sorted by date (`retrain.py:213`) |
| `form.py::compute_form_block` | **safe** — `groupby(team).shift(1)` then rolling, prior matches only |
| `travel.py::compute_travel_block` | **safe** — `shift(1)` on previous venue |
| `features.py::build_training_set` `all_idx = range(20, total)` | safe |
| **`retrain.py:220` `predictor.feature_pipe.fit(df)`** | **LEAK** — fits on the *full* frame including the 180-day holdout, so `_league_stats` (avg_goals, home_win_rate, draw_rate …) fed to features `league_avg/league_h_wr/league_draw` (slots 82-84) contain holdout labels. Small magnitude (holdout ≈ 2.5 k of 332 k rows) but it makes the reported holdout optimistic. |
| **`features.py::build_features` `get_hist()` / `h2h`** | **latent leak** — no `date < kickoff` filter on the history frame. Harmless today only because history is *empty* (finding 1); becomes real the moment history is passed in and contains rows after kickoff (e.g. upcoming fixtures with NaN scores). |

So: no material look-ahead in the rolling/groupby windows — holdout numbers are *not*
inflated by windowing. The optimism comes from the league-stats fit (small) and, far
more importantly, from the fact that **holdout rows are scored with real history while
live rows are scored with none** (§6.1).

---

## 6. Ranked findings and exact fixes

### 1. 🔴 Live inference runs with an **empty history frame** — 103/161 features collapse
*Impact: highest — this is a systematic train/serve mismatch on every fixture.*

`PredictionEngine.history` is only assigned in `load_or_train()`'s *retrain* branch
(`ai/model/predictor.py:124`), and `build_engine()` (`ai/scheduler.py:146`) /
`main_once()` load the pickle and return early — so `self.history` is an empty
`pd.DataFrame()`. `run()` then passes it straight into the feature pipeline
(`ai/model/predictor.py:187`), where `predict_match()` replaces it with
`pd.DataFrame(columns=…)` (`ai/model/trainer.py:470`).

Measured on a real fixture (Beijing Guoan vs Qingdao West Coast) with a pipeline fitted
on 2024-2026 rows (`analysis/_dq_feats.py`):

| | with history (training-equivalent) | with the empty frame the live path passes |
|---|---|---|
| features that change | — | **103 / 161** |
| features outside training [p5, p95] | **19** | **55** |
| `home_win_rate` / `home_ppg` / `home_goals_scored` | 0.607 / 0.733 / 1.972 | **0 / 0 / 0** |
| `away_win_rate` / `away_ppg` | 0.251 / 0.389 | **0 / 0** |
| `lambda_h` / `lambda_a` / `p_over25` | 0.418 / 0.503 / 0.066 | **0.100 / 0.100 / 0.001** |
| `p_0_0` | 0.409 | **0.820** |
| `ha_strength` / `h_att_vs_lg` | 1.250 / 0.105 | **0 / −1.0** |
| `h2h_n_matches` / `home_streak` / `days_h` | 6 / 0.40 / 1.000 | **0 / 0 / 0.117** |
| `travel_present` / `form_present` | 1.000 / 1.000 | **0 / 0** |

Everything derived from history — form, PPG, H2H, streaks, Poisson lambdas, venue
split, goal variance, rest days, travel, last-5 form — arrives at LightGBM at its
"team has never played" default, which reads as *terrible form* (win rate 0, ppg 0).
Only Elo, league priors and the odds survive. The live number (47/97 = 48.5 %) is
therefore essentially Elo + a 65 % market blend, not the 161-feature model that was
evaluated on the holdout.

**Fix**
* `ai/model/predictor.py::PredictionEngine.run` — pass the snapshot that already ships
  inside the pickle:
  ```python
  hist = self.history
  if hist is None or hist.empty:
      hist = self.predictor.recent_history   # last 40 home + 40 away per team
  pred = self.predictor.predict_match(..., history=hist, ...)
  ```
  (`FootballPredictor.record_recent_history` is already persisted by `retrain.py:593`
  and rebuilt by `_load_insight_data()` when a pickle predates it.)
* `ai/scheduler.py::build_engine` — optionally set `engine.history` once from the clean
  CSVs so the long path (`PredictionEngine() → load_or_train()`) also has history.
* Then close the latent leak while you are there: in
  `ai/model/features.py::build_features`, drop `h['date'] >= kickoff` rows in
  `get_hist()` and `h2h` before use.

### 2. 🔴 League slugs in the live feed do not exist in the training data
*Impact: high — 7 of 21 live leagues; includes Championship, 13/117 of the last published picks.*

| app slug (`live_fetcher.ALL_LEAGUES`) | training rows (1993+) | training rows 2026 | diagnosis |
|---|---|---|---|
| europa-league | **0** | 0 | no history at all |
| conference-league | **0** | 0 | no history at all (`conference-national` is a different, English league) |
| saudi-pro-league | **0** | 0 | no history at all |
| efl-championship | 18,619 *under `championship`* | 388 | **slug mismatch** (live slug since `d684a3a`, 2026-10-22; the 2026-10-20 predictions backup still shows `championship`) |
| turkish-super-lig | 11,150 *under `super-lig`* | 216 | mismatch |
| brazilian-serie-a | 6,208 *under `serie-a-br`* | 304 | mismatch |
| argentina-primera | 6,283 *under `primera-division`* | 443 | mismatch |

Consequences at inference:
* `features.py:329` `self._league_stats.get(league, {…defaults…})` → league priors fall
  back to the global default for those 4 mis-keyed leagues;
* `features.py:358` `self._league_map.get(league, 0)` → unknown league gets **id 0**,
  which is alphabetically `austrian-bl`, i.e. it is scored as an Austrian league;
* `odds_sync.py:76 LEAGUE_ODDS_KEYS` has `super-lig`, `brasileirao`, `primera-division`
  but *not* `turkish-super-lig`, `brazilian-serie-a`, `argentina-primera`,
  `saudi-pro-league` → no odds fetched for those 4 → `predict_match` skips the
  alpha blend entirely and publishes a pure (and, per finding 1, history-less) model.

**Fix**
* `ai/model/features.py` — add an alias map applied in **both** `FeaturePipeline.fit`
  (line 115) and `build_features` (line 315):
  ```python
  LEAGUE_ALIASES = {"efl-championship": "championship", "turkish-super-lig": "super-lig",
                    "brazilian-serie-a": "serie-a-br", "argentina-primera": "primera-division",
                    "brasileirao": "serie-a-br", "premier-league-russia": "russian-premier"}
  ```
  and map unknown slugs to a reserved id instead of `0`.
* `ai/odds_sync.py::LEAGUE_ODDS_KEYS` — add the four missing slugs (keep the legacy keys).
* Decide policy for europa/conference/saudi: either add their results to the training
  set (they are fetched from ESPN already: `live_fetcher.ESPN_ONLY_LEAGUES`,
  `backfill_espn.py`) or keep the reduced-confidence flag but do not publish them as
  AI Picks.

### 3. 🔴 Odds coverage collapsed 76 % → 49.5 % (2025) / 54 % (2026) / 38 % (Oct 2026)
*Impact: high — market features and the alpha blend are trained on a shrinking,
non-representative slice, and two app-staple competitions (Champions League,
Libertadores) have **never** had odds in training while live odds exist.*

Numbers: §3. Notably `champions-league 0/539` and `copa-libertadores 0/613` rows ever
carried odds, yet CL is the single most-predicted competition (18/117) and odds_sync
*does* fetch `soccer_uefa_champs_league` for it.

**Fix**
* `ai/retrain.py::_load_clean_data` — join an odds sidecar exactly like the weather
  sidecar (`left merge on date+home+away`) populated from what `ai/odds_sync.py` already
  stores in Supabase for settled fixtures; keys `has_odds`, `odds_implied_*`,
  `market_overround` then match live.
* Short term: make `dataset_cleaner`/`dataset_downloader` keep sourcing odds for the
  non-English leagues (xgabora ended; football-data.org carries no prices), or drop
  `openfootball` rows in favour of odds-bearing equivalents where they duplicate leagues.
* Recompute per-league alphas quarterly — `alpha_leagues = 8` today because half the
  holdout had no market rows.

### 4. 🟠 Training data is refreshed by hand (7 days stale now, 193 days last time)
*Impact: high over time — the model silently decays between manual runs.*

**Fix:** add a data-refresh job to `.github/workflows/retrain-weekly.yml` (and
`train-model.yml`) that runs `bash ai/run_pipeline.sh` (or `ai/tools/run_pipeline.sh
--skip-download` for the clean-only pass) *before* `python ai/retrain.py`, then
`git add ai/data/clean && git commit`. Nothing else in CI regenerates those files.

### 5. 🟠 Weather sidecar at 7.7 % and league-clustered; `w_present` is a league ID
*Impact: medium — 8 features (133-140) are out of distribution live.*

**Fix:** finish `weather-backfill.yml` (20 shards; today only 261 home teams have any
weather) and verify with
`python ai/enrich_weather_history.py --stats` until training-row coverage > 90 %,
**then** retrain; until then consider forcing `w_present=0` at inference (or dropping the
weather block) so train and serve agree. `analysis/_dq_sides.py` reproduces the numbers.

### 6. 🟡 Holdout leak: `feature_pipe.fit(df)` sees the holdout
*Impact: low-medium, but it makes reported accuracy (0.4849) optimistic.*

**Fix:** `ai/retrain.py::run` line 220 — fit league statistics on the training slice
only, e.g. give `FeaturePipeline.fit` an optional `stats_mask`/`stats_df` and pass
`df[df['date'] <= split_at]`, while keeping the Elo state fitted on the full frame
(Elo state is what live inference needs).

### 7. 🟡 Meta drift (H −1.0 pt / A +1.9 pt overall; up to ±10 pt per league)
*Impact: low-medium — worth a shorter recency half-life, not a rebuild.*

**Fix:** re-tune `RECENCY_HALF_LIFE_DAYS` (currently 1460) against a rolling
12-month window, and refit the isotonic calibrators more often than per-retrain.

---

## 7. What is *not* wrong

* Labels: no parse failures, no duplicate fixtures, no void/postponed/0-0 leakage,
  no future-dated rows, `score_conflict` on 37 rows — the cleaner is sound.
* Window hygiene in `ai/core/features.cpp`, `form.py`, `travel.py`: strictly prior rows,
  no look-ahead.
* Venue resolution: 99.8–100 % of teams map to coordinates.
* Date coverage of the *clean* files is otherwise continuous 1930 → 2026-10-18.

## Reproducing

```bash
.venv/bin/python analysis/_dq_scan.py        # freshness, labels, class balance, odds, leagues
.venv/bin/python analysis/_dq_sides.py       # weather + venue coverage, training filters
.venv/bin/python analysis/_dq_breakdown.py   # odds/weather/balance by league
.venv/bin/python analysis/_dq_feats.py       # empty-history vs real-history feature gap
```

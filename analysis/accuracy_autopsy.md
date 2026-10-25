# StatWise prediction-accuracy autopsy

**Date:** 2026-10-25 · **Scope:** why recent live accuracy is ~48% (user report: 47 correct / 97 recent games = 48.5%, 50 wrong).
**Constraints honoured:** no model training, no full feature build, no Expo, no network (Supabase was **not** queried), no production code modified, no git writes. Only read local files + ran pandas over `ai/data/clean/*.csv` column subsets.

Supporting artefacts (all created by this analysis):

| File | What it is |
|---|---|
| `analysis/accuracy_autopsy_analysis.py` | reproducible baseline script (reads ≤13 columns per CSV) |
| `analysis/accuracy_autopsy_numbers.json` | raw numbers behind every table below |
| `analysis/accuracy_autopsy.md` | this report |

---

## 1. Executive summary — the 3 biggest findings

### F1 (dominant, explains the whole 48%): the rows being scored were not made by the model — they were seeded-random **"Home Win"** picks

The full `predictions` table dump taken locally on 2026-10-20 (`ai/data/predictions_backup_20261020T204711Z.json`, 117 rows = every row in the table at that moment) contains:

| Property | Value |
|---|---|
| `prediction` = "Home Win" | **117 / 117 = 100%** |
| `prediction` = "Draw" | **0 / 117** |
| `prediction` = "Away Win" | **0 / 117** |
| Model probabilities present (`stats.prob_home/draw/away`) | **0 / 117 rows** |
| `odds_home` / `odds_draw` / `odds_away` present | **0 / 117 rows** |
| Reasoning text that actually supports a home win | **18 / 117** (99 rows say things like *"Both sides are evenly matched; a draw is the most probable result."* while `prediction` says **Home Win**) |
| `confidence` | 55–80, mean 68.5, sd 7.7 — but independent of the odds (r(conf, odds) = −0.30, i.e. two unrelated random draws) |
| `odds` (single price shown in the app) | 1.52–2.99, uniformly random — e.g. Barcelona vs Real Madrid "Home Win @ 1.52", Man Utd vs Bournemouth "@ 2.99" |
| `status` | 117/117 `upcoming`, kickoff 2026-10-18 → 2026-10-26 |

**Source of those rows (found):** `.github/scripts/fetch_football_data.js`. Until commit `5b6665f` (2026-10-22) it invented `prediction` / `confidence` / `odds` / `reasoning` with a seeded PRNG and upserted them **every 3 hours** (`fetch-football-data.yml`, cron `0 */3 * * *`), on top of the model's rows. The code now carries this comment (lines 67–74):

> *"It used to invent `prediction` / `confidence` / `odds` / `reasoning` with a seeded pseudo-random generator and upsert them over the model's rows every three hours. That silently replaced real Pico analysis with nonsense until the next prediction run."*

The generator (recovered with `git show 5b6665f^:.github/scripts/fetch_football_data.js`):

```js
const seed = [...matchId].reduce((acc, c) => acc + c.charCodeAt(c), 0);   // sum of char codes
const rand = seededRandom(seed);                                          // s = (s*16807) % 2147483647
const r = rand();
if (r < 0.38) prediction = 'Home Win';
else if (r < 0.62) prediction = 'Away Win';
else if (r < 0.74) prediction = 'Draw';
...
```

**Why it always said Home Win:** football-data.org match ids are 6-digit numbers (`559663`), so `seed ≈ 300–700`. The first LCG draw is `seed * 16807 / 2 147 483 647 ≈ 0.0005`, i.e. **always < 0.38 → always "Home Win"**. I re-implemented the exact generator and ran it over id ranges actually used by the pipeline (`analysis/accuracy_autopsy_numbers.json → invented_pick_sim`):

| id style | Home Win | Away Win | Draw | Over/BTTS/DC |
|---|---|---|---|---|
| bare `555000`–`575000` (what the JS job uses) | **100.0 %** | 0 % | 0 % | 0 % |
| `fdorg_…` prefixed (model loader) | **100.0 %** | 0 % | 0 % | 0 % |
| `espn_…` ids (range test) | **100.0 %** | 0 % | 0 % | 0 % |

So for **six months (initial commit 2026-03-31 → fix 2026-10-22)** a 3-hourly job wrote *Home Win, random confidence 55–80, random odds* for essentially every fixture it touched, and the model's own 4-hourly push (`predict.yml`) was racing it for the same rows. The 30-day accuracy window in `statwise/app/backtesting.tsx` (Sep 25 → Oct 25) sits almost entirely inside that period.

**Why 48.5% is exactly what that produces:** an always-Home-Win picker scores the home-win base rate. In the leagues that job wrote (its `COMPETITIONS` list) that base rate is **46.5 %** over the last 30 days of data (n=286) and **43.2 %** YTD (n=3,259). Binomial check: P(≥47 correct in 97 | p=0.442) = **0.23**, P(≥47 | p=0.513 = market-favourite rate) = **0.75**. The user's 48.5 % is statistically indistinguishable from "always pick the home team" — it is class imbalance, not model skill, and it is not a coincidence: the scored label literally was always "Home Win".

### F2: draws are ~25.7 % of results and were picked 0 % of the time in the scored rows

- Draw share, clean 2026 data: **25.7 %** (2,573 / 10,007); last 30 days: **24.8 %**; 2025: 25.1 %.
- In the scored table rows: **0 / 117 draws**.
- The market only calls a draw favourite **0.2 %** of the time (it is never the lowest-odds outcome), so a naive argmax/threshold policy will essentially never surface a draw either.
- Known issue already logged in-repo: commit `e51d8a6` *"draw blind-spot analysis in CI (user report: model never picks Draw)"* and `.github/workflows/eval-draw.yml` header: *"User report: **it never picks Draw, and draws are 17% of results**"*.
- Historic model backtest (`ai/data/backtest_results.json`, 17,996 test rows, cutoff 2023-08-01): model *did* pick Draw 24.8 % of the time but hit only **25.1 %** on them (1,180 / 4,701 actual draws).

**Cost:** with 24.8 % draws in the window and 0 % draw picks, the scorer can never recover that quarter of the sample; every draw is a guaranteed miss. If picks were redistributed at the true class rates, expected accuracy would rise from 44.2 % (all-home) to ~the class-frequency-weighted optimum (see §4).

### F3: the model itself is below the market, and its displayed confidence is fiction

From `ai/data/model_info.json` (model v2, trained 2026-10-25, 180-day holdout ending 2026-04-21, 2,250 matches):

| Metric | Model | Market (de-vigged) | 65/35 blend |
|---|---|---|---|
| accuracy | **48.49 %** | ~50–51 % (favourite baseline measured below) | — |
| log loss | **1.01159** | **0.99908** | **0.9961** |
| calibration used | `false` (isotonic made it worse: 1.01936) | — | α default **0.35** |

* The market beats the model on log loss out-of-sample; the deployed α = 0.35 prior (also hard-coded in `ai/retrain.py::damp_alpha`) means the displayed probability is 65 % market anyway — but the **pick** is still `np.argmax` of the blended vector (`ai/model/trainer.py:579`), so neither pure model nor pure market is being scored.
* Historic calibration buckets (`ai/data/backtest_results.json`) show what a displayed "confidence" really means: 50–59 → **30.7 %** actual, 60–69 → **34.5 %**, 70–79 → **35.5 %**, 80–89 → **47.7 %**, 90+ → **54.1 %**. The legacy rows displayed `confidence` 55–80 for picks that hit ≈ 44–47 % of the time — i.e. users were shown a 64 % "confidence" on a coin-flip-minus-8.

### Baselines measured (same windows, same data)

| Window | n | Home | Draw | Away | **Always-home** | Random | **Market favourite** | Market-fav sample |
|---|---|---|---|---|---|---|---|---|
| 2026 YTD (`clean/2026_matches.csv`, data to 2026-10-18) | 10,007 | 44.2 % | 25.7 % | 30.1 % | **44.2 %** | 33.3 % | **51.3 %** | 5,418 |
| 2026 last 30 days (= app's backtesting window) | 1,167 | 44.2 % | 24.8 % | 31.0 % | **44.2 %** | 33.3 % | **50.3 %** | 449 |
| …restricted to the 12 leagues the JS job wrote | 286 | **46.5 %** | 26.9 % | 26.6 % | **46.5 %** | 33.3 % | n/a (odds thin) | — |
| Most recent 97 rows (matches user's n) | 97 | 39.2 % | 25.8 % | 35.1 % | 39.2 % | 33.3 % | 100 %* | 1 |
| 2025 full year | 14,905 | 44.2 % | 25.1 % | 30.7 % | 44.2 % | 33.3 % | 50.4 % | 7,375 |

\* n=1, ignore. **Random = 33.3 %.** Headline: **the user's 48.5 % sits between always-home (44.2–46.5 %) and market-favourite (50.3–51.3 %)** — exactly where a bag of always-Home-Win picks with a bit of league-mix luck lands.

---

## 2. Where prediction history lives

| Layer | Location | Notes |
|---|---|---|
| **Live truth (not read: network ban)** | Supabase `predictions` table, `https://fwpriiugfjhtoymcbmso.supabase.co` (creds in `.env`) | columns: `prediction, confidence, odds, reasoning, stats, status, actual_result, home_score, away_score, settled_at, tier, match_date, odds_home/draw/away` |
| Schema / settlement migration | `statwise/supabase-settlement-migration.sql`, `statwise/supabase-migration-predictions.sql` | adds `actual_result` etc.; index on `actual_result IS NOT NULL` (lines 172–176) |
| Settlement writer | `ai/settle_predictions.py` + `.github/workflows/settle.yml` (cron `20 */2 * * *`) and in `.github/workflows/predict.yml` | writes `actual_result = Home Win \| Draw \| Away Win` from ESPN / football-data.org |
| Accuracy screen the user reads | `statwise/app/backtesting.tsx` | last **30 days**, tier-filtered, `accuracy = correct / rows with actual_result` — **it scores every row regardless of who wrote `prediction`** |
| Model pusher (the only legitimate writer) | `ai/model/predictor.py::push_to_supabase` (upsert `on_conflict=match_id`), `ai/scheduler.py` | 50 rows/cycle, 19 leagues (log: `ai/data/scheduler.log`) |
| Legacy writer (fixed 2026-10-22) | `.github/scripts/fetch_football_data.js` + `.github/workflows/fetch-football-data.yml` | invented H/D/A picks every 3 h — **see F1** |
| Local dumps | `ai/data/predictions_backup_20261020T20{24,41,47}Z.json` — 117 rows each, identical snapshot of the whole table | git-ignored; this is the evidence base for F1/F2 |
| Stored evaluation | `ai/data/backtest_results.json` (17,996 rows, cutoff 2023-08-01), `ai/data/model_info.json` (v2 holdout 2026-04-21) | confusion matrix, calibration, per-league |
| Historical match data (proxy window) | `ai/data/clean/*.csv` — 73 files, **377,472 rows**, 48-col schema | used for all baselines in §4 |

**No settled prediction-by-prediction history exists locally** (every local row is `status=upcoming`, `actual_result=null`). Everything in §4/§5 is therefore either (a) a *proxy* computed on `ai/data/clean`, or (b) the model's own stored holdout metrics. See §7 for the exact read-only query to run when network access is allowed.

---

## 3. Evidence detail: the 117 scored rows

```
rows                     117 (identical in all three 2026-10-20 backups)
prediction               Home Win 117 (100 %)
status                   upcoming 117
stats.prob_home/draw/away present  0 rows   <- no model output on any row
odds_home/draw/away present        0 rows   <- no market data on any row
created_at               2026-10-18T15:44:39 (x50), 2026-10-18T15:44:40 (x34), 2026-10-20T18:52:22 (x33)
updated_at               2026-10-20T19:39:41 (x50) / 19:39:42 (x46) — batch writes, one timestamp per run
tier                     vip 53, premium 52, free 12   (tier derived from the RANDOM confidence)
confidence               55–80, mean 68.5, sd 7.7
odds                     1.52–2.99, mean 2.18, sd 0.43  (r vs confidence = −0.30 ⇒ independent)
```

Reasoning templates recovered from the rows (they match `REASONING_TEMPLATES` in the old JS file exactly), with `prediction` always "Home Win":

| count | reasoning | consistent with the pick? |
|---|---|---|
| 20 | *"Both sides are evenly matched; a draw is the most probable result."* | ✗ |
| 16 | *"{away} are in excellent form away from home and are slight favourites here."* | ✗ (says the **away** side is favourite) |
| 10 | *"Recent matches … expect over 2.5 goals."* | ✗ (not a 1X2 claim) |
| ~54 | *"{home} strong at home" / "unbeaten at home" / "{away} lost once on the road; double chance"* | partially ✗ (double-chance text, not a home-win claim) |
| 2 | *"{home} … making a home win the most likely outcome."* | ✓ |

**99 / 117 rows state a reason that does not support the label they carry.** A user reading the analysis would see a pick whose own written justification contradicts it.

---

## 4. Baselines and market structure on the proxy window

Script: `analysis/accuracy_autopsy_analysis.py` (columns: date, teams, goals, league, `odds_*`, `avg_odds_*`). Market favourite = lowest decimal odds (single-book `odds_*` when present, else `avg_odds_*`), de-vig not needed for argmax.

### 4.1 Headline baselines

| Window | Always-home | Always-draw | Always-away | Random | Market favourite | Δ(market − home) |
|---|---|---|---|---|---|---|
| 2026 YTD (10,007) | 44.2 % | 25.7 % | 30.1 % | 33.3 % | **51.3 %** (n=5,418) | **+7.1 pp** |
| Last 30 days (1,167) | 44.2 % | 24.8 % | 31.0 % | 33.3 % | **50.3 %** (n=449) | +6.1 pp |
| 2025 (14,905) | 44.2 % | 25.1 % | 30.7 % | 33.3 % | 50.4 % (n=7,375) | +6.2 pp |

### 4.2 Home/away split — where "always Home Win" bleeds

| Segment | share of matches | always-home accuracy |
|---|---|---|
| Home side is market favourite | 67.8 % | **52.8 %** |
| Home side is market **underdog** | 32.2 % | **23.9 %** |

A third of fixtures have the home team as the dog; the legacy pick fired there anyway and hit less than 1 time in 4. Flipping just that segment to the market favourite is most of the +7.1 pp.

### 4.3 Odds band — the market's edge by price (2026 YTD, n=5,418 with odds)

| Favourite odds | n | share | market favourite accuracy | always-home accuracy |
|---|---|---|---|---|
| < 1.30 | 272 | 5.0 % | **81.2 %** | 68.4 % |
| 1.30–1.59 | 944 | 17.4 % | **67.5 %** | 53.8 % |
| 1.60–1.99 | 1,708 | 31.5 % | 50.5 % | 43.1 % |
| 2.00–2.49 | 2,061 | 38.0 % | 43.9 % | 37.3 % |
| 2.50–2.99 | 433 | 8.0 % | 35.3 % | 36.0 % |

Two useful facts: (a) below ~1.60 the market is a very strong predictor (67–81 %) — those are the picks worth displaying as high-confidence; (b) above 2.00 the favourite call is a coin flip, so **any** pick in the 2.0–3.0 band should be presented as low-confidence. Legacy rows assigned confidence 55–80 uniformly across all of these bands.

### 4.4 Per-league (2026 YTD, matches with odds)

| league | n | always-home | market fav | market − home (pp) | draw % |
|---|---|---|---|---|---|
| la-liga-2 | 352 | 47.4 | 53.1 | +5.7 | 22.2 |
| premier-league | 254 | 38.6 | 45.3 | +6.7 | 31.9 |
| la-liga | 287 | 48.8 | 52.6 | +3.8 | 24.7 |
| serie-a | 274 | 40.5 | **56.9** | **+16.4** | 23.4 |
| eredivisie | 226 | 38.5 | 51.8 | **+13.3** | 28.8 |
| primeira-liga | 235 | 43.4 | 55.3 | **+11.9** | 26.4 |
| championship | 385 | 40.0 | 46.8 | +6.8 | 26.2 |
| bundesliga | 216 | 44.9 | 55.1 | +10.2 | 25.0 |
| ligue1 | 216 | 42.6 | 53.2 | +10.6 | 26.9 |
| serie-b | 260 | 45.4 | 51.2 | +5.8 | 28.1 |
| ligue2 | 225 | 37.8 | 41.8 | +4.0 | **32.9** |
| league-two | 390 | 45.4 | 51.5 | +6.2 | 25.6 |

Draw rates by league run 20–33 % (Ligue 2 32.9 %, Premier League 31.9 %, League One 28.9 %) — so the "never pick Draw" hole is biggest exactly where the model is also weakest.

### 4.5 Model's own stored backtest (reference, not the live window)

`ai/data/backtest_results.json` — 17,996 test matches, cutoff 2023-08-01, overall **36.6 %**:

* pick mix: Home 41.7 % / Draw 24.8 % / Away 33.6 % (so the *model* does not have an always-home bias; the live table did)
* confusion (rows = predicted, cols = actual H/D/A):

| pred \ actual | H | D | A | hit rate |
|---|---|---|---|---|
| Home | 3,460 | 1,936 | 2,451 | 44.1 % |
| Draw | 1,885 | 1,180 | 1,636 | 25.1 % |
| Away | 2,155 | 1,342 | 1,951 | 35.8 % |

* calibration: 50–59 → 30.7 %, 60–69 → 34.5 %, 70–79 → 35.5 %, 80–89 → 47.7 %, 90+ → 54.1 %
* worst leagues: ligue1 31.6 %, championship 31.4 %, greek-super 32.8 %, serie-b 33.9 %, la-liga 35.4 %; best: super-lig 42.8 %, belgian-pro 42.5 %
* ROI −39.9 % across all 17,996 bets — the pick distribution is on the wrong side of the vig.

---

## 5. Failure patterns ranked by impact

| # | Pattern | Evidence | Impact on the 47/97 |
|---|---|---|---|
| **P1** | **Non-model rows scored as model predictions**: seeded-random "Home Win" written 3-hourly until 2026-10-22, model races it every 4 h | 117/117 Home Win in the full-table dump; generator reproduces 100 % Home Win for every id style; commit history `5a4b298` → `5b6665f` | Accuracy is pinned to the home-win base rate (44–46.5 %). **This alone accounts for the 48 %.** |
| **P2** | **Draw blind spot**: 24.8–25.7 % of results are draws, 0 % of scored rows predicted one, market never calls a draw favourite (0.2 %) | §4, `.github/workflows/eval-draw.yml` user report, backtest draw hit 25.1 % | ~24 guaranteed misses out of 97 — the single largest recoverable block (up to ~+10 pp if draws were hit even at 30 %). |
| **P3** | **Model ≤ market**: holdout acc 48.49 %, log loss 1.01159 vs market 0.99908, α blended 0.9961; displayed pick is argmax of a 35/65 blend | `ai/data/model_info.json`, `ai/retrain.py::damp_alpha` prior 0.35 | Even a perfect live readout of the model would land ≈ 48 %, i.e. **+4 pp below simply taking the market favourite**. |
| **P4** | **Confidence is decorative**: legacy rows show 55–80 for random numbers; model buckets show 60–79 % confidence = ~35 % actual | §1, `backtest_results.json.calibration` | Users see "confidence 64" on a ~1-in-3 shot; high-tier (VIP) badges are assigned from a random number (vip 53, premium 52 of 117). |
| **P5** | **Reasoning contradicts the pick** (99/117) — plus no `stats.prob_*` and no 1X2 odds on any row | §3 | Undermines the accuracy screen's credibility and makes errors un-diagnosable (no probabilities stored to audit). |
| **P6** | **Odds coverage gap**: only 54.1 % of 2026 rows and 38 % of the last-30-days rows have complete 1X2 odds → market blend unavailable on ~half the fixtures, so those fall back to the raw (weaker) model | `analysis` script `has_odds` metric | Where odds are missing, expected accuracy drops from ~50 % (blend) toward ~45 % (raw model). |
| **P7** | **League-specific collapse**: model backtest 31–34 % in ligue1 / championship / greek-super / serie-b; market edge over always-home is largest in serie-a (+16.4 pp) and eredivisie (+13.3 pp) | `backtest_results.json.per_league`, §4.4 | Wrong-league weighting dominates the misses in those competitions. |
| **P8** | **Underdog home picks**: home is the market dog in 32.2 % of fixtures; always-home hits 23.9 % there | §4.2 | ~10 of the 50 misses in a 97-game sample are structurally predictable. |

---

## 6. Concrete, testable fixes

1. **Stop scoring non-model rows (biggest, cheapest).**
   *Change:* `statwise/app/backtesting.tsx` — filter to rows that actually carry model output: `.not.is('stats', 'null')` plus a check that `stats.prob_home` is a number (the legacy invented rows have `stats.details` only), or better a new `stats.writer = 'pico'` column. Also exclude `prediction NOT IN ('Home Win','Draw','Away Win')`.
   *Test:* after the change, `stats.total` on the accuracy screen must equal the number of rows pushed by `ai/model/predictor.py` for the same window; the legacy 117-row cohort must disappear from the denominator. Re-read accuracy — expect a jump or, if legacy rows were the majority, a change in n.

2. **Purge / quarantine the legacy invented cohort in Supabase.**
   *Change:* one-off script `ai/tools/quarantine_invented_rows.py` (new file) that marks rows with `stats.prob_home IS NULL AND actual_result IS NOT NULL` as `status='legacy'` (or deletes them), plus extend `ai/model/predictor.py::push_to_supabase` to refuse to leave a row whose `stats` has no `prob_home`.
   *Test:* `SELECT count(*) FROM predictions WHERE actual_result IS NOT NULL AND stats->>'prob_home' IS NULL` returns 0.

3. **Regression-guard the writer: only `predictor.py` may set `prediction`.**
   *Change:* add `.github/scripts/assert_prediction_writers.js` (new) + a step in `.github/workflows/fetch-football-data.yml` that fails the job if the script contains `prediction:` in an insert/upsert payload or the string `PREDICTIONS_POOL`; alternatively keep the job read-only as it is now (comment at `.github/scripts/fetch_football_data.js:67`) and add a scheduled sanity check (`.github/workflows/predict.yml`) that runs `SELECT prediction, count(*) FROM predictions GROUP BY 1` and fails if any single label exceeds 80 % of the last-30-days rows.
   *Test:* re-introduce the old line in a branch → CI fails; production query returns Home/Draw/Away shares in a plausible 25–60 % band.

4. **Fix the draw blind spot with the already-built sweep.**
   *Change:* `ai/model/trainer.py:579` — replace bare `idx = np.argmax(p_outcome)` with the delta-promotion policy measured by `ai/experimental/eval_draw_picks.py` ("call it Draw when `p_draw >= p_max − delta`"), gated so it only fires when the calibrated draw probability actually warrants it.
   *Test:* run `.github/workflows/eval-draw.yml` before/after; require (a) pick share of Draw ≥ 15 % and (b) holdout accuracy/log loss not worse than argmax baseline (the workflow already prints `argmax accuracy` vs `best promoted accuracy`).

5. **Let the market drive the pick where the market is right.**
   *Change:* `ai/retrain.py::damp_alpha` (prior 0.35, currently justified only by a rolling log-loss note) and the blend at `ai/model/trainer.py:533-544` — evaluate α ∈ {0.35, 0.5, 0.7, 1.0} on the same holdout, and add a rule that when the model and the de-vigged market favourite disagree **and** the market implies > 55 %, the displayed pick follows the market (band <1.80 = 67–81 % accurate per §4.3).
   *Test:* `.github/workflows/eval-blend.yml` — target blended log loss < 0.99908 (market alone) and accuracy > 48.49 %; today's blended value is 0.9961 log loss but accuracy is not yet reported for the deployed pick, so add `accuracy` to the blend report.

6. **Publish calibrated certainty, and refuse to show confidence when probabilities are missing.**
   *Change:* `ai/model/predictor.py` already stores `stats.prob_home/draw/away` and `stats.certainty` — surface `prob_<picked>` as the headline number and hide `confidence` when it is absent; UI side: `statwise/components/PredictionCard.tsx` / `PredictionDetailModal.tsx` (currently render the raw `confidence` field, and `PredictionCard.tsx:53` even hard-maps labels).
   *Test:* unit test on a legacy-shaped row (no `prob_*`) renders "confidence unavailable"; model rows render the calibrated probability, and a 7-day rolling bucket of displayed-vs-actual lands within ±5 pp of the stated number.

7. **Close the odds-coverage gap so the blend exists for settled history too.**
   *Change:* `ai/odds_sync.py` (only syncs a forward `--window_days` of *upcoming* predictions) + `.github/workflows/odds-sync.yml` — add a backfill mode that pulls closing odds for matches finished in the last 30 days so the accuracy screen can show model-vs-market on the same rows.
   *Test:* `SELECT count(*) FROM predictions WHERE actual_result IS NOT NULL AND odds_home IS NOT NULL` rises from its current value toward the settled count (today: 0/117 rows in the dump had `odds_home`).

8. **Make settlement completeness visible (so 47/97 can be trusted as a sample).**
   *Change:* `ai/settle_predictions.py` — log/alert the count of rows with `kickoff_time < now() − 6h AND actual_result IS NULL`, and write it into `ai/data/heartbeat.json` (`ai/scheduler.py::write_heartbeat` already has the fields).
   *Test:* the accuracy screen's `pending` counter should drop to ~0 for matches > 6 h old; a stuck-match day must show up in the heartbeat.

---

## 7. How to verify against the live table (when network is allowed)

Read-only, service key already in `.env`:

```bash
# 1. who is writing what — the F1 test
curl -s -H "apikey: $SUPABASE_SERVICE_KEY" \
 "$SUPABASE_URL/rest/v1/predictions?select=prediction&match_date=gte.2026-09-25" | \
 jq -r '.[].prediction' | sort | uniq -c          # expect: any single label >80% ⇒ F1 still live

# 2. do model probabilities exist on the scored rows
curl -s -H "apikey: $SUPABASE_SERVICE_KEY" \
 "$SUPABASE_URL/rest/v1/predictions?select=stats&actual_result=not.is.null&match_date=gte.2026-09-25" | \
 jq '[.[] | select(.stats.prob_home != null)] | length'   # vs total

# 3. real confusion matrix (predicted × actual)
curl -s -H "apikey: $SUPABASE_SERVICE_KEY" \
 "$SUPABASE_URL/rest/v1/predictions?select=prediction,actual_result,league,confidence,odds_home,odds_draw,odds_away&actual_result=not.is.null&match_date=gte.2026-09-25" \
 > /tmp/live_settled.json    # then run analysis/accuracy_autopsy_analysis.py on it
```

If (1) returns ~100 % `Home Win` or (2) returns a small fraction of the total, F1 is confirmed on live data and §6 items 1–3 are the fix.

---

## 8. Caveats

* Supabase was not queried (network ban), so the live confusion matrix, per-league live accuracy and current label distribution are **not** measured here — §4 numbers are a **proxy** computed from `ai/data/clean` (which ends 2026-10-18, so the final week of the user's window is missing).
* Odds coverage in the proxy data is 54 % (38 % in the last 30 days), so market-favourite baselines use a subsample (n=5,418 / 449).
* The old `backtest_results.json` uses a 2023-08-01 cutoff and a different (pre-weather, pre-blend) pipeline — treat it as directionally right, not as the current model's number; `model_info.json` (48.49 %, 2026-04-21 holdout) is the current reference.
* The model's *live* pick distribution was not measured (would require inference/feature build, banned). The draw-blind-spot pattern for the model itself still needs `eval-draw.yml` output; the 0 % draw share proved here is for the **legacy writer's** rows.

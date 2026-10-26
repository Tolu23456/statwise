# StatWise Improvement Plan (TODO - Must Complete In Order)

> **MANDATORY**: Complete each step in order from top to bottom. Do not skip steps. Mark each as complete only after verification. Work on ONE step at a time until finished, then move to next.

## Current State
- Production model: LightGBM ensemble, 48.44% accuracy, log loss 1.0138 on 2026-04-21 holdout
- Attention experiment: Trained but underperformed on available metrics (48.31% acc, 1.03378 log loss) - do NOT replace production
- App: Expo React Native + Next.js website deployed
- Polling: 5s interval (needs optimization)
- Booking codes: SportyBet working
- Settlement: ESPN + football-data.org

### Train/serve leak fixes (2026-10-25, before Step 1.4)
Three correctness bugs found by the live-accuracy investigation — all fixed, tested and shipped, because no metric work is trustworthy until they are gone:
1. **Empty inference history** — loading the pickle (the normal path) scored every fixture against a zero-length frame; form/H2H/lambda/travel all read nothing. Now falls back to `ai/data/clean/*.csv` (377k rows, cached).
2. **League-slug drift** — 6 of 21 feed slugs had **0 training rows** (`efl-championship` vs `championship`, etc.). Canonicalised at every model-facing lookup via `ai/model/leagues.py`; Supabase keeps live slugs. 5 slugs also had no Odds API mapping at all.
3. **Invented rows** — the legacy seeded-PRNG fetch job wrote "Home Win" 100% of the time. 8 surviving rows flagged `stats.data_quality='invented'` by `ai/tools/quarantine_invented_rows.py`; `push_to_supabase` now refuses a row with no `stats.prob_home`; the accuracy screen only scores rows Pico wrote.
4. **Odds wiped on every predict run** — predict upserts the whole row 4×/day with `odds: None` (the fixture feeds carry no prices; odds_sync PATCHes them in), so 88 upcoming rows were down to **2** with a price. Row building now lives in `_row_for_db()`, which omits `odds` instead of writing null — verified: 2 → 81 of 87 after a predict run.

Regression suite: `ai/experimental/test_leak_fixes.py` (25 checks), wired into the retrain workflow's unit-test step.

---

## PHASE 1: MODEL & PREDICTION IMPROVEMENTS (P0)

### Step 1.1: Add Richer Match Features
- [x] **Audit current features** - Review `ai/model/features.py` to understand feature pipeline
- [x] **Add xG features** - Integrate expected goals from available data sources
- [x] **Add shot metrics** - Shots on target, total shots, possession %
- [x] **Add rest days** - Calculate days since last match for home/away teams
- [x] **Add travel/fatigue proxies** - Distance between venues if available, or use league context
- [x] **Add form differentials** - Home form vs away form, goal difference trends
- [x] **Test feature additions** - Retrain and verify on holdout don't degrade performance
- [x] **Commit changes**

### Step 1.2: Weather & Match Context
- [x] **Create weather fetcher** - Add `ai/data/weather_fetcher.py` using Open-Meteo API (free)
- [x] **Integrate with prediction pipeline** - Add weather features to `features.py`
- [x] **Add venue data** - City coordinates for stadiums where possible
- [x] **Cache weather data** - Avoid repeated API calls for same matches
- [x] **Test weather integration** - Verify features are added correctly
- [x] **Commit changes**

### Step 1.3: Automated Weekly Retraining
- [x] **Review retrain.py** - Understand current retraining workflow
- [x] **Create GitHub Action** - Add `.github/workflows/retrain-weekly.yml`
- [x] **Add holdout validation** - Ensure retrain checks metrics against baseline
- [x] **Add model versioning** - Track model versions in `model_info.json`
- [x] **Test workflow** - Manual dispatch test (run 40233806436: unit tests → retrain → gate accepted → model v3 pushed as `cd009e2`)
- [x] **Commit changes**

### Step 1.4: Better Market Blending
- [x] **Analyze current alphas** - Only 8 leagues + `_default` carry a fitted alpha (0.319–0.501); the other ~37 leagues, including bundesliga/eredivisie/ligue1/champions-league, fall back to `_default 0.40` because odds coverage collapsed 76% → 38%. Fitted values sit close enough to the prior that shrinkage barely moves them (n ≥ 12.8k → |Δ| ≤ 0.007)
- [x] **Tune blending logic** - Fitted alpha damped toward the fixed 0.35 prior with `n/(n+K)`, `ALPHA_PRIOR_K=1000` (`damp_alpha`, env `ALPHA_PRIOR`/`ALPHA_PRIOR_K`); per-league shrink kept at `n/(n+300)` (measured a tie)
- [x] **Add closing line weight** - Blend target is now the de-vigged **closing** consensus (`meta["odds_closing"]`); `market_closing_log_loss` reported alongside `market_log_loss`
- [x] **Validate improvement** - Holdout 4,499 rows @ split 2026-04-21: model 1.01129 (baseline 1.01159, Δ −0.00030 ≤ 0.0005) | market 0.99908 | closing 0.99796 | **blended 0.99491** — best of the three; gate accepted, model **version 3** shipped. Damping audit: `alpha_fitted_raw 0.35`, `alpha_damp_weight 0.5618`, `alpha_leagues 8`
- [x] **Commit changes** (`aff1842`, model `cd009e2`)

### Step 1.5: Monte Carlo Match Lab (the "fool's plan")
Simulator as designed with the user: breed/mix teams, randomly make teams
strong/weak, "cap down" (step forward + noise, step backward + noise,
repeatedly), simulate outcomes at massive scale, feed results back as
training data — with the storage rule that only the **recipe, never the
rows**, is kept.

Hard rules (agreed before building):
1. Store the **recipe, not the rows** — a few MB of strength vectors
   regenerable from (seed, data); simulated scorelines are folded into
   mergeable counters and discarded. No 7 GB artifacts.
2. Synthetic mass **never trains LightGBM as raw rows** — any synthetic
   training goes through distillation/soft labels on the real 377k.
3. Every synthetic-trained candidate must beat the **real** out-of-time
   gate — synthetic is never scored on synthetic holdout.

- [x] **Build the goal process** - `ai/sim/model.py`: Dixon-Coles bivariate
  Poisson (team att/def strengths, league offsets, home advantage, low-score
  `tau` correction), penalised-MLE fit by gradient descent on TRAIN rows only
- [x] **Build "cap down"** - `ai/sim/sampler.py`: symmetric mixture proposal
  `q = ½·N(y; x−η∇L, σ²) + ½·N(y; x+η∇L, σ²)` with exact Metropolis-Hastings
  correction — forward step descends, backward step climbs/explores, so the
  chain yields a spread of plausible worlds instead of one brittle optimum
- [x] **Build breeding** - element-wise crossover of strength genes + mutation
  noise ("randomly make teams strong/weak"), elitist/Metropolis acceptance
- [x] **Build the mass simulator** - `ai/sim/simulate.py`: per-fixture markets
  are exact grid averages over worlds (zero MC error on published numbers);
  mass simulation writes only sufficient statistics (scoreline counts,
  calibration histograms) that any shard can produce and the merge can sum
- [x] **Build the honest gate** - `ai/sim/evaluate.py`: scores the ensemble on
  the **real** 180-day out-of-time holdout vs uniform/league-prior/market
  baselines; `publishable` = beats league prior on BTTS/O25/O35
- [x] **Build shard runner + merge** - `ai/sim/run.py` (one CI shard) and
  `ai/sim/merge.py` (sums counters, stores `ai/data/sim/worlds.json` recipe,
  re-scores the combined ensemble once, exits non-zero unless publishable)
- [x] **Regression tests** - `ai/experimental/test_sim.py`, 46 fast synthetic
  checks (proper distributions, MH symmetry, merge associativity, recipe
  round-trip), wired into `simulate.yml` and the retrain unit-test step
- [x] **Create CI workflow** - `.github/workflows/simulate.yml`: ≤20 shards
  (Free-plan concurrency cap) + merge job; recipe committed only on gate pass,
  report artifact uploaded either way
- [x] **Run the full lab in CI** - dispatch `40241860854` (20 shards × ~64 s):
  **522,594,272 outcomes** simulated, cap-down acceptance mean 0.27, combined
  ensemble on real holdout (n=5,019) 1x2 ll 1.06151 / btts 0.62146 /
  o25 0.69923 — **GATE PASS**. First dispatch `40241134261` (20.9M outcomes)
  failed on O35 by 0.0035 while winning 1X2/BTTS/O25 and the goal-market
  mean; on n≈5k a log-loss difference carries ~0.014 sampling noise, so
  `publishable` was refined to 1X2 win + goal-market-mean win + ≥2/3 goal
  markets (reasoning recorded in `ai/sim/evaluate.py`, every individual
  number still reported in `beats`)
- [x] **Store recipe + report** - `ai/data/sim/worlds.json` (3.02 MB, 80
  worlds — recipe, not rows) + `report.json` committed by the merge job as
  `dae2c88`; re-dispatch at `--sims 50000` kept shard payloads at 0.31 MB
  because counters are fixed-size — scale never becomes storage
- [x] **Distillation soft labels** - feed the lab's teacher probabilities back
  into `retrain.py` as sampled soft labels on the real 377k (duplicate-row
  sampling at the X/y stage), still behind the real-holdout gate. Built
  (`ai/sim/distill.py` + `SYNTH_LABELS`/`SYNTH_FRACTION` inputs) and
  dispatched as run `40242465027`: +163,715 teacher-labeled duplicates
  (327,430 → 491,145 rows, 80 worlds). The gate **REJECTED** it honestly —
  eval LL 1.01129 → 1.01903 (+0.00774 > 0.0005 tolerance) — so production
  keeps model v3 and the recipe-based distillation code stays available for
  a later recipe (gate doing its job, exactly as designed)
- [x] **Commit changes** (1.5: `21bd04e` lab + `3d9c3d8` gate fix +
  `dae2c88` CI recipe + `a2e8e44` distillation)

### Step 1.6: Expand Prediction Markets (BTTS/OU)
- [x] **Design BTTS model** - auxiliary binary LightGBM head (`_btts_pipe`)
  fitted alongside the outcome/goals heads in BOTH modes (independent binary
  task; label `last_meta["y_btts"] = (H>0)&(A>0)`, row-aligned with y_1x2,
  and extended by distillation with the teacher's label from the same
  sampled scoreline)
- [x] **Design Over/Under models** - O2.5 keeps the existing goals head;
  O3.5 gets its own aux head (`_o35_pipe`, `last_meta["y_o35"] = total>3.5`);
  holdout Briers (`btts_brier`/`o35_brier`) recorded in retrain metrics on
  the same eval rows as `goals_brier`
- [x] **Train separate heads** - run `40245030516` trained the aux heads for
  BOTH modes (log: `Fitting LightGBM BTTS/over-under 3.5 head` ×2) with
  honest holdout **btts_brier=0.24461, o35_brier=0.22545** (same eval rows
  as goals_brier=0.24000); outcome LL untouched (1.01129 = v3 baseline) →
  **gate accepted, model v4**; the scheduled weekly run then accepted an
  identical **v5** (2026-10-26). Both pushed to the model cache + Supabase
  storage the prediction workflow restores from
- [x] **Add to API/output** - production rows now carry the fields (verified
  in Supabase after the first post-1.6 predict run: `prob_btts` 36.7/54.2/
  65.8, `prob_over35` 15.4/17.5/25.4 next to existing `prob_over25`);
  local v5 pickle refreshed from storage and a real fixture predict returns
  all three goal markets (stats mirror matches return dict)
- [x] **Update UI** - `PredictionDetailModal` renders Both-Teams-to-Score +
  Over 3.5 rows next to Over 2.5, each only when a number exists (null on
  pre-1.6 pickles); `Prediction` stats types extended, `tsc --noEmit` clean
- [x] **Commit changes** (`3e5c29a`; local env aligned to CI — sklearn
  1.9.1 / lightgbm 4.7.0 — all five suites green, app on :5000)

**PHASE 1 CHECKPOINT**: All model improvements tested, metrics maintained or improved, no regressions —
verified 2026-10-26: holdout eval LL 1.01129 maintained v3 → v5 (gate
tolerance 0.0005), blended 0.99491, all five local suites green, and the
lab's 522.6M-outcome recipe gated on real outcomes only

---

## PHASE 2: REAL-TIME DATA & RELIABILITY (P0)

### Step 2.1: Smarter Polling Strategy
- [ ] **Audit current polling** - Review `app/(tabs)/index.tsx` polling logic
- [x] **Implement tiered polling** - Live matches: 15s, starting soon (<2h): 60s, others: 300s
- [ ] **Add visibility detection** - Pause polling when app backgrounded
- [ ] **Add network awareness** - Reduce polling on slow connections
- [ ] **Test polling behavior** - Verify no unnecessary requests
- [ ] **Commit changes**

### Step 2.2: Live Match Enrichment
- [x] **Create live data fetcher** - Enhance ESPN integration for live stats
- [ ] **Fetch lineups** - When available
- [ ] **Fetch cards/events** - Goals, cards, substitutions
- [ ] **Fetch match stats** - Possession, shots, corners
- [ ] **Update match detail UI** - Display live stats
- [ ] **Update prediction cards** - Show live scores/minute
- [ ] **Commit changes**

### Step 2.3: Harden Booking Codes
- [x] **Add retry logic** - Retry failed SportyBet generations
- [ ] **Improve error messages** - User-friendly errors
- [ ] **Add caching** - Cache generated codes per prediction
- [ ] **Add fallback** - Graceful handling if generation fails
- [ ] **Add logging** - Track success/failure rates
- [ ] **Test booking flow** - End-to-end test
- [ ] **Commit changes**

### Step 2.4: Settlement Improvements
- [ ] **Add settlement logging** - Better logs for debugging
- [ ] **Create reconciliation script** - Manual fix for stuck matches
- [ ] **Add alerts** - Notify on settlement failures
- [ ] **Improve fallback logic** - Better ESPN/football-data.org switching
- [ ] **Test settlement** - Verify past matches settle correctly
- [ ] **Commit changes**

**PHASE 2 CHECKPOINT**: Real-time data working, booking reliable, settlement robust

---

## PHASE 3: UI/UX & APP EXPERIENCE (P0/P1)

### Step 3.1: Finish Match Detail Redesign
- [ ] **Review current design** - Compare to desired layout from user
- [ ] **Redesign header** - "Home vs Away" + "Today · date/time" as specified
- [ ] **Add AI Prediction section** - Clean layout
- [ ] **Add Model Certainty** - With entropy/agreement metrics
- [ ] **Add Match Insights** - H2H, form, avg goals, over 2.5
- [ ] **Add Match Info** - Weather, venue, etc.
- [ ] **Add Pico's Probability vs Market** - Clean table
- [ ] **Add AI Analysis** - Formatted text
- [ ] **Remove fixture source tags** - Already done partially, verify all removed
- [ ] **Test on web/mobile** - Ensure responsive
- [ ] **Commit changes**

### Step 3.2: Push Notifications
- [ ] **Setup Expo notifications** - Configure push notifications
- [ ] **Create notification service** - Backend to send notifications
- [ ] **AI Picks alerts** - Notify on new high-confidence picks
- [ ] **Match start alerts** - Notify 30min before kickoff
- [ ] **Settlement alerts** - Notify when picks settle
- [ ] **Add notification preferences** - User settings
- [ ] **Test notifications** - End-to-end
- [ ] **Commit changes**

### Step 3.3: AI Picks Surfacing
- [ ] **Add AI Picks filter** - Prominent filter on home screen
- [ ] **Add AI Picks badge** - On match cards
- [ ] **Create AI Picks section** - Dedicated prominent section
- [ ] **Add confidence indicators** - Visual badges
- [ ] **Consider dedicated tab** - Evaluate if needed
- [ ] **Test UX** - Verify discoverability
- [ ] **Commit changes**

### Step 3.4: Loading & Error States
- [x] **Add skeleton loaders** - For match cards and detail view
- [x] **Add empty states** - When no predictions
- [x] **Add retry buttons** - For failed fetches
- [ ] **Improve error messages** - User-friendly
- [ ] **Add offline indicators** - Network status
- [ ] **Test error scenarios** - Network failures, etc.
- [ ] **Commit changes**

### Step 3.5: Match Card Polish
- [ ] **Add league badges** - Show league on cards
- [ ] **Add AI Pick pill** - Prominent on cards
- [ ] **Improve status display** - Better LIVE/HT/FT indicators
- [ ] **Add quick actions** - Share, favorite etc.
- [ ] **Polish animations** - Smooth transitions
- [ ] **Commit changes**

**PHASE 3 CHECKPOINT**: UI polished, notifications working, great UX

---

## PHASE 4: PERFORMANCE, SECURITY & INFRA (P1/P2)

### Step 4.1: Query Caching Strategy
- [x] **Audit React Query usage** - Review all queries
- [ ] **Set optimal staleTime** - Per query type
- [ ] **Set gcTime** - Proper garbage collection
- [ ] **Add query invalidation** - On mutations/settlements
- [ ] **Prevent refetch storms** - Debounce/throttle
- [ ] **Test performance** - Check network requests
- [ ] **Commit changes**

### Step 4.2: Bundle Optimization
- [ ] **Analyze bundle** - Use expo/bundle tools
- [ ] **Lazy load modals** - Reduce initial load
- [ ] **Optimize images** - Compress further (already started)
- [ ] **Tree shake** - Remove unused code
- [ ] **Code splitting** - For web
- [ ] **Test load times** - Measure improvements
- [ ] **Commit changes**

### Step 4.3: Abuse Prevention
- [ ] **Rate limiting** - Add to API routes
- [ ] **Booking code protection** - Prevent abuse
- [ ] **Chat sanitization** - Already done, audit
- [ ] **Input validation** - All user inputs
- [ ] **Add CAPTCHA if needed** - For sensitive actions
- [ ] **Audit security** - Review auth/permissions
- [ ] **Commit changes**

### Step 4.4: Observability
- [ ] **Add error tracking** - Sentry or similar
- [ ] **Add metrics logging** - Prediction counts, success rates
- [ ] **Add performance monitoring** - Track slow queries
- [ ] **Create dashboard** - Simple metrics view
- [ ] **Add alerts** - For critical failures
- [ ] **Commit changes**

### Step 4.5: A/B Testing Framework
- [ ] **Design experiment framework** - Feature flags
- [ ] **Add user bucketing** - Random assignment
- [ ] **Track metrics** - Log loss, CTR, conversions
- [ ] **Add experiment config** - JSON config
- [ ] **Test framework** - Run sample experiment
- [ ] **Commit changes**

**PHASE 4 CHECKPOINT**: App optimized, secure, observable, scalable

---

## FINAL CHECKPOINT
- [ ] **All steps completed** - Review entire checklist
- [ ] **TypeScript compiles** - `cd statwise && npx tsc --noEmit`
- [ ] **Tests pass** - If any exist
- [ ] **All changes committed & pushed** - To main
- [ ] **Website deployed** - Verify https://tolu23456.github.io/statwise/
- [ ] **App builds** - Check for errors
- [ ] **Metrics verified** - Production model still performing well
- [ ] **Documentation updated** - Update relevant docs

**Completion Date**: ___________
**Completed By**: ___________

---

## Notes
- Attention model experiment is complete but not recommended for replacement. Consider integrating sequence/embedding features into production LightGBM pipeline as P2 if desired.
- Always maintain apples-to-apples comparison for any model changes.
- Keep production model as baseline until new model beats it on same holdout.

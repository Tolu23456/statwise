# StatWise Improvement Plan (TODO - Must Complete In Order)

> **MANDATORY**: Complete each step in order from top to bottom. Do not skip steps. Mark each as complete only after verification. Work on ONE step at a time until finished, then move to next.

## Current State
- Production model: LightGBM ensemble, 48.44% accuracy, log loss 1.0138 on 2026-04-21 holdout
- Attention experiment: Trained but underperformed on available metrics (48.31% acc, 1.03378 log loss) - do NOT replace production
- App: Expo React Native + Next.js website deployed
- Polling: 5s interval (needs optimization)
- Booking codes: SportyBet working
- Settlement: ESPN + football-data.org

---

## PHASE 1: MODEL & PREDICTION IMPROVEMENTS (P0)

### Step 1.1: Add Richer Match Features
- [x] **Audit current features** - Review `ai/model/features.py` to understand feature pipeline
- [x] **Add xG features** - Integrate expected goals from available data sources
- [x] **Add shot metrics** - Shots on target, total shots, possession %
- [x] **Add rest days** - Calculate days since last match for home/away teams
- [ ] **Add travel/fatigue proxies** - Distance between venues if available, or use league context
- [ ] **Add form differentials** - Home form vs away form, goal difference trends
- [ ] **Test feature additions** - Retrain and verify on holdout don't degrade performance
- [ ] **Commit changes**

### Step 1.2: Weather & Match Context
- [x] **Create weather fetcher** - Add `ai/data/weather_fetcher.py` using Open-Meteo API (free)
- [x] **Integrate with prediction pipeline** - Add weather features to `features.py`
- [ ] **Add venue data** - City coordinates for stadiums where possible
- [ ] **Cache weather data** - Avoid repeated API calls for same matches
- [ ] **Test weather integration** - Verify features are added correctly
- [ ] **Commit changes**

### Step 1.3: Automated Weekly Retraining
- [ ] **Review retrain.py** - Understand current retraining workflow
- [x] **Create GitHub Action** - Add `.github/workflows/retrain-weekly.yml`
- [ ] **Add holdout validation** - Ensure retrain checks metrics against baseline
- [ ] **Add model versioning** - Track model versions in `model_info.json`
- [ ] **Test workflow** - Manual dispatch test
- [ ] **Commit changes**

### Step 1.4: Better Market Blending
- [ ] **Analyze current alphas** - Review per-league alpha values
- [ ] **Tune blending logic** - Optimize in `retrain.py`/`trainer.py`
- [ ] **Add closing line weight** - Prefer closing odds when available
- [ ] **Validate improvement** - Check blended log loss improves
- [ ] **Commit changes**

### Step 1.5: Expand Prediction Markets (BTTS/OU)
- [ ] **Design BTTS model** - Add both-teams-to-score prediction
- [ ] **Design Over/Under models** - Add O/U 2.5 and 3.5
- [ ] **Train separate heads** - Or extend joint model
- [ ] **Add to API/output** - Expose in predictions
- [ ] **Update UI** - Show additional markets in match detail
- [ ] **Commit changes**

**PHASE 1 CHECKPOINT**: All model improvements tested, metrics maintained or improved, no regressions

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
- [ [ ] **Chat sanitization** - Already done, audit
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
- [ [ ] **TypeScript compiles** - `cd statwise && npx tsc --noEmit`
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

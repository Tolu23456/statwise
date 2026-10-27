# StatWise – Developer Handoff Document

## Current Status: FULLY OPERATIONAL ✅

Both workflows are running in the Replit environment:
- **Start application** → Expo web app at port 5000 (login screen renders correctly)
- **AI Scheduler** → Python AI engine running, generating predictions every 20 minutes

---

## Operations update (2026-10-27, plan Phase 4)

CI/CD now lives in **GitHub Actions** (`.github/workflows/`): data fetch →
predict → settle → notify crons, weekly retrain, daily metrics
(`metrics-daily.yml` — dashboard in the run's step summary, failure
emails are the alert channel), `test-app.yml` (13 Node suites, 445
checks) and dispatch-only `bundle-report.yml` (export size + headless
load measurement).

**Pending one-time actions — run in the Supabase SQL editor (safe to
re-run, verification steps in each file header):**
1. `statwise/supabase-notifications-migration.sql` — push subscriptions
2. `statwise/supabase-security-migration.sql` — forum RPC rate limit +
   RLS locks (server-side half of plan 4.3)
3. `statwise/supabase-telemetry-migration.sql` — `client_events` table
   for error/performance telemetry (plan 4.4)

All three no-op safely until run. Known open security finding: tier
upgrades are client-attested after the Flutterwave callback; closing it
needs server-side payment verification (webhook/edge function holding
the Flutterwave secret) — see the header of the security migration.

---

## Project Overview

StatWise is an AI-powered sports prediction platform. It combines:
1. A **React Native / Expo** mobile-first frontend served as a web app
2. A **Python AI backend** with a C++-accelerated LightGBM prediction engine

Users authenticate via Supabase, see predictions based on their subscription tier
(Free / Premium / VIP / VVIP), and can subscribe via Flutterwave payments.

---

## Replit Environment Setup

### Workflows
| Workflow | Command | Port |
|---|---|---|
| Start application | `cd statwise && npm run dev` | 5000 |
| AI Scheduler | `cd /home/runner/workspace && python3 ai/scheduler.py` | — |

### Scripts (statwise/package.json)
```
"dev": "EXPO_NO_DEVTOOLS=1 node node_modules/.bin/expo start --web --port 5000 --clear"
```
> Uses `node node_modules/.bin/expo` directly (not `npx expo`) to avoid interactive prompts.

### Key dependencies installed (beyond package.json)
- `react-dom@19.2.0` (required for Expo web)
- `react-native-worklets@0.7.2` (required by reanimated v4)
- `react@19.2.0` (pinned to match react-dom)
- Python: `numpy pandas scikit-learn lightgbm scipy requests supabase joblib schedule`

---

## AI Engine Architecture

### Model (ai/model/)

| Component | Details |
|---|---|
| **Outcome model** | LightGBM — either a 3-class 1X2 head or a joint 6-class outcome×goals head (winner picked on an out-of-time holdout) |
| **Goals model** | Over/Under 2.5 (second head, or the marginal of the joint model) |
| **Features** | 161 — see `ai/AI_DOCUMENTATION.md` |
| **Training** | GitHub Actions `Train Model` workflow on ~330k clean matches (`ai/data/clean/`) |
| **Calibration** | Isotonic on the earlier holdout half, deployed only if it wins out-of-sample |
| **Market blend** | Per-league alpha learned on the later holdout half (rows with odds only) |
| **Model file** | `ai/models/football_predictor.pkl` |

### Feature Set (161 features)

| Group | Features | Count |
|---|---|---|
| Elo ratings | elo_home, elo_away, elo_diff, elo_prob_home/draw/away | 6 |
| Home form | win/draw/loss rate, goals, momentum, PPG, CS rate, scoring rate | 10 |
| Away form | same as home | 10 |
| Head-to-head | win rates, goals per side, match count | 6 |
| Goal probabilities | p_over25, p_btts (Poisson model) | 2 |
| Differentials | form_win_diff, form_goals_diff, momentum_diff, ppg_diff | 4 |
| Market / odds | implied probs (normalised), market overround | 4 |
| Attack/defense | Poisson strength ratings (4 values) | 4 |
| **NEW** Venue form | home team's home win rate/PPG; away team's away win rate/PPG | 4 |
| **NEW** Streaks | current streak (normalised ±1) for each team | 2 |
| **NEW** Form trend | recent-5 vs prev-5 PPG delta for each team | 2 |
| **NEW** Consistency | inverse std-dev of goals scored | 2 |
| **NEW** H2H extended | avg total goals, H2H home advantage factor | 2 |
| **NEW** League context | league average goals per match | 1 |
| **NEW** Venue PPG diff | home team's home PPG minus away team's away PPG | 1 |

### Match Coverage

Sources are now queried **simultaneously** (not cascade) and merged/deduped:

| Priority | Source | Key | Leagues |
|---|---|---|---|
| 1 | football-data.org | `FOOTBALL_API_TOKEN` env var | 8 |
| 2 | API-Football (RapidAPI) | `X_RAPIDAPI_KEY` env var | 12 |
| 3 | TheSportsDB | free, no key | 14 |
| 4 | Mock fixtures | none needed | 50+ fixtures across 10 leagues |

### Scheduler (ai/scheduler.py)

- **Prediction cycle**: every 20 minutes, fetches 14 days of upcoming matches
- **Auto-retrain**: every 24 hours, retrains the model on fresh data
- **Heartbeat**: writes `ai/data/heartbeat.json` with status, prediction count, and league count
- **Graceful shutdown**: handles SIGTERM/SIGINT
- **One-shot mode**: `python ai/scheduler.py --once` — runs one cycle then exits (used by GitHub Actions)

### GitHub Actions (`.github/workflows/predict.yml`)

- Runs automatically **every 4 hours** via cron
- Can also be triggered manually from the GitHub Actions tab
- Uses `FOOTBALL_API_TOKEN` GitHub secret for football-data.org live data
- Optionally uses `SUPABASE_SERVICE_KEY` GitHub secret to bypass Supabase RLS
- Caches the trained model between runs (skips retraining when model is fresh)
- Falls back to pure-Python feature engine (no C++ lib needed in CI)

### Tier System

Tiers are driven by the **certainty score**, not raw confidence. Certainty combines
the model's probability split, the margin between the top two outcomes and the
agreement between the ensemble heads (`ai/model/trainer.py`).

| Tier | Certainty Range | DB value | Picks/day |
|---|---|---|---|
| Free | 0–40 | `free` | 5 |
| Premium | 40–55 | `premium` | 25 |
| VIP | 55–65 | `vip` | 75 |
| VVIP | 65–100 | `vvip` | unlimited |

`AI_PICK_CERTAINTY = 65.0` (`ai/model/predictor.py`) — the **AI Pick** badge is
awarded at exactly this threshold, so the VVIP tier and the AI Pick set are the
same matches. Picks the model flags as uncertain are forced down to the free tier.

Per-prediction model output lives inside the existing `stats` JSONB column (no
schema migration needed): `certainty`, `is_ai_pick`, `uncertainty`,
`prob_home` / `prob_draw` / `prob_away`, `value_edge`, `odds_home` / `odds_draw` /
`odds_away`.

### Data integrity guards

| Guard | Where | Why |
|---|---|---|
| `.env` is loaded before any API key is read | `ai/env_loader.py`, used by `ai/scheduler.py` + `ai/model/live_fetcher.py` | Without it the fetcher silently degraded to placeholder fixtures |
| Mock fixtures are never published | `ALLOW_MOCK_FIXTURES` env gate (`ai/model/live_fetcher.py`, `ai/model/predictor.py`) | Placeholder "Arsenal vs Chelsea" rows were being written to the live table and shown to users as real predictions |
| Duplicate fixtures are pruned after each push | `PredictionEngine.prune_duplicate_predictions` | Two id schemes (`575368` vs `fdorg_575368`) left every fixture stored twice |
| Settlement columns detected before writing | `PredictionEngine.settle_past_predictions` | The `actual_result` column is created by `statwise/supabase-settlement-migration.sql`; without it the accuracy screen stays empty |

---

## Frontend (statwise/)

### App Structure
```
app/
  _layout.tsx          – Root layout (auth guard, providers, fonts)
  (auth)/
    _layout.tsx        – Auth stack
    login.tsx          – Login screen
    signup.tsx         – Signup + email verification notice
    forgot-password.tsx – Password reset
  (tabs)/
    _layout.tsx        – Tab bar (5 tabs)
    index.tsx          – Home / Predictions (league filter, search)
    insights.tsx       – VIP Insights (locked for free/premium)
    subscriptions.tsx  – Subscription plans (Free/Premium/VIP/VVIP)
    forum.tsx          – Real-time community forum (Supabase realtime)
    profile.tsx        – User profile, photo upload, referral code, settings

constants/colors.ts    – Dark/light theme colors + tier colors
lib/supabase.ts        – Supabase client (SecureStore auth storage)
lib/format.ts          – Shared date/time/odds display helpers
context/AuthContext.tsx – Auth state, profile loading, session management
components/
  ErrorBoundary.tsx    – App crash recovery
  PredictionCard.tsx   – Prediction card (prob bars, badges, relative kickoff)
  PredictionDetailModal.tsx – Full analysis sheet (model vs market, certainty)
  OnboardingModal.tsx  – First-run intro carousel
  SkeletonCard.tsx     – Loading placeholder
```

### UI/UX highlights

| Area | Detail |
|---|---|
| Prediction card | Three-way probability bars (H/D/A, pick highlighted), AI Pick badge, VALUE badge, relative kickoff ("Today 19:45", "in 2h 15m", "Live"), certainty chip, odds labelled with the correct outcome |
| Home feed | Date group headers (Today / Tomorrow / Sat 25 Oct) with counts, per-league tab counts, result count, last-updated timestamp, free-tier upsell banner |
| Detail sheet | "Pico's Probability" vs market-implied probability, value/edge row, model-certainty section (entropy + head agreement), share button |
| Profile | "Your Plan" card with tier perks, subscription expiry date and an upgrade CTA |
| Plans | Certainty band per tier, honest measured-accuracy ranges (no fabricated win-rate/ROI claims) |
| Community | Day dividers, chat-style timestamps, character counter |
| Model info | "Pico" chip in the home header opens a full explanation of the model, certainty and accuracy disclaimer |

### Configuration
- **Supabase URL**: `https://fwpriiugfjhtoymcbmso.supabase.co`
- **Supabase Anon Key**: in `lib/supabase.ts`
- **Flutterwave Public Key**: `FLWPUBK-30eeb76b5875f40db71221d0960de0a8-X`

### Tech Stack

| Category | Technology |
|---|---|
| Framework | Expo SDK 55, React Native 0.83 |
| Routing | Expo Router 5 (file-based) |
| Auth + DB | Supabase (PostgreSQL, Auth, Realtime) |
| State | React Query (@tanstack/react-query v5) |
| Styling | React Native StyleSheet |
| Fonts | Inter (400–700) via @expo-google-fonts/inter |
| Icons | @expo/vector-icons (Ionicons) |
| Payments | Flutterwave (WebView integration – partially done) |
| Storage | expo-secure-store (auth session) |

---

## What Still Needs To Be Done

### HIGH PRIORITY

1. **Run the schema migration in Supabase** ⚠️
   - `statwise/supabase-settlement-migration.sql` (idempotent)
   - Adds `actual_result` / `home_score` / `away_score` / `settled_at` to `predictions`
   - Creates `odds_snapshots` and `inplay_states`
   - Until this runs: the "Prediction Accuracy" screen stays empty and the Odds Sync workflow crashes
   - Run it in Supabase → SQL Editor → New query → paste → Run

2. **Payment Integration (Flutterwave)**
   - Subscriptions screen shows plans but payment is not wired up
   - Use `expo-web-browser` or `expo-webview` to open Flutterwave checkout
   - On success, update `user_profiles.current_tier` in Supabase
   - The `FLWPUBK` key is already in `lib/supabase.ts`

3. **App Icons**
   - Current icons in `assets/images/` are placeholders
   - Generate proper icons: `icon.png` (1024×1024), `splash-icon.png` (512×512)
   - Use the media-generation skill

4. **API Keys for Live Data**
   - `FOOTBALL_API_TOKEN` is already a GitHub secret and is used by `.github/workflows/predict.yml`
   - Optionally add `SUPABASE_SERVICE_KEY` as a GitHub secret to bypass Supabase RLS in CI
   - Add `X_RAPIDAPI_KEY` as a GitHub secret for API-Football if more league coverage is needed
   - `THESPORTSDB_KEY` — the shared public test key now returns 403, so that source is disabled unless a personal key is set
   - `ODDS_API_KEY` — currently returns 403 from this network; without it there is no market price, so `value_edge` stays null and the VALUE badge never appears

### MEDIUM PRIORITY

5. **Push Notifications**
   - Use `expo-notifications` for match alerts and prediction updates

6. **Profile Image Bucket**
   - Ensure `profile-pictures` bucket exists and is public in Supabase Storage

7. **Subscription Management**
   - View current subscription details, cancel flow

8. **Tennis / basketball / baseball / volleyball models**
   - Datasets are harvested and documented in `ai/data/READMES/SPORTS_DATASETS.md`
   - Still needed: per-sport normalisers, feature builders, a `sport` column on `predictions` and a sport filter on the home tab

### LOW PRIORITY

9. **Offline Support**
   - `@react-native-community/netinfo` for offline detection

10. **Settled-prediction backfill**
   - Historical matches before today are not in the table, so the accuracy screen only accumulates from now on

---

## How to Start the Next Session

1. Read `replit.md` for full project context
2. Check `refresh_all_logs` for any workflow errors
3. The AI Scheduler will automatically retrain every 24 hours
4. To force a retrain manually: delete `ai/models/football_predictor.pkl` and restart AI Scheduler

## Important Notes

- **DO NOT** run `npx expo start` directly — use `restart_workflow`
- **DO NOT** edit package.json directly for installs — use the package-management skill
- The C++ library (`ai/libstatwise.so`) speeds up Elo/form computation; the system has a full Python fallback if it's unavailable
- Old PWA files (index.html, main.js, styles.css, Pages/) are still in the statwise/ root — harmless but can be cleaned up
- The model version check in `trainer.py` ensures a retrain is triggered automatically if the feature count changes

# StatWise – Developer Handoff Document

## Current Status: FULLY OPERATIONAL ✅

Both workflows are running in the Replit environment:
- **Start application** → Expo web app at port 5000 (login screen renders correctly)
- **AI Scheduler** → Python AI engine running, generating predictions every 20 minutes

---

## Project Overview

StatWise is an AI-powered sports prediction platform. It combines:
1. A **React Native / Expo** mobile-first frontend served as a web app
2. A **Python AI backend** with a C++-accelerated XGBoost + LightGBM prediction engine

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
- Python: `numpy pandas scikit-learn xgboost lightgbm scipy requests supabase joblib schedule`

---

## AI Engine Architecture

### Model (ai/model/)

| Component | Details |
|---|---|
| **Outcome model** | CalibratedXGBClassifier + CalibratedHistGBClassifier blended 50/50 |
| **Goals model** | Same ensemble for Over/Under 2.5 goals |
| **Features** | 60 features (was 45) — see table below |
| **Training** | Historical CSVs in `ai/data/`, sampled every 3rd match (was every 6th) |
| **Lookback window** | 400 matches per training sample (was 300) |
| **Model file** | `ai/models/football_predictor.pkl` |

### Feature Set (60 features)

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
| 1 | football-data.org | `FOOTBALL_API_KEY` env var | 8 |
| 2 | API-Football (RapidAPI) | `X_RAPIDAPI_KEY` env var | 12 |
| 3 | TheSportsDB | free, no key | 14 |
| 4 | Mock fixtures | none needed | 50+ fixtures across 10 leagues |

### Scheduler (ai/scheduler.py)

- **Prediction cycle**: every 20 minutes, fetches 14 days of upcoming matches
- **Auto-retrain**: every 24 hours, retrains the model on fresh data
- **Heartbeat**: writes `ai/data/heartbeat.json` with status, prediction count, and league count
- **Graceful shutdown**: handles SIGTERM/SIGINT

### Tier System

| Tier | Confidence Range | DB value |
|---|---|---|
| Free | 0–55% | `free` |
| Premium | 55–70% | `premium` |
| VIP | 70–82% | `vip` |
| VVIP | 82–100%+ | `vvip` |

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
context/AuthContext.tsx – Auth state, profile loading, session management
components/
  ErrorBoundary.tsx    – App crash recovery
  PredictionCard.tsx   – Prediction display card
```

### Configuration
- **Supabase URL**: `https://pdrcyuzfdqjnsltqqxvr.supabase.co`
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

1. **Payment Integration (Flutterwave)**
   - Subscriptions screen shows plans but payment is not wired up
   - Use `expo-web-browser` or `react-native-webview` to open Flutterwave checkout
   - On success, update `user_profiles.current_tier` in Supabase
   - The `FLWPUBK` key is already in `lib/supabase.ts`

2. **App Icons**
   - Current icons in `assets/images/` are placeholders
   - Generate proper icons: `icon.png` (1024×1024), `splash-icon.png` (512×512)
   - Use the media-generation skill

3. **API Keys for Live Data**
   - Add `FOOTBALL_API_KEY` (football-data.org free tier) to environment secrets
   - Add `X_RAPIDAPI_KEY` for API-Football if more coverage is needed
   - Without keys, the system falls back to TheSportsDB (14 leagues, no key needed)

### MEDIUM PRIORITY

4. **Push Notifications**
   - Use `expo-notifications` for match alerts and prediction updates

5. **Tier Expiry Dates**
   - Show subscription expiry in the profile and subscriptions screens

6. **Profile Image Bucket**
   - Ensure `profile-pictures` bucket exists and is public in Supabase Storage

7. **Subscription Management**
   - View current subscription details, cancel flow

### LOW PRIORITY

8. **Privacy Policy & Terms of Service**
   - Create `app/privacy-policy.tsx` and `app/terms-of-service.tsx`

9. **Offline Support**
   - `@react-native-community/netinfo` for offline detection

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

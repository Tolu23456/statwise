# StatWise - AI Sports Prediction Platform

## Overview
StatWise is an AI-powered sports prediction platform. It combines a React Native / Expo mobile-first frontend served as a web app on port 5000, and a Python AI backend with an XGBoost prediction engine that runs as a background scheduler.

## User Preferences
Preferred communication style: Simple, everyday language.

## Tech Stack

### Frontend (statwise/)
- **Framework:** Expo SDK 55, React Native 0.83, served as a Progressive Web App
- **Routing:** Expo Router 5 (file-based)
- **State:** React Query (@tanstack/react-query v5)
- **Auth:** Supabase Auth with expo-secure-store
- **Fonts:** Inter via @expo-google-fonts/inter
- **Icons:** @expo/vector-icons (Ionicons)
- **Port:** 5000

### AI Backend (ai/)
- **Language:** Python 3.11
- **Model:** 5-model deep stacking ensemble: XGBoost + HistGB + ExtraTrees + RandomForest + PyTorch NeuralNet → LogisticRegressionCV meta-learner
- **Features:** 98 features across 22 groups (Elo, Attack/Defence Elo, Dixon-Coles score matrix, Poisson goal probs, H2H, venue-split form, consecutive runs, goals variance, etc.)
- **C++ kernel:** ai/libstatwise.so — 11 exported functions including Dixon-Coles, attack/defence Elo, goals variance, venue-split form, consecutive run counters
- **Schedule:** Predictions every 20 minutes, model retrain every 24 hours
- **Entry point:** ai/scheduler.py
- **Anti-bias measures:**
  - Balanced sample weights at training time (inverse-frequency per class: home 0.74×, draw 1.33×, away 1.12×)
  - PyTorch NeuralNet: class-frequency CrossEntropyLoss weights merged with per-class sample weights
  - Draw detection floor: draw predicted when P(draw)≥0.255 AND home/away gap ≤0.14
  - Away boost: predict away when P(away) ≥ P(home) - 0.03
  - ET + RF base estimators use `class_weight='balanced'`
  - Draw confidence capped at 62% to avoid overconfidence

### Backend (Supabase - Serverless)
- **URL:** https://pdrcyuzfdqjnsltqqxvr.supabase.co
- **Auth:** Supabase Auth (email/password)
- **Database:** PostgreSQL via Supabase
- **Storage:** Supabase Storage (profile-pictures bucket)
- **Realtime:** Supabase Realtime subscriptions (forum + predictions)

### Payments
- **Flutterwave:** Public Key `FLWPUBK-30eeb76b5875f40db71221d0960de0a8-X`
- **Integration:** Inline checkout JS (web) — loads from checkout.flutterwave.com/v3.js dynamically

## Project Structure

```
statwise/              - Expo frontend app
  app/
    _layout.tsx        - Root layout (ThemeProvider, AuthProvider, QueryClient, notifications)
    (auth)/            - Auth screens (login, signup, forgot-password)
    (tabs)/            - Main tabs (index, insights, subscriptions, forum, profile)
    insight-detail.tsx - Full insight reader (VIP+)
    privacy-policy.tsx - Privacy policy screen
    terms-of-service.tsx - Terms of service screen
    backtesting.tsx    - Prediction accuracy / backtesting dashboard
  components/          - Shared UI components (PredictionCard, ErrorBoundary)
  constants/           - Colors (dark + light themes), tier colors
  context/
    AuthContext.tsx    - Auth state, profile loading
    ThemeContext.tsx   - System/light/dark theme with AsyncStorage persistence
  lib/supabase.ts      - Supabase client + types (Prediction, UserProfile, ReferralCode, etc.)

ai/                    - Python AI prediction engine
  scheduler.py         - Main entry point (runs on startup, includes backtesting settle)
  model/
    live_fetcher.py    - Fetches fixtures from football-data.org, API-Football, TheSportsDB
    predictor.py       - PredictionEngine (run, push, settle_past_predictions)
    trainer.py         - FootballPredictor ML model
    downloader.py      - Training data download
  models/              - Saved model binaries (.pkl)
  data/                - Historical CSVs, logs, heartbeat.json
```

## Workflows
| Workflow | Command | Port |
|---|---|---|
| Start application | `cd statwise && npm run dev` | 5000 |
| AI Scheduler | `python3 ai/scheduler.py` | — |

## Subscription Tiers
| Tier | Confidence | Daily Price | DB Value |
|------|------------|-------------|----------|
| Free | 0–55% | ₦0 | `free` |
| Premium | 55–70% | ₦500 | `premium` |
| VIP | 70–82% | ₦2,000 | `vip` |
| VVIP | 82–100% | ₦5,000 | `vvip` |

## Backend Prediction Locking
- Predictions query filters by `tier IN (allowed_tiers)` based on user's current tier
- Free users: only fetch `tier = 'free'` predictions
- Premium: `tier IN ('free', 'premium')` — etc.
- Prevents predictions being fetched at all for unauthorized tiers (true backend locking)

## Database Tables (Supabase)
- `user_profiles` - User data, tier, referral tracking, notifications flag
- `predictions` - Match predictions with confidence/odds/tier/actual_result/status
- `forum_messages` - Community messages (realtime)
- `referral_codes` - User referral codes + total_referrals count
- `insights` - VIP exclusive content (falls back to mock data if empty)
- `payment_transactions` - Payment history (tx_ref, plan_id, tier, status)

## Prediction Columns (predictions table)
Key: `match_id` (used for upsert deduplication)
- `tier` — DB tier value (free/premium/vip/vvip)
- `tier_required` — Tier name (Free Tier/Premium Tier/VIP Tier/VVIP Tier)
- `status` — upcoming | completed
- `actual_result` — Home Win | Away Win | Draw (filled by backtesting)
- `home_score`, `away_score` — set when settled
- `odds_home`, `odds_draw`, `odds_away` — real odds from API-Football (RapidAPI)

## Supported Leagues (19+)
Premier League, La Liga, Bundesliga, Serie A, Ligue 1, UEFA Champions League,
Eredivisie, Primeira Liga, MLS, Turkish Super Lig, Belgian Pro League,
Scottish Premiership, Brasileirao, Argentine Primera, EFL Championship,
Copa Libertadores, J1 League, Liga MX, Saudi Pro League

## Features Implemented
- AI predictions with backend tier locking (fetch filter, not UI hide)
- Flutterwave inline checkout payment (web) — loads checkout.flutterwave.com/v3.js
- ThemeContext: system/light/dark toggle, persisted to AsyncStorage
- Push notifications: browser Notification API on web, permission requested on login
- Live prediction badge: Supabase Realtime subscription + animated banner
- Drag-to-refresh (RefreshControl) across all list screens
- Referral program: code display, copy, reward points display
- VIP Insights with full "Read More" detail view
- Backtesting dashboard: accuracy stats by league, pending/correct/incorrect
- Privacy Policy and Terms of Service screens
- AI-generated app icons (icon.png, adaptive-icon.png, splash-icon.png)
- Backtesting settle: AI scheduler fetches actual results from TheSportsDB

## Important Notes
- Do NOT run `npx expo start` directly — use restart_workflow
- The C++ library (ai/libstatwise.so) speeds up computation; Python fallback exists
- Supabase anon key is safe to expose in frontend code (it's a public key)
- AI Scheduler writes heartbeat to ai/data/heartbeat.json
- Theme preference stored in AsyncStorage under key `statwise_theme_mode`
- Flutterwave secret key NOT needed — using inline checkout with public key only

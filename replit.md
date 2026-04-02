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
- **Model:** XGBoost + HistGBClassifier ensemble (60 features)
- **Schedule:** Predictions every 20 minutes, model retrain every 24 hours
- **Entry point:** ai/scheduler.py

### Backend (Supabase - Serverless)
- **URL:** https://pdrcyuzfdqjnsltqqxvr.supabase.co
- **Auth:** Supabase Auth (email/password)
- **Database:** PostgreSQL via Supabase
- **Storage:** Supabase Storage (profile-pictures bucket)
- **Realtime:** Supabase Realtime subscriptions (forum)

### Payments
- **Flutterwave:** Public Key `FLWPUBK-30eeb76b5875f40db71221d0960de0a8-X`

## Project Structure

```
statwise/              - Expo frontend app
  app/
    _layout.tsx        - Root layout (auth guard, font loading, providers)
    (auth)/            - Auth screens (login, signup, forgot-password)
    (tabs)/            - Main tabs (index, insights, subscriptions, forum, profile)
  components/          - Shared UI components
  constants/           - Colors and theme
  context/             - AuthContext
  lib/supabase.ts      - Supabase client + types

ai/                    - Python AI prediction engine
  scheduler.py         - Main entry point (runs on startup)
  model/               - PredictionEngine, trainer, fetcher
  models/              - Saved model binaries (.pkl)
  data/                - Historical CSVs, logs, heartbeat.json
```

## Workflows
| Workflow | Command | Port |
|---|---|---|
| Start application | `cd statwise && npm run dev` | 5000 |
| AI Scheduler | `python3 ai/scheduler.py` | — |

## Subscription Tiers
| Tier | Confidence | Daily Price |
|------|------------|-------------|
| Free | 0–55% | ₦0 |
| Premium | 55–70% | ₦500 |
| VIP | 70–82% | ₦2,000 |
| VVIP | 82–100% | ₦5,000 |

## Database Tables (Supabase)
- `user_profiles` - User data, tier, referral tracking
- `predictions` - Match predictions with confidence/odds
- `forum_messages` - Community messages
- `referral_codes` - User referral codes
- `insights` - VIP exclusive content
- `payment_transactions` - Payment history

## Important Notes
- Do NOT run `npx expo start` directly — use restart_workflow
- The C++ library (ai/libstatwise.so) speeds up computation; Python fallback exists
- Supabase anon key is safe to expose in frontend code (it's a public key)
- AI Scheduler writes heartbeat to ai/data/heartbeat.json

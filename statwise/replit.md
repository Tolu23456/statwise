# StatWise - AI Sports Prediction Expo App

## Overview
StatWise is an AI-powered sports prediction mobile app built with Expo (React Native). It provides users with match predictions, confidence levels, and odds across multiple sports leagues. Features a tiered subscription model (Free, Premium, VIP, VVIP), a referral system, and a real-time community forum.

## User Preferences
Preferred communication style: Simple, everyday language.

## Tech Stack

### Frontend (Expo / React Native)
- **Framework:** Expo SDK 55, React Native 0.84
- **Routing:** Expo Router 5 (file-based, similar to Next.js)
- **State:** React Query (@tanstack/react-query v5) for server state
- **Auth:** Supabase Auth with expo-secure-store for session persistence
- **Fonts:** Inter (400/500/600/700) via @expo-google-fonts/inter
- **Icons:** @expo/vector-icons (Ionicons)
- **Styling:** React Native StyleSheet

### Backend (Supabase - Serverless)
- **URL:** https://pdrcyuzfdqjnsltqqxvr.supabase.co
- **Auth:** Supabase Auth (email/password)
- **Database:** PostgreSQL via Supabase (same schema as the original PWA)
- **Storage:** Supabase Storage (`profile-pictures` bucket)
- **Realtime:** Supabase Realtime subscriptions (forum messages)

### Payments
- **Flutterwave:** Public Key `FLWPUBK-30eeb76b5875f40db71221d0960de0a8-X`
- Payment WebView integration is pending (see HANDOFF.md)

## Project Structure

```
app/
  _layout.tsx          - Root layout (auth guard, font loading, providers)
  (auth)/
    _layout.tsx        - Auth stack layout
    login.tsx          - Login screen
    signup.tsx         - Signup + email verification
    forgot-password.tsx - Password reset
  (tabs)/
    _layout.tsx        - Tab bar navigation (5 tabs)
    index.tsx          - Home / Today's Predictions
    insights.tsx       - Exclusive Insights (VIP/VVIP only)
    subscriptions.tsx  - Subscription plans
    forum.tsx          - Real-time community forum
    profile.tsx        - User profile & settings

assets/images/         - App icons (placeholder — regenerate with media-generation skill)
constants/colors.ts    - Dark/light theme colors + tier badge colors
lib/supabase.ts        - Supabase client + type definitions
context/AuthContext.tsx - Auth state, profile, session management
components/
  ErrorBoundary.tsx    - Error recovery component
  PredictionCard.tsx   - Prediction display card
```

## Screens & Features

### Auth Flow
- Login → checks email verification → routes to tabs
- Signup → sends verification email → shows notice
- Forgot Password → email reset link

### Home (Predictions)
- Fetches today's predictions from `predictions` Supabase table
- League filter tabs (scrollable)
- Search by team/league/prediction
- Tier-based locking (predictions lock for lower tiers)
- Pull to refresh

### Insights
- VIP/VVIP exclusive content from `insights` table
- Falls back to mock data if table doesn't exist
- Lock screen with upgrade CTA for lower tiers

### Subscriptions
- Shows current tier
- Daily/Monthly toggle
- 4 tiers: Free (₦0), Premium (₦500/day), VIP (₦2000/day), VVIP (₦5000/day)
- Flutterwave payment integration (alert placeholder — WebView needed)

### Forum
- Real-time messages via Supabase channel subscriptions
- Shows sender name, tier badge, and timestamp
- Supports multi-line messages

### Profile
- Avatar display (Supabase Storage upload)
- Edit display name
- Show referral code (copy on tap)
- Notifications toggle
- Sign out

## Subscription Tiers
| Tier | Predictions/day | Daily Price |
|------|-----------------|-------------|
| Free Tier | 5 | ₦0 |
| Premium Tier | 25 | ₦500 |
| VIP Tier | 75 | ₦2,000 |
| VVIP Tier | Unlimited | ₦5,000 |

## Database Tables (Supabase)
- `user_profiles` - User data, tier, referral tracking
- `predictions` - Match predictions with confidence/odds
- `forum_messages` - Community messages
- `referral_codes` - User referral codes
- `insights` - VIP exclusive content (may need to be created)
- `payment_transactions` - Payment history

## Environment Config
All credentials are hardcoded in `lib/supabase.ts` (Supabase anon key is safe for frontend).

## Original PWA Files
The original vanilla JS PWA files (index.html, main.js, styles.css, Pages/, etc.) are still in the root directory. They can be safely removed once the Expo app is fully verified.

## GitHub Actions — Football Data Pipeline

Automated workflow in `.github/workflows/fetch-football-data.yml`:
- **Schedule:** runs every 6 hours
- **Trigger:** also runs on push to main (when the script/workflow file changes) and manually
- **Script:** `.github/scripts/fetch_football_data.js`
- **Data source:** football-data.org API (competitions: PL, PD, BL1, SA, FL1, CL, MLS, ELC, DED, PPL)
- **Output:** upserts into Supabase `predictions` table (keyed on `match_id`)

### Required GitHub Secrets
Add these in your GitHub repo → Settings → Secrets and variables → Actions:
| Secret | Description |
|--------|-------------|
| `FOOTBALL_DATA_TOKEN` | Your football-data.org API token (already set) |
| `SUPABASE_URL` | `https://pdrcyuzfdqjnsltqqxvr.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Found in Supabase → Settings → API → service_role key |

### Database Setup
Run `supabase-schema.sql` in your Supabase SQL editor for a fresh setup.
If tables already exist, run `supabase-migration-predictions.sql` to add the missing columns.

## Pending Items
1. Flutterwave WebView payment integration
2. Better app icons (use media-generation skill)
3. Tier expiry dates display
4. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to GitHub Actions secrets

## Recent Changes (March 30, 2026)
- Converted entire app from vanilla JavaScript PWA to Expo (React Native)
- Set up Expo Router with 5-tab navigation
- Connected to existing Supabase backend
- Added real-time forum using Supabase Realtime
- Created HANDOFF.md for session continuity

# StatWise Expo Conversion - Agent Handoff Document

## Current Status: APP IS RUNNING ✅
The Expo app is running at port 5000 and the login screen renders correctly.
The workflow is named **"Start Frontend"** and runs with:
```
EXPO_NO_DEVTOOLS=1 npx expo start --web --port 5000 --clear
```

---

## Project Overview
StatWise is an AI-powered sports prediction app. It has been converted from a vanilla JavaScript PWA to a React Native Expo app. The conversion is **partially complete** — the core structure, all screens, and auth are done. See below for what still needs work.

## Last Commit at Handoff
**Commit:** `6c5b7e1a8a62236b0053e3a3f3c17c46e7d693a8`
**Message:** "Saved your changes before starting work"
**Date:** March 30, 2026

## What Was Done ✅

### Project Setup
- Installed Expo SDK 55, expo-router 55, React Native 0.84
- Set `"main": "expo-router/entry"` in `package.json`
- Created `app.json`, `babel.config.js`, `tsconfig.json`, `metro.config.js`

### Files Created
```
app/
  _layout.tsx          - Root layout (auth guard, providers, fonts)
  (auth)/
    _layout.tsx        - Auth stack
    login.tsx          - Login screen
    signup.tsx         - Signup + email verification notice
    forgot-password.tsx - Password reset
  (tabs)/
    _layout.tsx        - Tab bar (5 tabs)
    index.tsx          - Home / Predictions (with league filter, search)
    insights.tsx       - VIP Insights (locked for free/premium)
    subscriptions.tsx  - Subscription plans (Free/Premium/VIP/VVIP)
    forum.tsx          - Real-time community forum (Supabase realtime)
    profile.tsx        - User profile, photo upload, referral code, settings

assets/images/
  icon.png, splash-icon.png, adaptive-icon.png, favicon.png (placeholder)

constants/colors.ts    - Dark/light theme colors + tier colors
lib/supabase.ts        - Supabase client (SecureStore auth storage)
context/AuthContext.tsx - Auth state, profile loading, session management
components/
  ErrorBoundary.tsx    - App crash recovery
  PredictionCard.tsx   - Prediction display card
```

### Configuration
- **Supabase URL:** `https://fwpriiugfjhtoymcbmso.supabase.co`
- **Supabase Anon Key:** In `lib/supabase.ts`
- **Flutterwave Public Key:** `FLWPUBK-30eeb76b5875f40db71221d0960de0a8-X`

## What Still Needs To Be Done ⚠️

### HIGH PRIORITY

1. **Set up Expo Workflows in .replit**
   - Update `.replit` to run `npx expo start --port 8081`
   - Set `waitForPort = 8081`
   - Remove the old `StatWise PWA Server` workflow
   - Read the `workflows` skill before doing this

2. **Test the App**
   - Start the workflow and check logs for errors
   - Fix any import or type errors
   - Take a screenshot to verify it renders correctly

3. **App Icons**
   - The current icons in `assets/images/` are basic placeholders (dark bg + blue rect)
   - Generate proper app icons using the `media-generation` skill
   - Target: `assets/images/icon.png` (1024x1024), `splash-icon.png` (512x512)

4. **Payment Integration**
   - The subscriptions screen shows plans but payment is not wired up
   - Flutterwave has no official React Native SDK; use a WebView approach
   - Install `expo-web-browser` or `react-native-webview`
   - On "Pay Now" press, open Flutterwave's inline payment page in a WebView
   - After payment success, update `user_profiles.current_tier` in Supabase
   - The FLWPUBK is already in `lib/supabase.ts`

5. **Admin: Add Predictions to Database**
   - The home screen fetches from `predictions` table in Supabase
   - If the table is empty, the home screen shows empty state
   - Add some test predictions to the database OR implement an admin screen

### MEDIUM PRIORITY

6. **Image Assets for Profile**
   - `expo-image` is installed and used in `profile.tsx`
   - Profile photo upload uploads to `profile-pictures` Supabase Storage bucket
   - Ensure the bucket exists in Supabase and is public

7. **Push Notifications**
   - The original app used Firebase Cloud Messaging
   - Consider using `expo-notifications` instead
   - Implement in a later phase

8. **Tier Expiry Dates**
   - The original app showed subscription expiry dates
   - Currently not displayed in the Expo app
   - Add expiry logic to the profile and subscriptions screens

9. **Subscription Management**
   - Add the ability for users to view their current subscription details
   - Add cancel subscription flow

### LOW PRIORITY

10. **Privacy Policy & Terms of Service**
    - Create `app/privacy-policy.tsx` and `app/terms-of-service.tsx`
    - Link from the signup screen and profile

11. **Offline Support**
    - The original was a PWA with service workers
    - Consider `@react-native-community/netinfo` for offline detection

## Tech Stack Summary

| Category | Technology |
|----------|------------|
| Framework | Expo SDK 55, React Native 0.84 |
| Routing | Expo Router 5 (file-based) |
| Auth + DB | Supabase (existing project) |
| State | React Query (@tanstack/react-query v5) |
| Styling | React Native StyleSheet |
| Fonts | Inter (400/500/600/700) via @expo-google-fonts/inter |
| Icons | @expo/vector-icons (Ionicons) |
| Payments | Flutterwave (needs WebView integration) |
| Storage | expo-secure-store (auth session) |

## Key Installed Packages
```json
"expo": "^55.0.9",
"expo-router": "^55.0.8",
"react-native": "^0.84.1",
"@supabase/supabase-js": "^2.57.2",
"@tanstack/react-query": "^5.95.2",
"react-native-reanimated": "^4.3.0",
"react-native-safe-area-context": "^5.7.0",
"expo-secure-store": "^55.0.9",
"expo-haptics": "^55.0.9",
"expo-image-picker": "^55.0.14",
"expo-linear-gradient": "^55.0.9",
"expo-image": "^55.0.6",
"@expo-google-fonts/inter": "^0.4.2",
"react-native-keyboard-controller": "^1.21.3"
```

## How to Start the Next Session

1. Read this file first
2. Read `replit.md` for full project context
3. Use the `workflows` skill to set up the Expo workflow
4. Start the workflow and check `refresh_all_logs` for errors
5. Fix any errors, then proceed with the HIGH PRIORITY items above

## Important Notes

- **DO NOT edit package.json directly** — use the `package-management` skill / `installLanguagePackages`
- **DO NOT run `npx expo start` directly** — use `restart_workflow` tool instead
- The old PWA files (index.html, main.js, styles.css, Pages/, etc.) are still in the root
  - They are harmless but can be cleaned up once the Expo app is verified working
- The `.replit` file still points to the old HTTP server — this MUST be updated first

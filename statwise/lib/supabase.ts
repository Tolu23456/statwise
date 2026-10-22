import 'react-native-url-polyfill/auto';
import { createClient } from '@supabase/supabase-js';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

const supabaseUrl = 'https://fwpriiugfjhtoymcbmso.supabase.co';
const supabaseAnonKey =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTY5MDc3MTgsImV4cCI6MjA3MjQ4MzcxOH0.Sy3iehhGx2UNi28UBEjfNd81TjMxOVXJjY9HJS3xVjs';

export const FLUTTERWAVE_PUBLIC_KEY = 'FLWPUBK-30eeb76b5875f40db71221d0960de0a8-X';

const isWeb = Platform.OS === 'web';
const hasLocalStorage = isWeb && typeof localStorage !== 'undefined';

const ExpoSecureStoreAdapter = {
  getItem: (key: string) => {
    if (isWeb) {
      if (!hasLocalStorage) return Promise.resolve(null);
      return Promise.resolve(localStorage.getItem(key));
    }
    return SecureStore.getItemAsync(key);
  },
  setItem: (key: string, value: string) => {
    if (isWeb) {
      if (hasLocalStorage) localStorage.setItem(key, value);
      return Promise.resolve();
    }
    return SecureStore.setItemAsync(key, value);
  },
  removeItem: (key: string) => {
    if (isWeb) {
      if (hasLocalStorage) localStorage.removeItem(key);
      return Promise.resolve();
    }
    return SecureStore.deleteItemAsync(key);
  },
};

export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    storage: ExpoSecureStoreAdapter,
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: false,
  },
});

export type UserProfile = {
  id: string;
  email: string;
  username: string;
  display_name: string;
  current_tier: string;
  tier: string;
  subscription_status: string;
  subscription_period?: string | null;
  subscription_start?: string | null;
  subscription_end?: string | null;
  tier_expiry?: string | null;
  referral_code?: string | null;
  total_referrals?: number;
  total_referral_rewards?: number;
  avatar_url?: string;
  notifications: boolean;
  last_login?: string;
  updated_at?: string;
};

export type Prediction = {
  id: string;
  match_title: string;
  home_team: string;
  away_team: string;
  league: string;
  league_slug: string;
  prediction: string;
  odds: number;
  odds_home?: number | null;
  odds_draw?: number | null;
  odds_away?: number | null;
  confidence: number;
  kickoff_time: string;
  match_date: string;
  tier: string;
  tier_required: string;
  reasoning?: string;
  stats?: {
    h2h?: { home_wins: number; draws: number; away_wins: number };
    home_form?: number[];
    away_form?: number[];
    avg_goals?: number;
    home_value?: number;
    away_value?: number;
    home_rating?: number;
    away_rating?: number;
    /** Certainty score (0-100) — model confidence adjusted for probability spread + head agreement */
    certainty?: number;
    /** True when the model is genuinely sure about this match */
    is_ai_pick?: boolean;
    /**
     * True when the model has little league-specific training history for this
     * competition (e.g. the Europa/Conference League, which have no
     * football-data.co.uk season CSV). Predictions then lean on pooled priors.
     */
    reduced_confidence?: boolean;
    /** Historical matches the model has seen for this league (null = unknown) */
    league_samples?: number | null;
    /** Calibrated three-way outcome probabilities (0-100) */
    prob_home?: number | null;
    prob_draw?: number | null;
    prob_away?: number | null;
    /** Model probability minus de-vigged market probability, in points */
    value_edge?: number | null;
    /** Raw market odds captured at prediction time (for model vs market compare) */
    odds_home?: number | null;
    odds_draw?: number | null;
    odds_away?: number | null;
    uncertainty?: {
      entropy_norm?: number;
      head_disagreement?: number;
      is_uncertain?: boolean;
      certainty?: number;
      margin?: number;
    };
    details?: {
      espn_event_id?: string;
      venue?: string | null;
      city?: string | null;
      country?: string | null;
      attendance?: number | null;
      referee?: string | null;
      weather?: {
        temperature_c?: number | null;
        humidity_pct?: number | null;
        precipitation_mm?: number | null;
        wind_kmh?: number | null;
        condition?: string | null;
      } | null;
      scorers?: { player: string; team: string; minute: string; type: string }[];
      cards?: { player: string; team: string; minute: string; type: string }[];
      substitutions?: {
        player_in?: string; player_out?: string; text?: string; team: string; minute: string;
      }[];
      lineups?: Record<string, {
        starting: { name: string; jersey: string | null; position: string | null }[];
        bench: { name: string; jersey: string | null; position: string | null }[];
      }>;
      team_stats?: Record<string, Record<string, string>>;
    } | null;
  } | null;
  status?: string;
  actual_result?: string | null;
  home_score?: number | null;
  away_score?: number | null;
  created_at: string;
};

export type ForumMessage = {
  id: string;
  user_id: string;
  message: string;
  created_at: string;
  username?: string | null;
  user_profiles?: {
    username: string;
    display_name: string;
    avatar_url?: string;
    current_tier: string;
  };
};

export type ReferralCode = {
  code: string;
  total_referrals: number;
  active: boolean;
};

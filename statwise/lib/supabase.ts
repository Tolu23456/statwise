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
    h2h: { home_wins: number; draws: number; away_wins: number };
    home_form: number[];
    away_form: number[];
    avg_goals: number;
    home_value?: number;
    away_value?: number;
    home_rating?: number;
    away_rating?: number;
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

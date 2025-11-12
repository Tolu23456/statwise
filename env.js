// env.js
// ================================
// This file contains the environment-specific configuration for the StatWise PWA.
// It is intended to be populated with your actual Supabase and Flutterwave keys for local development.
// IMPORTANT: This file is listed in .gitignore and should NOT be committed to version control.
// To set up your local environment, copy the structure from `env.example.js` and fill in your keys.
// ================================

// Import Supabase client
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// --- Configuration ---
// Replace these with your actual Supabase and Flutterwave credentials.
const supabaseUrl = 'YOUR_SUPABASE_URL';
const supabaseKey = 'YOUR_SUPABASE_ANON_KEY';
const FLUTTERWAVE_PUBLIC_KEY = 'YOUR_FLUTTERWAVE_PUBLIC_KEY';

// --- Initialization ---
// Create and export the Supabase client
export const supabase = createClient(supabaseUrl, supabaseKey, {
  auth: {
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: true,
    storage: window.localStorage
  }
});

// Expose the URL and Key for the guard clause in main.js
supabase.supabaseUrl = supabaseUrl;
supabase.supabaseKey = supabaseKey;

// Export Flutterwave public key
export const FLWPUBK = FLUTTERWAVE_PUBLIC_KEY;

// --- Validation ---
// Log errors if the keys have not been replaced.
if (supabaseUrl === 'YOUR_SUPABASE_URL' || supabaseKey === 'YOUR_SUPABASE_ANON_KEY') {
  console.error('CRITICAL: Supabase environment variables are not set. Please update env.js with your project credentials.');
}

if (FLUTTERWAVE_PUBLIC_KEY === 'YOUR_FLUTTERWAVE_PUBLIC_KEY') {
  console.error('CRITICAL: Flutterwave public key is not set. Payment functionality will be disabled.');
}

// --- Compatibility Exports ---
// These are provided to ensure older parts of the application that might call `auth` or `db` directly still work.
export const auth = supabase.auth;
export const db = supabase;
export const storage = supabase.storage;
export const messaging = null; // Firebase messaging is not used in this application.

console.log('✅ Local environment configuration loaded. Ensure your keys are set correctly in env.js.');

// env.js
// ================================
// This is a template file for setting up your local environment.
// Copy this file to a new file named `env.js` and fill in your actual Supabase and Flutterwave keys.
// IMPORTANT: `env.js` is included in .gitignore and should not be committed to version control.
// ================================

// Import Supabase client
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// Supabase configuration
// Replace with your actual Supabase project URL and anon key
const supabaseUrl = 'YOUR_SUPABASE_URL';
const supabaseKey = 'YOUR_SUPABASE_ANON_KEY';

// Create and export the Supabase client
// This prevents the app from crashing if the credentials are not set.
// The guard clause in main.js will handle showing an error message.
export const supabase = (supabaseUrl === 'YOUR_SUPABASE_URL' || supabaseKey === 'YOUR_SUPABASE_ANON_KEY')
  ? { supabaseUrl, supabaseKey }
  : createClient(supabaseUrl, supabaseKey, {
      auth: {
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: true,
        storage: window.localStorage
      }
    });

// Flutterwave configuration
// Replace with your actual Flutterwave public key
const FLUTTERWAVE_PUBLIC_KEY = 'YOUR_FLUTTERWAVE_PUBLIC_KEY';

// Export Flutterwave public key
export const FLWPUBK = FLUTTERWAVE_PUBLIC_KEY;

// Validation checks to ensure keys are set
if (supabaseUrl === 'YOUR_SUPABASE_URL' || supabaseKey === 'YOUR_SUPABASE_ANON_KEY') {
  console.error('⚠️ Supabase environment variables are not set. Please update env.js with your project credentials.');
}

if (FLUTTERWAVE_PUBLIC_KEY === 'YOUR_FLUTTERWAVE_PUBLIC_KEY') {
  console.error('⚠️ Flutterwave public key is not set. Payment functionality will be disabled.');
}

// Export auth, db, and storage for compatibility with existing code
export const auth = supabase.auth;
export const db = supabase;
export const storage = supabase.storage;
export const messaging = null; // Firebase messaging is not used

console.log('✅ Environment configuration loaded.');

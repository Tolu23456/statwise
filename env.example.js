// env.example.js
// ================================
// This is a template file. Copy it to env.js and fill in your actual configuration.
// env.js is ignored by Git to keep your keys secure.
// ================================

// Import Supabase client
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// Supabase configuration
// IMPORTANT: Replace with your actual Supabase URL and Key from your Supabase project dashboard.
const supabaseUrl = 'YOUR_SUPABASE_URL';
const supabaseKey = 'YOUR_SUPABASE_ANON_KEY';

// Create Supabase client with auth and storage configurations
export const supabase = createClient(supabaseUrl, supabaseKey, {
  auth: {
    autoRefreshToken: true,
    persistSession: true,
    detectSessionInUrl: true,
    storage: window.localStorage
  },
  storage: {
    buckets: {
      'profile-pictures': {
        public: true,
        allowedMimeTypes: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
        fileSizeLimit: 5242880 // 5MB
      }
    }
  }
});

// Flutterwave configuration
// IMPORTANT: Replace with your actual Flutterwave Public Key from your Flutterwave dashboard.
const FLUTTERWAVE_PUBLIC_KEY = 'YOUR_FLUTTERWAVE_PUBLIC_KEY';

// Export Flutterwave public key (only public key is safe for frontend)
export const FLWPUBK = FLUTTERWAVE_PUBLIC_KEY;

// Validation checks to guide developers
if (!FLWPUBK || FLWPUBK === 'YOUR_FLUTTERWAVE_PUBLIC_KEY') {
  console.error('⚠️ FLUTTERWAVE_PUBLIC_KEY is not set. Payment functionality will not work.');
  console.log('💡 Please update env.js with your actual Flutterwave public key');
}

if (!supabaseUrl || supabaseUrl === 'YOUR_SUPABASE_URL') {
    console.error('⚠️ Supabase URL is not set. The application will not work.');
    console.log('💡 Please update env.js with your actual Supabase URL');
}

if (!supabaseKey || supabaseKey === 'YOUR_SUPABASE_ANON_KEY') {
    console.error('⚠️ Supabase Key is not set. The application will not work.');
    console.log('💡 Please update env.js with your actual Supabase anon key');
}


// Export auth for compatibility with existing code
export const auth = supabase.auth;

// Additional Supabase services for backward compatibility
export const db = supabase;
export const storage = supabase.storage;

// Firebase messaging is no longer used in this Supabase version
export const messaging = null;

console.log('✅ Supabase configuration template loaded.');
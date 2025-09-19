// env.js
// ================================
// Supabase-only configuration for StatWise PWA
// All Firebase dependencies have been removed
// ================================

// Import Supabase client
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// Supabase configuration
const supabaseUrl = 'https://fwpriiugfjhtoymcbmso.supabase.co';
const supabaseKey = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTY5MDc3MTgsImV4cCI6MjA3MjQ4MzcxOH0.Sy3iehhGx2UNi28UBEjfNd81TjMxOVXJjY9HJS3xVjs';

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
// Note: For security, only the PUBLIC KEY should be accessible in frontend code
// The SECRET and ENCRYPTION keys should remain server-side only

// You can set your Flutterwave public key here directly (it's safe for frontend)
// Or implement a secure method to fetch it from your Supabase backend
const FLUTTERWAVE_PUBLIC_KEY = 'YOUR_FLUTTERWAVE_PUBLIC_KEY_HERE';

// Export Flutterwave public key (only public key is safe for frontend)
export const FLWPUBK = FLUTTERWAVE_PUBLIC_KEY;

// Validation - ensure public key exists for payment functionality
if (!FLWPUBK || FLWPUBK === 'YOUR_FLUTTERWAVE_PUBLIC_KEY_HERE') {
  console.error('⚠️ FLUTTERWAVE_PUBLIC_KEY is not set. Payment functionality will not work.');
  console.log('💡 Please update env.js with your actual Flutterwave public key');
}

// Export auth for compatibility with existing code
export const auth = supabase.auth;

// Additional Supabase services for backward compatibility
export const db = supabase;
export const storage = supabase.storage;

// Remove Firebase messaging since we're not using it anymore
export const messaging = null;

console.log('✅ Supabase configuration loaded successfully!');
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

// Flutterwave Public Key (Safe for frontend)
export const FLWPUBK = "FLWPUBK_TEST-8336183053a03e565edf2dd9d6ec3ffd-X";

// Export auth for compatibility with existing code
export const auth = supabase.auth;

// Additional Supabase services for backward compatibility
export const db = supabase;
export const storage = supabase.storage;

// Remove Firebase messaging since we're not using it anymore
export const messaging = null;

console.log('✅ Supabase configuration loaded successfully!');
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

// Flutterwave configuration from environment variables
// These will be set in Supabase Dashboard > Project Settings > Environment Variables
const FLUTTERWAVE_PUBLIC_KEY = globalThis?.process?.env?.FLUTTERWAVE_PUBLIC_KEY || 
                                globalThis?.Deno?.env?.get?.('FLUTTERWAVE_PUBLIC_KEY') ||
                                globalThis?.FLUTTERWAVE_PUBLIC_KEY;

const FLUTTERWAVE_SECRET_KEY = globalThis?.process?.env?.FLUTTERWAVE_SECRET_KEY || 
                               globalThis?.Deno?.env?.get?.('FLUTTERWAVE_SECRET_KEY') ||
                               globalThis?.FLUTTERWAVE_SECRET_KEY;

const FLUTTERWAVE_WEBHOOK_SECRET = globalThis?.process?.env?.FLUTTERWAVE_WEBHOOK_SECRET || 
                                   globalThis?.Deno?.env?.get?.('FLUTTERWAVE_WEBHOOK_SECRET') ||
                                   globalThis?.FLUTTERWAVE_WEBHOOK_SECRET;

// Export Flutterwave keys (public key is safe for frontend)
export const FLWPUBK = FLUTTERWAVE_PUBLIC_KEY;
export const FLUTTERWAVE_SECRET = FLUTTERWAVE_SECRET_KEY;
export const FLUTTERWAVE_WEBHOOK = FLUTTERWAVE_WEBHOOK_SECRET;

// Validation - ensure public key exists for payment functionality
if (!FLWPUBK) {
  console.error('⚠️ FLUTTERWAVE_PUBLIC_KEY is not set. Payment functionality will not work.');
}

// Export auth for compatibility with existing code
export const auth = supabase.auth;

// Additional Supabase services for backward compatibility
export const db = supabase;
export const storage = supabase.storage;

// Remove Firebase messaging since we're not using it anymore
export const messaging = null;

console.log('✅ Supabase configuration loaded successfully!');
// env.js
// ================================
// This file contains the public configuration for Firebase and other services.
// It is safe to commit this file to your repository.
// ================================

// Import Firebase SDK
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
// Firebase Functions removed - using Supabase for backend operations
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import { getMessaging } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-messaging.js";
import { getStorage } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

// Firebase config - It's safe for this to be in client-side code.
const firebaseConfig = {
  apiKey: "AIzaSyDpPTmDw7RpxTo2AXf8ZDTq4AG46xKB16g",
  authDomain: "statwise-319a4.firebaseapp.com",
  projectId: "statwise-319a4",
  storageBucket: "statwise-319a4.firebasestorage.app",
  messagingSenderId: "416700134653",
  appId: "1:416700134653:web:f3a6f9766a2fafa8fdba94",
};

// Initialize Firebase app
const app = initializeApp(firebaseConfig);

// Export services
export const auth = getAuth(app);
export const db = getFirestore(app);
// Functions export removed - using Supabase for backend operations
export const messaging = getMessaging(app);
export const storage = getStorage(app);

// Flutterwave Public Key (Safe for frontend)
export const FLWPUBK = "FLWPUBK_TEST-8336183053a03e565edf2dd9d6ec3ffd-X";

// Supabase configuration
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

// Supabase credentials - Updated to use your new Supabase project
// Note: These are public keys safe for client-side code (anon key has Row Level Security)
const supabaseUrl = 'https://fwpriiugfjhtoymcbmso.supabase.co';
const supabaseKey = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTY5MDc3MTgsImV4cCI6MjA3MjQ4MzcxOH0.Sy3iehhGx2UNi28UBEjfNd81TjMxOVXJjY9HJS3xVjs';

// Create Supabase client
export const supabase = createClient(supabaseUrl, supabaseKey);
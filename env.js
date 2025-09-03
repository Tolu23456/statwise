// env.js
// ================================
// This file contains the public configuration for Firebase and other services.
// It is safe to commit this file to your repository.
// ================================

// Import Firebase SDK
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { getFunctions } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-functions.js";
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
export const functions = getFunctions(app);
export const messaging = getMessaging(app);
export const storage = getStorage(app);

// Flutterwave Public Key (Safe for frontend)
export const FLWPUBK = "FLWPUBK_TEST-8336183053a03e565edf2dd9d6ec3ffd-X"; // <-- IMPORTANT: Add your key here!
// env.js
// ================================
// Firebase Config & Initialization
// ================================

// Import Firebase SDK
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import { getFirestore } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";
import { getStorage } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";

// Firebase config
const firebaseConfig = {
  apiKey: "AIzaSyDpPTmDw7RpxTo2AXf8ZDTq4AG46xKB16g",
  authDomain: "statwise-319a4.firebaseapp.com",
  databaseURL: "https://statwise-319a4-default-rtdb.firebaseio.com",
  projectId: "statwise-319a4",
  storageBucket: "statwise-319a4.firebasestorage.app",
  messagingSenderId: "416700134653",
  appId: "1:416700134653:web:f3a6f9766a2fafa8fdba94",
  measurementId: "G-QB5346HMZC"
};

// Initialize Firebase app
const app = initializeApp(firebaseConfig);

// Export Auth and Firestore
export const auth = getAuth(app);
export const db = getFirestore(app);
export const storage = getStorage(app);

// ================================
// Flutterwave Keys
// ================================
export const FLWPUBK = "FLWPUBK_TEST-8336183053a03e565edf2dd9d6ec3ffd-X";      // Public Key (frontend)
export const FLWSECRET = "FLWSECK_TEST-b52c73bafee89fe8c66996e1a6ec49fb-X";    // Secret Key (backend verification)
export const FLWENCRYPTION = "FLWSECK_TESTe3932e89fdb0";                      // Optional encryption key

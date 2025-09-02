// auth.js
// =====================================
// Firebase Auth + Firestore Setup
// =====================================

import { auth, db } from "../env.js";
import { addHistoryUnique } from "../utils.js";
import {
    createUserWithEmailAndPassword,
    signInWithEmailAndPassword,
    updateProfile,
    signOut
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

import {
    doc,
    setDoc,
    getDoc,
    collection,
    addDoc,
    getDocs,
    query,
    where,
    serverTimestamp
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

// ===== Utility UI Helpers =====
function showSpinner(btn) {
    btn.disabled = true;
    btn.querySelector(".btn-text").textContent = "Loading...";
}

function hideSpinner(btn, text) {
    btn.disabled = false;
    btn.querySelector(".btn-text").textContent = text;
}

function showSuccess(btn) {
    btn.querySelector(".btn-text").textContent = "✅ Success!";
}

// ===== Grab DOM Elements =====
// Login
const loginForm = document.querySelector("#login-form");
const loginEmail = document.querySelector("#login-email");
const loginPassword = document.querySelector("#login-password");
const loginBtn = document.querySelector("#login-btn");
const loginError = document.querySelector("#login-error");

// Signup
const signupForm = document.querySelector("#signup-form");
const signupUsername = document.querySelector("#signup-username");
const signupEmail = document.querySelector("#signup-email");
const signupPassword = document.querySelector("#signup-password");
const signupBtn = document.querySelector("#signup-btn");
const signupError = document.querySelector("#signup-error");

// ===== Firestore Collections =====
const usersCol = collection(db, "users");
const subscriptionsCol = collection(db, "subscriptions");

// ===== Login Logic =====
if (loginForm) {
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        loginError.textContent = "";

        const email = loginEmail.value.trim();
        const password = loginPassword.value.trim();
        if (!email || !password) {
            loginError.textContent = "Please enter email and password.";
            return;
        }

        showSpinner(loginBtn);

        try {
            const userCredential = await signInWithEmailAndPassword(auth, email, password);
            const user = userCredential.user;

            const userRef = doc(usersCol, user.uid);
            const userSnap = await getDoc(userRef);

            if (!userSnap.exists()) {
                // First login → create user profile
                await setDoc(userRef, {
                    username: user.displayName || "User",
                    email: user.email,
                    tier: "Free Tier",
                    tierExpiry: null,
                    photoURL: null,
                    notifications: true,
                    autoRenew: false,
                    createdAt: new Date().toISOString(),
                    lastLogin: new Date().toISOString()
                });

                // Default subscription
                const subRef = doc(subscriptionsCol, user.uid);
                await setDoc(subRef, {
                    currentTier: "Free Tier",
                    startDate: new Date().toISOString(),
                    expiryDate: null,
                    transactions: []
                });

            } else {
                // Update last login timestamp
                await setDoc(userRef, { lastLogin: new Date().toISOString() }, { merge: true });

                // Ensure subscription doc exists for older users
                const subRef = doc(subscriptionsCol, user.uid);
                const subSnap = await getDoc(subRef);
                if (!subSnap.exists()) {
                    await setDoc(subRef, {
                        currentTier: userSnap.data()?.tier || "Free Tier",
                        startDate: userSnap.data()?.createdAt || new Date().toISOString(),
                        expiryDate: userSnap.data()?.tierExpiry || null,
                        transactions: []
                    });
                }

            }

            showSuccess(loginBtn);
            setTimeout(() => window.location.href = "../index.html", 1000);

        } catch (error) {
            console.error(error);
            hideSpinner(loginBtn, "Login");
            loginError.textContent = error.message;
        }
    });
}

// ===== Signup Logic =====
if (signupForm) {
    signupForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        signupError.textContent = "";

        const username = signupUsername.value.trim();
        const email = signupEmail.value.trim();
        const password = signupPassword.value.trim();
        if (!username || !email || !password) {
            signupError.textContent = "All fields are required.";
            return;
        }

        showSpinner(signupBtn);

        try {
            const userCredential = await createUserWithEmailAndPassword(auth, email, password);
            const user = userCredential.user;

            // Update Firebase Auth profile
            await updateProfile(user, { displayName: username });

            // Create Firestore profile
            const userRef = doc(usersCol, user.uid);
            await setDoc(userRef, {
                username,
                email,
                tier: "Free Tier",
                tierExpiry: null,
                photoURL: null,
                notifications: true,
                autoRenew: false,
                createdAt: new Date().toISOString(),
                lastLogin: new Date().toISOString()
            });

            // Create default subscription
            const subRef = doc(subscriptionsCol, user.uid);
            await setDoc(subRef, {
                currentTier: "Free Tier",
                startDate: new Date().toISOString(),
                expiryDate: null,
                transactions: []
            });

            showSuccess(signupBtn);
            setTimeout(() => window.location.href = "../index.html", 1000);

        } catch (error) {
            console.error(error);
            hideSpinner(signupBtn, "Sign Up");
            signupError.textContent = error.message;
        }
    });
}

// ===== Logout Function (can be used in main.js) =====
export async function logoutUser() {
    const user = auth.currentUser;
    if (!user) return;

    await addHistoryUnique(user.uid, "User logged out");
    await signOut(auth);
}

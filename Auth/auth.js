// auth.js
// =====================================
// Firebase Auth + Firestore Setup
// =====================================

import { auth, db } from "/env.js";
import { addHistoryUnique } from "/utils.js";
import {
    createUserWithEmailAndPassword,
    signInWithEmailAndPassword,
    updateProfile,
    signOut,
    sendPasswordResetEmail
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

import {
    doc,
    setDoc,
    getDoc,
    collection,
    addDoc,
    getDocs,
    query, limit,
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

// Signup
const signupForm = document.querySelector("#signup-form");
const signupUsername = document.querySelector("#signup-username");
const signupEmail = document.querySelector("#signup-email");
const signupPassword = document.querySelector("#signup-password");
const signupReferral = document.querySelector("#signup-referral");
const signupBtn = document.querySelector("#signup-btn");
const referralNameDisplay = document.querySelector("#referral-name-display");
const signupError = document.querySelector("#signup-error");

// Forgot Password
const forgotPasswordForm = document.querySelector("#forgot-password-form");
const forgotPasswordEmail = document.querySelector("#forgot-password-email");
const forgotPasswordBtn = document.querySelector("#forgot-password-btn");
const forgotPasswordMessage = document.querySelector("#forgot-password-message");


// ===== Firestore Collections =====
const usersCol = collection(db, "users");
const subscriptionsCol = collection(db, "subscriptions");

// ===== Login Logic =====
if (loginForm) {
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const loginError = document.querySelector("#login-error");
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
            setTimeout(() => window.location.href = "/index.html", 1000);

        } catch (error) {
            console.error(error);
            hideSpinner(loginBtn, "Login");
            loginError.textContent = error.message;
        }
    });
}

// ===== Referral Code Input Logic =====
if (signupReferral && referralNameDisplay) {
    const prefix = "REF-";

    signupReferral.addEventListener("input", () => {
        let value = signupReferral.value.toUpperCase();

        // Ensure the prefix is always there
        if (!value.startsWith(prefix)) {
            // User might be trying to delete the prefix, or just started typing
            const coreCode = value.replace(prefix, "");
            value = prefix + coreCode;
        }

        // Prevent the user from deleting the prefix with backspace
        if (value.length < prefix.length) {
            value = prefix;
        }

        // Update the input value if it has changed
        if (signupReferral.value !== value) {
            signupReferral.value = value;
        }

        // Clear the name display while typing
        referralNameDisplay.textContent = "";
        referralNameDisplay.style.display = "none";
    });

    signupReferral.addEventListener("blur", async () => {
        const code = signupReferral.value.trim().toUpperCase();
        if (code.length > prefix.length) {
            try {
                const q = query(usersCol, where("referralCode", "==", code), limit(1)); // Add limit(1) to match security rules
                const querySnapshot = await getDocs(q);
                if (!querySnapshot.empty) {
                    const referrerName = querySnapshot.docs[0].data().username;
                    referralNameDisplay.textContent = `Referred by: ${referrerName}`;
                    referralNameDisplay.style.display = "block";
                }
            } catch (error) {
                console.error("Error fetching referrer:", error);
            }
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
        const referralCode = signupReferral.value.trim().toUpperCase();
        if (!username || !email || !password) {
            signupError.textContent = "All fields are required.";
            return;
        }

        showSpinner(signupBtn);

        try {
            // --- Referral Code Validation ---
            let referrerId = null;
            if (referralCode) {
                const q = query(usersCol, where("referralCode", "==", referralCode), limit(1)); // Add limit(1) to match security rules
                const querySnapshot = await getDocs(q);
                if (!querySnapshot.empty) {
                    const referrerDoc = querySnapshot.docs[0];
                    referrerId = referrerDoc.id;
                } else {
                    hideSpinner(signupBtn, "Sign Up");
                    signupError.textContent = "Invalid referral code.";
                    return; // Stop signup if code is provided but invalid
                }
            }

            const userCredential = await createUserWithEmailAndPassword(auth, email, password);
            const user = userCredential.user;

            // Update Firebase Auth profile
            await updateProfile(user, { displayName: username });

            // Prepare user document data
            const newUserDoc = {
                username, email, tier: "Free Tier", tierExpiry: null, photoURL: null,
                notifications: true, autoRenew: false, createdAt: new Date().toISOString(),
                lastLogin: new Date().toISOString(), isNewUser: true
            };

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
                lastLogin: new Date().toISOString(),
                isNewUser: true, // Flag for the welcome tour
                ...(referrerId && { referredBy: referrerId }) // Add referrer ID if it exists
            });

            // If referred, update the referrer's document and notify them
            if (referrerId) {
                const referrerRef = doc(usersCol, referrerId);
                // We use a subcollection for history, so we can just add an action.
                const historyRef = collection(db, "users", referrerId, "history");
                await addDoc(historyRef, { action: `Your friend '${username}' joined using your referral code!`, createdAt: serverTimestamp() });
            }

            showSuccess(signupBtn);
            setTimeout(() => window.location.href = "/index.html", 1000);

        } catch (error) {
            console.error(error);
            hideSpinner(signupBtn, "Sign Up");
            signupError.textContent = error.message;
        }
    });
}

// ===== Forgot Password Logic =====
if (forgotPasswordForm) {
    forgotPasswordForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        forgotPasswordMessage.textContent = "";
        forgotPasswordMessage.style.color = ""; // Reset color

        const email = forgotPasswordEmail.value.trim();
        if (!email) {
            forgotPasswordMessage.textContent = "Please enter your email address.";
            return;
        }

        showSpinner(forgotPasswordBtn);

        try {
            await sendPasswordResetEmail(auth, email);
            forgotPasswordMessage.textContent = "Password reset email sent! Please check your inbox.";
            forgotPasswordMessage.style.color = "#28a745"; // Success green color
            hideSpinner(forgotPasswordBtn, "Send Reset Link");
            forgotPasswordBtn.disabled = true; // Prevent resending
        } catch (error) {
            console.error("Password reset error:", error);
            hideSpinner(forgotPasswordBtn, "Send Reset Link");
            forgotPasswordMessage.textContent = error.message;
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

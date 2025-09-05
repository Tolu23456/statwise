// auth.js
// =====================================
// Firebase Auth + Firestore Setup
// =====================================

import { auth, db } from "../env.js";
import { addHistoryUnique } from "../utils.js";
import {
    createUserWithEmailAndPassword,
    signInWithEmailAndPassword,
    setPersistence,
    browserSessionPersistence, browserLocalPersistence, updateProfile,
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
import { showSpinner, hideSpinner } from "../Loader/loader.js";

// ===== Utility UI Helpers =====
function showSuccess(btn) {
    btn.querySelector(".btn-text").textContent = "✅ Success!";
}

// ===== Theme Management =====
function applyTheme(isDark) {
    document.documentElement.classList.toggle("dark-mode", isDark);
}

function initializeTheme() {
    let isDark = localStorage.getItem('darkMode') === 'true';
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    if (localStorage.getItem('darkMode') === null) {
        isDark = prefersDark;
    }

    applyTheme(isDark);

    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
        // Only apply if the user hasn't set a manual preference
        if (localStorage.getItem('darkMode') === null) {
            applyTheme(e.matches);
        }
    });
}

// ===== Firestore Collections =====
const usersCol = collection(db, "users");
const referralCodesCol = collection(db, "referralCodes");
const subscriptionsCol = collection(db, "subscriptions");

// ===== Grab Page-Specific Forms =====
const loginForm = document.querySelector("#login-form");
const signupForm = document.querySelector("#signup-form");
const forgotPasswordForm = document.querySelector("#forgot-password-form");

// Initialize theme on page load
initializeTheme();

// ===== Login Logic =====
if (loginForm) {
    const loginPassword = document.querySelector("#login-password");
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const loginEmail = document.querySelector("#login-email");
        const rememberMe = document.querySelector("#remember-me");
        const loginBtn = document.querySelector("#login-btn");

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
            // Set persistence based on the "Remember Me" checkbox
            const persistence = rememberMe.checked ? browserLocalPersistence : browserSessionPersistence;
            await setPersistence(auth, persistence);

            // Sign in the user
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
                    lastLogin: new Date().toISOString(),
                    isNewUser: true // Add this flag to satisfy security rules on create
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

            // Clear the last visited page to ensure a fresh start on the home page
            localStorage.removeItem('lastPage');

            // Add fade-out transition before redirecting
            const authCard = loginForm.closest('.auth-card');
            if (authCard) {
                authCard.classList.add('fade-out');
            }
            setTimeout(() => window.location.href = "../index.html", 500); // Redirect after animation (500ms)

        } catch (error) {
            console.error(error);
            hideSpinner(loginBtn);
            loginError.textContent = error.message;
        }
    });

    // Password visibility toggle
    const passwordToggle = document.getElementById('password-toggle');
    if (passwordToggle && loginPassword) {
        passwordToggle.addEventListener('click', () => {
            const isPassword = loginPassword.type === 'password';
            loginPassword.type = isPassword ? 'text' : 'password';
            passwordToggle.classList.toggle('icon-eye', !isPassword);
            passwordToggle.classList.toggle('icon-eye-slash', isPassword);
        });
    }
}

// ===== Referral Code Input Logic =====
if (signupForm) {
    const signupReferral = document.querySelector("#signup-referral");
    const referralNameDisplay = document.querySelector("#referral-name-display");

    let debounceTimeout;

    const checkReferralCode = async (code) => {
        const coreCode = code.trim().toUpperCase();
        referralNameDisplay.style.display = "none"; // Hide by default

        if (!coreCode) {
            referralNameDisplay.textContent = "";
            return;
        }

        const fullCode = `REF-${coreCode}`;
        try {
            // Perform a direct, secure lookup on the dedicated referralCodes collection
            const referralDocRef = doc(referralCodesCol, fullCode);
            const referralDocSnap = await getDoc(referralDocRef);

            if (referralDocSnap.exists()) {
                const referrerName = referralDocSnap.data().username;
                referralNameDisplay.textContent = `Referred by: ${referrerName}`;
                referralNameDisplay.style.display = "block";
            } else {
                referralNameDisplay.textContent = "Invalid Code";
                referralNameDisplay.style.display = "block";
            }
        } catch (error) {
            console.error("Error fetching referrer:", error);
        }
    };

    signupReferral.addEventListener("input", (e) => {
        clearTimeout(debounceTimeout);
        debounceTimeout = setTimeout(() => checkReferralCode(e.target.value), 500); // 500ms delay
    });
}

// ===== Password Strength Checker =====
function checkPasswordStrength(password) {
    let score = 0;
    if (password.length >= 8) score++;
    if (/[a-z]/.test(password)) score++;
    if (/[A-Z]/.test(password)) score++;
    if (/[0-9]/.test(password)) score++;
    if (/[^A-Za-z0-9]/.test(password)) score++;

    let strength = "";
    let level = "";

    if (password.length === 0) {
        strength = "";
        level = "";
    } else if (score <= 2) {
        strength = "Weak";
        level = "weak";
    } else if (score === 3) {
        strength = "Medium";
        level = "medium";
    } else if (score === 4) {
        strength = "Strong";
        level = "strong";
    } else {
        strength = "Very Strong";
        level = "very-strong";
    }
    return { strength, level };
}

if (signupForm) {
    const strengthBars = document.querySelectorAll("#password-strength-container .strength-bar");
    const strengthText = document.getElementById("password-strength-text");

    signupPassword.addEventListener("input", () => {
        const password = signupPassword.value;
        const { strength, level } = checkPasswordStrength(password);

        strengthText.textContent = strength;
        strengthBars.forEach(bar => bar.className = 'strength-bar'); // Reset classes

        if (level) {
            if (level === 'weak') {
                strengthBars[0].classList.add(level);
            } else if (level === 'medium') {
                strengthBars[0].classList.add(level);
                strengthBars[1].classList.add(level);
            } else if (level === 'strong') {
                strengthBars[0].classList.add(level);
                strengthBars[1].classList.add(level);
                strengthBars[2].classList.add(level);
            } else if (level === 'very-strong') {
                strengthBars.forEach(bar => bar.classList.add(level));
            }
        }
    });
}

// ===== Password Match Validation =====
function validatePasswords() {
    if (!signupForm) return;
    const signupPassword = document.querySelector("#signup-password");
    const signupPasswordConfirm = document.querySelector("#signup-password-confirm");
    const signupError = document.querySelector("#signup-error");

    const password = signupPassword.value;
    const confirmPassword = signupPasswordConfirm.value;

    // Only show error if the confirm password field has been typed in
    if (confirmPassword && password !== confirmPassword) {
        signupPassword.classList.add('input-error');
        signupPasswordConfirm.classList.add('input-error');
        signupError.textContent = "Passwords do not match.";
    } else {
        signupPassword.classList.remove('input-error');
        signupPasswordConfirm.classList.remove('input-error');
        // Clear the error only if it's the password mismatch error
        if (signupError.textContent === "Passwords do not match.") {
            signupError.textContent = "";
        }
    }
}

// ===== Signup Logic =====
if (signupForm) {
    const signupPassword = document.querySelector("#signup-password");
    const signupPasswordConfirm = document.querySelector("#signup-password-confirm");

    signupForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const signupUsername = document.querySelector("#signup-username");
        const signupEmail = document.querySelector("#signup-email");
        const signupPassword = document.querySelector("#signup-password");
        const signupPasswordConfirm = document.querySelector("#signup-password-confirm");
        const signupReferral = document.querySelector("#signup-referral");
        const signupRememberMe = document.querySelector("#signup-remember-me");
        const signupBtn = document.querySelector("#signup-btn");
        const signupError = document.querySelector("#signup-error");

        signupError.textContent = "";

        const username = signupUsername.value.trim();
        const email = signupEmail.value.trim();
        const password = signupPassword.value.trim();
        const confirmPassword = signupPasswordConfirm.value.trim();
        const referralCode = signupReferral.value.trim().toUpperCase();
        if (!username || !email || !password || !confirmPassword) {
            signupError.textContent = "Please fill out all required fields.";
            return;
        }

        if (password !== confirmPassword) {
            validatePasswords(); // This will show the error text and borders
            return; 
        }

        showSpinner(signupBtn);

        try {
            // Set persistence based on the "Remember Me" checkbox
            const persistence = signupRememberMe.checked ? browserLocalPersistence : browserSessionPersistence;
            await setPersistence(auth, persistence);

            const userCredential = await createUserWithEmailAndPassword(auth, email, password);
            const user = userCredential.user;

            // --- Referral Code Validation ---
            let referrerId = null;
            if (referralCode) { // referralCode is now just the user-typed part
                const fullCode = `REF-${referralCode}`;
                const referrerDocRef = doc(referralCodesCol, fullCode);
                const referrerDocSnap = await getDoc(referrerDocRef);

                if (!referrerDocSnap.exists()) {
                    hideSpinner(signupBtn);
                    signupError.textContent = "Invalid referral code.";
                    await user.delete(); // Clean up the created user if referral is invalid
                    return;
                }
                referrerId = referrerSnapshot.docs[0].id;
                // Now that the user is created, we can safely check for self-referral.
                if (referrerId === user.uid) {
                    referrerId = null; // Nullify the referral if it's a self-referral
                }
            }

            // Update Firebase Auth profile
            await updateProfile(user, { displayName: username });

            // Generate a unique referral code for the new user
            const newReferralCode = `REF-${user.uid.substring(0, 6).toUpperCase()}`;

            // Create Firestore profile
            const userRef = doc(usersCol, user.uid);
            await setDoc(userRef, {
                username,
                email,
                tier: "Free Tier",
                referralCode: newReferralCode,
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
                await addDoc(historyRef, {
                    action: `Your friend '${username}' joined using your referral code!`,
                    createdAt: serverTimestamp(),
                    creatorId: user.uid // Add this field to satisfy the security rule
                });
            }

            showSuccess(signupBtn);

            // Clear the last visited page to ensure a fresh start on the home page
            localStorage.removeItem('lastPage');

            // Add fade-out transition before redirecting
            const authCard = signupForm.closest('.auth-card');
            if (authCard) {
                authCard.classList.add('fade-out');
            }
            setTimeout(() => window.location.href = "../index.html", 500); // Redirect after animation (500ms)

        } catch (error) {
            console.error(error);
            hideSpinner(signupBtn);
            signupError.textContent = error.message;
        }
    });

    // Password visibility toggle for signup page
    const passwordToggle = document.getElementById('password-toggle');
    const confirmPasswordToggle = document.getElementById('confirm-password-toggle');

    if (passwordToggle && signupPassword) { // Toggle for the main password field
        passwordToggle.addEventListener('click', () => {
            const isPassword = signupPassword.type === 'password';
            signupPassword.type = isPassword ? 'text' : 'password';
            passwordToggle.classList.toggle('icon-eye', !isPassword);
            passwordToggle.classList.toggle('icon-eye-slash', isPassword);
        });
    }
    if (confirmPasswordToggle && signupPasswordConfirm) { // Toggle for the confirm password field
        confirmPasswordToggle.addEventListener('click', () => {
            const isPassword = signupPasswordConfirm.type === 'password';
            signupPasswordConfirm.type = isPassword ? 'text' : 'password';
            confirmPasswordToggle.classList.toggle('icon-eye', !isPassword);
            confirmPasswordToggle.classList.toggle('icon-eye-slash', isPassword);
        });
    }

    // Add real-time validation listeners
    signupPassword?.addEventListener('input', validatePasswords);
    signupPasswordConfirm?.addEventListener('input', validatePasswords);
}

// ===== Forgot Password Logic =====
if (forgotPasswordForm) {
    forgotPasswordForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const forgotPasswordEmail = document.querySelector("#forgot-password-email");
        const forgotPasswordBtn = document.querySelector("#forgot-password-btn");
        const forgotPasswordMessage = document.querySelector("#forgot-password-message");

        forgotPasswordMessage.textContent = "";
        forgotPasswordMessage.style.color = ""; // Reset color

        const email = forgotPasswordEmail.value.trim();
        if (!email) {
            forgotPasswordMessage.textContent = "Please enter your email address.";
            return;
        }

        showSpinner(forgotPasswordBtn, "Sending...");

        try {
            await sendPasswordResetEmail(auth, email);
            forgotPasswordMessage.textContent = "Password reset email sent! Please check your inbox.";
            forgotPasswordMessage.style.color = "#28a745"; // Success green color
            hideSpinner(forgotPasswordBtn);
            forgotPasswordBtn.disabled = true; // Prevent resending
        } catch (error) {
            console.error("Password reset error:", error);
            hideSpinner(forgotPasswordBtn);
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

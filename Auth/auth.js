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
    const btnText = btn.querySelector(".btn-text");
    const btnIcon = btn.querySelector(".btn-icon") || document.createElement("span");
    
    if (!btn.querySelector(".btn-icon")) {
        btnIcon.className = "btn-icon";
        btnText.appendChild(btnIcon);
    }
    
    btnIcon.textContent = "✅";
    btnText.firstChild.textContent = "Success! ";
    btn.classList.add("success");
    
    // Reset after animation
    setTimeout(() => {
        btn.classList.remove("success");
    }, 2000);
}

function displayError(errorElement, message, shake = true) {
    if (!errorElement) return;
    
    errorElement.textContent = message;
    errorElement.classList.remove('show', 'shake');
    
    // Force reflow
    errorElement.offsetHeight;
    
    // Add show class for animation
    errorElement.classList.add('show');
    
    // Add shake animation if requested
    if (shake) {
        setTimeout(() => errorElement.classList.add('shake'), 50);
    }
    
    // Auto-hide after 10 seconds
    setTimeout(() => {
        if (errorElement.textContent === message) {
            errorElement.classList.remove('show');
        }
    }, 10000);
}

function clearError(errorElement) {
    if (!errorElement) return;
    errorElement.classList.remove('show', 'shake');
    setTimeout(() => errorElement.textContent = "", 300);
}

// ===== Theme Management =====
function applyTheme(isDark) {
    document.documentElement.classList.toggle("dark-mode", isDark);
}

function initializeTheme() {
    let isDark = localStorage.getItem('darkMode') === 'true'; // Get local storage first
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    if (localStorage.getItem('darkMode') === null) { // if local storage is empty, use system preference
        isDark = prefersDark;
    }

    applyTheme(isDark);

    // Listen for system theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
        // Only apply system preference if the user hasn't set a manual preference
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
        clearError(loginError);

        const email = loginEmail.value.trim();
        const password = loginPassword.value.trim();
        if (!email || !password) {
            displayError(loginError, "Please enter both email and password to continue.", true);
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
                authCard.classList.add('fade-out'); // Ensure this class is defined in auth.css
            }
            // Explicitly redirect to home page with hash to ensure homepage loads
            setTimeout(() => {
                try {
                    window.location.href = "../index.html#home";
                } catch (redirectError) {
                    // Fallback if redirect fails
                    console.error('Login redirect failed, using fallback:', redirectError);
                    window.location.replace("../index.html#home");
                }
            }, 500); // Redirect after animation (500ms)

        } catch (error) {            
            let errorMessage = "Unable to sign you in right now. Please try again.";

            if (error.code === 'auth/wrong-password') {
                errorMessage = "The password you entered is incorrect. Please double-check and try again.";
            } else if (error.code === 'auth/user-not-found') {
                errorMessage = "We couldn't find an account with that email address. Please check the email or create a new account.";
            } else if (error.code === 'auth/invalid-email') {
                errorMessage = "Please enter a valid email address.";
            } else if (error.code === 'auth/missing-email') {
                errorMessage = "Please enter your email address.";
            } else if (error.code === 'auth/network-request-failed') {
                errorMessage = "Connection problem. Please check your internet connection and try again.";
            } else if (error.code === 'auth/too-many-requests') {
                errorMessage = "Too many login attempts. Please wait a few minutes before trying again.";
            } else if (error.code === 'auth/user-disabled') {
                errorMessage = "This account has been disabled. Please contact support for assistance.";
            } else if (error.code === 'auth/invalid-credential') {
                errorMessage = "Invalid email or password. Please check your credentials and try again.";
            }

            console.error('Login error:', error.code, error.message);
            hideSpinner(loginBtn);
            displayError(loginError, errorMessage, true);
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
    let validReferrerId = null; // Store the validated referrer's ID

    const checkReferralCode = async (code) => {
        const coreCode = code.trim().toUpperCase();
        const wrapper = signupReferral.parentElement;
        referralNameDisplay.classList.remove('show', 'error'); // Hide by default

        if (!coreCode) {
            referralNameDisplay.textContent = "";
            validReferrerId = null;
            return;
        }

        // Show validating spinner
        wrapper.classList.add('validating');

        try {
            // Query the 'users' collection for a matching referral code.
            const q = query(usersCol, where("referralCode", "==", `REF-${coreCode}`), limit(1));
            const querySnapshot = await getDocs(q);

            if (!querySnapshot.empty) {
                const referrerDoc = querySnapshot.docs[0];
                validReferrerId = referrerDoc.id; // Cache the ID
                const referrerName = referrerDoc.data().username;
                referralNameDisplay.textContent = `Referred by: ${referrerName}`;
                referralNameDisplay.classList.remove('error');
                referralNameDisplay.classList.add('show');
            } else {
                validReferrerId = null; // Invalidate
                referralNameDisplay.textContent = "Invalid referral code";
                referralNameDisplay.classList.add('error', 'show');
            }
        } catch (error) {
            validReferrerId = null;
            console.error("Error fetching referrer:", error);
        } finally {
            // Hide validating spinner
            wrapper.classList.remove('validating');
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
    return { strength, level, score };
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
        displayError(signupError, "Passwords do not match - please check both fields.", true);
    } else {
        signupPassword.classList.remove('input-error');
        signupPasswordConfirm.classList.remove('input-error');
        // Clear the error only if it's the password mismatch error
        if (signupError.textContent.includes("Passwords do not match")) {
            clearError(signupError);
        }
    }
}

// ===== Signup Logic =====
if (signupForm) {
    const signupPassword = document.querySelector("#signup-password");
    const signupPasswordConfirm = document.querySelector("#signup-password-confirm");

    signupForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const signupReferral = document.querySelector("#signup-referral");
        const signupRememberMe = document.querySelector("#signup-remember-me");
        const signupBtn = document.querySelector("#signup-btn");
        const signupError = document.querySelector("#signup-error");

        clearError(signupError);

        // Use existing variables from the outer scope
        const signupUsername = document.querySelector("#signup-username");
        const signupEmail = document.querySelector("#signup-email");
        // signupPassword and signupPasswordConfirm are already defined outside

        const username = signupUsername.value.trim();
        const email = signupEmail.value.trim();
        const password = signupPassword.value.trim();
        const confirmPassword = signupPasswordConfirm.value.trim();
        const referralCode = signupReferral.value.trim().toUpperCase();
        if (!username || !email || !password || !confirmPassword) {
            displayError(signupError, "Please fill out all required fields to continue.", true);
            return;
        }

        if (password !== confirmPassword) {
            validatePasswords(); // This will show the error text and borders
            return; 
        }

        if (password.length < 6) {
            displayError(signupError, "Password must be at least 6 characters long for security.", true);
            return;
        }

        const strengthScore = checkPasswordStrength(password);
        if (strengthScore.score < 3) {
            displayError(signupError, "Password must contain uppercase, lowercase, numbers, and symbols for security.", true);
            return;
        }

        showSpinner(signupBtn);

        try {
            // Set persistence based on the "Remember Me" checkbox
            const persistence = signupRememberMe.checked ? browserLocalPersistence : browserSessionPersistence;
            await setPersistence(auth, persistence);
            
            // If a referral code was entered but found to be invalid by the UI check, block submission.
            if (referralCode && !validReferrerId) {
                hideSpinner(signupBtn);
                displayError(signupError, "Please enter a valid referral code or leave it blank.", true);
                return;
            }

            const userCredential = await createUserWithEmailAndPassword(auth, email, password);
            const user = userCredential.user;

            // --- Final Referral ID Check (Self-Referral) ---
            let finalReferrerId = validReferrerId;
            if (finalReferrerId && finalReferrerId === user.uid) {
                finalReferrerId = null; // Nullify if user is referring themselves.
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
                ...(finalReferrerId && { referredBy: finalReferrerId }) // Add referrer ID if it exists
            });

            // If referred, update the referrer's document and notify them
            if (finalReferrerId) {
                const referrerRef = doc(usersCol, finalReferrerId);
                // We use a subcollection for history, so we can just add an action.
                const historyRef = collection(db, "users", finalReferrerId, "history");
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
            // Explicitly redirect to home page with hash to ensure homepage loads
            setTimeout(() => {
                try {
                    window.location.assign("../index.html#home");
                } catch (redirectError) {
                    // Fallback if redirect fails
                    console.error('Signup redirect failed:', redirectError);
                    window.location.replace("../index.html#home");
                }
            }, 500); // Redirect after animation (500ms)

        } catch (error) {            
            let errorMessage = "Unable to create your account right now. Please try again.";

            if (error.code === 'auth/email-already-in-use') {
                errorMessage = "An account with this email already exists. Please use the login page instead.";
            } else if (error.code === 'auth/invalid-email') {
                errorMessage = "Please enter a valid email address.";
            } else if (error.code === 'auth/weak-password') {
                errorMessage = "Please choose a stronger password with at least 6 characters.";
            } else if (error.code === 'auth/network-request-failed') {
                errorMessage = "Connection problem. Please check your internet connection and try again.";
            } else if (error.code === 'auth/too-many-requests') {
                errorMessage = "Too many signup attempts. Please wait a few minutes before trying again.";
            } else if (error.code === 'auth/operation-not-allowed') {
                errorMessage = "Account creation is currently disabled. Please contact support.";
            }
            
            console.error('Signup error:', error.code, error.message);
            hideSpinner(signupBtn);
            displayError(signupError, errorMessage, true);
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

        clearError(forgotPasswordMessage);
        forgotPasswordMessage.style.color = ""; // Reset color

        const email = forgotPasswordEmail.value.trim();
        if (!email) {
            displayError(forgotPasswordMessage, "Please enter your email address to reset your password.", true);
            return;
        }

        showSpinner(forgotPasswordBtn, "Sending...");

        try {
            await sendPasswordResetEmail(auth, email);
            forgotPasswordMessage.classList.add('show');
            forgotPasswordMessage.style.color = '#28a745';
            forgotPasswordMessage.textContent = "✅ Password reset email sent! Please check your inbox.";
            hideSpinner(forgotPasswordBtn);
            forgotPasswordBtn.disabled = true; // Prevent resending
        } catch (error) {
            console.error("Password reset error:", error);
            hideSpinner(forgotPasswordBtn);
            displayError(forgotPasswordMessage, "Unable to send reset email. Please try again or contact support.", true);
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

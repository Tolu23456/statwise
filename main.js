// main.js
import { auth, db, FLWPUBK, storage, functions, messaging } from './env.js';
import { showLoader, hideLoader, showSpinner, hideSpinner } from './Loader/loader.js';
import { initInteractiveBackground } from './ui.js';
import { initializeAppSecurity, manageInitialPageLoad } from './manager.js';
import { formatTimestamp, addHistoryUnique } from './utils.js';
import { onAuthStateChanged, signOut, updatePassword, EmailAuthProvider, reauthenticateWithCredential } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
    ref, uploadBytes, getDownloadURL
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";
import { httpsCallable } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-functions.js";
import { 
    getToken, onMessage
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-messaging.js"; // This is a client-side library
import {
    doc, getDoc, setDoc, updateDoc,
    collection, addDoc, query, where, orderBy, getDocs, serverTimestamp, limit, deleteDoc, onSnapshot, arrayUnion
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

// ===== DOM Elements =====
const main = document.querySelector("main");
const navButtons = document.querySelectorAll(".bottom-nav button");
const defaultPage = "home";
let verifiedTier = "Free Tier"; // In-memory tier

initializeTheme(); // Apply theme on initial load

// ===== Helper: Clear dynamic assets =====
function clearDynamicAssets() {
    document.querySelectorAll("script[data-dynamic], link[data-dynamic], style[data-dynamic]").forEach(el => el.remove());
}

// ===== Modal System =====
function showModal(options) {
    const defaults = {
        message: '',
        confirmText: 'OK',
        cancelText: 'Cancel',
        onConfirm: () => {},
        onCancel: () => {},
        confirmClass: 'btn-primary',
        showCancel: false,
        inputType: null, // e.g., 'text' or 'password'
        inputValue: '',
        inputPlaceholder: ''
    };
    const config = { ...defaults, ...options };

    let modal = document.getElementById("customModal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "customModal";
        modal.className = "modal";
        modal.innerHTML = `
            <div class="modal-content">
                <p id="modalMessage"></p>
                <input type="text" id="modalInput" style="display: none;" />
                <div class="modal-actions">
                    <button id="modalCancel"></button>
                    <button id="modalConfirm"></button>
                </div>
            </div>`;
        document.body.appendChild(modal);
    }

    const modalMessage = modal.querySelector("#modalMessage");
    const confirmBtn = modal.querySelector("#modalConfirm");
    const cancelBtn = modal.querySelector("#modalCancel");
    const modalInput = modal.querySelector("#modalInput");

    modalMessage.textContent = config.message;
    confirmBtn.textContent = config.confirmText;
    confirmBtn.className = config.confirmClass;
    cancelBtn.textContent = config.cancelText;
    cancelBtn.className = 'btn-secondary';
    cancelBtn.style.display = config.showCancel ? 'inline-block' : 'none';
    modal.style.display = "flex";

    // Handle input field
    if (config.inputType) {
        modalInput.style.display = 'block';
        modalInput.type = config.inputType;
        modalInput.value = config.inputValue;
        modalInput.placeholder = config.inputPlaceholder;
    } else {
        modalInput.style.display = 'none';
    }

    const cleanup = () => { modal.style.display = "none"; confirmBtn.onclick = null; cancelBtn.onclick = null; modalInput.style.display = 'none'; };
    confirmBtn.onclick = () => { cleanup(); config.onConfirm(modalInput.value); };
    cancelBtn.onclick = () => { cleanup(); config.onCancel(); };
}

// ===== Theme Functions =====
function applyTheme(isDark) {
    document.documentElement.classList.toggle("dark-mode", isDark);
}

function initializeTheme() {
    const isDark = localStorage.getItem('darkMode') === 'true';
    applyTheme(isDark);
}

let cleanupAnimation = () => {}; // A function to stop the animation

function toggleBackgroundAnimation(show) {
    let animationArea = document.querySelector('.area');
    cleanupAnimation(); // Always cleanup previous state

    if (show) {
        if (!animationArea) {
            animationArea = document.createElement('div');
            animationArea.className = 'area';
            const list = document.createElement('ul');
            list.className = 'circles';
            for (let i = 0; i < 10; i++) {
                const li = document.createElement('li');
                list.appendChild(li);
            }
            animationArea.appendChild(list);
            document.body.prepend(animationArea);
        }
        cleanupAnimation = initInteractiveBackground(animationArea); // Start new animation
    } else {
        if (animationArea) {
            animationArea.remove();
        }
    }
}
const CLASS_TO_TIER = { free: "Free Tier", premium: "Premium Tier", vip: "VIP / Elite Tier", vvip: "VVIP / Pro Elite Tier" };
const TIER_ORDER = ["Free Tier", "Premium Tier", "VIP / Elite Tier", "VVIP / Pro Elite Tier"];

async function updateCurrentTierDisplay(userId) {
    const tierDisplay = document.getElementById("user-tier");
    if (!tierDisplay || !userId) return;
 
    const snapshot = await getDoc(doc(db, "users", userId));
    const userData = snapshot.exists() ? snapshot.data() : {};
    const tier = userData.tier || "Free Tier";
    const expiry = userData.tierExpiry;
 
    tierDisplay.textContent = tier;
 
    const expiryDisplay = document.getElementById("tier-expiry");
    if (expiryDisplay) {
        if (expiry && tier !== 'Free Tier') {
            expiryDisplay.textContent = `Expires on: ${new Date(expiry).toLocaleDateString()}`;
            expiryDisplay.style.display = 'block';
        } else {
            expiryDisplay.style.display = 'none';
        }
    }
    verifiedTier = tier; // update memory
    enforceTierRestrictions();
}

// Flutterwave payment + trial/free handling
async function handlePayment(userId, tier, amount, period) {
    let paymentCompleted = false; // Flag to prevent onclose modal after success

    if (parseFloat(amount) === 0) {
        await updateUserTier(userId, tier, period);
        showModal({ message: `You have selected the ${tier}` });
        return;
    }

    const txRef = `TX-${Date.now()}`;

    FlutterwaveCheckout({
        public_key: FLWPUBK,
        tx_ref: txRef,
        amount: amount,
        currency: "NGN",
        payment_options: "card,ussd,qr,banktransfer",
        customer: {
            email: auth.currentUser?.email || "user@example.com",
            name: auth.currentUser?.displayName || "User",
        },
        customizations: {
            title: "Statwise Subscription",
            description: `${tier} (${period}) plan`,
        },
        callback: async function(data) {
            paymentCompleted = true;
            this.close(); // Close the modal immediately

            if (data.status === "successful" || data.status === "completed") {
                showLoader();
                try {
                    // Call a Cloud Function to securely verify the payment and update the user's tier.
                    const verifyPayment = httpsCallable(functions, 'verifyPaymentAndGrantReward');
                    const result = await verifyPayment({
                        transaction_id: data.transaction_id,
                        tx_ref: data.tx_ref,
                        tier: tier,
                        period: period
                    });
                    showModal({ message: result.data.message || "Your subscription has been updated!" });
                } catch (error) {
                    console.error("Error verifying payment:", error);
                    let errorMessage = "Payment verification failed. Please contact support with your transaction details.";
                    
                    if (error.code === 'functions/cancelled') {
                        errorMessage = "Payment verification was cancelled. Please try again.";
                    } else if (error.code === 'functions/deadline-exceeded') {
                        errorMessage = "Payment verification timed out. Your payment may still be processing.";
                    } else if (error.code === 'functions/unavailable') {
                        errorMessage = "Payment service is temporarily unavailable. Please try again later.";
                    } else if (error.message && error.message.includes('network')) {
                        errorMessage = "Network error during payment verification. Please check your connection and try again.";
                    }
                    
                    showModal({ 
                        message: errorMessage,
                        confirmClass: 'btn-danger',
                        confirmText: 'Contact Support',
                        onConfirm: () => window.open('mailto:support@statwise.com?subject=Payment Issue')
                    });
                } finally {
                    hideLoader();
                }
            } else {
                showModal({ message: "Payment was not completed.", confirmClass: 'btn-danger' });
            }
        },
        onclose: function () {
            if (!paymentCompleted) {
                showModal({ message: "Payment window closed. Your transaction was not completed." });
            }
        }
    });
}

async function updateUserTier(userId, tier, period = null, expiry = null) {
    if (!userId || !tier) {
        console.error('updateUserTier called with invalid parameters');
        throw new Error('Invalid user ID or tier specified');
    }

    const userRef = doc(db, "users", userId);
    const updateData = {
        tier: tier,
        tierExpiry: expiry
    };

    if (tier === 'Free Tier' || !expiry) {
        updateData.tierExpiry = null; // Reset expiry for free tier
        updateData.autoRenew = false; // Disable auto-renew for free tier/cancellation
    }

    try {
        await updateDoc(userRef, updateData);
        verifiedTier = tier;
        await updateCurrentTierDisplay(userId);
        enforceTierRestrictions();
    } catch (error) {
        console.error('Failed to update user tier:', error);
        let errorMessage = 'Failed to update subscription. Please try again.';
        
        if (error.code === 'firestore/permission-denied') {
            errorMessage = 'Permission denied. Please contact support.';
        } else if (error.code === 'firestore/unavailable') {
            errorMessage = 'Service temporarily unavailable. Please try again later.';
        } else if (error.message && error.message.includes('network')) {
            errorMessage = 'Network error. Please check your connection and try again.';
        }
        
        throw new Error(errorMessage);
    }
}

// Attach subscription buttons with upgrade fully visible
async function attachSubscriptionButtons(userId) {
    if (!userId) return;

    const snapshot = await getDoc(doc(db, "users", userId));
    const currentTier = snapshot.exists() ? snapshot.data().tier : "Free Tier";
    const currentRank = TIER_ORDER.indexOf(currentTier);

    document.querySelectorAll(".subscription-card").forEach((card) => {
        const btn = card.querySelector(".subscribe-btn");
        if (!btn) return;

        const tierClass = Object.keys(CLASS_TO_TIER).find(cls => card.classList.contains(cls));
        const cardTier = tierClass ? CLASS_TO_TIER[tierClass] : (card.querySelector("h2")?.textContent.trim() || "Free Tier");
        const cardRank = TIER_ORDER.indexOf(cardTier);

        card.classList.remove('is-current-plan');
        btn.style.display = 'inline-block';

        if (cardRank < currentRank) {
            btn.style.display = 'none'; // Hide downgrade options
        } else if (cardRank === currentRank) {
            card.classList.add('is-current-plan');
            btn.textContent = 'Current Plan';
            btn.disabled = true;
        } else { // cardRank > currentRank
            btn.textContent = 'Upgrade';
            btn.disabled = false;
            btn.onclick = function(e) { // Use a function to ensure 'this' refers to the button
                e.preventDefault();
                const amount = parseFloat(btn.dataset.amount) || 0;
                const period = btn.dataset.period || "monthly";
                
                showModal({
                    message: `Proceed to upgrade to ${cardTier} for ₦${amount.toLocaleString()} (${period})?`,
                    showCancel: true,
                    confirmText: 'Proceed to Payment',
                    onConfirm: () => handlePayment(userId, cardTier, amount, period)
                });
            };
        }
    });
}

async function initManageSubscriptionPage(userId) {
    if (!userId) return;

    const planInfoCard = document.getElementById('plan-info-card');
    const changePlanBtn = document.getElementById('changePlanBtn');
    const cancelContainer = document.getElementById('cancel-subscription-container');
    const cancelBtn = document.getElementById('cancelSubscriptionBtn');
    const autoRenewContainer = document.getElementById('auto-renew-container');
    const viewPaymentHistoryBtn = document.getElementById('viewPaymentHistoryBtn');
    const toggleAutoRenewBtn = document.getElementById('toggleAutoRenewBtn');

    if (!planInfoCard || !changePlanBtn || !cancelContainer || !cancelBtn || !autoRenewContainer || !toggleAutoRenewBtn) return;

    const TIER_BENEFITS = {
        "Free Tier": ["Basic Access", "Limited Features", "Ads Supported"],
        "Premium Tier": ["Full Access", "No Ads", "Priority Support"],
        "VIP / Elite Tier": ["All Premium Features", "Exclusive Content", "VIP Support"],
        "VVIP / Pro Elite Tier": ["All VIP Features", "1-on-1 Coaching", "Early Access"]
    };

    try {
        const userRef = doc(db, "users", userId);
        const snapshot = await getDoc(userRef);
        const userData = snapshot.exists() ? snapshot.data() : {};

        const tier = userData.tier || "Free Tier";
        const expiry = userData.tierExpiry;
        const autoRenew = userData.autoRenew ?? false; // Default to false if undefined
        const benefits = TIER_BENEFITS[tier] || [];

        if (tier === 'Free Tier') {
            planInfoCard.innerHTML = `
                <h2>You are on the Free Tier</h2>
                <p>Upgrade your plan to unlock exclusive features, remove ads, and get priority support.</p>
            `;
            changePlanBtn.textContent = 'View Plans & Upgrade';
            cancelContainer.style.display = 'none';
            autoRenewContainer.style.display = 'none';
        } else {
            const benefitsList = benefits.map(b => `<li>${b}</li>`).join('');
            const remainingDays = Math.ceil((new Date(expiry) - new Date()) / (1000 * 60 * 60 * 24));
            const remainingDaysText = remainingDays > 0 ? `${remainingDays} day(s) remaining` : 'Expires today';

            planInfoCard.innerHTML = `
                <h2>Your Current Plan: ${tier}</h2>
                <p><strong>Status:</strong> Active until ${new Date(expiry).toLocaleDateString()}</p>
                <h3>Plan Benefits:</h3>
                <ul>${benefitsList}</ul>
                <div class="remaining-days-indicator">${remainingDaysText}</div>
            `;
            changePlanBtn.textContent = 'Change Plan';
            cancelContainer.style.display = 'block';
            autoRenewContainer.style.display = 'block';

            // Configure auto-renew button
            if (autoRenew) {
                toggleAutoRenewBtn.textContent = 'Cancel Auto-Renewal';
                toggleAutoRenewBtn.classList.add('btn-danger');
            } else {
                toggleAutoRenewBtn.textContent = 'Enable Auto-Renewal';
                toggleAutoRenewBtn.classList.remove('btn-danger');
            }
        }

        changePlanBtn.onclick = () => loadPage('subscriptions', userId);

        toggleAutoRenewBtn.onclick = async () => {
            const newAutoRenewState = !autoRenew;
            await updateDoc(doc(db, "users", userId), { autoRenew: newAutoRenewState });
            const message = newAutoRenewState ? "Auto-renewal has been enabled." : "Auto-renewal has been cancelled.";
            showModal({ message });
            await initManageSubscriptionPage(userId); // Refresh content
        };

        cancelBtn.onclick = () => {
            const benefitsLost = TIER_BENEFITS[tier]
                .filter(b => !TIER_BENEFITS["Free Tier"].includes(b))
                .map(b => `<li>${b}</li>`)
                .join('');

            const cancellationMessage = `
                <p>Are you sure you want to cancel? You will lose access to these benefits:</p>
                <ul style="text-align: left; padding-left: 20px; margin-top: 10px;">${benefitsLost}</ul>
            `;

            showModal({
                message: cancellationMessage,
                showCancel: true,
                confirmText: 'Yes, Cancel',
                confirmClass: 'btn-danger',
                onConfirm: async () => {
                    await updateUserTier(userId, 'Free Tier', null, null);
                    await addHistoryUnique(userId, "Subscription cancelled");
                    showModal({ message: "Your subscription has been successfully cancelled." });
                    await initManageSubscriptionPage(userId); // Refresh the page content
                }
            });
        };
    } catch (error) {
        console.error("Failed to load subscription management page:", error);
        planInfoCard.innerHTML = `<h2>Error</h2><p>Could not load your subscription details. Please try again later.</p>`;
    }

    if (viewPaymentHistoryBtn) {
        viewPaymentHistoryBtn.onclick = () => {
            sessionStorage.setItem('targetTab', 'transactions-tab');
            loadPage('history', userId);
        };
    }
}

// ===== Tier Restrictions & Watchdog =====
function enforceTierRestrictions() {
    document.querySelectorAll("[data-tier]").forEach(el => {
        const requiredTier = el.dataset.tier;
        if (!requiredTier) return;

        const requiredTierName = CLASS_TO_TIER[requiredTier] || requiredTier;
        const hasAccess = TIER_ORDER.indexOf(verifiedTier) >= TIER_ORDER.indexOf(requiredTierName);

        // Handle navigation buttons separately: show/hide them completely.
        if (el.matches('.bottom-nav button')) {
            el.style.display = hasAccess ? 'flex' : 'none';
        } else {
            // For other elements (like cards), lock them with an overlay effect.
            if (!hasAccess) {
                el.style.opacity = "0.8";
                el.dataset.locked = "true";
                el.setAttribute("title", `Requires ${requiredTierName} subscription`);
            } else {
                el.style.opacity = "1";
                el.dataset.locked = "false";
                el.removeAttribute("title");
            }
        }
    });
}

function startTierWatchdog(userId) {
    if (!userId) return;
    const userRef = doc(db, "users", userId);
    onSnapshot(userRef, async (snapshot) => {
        if (!snapshot.exists()) return;
        const userData = snapshot.data();
        const dbTier = userData.tier || "Free Tier";

        // Always trust the database as the single source of truth.
        if (verifiedTier !== dbTier) {
            verifiedTier = dbTier;
        }
        enforceTierRestrictions();
    });
}

// ===== Firebase Cloud Messaging (FCM) Functions =====
async function initFirebaseMessaging(userId) {
    try {
        const permission = await Notification.requestPermission();
        if (permission === 'granted') {
            console.log('Notification permission granted.');
            // IMPORTANT: Replace the placeholder below with your actual VAPID key from the Firebase Console.
            // Go to Project Settings > Cloud Messaging > Web configuration > Key pair.
            const vapidKey = 'BM1G4B3crWgsfCGig6_i1crB3GAGBO8GAWlDVHP5jwTq1ltxb4S3e_IBJRUThKdHOeVf9VTmBNgFffDjwRNXeqU';
            const fcmToken = await getToken(messaging, { vapidKey: vapidKey });

            if (fcmToken) {
                // Save the new token to the user's document
                const userRef = doc(db, "users", userId);
                await updateDoc(userRef, {
                    fcmTokens: arrayUnion(fcmToken),
                    notifications: true // Also enable the general flag
                });
                console.log('FCM Token saved:', fcmToken);
                showModal({ message: 'Push notifications enabled successfully!' });
            } else {
                console.log('No registration token available. Request permission to generate one.');
                showModal({ 
                    message: 'Unable to set up push notifications. Please try refreshing the page.', 
                    confirmClass: 'btn-warning' 
                });
            }
        } else if (permission === 'denied') {
            console.log('Notification permission denied.');
            // If permission is denied, ensure the toggle is off
            const userRef = doc(db, "users", userId);
            await updateDoc(userRef, { notifications: false });
            const toggle = document.getElementById('predictionAlertsToggle');
            if (toggle) toggle.checked = false;
            
            showModal({ 
                message: 'Push notifications are blocked in your browser. To enable them, click the notification icon in your browser\'s address bar and allow notifications for this site.', 
                confirmClass: 'btn-warning',
                confirmText: 'Got it'
            });
        } else {
            console.log('Notification permission default - user dismissed the prompt.');
            showModal({ 
                message: 'Push notifications were not enabled. You can enable them later in your profile settings.', 
                confirmClass: 'btn-warning' 
            });
        }
    } catch (error) {
        console.error('Error setting up push notifications:', error);
        let errorMessage = 'Failed to set up push notifications. This feature may not be supported on your device.';
        
        if (error.code === 'messaging/failed-service-worker-registration') {
            errorMessage = 'Failed to register service worker for notifications. Please refresh the page and try again.';
        } else if (error.code === 'messaging/unsupported-browser') {
            errorMessage = 'Push notifications are not supported in your browser.';
        } else if (error.code === 'messaging/permission-blocked') {
            errorMessage = 'Notification permissions are blocked. Please enable them in your browser settings.';
        }
        
        showModal({ message: errorMessage, confirmClass: 'btn-warning' });
        
        // Ensure the toggle is off on error
        const toggle = document.getElementById('predictionAlertsToggle');
        if (toggle) toggle.checked = false;
    }

    // Handle foreground messages
    onMessage(messaging, (payload) => {
        console.log('Message received in foreground.', payload);
        showModal({
            message: `${payload.notification.title}: ${payload.notification.body}`,
            confirmText: 'Awesome!',
        });
    });
}

/**
 * Fetches and displays user statistics on the profile page.
 * @param {string} userId The current user's ID.
 * @param {object} userData The user's document data.
 */
async function displayUserStats(userId, userData) {
    const memberSinceEl = document.getElementById('memberSinceStat');
    const totalPredictionsEl = document.getElementById('totalPredictionsStat');
    const winRateEl = document.getElementById('winRateStat');

    if (!memberSinceEl || !totalPredictionsEl || !winRateEl) return;

    // 1. Member Since
    memberSinceEl.textContent = new Date(userData.createdAt).toLocaleDateString();

    // 2. Predictions and Win Rate
    const historyRef = collection(db, "users", userId, "history");
    const predictionsQuery = query(historyRef, where("match", "!=", null));
    const querySnapshot = await getDocs(predictionsQuery);

    let total = 0;
    let wins = 0;
    let losses = 0;

    querySnapshot.forEach(doc => {
        const data = doc.data();
        total++;
        if (data.result === 'win') wins++;
        if (data.result === 'loss') losses++;
    });

    totalPredictionsEl.textContent = total;
    const winnableGames = wins + losses;
    winRateEl.textContent = winnableGames > 0 ? `${Math.round((wins / winnableGames) * 100)}%` : 'N/A';
}
// ===== Profile Functions =====
/**
 * Initializes all interactive elements on the profile page.
 * @param {string} userId - The current user's ID.
 */
async function initProfilePage(userId) {
    if (!userId) return;
    await updateCurrentTierDisplay(userId); // Ensure tier info is loaded and displayed

    const userRef = doc(db, "users", userId);
    const snapshot = await getDoc(userRef);
    const userData = snapshot.exists() ? snapshot.data() : {};

    // 1. Avatar and User Info
    const avatarContainer = document.getElementById('profileAvatarContainer');
    const avatarUploadInput = document.getElementById('avatarUpload');
    const userNameEl = document.getElementById('userName');
    const editUsernameBtn = document.getElementById('editUsernameBtn');
    const userEmailEl = document.getElementById('userEmail');

    const displayAvatar = (url, name) => {
        if (!avatarContainer) return;
        if (url) {
            avatarContainer.innerHTML = `<img src="${url}" alt="Profile Picture" class="profile-avatar-img">`;
        } else {
            const initial = name ? name.charAt(0).toUpperCase() : 'U';
            avatarContainer.innerHTML = `<span>${initial}</span>`;
        }
    };

    displayAvatar(userData.photoURL, userData.username);
    if (userNameEl) userNameEl.textContent = userData.username || 'User';
    if (userEmailEl) userEmailEl.textContent = userData.email || auth.currentUser?.email || 'N/A';

    if (avatarContainer && avatarUploadInput) {
        avatarContainer.addEventListener('click', () => avatarUploadInput.click());

        avatarUploadInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            // Validate file type and size
            const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
            const maxSize = 5 * 1024 * 1024; // 5MB

            if (!allowedTypes.includes(file.type)) {
                showModal({ 
                    message: 'Please select a valid image file (JPG, PNG, GIF, or WebP).', 
                    confirmClass: 'btn-danger' 
                });
                e.target.value = ''; // Clear the input
                return;
            }

            if (file.size > maxSize) {
                showModal({ 
                    message: 'Image file is too large. Please choose a file smaller than 5MB.', 
                    confirmClass: 'btn-danger' 
                });
                e.target.value = ''; // Clear the input
                return;
            }

            showLoader();
            try {
                const storageRef = ref(storage, `profile_pictures/${userId}`);
                const uploadResult = await uploadBytes(storageRef, file);
                const downloadURL = await getDownloadURL(uploadResult.ref);

                await updateDoc(userRef, { photoURL: downloadURL });
                displayAvatar(downloadURL, userData.username);
                await addHistoryUnique(userId, 'Updated profile picture');
                showModal({ message: 'Profile picture updated successfully!' });
            } catch (error) {
                console.error("Avatar upload failed:", error);
                let errorMessage = 'Failed to upload image. Please try again.';
                
                if (error.code === 'storage/unauthorized') {
                    errorMessage = 'You do not have permission to upload images. Please contact support.';
                } else if (error.code === 'storage/canceled') {
                    errorMessage = 'Image upload was cancelled.';
                } else if (error.code === 'storage/quota-exceeded') {
                    errorMessage = 'Storage quota exceeded. Please contact support.';
                } else if (error.message && error.message.includes('network')) {
                    errorMessage = 'Network error. Please check your connection and try again.';
                }
                
                showModal({ message: errorMessage, confirmClass: 'btn-danger' });
                e.target.value = ''; // Clear the input on error
            } finally {
                hideLoader();
            }
        });
    }

    if (editUsernameBtn && userNameEl) {
        editUsernameBtn.addEventListener('click', () => {
            showModal({
                message: 'Enter your new username:',
                showCancel: true,
                confirmText: 'Save',
                inputType: 'text',
                inputValue: userNameEl.textContent,
                onConfirm: async (newUsername) => {
                    if (newUsername && newUsername.trim() !== '' && newUsername !== userNameEl.textContent) {
                        const trimmedUsername = newUsername.trim();
                        
                        // Validate username length and characters
                        if (trimmedUsername.length < 2 || trimmedUsername.length > 30) {
                            showModal({ 
                                message: 'Username must be between 2 and 30 characters long.', 
                                confirmClass: 'btn-danger' 
                            });
                            return;
                        }
                        
                        if (!/^[a-zA-Z0-9_\-\s]+$/.test(trimmedUsername)) {
                            showModal({ 
                                message: 'Username can only contain letters, numbers, spaces, hyphens, and underscores.', 
                                confirmClass: 'btn-danger' 
                            });
                            return;
                        }

                        showLoader();
                        try {
                            await updateDoc(userRef, { username: trimmedUsername });
                            await auth.currentUser.updateProfile({ displayName: trimmedUsername });
                            await addHistoryUnique(userId, `Username changed to ${trimmedUsername}`);
                            userNameEl.textContent = trimmedUsername;
                            showModal({ message: 'Username updated successfully!' });
                        } catch (error) {
                            console.error("Username update failed:", error);
                            let errorMessage = 'Failed to update username. Please try again.';
                            
                            if (error.code === 'firestore/permission-denied') {
                                errorMessage = 'You do not have permission to update your username.';
                            } else if (error.message && error.message.includes('network')) {
                                errorMessage = 'Network error. Please check your connection and try again.';
                            }
                            
                            showModal({ message: errorMessage, confirmClass: 'btn-danger' });
                        } finally {
                            hideLoader();
                        }
                    }
                }
            });
        });
    }

    // Display User Stats
    await displayUserStats(userId, userData);

    // 2. Dark Mode Toggle
    const darkToggle = document.getElementById("darkModeToggle");
    if (darkToggle) {
        darkToggle.checked = localStorage.getItem('darkMode') === 'true';
        darkToggle.addEventListener("change", () => {
            const isDark = darkToggle.checked;
            applyTheme(isDark);
            localStorage.setItem('darkMode', isDark);
        });
    }

    // Background animation toggle removed - animation is now only on auth pages

    // 3. Notification Toggle
    const predictionAlertsToggle = document.getElementById("predictionAlertsToggle");
    if (predictionAlertsToggle) {
        // The card is hidden by CSS/JS, but we set the state anyway
        predictionAlertsToggle.checked = userData.notifications ?? false;
        predictionAlertsToggle.addEventListener("change", async () => {
            if (predictionAlertsToggle.checked) {
                await initFirebaseMessaging(userId); // Request permission and get token
            }
        });
    }

    // 4. Referral Program Button
    const referralBtn = document.getElementById("referralBtn");
    if (referralBtn) {
        referralBtn.onclick = (e) => {
            e.preventDefault();
            loadPage("referral", userId);
        };
    }

    // 4. Manage Subscription Button
    const manageBtn = document.getElementById("manageSubscription");
    if (manageBtn) manageBtn.onclick = (e) => {
        e.preventDefault(); // Prevent default link behavior
        loadPage("manage-subscription", userId);
    };

    // 5. Change Password Button
    const changePasswordBtn = document.getElementById('changePasswordBtn');
    if (changePasswordBtn) {
        changePasswordBtn.onclick = () => {
            showModal({
                message: "Enter your current password to continue:",
                inputType: 'password',
                inputPlaceholder: 'Current Password',
                showCancel: true,
                confirmText: 'Verify',
                onConfirm: async (currentPassword) => {
                    if (!currentPassword) return;
                    showLoader();
                    try {
                        const credential = EmailAuthProvider.credential(currentUser.email, currentPassword);
                        await reauthenticateWithCredential(currentUser, credential);
                        
                        // Re-authentication successful, now ask for the new password
                        hideLoader();
                        showModal({
                            message: 'Enter your new password:',
                            inputType: 'password',
                            inputPlaceholder: 'New Password',
                            showCancel: true,
                            confirmText: 'Save New Password',
                            onConfirm: async (newPassword) => {
                                if (newPassword && newPassword.length >= 6) {
                                    await updatePassword(currentUser, newPassword);
                                    await addHistoryUnique(userId, 'Password updated');
                                    showModal({ message: 'Password updated successfully!' });
                                } else {
                                    showModal({ message: 'Password must be at least 6 characters long.', confirmClass: 'btn-danger' });
                                }
                            }
                        });
                    } catch (error) {
                        hideLoader();
                        showModal({ message: `Error: ${error.message}`, confirmClass: 'btn-danger' });
                    }
                }
            });
        };
    }

    // 5. Logout Button
    const logoutBtn = document.getElementById("logoutBtn");
    if (logoutBtn) logoutBtn.onclick = () => showModal({
        message: "Are you sure you want to logout?",
        showCancel: true,
        confirmText: 'Logout',
        confirmClass: 'btn-danger',
        onConfirm: async () => {
            await signOut(auth);
            await addHistoryUnique(userId, "Logged out");
            localStorage.clear(); // Clear storage on logout
            window.location.href = 'Auth/login.html';
        }
    });

    // 6. Reset Storage Button
    const resetBtn = document.getElementById("resetStorage");
    if (resetBtn) resetBtn.onclick = () => showModal({
        message: "Are you sure you want to reset this device’s cached data?",
        showCancel: true,
        confirmText: 'Reset',
        confirmClass: 'btn-danger',
        onConfirm: () => {
            localStorage.clear();
            location.reload();
        }
    });

    // 7. Delete Account Button
    const deleteAccountBtn = document.getElementById("deleteAccountBtn");
    if (deleteAccountBtn) {
        deleteAccountBtn.onclick = () => {
            showModal({
                message: "Are you absolutely sure you want to delete your account? This action is irreversible.",
                showCancel: true,
                confirmText: "I Understand, Continue",
                confirmClass: 'btn-danger',
                onConfirm: () => {
                    showModal({
                        message: 'To confirm, please type "DELETE" in the box below.',
                        showCancel: true,
                        confirmText: "Delete My Account",
                        confirmClass: 'btn-danger',
                        inputType: 'text',
                        inputPlaceholder: 'DELETE',
                        onConfirm: async (confirmationText) => {
                            if (confirmationText === "DELETE") {
                                showLoader();
                                const currentUser = auth.currentUser;
                                try {
                                    // 1. Delete Firestore documents
                                    await deleteDoc(doc(db, 'users', userId));
                                    await deleteDoc(doc(db, 'subscriptions', userId));

                                    // 2. Delete Firebase Auth user (requires recent login)
                                    await currentUser.delete();

                                    hideLoader();
                                    localStorage.clear();
                                    window.location.href = 'Auth/login.html';
                                } catch (error) {
                                    hideLoader();
                                    showModal({ message: `Error: ${error.message}`, confirmClass: 'btn-danger' });
                                }
                            } else { showModal({ message: "Incorrect confirmation text. Account was not deleted." }); }
                        }
                    });
                }
            });
        };
    }
}

/**
 * Initializes the referral page.
 * @param {string} userId - The current user's ID.
 */
async function initReferralPage(userId) {
    console.log("InitReferralPage called with userId:", userId);
    if (!userId) {
        console.error("No userId provided to initReferralPage");
        return;
    }

    const codeInput = document.getElementById('referralCodeInput');
    const copyBtn = document.getElementById('copyReferralCodeBtn');
    const referralListContainer = document.getElementById('referralListContainer');
    const rewardsContainer = document.getElementById('rewardsContainer');

    console.log("DOM elements found:", { codeInput, copyBtn, referralListContainer, rewardsContainer });

    if (!codeInput || !copyBtn || !referralListContainer) {
        console.error("Required DOM elements not found");
        return;
    }

    try {
        // 1. Get/Generate Referral Code
        console.log("Fetching user document for userId:", userId);
        const userRef = doc(db, "users", userId);
        const userSnap = await getDoc(userRef);
        
        if (!userSnap.exists()) {
            console.error("Referral Page Error: User document not found.");
            referralListContainer.innerHTML = `<p>Error: Could not load your referral information.</p>`;
            return;
        }
        
        const userData = userSnap.data();
        console.log("User data retrieved:", userData);
        let referralCode = userData.referralCode;

        if (!referralCode) {
            console.log("No referral code found, generating new one");
            referralCode = `REF-${userId.substring(0, 6).toUpperCase()}`;
            await updateDoc(userRef, { referralCode });
            console.log("Generated and saved referral code:", referralCode);
        }
        
        console.log("Setting referral code input:", referralCode);
        codeInput.value = referralCode;

    // 2. Copy Button Logic
    copyBtn.addEventListener('click', async () => {
        const codeToCopy = codeInput.value.startsWith('REF-') ? codeInput.value.substring(4) : codeInput.value;
        try {
            await navigator.clipboard.writeText(codeToCopy);
            copyBtn.textContent = 'Copied!';
            copyBtn.classList.add('success'); // Optional: for styling
        } catch (err) {
            console.error('Failed to copy text: ', err);
            copyBtn.textContent = 'Failed!';
        } finally {
            // Reset button text and style after 2 seconds
            setTimeout(() => { copyBtn.textContent = 'Copy'; copyBtn.classList.remove('success'); }, 2000);
        }
    });

    // 3. Share Button Logic
    const shareWhatsAppBtn = document.getElementById('shareWhatsAppBtn');
    const shareTwitterBtn = document.getElementById('shareTwitterBtn');
    const shareGenericBtn = document.getElementById('shareGenericBtn');

    const shareText = `Hey! I'm using StatWise for AI-powered sports predictions. Join using my referral code to get rewards: ${referralCode}`;
    const shareUrl = window.location.origin; // Your site's main URL

    if (shareGenericBtn) {
        if (navigator.share) {
            shareGenericBtn.style.display = 'inline-flex'; // Show button if API is supported
            shareGenericBtn.addEventListener('click', async () => {
                try {
                    await navigator.share({
                        title: 'Join me on StatWise!',
                        text: shareText,
                        url: shareUrl,
                    });
                } catch (error) {
                    console.error('Error using Web Share API:', error);
                }
            });
        } else {
            shareGenericBtn.style.display = 'none'; // Hide button if not supported
        }
    }

    if (shareWhatsAppBtn) {
        shareWhatsAppBtn.addEventListener('click', () => {
            const whatsappUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(shareText + ' ' + shareUrl)}`;
            window.open(whatsappUrl, '_blank');
        });
    }

    if (shareTwitterBtn) {
        shareTwitterBtn.addEventListener('click', () => {
            const twitterUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(shareText)}&url=${encodeURIComponent(shareUrl)}`;
            window.open(twitterUrl, '_blank');
        });
    }

        // 4. Fetch and display list of referred users
        console.log("Fetching referred users for userId:", userId);
        const referralsQuery = query(collection(db, "users"), where("referredBy", "==", userId));
        const querySnapshot = await getDocs(referralsQuery);
        console.log("Referred users query result:", querySnapshot.size, "users found");

    if (!querySnapshot.empty) {
        referralListContainer.innerHTML = ''; // Clear the placeholder
        querySnapshot.forEach(doc => {
            const referredUser = doc.data();
            const card = document.createElement('div');
            card.className = 'history-card';

            const isSubscribed = referredUser.tier !== 'Free Tier';
            const statusBadge = isSubscribed
                ? `<span class="status-badge status-successful">Subscribed</span>`
                : `<span class="status-badge status-pending">Joined</span>`;

            card.innerHTML = `
                <div class="history-title">${referredUser.username}</div>
                <p class="history-detail">Status: ${statusBadge}</p>
                <p class="history-time">Joined on: ${new Date(referredUser.createdAt).toLocaleDateString()}</p>
            `;
            referralListContainer.appendChild(card);
        });
    } else {
        referralListContainer.innerHTML = `<p>No referrals yet. Share your code to get started!</p>`;
    }

        // 5. Fetch and display rewards
        if (rewardsContainer) {
            console.log("Fetching rewards for userId:", userId);
            // Remove orderBy to avoid index requirement - we'll sort client-side instead
            const rewardsQuery = query(collection(db, "rewards"), where("referrerId", "==", userId));
            const rewardsCountEl = document.getElementById('rewardsCount');

            // Reset rewards display
            if (rewardsCountEl) rewardsCountEl.textContent = '0';
            const rewardsSnapshot = await getDocs(rewardsQuery);
            console.log("Rewards query result:", rewardsSnapshot.size, "rewards found");

        if (!rewardsSnapshot.empty) {
            rewardsContainer.innerHTML = ''; // Clear placeholder
            
            // Convert to array and sort client-side by createdAt (newest first)
            const rewardsArray = [];
            rewardsSnapshot.forEach(doc => {
                const reward = doc.data();
                reward.id = doc.id;
                rewardsArray.push(reward);
            });
            
            // Sort by createdAt (newest first)
            rewardsArray.sort((a, b) => {
                const aTime = a.createdAt?.toDate?.() || new Date(0);
                const bTime = b.createdAt?.toDate?.() || new Date(0);
                return bTime - aTime;
            });
            
            rewardsArray.forEach(reward => {
                const card = document.createElement('div');
                card.className = 'history-card';

                const statusBadge = reward.claimed
                    ? `<span class="status-badge status-successful">Claimed</span>`
                    : `<span class="status-badge status-pending">Pending</span>`;

                card.innerHTML = `
                    <div class="history-title">
                        ${reward.rewardDurationDays || 30}-Day ${reward.rewardTier || 'Premium'} Reward
                    </div>
                    <p class="history-detail">From: ${reward.grantedByUsername || 'System'}</p>
                    <p class="history-detail">Status: ${statusBadge}</p>
                    <p class="history-time">Granted on: ${reward.createdAt ? new Date(reward.createdAt.toDate()).toLocaleDateString() : 'Unknown'}</p>
                `;
                rewardsContainer.appendChild(card);
            });
            if (rewardsCountEl) {
                rewardsCountEl.textContent = rewardsSnapshot.size.toString();
            }
        } else {
            rewardsContainer.innerHTML = `<p>No rewards earned yet. You'll get a reward when a referred user subscribes!</p>`;
        }
    } else {
        console.log("rewardsContainer not found");
    }
        
    } catch (error) {
        console.error("Error in initReferralPage:", error);
        if (referralListContainer) {
            referralListContainer.innerHTML = `<p>Error loading referral page: ${error.message}</p>`;
        }
    }
}

// ===== Admin Function: Create Reward =====
window.createRewardForUser = async function(referrerId, referredUsername, rewardTier = "Premium", rewardDurationDays = 30) {
    try {
        const rewardData = {
            referrerId: referrerId,
            grantedByUsername: referredUsername,
            rewardTier: rewardTier,
            rewardDurationDays: rewardDurationDays,
            claimed: false,
            createdAt: serverTimestamp(),
            type: "referral_bonus"
        };

        const rewardRef = await addDoc(collection(db, "rewards"), rewardData);
        console.log("Reward created with ID:", rewardRef.id);
        
        // Also log this action
        if (referrerId) {
            const activityRef = collection(db, "users", referrerId, "history");
            await addDoc(activityRef, {
                action: `Referral reward granted: ${rewardDurationDays}-day ${rewardTier}`,
                createdAt: serverTimestamp(),
                creatorId: "admin"
            });
        }
        
        return rewardRef.id;
    } catch (error) {
        console.error("Error creating reward:", error);
        throw error;
    }
};

// ===== Admin Function: Grant Subscription =====
window.grantSubscription = async function(userId, tier, durationDays = 30) {
    try {
        const endDate = new Date();
        endDate.setDate(endDate.getDate() + durationDays);
        
        // Update user tier
        const userRef = doc(db, "users", userId);
        await updateDoc(userRef, {
            tier: tier,
            subscriptionEnd: endDate
        });
        
        // Update subscription document
        const subscriptionRef = doc(db, "subscriptions", userId);
        await updateDoc(subscriptionRef, {
            currentTier: tier,
            endDate: endDate,
            lastUpdated: serverTimestamp()
        });
        
        // Log the activity
        const activityRef = collection(db, "users", userId, "history");
        await addDoc(activityRef, {
            action: `Subscription granted: ${tier} for ${durationDays} days`,
            createdAt: serverTimestamp(),
            creatorId: "admin"
        });
        
        console.log(`Granted ${tier} subscription to user ${userId} for ${durationDays} days`);
        return true;
    } catch (error) {
        console.error("Error granting subscription:", error);
        throw error;
    }
};

// ===== Save AI Prediction =====
async function savePredictionToDB(userId, prediction) {
    if (!userId) return;
    try {
        const historyRef = collection(db, "users", userId, "history");
        await addDoc(historyRef, {
            match: prediction.match || "Unknown Match",
            prediction: prediction.prediction || prediction.pick || "-",
            odds: prediction.odds || "-",
            confidence: prediction.confidence || "-",
            result: prediction.result || "pending",
            createdAt: serverTimestamp()
        });
    } catch (err) {
        console.error("Failed to save prediction to DB:", err);
    }
}

// ===== Clean Predictions Older Than 7 Days =====
async function cleanupOldPredictions(userId) {
    if (!userId) return;

    const historyRef = collection(db, "users", userId, "history");
    const snapshot = await getDocs(historyRef);
    const now = Date.now();
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;

    snapshot.forEach(docu => {
        const data = docu.data();
        if (data.match) {
            const createdAtMs = data.createdAt?.toMillis?.() || 0;
            if (now - createdAtMs > sevenDaysMs) {
                deleteDoc(doc(db, "users", userId, "history", docu.id));
            }
        }
    });
}

// ===== Fetch History =====
async function fetchHistory(userId) {
    if (!userId) return;
    showLoader();
 
    const predictionsContainer = document.querySelector("#predictions-tab .history-container");
    const accountContainer = document.querySelector("#account-tab .history-container");
    const transactionsContainer = document.querySelector("#transactions-tab .history-container");
 
    if (!predictionsContainer || !accountContainer || !transactionsContainer) {
        hideLoader();
        return;
    }
 
    // Clear containers
    predictionsContainer.innerHTML = "";
    accountContainer.innerHTML = "";
    transactionsContainer.innerHTML = "";
 
    await cleanupOldPredictions(userId);
 
    try {
        // 1. Fetch Account & Prediction History
        const historyRef = collection(db, "users", userId, "history");
        const q = query(historyRef, orderBy("createdAt", "desc"));
        const historySnapshot = await getDocs(q);
 
        let hasPredictions = false;
        let hasAccountActions = false;
 
        if (!historySnapshot.empty) {
            historySnapshot.forEach(docu => {
                const data = docu.data();
                const card = document.createElement("div");
                card.className = "history-card";
 
                if (data.match) {
                    hasPredictions = true;
                    const resultClass = (data.result || "pending").toLowerCase();
                    card.innerHTML = `
                        <h2 class="history-title">${data.match}</h2>
                        <p class="history-detail">Pick: ${data.prediction || data.pick || "-"}</p>
                        <p class="history-detail">Odds: ${data.odds || "-"}</p>
                        <p class="history-detail">Confidence: ${data.confidence || "-"}</p>
                        <p class="history-time">${formatTimestamp(data.createdAt)}</p>
                        <span class="history-result ${resultClass}">${(data.result || "PENDING").toUpperCase()}</span>
                    `;
                    predictionsContainer.appendChild(card);
                } else if (data.action) {
                    hasAccountActions = true;
                    card.innerHTML = `
                        <p><strong>Action:</strong> ${data.action}</p>
                        <p><strong>IP:</strong> ${data.ip || "Unknown"}</p>
                        <p><small>${formatTimestamp(data.createdAt)}</small></p>
                    `;
                    accountContainer.appendChild(card);
                }
            });
        }
 
        if (!hasPredictions) predictionsContainer.innerHTML = "<p>No predictions yet.</p>";
        if (!hasAccountActions) accountContainer.innerHTML = "<p>No account activity yet.</p>";
 
        // 2. Fetch Transaction History
        const subRef = doc(db, "subscriptions", userId);
        const subSnap = await getDoc(subRef);
 
        if (subSnap.exists() && subSnap.data().transactions?.length > 0) {
            const transactions = subSnap.data().transactions
                .sort((a, b) => (b.createdAt?.toMillis() || 0) - (a.createdAt?.toMillis() || 0)); // Sort newest first
 
            transactions.forEach(tx => {
                const card = document.createElement("div");
                card.className = "history-card";
                const statusClass = (tx.status || "unknown").toLowerCase();
                card.innerHTML = `
                    <h2 class="history-title">${tx.description}</h2>
                    <p class="history-detail">Amount: ${tx.currency} ${tx.amount.toLocaleString()}</p>
                    <p class="history-detail">Status: <span class="status-badge status-${statusClass}">${tx.status}</span></p>
                    <p class="history-detail">ID: ${tx.transactionId}</p>
                    <p class="history-time">${formatTimestamp(tx.createdAt)}</p>
                `;
                transactionsContainer.appendChild(card);
            });
        } else {
            transactionsContainer.innerHTML = "<p>No transactions found.</p>";
        }
    } catch (err) {
        console.error("Failed to fetch history:", err);
        if (predictionsContainer) predictionsContainer.innerHTML = "<p>Error loading history.</p>";
        if (accountContainer) accountContainer.innerHTML = "<p>Error loading history.</p>";
        if (transactionsContainer) transactionsContainer.innerHTML = "<p>Error loading history.</p>";
    } finally {
        hideLoader();
    }
}

/**
 * Initializes a pull-to-refresh feature on a container.
 * This should be called once. The feature is activated/deactivated
 * by setting `container.dataset.pullToRefreshActive = 'true'/'false'`.
 * @param {HTMLElement} container The scrollable element.
 * @param {Function} onRefresh A function that returns a promise, to be called on refresh.
 */
function initPullToRefresh(container, onRefresh) {
    let startY = 0;
    let isDragging = false;
    let pullDistance = 0;
    let animationFrameId = null;
    const pullThreshold = 85; // Pixels to pull before refresh triggers

    // Create or find the refresh indicator in the BODY
    const refreshIndicator = document.getElementById('refresh-indicator');
    if (!refreshIndicator) {
        refreshIndicator = document.createElement('div');
        refreshIndicator.id = 'refresh-indicator';
        refreshIndicator.innerHTML = `<span>&#x21bb;</span>`; // Refresh icon
        document.body.appendChild(refreshIndicator);
    }

    const resetIndicator = () => {
        refreshIndicator.classList.add('transitioning');
        refreshIndicator.style.transform = 'translateX(-50%) scale(0)';
        refreshIndicator.style.opacity = '0';
        refreshIndicator.classList.remove('refreshing');
        // Remove transition after animation so next pull is instant
        setTimeout(() => {
            refreshIndicator.classList.remove('transitioning');
        }, 300); // Match CSS transition duration
    };

    container.addEventListener('touchstart', (e) => {
        if (container.dataset.pullToRefreshActive !== 'true' || container.scrollTop !== 0) {
            isDragging = false;
            return;
        }
        isDragging = true;
        startY = e.touches[0].pageY;
        pullDistance = 0;
        refreshIndicator.classList.remove('transitioning');
    }, { passive: true });

    const updateIndicator = () => {
        const pullRatio = Math.min(pullDistance / pullThreshold, 1);
        const elasticScale = pullRatio + (pullRatio * 0.2 * Math.sin(pullDistance * 0.1));
        const rotationAngle = pullDistance * 3;
        const shadowIntensity = 0.2 + (pullRatio * 0.3);
        
        refreshIndicator.style.opacity = pullRatio;
        refreshIndicator.style.transform = `translateX(-50%) scale(${Math.max(0.1, elasticScale)}) rotate(${rotationAngle}deg)`;
        refreshIndicator.style.boxShadow = `0 ${8 + pullRatio * 10}px ${25 + pullRatio * 15}px rgba(14, 99, 156, ${shadowIntensity})`;
        
        // Add color transition based on pull progress
        if (pullRatio >= 0.8) {
            refreshIndicator.style.background = 'linear-gradient(135deg, #4caf50 0%, #ff9800 50%, #0e639c 100%)';
        } else {
            refreshIndicator.style.background = 'linear-gradient(135deg, #0e639c 0%, #4caf50 50%, #ff9800 100%)';
        }
        
        animationFrameId = null; // Allow next frame to be requested
    };

    container.addEventListener('touchmove', (e) => {
        if (!isDragging) return;

        const currentY = e.touches[0].pageY;
        const newPullDistance = currentY - startY;

        if (newPullDistance > 0) {
            // Prevent the browser's overscroll-bounce effect on mobile
            e.preventDefault();
            pullDistance = newPullDistance;

            // Schedule a single update for the next animation frame
            if (!animationFrameId) {
                animationFrameId = requestAnimationFrame(updateIndicator);
            }
        } else {
            // If user starts scrolling up, stop the pull-to-refresh gesture
            isDragging = false;
        }
    }, { passive: false }); // passive:false is needed for preventDefault()

    container.addEventListener('touchend', async (e) => {
        if (!isDragging) return;
        isDragging = false;
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
            animationFrameId = null;
        }

        if (pullDistance >= pullThreshold) {
            // User pulled enough, trigger refresh
            refreshIndicator.classList.add('transitioning');
            refreshIndicator.style.transform = 'translateX(-50%) scale(1)';
            refreshIndicator.classList.add('refreshing');
            
            try {
                await onRefresh();
            } finally {
                // Once refresh is done, hide the indicator
                resetIndicator();
            }
        } else {
            // Didn't pull enough, just hide the indicator
            resetIndicator();
        }
    });
}

// ===== Dynamic Page Loader =====
async function loadPage(page, userId, addToHistory = true) {
    // Prevent rapid clicks from breaking the animation
    if (main.classList.contains('page-transitioning')) return;
    main.classList.add('page-transitioning');

    // Show loader at the start of any page load
    showLoader(); // Use the delayed loader for in-app navigation

    // Only run the fade-out animation if there's existing content to transition from.
    if (main.innerHTML.trim() !== '') {
        // Animate the current content out
        main.classList.add('page-fade-out');
        await new Promise(resolve => setTimeout(resolve, 200)); // Match animation duration
    }

    try {
        const response = await fetch(`Pages/${page}.html`);
        if (!response.ok) throw new Error(`Page not found: ${page}`);
        const html = await response.text();
        const parser = new DOMParser();
        const docu = parser.parseFromString(html, "text/html");
        const pageMain = docu.querySelector("main") || docu.body || docu;
        main.innerHTML = pageMain.innerHTML;
        main.dataset.pullToRefreshActive = 'false'; // Deactivate for all pages by default

        // Define a single base URL for resolving relative asset paths.
        // This works for both localhost (e.g., http://127.0.0.1:3000) and deployed environments.
        const assetsBaseUrl = new URL('Pages/', window.location.href).href;

        clearDynamicAssets();

        docu.querySelectorAll("link[rel='stylesheet']").forEach(link => {
            const newLink = document.createElement("link");
            newLink.rel = "stylesheet";
            // Resolve relative URLs against the base path, leave absolute URLs as is.
            newLink.href = new URL(link.getAttribute('href'), assetsBaseUrl).href;
            newLink.setAttribute("data-dynamic", "true");
            document.head.appendChild(newLink);
        });

        docu.querySelectorAll("style").forEach(style => {
            const newStyle = document.createElement("style");
            newStyle.textContent = style.textContent;
            newStyle.setAttribute("data-dynamic", "true");
            document.head.appendChild(newStyle);
        });

        docu.querySelectorAll("script").forEach(script => {
            const newScript = document.createElement("script");
            if (script.src) {
                // Resolve relative URLs against the base path.
                newScript.src = new URL(script.getAttribute('src'), assetsBaseUrl).href;
            } else {
                newScript.textContent = script.textContent;
            }
            newScript.setAttribute("data-dynamic", "true");
            document.body.appendChild(newScript);
        });

        navButtons.forEach(btn => btn.classList.toggle("active", btn.getAttribute("data-page") === page));
        localStorage.setItem("lastPage", page);
        if (addToHistory) history.pushState({ page }, "", `#${page}`);

        // Page-specific init
        if (page === "subscriptions") {
            updateCurrentTierDisplay(userId);
            attachSubscriptionButtons(userId);
            initTabs(); // Use the generic tab handler
        }
        
        // Remove any animated background elements from main app pages
        const bgElement = document.querySelector('.animated-background');
        if (bgElement) {
            bgElement.remove();
        }
        if (page === "manage-subscription") {
            await initManageSubscriptionPage(userId);
        }
        if (page === "profile") {
            await initProfilePage(userId);
        }
        if (page === "referral") {
            await initReferralPage(userId);
        }
        if (page === "history") {
            await fetchHistory(userId);
            initTabs(); // Use the generic tab handler
            // After tabs are initialized, check for a target tab from session storage
            const targetTabId = sessionStorage.getItem('targetTab');
            if (targetTabId) {
                const targetTabButton = document.querySelector(`.tab-btn[data-tab="${targetTabId}"]`);
                if (targetTabButton) {
                    handleTabSwitch(targetTabButton);
                }
                sessionStorage.removeItem('targetTab'); // Clean up
            }
        }
        if (page === "insights") {
            // Placeholder for any future JS needed for the insights page
            const cards = document.querySelectorAll('.insights-container .card');
            cards.forEach((card, index) => {
                card.style.animationDelay = `${index * 100}ms`;
                card.classList.add('card-animation');
            });
        }
        if (page === "home") {
            main.dataset.pullToRefreshActive = 'true'; // Activate for home page
            // No animated background on home page - only on auth pages
            initTabs(); // Initialize league tabs for home page
            initCollapsibleTabs(); // Initialize collapsible tabs functionality

            const cards = document.querySelectorAll('.prediction-card');
            cards.forEach((card, index) => {
                card.style.animationDelay = `${index * 100}ms`;
                card.classList.add('card-animation');
            });

            const searchInput = document.getElementById("predictionSearch");
            const predictionsContainer = document.querySelector(".predictions-container");

            if (searchInput && predictionsContainer) {
                // Create a wrapper for the search input to enable ghost text
                if (!searchInput.parentElement.classList.contains('search-wrapper')) {
                    const parent = searchInput.parentNode;
                    const wrapper = document.createElement('div');
                    wrapper.className = 'search-wrapper';
                    parent.replaceChild(wrapper, searchInput);

                    const ghostEl = document.createElement('div');
                    ghostEl.id = 'search-ghost-text';

                    wrapper.appendChild(ghostEl); // Ghost text first for z-index stacking
                    wrapper.appendChild(searchInput);
                }

                let noResultsEl = predictionsContainer.querySelector('.no-results-message');
                if (!noResultsEl) {
                    noResultsEl = document.createElement('p');
                    noResultsEl.className = 'no-results-message';
                    noResultsEl.textContent = 'No matches found.';
                    predictionsContainer.appendChild(noResultsEl);
                }

                const originalCardElements = Array.from(predictionsContainer.querySelectorAll(".prediction-card"));

                searchInput.addEventListener("input", () => {
                    const value = searchInput.value;
                    const lowerCaseValue = value.toLowerCase();

                    // 1. PARSE THE SEARCH QUERY
                    const commandRegex = /\/c\d*|\/odds|\//g; // Allow /c without number for autocomplete
                    const allCommands = lowerCaseValue.match(commandRegex) || [];
                    const textQuery = lowerCaseValue.replace(commandRegex, '').replace(/\s+/g, ' ').trim();

                    const sortCommands = allCommands.filter(c => c === '/' || c === '/odds');
                    const confidenceFilterCommand = allCommands.find(c => c.startsWith('/c'));
                    const minConfidence = confidenceFilterCommand && confidenceFilterCommand.length > 2 ? parseInt(confidenceFilterCommand.substring(2), 10) : 0;

                    const cards = Array.from(predictionsContainer.querySelectorAll(".prediction-card"));
                    let visibleCount = 0;

                    // 2. FILTER FOOTBALL COVERAGE TABS
                    const tabsContainer = document.getElementById('league-tabs');
                    if (tabsContainer && textQuery) {
                        const tabs = tabsContainer.querySelectorAll('.tab-btn');
                        let hasVisibleTabs = false;
                        
                        tabs.forEach(tab => {
                            const tabText = tab.textContent.toLowerCase();
                            const tabData = tab.dataset.tab || '';
                            
                            // Check if search term matches tab text or data attribute
                            const isMatch = tabText.includes(textQuery) || 
                                          tabData.includes(textQuery.replace(/\s+/g, '-'));
                            
                            if (isMatch || tab.dataset.tab === 'all-leagues') {
                                tab.style.display = 'flex';
                                hasVisibleTabs = true;
                            } else {
                                tab.style.display = 'none';
                            }
                        });
                        
                        // If no matches found, show "All" tab
                        if (!hasVisibleTabs) {
                            const allTab = tabsContainer.querySelector('[data-tab="all-leagues"]');
                            if (allTab) allTab.style.display = 'flex';
                        }
                    } else if (tabsContainer) {
                        // Reset tabs when search is empty
                        const tabs = tabsContainer.querySelectorAll('.tab-btn');
                        tabs.forEach(tab => tab.style.display = 'flex');
                    }

                    // 3. FILTER CARDS
                    cards.forEach(card => {
                        const title = card.querySelector(".match-title")?.textContent.toLowerCase() || '';
                        const confidence = parseInt(card.querySelector('.confidence span')?.textContent.match(/\d+/)?.[0] || '0', 10);

                        const textMatch = !textQuery || title.includes(textQuery);
                        const confidenceMatch = !confidenceFilterCommand || confidence >= minConfidence;

                        const shouldShow = textMatch && confidenceMatch;
                        card.style.display = shouldShow ? "block" : "none";
                        if (shouldShow) visibleCount++;
                    });

                    // 4. SORT VISIBLE CARDS
                    if (sortCommands.length > 0) {
                        const visibleCards = cards.filter(card => card.style.display === 'block');
                        visibleCards.sort((a, b) => {
                            let sortResult = 0;
                            for (const command of sortCommands) {
                                if (sortResult !== 0) break;
                                if (command === '/odds') { // Sort by highest odds
                                    const oddsA = parseFloat(a.querySelector('.odds')?.textContent.match(/[\d.]+/)?.[0] || '0');
                                    const oddsB = parseFloat(b.querySelector('.odds')?.textContent.match(/[\d.]+/)?.[0] || '0');
                                    sortResult = oddsB - oddsA;
                                } else if (command === '/') { // Sort by highest confidence
                                    const confidenceA = parseInt(a.querySelector('.confidence span')?.textContent.match(/\d+/)?.[0] || '0', 10);
                                    const confidenceB = parseInt(b.querySelector('.confidence span')?.textContent.match(/\d+/)?.[0] || '0', 10);
                                    sortResult = confidenceB - confidenceA;
                                }
                            }
                            return sortResult;
                        });
                        visibleCards.forEach(card => predictionsContainer.appendChild(card));
                    } else if (value.trim() === '') {
                        // 5. RESTORE ORIGINAL ORDER IF SEARCH IS EMPTY
                        originalCardElements.forEach(card => predictionsContainer.appendChild(card));
                    }

                    // 6. UPDATE UI (No Results Message & Autocomplete)
                    noResultsEl.style.display = visibleCount === 0 && value.trim() !== '' ? 'block' : 'none';

                    const ghostEl = document.getElementById('search-ghost-text');
                    if (ghostEl && value) { // Only show suggestions if there's input
                        let suggestion = '';
                        const commandsInValue = value.match(commandRegex) || [];
                        const textInValue = value.replace(commandRegex, '').trim();

                        // 1. Command Autocomplete
                        const lastChar = value.slice(-1);
                        const lastWord = value.split(' ').pop();

                        if (lastChar === '/') {
                            suggestion = value + 'odds';
                        } else if (lastWord.startsWith('/') && '/odds'.startsWith(lastWord) && lastWord !== '/odds') {
                            suggestion = value.substring(0, value.lastIndexOf(lastWord)) + '/odds';
                        } else if (lastWord === '/c') {
                            suggestion = value + '75';
                        }

                        // 2. Text Autocomplete (only if no command is being suggested)
                        if (textQuery) {
                            const allTitles = originalCardElements.map(card => card.querySelector('.match-title')?.textContent || '');
                            // Find a title that starts with the query, otherwise one that includes it.
                            let matchedTitle = allTitles.find(title => title.toLowerCase().startsWith(textQuery));

                            if (matchedTitle) {
                                // Reconstruct suggestion, preserving commands
                                suggestion = commandsInValue.join(' ') + ' ' + matchedTitle;
                            }
                        }

                        ghostEl.textContent = suggestion;
                    } else if (ghostEl) {
                        ghostEl.textContent = ''; // Clear suggestion on empty input
                    }
                });
            }
        }

        enforceTierRestrictions();

        // Reset scroll position for the new page
        main.scrollTop = 0;

    } catch (error) {
        console.error(`Failed to load page ${page}:`, error);
        let errorMessage = `Unable to load the ${page} page right now.`;
        let retryButton = '';
        
        if (error.name === 'TypeError' || error.message.includes('network')) {
            errorMessage = `There was a network issue. Please check your connection and try again.`;
            retryButton = `<button onclick="loadPage('${page}', '${userId || ''}', false)" class="button" style="margin-top: 10px;">Try Again</button>`;
        }
        
        main.innerHTML = `
            <div class="error-container">
                <h1 class="error-title">Oops!</h1>
                <h2 class="error-subtitle">Something went wrong</h2>
                <p class="error-message">${errorMessage}</p>
                ${retryButton}
                <a href="#" onclick="loadPage('home', '${userId || ''}', false); return false;" class="button">Go to Homepage</a>
            </div>
        `;
    } finally {
        // Animate the new content in
        main.classList.remove('page-fade-out');
        main.classList.add('page-fade-in'); // Start animation

        // The loader is hidden after a short, fixed delay. This is more reliable
        // than waiting for an animation event that might not fire on very fast loads.
        setTimeout(() => {
            hideLoader();
            main.classList.remove('page-fade-in', 'page-transitioning');
        }, 350); // A value slightly longer than the animation duration.
    }
}

// ===== Navigation =====
navButtons.forEach(button => {
    button.addEventListener("click", () => {
        const page = button.getAttribute("data-page");
        if (!page) return;
        loadPage(page, auth.currentUser?.uid);
    });
});

// ===== Browser Back/Forward =====
window.addEventListener("popstate", (e) => {
    const page = e.state?.page || defaultPage;
    loadPage(page, auth.currentUser?.uid, false);
});

// ===== Welcome Tour for New Users =====
let introJsLoaded = false;

function loadIntroJsAssets() {
    if (introJsLoaded) return Promise.resolve();
    return new Promise((resolve, reject) => {
        // Load CSS
        const cssLink = document.createElement('link');
        cssLink.rel = 'stylesheet';
        cssLink.href = 'https://unpkg.com/intro.js/minified/introjs.min.css';
        document.head.appendChild(cssLink);

        // Load JS
        const script = document.createElement('script');
        script.src = 'https://unpkg.com/intro.js/minified/intro.min.js';
        script.onload = () => {
            introJsLoaded = true;
            resolve();
        };
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

async function startWelcomeTour(userId) {
    try {
        await loadIntroJsAssets();

        const intro = introJs();
        intro.setOptions({
            steps: [
                {
                    title: 'Welcome to StatWise!',
                    intro: 'Let\'s take a quick tour of the main features.'
                },
                {
                    element: document.querySelector('.search-container'),
                    title: 'Search & Filter',
                    intro: 'Quickly find matches or use commands like <strong>/odds</strong> to sort by the highest odds.'
                },
                {
                    element: document.querySelector('.prediction-card'),
                    title: 'Prediction Cards',
                    intro: 'Each card gives you an AI-powered prediction, confidence level, and odds.'
                },
                {
                    element: document.querySelector('.bottom-nav [data-page="history"]'),
                    title: 'Your History',
                    intro: 'Track your past predictions, transactions, and account activity here.'
                },
                {
                    element: document.querySelector('.bottom-nav [data-page="profile"]'),
                    title: 'Your Profile',
                    intro: 'Manage your subscription, settings, and logout from your profile.'
                }
            ],
            showStepNumbers: true,
            exitOnOverlayClick: false,
            doneLabel: 'Got it!'
        });

        intro.oncomplete(async () => {
            await updateDoc(doc(db, "users", userId), { isNewUser: false });
        });

        intro.onexit(async () => {
            await updateDoc(doc(db, "users", userId), { isNewUser: false });
        });

        intro.start();

    } catch (error) {
        console.error("Failed to start welcome tour:", error);
        // Ensure the flag is still set to false even if the tour fails to load
        await updateDoc(doc(db, "users", userId), { isNewUser: false });
    }
}

/**
 * Handles tab switching for components like the history page.
 * @param {HTMLElement} tabButton The tab button that was clicked.
 */
function handleTabSwitch(tabButton) {
    // Find the closest common ancestor for the tabs and content
    const tabParent = tabButton.closest('.history-section, .subscription-section, main'); // Extendable
    if (!tabParent) return;

    // Don't do anything if the tab is already active
    if (tabButton.classList.contains('active')) return;

    const tabButtons = tabParent.querySelectorAll(".tab-btn");
    const tabContents = tabParent.querySelectorAll(".tab-content, .pricing-container");

    tabButtons.forEach(b => b.classList.remove("active"));
    tabButton.classList.add("active");

    tabContents.forEach(c => c.classList.remove("active"));
    const targetId = tabButton.dataset.tab;
    const target = tabParent.querySelector(`#${targetId}`);
    if (target) target.classList.add("active");
}

/**
 * Initializes tab functionality for the current page.
 * This should be called after a page with tabs is loaded.
 */
function initTabs() {
    const tabContainer = document.querySelector('.tab-container');
    if (!tabContainer) return;

    tabContainer.addEventListener('click', (e) => {
        const tabButton = e.target.closest('.tab-btn');
        if (tabButton) {
            handleTabSwitch(tabButton);
        }
    });
}

/**
 * Initialize tabs functionality for the homepage league tabs (no collapsing for better mobile UX)
 */
function initCollapsibleTabs() {
    const tabsContainer = document.getElementById('league-tabs');
    
    if (!tabsContainer) return;
    
    // Add click handlers to tab buttons for switching only
    tabsContainer.addEventListener('click', (e) => {
        const tabButton = e.target.closest('.tab-btn');
        if (!tabButton) return;
        
        // Handle tab switching - tabs stay visible for better mobile experience
        handleTabSwitch(tabButton);
    });
}

/**
 * Checks for any unclaimed referral rewards and applies them to the user's account.
 * This function is designed to be run for the currently logged-in user.
 * @param {string} userId The ID of the user (the referrer) to check rewards for.
 */
async function checkForAndClaimRewards(userId) {
    const rewardsRef = collection(db, "rewards");
    const q = query(rewardsRef, where("referrerId", "==", userId), where("claimed", "==", false));

    try {
        const querySnapshot = await getDocs(q);
        if (querySnapshot.empty) {
            return; // No rewards to claim
        }

        for (const rewardDoc of querySnapshot.docs) {
            const rewardData = rewardDoc.data();

            // 1. Apply the reward to the user's tier
            const newExpiry = new Date();
            newExpiry.setDate(newExpiry.getDate() + rewardData.rewardDurationDays);

            await updateUserTier(userId, rewardData.rewardTier, 'reward', newExpiry.toISOString());

            // 2. Mark the reward as claimed to prevent re-application
            await updateDoc(doc(db, "rewards", rewardDoc.id), { claimed: true });

            // 3. Notify the user
            const message = `You've received a ${rewardData.rewardDurationDays}-day ${rewardData.rewardTier} reward because ${rewardData.grantedByUsername} subscribed!`;
            showModal({ message });
            await addHistoryUnique(userId, `Claimed referral reward from ${rewardData.grantedByUsername}`);
            console.log(`Claimed and applied reward ${rewardDoc.id}`);
        }
    } catch (error) {
        console.error("Error checking or claiming rewards:", error);
    }
}

// ===== Initial Auth Check =====
let authInitialized = false;

const handleUserAuthenticated = async (user) => {
    try {
        // Initialize client-side security measures first
        initializeAppSecurity();

        // Initialize core features that persist across pages
        initPullToRefresh(main, async () => {
            await loadPage('home', user.uid, false);
        });

        // Don't apply background animation on main page - it's now only for auth pages

        // PRIORITY 1: Load the UI immediately to prevent blank screen
        const pageToLoad = manageInitialPageLoad(user.uid, loadPage);

        // PRIORITY 2: Handle user data setup in background (non-blocking)
        setupUserDataBackground(user, pageToLoad);

        // Global click handler for locked features
        main.addEventListener('click', (e) => {
            const lockedEl = e.target.closest('[data-locked="true"]');
            if (lockedEl) {
                e.preventDefault();
                e.stopPropagation();
                showModal({
                    message: `This feature is locked. Upgrade to access it.`,
                    confirmText: 'View Plans',
                    onConfirm: () => loadPage('subscriptions', user.uid)
                });
            }
        });

    } catch (error) {
        console.error('Authentication setup error:', error);
        // Even if setup fails, ensure user gets to homepage
        manageInitialPageLoad(user.uid, loadPage);
    }
};

// Background user data setup (non-blocking)
const setupUserDataBackground = async (user, pageToLoad) => {
    try {
        const userRef = doc(db, "users", user.uid);
        const snapshot = await getDoc(userRef);
        let userData = {};

        if (!snapshot.exists()) {
            const newUserData = {
                username: user.displayName || "User",
                email: user.email,
                tier: "Free Tier",
                tierExpiry: null,
                photoURL: null,
                notifications: true,
                autoRenew: false,
                createdAt: new Date().toISOString(),
                lastLogin: new Date().toISOString(),
                isNewUser: true
            };
            await setDoc(userRef, newUserData);
            userData = newUserData;
            // Non-blocking history logging
            addHistoryUnique(user.uid, "Signed up").catch(err => 
                console.error("Failed to log signup:", err)
            );
        } else {
            userData = snapshot.data();
            // Non-blocking updates
            updateDoc(userRef, { lastLogin: new Date().toISOString() }).catch(err => 
                console.error("Failed to update login time:", err)
            );
            addHistoryUnique(user.uid, "Logged in").catch(err => 
                console.error("Failed to log login:", err)
            );
        }

        // Set verified tier for UI
        verifiedTier = userData.tier || "Free Tier";

        // Non-blocking subscription check
        checkExpiredSubscription(user.uid, userData);

        // Start tier watchdog
        startTierWatchdog(user.uid);

        // Non-blocking rewards check
        checkForAndClaimRewards(user.uid).catch(err => {
            console.error("Failed to process rewards on startup:", err);
        });

        // Check if the user is new to start the welcome tour
        if (userData.isNewUser && pageToLoad === 'home') {
            setTimeout(() => startWelcomeTour(user.uid), 1000);
        }

    } catch (error) {
        console.error('Background user setup error:', error);
        // Set default tier if database fails
        verifiedTier = "Free Tier";
    }
};

// Non-blocking subscription expiry check
const checkExpiredSubscription = async (userId, userData) => {
    try {
        if (userData.tier !== 'Free Tier' && userData.tierExpiry) {
            const expiryDate = new Date(userData.tierExpiry);
            if (new Date() > expiryDate) {
                console.log(`User ${userId}'s subscription has expired. Downgrading.`);
                const userRef = doc(db, "users", userId);
                await updateDoc(userRef, {
                    tier: 'Free Tier',
                    tierExpiry: null,
                    autoRenew: false
                });
                verifiedTier = "Free Tier";
                addHistoryUnique(userId, "Subscription expired, reverted to Free Tier.").catch(err => 
                    console.error("Failed to log subscription expiry:", err)
                );
            }
        }
    } catch (error) {
        console.error('Subscription check error:', error);
    }
};

showLoader(); // Show loader immediately on script load

const unsubscribe = onAuthStateChanged(auth, async (user) => {
    if (user) {
        // User is signed in.
        if (!authInitialized) {
            authInitialized = true;
            await handleUserAuthenticated(user);
        }
    } else {
        // User is signed out.
        if (!authInitialized) {
            authInitialized = true;
            // If after the initial check, there's no user, redirect to login.
            window.location.href = 'Auth/login.html';
        }
    }
});

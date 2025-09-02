// main.js
import { auth, db, FLWPUBK, storage } from './env.js';
import { showLoader, hideLoader } from './Loader/loader.js';
import { formatTimestamp, addHistoryUnique } from './utils.js';
import { onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
    ref, uploadBytes, getDownloadURL
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-storage.js";
import {
    doc, getDoc, setDoc, updateDoc,
    collection, addDoc, query, orderBy, getDocs, serverTimestamp, limit, deleteDoc, onSnapshot, arrayUnion
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

// ===== DOM Elements =====
const main = document.querySelector("main");
const navButtons = document.querySelectorAll(".bottom-nav button");
const defaultPage = "home";

initializeTheme(); // Apply theme on initial load

// ===== In-Memory Tier Verification =====
let verifiedTier = "Free Tier";

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

    modalMessage.textContent = config.message;
    confirmBtn.textContent = config.confirmText;
    confirmBtn.className = config.confirmClass;
    cancelBtn.textContent = config.cancelText;
    cancelBtn.className = 'btn-secondary';
    cancelBtn.style.display = config.showCancel ? 'inline-block' : 'none';
    modal.style.display = "flex";

    const cleanup = () => { modal.style.display = "none"; confirmBtn.onclick = null; cancelBtn.onclick = null; };
    confirmBtn.onclick = () => { cleanup(); config.onConfirm(); };
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

// ===== Subscription & Trial Functions =====
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
    if (amount === 0 || amount === "0") {
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
        callback: async function (data) {
            if (data.status === "successful") {
                await updateUserTier(userId, tier, period);
                showModal({ message: `Payment successful! You are now subscribed to ${tier}` });
                await addHistoryUnique(userId, `Subscribed to ${tier} (${period})`);

                // Add transaction to subscription history
                const subscriptionRef = doc(db, "subscriptions", userId);
                await updateDoc(subscriptionRef, {
                    transactions: arrayUnion({
                        amount: amount,
                        currency: "NGN",
                        description: `${tier} (${period})`,
                        status: data.status,
                        transactionId: data.transaction_id,
                        createdAt: serverTimestamp()
                    })
                });
            } else {
                showModal({ message: "Payment was not completed.", confirmClass: 'btn-danger' });
            }
        },
        onclose: function () { console.log("Payment modal closed"); }
    });
}

async function updateUserTier(userId, tier, period = null) {
    const userRef = doc(db, "users", userId);
    const updateData = { tier };

    if (period && tier !== 'Free Tier') {
        const expiry = new Date();
        if (period === 'daily') {
            expiry.setDate(expiry.getDate() + 1);
        } else if (period === 'monthly') {
            expiry.setMonth(expiry.getMonth() + 1);
        }
        updateData.tierExpiry = expiry.toISOString();
        updateData.autoRenew = true; // Set auto-renew on new subscription
    } else {
        updateData.tierExpiry = null;
        updateData.autoRenew = false; // Disable auto-renew for free tier/cancellation
    }

    await updateDoc(userRef, updateData);
    verifiedTier = tier;
    await updateCurrentTierDisplay(userId);
    enforceTierRestrictions();
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
            btn.onclick = async (e) => {
                e.preventDefault();
                const amount = parseFloat(btn.dataset.amount) || 0;
                const period = btn.dataset.period || "monthly";
                await handlePayment(userId, cardTier, amount, period);
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
    const toggleAutoRenewBtn = document.getElementById('toggleAutoRenewBtn');

    if (!planInfoCard || !changePlanBtn || !cancelContainer || !cancelBtn || !autoRenewContainer || !toggleAutoRenewBtn) return;

    const TIER_BENEFITS = {
        "Free Tier": ["Basic Access", "Limited Features", "Ads Supported"],
        "Premium Tier": ["Full Access", "No Ads", "Priority Support"],
        "VIP / Elite Tier": ["All Premium Features", "Exclusive Content", "VIP Support"],
        "VVIP / Pro Elite Tier": ["All VIP Features", "1-on-1 Coaching", "Early Access"]
    };

    showLoader();
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
            showModal({
                message: "Are you sure you want to cancel your subscription? This action is immediate and cannot be undone.",
                showCancel: true,
                confirmText: 'Yes, Cancel',
                confirmClass: 'btn-danger',
                onConfirm: async () => {
                    await updateUserTier(userId, 'Free Tier');
                    await addHistoryUnique(userId, "Subscription cancelled");
                    showModal({ message: "Your subscription has been successfully cancelled." });
                    await initManageSubscriptionPage(userId); // Refresh the page content
                }
            });
        };
    } catch (error) {
        console.error("Failed to load subscription management page:", error);
        planInfoCard.innerHTML = `<h2>Error</h2><p>Could not load your subscription details. Please try again later.</p>`;
    } finally {
        hideLoader();
    }
}

function initSubscriptionTabs() {
    const tabButtons = document.querySelectorAll(".tab-btn");
    const containers = document.querySelectorAll(".pricing-container");
    if (!tabButtons.length || !containers.length) return;

    tabButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            tabButtons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");

            containers.forEach(c => c.classList.remove("active"));
            const targetId = btn.dataset.tab;
            const target = document.getElementById(targetId);
            if (target) target.classList.add("active");
        });
    });
}

// ===== Tier Restrictions & Watchdog =====
function enforceTierRestrictions() {
    document.querySelectorAll("[data-tier]").forEach(el => {
        const requiredTier = el.dataset.tier;
        if (!requiredTier) return;

        const requiredTierName = CLASS_TO_TIER[requiredTier] || requiredTier;
        if (TIER_ORDER.indexOf(verifiedTier) < TIER_ORDER.indexOf(requiredTierName)) {
            el.style.opacity = "0.8";
            el.dataset.locked = "true";
            el.setAttribute("title", `Requires ${requiredTierName} subscription`);
        } else {
            el.style.opacity = "1";
            el.dataset.locked = "false";
            el.removeAttribute("title");
        }
    });
}

function startTierWatchdog(userId) {
    if (!userId) return;
    const userRef = doc(db, "users", userId);
    onSnapshot(userRef, async (snapshot) => {
        if (!snapshot.exists()) return;
        const currentTier = snapshot.data().tier;
        if (TIER_ORDER.indexOf(currentTier) > TIER_ORDER.indexOf(verifiedTier)) {
            await updateDoc(userRef, { tier: verifiedTier });
            await addHistoryUnique(userId, `Unauthorized tier correction (watchdog)`);
        } else {
            verifiedTier = currentTier;
        }
        enforceTierRestrictions();
    });
}

// ===== Profile Functions =====
/**
 * Initializes all interactive elements on the profile page.
 * @param {string} userId - The current user's ID.
 */
async function initProfilePage(userId) {
    if (!userId) return;

    const userRef = doc(db, "users", userId);
    const snapshot = await getDoc(userRef);
    const userData = snapshot.exists() ? snapshot.data() : {};

    // 1. Avatar and User Info
    const avatarContainer = document.getElementById('profileAvatarContainer');
    const avatarUploadInput = document.getElementById('avatarUpload');
    const userNameEl = document.getElementById('userName');
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

            showLoader();
            try {
                const storageRef = ref(storage, `profile_pictures/${userId}`);
                const uploadResult = await uploadBytes(storageRef, file);
                const downloadURL = await getDownloadURL(uploadResult.ref);

                await updateDoc(userRef, { photoURL: downloadURL });
                displayAvatar(downloadURL, userData.username);
                await addHistoryUnique(userId, 'Updated profile picture');
            } catch (error) {
                console.error("Avatar upload failed:", error);
                showModal({ message: 'Failed to upload image. Please try again.', confirmClass: 'btn-danger' });
            } finally {
                hideLoader();
            }
        });
    }

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

    // 3. Notification Toggle
    const notifToggle = document.getElementById("notificationToggle");
    if (notifToggle) {
        notifToggle.checked = userData.notifications ?? true; // Default to true if undefined
        notifToggle.addEventListener("change", async () => {
            await updateDoc(userRef, { notifications: notifToggle.checked });
        });
    }

    // 4. Manage Subscription Button
    const manageBtn = document.getElementById("manageSubscription");
    if (manageBtn) manageBtn.onclick = (e) => {
        e.preventDefault(); // Prevent default link behavior
        loadPage("manage-subscription", userId);
    };

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
            localStorage.clear();
            window.location.href = './Auth/login.html';
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
}

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
    const pullThreshold = 85; // Pixels to pull before refresh triggers

    // Create or find the refresh indicator in the BODY
    let refreshIndicator = document.getElementById('refresh-indicator');
    if (!refreshIndicator) {
        refreshIndicator = document.createElement('div');
        refreshIndicator.id = 'refresh-indicator';
        refreshIndicator.innerHTML = `<span>&#x21bb;</span>`; // Refresh icon
        document.body.appendChild(refreshIndicator);
    }

    const resetIndicator = () => {
        refreshIndicator.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
        refreshIndicator.style.transform = 'translateX(-50%) scale(0)';
        refreshIndicator.style.opacity = '0';
        refreshIndicator.classList.remove('refreshing');
        // Remove transition after animation so next pull is instant
        setTimeout(() => {
            if (refreshIndicator) refreshIndicator.style.transition = '';
        }, 300);
    };

    container.addEventListener('touchstart', (e) => {
        if (container.dataset.pullToRefreshActive !== 'true' || container.scrollTop !== 0) {
            isDragging = false;
            return;
        }
        isDragging = true;
        startY = e.touches[0].pageY;
        refreshIndicator.style.transition = ''; // Remove transition for direct manipulation
    }, { passive: true });

    container.addEventListener('touchmove', (e) => {
        if (!isDragging) return;

        const currentY = e.touches[0].pageY;
        const pullDistance = currentY - startY;

        if (pullDistance > 0) {
            // Prevent the browser's overscroll-bounce effect on mobile
            e.preventDefault();
            
            const pullRatio = Math.min(pullDistance / pullThreshold, 1);
            refreshIndicator.style.opacity = pullRatio;
            // Rotate the icon as you pull
            refreshIndicator.style.transform = `translateX(-50%) scale(${pullRatio}) rotate(${pullDistance * 2.5}deg)`;
        } else {
            // If user starts scrolling up, stop the pull-to-refresh gesture
            isDragging = false;
            startY = 0;
        }
    }, { passive: false }); // passive:false is needed for preventDefault()

    container.addEventListener('touchend', async (e) => {
        if (!isDragging) return;
        isDragging = false;

        const pullDistance = e.changedTouches[0].pageY - startY;
        startY = 0;

        if (pullDistance > pullThreshold) {
            // User pulled enough, trigger refresh
            refreshIndicator.style.transition = 'transform 0.2s ease';
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

    // Animate the current content out
    main.classList.add('page-fade-out');
    await new Promise(resolve => setTimeout(resolve, 200)); // Match animation duration

    try {
        const response = await fetch(`Pages/${page}.html`);
        if (!response.ok) throw new Error(`Page not found: ${page}`);
        const html = await response.text();
        const parser = new DOMParser();
        const docu = parser.parseFromString(html, "text/html");
        const pageMain = docu.querySelector("main") || docu.body || docu;
        main.innerHTML = pageMain.innerHTML;
        main.dataset.pullToRefreshActive = 'false'; // Deactivate for all pages by default

        clearDynamicAssets();

        docu.querySelectorAll("link[rel='stylesheet']").forEach(link => {
            const newLink = document.createElement("link");
            newLink.rel = "stylesheet";
            newLink.href = link.href;
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
            if (script.src) newScript.src = script.src;
            else newScript.textContent = script.textContent;
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
            initSubscriptionTabs();
        }
        if (page === "manage-subscription") {
            await initManageSubscriptionPage(userId);
        }
        if (page === "profile") {
            await initProfilePage(userId);
        }
        if (page === "history") {
            await fetchHistory(userId);
        }
        if (page === "home") {
            main.dataset.pullToRefreshActive = 'true'; // Activate for home page

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

                    // 2. FILTER CARDS
                    cards.forEach(card => {
                        const title = card.querySelector(".match-title")?.textContent.toLowerCase() || '';
                        const confidence = parseInt(card.querySelector('.confidence span')?.textContent.match(/\d+/)?.[0] || '0', 10);

                        const textMatch = !textQuery || title.includes(textQuery);
                        const confidenceMatch = !confidenceFilterCommand || confidence >= minConfidence;

                        const shouldShow = textMatch && confidenceMatch;
                        card.style.display = shouldShow ? "block" : "none";
                        if (shouldShow) visibleCount++;
                    });

                    // 3. SORT VISIBLE CARDS
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
                        // 4. RESTORE ORIGINAL ORDER IF SEARCH IS EMPTY
                        originalCardElements.forEach(card => predictionsContainer.appendChild(card));
                    }

                    // 5. UPDATE UI (No Results Message & Autocomplete)
                    noResultsEl.style.display = visibleCount === 0 && value.trim() !== '' ? 'block' : 'none';

                    const ghostEl = document.getElementById('search-ghost-text');
                    if (ghostEl) {
                        let suggestion = '';
                        // Match Title Autocomplete
                        if (textQuery) {
                            const allTitles = originalCardElements.map(card => card.querySelector('.match-title')?.textContent || '');
                            const matchedTitle = allTitles.find(title => title.toLowerCase().startsWith(textQuery));
                            if (matchedTitle) {
                                const textQueryStartIndex = lowerCaseValue.lastIndexOf(textQuery);
                                if (textQueryStartIndex !== -1) {
                                    suggestion = value.substring(0, textQueryStartIndex) + matchedTitle;
                                }
                            }
                        }
                        // Command Autocomplete (only if no text is being typed)
                        else if (!lowerCaseValue.includes(' ')) {
                            if (lowerCaseValue === '/') {
                                suggestion = '/odds';
                            } else if ('/odds'.startsWith(lowerCaseValue) && lowerCaseValue.length > 0 && lowerCaseValue !== '/odds') {
                                suggestion = '/odds';
                            } else if (lowerCaseValue === '/c') {
                                suggestion = '/c75';
                            }
                        }
                        ghostEl.textContent = suggestion;
                    }
                });
            }
        }

        enforceTierRestrictions();

        // Reset scroll position for the new page
        main.scrollTop = 0;

    } catch (error) {
        console.error(error);
        main.innerHTML = `<p>Sorry, failed to load ${page}.</p>`;
    } finally {
        // Animate the new content in
        main.classList.remove('page-fade-out');
        main.classList.add('page-fade-in');
        main.addEventListener('animationend', () => {
            main.classList.remove('page-fade-in');
            main.classList.remove('page-transitioning');
        }, { once: true });
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

// ===== Initial Auth Check =====
onAuthStateChanged(auth, async (user) => {
    if (!user) {
        window.location.href = './Auth/login.html';
    } else {
        const userRef = doc(db, "users", user.uid);
        const snapshot = await getDoc(userRef);

        if (!snapshot.exists()) {
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
            await addHistoryUnique(user.uid, "Signed up");
        } else {
            await updateDoc(userRef, { lastLogin: new Date().toISOString() });
            await addHistoryUnique(user.uid, "Logged in");
        }

        // Initialize core features that persist across pages
        initPullToRefresh(main, async () => {
            await loadPage('home', user.uid, false);
        });

        // Global click handler for locked features and dynamic tabs using event delegation
        main.addEventListener('click', (e) => {
            // 1. Locked features
            const lockedEl = e.target.closest('[data-locked="true"]');
            if (lockedEl) {
                e.preventDefault();
                e.stopPropagation();
                showModal({
                    message: `This feature is locked. Upgrade to access it.`,
                    confirmText: 'Upgrade',
                    onConfirm: () => loadPage('subscriptions', auth.currentUser?.uid)
                });
                return; // Stop further processing
            }

            // 2. History page tabs
            const historyTabButton = e.target.closest('.history-section .tab-btn');
            if (historyTabButton && !historyTabButton.classList.contains('active')) {
                const historySection = historyTabButton.closest('.history-section');
                if (!historySection) return;

                const tabButtons = historySection.querySelectorAll(".tab-btn");
                const tabContents = historySection.querySelectorAll(".tab-content");

                tabButtons.forEach(b => b.classList.remove("active"));
                historyTabButton.classList.add("active");

                tabContents.forEach(c => c.classList.remove("active"));
                const targetId = historyTabButton.dataset.tab;
                const target = historySection.querySelector(`#${targetId}`);
                if (target) target.classList.add("active");
            }
        });

        const userData = snapshot.exists() ? snapshot.data() : {};

        // Check for expired subscription
        if (userData.tierExpiry && new Date(userData.tierExpiry) < new Date() && userData.tier !== 'Free Tier') {
            await updateUserTier(user.uid, 'Free Tier');
            await addHistoryUnique(user.uid, `Subscription expired, reverted to Free Tier.`);
            verifiedTier = 'Free Tier';
        } else {
            verifiedTier = userData.tier || "Free Tier";
        }

        startTierWatchdog(user.uid);

        const lastPage = localStorage.getItem("lastPage") || defaultPage;
        loadPage(lastPage, user.uid);
    }
});

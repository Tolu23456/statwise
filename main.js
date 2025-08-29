// main.js
import { auth, db, FLWPUBK } from './env.js';
import { showLoader, hideLoader } from './Loader/loader.js';
import { onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";
import {
    doc, getDoc, setDoc, updateDoc,
    collection, addDoc, query, orderBy, getDocs, serverTimestamp, limit, deleteDoc, onSnapshot
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-firestore.js";

// ===== DOM Elements =====
const main = document.querySelector("main");
const navButtons = document.querySelectorAll(".bottom-nav button");
const defaultPage = "home";

// ===== In-Memory Tier Verification =====
let verifiedTier = "Free Tier";

// ===== Helper: Clear dynamic assets =====
function clearDynamicAssets() {
    document.querySelectorAll("script[data-dynamic], link[data-dynamic], style[data-dynamic]").forEach(el => el.remove());
}

// ===== Modal System =====
function showModal(message, confirmCallback, cancelCallback = null) {
    let modal = document.getElementById("customModal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "customModal";
        modal.className = "modal";
        modal.innerHTML = `
            <div class="modal-content">
                <p id="modalMessage"></p>
                <div class="modal-actions">
                    <button id="modalCancel">Cancel</button>
                    <button id="modalConfirm">OK</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
    }

    const modalMessage = modal.querySelector("#modalMessage");
    const confirmBtn = modal.querySelector("#modalConfirm");
    const cancelBtn = modal.querySelector("#modalCancel");

    modalMessage.textContent = message;
    modal.style.display = "flex";

    const cleanup = () => {
        modal.style.display = "none";
        confirmBtn.onclick = null;
        cancelBtn.onclick = null;
    };

    confirmBtn.onclick = () => { cleanup(); confirmCallback(); };
    cancelBtn.onclick = () => { cleanup(); if (cancelCallback) cancelCallback(); };
}

// ===== Theme Functions =====
function applyTheme(isDark) {
    document.body.classList.toggle("dark-mode", isDark);
}

async function initThemeToggle(userId) {
    const darkToggle = document.getElementById("darkModeToggle");
    if (!darkToggle || !userId) return;

    const userRef = doc(db, "users", userId);
    const snapshot = await getDoc(userRef);
    const darkModeEnabled = snapshot.exists() ? snapshot.data().darkMode : false;

    darkToggle.checked = darkModeEnabled;
    applyTheme(darkModeEnabled);

    darkToggle.addEventListener("change", async () => {
        const isDark = darkToggle.checked;
        applyTheme(isDark);
        await updateDoc(userRef, { darkMode: isDark });
    });
}

// ===== Notification Toggle =====
async function initNotificationToggle(userId) {
    const notifToggle = document.getElementById("notificationToggle");
    if (!notifToggle || !userId) return;

    const userRef = doc(db, "users", userId);
    const snapshot = await getDoc(userRef);
    const notificationsEnabled = snapshot.exists() ? snapshot.data().notifications : true;

    notifToggle.checked = notificationsEnabled;

    notifToggle.addEventListener("change", async () => {
        await updateDoc(userRef, { notifications: notifToggle.checked });
    });
}

// ===== Subscription & Trial Functions =====
const CLASS_TO_TIER = { free: "Free Tier", premium: "Premium Tier", vip: "VIP / Elite Tier", vvip: "VVIP / Pro Elite Tier" };
const TIER_ORDER = ["Free Tier", "Premium Tier", "VIP / Elite Tier", "VVIP / Pro Elite Tier"];

async function updateCurrentTierDisplay(userId) {
    const tierDisplay = document.getElementById("user-tier");
    if (!tierDisplay || !userId) return;

    const snapshot = await getDoc(doc(db, "users", userId));
    const tier = snapshot.exists() ? snapshot.data().tier : "Free Tier";
    tierDisplay.textContent = tier;
    verifiedTier = tier; // update memory
    enforceTierRestrictions();
}

// Flutterwave payment + trial/free handling
async function handlePayment(userId, tier, amount, period) {
    if (amount === 0 || amount === "0") {
        await updateUserTier(userId, tier);
        showModal(`You have selected the ${tier}`, () => { });
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
                await updateUserTier(userId, tier);
                showModal(`Payment successful! You are now subscribed to ${tier}`, () => { });
                await addHistoryUnique(userId, `Subscribed to ${tier} (${period})`);
            } else {
                showModal("Payment was not completed.", () => { });
            }
        },
        onclose: function () { console.log("Payment modal closed"); }
    });
}

async function updateUserTier(userId, tier) {
    const userRef = doc(db, "users", userId);
    await updateDoc(userRef, { tier });
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

        // Always fully visible
        card.style.opacity = "1";
        btn.style.display = "inline-block";
        btn.disabled = false;

        btn.onclick = async (e) => {
            e.preventDefault();
            if (cardRank <= currentRank) {
                showModal(
                    `You already have this tier or higher. Upgrade to higher tier instead.`,
                    () => attachSubscriptionButtons(userId)
                );
                return;
            }
            const amount = parseFloat(btn.dataset.amount) || 0;
            const period = btn.dataset.period || "monthly";
            await handlePayment(userId, cardTier, amount, period);
        };
    });
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

    document.querySelectorAll("[data-tier]").forEach(el => {
        el.onclick = () => {
            if (el.dataset.locked === "true") {
                showModal(
                    `This feature is locked. Upgrade to access it.`,
                    () => attachSubscriptionButtons(auth.currentUser?.uid)
                );
            }
        };
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
function attachResetButton() {
    const resetBtn = document.getElementById("reset-storage");
    if (!resetBtn) return;

    resetBtn.onclick = () => {
        showModal("Are you sure you want to reset this device’s cached data?", () => {
            localStorage.clear();
            location.reload();
        });
    };
}

function attachLogoutButton(userId) {
    const logoutBtn = document.getElementById("logoutBtn");
    if (!logoutBtn) return;

    logoutBtn.onclick = () => {
        showModal("Are you sure you want to logout?", async () => {
            await signOut(auth);
            await addHistoryUnique(userId, "Logged out");
            localStorage.clear();
            window.location.href = './Auth/login.html';
        });
    };
}

async function fillProfileInfo(userId) {
    const emailEl = document.getElementById("userEmail");
    const nameEl = document.getElementById("userName");

    if (!emailEl && !nameEl) return;

    const snapshot = await getDoc(doc(db, "users", userId));
    if (snapshot.exists()) {
        const data = snapshot.data();
        if (emailEl) emailEl.textContent = data.email || auth.currentUser?.email;
        if (nameEl) nameEl.textContent = data.username || "User";
    }
}

async function initProfile(userId) {
    // Fill user info
    fillProfileInfo(userId);

    // Initialize toggles
    initThemeToggle(userId);
    initNotificationToggle(userId);

    // Attach buttons
    attachResetButton();
    attachLogoutButton(userId);

    // Manage Subscription button
    const manageBtn = document.getElementById("manageSubscription");
    if (manageBtn) {
        manageBtn.onclick = () => {
            loadPage("subscriptions", auth.currentUser?.uid);
        };
    }
}


// ===== History Functions =====
async function getPublicIP() {
    try {
        const res = await fetch("https://api.ipify.org?format=json");
        const data = await res.json();
        return data.ip || "Unknown";
    } catch {
        return "Unknown";
    }
}

async function addHistoryUnique(userId, action) {
    if (!userId) return;
    try {
        const historyRef = collection(db, "users", userId, "history");
        const q = query(historyRef, orderBy("createdAt", "desc"), limit(1));
        const snap = await getDocs(q);
        if (!snap.empty && snap.docs[0].data().action === action) return;

        const ip = await getPublicIP();
        await addDoc(historyRef, {
            action,
            ip,
            createdAt: serverTimestamp()
        });
    } catch (err) {
        console.error("Failed to add history:", err);
    }
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

// ===== Utility =====
function formatTimestamp(timestamp) {
    if (!timestamp?.toDate) return "";
    const date = timestamp.toDate();
    const day = String(date.getDate()).padStart(2, "0");
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const year = date.getFullYear();
    const hours = String(date.getHours()).padStart(2, "0");
    const mins = String(date.getMinutes()).padStart(2, "0");
    return `${day}/${month}/${year} ${hours}:${mins}`;
}

// ===== Fetch History =====
async function fetchHistory(userId) {
    if (!userId) return;
    showLoader();

    const predictionsContainer = document.querySelector("#predictions-tab .history-container");
    const accountContainer = document.querySelector("#account-tab .history-container");
    if (!predictionsContainer || !accountContainer) return;

    await cleanupOldPredictions(userId);

    try {
        const historyRef = collection(db, "users", userId, "history");
        const q = query(historyRef, orderBy("createdAt", "desc"));
        const snapshot = await getDocs(q);

        predictionsContainer.innerHTML = "";
        accountContainer.innerHTML = "";

        if (snapshot.empty) {
            predictionsContainer.innerHTML = "<p>No predictions yet.</p>";
            accountContainer.innerHTML = "<p>No account activity yet.</p>";
            return;
        }

        snapshot.forEach(docu => {
            const data = docu.data();
            const card = document.createElement("div");
            card.className = "history-card";

            if (data.match) {
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
                card.innerHTML = `
                    <p><strong>Action:</strong> ${data.action}</p>
                    <p><strong>IP:</strong> ${data.ip || "Unknown"}</p>
                    <p><small>${formatTimestamp(data.createdAt)}</small></p>
                `;
                accountContainer.appendChild(card);
            }
        });
    } catch (err) {
        console.error("Failed to fetch history:", err);
    } finally {
        hideLoader();
    }
}

// ===== Dynamic Page Loader =====
async function loadPage(page, userId, addToHistory = true) {
    try {
        showLoader();
        const response = await fetch(`Pages/${page}.html`);
        if (!response.ok) throw new Error(`Page not found: ${page}`);
        const html = await response.text();
        const parser = new DOMParser();
        const docu = parser.parseFromString(html, "text/html");
        const pageMain = docu.querySelector("main") || docu.body || docu;
        main.innerHTML = pageMain.innerHTML;

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
        if (page === "profile") {
            initProfile(userId);
        }
        if (page === "history") {
            const historySection = document.querySelector(".history-section");
            if (historySection) {
                const tabButtons = historySection.querySelectorAll(".tab-btn");
                const tabContents = historySection.querySelectorAll(".tab-content");
                tabButtons.forEach(btn => {
                    btn.addEventListener("click", () => {
                        tabButtons.forEach(b => b.classList.remove("active"));
                        btn.classList.add("active");
                        tabContents.forEach(c => c.classList.remove("active"));
                        const targetId = btn.dataset.tab;
                        const target = historySection.querySelector(`#${targetId}`);
                        if (target) target.classList.add("active");
                    });
                });
            }

            await fetchHistory(userId);
        }

        enforceTierRestrictions();

    } catch (error) {
        console.error(error);
        main.innerHTML = `<p>Sorry, failed to load ${page}.</p>`;
    } finally {
        hideLoader();
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
                darkMode: false,
                notifications: true,
                createdAt: new Date().toISOString(),
                lastLogin: new Date().toISOString()
            });
            await addHistoryUnique(user.uid, "Signed up");
        } else {
            await updateDoc(userRef, { lastLogin: new Date().toISOString() });
            await addHistoryUnique(user.uid, "Logged in");
        }

        verifiedTier = snapshot.exists() ? snapshot.data().tier : "Free Tier";

        startTierWatchdog(user.uid);

        const lastPage = localStorage.getItem("lastPage") || defaultPage;
        loadPage(lastPage, user.uid);
    }
});

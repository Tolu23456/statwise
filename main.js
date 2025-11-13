// main.js - StatWise PWA with Supabase-only implementation
console.log('main.js loaded');
import { supabase, FLWPUBK } from './env.js';
import { showLoader, hideLoader } from './Loader/loader.js';
import { initializeTheme } from './ui.js';
import { initializeAppSecurity } from './manager.js';
import { showModal } from './utils.js';

// ===== Global Variables =====
const main = document.querySelector("main");
const navButtons = document.querySelectorAll(".bottom-nav button");
const defaultPage = "home";
let currentUser = null;
let verifiedTier = null; // Start as null until profile loads
let adsLoaded = false;


// Initialize the app
initializeTheme(); // Initialize theme system

// Guard clause to prevent app from running with placeholder credentials
if (typeof supabase === 'undefined' || supabase.supabaseUrl.includes('YOUR_SUPABASE_URL') || supabase.supabaseKey.includes('YOUR_SUPABASE_ANON_KEY')) {
    document.querySelector('main').innerHTML = `
        <div class="error-container">
            <h1>Configuration Error</h1>
            <p>The application is not configured correctly. Please copy <code>env.example.js</code> to <code>env.js</code> and fill in your Supabase credentials.</p>
        </div>
    `;
    console.error('Supabase client not initialized. Halting app execution.');
} else {
    initializeSupabaseAuth();
    checkPaymentRedirect();
    // Ad system will be initialized after user tier is loaded
}

// ===== Authentication Setup =====
async function initializeSupabaseAuth() {
    // Get initial session
    const { data: { session } } = await supabase.auth.getSession();

    if (session) {
        currentUser = session.user;
        await handleUserLogin(session.user);
    } else {
        // Allow access to subscription page without authentication
        const currentPage = localStorage.getItem('lastPage') || 'home';
        if (currentPage === 'subscriptions') {
            await loadPage('subscriptions');
        } else {
            redirectToLogin();
        }
    }

    // Listen for auth changes
    supabase.auth.onAuthStateChange(async (event, session) => {
        console.log('Auth state changed:', event);

        if (event === 'SIGNED_IN' && session) {
            currentUser = session.user;
            await handleUserLogin(session.user);
        } else if (event === 'SIGNED_OUT') {
            currentUser = null;
            redirectToLogin();
        }
    });
}

// ===== User Management =====
async function handleUserLogin(user) {
    try {
        showLoader();
        console.log('User logged in:', user.email);

        // Check if email is verified
        if (!user.email_confirmed_at) {
            console.log('Email not verified, showing verification notice');
            hideLoader();
            showEmailVerificationNotice(user.email);
            return;
        }

        // Create or update user profile
        await createOrUpdateUserProfile(user);

        // Load user data and initialize app
        await loadUserData(user);

        // Initialize the main application
        initializeApp();

        hideLoader();
    } catch (error) {
        console.error('Error handling user login:', error);
        hideLoader();
    }
}

function showEmailVerificationNotice(email) {
    const main = document.querySelector('main');
    if (!main) return;

    main.innerHTML = `
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 60vh; padding: 20px; text-align: center;">
            <div style="background: var(--card-bg, #fff); padding: 40px; border-radius: 16px; max-width: 500px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                <div style="font-size: 64px; margin-bottom: 20px;">📧</div>
                <h2 style="color: var(--text-primary, #333); margin-bottom: 16px;">Verify Your Email</h2>
                <p style="color: var(--text-secondary, #666); margin-bottom: 24px; line-height: 1.6;">
                    We sent a verification link to<br>
                    <strong style="color: var(--primary-color, #0e639c);">${email}</strong>
                </p>
                <p style="color: var(--text-secondary, #666); margin-bottom: 32px; line-height: 1.6;">
                    Please check your inbox and click the verification link to continue.
                </p>
                <div style="display: flex; flex-direction: column; gap: 12px;">
                    <button onclick="window.location.reload()" style="background: var(--primary-color, #0e639c); color: white; border: none; padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 16px; font-weight: 500;">
                        I've Verified My Email
                    </button>
                    <button onclick="signOut()" style="background: transparent; color: var(--text-secondary, #666); border: 1px solid var(--border-color, #ddd); padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 16px;">
                        Sign Out
                    </button>
                </div>
                <p style="color: var(--text-tertiary, #999); margin-top: 24px; font-size: 14px;">
                    Didn't receive the email? Check your spam folder or contact support.
                </p>
            </div>
        </div>
    `;
}

async function createOrUpdateUserProfile(user) {
    try {
        const userData = {
            id: user.id,
            email: user.email,
            username: user.user_metadata?.display_name || user.email.split('@')[0],
            display_name: user.user_metadata?.display_name || user.email.split('@')[0],
            current_tier: 'Free Tier',
            tier: 'Free Tier',
            subscription_status: 'active',
            is_new_user: true,
            notifications: true,
            last_login: new Date().toISOString(),
            updated_at: new Date().toISOString()
        };

        const { data, error } = await supabase
            .from('user_profiles')
            .upsert(userData, { onConflict: 'id' })
            .select()
            .single();

        if (error && error.code !== '23505') { // Ignore unique constraint violations
            console.warn('Profile creation warning:', error);
        } else {
            console.log('User profile created/updated:', data);
        }

        // Generate referral code if not exists
        await generateReferralCode(user.id);

    } catch (error) {
        console.warn('Error creating user profile:', error);
    }
}

async function loadUserData(user) {
    try {
        // Load user profile
        const { data: profile, error } = await supabase
            .from('user_profiles')
            .select('*')
            .eq('id', user.id)
            .single();

        if (profile) {
            verifiedTier = profile.current_tier || 'Free Tier';
            console.log('User tier loaded:', verifiedTier);

            // Initialize ad system now that we know the user's tier
            initializeAdSystemForUser();
        } else {
            // Default to free tier if no profile found
            verifiedTier = 'Free Tier';
            initializeAdSystemForUser();
        }
    } catch (error) {
        console.warn('Error loading user data:', error);
    }
}

// ===== Referral System =====
async function generateReferralCode(userId) {
    try {
        const code = userId.substring(0, 8).toUpperCase();

        const { data, error } = await supabase
            .from('referral_codes')
            .upsert({
                user_id: userId,
                code: code,
                username: currentUser?.email?.split('@')[0] || 'User',
                total_referrals: 0,
                active: true,
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString()
            }, { onConflict: 'user_id' })
            .select()
            .single();

        if (error && error.code !== '23505') {
            console.warn('Referral code generation warning:', error);
        } else {
            console.log('Referral code generated:', data?.code);
        }
    } catch (error) {
        console.warn('Error generating referral code:', error);
    }
}

// ===== Payment System =====
function checkPaymentRedirect() {
    const urlParams = new URLSearchParams(window.location.search);
    const paymentStatus = urlParams.get('payment');
    const transactionId = urlParams.get('transaction_id');

    if (paymentStatus === 'success' && transactionId) {
        setTimeout(() => {
            showModal({
                message: `🎉 Welcome back!\n\nYour payment has been processed successfully.\nTransaction ID: ${transactionId}\n\nPlease wait while we verify your subscription...`,
                confirmClass: 'btn-success',
                confirmText: 'Continue',
                onConfirm: () => {
                    loadPage('subscriptions');
                }
            });
        }, 1000);

        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (paymentStatus === 'cancelled') {
        setTimeout(() => {
            showModal({
                message: '❌ Payment was cancelled.\n\nYour subscription has not been updated. You can try again anytime.',
                confirmClass: 'btn-warning',
                confirmText: 'OK'
            });
        }, 1000);

        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

// ===== App Navigation =====
function initializeApp() {
    // Initialize other app features first
    initializeAppSecurity();

    // Set up navigation
    navButtons.forEach(button => {
        button.addEventListener("click", () => {
            const page = button.getAttribute("data-page");
            const tier = button.getAttribute("data-tier");

            if (tier && !hasAccess(tier)) {
                showUpgradeModal(tier);
                return;
            }

            loadPage(page);
        });
    });

    // Load the correct initial page (checks localStorage for last page)
    loadInitialPage();
}

function loadInitialPage() {
    // Determine page load priority: URL hash > localStorage > default
    const initialHash = window.location.hash.substring(1);
    const lastPage = localStorage.getItem("lastPage");
    const pageToLoad = initialHash || lastPage || defaultPage;

    console.log('Loading initial page:', pageToLoad, 'from:', initialHash ? 'hash' : lastPage ? 'localStorage' : 'default');
    loadPage(pageToLoad);
}

function hasAccess(requiredTier) {
    const tierLevels = {
        'Free Tier': 0,
        'Premium Tier': 1,
        'VIP Tier': 2,
        'VVIP Tier': 3
    };

    const currentLevel = tierLevels[verifiedTier] || 0;
    const requiredLevel = tierLevels[requiredTier] || 0;

    return currentLevel >= requiredLevel;
}

function showUpgradeModal(requiredTier) {
    showModal({
        message: `This feature requires ${requiredTier} subscription.\n\nWould you like to upgrade now?`,
        confirmText: 'Upgrade',
        cancelText: 'Cancel',
        onConfirm: () => {
            loadPage('subscriptions');
        }
    });
}

async function loadPage(page) {
    try {
        // Validate page parameter
        if (!page || typeof page !== 'string') {
            console.error('Invalid page parameter:', page);
            return;
        }

        showLoader();

        // Save current page to localStorage for reload persistence
        try {
            localStorage.setItem('lastPage', page);
        } catch (storageError) {
            console.warn('Failed to save page to localStorage:', storageError);
        }

        // Update active navigation with null checks
        if (navButtons && navButtons.length > 0) {
            navButtons.forEach(btn => {
                if (btn && btn.getAttribute) {
                    btn.classList.toggle("active", btn.getAttribute("data-page") === page);
                }
            });
        }

        // Add fade-out transition to current content
        if (main) {
            main.classList.add('page-fade-out');

            // Wait for fade-out animation to complete
            await new Promise(resolve => setTimeout(resolve, 200));

            // Load page content
            const pageUrl = `./Pages/${page}.html`;
            console.log('[loadPage] fetching page URL:', pageUrl);
            const response = await fetch(pageUrl);
            console.log('[loadPage] fetch response status:', response.status, response.statusText);
            if (response.ok) {
                const content = await response.text();
                main.innerHTML = content;

                // Reset scroll position to top for each new page - force immediate reset
                main.scrollTop = 0;
                document.documentElement.scrollTop = 0;
                document.body.scrollTop = 0;

                // Force a layout recalculation to ensure scroll reset
                main.offsetHeight;

                // Remove fade-out and add fade-in transition
                main.classList.remove('page-fade-out');
                main.classList.add('page-fade-in');

                // Initialize page-specific functionality
                try {
                    await initializePage(page);
                } catch (initError) {
                    console.error('Error initializing page:', initError);
                }

                // Remove fade-in class after animation completes
                setTimeout(() => {
                    if (main) {
                        main.classList.remove('page-fade-in');
                    }
                }, 300);

                // Force style recalculation to ensure CSS is applied
                if (main) {
                    main.offsetHeight; // Trigger reflow
                    window.getComputedStyle(main).opacity; // Force style recalculation
                }
            } else {
                main.innerHTML = '<div class="error">Page not found</div>';
                main.scrollTop = 0;
                document.documentElement.scrollTop = 0;
                document.body.scrollTop = 0;
                main.offsetHeight;
                main.classList.remove('page-fade-out');
            }
        }

        hideLoader();
    } catch (error) {
        console.error('Error loading page:', error);
        if (main) {
            main.innerHTML = '<div class="error">Error loading page</div>';
            main.scrollTop = 0;
            document.documentElement.scrollTop = 0;
            document.body.scrollTop = 0;
            main.offsetHeight;
            main.classList.remove('page-fade-out');
        }
        hideLoader();
    }
}

async function initializePage(page) {
    switch (page) {
        case 'home':
            const { initializeHomePage } = await import('./js/pages/home.js');
            await initializeHomePage(verifiedTier);
            break;
        case 'profile':
            const { initializeProfilePage } = await import('./js/pages/profile.js');
            await initializeProfilePage(currentUser);
            break;
        case 'subscriptions':
            const { initializeSubscriptionsPage } = await import('./js/pages/subscriptions.js');
            await initializeSubscriptionsPage(currentUser);
            break;
        case 'manage-subscription':
            const { initializeManageSubscriptionPage } = await import('./js/pages/subscriptions.js');
            await initializeManageSubscriptionPage(currentUser);
            break;
        case 'referral':
            const { initializeReferralPage } = await import('./js/pages/referral.js');
            await initializeReferralPage(currentUser);
            break;
        case 'insights':
            const { initializeInsightsPage } = await import('./js/pages/insights.js');
            await initializeInsightsPage(verifiedTier, showUpgradeModal);
            break;
        case 'forum':
            const { initializeForumPage } = await import('./js/pages/forum.js');
            await initializeForumPage(currentUser);
            break;
    }
}

// ===== Global Functions =====
window.savePrediction = async function(predictionId) {
    if (!currentUser) {
        showModal({ message: 'Please log in to save predictions.', confirmText: 'OK' });
        return;
    }

    if (!predictionId) {
        showModal({ message: 'Invalid prediction ID.', confirmText: 'OK' });
        return;
    }

    try {
        const { error } = await supabase
            .from('user_prediction_history')
            .insert({
                user_id: currentUser.id,
                prediction_id: predictionId,
                saved_at: new Date().toISOString()
            });

        if (error && error.code !== '23505') { // Ignore duplicate key errors
            console.warn('Error saving prediction:', error);
            showModal({ message: 'Error saving prediction. Please try again.', confirmText: 'OK' });
            return;
        }

        showModal({
            message: 'Prediction saved to your history!',
            confirmText: 'View History',
            cancelText: 'Continue',
            onConfirm: () => {
                try {
                    loadPage('history');
                } catch (error) {
                    console.error('Error loading history page:', error);
                }
            }
        });
    } catch (error) {
        console.error('Error saving prediction:', error);
        showModal({ message: 'Error saving prediction. Please try again.', confirmText: 'OK' });
    }
};

window.initializePayment = function(tier, period, amount) {
    if (!currentUser) {
        showModal({ message: 'Please log in to subscribe.' });
        return;
    }

    // Initialize Flutterwave payment
    FlutterwaveCheckout({
        public_key: FLWPUBK,
        tx_ref: `statwise_${currentUser.id}_${Date.now()}`,
        amount: amount,
        currency: "NGN",
        payment_options: "card,mobilemoney,ussd",
        customer: {
            email: currentUser.email,
            phone_number: "",
            name: currentUser.user_metadata?.display_name || currentUser.email
        },
        customizations: {
            title: "StatWise Subscription",
            description: `${tier} - ${period}`,
            logo: ""
        },
        callback: function (data) {
            console.log('Payment callback:', data);
            if (data.status === "successful") {
                // Show loader while verifying payment
                showLoader();
                handleSuccessfulPayment(data, tier, period, amount);
            } else if (data.status === "cancelled") {
                console.log('Payment was cancelled by user');
                showModal({
                    message: 'Payment was cancelled. You can try again anytime.',
                    confirmText: 'OK'
                });
            } else {
                console.log('Payment failed:', data);
                showModal({
                    message: 'Payment failed. Please try again or contact support.',
                    confirmText: 'OK'
                });
            }
        },
        onclose: function() {
            console.log('Payment modal closed');
            // Don't show loader if modal is just closed without payment
        }
    });
};

async function handleSuccessfulPayment(paymentData, tier, period, amount) {
    try {
        console.log('🔄 Verifying payment with server...');

        // Call Supabase Edge Function to verify payment
        const { data: verificationResult, error: verificationError } = await supabase.functions.invoke('verify-payment', {
            body: {
                transaction_id: paymentData.transaction_id,
                tx_ref: paymentData.tx_ref,
                amount: amount,
                tier: tier,
                period: period,
                user_id: currentUser.id,
                flw_ref: paymentData.flw_ref || paymentData.transaction_id
            }
        });

        // Always hide loader after verification attempt (success or error)
        hideLoader();

        if (verificationError) {
            console.error('Payment verification failed:', verificationError);
            showModal({
                message: 'Payment verification failed. Please contact support with your transaction ID: ' + paymentData.transaction_id,
                confirmText: 'OK'
            });
            return;
        }

        if (verificationResult?.success) {
            // Update local user tier
            verifiedTier = tier;
            console.log('✅ Payment verified and subscription updated successfully!');

            showModal({
                message: `🎉 Congratulations!\n\nYour ${tier} subscription is now active!\n\nTransaction ID: ${paymentData.transaction_id}`,
                confirmText: 'Continue',
                onConfirm: () => {
                    // Reload the subscriptions page to show updated tier
                    loadPage('subscriptions');
                }
            });
        } else {
            console.error('Payment verification failed:', verificationResult);
            showModal({
                message: verificationResult?.message || 'Payment could not be verified. Please contact support.',
                confirmText: 'OK'
            });
        }

    } catch (error) {
        hideLoader();
        console.error('Error handling successful payment:', error);
        showModal({
            message: 'Payment successful but there was an error verifying it. Please contact support with your transaction ID: ' + paymentData.transaction_id,
            confirmText: 'OK'
        });
    }
}

function redirectToLogin() {
    console.log('No user found, redirecting to login...');
    window.location.href = './Auth/login.html';
}

// ===== Ad System Management =====
function initializeAdSystemForUser() {
    console.log('🔧 Initializing ad system for tier:', verifiedTier);

    if (verifiedTier === "Free Tier") {
        // Check if consent has been granted for advertising
        checkConsentAndLoadAds();
    } else {
        console.log('👑 Premium user - no ads');
        hideAdBlockerMessage(); // Hide any existing adblocker message
    }
}

// Check consent status and load ads accordingly
function checkConsentAndLoadAds() {
    // Listen for consent updates
    window.addEventListener('consentUpdated', function(event) {
        const consent = event.detail;
        console.log('🍪 Consent updated:', consent);

        if (consent.ad_storage === 'granted' && verifiedTier === "Free Tier") {
            loadAdsForFreeUsers();
        } else {
            console.log('🚫 Ads not loaded - consent denied or premium user');
        }
    });

    // Check if consent manager is available and get current consent
    if (window.consentManager) {
        const currentConsent = window.consentManager.getConsentStatus();
        if (currentConsent && currentConsent.ad_storage === 'granted') {
            loadAdsForFreeUsers();
        } else {
            console.log('⏳ Waiting for user consent to load ads...');
        }
    } else {
        // Fallback: load consent manager if not available
        console.log('⏳ Consent manager not ready, waiting...');
        setTimeout(checkConsentAndLoadAds, 1000);
    }
}

function loadAdsForFreeUsers() {
    if (verifiedTier !== "Free Tier" || adsLoaded) {
        console.log('👑 Premium user - ads disabled');
        return;
    }

    console.log('📺 Loading ads for free user...');

    // Load Google AdSense script dynamically
    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9868946535437166';
    script.crossOrigin = 'anonymous';

    script.onload = () => {
        console.log('✅ AdSense loaded successfully');
        adsLoaded = true;
        // Adblocker detection disabled
        // setTimeout(detectAdBlocker, 1000);
    };

    script.onerror = () => {
        console.log('❌ AdSense failed to load');
        // Adblocker detection disabled
        // adblockerDetected = true;
        // showAdBlockerMessage();
    };

    document.head.appendChild(script);
}

function showAdBlockerMessage() {
    try {
        // Only show for free users
        if (verifiedTier !== "Free Tier") return;

        console.log('📢 Showing adblocker message');

        // Don't show if already displayed
        if (document.getElementById('adblocker-overlay')) {
            console.log('Adblocker overlay already displayed');
            return;
        }

        // Create full-page overlay
        const overlay = document.createElement('div');
        overlay.id = 'adblocker-overlay';
        overlay.innerHTML = `
            <div class="adblocker-container">
                <div class="adblocker-content">
                    <div class="adblocker-icon">🚫</div>
                    <h2>AdBlocker Detected</h2>
                    <p>We noticed you're using an ad blocker. To continue using StatWise for free, please:</p>
                    <ul>
                        <li>✅ Disable your ad blocker for this site</li>
                        <li>🔄 Refresh the page</li>
                        <li>💎 Or upgrade to Premium for an ad-free experience</li>
                    </ul>
                    <div class="adblocker-buttons">
                        <button onclick="window.location.reload()" class="btn-refresh">Refresh Page</button>
                        <button onclick="window.loadPage('subscriptions')" class="btn-upgrade">Upgrade to Premium</button>
                    </div>
                    <p class="adblocker-note">Ads help us keep StatWise free for everyone!</p>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);
    } catch (err) {
        console.error('Error showing adblocker message:', err);
    }
}

function hideAdBlockerMessage() {
    const overlay = document.getElementById('adblocker-overlay');
    if (overlay) {
        overlay.remove();
    }
}

console.log('✅ StatWise main application loaded with Supabase integration!');

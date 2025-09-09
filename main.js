// main.js - StatWise PWA with Supabase-only implementation
import { supabase, FLWPUBK } from './env.js';
import { showLoader, hideLoader, showSpinner, hideSpinner } from './Loader/loader.js';
import { initInteractiveBackground, initializeTheme } from './ui.js';
import { initializeAppSecurity, manageInitialPageLoad } from './manager.js';
import { formatTimestamp, addHistoryUnique } from './utils.js';

// ===== Global Variables =====
const main = document.querySelector("main");
const navButtons = document.querySelectorAll(".bottom-nav button");
const defaultPage = "home";
let currentUser = null;
let verifiedTier = "Free Tier";

// Initialize the app
initializeTheme(); // Initialize theme system
initializeSupabaseAuth();
checkPaymentRedirect();

// ===== Authentication Setup =====
async function initializeSupabaseAuth() {
    // Get initial session
    const { data: { session }, error } = await supabase.auth.getSession();
    
    if (session) {
        currentUser = session.user;
        await handleUserLogin(session.user);
    } else {
        redirectToLogin();
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
    // Load default page
    loadPage(defaultPage);
    
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
    
    // Initialize other app features
    initializeAppSecurity();
    manageInitialPageLoad();
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
        showLoader();
        
        // Update active navigation
        navButtons.forEach(btn => {
            btn.classList.toggle("active", btn.getAttribute("data-page") === page);
        });
        
        // Load page content
        const response = await fetch(`./Pages/${page}.html`);
        if (response.ok) {
            const content = await response.text();
            main.innerHTML = content;
            
            // Reset scroll position to top for each new page
            main.scrollTop = 0;
            
            // Initialize page-specific functionality
            await initializePage(page);
        } else {
            main.innerHTML = '<div class="error">Page not found</div>';
            main.scrollTop = 0;
        }
        
        hideLoader();
    } catch (error) {
        console.error('Error loading page:', error);
        main.innerHTML = '<div class="error">Error loading page</div>';
        main.scrollTop = 0;
        hideLoader();
    }
}

async function initializePage(page) {
    switch (page) {
        case 'home':
            await initializeHomePage();
            break;
        case 'history':
            await initializeHistoryPage();
            break;
        case 'profile':
            await initializeProfilePage();
            break;
        case 'subscriptions':
            await initializeSubscriptionsPage();
            break;
        case 'referral':
            await initializeReferralPage();
            break;
        case 'insights':
            await initializeInsightsPage();
            break;
    }
}

// ===== Page Initializers =====
async function initializeHomePage() {
    // Load predictions based on user tier
    await loadPredictions();
}

async function loadPredictions() {
    try {
        // Determine accessible tiers based on user subscription
        let accessibleTiers = ['free'];
        
        if (verifiedTier === 'Premium Tier') {
            accessibleTiers.push('premium');
        } else if (verifiedTier === 'VIP Tier') {
            accessibleTiers.push('premium', 'vip');
        } else if (verifiedTier === 'VVIP Tier') {
            accessibleTiers.push('premium', 'vip', 'vvip');
        }
        
        const { data: predictions, error } = await supabase
            .from('predictions')
            .select('*')
            .in('tier', accessibleTiers)
            .gte('kickoff_time', new Date().toISOString())
            .order('kickoff_time', { ascending: true })
            .limit(10);
            
        if (error) {
            console.warn('Error loading predictions:', error);
            return;
        }
        
        displayPredictions(predictions || []);
    } catch (error) {
        console.error('Error loading predictions:', error);
    }
}

function displayPredictions(predictions) {
    const container = document.getElementById('predictions-container');
    if (!container) return;
    
    if (predictions.length === 0) {
        container.innerHTML = `
            <div class="no-predictions">
                <h3>No predictions available</h3>
                <p>Check back later for new AI predictions!</p>
            </div>
        `;
        return;
    }
    
    const predictionsHTML = predictions.map(prediction => `
        <div class="prediction-card tier-${prediction.tier}">
            <div class="match-header">
                <h4>${prediction.home_team} vs ${prediction.away_team}</h4>
                <span class="league">${prediction.league}</span>
            </div>
            <div class="prediction-content">
                <div class="prediction-result">
                    <span class="label">Prediction:</span>
                    <span class="result">${prediction.prediction}</span>
                </div>
                <div class="confidence">
                    <span class="label">Confidence:</span>
                    <span class="value">${prediction.confidence}%</span>
                </div>
                ${prediction.odds ? `
                    <div class="odds">
                        <span class="label">Odds:</span>
                        <span class="value">${prediction.odds}</span>
                    </div>
                ` : ''}
                <div class="kickoff">
                    <span class="label">Kickoff:</span>
                    <span class="time">${formatTimestamp(prediction.kickoff_time)}</span>
                </div>
            </div>
            ${prediction.reasoning ? `
                <div class="reasoning">
                    <p>${prediction.reasoning}</p>
                </div>
            ` : ''}
            <div class="prediction-actions">
                <button onclick="savePrediction('${prediction.id}')" class="btn-save">
                    Save to History
                </button>
            </div>
        </div>
    `).join('');
    
    container.innerHTML = predictionsHTML;
}

async function initializeHistoryPage() {
    await loadUserPredictionHistory();
}

async function loadUserPredictionHistory() {
    if (!currentUser) return;
    
    try {
        const { data: history, error } = await supabase
            .from('user_prediction_history')
            .select(`
                *,
                predictions (*)
            `)
            .eq('user_id', currentUser.id)
            .order('saved_at', { ascending: false });
            
        if (error) {
            console.warn('Error loading prediction history:', error);
            return;
        }
        
        displayPredictionHistory(history || []);
    } catch (error) {
        console.error('Error loading prediction history:', error);
    }
}

function displayPredictionHistory(history) {
    const container = document.getElementById('history-container');
    if (!container) return;
    
    if (history.length === 0) {
        container.innerHTML = `
            <div class="no-history">
                <h3>No saved predictions</h3>
                <p>Save predictions from the home page to track them here!</p>
            </div>
        `;
        return;
    }
    
    const historyHTML = history.map(item => {
        const prediction = item.predictions;
        return `
            <div class="history-item">
                <div class="match-info">
                    <h4>${prediction.home_team} vs ${prediction.away_team}</h4>
                    <span class="league">${prediction.league}</span>
                </div>
                <div class="prediction-info">
                    <span class="prediction">${prediction.prediction}</span>
                    <span class="confidence">${prediction.confidence}% confidence</span>
                </div>
                <div class="saved-date">
                    Saved: ${formatTimestamp(item.saved_at)}
                </div>
                ${item.notes ? `<div class="notes">${item.notes}</div>` : ''}
            </div>
        `;
    }).join('');
    
    container.innerHTML = historyHTML;
}

async function initializeProfilePage() {
    await loadUserProfile();
}

async function loadUserProfile() {
    if (!currentUser) return;
    
    try {
        const { data: profile, error } = await supabase
            .from('user_profiles')
            .select('*')
            .eq('id', currentUser.id)
            .single();
            
        if (error) {
            console.warn('Error loading user profile:', error);
            return;
        }
        
        displayUserProfile(profile);
    } catch (error) {
        console.error('Error loading user profile:', error);
    }
}

function displayUserProfile(profile) {
    // Update user name
    const userNameElement = document.getElementById('userName');
    if (userNameElement) {
        userNameElement.textContent = profile.display_name || profile.username || 'User';
    }
    
    // Update user email
    const userEmailElement = document.getElementById('userEmail');
    if (userEmailElement) {
        userEmailElement.textContent = profile.email || '';
    }
    
    // Update user tier
    const userTierElement = document.getElementById('user-tier');
    if (userTierElement) {
        userTierElement.textContent = profile.current_tier || 'Free Tier';
    }
    
    // Update profile avatar
    const avatarContainer = document.getElementById('profileAvatarContainer');
    if (avatarContainer) {
        if (profile.profile_picture_url) {
            avatarContainer.innerHTML = `<img src="${profile.profile_picture_url}" alt="Profile Picture" class="avatar-img">`;
        } else {
            const initial = (profile.display_name || profile.username || 'U').charAt(0).toUpperCase();
            avatarContainer.innerHTML = `<div class="default-avatar">${initial}</div>`;
        }
    }
    
    // Initialize profile page interactions
    initializeProfileInteractions();
}

function initializeProfileInteractions() {
    // Initialize dark mode toggle
    const darkModeToggle = document.getElementById('darkModeToggle');
    if (darkModeToggle) {
        // Set current state
        const currentTheme = localStorage.getItem('statwise-theme') || 'light';
        darkModeToggle.checked = currentTheme === 'dark';
        
        // Add event listener
        darkModeToggle.addEventListener('change', function() {
            import('./ui.js').then(({ toggleTheme }) => {
                toggleTheme();
            });
        });
    }
    
    // Initialize logout button
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', window.signOut);
    }
    
    // Initialize manage subscription button
    const manageSubscription = document.getElementById('manageSubscription');
    if (manageSubscription) {
        manageSubscription.addEventListener('click', () => {
            loadPage('manage-subscription');
        });
    }
    
    // Initialize referral button
    const referralBtn = document.getElementById('referralBtn');
    if (referralBtn) {
        referralBtn.addEventListener('click', () => {
            loadPage('referral');
        });
    }
    
    // Initialize reset storage button
    const resetStorage = document.getElementById('resetStorage');
    if (resetStorage) {
        resetStorage.addEventListener('click', () => {
            if (confirm('Are you sure you want to reset local cache? This will clear saved predictions and preferences.')) {
                localStorage.clear();
                location.reload();
            }
        });
    }
    
    // Initialize delete account button
    const deleteAccountBtn = document.getElementById('deleteAccountBtn');
    if (deleteAccountBtn) {
        deleteAccountBtn.addEventListener('click', async () => {
            if (confirm('Are you sure you want to delete your account? This action cannot be undone.')) {
                await deleteUserAccount();
            }
        });
    }
}

async function deleteUserAccount() {
    try {
        // Note: Account deletion requires server-side implementation
        // For now, we'll just sign out and show a message
        alert('Account deletion request submitted. Please contact support for assistance.');
        await window.signOut();
    } catch (error) {
        console.error('Error deleting account:', error);
        alert('Error processing account deletion request.');
    }
}

async function initializeSubscriptionsPage() {
    await loadSubscriptionInfo();
}

async function loadSubscriptionInfo() {
    if (!currentUser) return;
    
    try {
        const { data: profile, error } = await supabase
            .from('user_profiles')
            .select('current_tier, subscription_period, subscription_start, subscription_end, subscription_status')
            .eq('id', currentUser.id)
            .single();
            
        if (error) {
            console.warn('Error loading subscription info:', error);
            return;
        }
        
        displaySubscriptionInfo(profile);
    } catch (error) {
        console.error('Error loading subscription info:', error);
    }
}

function displaySubscriptionInfo(profile) {
    const container = document.getElementById('subscription-container');
    if (!container) return;
    
    const isActive = profile.subscription_status === 'active';
    const hasSubscription = profile.current_tier !== 'Free Tier';
    
    container.innerHTML = `
        <div class="subscription-section">
            <div class="current-subscription">
                <h3>Current Subscription</h3>
                <div class="subscription-card">
                    <h4>${profile.current_tier}</h4>
                    ${hasSubscription ? `
                        <p>Period: ${profile.subscription_period}</p>
                        <p>Status: ${isActive ? '✅ Active' : '❌ Inactive'}</p>
                        ${profile.subscription_end ? `
                            <p>Expires: ${formatTimestamp(profile.subscription_end)}</p>
                        ` : ''}
                    ` : `
                        <p>Free tier with basic features</p>
                    `}
                </div>
            </div>
            
            <div class="upgrade-options">
                <h3>Upgrade Your Plan</h3>
                <div class="plans-grid">
                    <div class="plan-card">
                        <h4>Premium Tier</h4>
                        <p class="price">₦2,000/month</p>
                        <ul>
                            <li>Premium predictions</li>
                            <li>Higher accuracy</li>
                            <li>Email notifications</li>
                        </ul>
                        <button onclick="initializePayment('Premium Tier', 'monthly', 2000)" class="btn-upgrade">
                            Upgrade to Premium
                        </button>
                    </div>
                    
                    <div class="plan-card featured">
                        <h4>VIP Tier</h4>
                        <p class="price">₦5,000/month</p>
                        <ul>
                            <li>VIP predictions</li>
                            <li>Insider insights</li>
                            <li>Priority support</li>
                        </ul>
                        <button onclick="initializePayment('VIP Tier', 'monthly', 5000)" class="btn-upgrade">
                            Upgrade to VIP
                        </button>
                    </div>
                    
                    <div class="plan-card">
                        <h4>VVIP Tier</h4>
                        <p class="price">₦10,000/month</p>
                        <ul>
                            <li>VVIP predictions</li>
                            <li>Exclusive analysis</li>
                            <li>Direct AI access</li>
                        </ul>
                        <button onclick="initializePayment('VVIP Tier', 'monthly', 10000)" class="btn-upgrade">
                            Upgrade to VVIP
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

async function initializeReferralPage() {
    await loadReferralData();
}

async function loadReferralData() {
    if (!currentUser) return;
    
    try {
        // Get user's referral code
        const { data: referralCode, error: codeError } = await supabase
            .from('referral_codes')
            .select('*')
            .eq('user_id', currentUser.id)
            .single();
            
        // Get user's referrals
        const { data: referrals, error: referralsError } = await supabase
            .from('referrals')
            .select(`
                *,
                user_profiles!referred_id (display_name, email, current_tier, created_at)
            `)
            .eq('referrer_id', currentUser.id)
            .order('created_at', { ascending: false });
            
        if (codeError && codeError.code !== 'PGRST116') {
            console.warn('Error loading referral code:', codeError);
        }
        
        if (referralsError) {
            console.warn('Error loading referrals:', referralsError);
        }
        
        displayReferralData(referralCode, referrals || []);
    } catch (error) {
        console.error('Error loading referral data:', error);
    }
}

function displayReferralData(referralCode, referrals) {
    const container = document.getElementById('referral-container');
    if (!container) return;
    
    const code = referralCode?.code || 'Loading...';
    
    container.innerHTML = `
        <div class="referral-section">
            <div class="referral-code-section">
                <h3>Your Referral Code</h3>
                <div class="referral-code-card">
                    <div class="code-display">
                        <span id="referral-code">${code}</span>
                        <button onclick="copyReferralCode()" class="btn-copy">Copy</button>
                    </div>
                    <p>Share this code with friends to earn rewards!</p>
                </div>
            </div>
            
            <div class="referral-stats">
                <div class="stat">
                    <span class="label">Total Referrals</span>
                    <span class="value">${referrals.length}</span>
                </div>
                <div class="stat">
                    <span class="label">Total Rewards</span>
                    <span class="value">₦${(referrals.length * 500).toLocaleString()}</span>
                </div>
            </div>
            
            <div class="referrals-list">
                <h3>Your Referrals</h3>
                ${referrals.length === 0 ? `
                    <div class="no-referrals">
                        <p>No referrals yet. Share your code to start earning!</p>
                    </div>
                ` : `
                    <div class="referrals-grid">
                        ${referrals.map(referral => `
                            <div class="referral-item">
                                <h4>${referral.user_profiles.display_name}</h4>
                                <p class="email">${referral.user_profiles.email}</p>
                                <span class="tier">${referral.user_profiles.current_tier}</span>
                                <div class="referral-date">
                                    Joined: ${formatTimestamp(referral.created_at)}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `}
            </div>
        </div>
    `;
}

async function initializeInsightsPage() {
    // VIP and VVIP tier only
    if (!hasAccess('VIP Tier')) {
        showUpgradeModal('VIP Tier');
        return;
    }
    
    await loadInsights();
}

async function loadInsights() {
    try {
        const { data: accuracy, error } = await supabase
            .from('prediction_accuracy')
            .select('*')
            .order('date', { ascending: false })
            .limit(30);
            
        if (error) {
            console.warn('Error loading insights:', error);
            return;
        }
        
        displayInsights(accuracy || []);
    } catch (error) {
        console.error('Error loading insights:', error);
    }
}

function displayInsights(accuracy) {
    const container = document.getElementById('insights-container');
    if (!container) return;
    
    const totalPredictions = accuracy.reduce((sum, day) => sum + (day.total_predictions || 0), 0);
    const correctPredictions = accuracy.reduce((sum, day) => sum + (day.correct_predictions || 0), 0);
    const overallAccuracy = totalPredictions > 0 ? (correctPredictions / totalPredictions * 100).toFixed(1) : 0;
    
    container.innerHTML = `
        <div class="insights-section">
            <div class="insights-header">
                <h3>AI Prediction Performance</h3>
                <div class="overall-stats">
                    <div class="stat-card">
                        <h4>Overall Accuracy</h4>
                        <span class="stat-value">${overallAccuracy}%</span>
                    </div>
                    <div class="stat-card">
                        <h4>Total Predictions</h4>
                        <span class="stat-value">${totalPredictions}</span>
                    </div>
                    <div class="stat-card">
                        <h4>Correct Predictions</h4>
                        <span class="stat-value">${correctPredictions}</span>
                    </div>
                </div>
            </div>
            
            <div class="accuracy-chart">
                <h4>Recent Performance</h4>
                ${accuracy.length === 0 ? `
                    <p>No performance data available yet.</p>
                ` : `
                    <div class="chart-data">
                        ${accuracy.slice(0, 7).map(day => {
                            const dayAccuracy = day.total_predictions > 0 ? 
                                (day.correct_predictions / day.total_predictions * 100).toFixed(1) : 0;
                            return `
                                <div class="chart-bar">
                                    <div class="bar" style="height: ${dayAccuracy}%"></div>
                                    <span class="date">${new Date(day.date).toLocaleDateString()}</span>
                                    <span class="accuracy">${dayAccuracy}%</span>
                                </div>
                            `;
                        }).join('')}
                    </div>
                `}
            </div>
        </div>
    `;
}

// ===== Global Functions =====
window.savePrediction = async function(predictionId) {
    if (!currentUser) {
        showModal({ message: 'Please log in to save predictions.' });
        return;
    }
    
    try {
        const { data, error } = await supabase
            .from('user_prediction_history')
            .insert({
                user_id: currentUser.id,
                prediction_id: predictionId,
                saved_at: new Date().toISOString()
            });
            
        if (error && error.code !== '23505') { // Ignore duplicate key errors
            console.warn('Error saving prediction:', error);
            showModal({ message: 'Error saving prediction. Please try again.' });
            return;
        }
        
        showModal({ 
            message: 'Prediction saved to your history!',
            confirmText: 'View History',
            cancelText: 'Continue',
            onConfirm: () => loadPage('history')
        });
    } catch (error) {
        console.error('Error saving prediction:', error);
        showModal({ message: 'Error saving prediction. Please try again.' });
    }
};

window.copyReferralCode = function() {
    const codeElement = document.getElementById('referral-code');
    if (codeElement) {
        navigator.clipboard.writeText(codeElement.textContent).then(() => {
            showModal({ message: 'Referral code copied to clipboard!' });
        });
    }
};

window.signOut = async function() {
    try {
        const { error } = await supabase.auth.signOut();
        if (error) {
            console.error('Error signing out:', error);
        }
    } catch (error) {
        console.error('Error signing out:', error);
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
                handleSuccessfulPayment(data, tier, period, amount);
            }
        },
        onclose: function() {
            console.log('Payment modal closed');
        }
    });
};

async function handleSuccessfulPayment(paymentData, tier, period, amount) {
    try {
        // Calculate subscription dates
        const startDate = new Date();
        const endDate = new Date();
        endDate.setMonth(endDate.getMonth() + 1); // Add 1 month
        
        // Update user subscription
        const { data: updatedProfile, error: updateError } = await supabase
            .from('user_profiles')
            .update({
                current_tier: tier,
                tier: tier,
                subscription_period: period,
                subscription_start: startDate.toISOString(),
                subscription_end: endDate.toISOString(),
                subscription_status: 'active',
                tier_expiry: endDate.toISOString(),
                updated_at: new Date().toISOString()
            })
            .eq('id', currentUser.id)
            .select()
            .single();
            
        if (updateError) {
            console.error('Error updating subscription:', updateError);
        } else {
            verifiedTier = tier;
            console.log('Subscription updated successfully');
        }
        
        // Log payment transaction
        await supabase
            .from('payment_transactions')
            .insert({
                user_id: currentUser.id,
                transaction_id: paymentData.transaction_id,
                tx_ref: paymentData.tx_ref,
                amount: amount,
                currency: 'NGN',
                status: 'successful',
                payment_type: 'flutterwave',
                tier: tier,
                period: period,
                created_at: new Date().toISOString()
            });
            
        // Log subscription event
        await supabase
            .from('subscription_events')
            .insert({
                user_id: currentUser.id,
                event_type: 'subscription_purchase',
                event_data: {
                    tier: tier,
                    period: period,
                    amount: amount,
                    transaction_id: paymentData.transaction_id
                },
                created_at: new Date().toISOString()
            });
        
        showModal({
            message: `🎉 Congratulations!\n\nYour ${tier} subscription is now active!\n\nTransaction ID: ${paymentData.transaction_id}`,
            confirmText: 'Continue',
            onConfirm: () => {
                loadPage('subscriptions');
            }
        });
        
    } catch (error) {
        console.error('Error handling successful payment:', error);
        showModal({
            message: 'Payment successful but there was an error updating your subscription. Please contact support.',
            confirmText: 'OK'
        });
    }
}

function redirectToLogin() {
    console.log('No user found, redirecting to login...');
    window.location.href = './Auth/login.html';
}

// Theme initialization is now handled by ui.js

// ===== Modal Helper Function =====
function showModal(options) {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content">
            <div class="modal-message">${options.message}</div>
            <div class="modal-actions">
                ${options.cancelText ? `<button class="btn-cancel">${options.cancelText}</button>` : ''}
                <button class="btn-confirm ${options.confirmClass || 'btn-primary'}">${options.confirmText || 'OK'}</button>
            </div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    const confirmBtn = modal.querySelector('.btn-confirm');
    const cancelBtn = modal.querySelector('.btn-cancel');
    
    confirmBtn.addEventListener('click', () => {
        document.body.removeChild(modal);
        if (options.onConfirm) options.onConfirm();
    });
    
    if (cancelBtn) {
        cancelBtn.addEventListener('click', () => {
            document.body.removeChild(modal);
            if (options.onCancel) options.onCancel();
        });
    }
    
    // Close on overlay click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            document.body.removeChild(modal);
            if (options.onCancel) options.onCancel();
        }
    });
}

console.log('✅ StatWise main application loaded with Supabase integration!');
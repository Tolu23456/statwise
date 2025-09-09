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
        showLoader();
        
        // Save current page to localStorage for reload persistence
        localStorage.setItem('lastPage', page);
        
        // Update active navigation
        navButtons.forEach(btn => {
            btn.classList.toggle("active", btn.getAttribute("data-page") === page);
        });
        
        // Add fade-out transition to current content
        main.classList.add('page-fade-out');
        
        // Wait for fade-out animation to complete
        await new Promise(resolve => setTimeout(resolve, 200));
        
        // Load page content
        const response = await fetch(`./Pages/${page}.html`);
        if (response.ok) {
            const content = await response.text();
            main.innerHTML = content;
            
            // Reset scroll position to top for each new page
            main.scrollTop = 0;
            
            // Remove fade-out and add fade-in transition
            main.classList.remove('page-fade-out');
            main.classList.add('page-fade-in');
            
            // Initialize page-specific functionality
            await initializePage(page);
            
            // Remove fade-in class after animation completes
            setTimeout(() => {
                main.classList.remove('page-fade-in');
            }, 300);
        } else {
            main.innerHTML = '<div class="error">Page not found</div>';
            main.scrollTop = 0;
            main.classList.remove('page-fade-out');
        }
        
        hideLoader();
    } catch (error) {
        console.error('Error loading page:', error);
        main.innerHTML = '<div class="error">Error loading page</div>';
        main.scrollTop = 0;
        main.classList.remove('page-fade-out');
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
    initializeHistoryTabs();
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
    // Update predictions tab
    const predictionsContainer = document.querySelector('#predictions-tab .history-container');
    if (predictionsContainer) {
        if (history.length === 0) {
            predictionsContainer.innerHTML = `
                <div class="no-history">
                    <h3>No saved predictions</h3>
                    <p>Save predictions from the home page to track them here!</p>
                </div>
            `;
        } else {
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
            predictionsContainer.innerHTML = historyHTML;
        }
    }
    
    // Load account history
    loadAccountHistory();
    
    // Load transaction history  
    loadTransactionHistory();
}

function initializeHistoryTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');
    
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetTab = button.getAttribute('data-tab');
            
            // Update active button
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            
            // Update active content
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === targetTab) {
                    content.classList.add('active');
                }
            });
        });
    });
}

async function loadAccountHistory() {
    if (!currentUser) return;
    
    const accountContainer = document.querySelector('#account-tab .history-container');
    if (!accountContainer) return;
    
    try {
        // Get user profile updates, login history, etc.
        const { data: profile, error } = await supabase
            .from('user_profiles')
            .select('created_at, last_login, current_tier, subscription_status')
            .eq('id', currentUser.id)
            .single();
            
        if (error) {
            accountContainer.innerHTML = '<p>Error loading account history</p>';
            return;
        }
        
        accountContainer.innerHTML = `
            <div class="account-history">
                <div class="history-item">
                    <h4>Account Created</h4>
                    <p>${formatTimestamp(profile.created_at)}</p>
                </div>
                <div class="history-item">
                    <h4>Last Login</h4>
                    <p>${formatTimestamp(profile.last_login)}</p>
                </div>
                <div class="history-item">
                    <h4>Current Tier</h4>
                    <p>${profile.current_tier}</p>
                </div>
                <div class="history-item">
                    <h4>Subscription Status</h4>
                    <p>${profile.subscription_status || 'active'}</p>
                </div>
            </div>
        `;
    } catch (error) {
        console.error('Error loading account history:', error);
        accountContainer.innerHTML = '<p>Error loading account history</p>';
    }
}

async function loadTransactionHistory() {
    if (!currentUser) return;
    
    const transactionsContainer = document.querySelector('#transactions-tab .history-container');
    if (!transactionsContainer) return;
    
    try {
        const { data: transactions, error } = await supabase
            .from('subscription_events')
            .select('*')
            .eq('user_id', currentUser.id)
            .order('created_at', { ascending: false });
            
        if (error) {
            transactionsContainer.innerHTML = '<p>Error loading transaction history</p>';
            return;
        }
        
        if (transactions.length === 0) {
            transactionsContainer.innerHTML = '<p>No transactions found</p>';
            return;
        }
        
        const transactionsHTML = transactions.map(transaction => `
            <div class="history-item">
                <div class="transaction-info">
                    <h4>${transaction.event_type}</h4>
                    <p>Amount: ₦${transaction.amount?.toLocaleString() || '0'}</p>
                    <p>Status: ${transaction.status}</p>
                    <p>Date: ${formatTimestamp(transaction.created_at)}</p>
                </div>
            </div>
        `).join('');
        
        transactionsContainer.innerHTML = transactionsHTML;
    } catch (error) {
        console.error('Error loading transaction history:', error);
        transactionsContainer.innerHTML = '<p>Error loading transaction history</p>';
    }
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
            avatarContainer.innerHTML = `<img src="${profile.profile_picture_url}" alt="Profile Picture" class="avatar-img" onclick="triggerAvatarUpload()">`;
        } else {
            const initial = (profile.display_name || profile.username || 'U').charAt(0).toUpperCase();
            avatarContainer.innerHTML = `<div class="default-avatar" onclick="triggerAvatarUpload()">${initial}</div>`;
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
    
    
    // Initialize avatar upload
    const avatarUpload = document.getElementById('avatarUpload');
    if (avatarUpload) {
        console.log('Avatar upload input found, adding event listener');
        avatarUpload.addEventListener('change', handleAvatarUpload);
    } else {
        console.warn('Avatar upload input not found during initialization');
    }
}

// ===== Avatar Upload Functions =====
function triggerAvatarUpload() {
    const avatarUpload = document.getElementById('avatarUpload');
    if (avatarUpload) {
        console.log('Triggering file upload dialog...');
        // Reset the input value to ensure change event fires even for same file
        avatarUpload.value = '';
        avatarUpload.click();
    } else {
        console.error('Avatar upload input not found');
    }
}

// Make functions globally available
window.triggerAvatarUpload = triggerAvatarUpload;

async function handleAvatarUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    // Validate file type
    const allowedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'];
    if (!allowedTypes.includes(file.type)) {
        showModal({
            message: 'Please select a valid image file (JPEG, PNG, GIF, or WebP).',
            confirmText: 'OK'
        });
        return;
    }
    
    // Validate file size (5MB limit)
    const maxSize = 5 * 1024 * 1024; // 5MB in bytes
    if (file.size > maxSize) {
        showModal({
            message: 'Image file is too large. Please select an image smaller than 5MB.',
            confirmText: 'OK'
        });
        return;
    }
    
    try {
        showSpinner();
        
        // Generate unique filename
        const fileExt = file.name.split('.').pop();
        const fileName = `${currentUser.id}-${Date.now()}.${fileExt}`;
        
        // Upload to Supabase Storage
        const { data: uploadData, error: uploadError } = await supabase.storage
            .from('profile-pictures')
            .upload(fileName, file, {
                cacheControl: '3600',
                upsert: true
            });
            
        if (uploadError) {
            console.error('Upload error:', uploadError);
            showModal({
                message: 'Failed to upload profile picture. Please try again.',
                confirmText: 'OK'
            });
            return;
        }
        
        // Get public URL
        const { data: urlData } = supabase.storage
            .from('profile-pictures')
            .getPublicUrl(fileName);
            
        if (!urlData.publicUrl) {
            showModal({
                message: 'Failed to get image URL. Please try again.',
                confirmText: 'OK'
            });
            return;
        }
        
        // Update user profile with new image URL
        const { error: updateError } = await supabase
            .from('user_profiles')
            .update({ 
                profile_picture_url: urlData.publicUrl,
                updated_at: new Date().toISOString()
            })
            .eq('id', currentUser.id);
            
        if (updateError) {
            console.error('Profile update error:', updateError);
            showModal({
                message: 'Failed to update profile. Please try again.',
                confirmText: 'OK'
            });
            return;
        }
        
        // Update the avatar display immediately
        const avatarContainer = document.getElementById('profileAvatarContainer');
        if (avatarContainer) {
            avatarContainer.innerHTML = `<img src="${urlData.publicUrl}" alt="Profile Picture" class="avatar-img" onclick="triggerAvatarUpload()">`;
        }
        
        showModal({
            message: '✅ Profile picture updated successfully!',
            confirmText: 'OK'
        });
        
        console.log('Profile picture updated:', urlData.publicUrl);
        
    } catch (error) {
        console.error('Error uploading avatar:', error);
        showModal({
            message: 'An error occurred while uploading your profile picture. Please try again.',
            confirmText: 'OK'
        });
    } finally {
        hideSpinner();
        // Clear the file input
        event.target.value = '';
    }
}


async function initializeSubscriptionsPage() {
    await loadSubscriptionInfo();
    initializeSubscriptionTabs();
    initializeSubscriptionButtons();
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
    // Update current tier display
    const userTierElement = document.getElementById('user-tier');
    if (userTierElement) {
        userTierElement.textContent = profile.current_tier || 'Free Tier';
    }
    
    // Update tier expiry
    const tierExpiryElement = document.getElementById('tier-expiry');
    if (tierExpiryElement && profile.subscription_end) {
        tierExpiryElement.textContent = `Expires: ${formatTimestamp(profile.subscription_end)}`;
        tierExpiryElement.style.display = 'block';
    } else if (tierExpiryElement) {
        tierExpiryElement.style.display = 'none';
    }
}

function initializeSubscriptionTabs() {
    const tabButtons = document.querySelectorAll('[data-tab]');
    const tabContents = document.querySelectorAll('.pricing-container');
    
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetTab = button.getAttribute('data-tab');
            
            // Update active button
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');
            
            // Update active content
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === targetTab) {
                    content.classList.add('active');
                }
            });
        });
    });
}

function initializeSubscriptionButtons() {
    const subscribeButtons = document.querySelectorAll('.subscribe-btn');
    
    subscribeButtons.forEach(button => {
        button.addEventListener('click', (e) => {
            e.preventDefault();
            
            const tier = button.getAttribute('data-tier');
            const amount = button.getAttribute('data-amount');
            const period = button.getAttribute('data-period');
            
            if (tier === 'free') {
                alert('You are already on the free tier!');
                return;
            }
            
            // Handle subscription upgrade
            handleSubscriptionUpgrade(tier, amount, period);
        });
    });
}

async function handleSubscriptionUpgrade(tier, amount, period) {
    try {
        // For now, show a placeholder payment flow
        const confirmUpgrade = confirm(`Upgrade to ${tier} tier for ₦${amount}/${period}?`);
        
        if (confirmUpgrade) {
            // Here you would integrate with Flutterwave for actual payment
            alert(`Payment integration would be initiated here for ${tier} tier upgrade.`);
            
            // For demo purposes, you could update the user's tier in the database
            // await upgradeUserTier(tier, period);
        }
    } catch (error) {
        console.error('Error handling subscription upgrade:', error);
        alert('Error processing subscription upgrade. Please try again.');
    }
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
    const code = referralCode?.code || 'No Code Found';
    
    // Update referral code input
    const referralCodeInput = document.getElementById('referralCodeInput');
    if (referralCodeInput) {
        referralCodeInput.value = code;
    }
    
    // Update referral list
    const referralListContainer = document.getElementById('referralListContainer');
    if (referralListContainer) {
        if (referrals.length === 0) {
            referralListContainer.innerHTML = '<p>No referrals yet. Share your code to get started!</p>';
        } else {
            const referralHTML = referrals.map(referral => `
                <div class="referral-item">
                    <div class="referral-info">
                        <h4>${referral.user_profiles?.display_name || 'User'}</h4>
                        <p class="email">${referral.user_profiles?.email || ''}</p>
                        <span class="tier">${referral.user_profiles?.current_tier || 'Free Tier'}</span>
                        <div class="referral-date">
                            Joined: ${formatTimestamp(referral.created_at)}
                        </div>
                    </div>
                    <div class="referral-reward">
                        ${referral.reward_claimed ? '✅ Rewarded' : '⏳ Pending'}
                    </div>
                </div>
            `).join('');
            referralListContainer.innerHTML = referralHTML;
        }
    }
    
    // Update rewards count
    const rewardsCount = document.getElementById('rewardsCount');
    if (rewardsCount) {
        const claimedRewards = referrals.filter(r => r.reward_claimed).length;
        rewardsCount.textContent = claimedRewards;
    }
    
    // Update rewards container
    const rewardsContainer = document.getElementById('rewardsContainer');
    if (rewardsContainer) {
        const claimedReferrals = referrals.filter(r => r.reward_claimed);
        if (claimedReferrals.length === 0) {
            rewardsContainer.innerHTML = '<p>No rewards earned yet. You\'ll get a reward when a referred user subscribes!</p>';
        } else {
            const rewardsHTML = claimedReferrals.map(referral => `
                <div class="reward-item">
                    <span>Premium Week from ${referral.user_profiles?.display_name || 'User'}</span>
                    <span class="reward-amount">₦${referral.reward_amount?.toLocaleString() || '500'}</span>
                </div>
            `).join('');
            rewardsContainer.innerHTML = rewardsHTML;
        }
    }
    
    // Initialize referral page interactions
    initializeReferralInteractions();
}

function initializeReferralInteractions() {
    // Initialize copy referral code button
    const copyReferralCodeBtn = document.getElementById('copyReferralCodeBtn');
    if (copyReferralCodeBtn) {
        copyReferralCodeBtn.addEventListener('click', () => {
            const referralCodeInput = document.getElementById('referralCodeInput');
            if (referralCodeInput && referralCodeInput.value !== 'Loading...' && referralCodeInput.value !== 'No Code Found') {
                navigator.clipboard.writeText(referralCodeInput.value).then(() => {
                    // Show success feedback
                    copyReferralCodeBtn.textContent = 'Copied!';
                    copyReferralCodeBtn.style.background = '#28a745';
                    setTimeout(() => {
                        copyReferralCodeBtn.textContent = 'Copy';
                        copyReferralCodeBtn.style.background = '';
                    }, 2000);
                }).catch(() => {
                    alert('Failed to copy referral code');
                });
            }
        });
    }
    
    // Initialize share buttons
    const shareWhatsAppBtn = document.getElementById('shareWhatsAppBtn');
    if (shareWhatsAppBtn) {
        shareWhatsAppBtn.addEventListener('click', () => {
            const referralCode = document.getElementById('referralCodeInput')?.value;
            if (referralCode && referralCode !== 'Loading...' && referralCode !== 'No Code Found') {
                const message = `Join StatWise using my referral code: ${referralCode} and get exclusive AI sports predictions!`;
                const url = `https://wa.me/?text=${encodeURIComponent(message)}`;
                window.open(url, '_blank');
            }
        });
    }
    
    const shareTwitterBtn = document.getElementById('shareTwitterBtn');
    if (shareTwitterBtn) {
        shareTwitterBtn.addEventListener('click', () => {
            const referralCode = document.getElementById('referralCodeInput')?.value;
            if (referralCode && referralCode !== 'Loading...' && referralCode !== 'No Code Found') {
                const message = `Join StatWise using my referral code: ${referralCode} and get exclusive AI sports predictions!`;
                const url = `https://twitter.com/intent/tweet?text=${encodeURIComponent(message)}`;
                window.open(url, '_blank');
            }
        });
    }
    
    const shareGenericBtn = document.getElementById('shareGenericBtn');
    if (shareGenericBtn) {
        shareGenericBtn.addEventListener('click', () => {
            const referralCode = document.getElementById('referralCodeInput')?.value;
            if (referralCode && referralCode !== 'Loading...' && referralCode !== 'No Code Found') {
                const message = `Join StatWise using my referral code: ${referralCode} and get exclusive AI sports predictions!`;
                if (navigator.share) {
                    navigator.share({
                        title: 'StatWise Referral',
                        text: message
                    });
                } else {
                    // Fallback to copying to clipboard
                    navigator.clipboard.writeText(message).then(() => {
                        alert('Referral message copied to clipboard!');
                    });
                }
            }
        });
    }
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

// Referral code copying is now handled in initializeReferralInteractions()

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
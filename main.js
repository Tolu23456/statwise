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
let verifiedTier = null; // Start as null until profile loads
let adsLoaded = false;
let adblockerDetected = false;

// Initialize the app
initializeTheme(); // Initialize theme system
initializeSupabaseAuth();
checkPaymentRedirect();
// Ad system will be initialized after user tier is loaded

// ===== Authentication Setup =====
async function initializeSupabaseAuth() {
    // Get initial session
    const { data: { session }, error } = await supabase.auth.getSession();

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
                    <button onclick="window.signOut()" style="background: transparent; color: var(--text-secondary, #666); border: 1px solid var(--border-color, #ddd); padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 16px;">
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
            const response = await fetch(`./Pages/${page}.html`);
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
            await initializeHomePage();
            break;
        case 'forum':
            await initializeForumPage();
            break;
        case 'profile':
            await initializeProfilePage();
            break;
        case 'subscriptions':
            await initializeSubscriptionsPage();
            break;
        case 'manage-subscription':
            await initializeManageSubscriptionPage();
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
    // Initialize league tabs
    initializeLeagueTabs();
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

// ===== Forum Functionality =====
async function initializeForumPage() {
    // Forum is coming soon - no functionality needed
    console.log('Forum page loaded - Coming Soon');
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
        // Set proper styling for container
        avatarContainer.style.position = 'relative';
        avatarContainer.style.cursor = 'pointer';
        
        if (profile.profile_picture_url) {
            avatarContainer.innerHTML = `
                <img src="${profile.profile_picture_url}" alt="Profile Picture" class="avatar-img" style="width: 100%; height: 100%; object-fit: cover; border-radius: 50%;">
                <div class="avatar-overlay" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.4); border-radius: 50%; display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity 0.3s; pointer-events: none;">
                    <svg width="24" height="24" fill="white" viewBox="0 0 24 24">
                        <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
                        <path d="M19 13h-2v2h-2v2h2v2h2v-2h2v-2h-2z"/>
                    </svg>
                </div>
            `;
        } else {
            const initial = (profile.display_name || profile.username || 'U').charAt(0).toUpperCase();
            avatarContainer.innerHTML = `
                <div class="default-avatar" style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; font-size: 48px; font-weight: bold; background: linear-gradient(135deg, var(--primary-color, #0e639c), #1e88e5); color: white; border-radius: 50%;">
                    ${initial}
                </div>
                <div class="avatar-overlay" style="position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.4); border-radius: 50%; display: flex; align-items: center; justify-content: center; opacity: 0; transition: opacity 0.3s; pointer-events: none;">
                    <svg width="24" height="24" fill="white" viewBox="0 0 24 24">
                        <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/>
                        <path d="M19 13h-2v2h-2v2h2v2h2v-2h2v-2h-2z"/>
                    </svg>
                </div>
            `;
        }

        // Add hover effect and click handler
        avatarContainer.addEventListener('mouseenter', () => {
            const overlay = avatarContainer.querySelector('.avatar-overlay');
            if (overlay) overlay.style.opacity = '1';
        });
        
        avatarContainer.addEventListener('mouseleave', () => {
            const overlay = avatarContainer.querySelector('.avatar-overlay');
            if (overlay) overlay.style.opacity = '0';
        });
        
        avatarContainer.addEventListener('click', () => {
            console.log('Avatar clicked - triggering upload');
            const avatarUpload = document.getElementById('avatarUpload');
            if (avatarUpload) {
                avatarUpload.click();
            } else {
                console.error('Avatar upload input not found');
            }
        });
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
            showModal({
                message: 'Are you sure you want to reset local cache? This will clear saved predictions and preferences.',
                confirmText: 'Reset Cache',
                cancelText: 'Cancel',
                onConfirm: () => {
                    localStorage.clear();
                    location.reload();
                }
            });
        });
    }

    // Initialize edit username button
    const editUsernameBtn = document.getElementById('editUsernameBtn');
    if (editUsernameBtn) {
        editUsernameBtn.addEventListener('click', () => {
            const userNameElement = document.getElementById('userName');
            if (userNameElement) {
                const currentName = userNameElement.textContent;

                showModal({
                    message: 'Enter your new username:',
                    inputType: 'text',
                    inputValue: currentName,
                    inputPlaceholder: 'Enter username',
                    confirmText: 'Save',
                    cancelText: 'Cancel',
                    onConfirm: async (newUsername) => {
                        if (newUsername && newUsername.trim() && newUsername.trim() !== currentName) {
                            await updateUsername(newUsername.trim());
                        }
                    }
                });
            }
        });
    }

    // Initialize avatar upload - wait for DOM to be ready
    const avatarUpload = document.getElementById('avatarUpload');
    if (avatarUpload) {
        console.log('✅ Avatar upload input found, adding event listener');
        // Remove any existing listeners first
        avatarUpload.removeEventListener('change', handleAvatarUpload);
        // Add the event listener
        avatarUpload.addEventListener('change', handleAvatarUpload);
    } else {
        console.warn('⚠️ Avatar upload input not found in profile page');
    }
}

// Make functions globally available
window.loadPage = loadPage;

async function handleAvatarUpload(event) {
    console.log('📸 Avatar upload triggered, processing file...');

    if (!event || !event.target || !event.target.files) {
        console.warn('Invalid upload event');
        return;
    }

    const file = event.target.files[0];
    if (!file) {
        console.log('No file selected');
        return;
    }

    console.log('File selected:', {
        name: file.name,
        size: file.size,
        type: file.type
    });

    // Validate file type
    const allowedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'];
    if (!allowedTypes.includes(file.type)) {
        console.error('Invalid file type:', file.type);
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

        // Reload profile to show updated avatar
        await loadUserProfile();

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
        if (event && event.target) {
            event.target.value = '';
        }
    }
}


async function initializeSubscriptionsPage() {
    console.log('Initializing subscriptions page...');
    await loadSubscriptionInfo();
    initializeSubscriptionTabs();
    initializeSubscriptionButtons();
    console.log('Subscriptions page initialized successfully');
}

async function initializeManageSubscriptionPage() {
    console.log('Initializing manage subscription page...');
    await loadManageSubscriptionInfo();
    initializeManageSubscriptionButtons();
}

async function loadManageSubscriptionInfo() {
    if (!currentUser) {
        console.warn('No current user found for manage subscription');
        return;
    }

    console.log('Loading manage subscription info for user:', currentUser.id);

    try {
        const { data: profile, error } = await supabase
            .from('user_profiles')
            .select('*')
            .eq('id', currentUser.id)
            .single();

        if (error) {
            console.warn('Error loading subscription info:', error);
            return;
        }

        console.log('Profile data loaded:', profile);
        displayManageSubscriptionInfo(profile);
    } catch (error) {
        console.error('Error loading subscription info:', error);
    }
}

function displayManageSubscriptionInfo(profile) {
    const planInfoCard = document.getElementById('plan-info-card');
    if (!planInfoCard) {
        console.warn('plan-info-card element not found');
        return;
    }

    console.log('Displaying subscription info for profile:', profile);

    const currentTier = profile.current_tier || 'Free Tier';
    const subscriptionEnd = profile.subscription_end;
    const subscriptionStatus = profile.subscription_status || 'active';

    let planContent = `
        <h2>Current Plan: ${currentTier}</h2>
        <p><strong>Status:</strong> ${subscriptionStatus}</p>
    `;

    if (subscriptionEnd) {
        planContent += `<p><strong>Next Billing:</strong> ${formatTimestamp(subscriptionEnd)}</p>`;
    }

    if (currentTier === 'Free Tier') {
        planContent += `
            <p>You're currently on the free plan. Upgrade to unlock premium features!</p>
            <button onclick="window.loadPage('subscriptions')" class="button">Upgrade Now</button>
        `;
    } else {
        planContent += `
            <p>Thank you for being a ${currentTier} subscriber!</p>
        `;

        // Show auto-renewal and cancellation options for paid plans
        const autoRenewContainer = document.getElementById('auto-renew-container');
        const cancelContainer = document.getElementById('cancel-subscription-container');

        if (autoRenewContainer) autoRenewContainer.style.display = 'block';
        if (cancelContainer) cancelContainer.style.display = 'block';
    }

    planInfoCard.innerHTML = planContent;
    console.log('Subscription info displayed successfully');
}

function initializeManageSubscriptionButtons() {
    // Change Plan button
    const changePlanBtn = document.getElementById('changePlanBtn');
    if (changePlanBtn) {
        changePlanBtn.addEventListener('click', () => {
            loadPage('subscriptions');
        });
    }

    // Auto-renewal toggle
    const toggleAutoRenewBtn = document.getElementById('toggleAutoRenewBtn');
    if (toggleAutoRenewBtn) {
        toggleAutoRenewBtn.textContent = 'Manage Auto-Renewal';
        toggleAutoRenewBtn.addEventListener('click', () => {
            showModal({
                message: 'Auto-renewal management is coming soon! Contact support for assistance.',
                confirmText: 'OK'
            });
        });
    }

    // Cancel subscription button
    const cancelSubscriptionBtn = document.getElementById('cancelSubscriptionBtn');
    if (cancelSubscriptionBtn) {
        cancelSubscriptionBtn.addEventListener('click', () => {
            showModal({
                message: 'Are you sure you want to cancel your subscription? You will lose access to premium features at the end of your billing period.',
                confirmText: 'Yes, Cancel',
                cancelText: 'Keep Subscription',
                onConfirm: () => {
                    handleSubscriptionCancellation();
                }
            });
        });
    }
}

async function handleSubscriptionCancellation() {
    try {
        showSpinner();

        // Update subscription status to cancelled
        const { error } = await supabase
            .from('user_profiles')
            .update({
                subscription_status: 'cancelled',
                updated_at: new Date().toISOString()
            })
            .eq('id', currentUser.id);

        if (error) {
            console.error('Error cancelling subscription:', error);
            showModal({
                message: 'Error cancelling subscription. Please try again or contact support.',
                confirmText: 'OK'
            });
            return;
        }

        showModal({
            message: 'Your subscription has been cancelled. You will retain access to premium features until the end of your current billing period.',
            confirmText: 'OK',
            onConfirm: () => {
                // Reload the page to show updated status
                loadManageSubscriptionInfo();
            }
        });

    } catch (error) {
        console.error('Error handling cancellation:', error);
        showModal({
            message: 'An error occurred. Please try again.',
            confirmText: 'OK'
        });
    } finally {
        hideSpinner();
    }
}

async function loadSubscriptionInfo() {
    if (!currentUser) {
        console.log('No user authenticated, skipping subscription info load');
        return;
    }

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

function initializeLeagueTabs() {
    console.log('Initializing league tabs...');
    const tabButtons = document.querySelectorAll('.tab-btn[data-tab]');
    const tabContents = document.querySelectorAll('.tab-content');

    console.log('Found tab buttons:', tabButtons.length);
    console.log('Found tab contents:', tabContents.length);

    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            const targetTab = button.getAttribute('data-tab');
            console.log('Tab clicked:', targetTab);

            // Update active button
            tabButtons.forEach(btn => btn.classList.remove('active'));
            button.classList.add('active');

            // Update active content
            tabContents.forEach(content => {
                content.classList.remove('active');
                if (content.id === targetTab) {
                    content.classList.add('active');
                    console.log('Activated tab content:', targetTab);
                }
            });
        });
    });
}

function initializeSubscriptionButtons() {
    console.log('Initializing subscription buttons...');
    const subscribeButtons = document.querySelectorAll('.subscribe-btn');
    console.log('Found subscription buttons:', subscribeButtons.length);

    subscribeButtons.forEach(button => {
        button.addEventListener('click', (e) => {
            e.preventDefault();
            console.log('Subscription button clicked');

            const tier = button.getAttribute('data-tier');
            const amount = button.getAttribute('data-amount');
            const period = button.getAttribute('data-period');

            console.log('Subscription details:', { tier, amount, period });

            if (tier === 'free') {
                showModal({
                    message: 'You are already on the free tier!',
                    confirmText: 'OK'
                });
                return;
            }

            // Handle subscription upgrade
            handleSubscriptionUpgrade(tier, amount, period);
        });
    });
}

async function handleSubscriptionUpgrade(tier, amount, period) {
    try {
        // Check if user is authenticated before showing upgrade modal
        if (!currentUser) {
            showModal({
                message: 'Please log in to upgrade your subscription.',
                confirmText: 'Login',
                cancelText: 'Cancel',
                onConfirm: () => {
                    redirectToLogin();
                }
            });
            return;
        }

        // For authenticated users, show payment flow
        showModal({
            message: `Upgrade to ${tier} tier for ₦${amount}/${period}?`,
            confirmText: 'Upgrade',
            cancelText: 'Cancel',
            onConfirm: () => {
                // Initialize Flutterwave payment
                initializePayment(tier, period, amount);
            }
        });
    } catch (error) {
        console.error('Error handling subscription upgrade:', error);
        showModal({
            message: 'Error processing subscription upgrade. Please try again.',
            confirmText: 'OK'
        });
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

    // Display "Referred By" information
    displayReferredBy();

    // Update referral list
    const referralListContainer = document.getElementById('referralListContainer');
    if (referralListContainer) {
        if (referrals.length === 0) {
            referralListContainer.innerHTML = '<p>No referrals yet. Share your code to get started!</p>';
        } else {
            const referralHTML = `
                <div class="table-responsive">
                    <table class="referral-table">
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Email</th>
                                <th>Tier</th>
                                <th>Joined</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${referrals.map(referral => `
                                <tr>
                                    <td data-label="Name">${referral.user_profiles?.display_name || 'User'}</td>
                                    <td data-label="Email">${referral.user_profiles?.email || ''}</td>
                                    <td data-label="Tier"><span class="tier-badge-small">${referral.user_profiles?.current_tier || 'Free Tier'}</span></td>
                                    <td data-label="Joined">${formatTimestamp(referral.created_at)}</td>
                                    <td data-label="Status">
                                        <span class="reward-status ${referral.reward_claimed ? 'claimed' : 'pending'}">
                                            ${referral.reward_claimed ? '✅ Rewarded' : '⏳ Pending'}
                                        </span>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            `;
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
                    showModal({
                        message: 'Failed to copy referral code',
                        confirmText: 'OK'
                    });
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
                        showModal({
                            message: 'Referral message copied to clipboard!',
                            confirmText: 'OK'
                        });
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
        showModal({ message: 'Please log in to save predictions.', confirmText: 'OK' });
        return;
    }

    if (!predictionId) {
        showModal({ message: 'Invalid prediction ID.', confirmText: 'OK' });
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

// Referral code copying is now handled in initializeReferralInteractions()

window.signOut = async function() {
    try {
        console.log('Starting sign out process...');
        showLoader();
        
        // Sign out from Supabase
        const { error } = await supabase.auth.signOut();
        
        if (error) {
            console.error('Supabase sign out error:', error);
            hideLoader();
            
            // Show error and still proceed with local cleanup
            alert('Sign out encountered an issue, but local data will be cleared.');
        }
        
        // Clear local data regardless of Supabase error
        try {
            localStorage.clear();
            sessionStorage.clear();
        } catch (storageError) {
            console.warn('Error clearing storage:', storageError);
        }
        
        // Reset global variables
        currentUser = null;
        verifiedTier = null;
        
        hideLoader();
        
        console.log('Sign out successful, redirecting to login...');
        
        // Redirect to login page using absolute path from root
        window.location.href = '/Auth/login.html';
        
    } catch (error) {
        console.error('Unexpected error during sign out:', error);
        hideLoader();
        
        // Force cleanup and redirect even on error
        try {
            localStorage.clear();
            sessionStorage.clear();
        } catch (e) {
            console.warn('Error clearing storage:', e);
        }
        
        alert('Sign out completed with errors. Redirecting to login...');
        window.location.href = '/Auth/login.html';
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

function detectAdBlocker() {
    try {
        console.log('🕵️ Checking for adblocker...');

        // Create a test ad element
        const testAd = document.createElement('div');
        testAd.innerHTML = '&nbsp;';
        testAd.className = 'adsbox adsbygoogle';
        testAd.style.position = 'absolute';
        testAd.style.left = '-9999px';
        testAd.style.width = '1px';
        testAd.style.height = '1px';

        document.body.appendChild(testAd);

        setTimeout(() => {
            try {
                const isBlocked = testAd.offsetHeight === 0 ||
                                 testAd.offsetWidth === 0 ||
                                 testAd.style.display === 'none' ||
                                 testAd.style.visibility === 'hidden';

                if (document.body.contains(testAd)) {
                    document.body.removeChild(testAd);
                }

                if (isBlocked || !window.adsbygoogle) {
                    console.log('🚫 Adblocker detected');
                    adblockerDetected = true;
                    showAdBlockerMessage();
                } else {
                    console.log('✅ No adblocker detected');
                    adblockerDetected = false;
                }
            } catch (err) {
                console.error('Error during adblocker detection:', err);
            }
        }, 100);
    } catch (err) {
        console.error('Error in detectAdBlocker:', err);
    }
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

// ===== Username Update Function =====
async function updateUsername(newUsername) {
    try {
        showSpinner();

        // First check if username is already taken
        const { data: existingUser, error: checkError } = await supabase
            .from('user_profiles')
            .select('id')
            .eq('username', newUsername)
            .neq('id', currentUser.id)
            .maybeSingle();

        if (checkError) {
            console.warn('Error checking username availability:', checkError);
        }

        if (existingUser) {
            hideSpinner();
            showModal({
                message: 'This username is already taken. Please choose a different one.',
                confirmText: 'OK'
            });
            return;
        }

        const { error } = await supabase
            .from('user_profiles')
            .update({
                display_name: newUsername,
                username: newUsername,
                updated_at: new Date().toISOString()
            })
            .eq('id', currentUser.id);

        if (error) {
            console.error('Error updating username:', error);
            
            // Handle specific error cases
            let errorMessage = 'Failed to update username. Please try again.';
            if (error.code === '23505') {
                errorMessage = 'This username is already taken. Please choose a different one.';
            } else if (error.message) {
                errorMessage = error.message;
            }
            
            showModal({
                message: errorMessage,
                confirmText: 'OK'
            });
            return;
        }

        // Update the display immediately
        const userNameElement = document.getElementById('userName');
        if (userNameElement) {
            userNameElement.textContent = newUsername;
        }

        showModal({
            message: 'Username updated successfully!',
            confirmText: 'OK'
        });

    } catch (error) {
        console.error('Error updating username:', error);
        showModal({
            message: 'Failed to update username. Please try again.',
            confirmText: 'OK'
        });
    } finally {
        hideSpinner();
    }
}

// Theme initialization is now handled by ui.js

// ===== Modal Helper Function =====
function showModal(options) {
    try {
        // Validate options
        if (!options || typeof options !== 'object') {
            console.error('Invalid modal options');
            return;
        }

        const modal = document.createElement('div');
        modal.className = 'modal-overlay';

        const inputFieldHTML = options.inputType ? `
            <div class="modal-input-wrapper">
                <input type="${options.inputType}" class="modal-input" value="${options.inputValue || ''}" placeholder="${options.inputPlaceholder || ''}">
            </div>
        ` : '';

        // Escape HTML to prevent XSS
        const safeMessage = options.message ? String(options.message).replace(/</g, '&lt;').replace(/>/g, '&gt;') : '';

        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-message">${safeMessage}</div>
                ${inputFieldHTML}
                <div class="modal-actions">
                    ${options.cancelText ? `<button class="btn-cancel">${options.cancelText}</button>` : ''}
                    <button class="btn-confirm ${options.confirmClass || 'btn-primary'}">${options.confirmText || 'OK'}</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const confirmBtn = modal.querySelector('.btn-confirm');
        const cancelBtn = modal.querySelector('.btn-cancel');
        const inputField = modal.querySelector('.modal-input');

        // Focus input if it exists
        if (inputField) {
            setTimeout(() => {
                try {
                    inputField.focus();
                } catch (focusError) {
                    console.warn('Could not focus input field:', focusError);
                }
            }, 100);
        }

        const cleanup = () => {
            try {
                if (modal && modal.parentNode) {
                    document.body.removeChild(modal);
                }
            } catch (cleanupError) {
                console.warn('Error cleaning up modal:', cleanupError);
            }
        };

        if (confirmBtn) {
            confirmBtn.addEventListener('click', () => {
                try {
                    const inputValue = inputField ? inputField.value : null;
                    cleanup();
                    if (options.onConfirm) {
                        if (inputField) {
                            options.onConfirm(inputValue);
                        } else {
                            options.onConfirm();
                        }
                    }
                } catch (confirmError) {
                    console.error('Error in modal confirm:', confirmError);
                    cleanup();
                }
            });
        }

        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                try {
                    cleanup();
                    if (options.onCancel) options.onCancel();
                } catch (cancelError) {
                    console.error('Error in modal cancel:', cancelError);
                }
            });
        }

        // Handle Enter key for input
        if (inputField) {
            inputField.addEventListener('keypress', (e) => {
                if (e.key === 'Enter' && confirmBtn) {
                    confirmBtn.click();
                }
            });
        }

        // Close on overlay click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                try {
                    cleanup();
                    if (options.onCancel) options.onCancel();
                } catch (overlayError) {
                    console.error('Error in modal overlay click:', overlayError);
                }
            }
        });

    } catch (error) {
        console.error('Error creating modal:', error);
    }
}

console.log('✅ StatWise main application loaded with Supabase integration!');


async function displayReferredBy() {
    const referredByCard = document.getElementById('referredByCard');
    const referredByText = document.getElementById('referredByText');
    
    if (!referredByCard || !referredByText) return;

    try {
        // Get current user's profile to check if they were referred
        const { data: { user } } = await supabase.auth.getUser();
        if (!user) return;

        const { data: currentProfile, error: profileError } = await supabase
            .from('user_profiles')
            .select('referred_by')
            .eq('id', user.id)
            .single();

        if (profileError) {
            console.warn('Error fetching user profile:', profileError);
            return;
        }

        // If user was referred, get the referrer's information
        if (currentProfile?.referred_by) {
            const { data: referrerProfile, error: referrerError } = await supabase
                .from('user_profiles')
                .select('display_name, username, email')
                .eq('id', currentProfile.referred_by)
                .single();

            if (referrerError) {
                console.warn('Error fetching referrer profile:', referrerError);
                return;
            }

            // Get the referral code used
            const { data: referralData, error: referralError } = await supabase
                .from('referrals')
                .select('referral_code')
                .eq('referred_id', user.id)
                .single();

            const referrerName = referrerProfile?.display_name || referrerProfile?.username || 'Unknown User';
            const usedCode = referralData?.referral_code || 'N/A';

            referredByText.innerHTML = `
                <strong>${referrerName}</strong> referred you using code <strong>${usedCode}</strong>
                <br>
                <span style="font-size: 13px; color: #666;">Thank you for joining through their referral!</span>
            `;
            referredByCard.style.display = 'block';
        }
    } catch (error) {
        console.error('Error displaying referred by information:', error);
    }
}

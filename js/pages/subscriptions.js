import { supabase } from '../../env.js';
import { showModal, formatTimestamp } from '../../utils.js';

let currentUser;

export async function initializeSubscriptionsPage(user) {
    currentUser = user;
    console.log('Initializing subscriptions page...');
    await loadSubscriptionInfo();
    initializeSubscriptionTabs();
    initializeSubscriptionButtons();
    console.log('Subscriptions page initialized successfully');
}

export async function initializeManageSubscriptionPage(user) {
    currentUser = user;
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

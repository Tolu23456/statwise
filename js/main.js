// main.js - StatWise PWA with Supabase-only implementation
import { supabase } from '../env.js';
import { showLoader, hideLoader, showSpinner, hideSpinner } from '../Loader/loader.js';
import { initInteractiveBackground, initializeTheme } from '../ui.js';
import { initializeAppSecurity, manageInitialPageLoad } from '../manager.js';
import { formatTimestamp, addHistoryUnique } from '../utils.js';
import { initializeSupabaseAuth } from './auth.js';
import { checkPaymentRedirect } from './payments.js';
import { verifiedTier, initializeProfileInteractions, loadUserProfile, updateUsername, handleAvatarUpload } from './user.js';
import { initializeAdSystemForUser } from './ads.js';


// ===== Global Variables =====
const main = document.querySelector("main");
const navButtons = document.querySelectorAll(".bottom-nav button");
const defaultPage = "home";

// Initialize the app
initializeTheme(); // Initialize theme system
initializeSupabaseAuth(loadPage);
checkPaymentRedirect();
// Ad system will be initialized after user tier is loaded

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
            const response = await fetch(`../Pages/${page}.html`);
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
    // Populate per-league tab content from matches table
    await populateLeagueTabsFromMatches();
    // Initialize league tabs
    initializeLeagueTabs();
    // Initialize advanced filters
    initializeAdvancedFilters();
    // Initialize search/command input on the home page
    initializeSearchBar();
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

        allPredictions = predictions || [];
        displayPredictions(allPredictions);
    } catch (error) {
        console.error('Error loading predictions:', error);
    }
}

function displayPredictions(predictions) {
    // Prefer the predictions container inside the currently active tab; fall back to the global id.
    const container = document.querySelector('.tab-content.active .predictions-container') || document.getElementById('predictions-container');
    if (!container) return;

    // if (predictions.length === 0) {
    //     container.innerHTML = `
    //         <div class="no-predictions">
    //             <h3>No predictions available</h3>
    //             <p>Check back later for new AI predictions!</p>
    //         </div>
    //     `;
    //     return;
    // }

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

        // Support both 'inputValue' (preferred) and legacy 'inputVal'
        const modalInputValue = options.inputValue ?? options.inputVal ?? '';
        const inputFieldHTML = options.inputType ? `
            <div class="modal-input-wrapper">
                <input type="${options.inputType}" class="modal-input" value="${modalInputValue}" placeholder="${options.inputPlaceholder || ''}">
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

window.initializeApp = initializeApp;

console.log('✅ StatWise main application loaded with Supabase integration!');

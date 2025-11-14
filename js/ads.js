// js/ads.js
import { verifiedTier } from './user.js';
import { loadPage } from './main.js';

let adsLoaded = false;
let adblockerDetected = false;

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

export { initializeAdSystemForUser, checkConsentAndLoadAds, loadAdsForFreeUsers, detectAdBlocker, showAdBlockerMessage, hideAdBlockerMessage };

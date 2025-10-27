/**
 * StatWise Google Certified CMP Integration
 * Uses Google's Funding Choices (officially certified CMP)
 * Compliant with IAB TCF v2.2 and Google requirements
 */

// Global error handler to suppress non-critical errors from third-party scripts
(function() {
    // Filter console.error to suppress benign third-party errors
    const originalConsoleError = console.error;
    console.error = function(...args) {
        // Check if this is the "uncaught exception" error from Google scripts
        if (args.length > 0 && args[0] && typeof args[0] === 'object') {
            const message = args[0].message || JSON.stringify(args[0]);
            if (message.includes('uncaught exception') || message.includes('error was not an error object')) {
                // Suppress this benign error - it's from Google's third-party scripts
                return;
            }
        }
        // Log all other errors normally
        originalConsoleError.apply(console, args);
    };
    
    const originalErrorHandler = window.onerror;
    
    window.onerror = function(message, source, lineno, colno, error) {
        // Suppress "uncaught exception" errors from third-party scripts
        if (message && typeof message === 'string' && message.includes('uncaught exception')) {
            return true; // Prevent default error handling
        }
        
        // Check if error is from Google CMP or ads scripts
        if (source && (source.includes('fundingchoicesmessages') || source.includes('adsbygoogle'))) {
            return true;
        }
        
        // Call original handler if it exists
        if (originalErrorHandler) {
            return originalErrorHandler(message, source, lineno, colno, error);
        }
        
        return false;
    };
    
    // Catch unhandled promise rejections
    window.addEventListener('unhandledrejection', function(event) {
        if (!event.reason || typeof event.reason !== 'object' || !event.reason.stack) {
            event.preventDefault();
        }
    });
})();

class GoogleCertifiedCMP {
    constructor() {
        this.consentKey = 'statwise_consent_v2';
        this.cmpLoaded = false;
        this.tcfApiAvailable = false;
        this.init();
    }

    init() {
        // Initialize Google Consent Mode v2 with region-specific defaults
        this.initializeConsentMode();
        
        // Load Google's certified CMP (Funding Choices)
        this.loadGoogleCMP();
        
        // Setup IAB TCF API integration
        this.setupTCFIntegration();
    }

    initializeConsentMode() {
        // Ensure gtag is available
        if (typeof gtag === 'undefined') {
            window.dataLayer = window.dataLayer || [];
            function gtag(){dataLayer.push(arguments);}
            window.gtag = gtag;
            gtag('js', new Date());
        }

        // Set region-specific default consent states
        // EEA, UK, and Switzerland require consent banners
        gtag('consent', 'default', {
            'ad_storage': 'denied',
            'ad_user_data': 'denied',
            'ad_personalization': 'denied',
            'analytics_storage': 'denied',
            'functionality_storage': 'denied',
            'personalization_storage': 'denied',
            'security_storage': 'granted',
            'wait_for_update': 2000,
            'region': ['AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'GB', 'CH']
        });

        // For other regions, grant consent by default
        gtag('consent', 'default', {
            'ad_storage': 'granted',
            'ad_user_data': 'granted',
            'ad_personalization': 'granted',
            'analytics_storage': 'granted',
            'functionality_storage': 'granted',
            'personalization_storage': 'granted',
            'security_storage': 'granted',
            'region': ['US', 'CA', 'AU', 'JP', 'KR', 'IN', 'BR', 'MX', 'ZA', 'EG', 'NG', 'KE', 'MA', 'TN', 'GH', 'SN', 'CI', 'UG', 'TZ', 'ZW', 'ZM', 'MW', 'RW', 'BW', 'NA', 'SZ', 'LS', 'MG', 'MU', 'SC']
        });

        console.log('StatWise Google Consent Mode v2 initialized with regional defaults');
    }

    loadGoogleCMP() {
        // Load Google's Funding Choices (officially certified CMP)
        window.googletag = window.googletag || {cmd: []};
        
        // Configure Funding Choices for your AdSense publisher ID
        const script = document.createElement('script');
        script.async = true;
        script.src = 'https://fundingchoicesmessages.google.com/i/pub-9868946535437166?ers=1';
        script.onload = () => {
            console.log('✅ Google Funding Choices CMP loaded');
            this.cmpLoaded = true;
        };
        script.onerror = () => {
            console.warn('⚠️ Google CMP failed to load, falling back to manual consent');
            this.initializeFallbackConsent();
        };
        
        document.head.appendChild(script);

        // Configure CMP behavior
        window.googletag.cmd.push(() => {
            window.googletag.pubads().enableAsyncRendering();
            window.googletag.pubads().enableSingleRequest();
            window.googletag.enableServices();
        });
    }

    setupTCFIntegration() {
        // Check if __tcfapi is available
        const checkTCFAPI = () => {
            if (typeof window.__tcfapi === 'function') {
                this.tcfApiAvailable = true;
                this.initializeTCFListeners();
            } else {
                // Wait for TCF API to be available
                setTimeout(checkTCFAPI, 100);
            }
        };
        
        checkTCFAPI();
    }

    initializeTCFListeners() {
        console.log('✅ TCF API available, setting up consent listeners');
        
        // Listen for TCF consent changes
        window.__tcfapi('addEventListener', 2, (tcData, success) => {
            if (success && tcData) {
                console.log('🍪 TCF consent event:', tcData.eventStatus);
                this.handleTCFConsentUpdate(tcData);
            }
        });

        // Get initial consent status
        window.__tcfapi('getTCData', 2, (tcData, success) => {
            if (success && tcData) {
                this.handleTCFConsentUpdate(tcData);
            }
        });
    }

    handleTCFConsentUpdate(tcData) {
        // Convert TCF data to our format
        const consentData = this.convertTCFToConsent(tcData);
        
        console.log('🍪 Converting TCF consent:', consentData);
        
        // Update Google Consent Mode
        gtag('consent', 'update', {
            'ad_storage': consentData.ad_storage,
            'ad_user_data': consentData.ad_user_data,
            'ad_personalization': consentData.ad_personalization,
            'analytics_storage': consentData.analytics_storage,
            'functionality_storage': consentData.functionality_storage,
            'personalization_storage': consentData.personalization_storage
        });

        // Store consent locally
        this.storeConsent(consentData);
        
        // Trigger ad system update
        this.triggerAdSystemUpdate(consentData);
    }

    convertTCFToConsent(tcData) {
        // Check for Google vendor consent and purposes
        const googleVendorConsent = tcData.vendor && tcData.vendor.consents && tcData.vendor.consents[755]; // Google vendor ID
        const purpose1 = tcData.purpose && tcData.purpose.consents && tcData.purpose.consents[1]; // Store and/or access information
        const purpose3 = tcData.purpose && tcData.purpose.consents && tcData.purpose.consents[3]; // Create personalized ads profile
        const purpose4 = tcData.purpose && tcData.purpose.consents && tcData.purpose.consents[4]; // Select personalized ads
        
        return {
            ad_storage: (purpose1 && googleVendorConsent) ? 'granted' : 'denied',
            ad_user_data: (purpose1 && googleVendorConsent) ? 'granted' : 'denied',
            ad_personalization: (purpose3 && purpose4 && googleVendorConsent) ? 'granted' : 'denied',
            analytics_storage: (purpose1 && googleVendorConsent) ? 'granted' : 'denied',
            functionality_storage: 'granted', // Always granted for basic functionality
            personalization_storage: (purpose3 && googleVendorConsent) ? 'granted' : 'denied',
            tcfStatus: tcData.eventStatus,
            cmpStatus: tcData.cmpStatus
        };
    }

    handleConsentUpdate(consentData) {
        console.log('🍪 Google CMP consent updated:', consentData);
        
        // Update Google Consent Mode
        gtag('consent', 'update', {
            'ad_storage': consentData.ad_storage || 'denied',
            'ad_user_data': consentData.ad_user_data || 'denied',
            'ad_personalization': consentData.ad_personalization || 'denied',
            'analytics_storage': consentData.analytics_storage || 'denied',
            'functionality_storage': consentData.functionality_storage || 'granted',
            'personalization_storage': consentData.personalization_storage || 'denied'
        });

        // Store consent locally
        this.storeConsent(consentData);
        
        // Trigger ad system update
        this.triggerAdSystemUpdate(consentData);
    }

    storeConsent(consent) {
        const consentData = {
            ...consent,
            timestamp: Date.now(),
            cmp: 'google-funding-choices'
        };

        try {
            localStorage.setItem(this.consentKey, JSON.stringify(consentData));
        } catch (e) {
            console.warn('Could not store consent data:', e);
        }
    }

    getStoredConsent() {
        try {
            const stored = localStorage.getItem(this.consentKey);
            return stored ? JSON.parse(stored) : null;
        } catch (e) {
            return null;
        }
    }

    triggerAdSystemUpdate(consent) {
        // Dispatch event for other systems (backward compatibility)
        const event = new CustomEvent('consentUpdated', { 
            detail: consent 
        });
        window.dispatchEvent(event);

        // Direct integration with StatWise ad system
        if (consent.ad_storage === 'granted' && window.initializeAdSystemForUser) {
            console.log('🎯 Triggering ad system for consented user');
            window.initializeAdSystemForUser();
        } else {
            console.log('🚫 Ad system not triggered - consent denied or function not available');
        }
        
        console.log('📡 Consent update event dispatched:', consent);
    }

    checkAndApplyConsent() {
        const storedConsent = this.getStoredConsent();
        if (storedConsent) {
            // Apply previously stored consent
            this.handleConsentUpdate(storedConsent);
        }
    }

    // Fallback consent for when Google CMP fails to load
    initializeFallbackConsent() {
        console.log('📋 Initializing privacy-compliant fallback consent system');
        
        // Simple regional-only banner (no IP geolocation)
        this.showFallbackConsentBanner();
    }

    showFallbackConsentBanner() {
        // Only show if no consent has been given yet
        if (this.getStoredConsent()) return;

        const banner = document.createElement('div');
        banner.id = 'fallback-consent-banner';
        banner.style.cssText = `
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: #1a1a1a;
            color: white;
            padding: 20px;
            text-align: center;
            z-index: 10000;
            box-shadow: 0 -2px 10px rgba(0,0,0,0.3);
        `;
        
        banner.innerHTML = `
            <div style="max-width: 1200px; margin: 0 auto;">
                <p style="margin: 0 0 15px 0;"><strong>🍪 Privacy Notice:</strong> We use cookies and similar technologies to enhance your experience, analyze usage, and show relevant ads. Choose your preferences below.</p>
                <button id="accept-fallback-consent" style="background: #00d4aa; color: white; border: none; padding: 12px 24px; border-radius: 8px; margin: 0 10px; cursor: pointer; font-weight: 600;">✓ Accept All</button>
                <button id="reject-fallback-consent" style="background: #ff6b6b; color: white; border: none; padding: 12px 24px; border-radius: 8px; margin: 0 10px; cursor: pointer; font-weight: 600;">✗ Reject All</button>
                <button id="essential-only-consent" style="background: #666; color: white; border: none; padding: 12px 24px; border-radius: 8px; margin: 0 10px; cursor: pointer; font-weight: 600;">Essential Only</button>
            </div>
        `;
        
        document.body.appendChild(banner);
        
        // Handle consent choices
        banner.querySelector('#accept-fallback-consent').addEventListener('click', () => {
            this.handleTCFConsentUpdate({
                eventStatus: 'useractioncomplete',
                purpose: { consents: { 1: true, 3: true, 4: true } },
                vendor: { consents: { 755: true } } // Google vendor
            });
            banner.remove();
        });
        
        banner.querySelector('#reject-fallback-consent').addEventListener('click', () => {
            this.handleTCFConsentUpdate({
                eventStatus: 'useractioncomplete',
                purpose: { consents: { 1: false, 3: false, 4: false } },
                vendor: { consents: { 755: false } }
            });
            banner.remove();
        });
        
        banner.querySelector('#essential-only-consent').addEventListener('click', () => {
            this.handleTCFConsentUpdate({
                eventStatus: 'useractioncomplete',
                purpose: { consents: { 1: false, 3: false, 4: false } },
                vendor: { consents: { 755: false } }
            });
            banner.remove();
        });
    }

    // Public methods for manual consent management
    grantAllConsent() {
        this.handleConsentUpdate({
            ad_storage: 'granted',
            ad_user_data: 'granted',
            ad_personalization: 'granted',
            analytics_storage: 'granted',
            functionality_storage: 'granted',
            personalization_storage: 'granted'
        });
    }

    revokeAllConsent() {
        this.handleConsentUpdate({
            ad_storage: 'denied',
            ad_user_data: 'denied',
            ad_personalization: 'denied',
            analytics_storage: 'denied',
            functionality_storage: 'granted',
            personalization_storage: 'denied'
        });
    }

    resetConsent() {
        localStorage.removeItem(this.consentKey);
        location.reload();
    }

    getConsentStatus() {
        return this.getStoredConsent();
    }
}

// Initialize Google certified CMP when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Make CMP globally available
    window.googleCMP = new GoogleCertifiedCMP();
    
    // Backward compatibility with proper TCF integration
    window.consentManager = {
        getConsentStatus: () => {
            const status = window.googleCMP?.getConsentStatus();
            console.log('📊 Getting consent status:', status);
            return status;
        },
        resetConsent: () => window.googleCMP?.resetConsent(),
        grantAllConsent: () => window.googleCMP?.grantAllConsent(),
        revokeAllConsent: () => window.googleCMP?.revokeAllConsent(),
        // Additional methods for compatibility
        setConsentChoices: () => console.log('🔧 Consent choices - using Google CMP interface'),
        showBanner: () => console.log('🏠 Banner management handled by Google CMP')
    };
});

// Global functions for testing
window.grantAllConsent = () => window.googleCMP?.grantAllConsent();
window.revokeAllConsent = () => window.googleCMP?.revokeAllConsent();
window.resetConsent = () => window.googleCMP?.resetConsent();
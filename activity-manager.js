// Activity Manager - Handles app focus detection and inactive state
class ActivityManager {
    constructor() {
        this.isActive = true;
        this.inactiveContainer = null;
        this.inactiveThreshold = 5000; // 5 seconds of inactivity before showing inactive page
        this.timeoutId = null;
        this.lastActivityTime = Date.now();
        this.isEnabled = this.getToggleSetting();
        
        this.init();
    }
    
    init() {
        this.createInactiveContainer();
        this.setupEventListeners();
        this.startActivityMonitoring();
        
        // Make this available globally for the inactive page
        window.activityManager = this;
        
        console.log('🎯 Activity Manager initialized successfully');
    }
    
    createInactiveContainer() {
        // Create the inactive container and inject the inactive page content
        this.inactiveContainer = document.createElement('div');
        this.inactiveContainer.id = 'activity-inactive-overlay';
        this.inactiveContainer.style.display = 'none';
        
        // Load inactive page content
        fetch('./Pages/inactive.html')
            .then(response => response.text())
            .then(html => {
                this.inactiveContainer.innerHTML = html;
                document.body.appendChild(this.inactiveContainer);
            })
            .catch(error => {
                console.error('Failed to load inactive page:', error);
                // Fallback content
                this.inactiveContainer.innerHTML = `
                    <div class="inactive-container show">
                        <div class="inactive-content">
                            <div class="pulse-animation">
                                <div class="logo-icon">⭐</div>
                            </div>
                            <h1 class="inactive-title">StatWise is waiting...</h1>
                            <p class="inactive-message">Click to return to your predictions</p>
                            <button class="return-btn" onclick="window.activityManager.returnToApp()">
                                Return to StatWise
                            </button>
                        </div>
                    </div>
                `;
                document.body.appendChild(this.inactiveContainer);
            });
    }
    
    setupEventListeners() {
        // Page Visibility API - detects tab switching
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                this.scheduleInactive();
            } else {
                this.markActive();
            }
        });
        
        // Window focus/blur events
        window.addEventListener('focus', () => {
            this.markActive();
        });
        
        window.addEventListener('blur', () => {
            this.scheduleInactive();
        });
        
        // Mouse and keyboard activity
        ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart', 'click'].forEach(event => {
            document.addEventListener(event, () => {
                this.markActive();
            }, { passive: true });
        });
        
        // Mobile-specific events
        document.addEventListener('touchend', () => {
            this.markActive();
        }, { passive: true });
    }
    
    startActivityMonitoring() {
        // Monitor for extended periods of inactivity
        setInterval(() => {
            const timeSinceActivity = Date.now() - this.lastActivityTime;
            
            // If inactive for more than 30 seconds and not already showing inactive page
            if (timeSinceActivity > 30000 && this.isActive && !document.hidden) {
                this.scheduleInactive();
            }
        }, 5000); // Check every 5 seconds
    }
    
    scheduleInactive() {
        if (!this.isActive || !this.isEnabled) return;
        
        // Clear any existing timeout
        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
        }
        
        // Schedule inactive state after threshold
        this.timeoutId = setTimeout(() => {
            this.showInactivePage();
        }, this.inactiveThreshold);
    }
    
    markActive() {
        this.lastActivityTime = Date.now();
        
        // Clear inactive timeout
        if (this.timeoutId) {
            clearTimeout(this.timeoutId);
            this.timeoutId = null;
        }
        
        // If we were inactive, show welcome back and return to app
        if (!this.isActive) {
            this.showWelcomeBack();
            setTimeout(() => {
                this.hideInactivePage();
            }, 1500); // Show welcome back for 1.5 seconds
        }
        
        this.isActive = true;
    }
    
    showInactivePage() {
        if (!this.isActive || !this.isEnabled) return;
        
        // Hide any visible loader first to prevent conflicts
        const loader = document.getElementById('globalLoader');
        if (loader && loader.style.display !== 'none') {
            loader.style.display = 'none';
            console.log('🔄 Loader hidden by activity manager');
        }
        
        this.isActive = false;
        
        if (this.inactiveContainer) {
            this.inactiveContainer.style.display = 'block';
            
            // Add show class for animation
            setTimeout(() => {
                const container = this.inactiveContainer.querySelector('.inactive-container');
                if (container) {
                    container.classList.add('show');
                }
            }, 10);
            
            console.log('📱 Inactive page displayed');
            
            // Initialize inactive page interactions safely
            this.initializeInactivePageEvents();
        }
    }
    
    hideInactivePage() {
        if (this.inactiveContainer) {
            const container = this.inactiveContainer.querySelector('.inactive-container');
            if (container) {
                container.classList.remove('show');
            }
            
            // Hide after animation completes
            setTimeout(() => {
                this.inactiveContainer.style.display = 'none';
            }, 300);
            
            console.log('📱 Returned to active app');
        }
        
        this.isActive = true;
    }
    
    showWelcomeBack() {
        if (this.inactiveContainer) {
            const welcomeMessage = this.inactiveContainer.querySelector('.welcome-back-message');
            const regularContent = this.inactiveContainer.querySelector('.inactive-message');
            const stats = this.inactiveContainer.querySelector('.inactive-stats');
            
            if (welcomeMessage && regularContent && stats) {
                // Hide regular content
                regularContent.style.display = 'none';
                stats.style.display = 'none';
                
                // Show welcome back message
                welcomeMessage.style.display = 'block';
            }
        }
    }
    
    returnToApp() {
        this.markActive();
        this.hideInactivePage();
    }
    
    // Force show inactive page (for testing)
    forceInactive() {
        this.showInactivePage();
    }
    
    // Get current activity status
    getActivityStatus() {
        return {
            isActive: this.isActive,
            lastActivity: this.lastActivityTime,
            timeSinceActivity: Date.now() - this.lastActivityTime,
            isEnabled: this.isEnabled
        };
    }
    
    // Toggle setting management
    getToggleSetting() {
        const setting = localStorage.getItem('inactivePageEnabled');
        return setting === null ? true : setting === 'true'; // Default to enabled
    }
    
    setToggleSetting(enabled) {
        this.isEnabled = enabled;
        localStorage.setItem('inactivePageEnabled', enabled.toString());
        
        // If disabled while inactive, hide the page
        if (!enabled && !this.isActive) {
            this.hideInactivePage();
        }
        
        console.log(`🎯 Inactive page feature ${enabled ? 'enabled' : 'disabled'}`);
    }
    
    // Public method to enable/disable the feature
    enableInactivePage(enabled) {
        this.setToggleSetting(enabled);
    }
    
    // Safe event initialization for inactive page
    initializeInactivePageEvents() {
        if (!this.inactiveContainer) return;
        
        // Find and bind return button safely
        const returnBtn = this.inactiveContainer.querySelector('.return-btn');
        if (returnBtn) {
            returnBtn.addEventListener('click', () => this.returnToApp());
        }
        
        // Find and bind container click event safely
        const container = this.inactiveContainer.querySelector('.inactive-container');
        if (container) {
            container.addEventListener('click', () => this.returnToApp());
        }
        
        // Start away timer if there's a timer element
        const timerElement = this.inactiveContainer.querySelector('[data-away-timer]');
        if (timerElement && typeof window.startAwayTimer === 'function') {
            try {
                window.startAwayTimer();
            } catch (error) {
                console.warn('Away timer function not available:', error);
            }
        }
        
        console.log('✅ Inactive page events initialized safely');
    }
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        new ActivityManager();
    });
} else {
    new ActivityManager();
}

export { ActivityManager };
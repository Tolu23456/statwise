// auth.js - Supabase Authentication
import { supabase } from '../env.js';
import { showLoader, hideLoader } from '../Loader/loader.js';
import { initializeTheme } from '../ui.js';

// Global variables for auth page

// Initialize the auth page
document.addEventListener('DOMContentLoaded', function() {
    console.log('🔐 Initializing auth page...');

    // Add loading animation to auth card
    const authCard = document.querySelector('.auth-card');
    if (authCard) {
        authCard.classList.add('loading');
    }

    // Test Supabase connection
    testSupabaseConnection();

    initializeTheme(); // Initialize theme system

    // Initialize interactive background animation for auth pages
    console.log('🌀 Starting auth page background animation...');
    import('../ui.js').then(({ initAuthBackgroundAnimation }) => {
        if (window.authBackgroundCleanup) {
            window.authBackgroundCleanup(); // Clean up any existing animation
        }
        window.authBackgroundCleanup = initAuthBackgroundAnimation();
    });

    initializeAuthForms();
    initializeAuthAds(); // Initialize ads for auth page
    
    // Update theme icon after DOM is ready
    updateThemeIcon();

    console.log('✅ Auth page initialized successfully');
});

// Test Supabase connection
async function testSupabaseConnection() {
    try {
        console.log('🔗 Testing Supabase connection...');
        const { data, error } = await supabase.auth.getSession();
        if (error) {
            console.error('❌ Supabase connection error:', error);
        } else {
            console.log('✅ Supabase connected successfully');
            if (data.session) {
                console.log('👤 User session found:', data.session.user.email);
            }
        }
    } catch (error) {
        console.error('❌ Supabase connection test failed:', error);
    }
}

function initializeAuthForms() {
    // Login form
    const loginForm = document.getElementById('login-form');
    if (loginForm) {
        loginForm.addEventListener('submit', handleLogin);
    }

    // Signup form
    const signupForm = document.getElementById('signup-form');
    if (signupForm) {
        signupForm.addEventListener('submit', handleSignup);
    }

    // Forgot password form
    const forgotPasswordForm = document.getElementById('forgot-password-form');
    if (forgotPasswordForm) {
        forgotPasswordForm.addEventListener('submit', handleForgotPassword);
    }

    // Theme toggle button
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', handleThemeToggle);
        updateThemeIcon();
    }

    // Toggle password visibility for login
    const loginPasswordToggle = document.getElementById('login-password-toggle');
    if (loginPasswordToggle) {
        loginPasswordToggle.addEventListener('click', () => togglePasswordVisibility('login-password'));
    }

    // Toggle password visibility for signup
    const signupPasswordToggle = document.getElementById('signup-password-toggle');
    if (signupPasswordToggle) {
        signupPasswordToggle.addEventListener('click', () => togglePasswordVisibility('signup-password'));
    }

    // Toggle password visibility for signup confirm
    const confirmPasswordToggle = document.getElementById('confirm-password-toggle');
    if (confirmPasswordToggle) {
        confirmPasswordToggle.addEventListener('click', () => togglePasswordVisibility('signup-password-confirm'));
    }
    // Add smooth transitions for auth page navigation
    initializeAuthNavigation();

    // Password strength indicator for signup
    const signupPassword = document.getElementById('signup-password');
    if (signupPassword) {
        signupPassword.addEventListener('input', updatePasswordStrength);
    }

    // Real-time referral code validation
    const signupReferralCode = document.getElementById('signup-referral');
    if (signupReferralCode) {
        signupReferralCode.addEventListener('input', debounce(validateReferralCodeInput, 500));
    }
}

// Debounce function for input validation
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Validate referral code input in real-time
async function validateReferralCodeInput() {
    const referralCodeInput = document.getElementById('signup-referral');
    const statusElement = document.getElementById('referral-code-status');

    if (!referralCodeInput || !statusElement) return;

    const code = referralCodeInput.value.trim().toUpperCase();

    // Clear validation if input is empty
    if (!code) {
        statusElement.classList.remove('show', 'success', 'error');
        referralCodeInput.style.borderColor = '';
        referralCodeInput.style.borderWidth = '';
        return;
    }

    // Only validate if code is exactly 8 characters (standard referral code length)
    if (code.length !== 8) {
        statusElement.classList.remove('show', 'success', 'error');
        referralCodeInput.style.borderColor = '';
        referralCodeInput.style.borderWidth = '';
        return;
    }

    try {
        const { data, error } = await supabase
            .from('referral_codes')
            .select('user_id, username')
            .eq('code', code)
            .eq('active', true)
            .single();

        if (error || !data) {
            // Invalid code - show error
            statusElement.textContent = '❌ Invalid referral code';
            statusElement.className = 'referral-name-display show error';
            referralCodeInput.style.borderColor = '#d9534f';
            referralCodeInput.style.borderWidth = '2px';
        } else {
            // Valid code - show success
            statusElement.textContent = `✓ Using ${data.username}'s referral code`;
            statusElement.className = 'referral-name-display show success';
            referralCodeInput.style.borderColor = '#28a745';
            referralCodeInput.style.borderWidth = '2px';
        }
    } catch (error) {
        console.warn('Error validating referral code:', error);
        statusElement.classList.remove('show', 'success', 'error');
        referralCodeInput.style.borderColor = '';
        referralCodeInput.style.borderWidth = '';
    }
}

function handleThemeToggle(e) {
    const button = e.target.closest('.theme-toggle');
    if (!button) return;

    // Determine target theme
    const currentTheme = localStorage.getItem('statwise-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';

    // Apply theme change with loading animation
    import('../ui.js').then(({ toggleTheme, initAuthBackgroundAnimation }) => {
        toggleTheme();
        updateThemeIcon(newTheme);

        // Reinitialize background animation with new theme
        if (window.authBackgroundCleanup) {
            window.authBackgroundCleanup();
        }
        window.authBackgroundCleanup = initAuthBackgroundAnimation();

        // Add loading animation to auth card
        const authCard = document.querySelector('.auth-card');
        if (authCard) {
            authCard.classList.remove('loading');
            setTimeout(() => {
                authCard.classList.add('loading');
            }, 10);
        }
    });
}

function updateThemeIcon(theme = null) {
    const themeIcon = document.querySelector('.theme-icon');
    if (!themeIcon) return;

    const currentTheme = theme || localStorage.getItem('statwise-theme') || 'light';
    themeIcon.textContent = currentTheme === 'dark' ? '☀️' : '🌙';
}

// Enhanced Error Handling Functions
function showErrorMessage(elementId, message) {
    const errorElement = document.getElementById(elementId);
    if (!errorElement) return;

    errorElement.textContent = message;
    errorElement.className = 'error-msg show';
    errorElement.classList.add('shake');

    setTimeout(() => {
        errorElement.classList.remove('shake');
    }, 500);

    // Auto-hide error messages after 5 seconds
    setTimeout(() => {
        if (errorElement.classList.contains('show')) {
            errorElement.classList.remove('show');
        }
    }, 5000);
}

function showSuccessMessage(elementId, message) {
    const errorElement = document.getElementById(elementId);
    if (!errorElement) return;

    errorElement.textContent = message;
    errorElement.className = 'error-msg success show';

    // Auto-hide success messages after 4 seconds
    setTimeout(() => {
        if (errorElement.classList.contains('show')) {
            errorElement.classList.remove('show');
        }
    }, 4000);
}

function showWarningMessage(elementId, message) {
    const errorElement = document.getElementById(elementId);
    if (!errorElement) return;

    errorElement.textContent = message;
    errorElement.className = 'error-msg warning show';

    // Auto-hide warning messages after 5 seconds
    setTimeout(() => {
        if (errorElement.classList.contains('show')) {
            errorElement.classList.remove('show');
        }
    }, 5000);
}

function clearErrorMessages() {
    const errorElements = document.querySelectorAll('.error-msg');
    errorElements.forEach(element => {
        element.classList.remove('show', 'success', 'warning');
        element.textContent = '';
    });
}

function isValidEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
}

function isValidPassword(password) {
    // At least 8 characters, one uppercase, one lowercase, one number
    if (!password || password.length < 8) return false;
    if (!/[a-z]/.test(password)) return false;
    if (!/[A-Z]/.test(password)) return false;
    if (!/\d/.test(password)) return false;
    return true;
}

function getPasswordValidationErrors(password) {
    const errors = [];

    if (password.length < 8) {
        errors.push('Must be at least 8 characters');
    }
    if (!/[a-z]/.test(password)) {
        errors.push('Include a lowercase letter');
    }
    if (!/[A-Z]/.test(password)) {
        errors.push('Include an uppercase letter');
    }
    if (!/\d/.test(password)) {
        errors.push('Include a number');
    }

    return errors;
}

function getPasswordStrength(password) {
    let score = 0;
    const length = password.length;

    // Length scoring
    if (length >= 8) score++;
    if (length >= 12) score++;
    if (length >= 16) score++;

    // Character type scoring
    if (/[a-z]/.test(password)) score++;
    if (/[A-Z]/.test(password)) score++;
    if (/\d/.test(password)) score++;
    if (/[^\w\s]/.test(password)) score++;

    // Bonus for variety
    const hasVariety = (/[a-z]/.test(password) && /[A-Z]/.test(password) && /\d/.test(password));
    if (hasVariety) score++;

    // Penalty for common patterns
    const commonPatterns = [
        /^[a-zA-Z]+$/,  // Only letters
        /^[0-9]+$/,     // Only numbers
        /(.)\1{2,}/,    // Repeated characters (aaa, 111)
        /12345|password|qwerty|abc123/i  // Common sequences
    ];

    if (commonPatterns.some(pattern => pattern.test(password))) {
        score = Math.max(0, score - 1);
    }

    // Calculate strength
    if (length < 8 || score < 3) {
        return { strength: 'weak', text: 'Weak', color: '#d9534f' };
    } else if (score < 5) {
        return { strength: 'medium', text: 'Medium', color: '#f0ad4e' };
    } else if (score < 7) {
        return { strength: 'strong', text: 'Strong', color: '#5bc0de' };
    } else {
        return { strength: 'very-strong', text: 'Very Strong', color: '#5cb85c' };
    }
}

function togglePasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    const toggle = input?.parentElement.querySelector('.password-toggle-icon');

    if (!input || !toggle) return;

    if (input.type === 'password') {
        input.type = 'text';
        toggle.classList.remove('icon-eye');
        toggle.classList.add('icon-eye-slash');
    } else {
        input.type = 'password';
        toggle.classList.remove('icon-eye-slash');
        toggle.classList.add('icon-eye');
    }
}

function updatePasswordStrength() {
    const passwordInput = document.getElementById('signup-password');
    const strengthContainer = document.getElementById('password-strength-container');
    const strengthText = document.getElementById('password-strength-text');
    const strengthBars = strengthContainer?.querySelectorAll('.strength-bar');

    if (!passwordInput || !strengthContainer || !strengthText || !strengthBars) return;

    const password = passwordInput.value;

    // Clear if empty
    if (password.length === 0) {
        strengthBars.forEach(bar => bar.className = 'strength-bar');
        strengthText.textContent = '';
        strengthText.className = '';
        passwordInput.classList.remove('input-error', 'input-success');
        return;
    }

    const strength = getPasswordStrength(password);

    // Clear all bars
    strengthBars.forEach(bar => {
        bar.className = 'strength-bar';
    });

    // Determine active bars and color
    let activeCount = 0;
    let colorClass = '';

    switch (strength.strength) {
        case 'weak':
            activeCount = 1;
            colorClass = 'weak';
            passwordInput.classList.add('input-error');
            passwordInput.classList.remove('input-success');
            break;
        case 'medium':
            activeCount = 2;
            colorClass = 'medium';
            passwordInput.classList.remove('input-error', 'input-success');
            break;
        case 'strong':
            activeCount = 3;
            colorClass = 'strong';
            passwordInput.classList.remove('input-error');
            passwordInput.classList.add('input-success');
            break;
        case 'very-strong':
            activeCount = 4;
            colorClass = 'very-strong';
            passwordInput.classList.remove('input-error');
            passwordInput.classList.add('input-success');
            break;
    }

    // Activate bars with animation
    for (let i = 0; i < activeCount; i++) {
        setTimeout(() => {
            strengthBars[i].classList.add('active', colorClass);
        }, i * 50);
    }

    // Update text with color
    strengthText.textContent = strength.text;
    strengthText.className = colorClass;
    strengthText.style.color = strength.color;
}

async function handleLogin(e) {
    e.preventDefault();
    console.log('🔐 Login attempt started...');
    clearErrorMessages();

    const formData = new FormData(e.target);
    const email = formData.get('email')?.trim();
    const password = formData.get('password');
    const loginBtn = document.getElementById('login-btn');

    console.log('Login data:', { email: email, passwordLength: password?.length });

    // Enhanced validation
    if (!email || !password) {
        console.log('❌ Validation failed: Missing fields');
        showErrorMessage('login-error', 'Please fill in all fields');
        return;
    }

    if (!isValidEmail(email)) {
        console.log('❌ Validation failed: Invalid email format');
        showErrorMessage('login-error', 'Please enter a valid email address');
        return;
    }

    try {
        console.log('🔄 Starting Supabase authentication...');
        showLoader();
        loginBtn.classList.add('loading');

        const { data, error } = await supabase.auth.signInWithPassword({
            email: email,
            password: password
        });

        console.log('Supabase auth response:', { data, error });

        if (error) {
            console.error('❌ Login error:', error.message, error.status);
            showErrorMessage('login-error', getErrorMessage(error));
            return;
        }

        if (data.user) {
            // Check if email is verified
            if (!data.user.email_confirmed_at) {
                console.log('❌ Email not verified');
                showErrorMessage('login-error', 'Please verify your email address before logging in. Check your inbox for the verification link.');

                // Sign out the user
                await supabase.auth.signOut();
                return;
            }

            console.log('✅ Login successful for:', data.user.email);
            loginBtn.classList.remove('loading');
            loginBtn.classList.add('success');
            showSuccessMessage('login-error', 'Login successful! Redirecting...');

            // Create or update user profile
            console.log('📝 Creating/updating user profile...');
            await createOrUpdateUserProfile(data.user);

            // Redirect to main app
            console.log('🔄 Redirecting to main app...');
            setTimeout(() => {
                window.location.href = '../index.html';
            }, 1500);
        } else {
            console.log('❌ No user data returned from Supabase');
            showErrorMessage('login-error', 'Login failed - no user data received');
        }

    } catch (error) {
        console.error('Unexpected login error:', error);
        showErrorMessage('login-error', 'An unexpected error occurred. Please try again.');
    } finally {
        hideLoader();
        loginBtn.classList.remove('loading');
    }
}

async function handleSignup(e) {
    e.preventDefault();
    clearErrorMessages();

    const formData = new FormData(e.target);
    const email = formData.get('email')?.trim();
    const password = formData.get('password');
    const confirmPassword = formData.get('confirmPassword');
    const username = formData.get('username')?.trim();
    const referralCode = formData.get('referralCode')?.trim();
    const privacyPolicyAccepted = document.getElementById('signup-privacy-policy')?.checked;
    const signupBtn = document.getElementById('signup-btn');

    console.log('🔐 Signup attempt:', { email, username, hasPassword: !!password, privacyAccepted: privacyPolicyAccepted });

    // Enhanced validation
    if (!email || !password || !confirmPassword || !username) {
        console.log('❌ Validation failed: Missing required fields');
        showErrorMessage('signup-error', 'Please fill in all required fields');
        return;
    }

    if (!privacyPolicyAccepted) {
        console.log('❌ Validation failed: Privacy policy not accepted');
        showErrorMessage('signup-error', 'Please accept the Privacy Policy and Terms of Service');
        return;
    }

    if (!isValidEmail(email)) {
        console.log('❌ Validation failed: Invalid email');
        showErrorMessage('signup-error', 'Please enter a valid email address');
        return;
    }

    if (username.length < 3) {
        console.log('❌ Validation failed: Username too short');
        showErrorMessage('signup-error', 'Username must be at least 3 characters long');
        return;
    }

    if (!isValidPassword(password)) {
        const errors = getPasswordValidationErrors(password);
        console.log('❌ Validation failed: Weak password', errors);
        const errorMsg = errors.length > 0 
            ? 'Password requirements: ' + errors.join(', ')
            : 'Password must be at least 8 characters with uppercase, lowercase, and number';
        showErrorMessage('signup-error', errorMsg);
        return;
    }

    if (password !== confirmPassword) {
        console.log('❌ Validation failed: Passwords do not match');
        showErrorMessage('signup-error', 'Passwords do not match');
        return;
    }

    try {
        showLoader();
        signupBtn.classList.add('loading');

        // Validate referral code if provided (optional, so don't block signup)
        let referrerId = null;
        if (referralCode && referralCode.length > 0) {
            console.log('🔍 Validating referral code:', referralCode);
            try {
                const { data: referralData, error: referralError } = await supabase
                    .from('referral_codes')
                    .select('user_id')
                    .eq('code', referralCode.toUpperCase())
                    .eq('active', true)
                    .single();

                if (referralError || !referralData) {
                    console.log('⚠️ Invalid referral code, proceeding without it');
                    showWarningMessage('signup-error', 'Invalid referral code - proceeding without it');
                    await new Promise(resolve => setTimeout(resolve, 2000));
                } else {
                    referrerId = referralData.user_id;
                    console.log('✅ Valid referral code');
                }
            } catch (refError) {
                console.log('⚠️ Referral validation error:', refError);
                // Continue without referral
            }
        }

        console.log('📝 Creating account...');
        const { data, error } = await supabase.auth.signUp({
            email: email,
            password: password,
            options: {
                data: {
                    display_name: username,
                    username: username
                },
                emailRedirectTo: window.location.origin + '/Auth/login.html'
            }
        });

        if (error) {
            console.error('❌ Signup error:', error);
            hideLoader();
            signupBtn.classList.remove('loading');
            showErrorMessage('signup-error', getErrorMessage(error));
            return;
        }

        if (data.user) {
            console.log('✅ Signup successful:', data.user.email);

            // Create user profile (but user can't login until email verified)
            try {
                await createOrUpdateUserProfile(data.user, { referred_by: referrerId });
                console.log('✅ User profile created');
            } catch (profileError) {
                console.warn('⚠️ Profile creation warning:', profileError);
            }

            // Create referral relationship if applicable
            let referrerUsername = null;
            if (referrerId && referralCode) {
                try {
                    await createReferralRelationship(referrerId, data.user.id, referralCode);
                    console.log('✅ Referral relationship created');

                    // Get referrer's username for display
                    const { data: referrerData } = await supabase
                        .from('user_profiles')
                        .select('username, display_name')
                        .eq('id', referrerId)
                        .single();

                    referrerUsername = referrerData?.display_name || referrerData?.username || 'a friend';
                } catch (refError) {
                    console.warn('⚠️ Referral relationship warning:', refError);
                }
            }

            signupBtn.classList.remove('loading');
            signupBtn.classList.add('success');

            // Show different success messages based on referral status
            if (referrerUsername) {
                showSuccessMessage('signup-error', `✓ Account created using ${referrerUsername}'s referral code! Please check your email to verify your account before logging in.`);
            } else {
                showSuccessMessage('signup-error', '✓ Account created! Please check your email to verify your account before logging in.');
            }

            // Helpful hint for admins if email delivery is not configured
            console.info('If users do not receive verification emails, see SUPABASE_EMAIL.md for troubleshooting and SMTP/webhook options.');

            // Redirect to login after a delay
            setTimeout(() => {
                window.location.href = './login.html';
            }, 4000);
        }

    } catch (error) {
        console.error('❌ Unexpected signup error:', error);
        showErrorMessage('signup-error', 'An unexpected error occurred. Please try again.');
    } finally {
        hideLoader();
        signupBtn.classList.remove('loading');
    }
}

async function handleForgotPassword(e) {
    e.preventDefault();
    clearErrorMessages();

    const formData = new FormData(e.target);
    const email = formData.get('email')?.trim();
    const forgotBtn = document.getElementById('forgot-password-btn');

    if (!email) {
        showErrorMessage('forgot-password-error', 'Please enter your email address');
        return;
    }

    if (!isValidEmail(email)) {
        showErrorMessage('forgot-password-error', 'Please enter a valid email address');
        return;
    }

    try {
        showLoader();
        forgotBtn.classList.add('loading');

        const { error } = await supabase.auth.resetPasswordForEmail(email, {
            redirectTo: window.location.origin + '/Auth/login.html'
        });

        if (error) {
            console.error('Password reset error:', error);
            showErrorMessage('forgot-password-error', getErrorMessage(error));
            return;
        }

        forgotBtn.classList.remove('loading');
        forgotBtn.classList.add('success');
        showSuccessMessage('forgot-password-error', '✓ Password reset email sent! Please check your inbox.');

        // Redirect to login after a delay
        setTimeout(() => {
            window.location.href = './login.html';
        }, 3000);

    } catch (error) {
        console.error('Unexpected password reset error:', error);
        showErrorMessage('forgot-password-error', 'An unexpected error occurred. Please try again.');
    } finally {
        hideLoader();
        forgotBtn.classList.remove('loading');
    }
}

async function createOrUpdateUserProfile(user, additionalData = {}) {
    try {
        const userData = {
            id: user.id,
            email: user.email,
            username: user.user_metadata?.username || user.user_metadata?.display_name || user.email.split('@')[0],
            display_name: user.user_metadata?.display_name || user.user_metadata?.username || user.email.split('@')[0],
            current_tier: 'Free Tier',
            tier: 'Free Tier',
            subscription_status: 'active',
            is_new_user: true,
            notifications: true,
            created_at: new Date().toISOString(),
            last_login: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            ...additionalData
        };

        const { data, error } = await supabase
            .from('user_profiles')
            .upsert(userData, { onConflict: 'id' })
            .select()
            .single();

        if (error && error.code !== '23505') {
            console.warn('Profile creation warning:', error);
        } else {
            console.log('User profile created/updated:', data);
        }

        // Generate referral code
        await generateReferralCode(user.id, userData.username);

    } catch (error) {
        console.warn('Error creating user profile:', error);
    }
}

async function generateReferralCode(userId, username) {
    try {
        const code = userId.substring(0, 8).toUpperCase();

        const { data, error } = await supabase
            .from('referral_codes')
            .upsert({
                user_id: userId,
                code: code,
                username: username,
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

async function createReferralRelationship(referrerId, referredId, referralCode) {
    try {
        const { data, error } = await supabase
            .from('referrals')
            .insert({
                referrer_id: referrerId,
                referred_id: referredId,
                referral_code: referralCode,
                reward_claimed: false,
                reward_amount: 500.00, // Default reward
                created_at: new Date().toISOString()
            })
            .select()
            .single();

        if (error && error.code !== '23505') {
            console.warn('Referral relationship creation warning:', error);
        } else {
            console.log('Referral relationship created:', data);

            // Update referral stats
            await updateReferralStats(referrerId);

            // Log account history for referrer notification
            try {
                const { data: referredUser } = await supabase
                    .from('user_profiles')
                    .select('email, username, display_name')
                    .eq('id', referredId)
                    .single();

                await supabase
                    .from('account_history')
                    .insert({
                        user_id: referrerId,
                        action: `New referral: ${referredUser?.display_name || referredUser?.username || referredUser?.email || 'Someone'} signed up using your referral code ${referralCode}`,
                        action_type: 'referral_signup',
                        created_at: new Date().toISOString()
                    });
            } catch (historyError) {
                console.warn('Error logging referral history:', historyError);
            }
        }
    } catch (error) {
        console.warn('Error creating referral relationship:', error);
    }
}

async function updateReferralStats(userId) {
    try {
        // Count total referrals
        const { count } = await supabase
            .from('referrals')
            .select('*', { count: 'exact', head: true })
            .eq('referrer_id', userId);

        // Update referral code stats
        await supabase
            .from('referral_codes')
            .update({
                total_referrals: count || 0,
                updated_at: new Date().toISOString()
            })
            .eq('user_id', userId);

        // Update user profile stats
        await supabase
            .from('user_profiles')
            .update({
                total_referrals: count || 0,
                updated_at: new Date().toISOString()
            })
            .eq('id', userId);

        console.log(`Updated referral stats for user ${userId}: ${count} referrals`);
    } catch (error) {
        console.warn('Error updating referral stats:', error);
    }
}


function getErrorMessage(error) {
    switch (error.message) {
        case 'Invalid login credentials':
            return 'Invalid email or password. Please check your credentials and try again.';
        case 'Email not confirmed':
            return 'Please verify your email address before signing in. Check your inbox for the verification link.';
        case 'User already registered':
            return 'An account with this email already exists. Please sign in instead.';
        case 'Password should be at least 6 characters':
            return 'Password must be at least 6 characters long.';
        case 'Unable to validate email address: invalid format':
            return 'Please enter a valid email address.';
        case 'Too many requests':
            return 'Too many attempts. Please wait a moment before trying again.';
        default:
            return error.message || 'An error occurred. Please try again.';
    }
}

// Initialize smooth auth page navigation with loaders
function initializeAuthNavigation() {
    // Find all auth navigation links
    const authLinks = document.querySelectorAll('.switch-auth a, a[href*="login.html"], a[href*="signup.html"], a[href*="forgot-password.html"]');

    authLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const targetUrl = this.getAttribute('href');

            // Show loader animation
            showLoader();

            // Navigate immediately
            window.location.href = targetUrl;
        });
    });
}

// Check if user is already logged in and redirect to main app
async function checkExistingSession() {
    const { data: { session } } = await supabase.auth.getSession();
    if (session) {
        console.log('User already logged in, redirecting to main app...');
        // Go to homepage after authentication
        window.location.href = '../index.html#home';
    }
}

// Check for existing session when page loads
checkExistingSession();

// ===== Auth Page Ad System =====
function initializeAuthAds() {
    console.log('📺 Initializing ads for auth page...');

    // Wait for consent manager and check consent
    checkAuthConsentAndLoadAds();
}

// Check consent status and load ads accordingly for auth pages
function checkAuthConsentAndLoadAds() {
    // Listen for consent updates
    window.addEventListener('consentUpdated', function(event) {
        const consent = event.detail;
        console.log('🍪 Auth page: Consent updated:', consent);

        if (consent.ad_storage === 'granted') {
            loadAuthPageAds();
        } else {
            console.log('🚫 Auth page: Ads not loaded - consent denied');
        }
    });

    // Check if consent manager is available and get current consent
    if (window.consentManager) {
        const currentConsent = window.consentManager.getConsentStatus();
        if (currentConsent && currentConsent.ad_storage === 'granted') {
            loadAuthPageAds();
        } else {
            console.log('⏳ Auth page: Waiting for user consent to load ads...');
        }
    } else {
        // Fallback: wait for consent manager to load
        console.log('⏳ Auth page: Consent manager not ready, waiting...');
        setTimeout(checkAuthConsentAndLoadAds, 1000);
    }
}

function loadAuthPageAds() {
    console.log('📺 Loading ads for auth page...');

    // Load Google AdSense script dynamically
    const script = document.createElement('script');
    script.async = true;
    script.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-9868946535437166';
    script.crossOrigin = 'anonymous';

    script.onload = () => {
        console.log('✅ AdSense loaded successfully on auth page');
    };

    script.onerror = () => {
        console.log('❌ AdSense failed to load on auth page');
    };

    document.head.appendChild(script);
}

console.log('✅ Supabase authentication system loaded successfully!');
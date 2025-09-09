// auth.js - Supabase Authentication
import { supabase } from '../env.js';
import { showLoader, hideLoader, showSpinner, hideSpinner } from '../Loader/loader.js';
import { initInteractiveBackground, initializeTheme } from '../ui.js';

// Initialize the auth page
document.addEventListener('DOMContentLoaded', function() {
    console.log('🔐 Initializing auth page...');
    
    // Test Supabase connection
    testSupabaseConnection();
    
    initializeTheme(); // Initialize theme system
    initInteractiveBackground(); // Add background animation to auth pages
    initializeAuthForms();
    
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
    
    // Toggle password visibility
    const passwordToggle = document.getElementById('password-toggle');
    if (passwordToggle) {
        passwordToggle.addEventListener('click', () => togglePasswordVisibility('login-password'));
    }
    
    const confirmPasswordToggle = document.getElementById('confirm-password-toggle');
    if (confirmPasswordToggle) {
        confirmPasswordToggle.addEventListener('click', () => togglePasswordVisibility('signup-password-confirm'));
    }
    
    // Password strength indicator for signup
    const signupPassword = document.getElementById('signup-password');
    if (signupPassword) {
        signupPassword.addEventListener('input', updatePasswordStrength);
    }
}

function handleThemeToggle(e) {
    const button = e.target.closest('.theme-toggle');
    if (!button) return;
    
    // Get button position for circle origin
    const rect = button.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    
    // Create transition circle
    const circle = document.createElement('div');
    circle.className = 'theme-transition-circle';
    circle.style.left = centerX + 'px';
    circle.style.top = centerY + 'px';
    circle.style.transform = 'translate(-50%, -50%)';
    
    // Determine target theme
    const currentTheme = localStorage.getItem('statwise-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    
    // Set circle color based on target theme
    if (newTheme === 'light') {
        circle.classList.add('light-mode');
    }
    
    document.body.appendChild(circle);
    
    // Trigger expansion
    setTimeout(() => {
        circle.classList.add('expanding');
    }, 10);
    
    // Apply theme change during expansion
    setTimeout(() => {
        import('../ui.js').then(({ toggleTheme }) => {
            toggleTheme();
            updateThemeIcon(newTheme);
        });
    }, 300);
    
    // Remove circle after animation
    setTimeout(() => {
        if (circle.parentNode) {
            circle.parentNode.removeChild(circle);
        }
    }, 700);
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
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[a-zA-Z\d@$!%*?&]{8,}$/;
    return passwordRegex.test(password);
}

function getPasswordStrength(password) {
    let score = 0;
    if (password.length >= 8) score++;
    if (/[a-z]/.test(password)) score++;
    if (/[A-Z]/.test(password)) score++;
    if (/\d/.test(password)) score++;
    if (/[^\w\s]/.test(password)) score++;
    
    if (score < 2) return { strength: 'weak', text: 'Weak' };
    if (score < 4) return { strength: 'medium', text: 'Medium' };
    return { strength: 'strong', text: 'Strong' };
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
    const strength = getPasswordStrength(password);
    
    // Clear all bars
    strengthBars.forEach(bar => {
        bar.className = 'strength-bar';
    });
    
    // Update based on strength
    let activeCount = 0;
    let colorClass = '';
    
    if (password.length === 0) {
        strengthText.textContent = '';
        return;
    }
    
    switch (strength.strength) {
        case 'weak':
            activeCount = 1;
            colorClass = 'weak';
            break;
        case 'medium':
            activeCount = 3;
            colorClass = 'medium';
            break;
        case 'strong':
            activeCount = 4;
            colorClass = 'strong';
            break;
    }
    
    // Activate bars
    for (let i = 0; i < activeCount; i++) {
        strengthBars[i].classList.add('active', colorClass);
    }
    
    strengthText.textContent = strength.text;
    strengthText.className = colorClass;
}

async function handleLogin(e) {
    e.preventDefault();
    console.log('🔐 Login attempt started...');
    clearErrorMessages();
    
    const formData = new FormData(e.target);
    const email = formData.get('email')?.trim();
    const password = formData.get('password');
    
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
            console.log('✅ Login successful for:', data.user.email);
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
    
    // Enhanced validation
    if (!email || !password || !confirmPassword || !username) {
        showErrorMessage('signup-error', 'Please fill in all required fields');
        return;
    }
    
    if (!isValidEmail(email)) {
        showErrorMessage('signup-error', 'Please enter a valid email address');
        return;
    }
    
    if (username.length < 3) {
        showErrorMessage('signup-error', 'Username must be at least 3 characters long');
        return;
    }
    
    if (!isValidPassword(password)) {
        showErrorMessage('signup-error', 'Password must be at least 8 characters with uppercase, lowercase, and number');
        return;
    }
    
    if (password !== confirmPassword) {
        showErrorMessage('signup-error', 'Passwords do not match');
        return;
    }
    
    try {
        showLoader();
        
        // Validate referral code if provided
        let referrerId = null;
        if (referralCode) {
            const { data: referralData, error: referralError } = await supabase
                .from('referral_codes')
                .select('user_id')
                .eq('code', referralCode.toUpperCase())
                .eq('active', true)
                .single();
                
            if (referralError || !referralData) {
                showErrorMessage('signup-error', 'Invalid referral code');
                return;
            }
            referrerId = referralData.user_id;
        }
        
        const { data, error } = await supabase.auth.signUp({
            email: email,
            password: password,
            options: {
                data: {
                    display_name: username,
                    username: username
                }
            }
        });
        
        if (error) {
            console.error('Signup error:', error);
            showErrorMessage('signup-error', getErrorMessage(error));
            return;
        }
        
        if (data.user) {
            console.log('Signup successful:', data.user.email);
            
            // Create user profile
            await createOrUpdateUserProfile(data.user, { referred_by: referrerId });
            
            // Create referral relationship if applicable
            if (referrerId && referralCode) {
                await createReferralRelationship(referrerId, data.user.id, referralCode);
            }
            
            showSuccessMessage('signup-error', 'Account created successfully! Please check your email to verify your account.');
            
            // Redirect to login after a delay
            setTimeout(() => {
                window.location.href = './login.html';
            }, 3000);
        }
        
    } catch (error) {
        console.error('Unexpected signup error:', error);
        showErrorMessage('signup-error', 'An unexpected error occurred. Please try again.');
    } finally {
        hideLoader();
    }
}

async function handleForgotPassword(e) {
    e.preventDefault();
    clearErrorMessages();
    
    const formData = new FormData(e.target);
    const email = formData.get('email')?.trim();
    
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
        
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
            redirectTo: window.location.origin + '/Auth/login.html'
        });
        
        if (error) {
            console.error('Password reset error:', error);
            showErrorMessage('forgot-password-error', getErrorMessage(error));
            return;
        }
        
        showSuccessMessage('forgot-password-error', 'Password reset email sent! Please check your inbox.');
        
        // Redirect to login after a delay
        setTimeout(() => {
            window.location.href = './login.html';
        }, 3000);
        
    } catch (error) {
        console.error('Unexpected password reset error:', error);
        showErrorMessage('forgot-password-error', 'An unexpected error occurred. Please try again.');
    } finally {
        hideLoader();
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
            return 'Please verify your email address before signing in.';
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

function showMessage(message, type = 'info') {
    // Remove existing messages
    const existingMessages = document.querySelectorAll('.auth-message');
    existingMessages.forEach(msg => msg.remove());
    
    // Create new message
    const messageDiv = document.createElement('div');
    messageDiv.className = `auth-message auth-message-${type}`;
    messageDiv.textContent = message;
    
    // Insert message at the top of the form container
    const container = document.querySelector('.auth-container');
    if (container) {
        container.insertBefore(messageDiv, container.firstChild);
        
        // Auto-remove success/error messages after 5 seconds
        if (type === 'success' || type === 'error') {
            setTimeout(() => {
                if (messageDiv.parentNode) {
                    messageDiv.remove();
                }
            }, 5000);
        }
    }
}

// Check if user is already logged in and redirect to main app
async function checkExistingSession() {
    const { data: { session } } = await supabase.auth.getSession();
    if (session) {
        console.log('User already logged in, redirecting to main app...');
        window.location.href = '../index.html';
    }
}

// Check for existing session when page loads
checkExistingSession();

console.log('✅ Supabase authentication system loaded successfully!');
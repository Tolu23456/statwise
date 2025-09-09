// auth.js - Supabase Authentication
import { supabase } from '../env.js';
import { showLoader, hideLoader, showSpinner, hideSpinner } from '../Loader/loader.js';
import { initInteractiveBackground, initializeTheme } from '../ui.js';

// Initialize the auth page
document.addEventListener('DOMContentLoaded', function() {
    initializeTheme(); // Initialize theme system
    initInteractiveBackground(); // Add background animation to auth pages
    initializeAuthForms();
});

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
    const toggleButtons = document.querySelectorAll('.toggle-password');
    toggleButtons.forEach(button => {
        button.addEventListener('click', togglePasswordVisibility);
    });
}

function handleThemeToggle() {
    // Import toggleTheme function dynamically
    import('../ui.js').then(({ toggleTheme }) => {
        const newTheme = toggleTheme();
        updateThemeIcon(newTheme);
    });
}

function updateThemeIcon(theme = null) {
    const themeIcon = document.querySelector('.theme-icon');
    if (!themeIcon) return;
    
    const currentTheme = theme || localStorage.getItem('statwise-theme') || 'light';
    themeIcon.textContent = currentTheme === 'dark' ? '☀️' : '🌙';
}

async function handleLogin(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const email = formData.get('email');
    const password = formData.get('password');
    
    if (!email || !password) {
        showMessage('Please fill in all fields', 'error');
        return;
    }
    
    try {
        showLoader();
        
        const { data, error } = await supabase.auth.signInWithPassword({
            email: email,
            password: password
        });
        
        if (error) {
            console.error('Login error:', error);
            showMessage(getErrorMessage(error), 'error');
            return;
        }
        
        if (data.user) {
            console.log('Login successful:', data.user.email);
            showMessage('Login successful! Redirecting...', 'success');
            
            // Create or update user profile
            await createOrUpdateUserProfile(data.user);
            
            // Redirect to main app
            setTimeout(() => {
                window.location.href = '../index.html';
            }, 1500);
        }
        
    } catch (error) {
        console.error('Unexpected login error:', error);
        showMessage('An unexpected error occurred. Please try again.', 'error');
    } finally {
        hideLoader();
    }
}

async function handleSignup(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const email = formData.get('email');
    const password = formData.get('password');
    const confirmPassword = formData.get('confirmPassword');
    const username = formData.get('username');
    const referralCode = formData.get('referralCode');
    
    // Validation
    if (!email || !password || !confirmPassword || !username) {
        showMessage('Please fill in all required fields', 'error');
        return;
    }
    
    if (password !== confirmPassword) {
        showMessage('Passwords do not match', 'error');
        return;
    }
    
    if (password.length < 6) {
        showMessage('Password must be at least 6 characters long', 'error');
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
                showMessage('Invalid referral code', 'error');
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
            showMessage(getErrorMessage(error), 'error');
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
            
            showMessage('Account created successfully! Please check your email to verify your account.', 'success');
            
            // Redirect to login after a delay
            setTimeout(() => {
                window.location.href = './login.html';
            }, 3000);
        }
        
    } catch (error) {
        console.error('Unexpected signup error:', error);
        showMessage('An unexpected error occurred. Please try again.', 'error');
    } finally {
        hideLoader();
    }
}

async function handleForgotPassword(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const email = formData.get('email');
    
    if (!email) {
        showMessage('Please enter your email address', 'error');
        return;
    }
    
    try {
        showLoader();
        
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
            redirectTo: window.location.origin + '/Auth/login.html'
        });
        
        if (error) {
            console.error('Password reset error:', error);
            showMessage(getErrorMessage(error), 'error');
            return;
        }
        
        showMessage('Password reset email sent! Please check your inbox.', 'success');
        
        // Redirect to login after a delay
        setTimeout(() => {
            window.location.href = './login.html';
        }, 3000);
        
    } catch (error) {
        console.error('Unexpected password reset error:', error);
        showMessage('An unexpected error occurred. Please try again.', 'error');
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

function togglePasswordVisibility(e) {
    const button = e.target.closest('.toggle-password');
    const input = button.previousElementSibling;
    const icon = button.querySelector('use');
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.setAttribute('href', '#eye-slash');
    } else {
        input.type = 'password';
        icon.setAttribute('href', '#eye');
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
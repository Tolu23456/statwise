// js/auth.js
import { supabase } from '../env.js';
import { showLoader, hideLoader } from '../Loader/loader.js';
import { createOrUpdateUserProfile, loadUserData } from './user.js';

let currentUser = null;

// ===== Authentication Setup =====
async function initializeSupabaseAuth(loadPage) {
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
        window.initializeApp();

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

function redirectToLogin() {
    console.log('No user found, redirecting to login...');
    window.location.href = '../Auth/login.html';
}

async function signOut() {
    try {
        console.log('Starting sign out process...');
        showLoader();

        currentUser = null;

        const { error } = await supabase.auth.signOut();

        if (error) {
            console.error('Supabase sign out error:', error);
        }

        localStorage.clear();
        sessionStorage.clear();

        hideLoader();

        console.log('Sign out successful, redirecting to login...');
        window.location.href = '../Auth/login.html';

    } catch (error) {
        console.error('Unexpected error during sign out:', error);
        hideLoader();

        try {
            localStorage.clear();
            sessionStorage.clear();
        } catch (e) {
            console.warn('Error clearing storage:', e);
        }

        alert('Sign out completed with errors. Redirecting to login...');
        window.location.href = '../Auth/login.html';
    }
}

export { initializeSupabaseAuth, redirectToLogin, signOut, currentUser };

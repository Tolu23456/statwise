// js/user.js
import { supabase } from '../env.js';
import { showSpinner, hideSpinner } from '../Loader/loader.js';
import { showModal, loadPage } from './main.js';
import { currentUser, signOut } from './auth.js';
import { initializeAdSystemForUser } from './ads.js';

let verifiedTier = null;

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
            import('../ui.js').then(({ toggleTheme }) => {
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

    // Initialize FAQ toggle functionality
    const faqQuestions = document.querySelectorAll('.faq-question');
    if (faqQuestions.length > 0) {
        faqQuestions.forEach(question => {
            question.addEventListener('click', () => {
                const faqItem = question.closest('.faq-item');
                const isActive = faqItem.classList.contains('active');

                // Close all other FAQ items
                document.querySelectorAll('.faq-item').forEach(item => {
                    item.classList.remove('active');
                });

                // Toggle current item
                if (!isActive) {
                    faqItem.classList.add('active');
                }
            });
        });
    }
}
async function ensureBucketExists(bucketName) {
    try {
        // Try listing root of the bucket. If bucket doesn't exist, Supabase returns an error.
        const { data, error } = await supabase.storage.from(bucketName).list('', { limit: 1 });
        if (error) {
            console.warn('Bucket check error for', bucketName, error);
            return false;
        }
        return true;
    } catch (err) {
        console.warn('Unexpected error checking bucket:', err);
        return false;
    }
}
async function handleAvatarUpload(event) {
    console.log('📸 Avatar upload triggered, processing file...');

    // Ensure user is signed in
    if (!currentUser) {
        showModal({ message: 'Please log in before uploading a profile picture.', confirmText: 'Login' });
        return;
    }

    // Quick sanity check for bucket availability
    const bucketOk = await ensureBucketExists('profile-pictures');
    if (!bucketOk) {
        showModal({
            message: 'Profile pictures storage is not available. Please create the "profile-pictures" bucket in your Supabase project or contact support.',
            confirmText: 'OK'
        });
        return;
    }

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

    // Validate file type with both MIME type and file extension
    const allowedTypes = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp'];
    const fileExtension = file.name.split('.').pop().toLowerCase();
    const allowedExtensions = ['jpg', 'jpeg', 'png', 'gif', 'webp'];

    if (!allowedTypes.includes(file.type) || !allowedExtensions.includes(fileExtension)) {
        console.error('Invalid file type:', file.type, 'or extension:', fileExtension);
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
async function updateUsername(newUsername) {
    try {
        showSpinner();

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
            showModal({
                message: 'Failed to update username. Please try again.',
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

export { createOrUpdateUserProfile, loadUserData, generateReferralCode, loadUserProfile, displayUserProfile, initializeProfileInteractions, updateUsername, ensureBucketExists, handleAvatarUpload, verifiedTier };

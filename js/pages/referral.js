import { supabase } from '../../env.js';
import { showModal, formatTimestamp } from '../../utils.js';

let currentUser;

export async function initializeReferralPage(user) {
    currentUser = user;
    await loadReferralData();
}

async function loadReferralData() {
    // If currentUser is not set, try to get the active session from Supabase
    if (!currentUser) {
        try {
            const { data: { session } } = await supabase.auth.getSession();
                if (session && session.user) {
                    currentUser = session.user;
                } else {
                    console.warn('loadReferralData: no logged-in user found - redirecting to login');
                    // Redirect unauthenticated users to login when trying to access referral page
                    redirectToLogin();
                    return;
                }
        } catch (sessErr) {
            console.warn('Error obtaining auth session in loadReferralData:', sessErr);
            displayReferralData(null, []);
            return;
        }
    }

    try {
        // Get user's referral code
        const { data: referralCode, error: codeError } = await supabase
            .from('referral_codes')
            .select('*')
            .eq('user_id', currentUser.id)
            .single();

        if (codeError && codeError.code !== 'PGRST116') {
            console.warn('Error loading referral code:', codeError);
        }

        // Get user's referrals
        const { data: referrals, error: referralsError } = await supabase
            .from('referrals')
            .select('*')
            .eq('referrer_id', currentUser.id)
            .order('created_at', { ascending: false });

        if (referralsError) {
            console.warn('Error loading referrals:', referralsError);
            displayReferralData(referralCode, []);
            return;
        }

        // Fetch referred user details for each referral
        const referralsWithDetails = await Promise.all(
            (referrals || []).map(async (referral) => {
                const { data: referredUser, error: userError } = await supabase
                    .from('user_profiles')
                    .select('display_name, username, email, current_tier, created_at')
                    .eq('id', referral.referred_id)
                    .single();

                if (userError) {
                    console.warn('Error loading referred user:', userError);
                }

                return {
                    ...referral,
                    user_profiles: referredUser || null
                };
            })
        );

        displayReferralData(referralCode, referralsWithDetails);
    } catch (error) {
        console.error('Error loading referral data:', error);
        displayReferralData(referralCode, []);
    }
}

function displayReferralData(referralCode, referrals) {
    const code = referralCode?.code || 'No Code Found';

    // Update referral code input
    const referralCodeInput = document.getElementById('referralCodeInput');
    if (referralCodeInput) {
        referralCodeInput.value = code;
    }

    console.log('Displaying referral data:', { code, referralCount: referrals.length, referrals });

    // Update referral list
    const referralListContainer = document.getElementById('referralListContainer');
    if (referralListContainer) {
        if (!referrals || referrals.length === 0) {
            referralListContainer.innerHTML = '<p>No referrals yet. Share your code to get started!</p>';
        } else {
            const referralHTML = `
                <div class="table-responsive">
                    <table class="referral-table">
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Email</th>
                                <th>Tier</th>
                                <th>Joined</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${referrals.map(referral => {
                                console.log('Processing referral:', referral);
                                // Handle the joined data structure with better null safety
                                const referredUser = referral.user_profiles || {};
                                const userName = referredUser.display_name || referredUser.username || 'Unknown User';
                                const userEmail = referredUser.email || 'N/A';
                                const userTier = referredUser.current_tier || 'Free Tier';

                                return `
                                    <tr>
                                        <td data-label="Name">${userName}</td>
                                        <td data-label="Email">${userEmail}</td>
                                        <td data-label="Tier"><span class="tier-badge-small">${userTier}</span></td>
                                        <td data-label="Joined">${formatTimestamp(referral.created_at)}</td>
                                        <td data-label="Status">
                                            <span class="reward-status ${referral.reward_claimed ? 'claimed' : 'pending'}">
                                                ${referral.reward_claimed ? '✅ Rewarded' : '⏳ Pending'}
                                            </span>
                                        </td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            `;
            referralListContainer.innerHTML = referralHTML;
        }
    }

    // Update referral count display
    const totalReferrals = referrals ? referrals.length : 0;
    console.log('Total referrals:', totalReferrals);

    // Update rewards count
    const rewardsCount = document.getElementById('rewardsCount');
    if (rewardsCount) {
        const claimedRewards = referrals ? referrals.filter(r => r.reward_claimed).length : 0;
        rewardsCount.textContent = claimedRewards;
    }

    // Update rewards container
    const rewardsContainer = document.getElementById('rewardsContainer');
    if (rewardsContainer) {
        const claimedReferrals = referrals ? referrals.filter(r => r.reward_claimed) : [];
        if (claimedReferrals.length === 0) {
            rewardsContainer.innerHTML = '<p>No rewards earned yet. You\'ll get a reward when a referred user subscribes!</p>';
        } else {
            const rewardsHTML = claimedReferrals.map(referral => {
                const referredUser = referral.user_profiles || {};
                return `
                    <div class="reward-item">
                        <span>Premium Week from ${referredUser.display_name || referredUser.username || 'User'}</span>
                        <span class="reward-amount">₦${referral.reward_amount?.toLocaleString() || '500'}</span>
                    </div>
                `;
            }).join('');
            rewardsContainer.innerHTML = rewardsHTML;
        }
    }

    // Initialize referral page interactions
    initializeReferralInteractions();
}

function initializeReferralInteractions() {
    // Initialize copy referral code button
    const copyReferralCodeBtn = document.getElementById('copyReferralCodeBtn');
    if (copyReferralCodeBtn) {
        copyReferralCodeBtn.addEventListener('click', () => {
            const referralCodeInput = document.getElementById('referralCodeInput');
            if (referralCodeInput && referralCodeInput.value !== 'Loading...' && referralCodeInput.value !== 'No Code Found') {
                navigator.clipboard.writeText(referralCodeInput.value).then(() => {
                    // Show success feedback
                    copyReferralCodeBtn.textContent = 'Copied!';
                    copyReferralCodeBtn.style.background = '#28a745';
                    setTimeout(() => {
                        copyReferralCodeBtn.textContent = 'Copy';
                        copyReferralCodeBtn.style.background = '';
                    }, 2000);
                }).catch(() => {
                    showModal({
                        message: 'Failed to copy referral code',
                        confirmText: 'OK'
                    });
                });
            }
        });
    }

    // Initialize share buttons
    const shareWhatsAppBtn = document.getElementById('shareWhatsAppBtn');
    if (shareWhatsAppBtn) {
        shareWhatsAppBtn.addEventListener('click', () => {
            const referralCode = document.getElementById('referralCodeInput')?.value;
            if (referralCode && referralCode !== 'Loading...' && referralCode !== 'No Code Found') {
                const message = `Join StatWise using my referral code: ${referralCode} and get exclusive AI sports predictions!`;
                const url = `https://wa.me/?text=${encodeURIComponent(message)}`;
                window.open(url, '_blank');
            }
        });
    }

    const shareTwitterBtn = document.getElementById('shareTwitterBtn');
    if (shareTwitterBtn) {
        shareTwitterBtn.addEventListener('click', () => {
            const referralCode = document.getElementById('referralCodeInput')?.value;
            if (referralCode && referralCode !== 'Loading...' && referralCode !== 'No Code Found') {
                const message = `Join StatWise using my referral code: ${referralCode} and get exclusive AI sports predictions!`;
                const url = `https://twitter.com/intent/tweet?text=${encodeURIComponent(message)}`;
                window.open(url, '_blank');
            }
        });
    }

    const shareGenericBtn = document.getElementById('shareGenericBtn');
    if (shareGenericBtn) {
        shareGenericBtn.addEventListener('click', () => {
            const referralCode = document.getElementById('referralCodeInput')?.value;
            if (referralCode && referralCode !== 'Loading...' && referralCode !== 'No Code Found') {
                const message = `Join StatWise using my referral code: ${referralCode} and get exclusive AI sports predictions!`;
                if (navigator.share) {
                    navigator.share({
                        title: 'StatWise Referral',
                        text: message
                    });
                } else {
                    // Fallback to copying to clipboard
                    navigator.clipboard.writeText(message).then(() => {
                        showModal({
                            message: 'Referral message copied to clipboard!',
                            confirmText: 'OK'
                        });
                    });
                }
            }
        });
    }
}

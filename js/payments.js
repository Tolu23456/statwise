// js/payments.js
import { supabase, FLWPUBK } from '../env.js';
import { showLoader, hideLoader } from '../Loader/loader.js';
import { showModal, loadPage } from './main.js';
import { currentUser } from './auth.js';
import { verifiedTier } from './user.js';

function checkPaymentRedirect() {
    const urlParams = new URLSearchParams(window.location.search);
    const paymentStatus = urlParams.get('payment');
    const transactionId = urlParams.get('transaction_id');

    if (paymentStatus === 'success' && transactionId) {
        setTimeout(() => {
            showModal({
                message: `🎉 Welcome back!\n\nYour payment has been processed successfully.\nTransaction ID: ${transactionId}\n\nPlease wait while we verify your subscription...`,
                confirmClass: 'btn-success',
                confirmText: 'Continue',
                onConfirm: () => {
                    loadPage('subscriptions');
                }
            });
        }, 1000);

        window.history.replaceState({}, document.title, window.location.pathname);
    } else if (paymentStatus === 'cancelled') {
        setTimeout(() => {
            showModal({
                message: '❌ Payment was cancelled.\n\nYour subscription has not been updated. You can try again anytime.',
                confirmClass: 'btn-warning',
                confirmText: 'OK'
            });
        }, 1000);

        window.history.replaceState({}, document.title, window.location.pathname);
    }
}
window.initializePayment = function(tier, period, amount) {
    if (!currentUser) {
        showModal({ message: 'Please log in to subscribe.' });
        return;
    }

    // Initialize Flutterwave payment
    FlutterwaveCheckout({
        public_key: FLWPUBK,
        tx_ref: `statwise_${currentUser.id}_${Date.now()}`,
        amount: amount,
        currency: "NGN",
        payment_options: "card,mobilemoney,ussd",
        customer: {
            email: currentUser.email,
            phone_number: "",
            name: currentUser.user_metadata?.display_name || currentUser.email
        },
        customizations: {
            title: "StatWise Subscription",
            description: `${tier} - ${period}`,
            logo: ""
        },
        callback: function (data) {
            console.log('Payment callback:', data);
            if (data.status === "successful") {
                // Show loader while verifying payment
                showLoader();
                handleSuccessfulPayment(data, tier, period, amount);
            } else if (data.status === "cancelled") {
                console.log('Payment was cancelled by user');
                showModal({
                    message: 'Payment was cancelled. You can try again anytime.',
                    confirmText: 'OK'
                });
            } else {
                console.log('Payment failed:', data);
                showModal({
                    message: 'Payment failed. Please try again or contact support.',
                    confirmText: 'OK'
                });
            }
        },
        onclose: function() {
            console.log('Payment modal closed');
            // Don't show loader if modal is just closed without payment
        }
    });
};
async function handleSuccessfulPayment(paymentData, tier, period, amount) {
    try {
        console.log('🔄 Verifying payment with server...');

        // Call Supabase Edge Function to verify payment
        const { data: verificationResult, error: verificationError } = await supabase.functions.invoke('verify-payment', {
            body: {
                transaction_id: paymentData.transaction_id,
                tx_ref: paymentData.tx_ref,
                amount: amount,
                tier: tier,
                period: period,
                user_id: currentUser.id,
                flw_ref: paymentData.flw_ref || paymentData.transaction_id
            }
        });

        // Always hide loader after verification attempt (success or error)
        hideLoader();

        if (verificationError) {
            console.error('Payment verification failed:', verificationError);
            showModal({
                message: 'Payment verification failed. Please contact support with your transaction ID: ' + paymentData.transaction_id,
                confirmText: 'OK'
            });
            return;
        }

        if (verificationResult?.success) {
            // Update local user tier
            verifiedTier = tier;
            console.log('✅ Payment verified and subscription updated successfully!');

            showModal({
                message: `🎉 Congratulations!\n\nYour ${tier} subscription is now active!\n\nTransaction ID: ${paymentData.transaction_id}`,
                confirmText: 'Continue',
                onConfirm: () => {
                    // Reload the subscriptions page to show updated tier
                    loadPage('subscriptions');
                }
            });
        } else {
            console.error('Payment verification failed:', verificationResult);
            showModal({
                message: verificationResult?.message || 'Payment could not be verified. Please contact support.',
                confirmText: 'OK'
            });
        }

    } catch (error) {
        hideLoader();
        console.error('Error handling successful payment:', error);
        showModal({
            message: 'Payment successful but there was an error verifying it. Please contact support with your transaction ID: ' + paymentData.transaction_id,
            confirmText: 'OK'
        });
    }
}

export { checkPaymentRedirect, initializePayment, handleSuccessfulPayment };

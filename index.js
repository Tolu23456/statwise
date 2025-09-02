const functions = require("firebase-functions");
const admin = require("firebase-admin");
const axios = require("axios");

admin.initializeApp();
const db = admin.firestore();

// Get secret key from environment variables
// In your terminal, run: firebase functions:config:set flutterwave.secret="YOUR_FLWSECK_TEST_KEY"
const FLW_SECRET_KEY = functions.config().flutterwave.secret;

/**
 * Verifies a Flutterwave transaction and updates the user's tier if successful.
 */
exports.verifyFlutterwavePayment = functions.https.onCall(async (data, context) => {
    // 1. Check for authentication
    if (!context.auth) {
        throw new functions.https.HttpsError(
            "unauthenticated",
            "The function must be called while authenticated.",
        );
    }

    const { transactionId, tx_ref, tier, period, amount } = data;
    const userId = context.auth.uid;

    if (!transactionId || !tier || !period || !amount || !tx_ref) {
        throw new functions.https.HttpsError(
            "invalid-argument",
            "Missing required data for verification.",
        );
    }

    try {
        // 2. Call Flutterwave's verification endpoint
        const response = await axios.get(
            `https://api.flutterwave.com/v3/transactions/${transactionId}/verify`,
            {
                headers: {
                    "Authorization": `Bearer ${FLW_SECRET_KEY}`,
                },
            },
        );

        const verificationData = response.data.data;

        // 3. Perform crucial server-side checks
        if (
            verificationData.status === "successful" &&
            verificationData.tx_ref === tx_ref &&
            verificationData.amount >= amount && // Use >= in case of minor discrepancies
            verificationData.currency === "NGN"
        ) {
            // 4. Verification successful, update user's tier in Firestore
            const userRef = db.collection("users").doc(userId);
            const subRef = db.collection("subscriptions").doc(userId);

            const expiry = new Date();
            if (period === 'daily') {
                expiry.setDate(expiry.getDate() + 1);
            } else if (period === 'monthly') {
                expiry.setMonth(expiry.getMonth() + 1);
            }

            // Update user document
            await userRef.update({
                tier: tier,
                tierExpiry: expiry.toISOString(),
                autoRenew: true,
            });

            // --- Referral Reward Logic ---
            const paidUserSnap = await userRef.get();
            const paidUserData = paidUserSnap.data();
            if (paidUserData.referredBy) {
                const referrerId = paidUserData.referredBy;
                const referrerRef = db.collection("users").doc(referrerId);
                const referrerSnap = await referrerRef.get();

                if (referrerSnap.exists) {
                    const referrerData = referrerSnap.data();
                    const now = new Date();
                    let newExpiry;

                    // If referrer is already on a paid plan, extend it. Otherwise, give them a new one.
                    if (referrerData.tier !== 'Free Tier' && referrerData.tierExpiry && new Date(referrerData.tierExpiry) > now) {
                        newExpiry = new Date(referrerData.tierExpiry);
                    } else {
                        newExpiry = now;
                    }
                    newExpiry.setDate(newExpiry.getDate() + 7); // Add 7 days

                    await referrerRef.update({
                        tier: 'Premium Tier',
                        tierExpiry: newExpiry.toISOString(),
                    });

                    // Notify the referrer via their history log
                    const historyRef = db.collection('users', referrerId, 'history');
                    await historyRef.add({ action: `Your referral ${paidUserData.username} subscribed! You've been rewarded with 1 week of Premium.`, createdAt: admin.firestore.FieldValue.serverTimestamp() });
                }
            }
            // --- End Referral Reward Logic ---

            // Log the verified transaction
            const transactionData = {
                amount: verificationData.amount,
                currency: verificationData.currency,
                description: `${tier} (${period})`,
                status: verificationData.status,
                transactionId: verificationData.id,
                tx_ref: verificationData.tx_ref,
                createdAt: admin.firestore.FieldValue.serverTimestamp(),
            };

            await subRef.set({
                transactions: admin.firestore.FieldValue.arrayUnion(transactionData),
            }, { merge: true });

            return { status: "success", message: `Successfully subscribed to ${tier}!` };
        } else {
            // 5. Verification failed
            throw new functions.https.HttpsError(
                "failed-precondition",
                "Payment verification failed. The transaction was not successful or data mismatch.",
                { details: verificationData },
            );
        }
    } catch (error) {
        console.error("Verification Error:", error.response ? error.response.data : error.message);
        throw new functions.https.HttpsError(
            "internal",
            "An error occurred while verifying the payment.",
            error.message,
        );
    }
});

/**
 * A scheduled function that runs daily to check for and handle expired subscriptions.
 */
exports.handleExpiredSubscriptions = functions.pubsub.schedule('every 24 hours').onRun(async (context) => {
    const now = new Date();
    console.log(`Running daily subscription check at: ${now.toISOString()}`);

    // Query for users whose subscription has expired and are not already on the Free Tier.
    const expiredUsersQuery = db.collection('users')
        .where('tier', '!=', 'Free Tier')
        .where('tierExpiry', '<=', now.toISOString());

    try {
        const snapshot = await expiredUsersQuery.get();

        if (snapshot.empty) {
            console.log('No expired subscriptions found.');
            return null;
        }

        const batch = db.batch();
        const historyPromises = [];

        snapshot.forEach(doc => {
            const userId = doc.id;
            console.log(`Processing expired user: ${userId}`);

            const userRef = db.collection('users').doc(userId);
            // Downgrade the user in the batch operation.
            batch.update(userRef, {
                tier: 'Free Tier',
                tierExpiry: null,
                autoRenew: false
            });

            // Add a history log entry. This is done separately from the batch.
            const historyRef = db.collection('users', userId, 'history');
            historyPromises.push(historyRef.add({ action: 'Subscription expired, reverted to Free Tier.', createdAt: admin.firestore.FieldValue.serverTimestamp() }));
        });

        await batch.commit(); // Commit all user downgrades at once.
        await Promise.all(historyPromises); // Wait for all history logs to be added.

        console.log(`Successfully processed ${snapshot.size} expired subscriptions.`);
        return null;
    } catch (error) {
        console.error('Error handling expired subscriptions:', error);
        // Throwing an error will cause the function to be retried.
        throw new functions.https.HttpsError('internal', 'Failed to process expired subscriptions.');
    }
});

/**
 * Sends a push notification to all eligible premium users.
 * This is a callable function, intended to be triggered by an admin panel or another secure process.
 */
exports.sendPredictionAlert = functions.https.onCall(async (data, context) => {
    // For production, you'd want to verify that the caller is an admin.
    // if (!context.auth.token.isAdmin) {
    //     throw new functions.https.HttpsError('permission-denied', 'Only admins can send alerts.');
    // }

    const { title, body, matchUrl } = data;
    if (!title || !body) {
        throw new functions.https.HttpsError('invalid-argument', 'Missing title or body.');
    }

    // 1. Find all users who are premium or higher and have notifications enabled.
    const premiumTiers = ["Premium Tier", "VIP / Elite Tier", "VVIP / Pro Elite Tier"];
    const usersSnapshot = await db.collection('users')
        .where('tier', 'in', premiumTiers)
        .where('notifications', '==', true)
        .get();

    if (usersSnapshot.empty) {
        return { status: 'success', message: 'No users to notify.' };
    }

    const tokens = [];
    usersSnapshot.forEach(doc => {
        const userTokens = doc.data().fcmTokens;
        if (Array.isArray(userTokens) && userTokens.length > 0) {
            tokens.push(...userTokens);
        }
    });

    if (tokens.length === 0) {
        return { status: 'success', message: 'Users found, but no notification tokens available.' };
    }

    // 2. Construct the notification payload.
    const payload = {
        notification: {
            title: title,
            body: body,
            icon: 'https://your-domain.com/icon-192.png', // Replace with your public icon URL
        },
        webpush: { fcm_options: { link: matchUrl || 'https://your-domain.com/' } } // Link to open on click
    };

    // 3. Send the messages.
    const response = await admin.messaging().sendToDevice(tokens, payload);
    console.log(`Successfully sent message to ${response.successCount} devices.`);
    return { status: 'success', message: `Notification sent to ${response.successCount} users.` };
});

/**
 * Deletes a user's account and all associated data.
 * This is a callable function triggered from the client.
 */
exports.deleteUserAccount = functions.https.onCall(async (data, context) => {
    // 1. Check for authentication
    if (!context.auth) {
        throw new functions.https.HttpsError(
            "unauthenticated",
            "You must be logged in to delete an account.",
        );
    }

    const uid = context.auth.uid;
    console.log(`Attempting to delete account for user: ${uid}`);

    try {
        // 2. Delete Firestore documents in a batch
        const batch = db.batch();
        const userDocRef = db.collection('users').doc(uid);
        const subDocRef = db.collection('subscriptions').doc(uid);

        batch.delete(userDocRef);
        batch.delete(subDocRef);

        await batch.commit();

        // 3. Delete the Firebase Auth user (this is the final step)
        await admin.auth().deleteUser(uid);

        console.log(`Successfully deleted all data for user: ${uid}`);
        return { status: 'success', message: 'Account deleted successfully.' };
    } catch (error) {
        console.error(`Failed to delete account for user ${uid}:`, error);
        throw new functions.https.HttpsError('internal', 'Failed to delete account.', error.message);
    }
});
# StatWise - AI Sports Prediction PWA

StatWise is a Progressive Web App (PWA) that provides users with AI-powered sports predictions. It features a multi-tiered subscription model, a referral program, and a comprehensive user profile management system, all built on a modern serverless stack with Firebase.

## ✨ Features

*   **Single Page Application (SPA):** A fast, seamless user experience with dynamic page loading and animated transitions.
*   **User Authentication:** Secure login, signup, and password reset functionality.
*   **Tiered Subscriptions:**
    *   Multiple subscription levels (Free, Premium, VIP, VVIP).
    *   Payment integration with **Flutterwave**.
    *   Client-side subscription status checks and downgrades on expiry.
    *   Tier-based access control for features and content.
*   **AI Predictions Homepage:**
    *   Displays a list of prediction cards with match details, pick, odds, and confidence levels.
    *   **Advanced Search:** Filter matches by name and use commands (`/odds`, `/c75`) to sort and filter.
    *   **Pull-to-Refresh:** Easily refresh the prediction list.
*   **Comprehensive User Profile:**
    *   Update username and profile picture (uploads to Firebase Storage).
    *   Change password with re-authentication.
    *   Dark mode toggle.
    *   View user statistics (member since, total predictions, win rate).
*   **History Tracking:**
    *   Tabbed view for **Predictions**, **Account Activity**, and **Transactions**.
    *   Tracks saved predictions and their outcomes (Win/Loss/Pending).
    *   Logs important account actions like login, logout, and profile updates.
*   **Referral System:**
    *   Each user gets a unique referral code.
    *   Share code via WhatsApp, Twitter, or the native Web Share API.
    *   View a list of users you've referred.
    *   Automatic reward claiming for successful referrals.
*   **Push Notifications:**
    *   Users can opt-in to receive push notifications via Firebase Cloud Messaging (FCM).
    *   Handles foreground and background messages.
*   **Progressive Web App (PWA):**
    *   Installable on user devices.
    *   **Offline Support:** A custom offline page is served via a Service Worker when the user has no network connection.
*   **Security:**
    *   Client-side deterrents to prevent basic inspection (disabling right-click, dev tools shortcuts).
    *   Secure, multi-step account deletion process.

## 🛠️ Tech Stack

*   **Frontend:** Vanilla JavaScript (ES Modules), HTML5, CSS3
*   **Backend (Serverless):**
    *   **Firebase Authentication:** For user management.
    *   **Firestore:** As the primary NoSQL database for user data, predictions, history, and subscriptions.
    *   **Firebase Storage:** For user profile picture uploads.
    *   **Firebase Cloud Messaging (FCM):** For push notifications.
*   **Payments:** Flutterwave
*   **UI/UX:**
    *   Intro.js: For the new user welcome tour.

## 📂 Project Structure

```
statwise/
├── Auth/
│   ├── auth.js         # Handles login, signup, password reset logic
│   └── login.html      # Login page
│   └── signup.html     # Signup page
│   └── forgot.html     # Forgot password page
├── Offline/
│   ├── offline.html    # Offline fallback page
│   └── offline.css
├── Pages/              # Dynamically loaded page content
│   ├── home.html
│   ├── history.html
│   ├── profile.html
│   ├── referral.html
│   ├── subscriptions.html
│   └── ...
├── Assets/
│   ├── Icons/
│   └── Fonts/
├── env.js              # Firebase & service keys (IMPORTANT: Should be gitignored)
├── env.example.js      # Template for environment variables
├── main.js             # Core application logic, router, state management
├── utils.js            # Utility functions (timestamp formatting, etc.)
├── index.html          # Main entry point of the app
├── styles.css          # Global styles
├── sw.js               # Service Worker for PWA features (offline, push)
└── README.md           # This file
```

## 🚀 Getting Started

Follow these instructions to get a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

*   A Firebase project.
*   A Flutterwave account for payment processing.
*   A local web server. You can use the `http-server` npm package for a quick setup:
    ```bash
    npm install -g http-server
    ```

### Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/statwise.git
    cd statwise
    ```

2.  **Configure Environment Variables:**
    *   Copy `env.example.js` to a new file named `env.js`.
    *   Open `env.js` and fill in your Firebase project configuration details in the `firebaseConfig` object.
    *   Add your Flutterwave public key to the `FLWPUBK` constant.

3.  **Update Service Worker Config:**
    *   Open `sw.js` and replace the placeholder `firebaseConfig` object with your own Firebase project configuration.

4.  **Update FCM VAPID Key:**
    *   In `main.js`, find the `initFirebaseMessaging` function.
    *   Replace the placeholder `'YOUR_VAPID_KEY_HERE'` with your actual VAPID key from the Firebase Console (*Project Settings > Cloud Messaging > Web configuration*).

5.  **Run the local server:**
    *   From the root of the project directory, run:
        ```bash
        http-server
        ```
    *   Open your browser and navigate to the local address provided by `http-server` (e.g., `http://127.0.0.1:8080`). You should start at the login page (`/Auth/login.html`).

### Firebase Setup Notes

*   **Authentication:** Enable "Email/Password" as a sign-in method in the Firebase Authentication console.
*   **Firestore:** Your database structure will be created automatically as users sign up and interact with the app. You will need to configure Firestore Security Rules to ensure data is accessed securely.
*   **Storage:** Ensure your Firebase Storage rules allow authenticated users to read and write to their own `profile_pictures/{userId}` path.

### ⚠️ Security Warning

The payment verification logic in this project is designed to be handled by a **Firebase Cloud Function** (`verifyPaymentAndGrantReward`). The client-side code calls this function but does not perform the verification itself. This is the recommended secure approach for production.

For a production environment, you **must** implement this Cloud Function to securely communicate with the Flutterwave API, verify transactions, and grant subscriptions and referral rewards. Client-side verification is insecure and can be easily bypassed.

---
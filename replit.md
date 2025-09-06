# StatWise - AI Sports Prediction PWA

## Overview

StatWise is a Progressive Web App (PWA) that provides AI-powered sports predictions with a tiered subscription model. The application is built as a single-page application (SPA) using vanilla JavaScript and Firebase services, offering users match predictions with confidence ratings, subscription management, referral programs, and comprehensive user profiles. The app is designed to work offline and can be installed on user devices as a native-like experience.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
The application follows a Single Page Application (SPA) pattern built with vanilla JavaScript ES modules. The main entry point is `index.html` with dynamic content loading managed by `main.js`. The routing system uses JavaScript to dynamically load page content without full page refreshes, providing smooth transitions between views.

The UI is organized into modular pages stored in the `Pages/` directory, including home, history, profile, subscriptions, insights, and referral management. CSS styling is centralized in `styles.css` with component-specific stylesheets for authentication and other specialized views.

### Backend Architecture (Serverless)
The application leverages Firebase's serverless architecture exclusively:

- **Authentication**: Firebase Authentication handles user registration, login, password reset, and session management
- **Database**: Firestore (NoSQL) stores user profiles, subscription data, prediction history, referral information, and account activity logs
- **File Storage**: Firebase Storage manages user profile picture uploads
- **Cloud Functions**: While `index.js` exists, the application has been refactored to use client-side logic instead of Firebase Cloud Functions to maintain free-tier compatibility

### Data Storage Design
The Firestore database uses a document-based structure with collections for users, predictions, history, and transactions. User data includes subscription tiers, referral codes, account activity, and saved predictions. The system implements client-side subscription status checks and automatic downgrades on expiry.

### Authentication and Authorization
Firebase Authentication provides secure user management with email/password authentication, password reset functionality, and session persistence options. The application includes tier-based access control that restricts features based on subscription levels (Free, Premium, VIP, VVIP). Client-side security measures include basic inspection deterrents and secure account deletion processes.

### Progressive Web App Features
The application implements PWA standards with a service worker (`sw.js`) that provides offline functionality and caching. Users can install the app on their devices, and the service worker serves an offline page when network connectivity is unavailable. The app includes a Web App Manifest for native-like installation experience.

### Push Notification System
Firebase Cloud Messaging (FCM) enables push notifications for prediction alerts and account updates. The system handles both foreground and background messages, allowing users to opt-in to receive notifications about new predictions and subscription changes.

## External Dependencies

### Payment Processing
- **Flutterwave**: Integrated for subscription payment processing with public API key configuration for frontend payment flows

### Firebase Services
- **Firebase Authentication**: User management and security
- **Firestore Database**: Primary data storage for all application data
- **Firebase Storage**: Profile picture and file uploads
- **Firebase Cloud Messaging**: Push notification delivery
- **Firebase Hosting**: Static site hosting configuration

### Third-Party Libraries
- **Vercel Analytics**: User behavior tracking and performance monitoring
- **HTTP Server**: Development server for local testing

### External APIs
- **IP Geolocation**: Public IP detection service (api.ipify.org) for user location tracking and security logging

### Development Tools
- **Node.js/NPM**: Package management and development dependencies
- **HTTP Server**: Development server configured for local testing on port 5000 with CORS enabled
- **Replit**: Development environment and deployment platform with autoscale deployment target

## Recent Changes

### September 6, 2025 - Project Setup and Authentication Fixes
- Successfully imported from GitHub and configured for Replit environment
- Set up workflow using http-server on port 5000 with CORS enabled for proper proxy support
- Configured deployment target as "autoscale" for production deployment
- Verified all Firebase services and dependencies are properly configured
- All static assets (CSS, JavaScript, fonts, icons) are loading correctly
- Progressive Web App features including service worker registration are functioning

### September 6, 2025 - Authentication and UI Improvements
- Fixed authentication issues by adding missing referral code generation to login flow
- Corrected variable naming typo in signup code that was causing authentication failures
- Updated Firestore rules compliance to ensure proper user document creation
- Removed background animation from main application pages for cleaner UI
- Kept background animation only on authentication pages (login, signup, forgot password)
- Removed background animation toggle from profile settings as requested
- Authentication flow and subscription management systems are now fully operational
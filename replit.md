# StatWise - AI Sports Prediction PWA

## Overview

StatWise is a Progressive Web App (PWA) that provides AI-powered sports predictions with a tiered subscription model. The application is built as a single-page application (SPA) using vanilla JavaScript and Firebase services, offering users match predictions with confidence ratings, subscription management, referral programs, and comprehensive user profiles. The app is designed to work offline and can be installed on user devices as a native-like experience.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
The application follows a Single Page Application (SPA) pattern built with vanilla JavaScript ES modules. The main entry point is `index.html` with dynamic content loading managed by `main.js`. The routing system uses JavaScript to dynamically load page content without full page refreshes, providing smooth transitions between views.

The UI is organized into modular pages stored in the `Pages/` directory, including home, history, profile, subscriptions, insights, and referral management. CSS styling is centralized in `styles.css` with component-specific stylesheets for authentication and other specialized views.

### Backend Architecture (Hybrid Serverless)
The application leverages a hybrid serverless architecture combining Firebase and Supabase:

- **Authentication**: Firebase Authentication handles user registration, login, password reset, and session management
- **Primary Database**: Firestore (NoSQL) stores user profiles, subscription data, prediction history, and account activity logs
- **Secondary Database**: Supabase (PostgreSQL) handles advanced analytics, referral system, and enhanced subscription tracking
- **File Storage**: Supabase Storage (primary) with Firebase Storage (fallback) for user profile picture uploads
- **Cross-Platform Sync**: Automatic data synchronization between Firebase and Supabase ensures data consistency and reliability
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
- **Firebase Storage**: Fallback storage for profile pictures and file uploads
- **Firebase Cloud Messaging**: Push notification delivery
- **Firebase Hosting**: Static site hosting configuration

### Supabase Services
- **Supabase Database (PostgreSQL)**: Advanced analytics, referral system, subscription tracking, and payment transaction logs
- **Supabase Storage**: Primary storage for profile pictures with automatic public URL generation
- **Row Level Security**: Secure data access with user-based permissions
- **Real-time Subscriptions**: Future capability for live data updates and notifications

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

### September 6, 2025 - Enhanced Mobile Experience and AI Chat Integration
- **Custom Pull-to-Refresh Icon**: Created custom SVG refresh icon matching app's primary blue theme colors (#0e639c)
- **Enhanced Mobile Pull-to-Refresh**: Added haptic feedback, elastic animations, better touch handling, and improved mobile responsiveness
- **App Tour Theme Integration**: Styled intro.js tour elements to match StatWise's blue theme with glassmorphism effects and consistent typography
- **AI Chat Feature**: Added floating chat button in bottom right corner with animated SVG icon featuring typing indicators and sparkle effects
- **Interactive AI Modal**: Implemented chat interface with app-themed styling, allowing users to ask about sports predictions and app features
- All new features use the app's signature blue gradient (#0e639c to #4caf50) and maintain visual consistency across the platform

### September 7, 2025 - Supabase Integration for Enhanced Data Management
- **Supabase Database Integration**: Added Supabase as a secondary database alongside Firebase for enhanced subscription and payment tracking
- **Hybrid Architecture**: Configured the app to use Firebase for authentication and general app data, while Supabase handles subscription analytics and payment logging
- **Payment Data Syncing**: Modified Flutterwave payment flow to automatically sync all transaction data to Supabase for better analytics and reporting
- **User Profile Synchronization**: Added automatic user profile syncing between Firebase and Supabase on authentication
- **Database Schema**: Created comprehensive Supabase database schema with tables for user profiles, subscription events, and payment transactions
- **Non-blocking Integration**: All Supabase operations are designed to be non-blocking, ensuring the main app functionality continues to work even if Supabase is unavailable
- **Enhanced Analytics**: Supabase integration provides better subscription analytics, payment tracking, and user behavior insights
- The integration maintains backward compatibility with existing Firebase functionality while adding powerful new data management capabilities

### September 7, 2025 - Critical Payment and Subscription System Fixes
- **Supabase Storage Integration**: Migrated profile picture storage from Firebase Storage to Supabase Storage with automatic fallback to Firebase for reliability
- **Enhanced Referral System**: Completely migrated referral system from Firebase to Supabase with new dedicated tables for referrals, referral codes, and reward tracking
- **Cross-Platform Sync**: Implemented automatic data synchronization between Firebase and Supabase for referral codes and user relationships
- **Improved Database Schema**: Extended Supabase schema with referral_codes and referrals tables, including proper indexing for optimal performance
- **Hybrid Storage Approach**: Profile picture uploads now use Supabase Storage as primary with Firebase Storage as fallback, ensuring 100% reliability
- **Smart Referral Management**: Referral page now intelligently fetches data from Supabase first, falling back to Firebase if needed, maintaining seamless user experience
- **Enhanced Referral Analytics**: New Supabase-based referral system provides better tracking of referral statistics, reward distribution, and user engagement metrics
- **Backward Compatibility**: All existing Firebase referral data continues to work while new referrals are created in Supabase, ensuring no data loss during migration
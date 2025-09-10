# StatWise - AI Sports Prediction PWA

## Overview

StatWise is a Progressive Web App (PWA) that provides AI-powered sports predictions with a tiered subscription model. The application is built as a single-page application (SPA) using vanilla JavaScript and Firebase services, offering users match predictions with confidence ratings, subscription management, referral programs, and comprehensive user profiles. The app is designed to work offline and can be installed on user devices as a native-like experience.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
The application follows a Single Page Application (SPA) pattern built with vanilla JavaScript ES modules. The main entry point is `index.html` with dynamic content loading managed by `main.js`. The routing system uses JavaScript to dynamically load page content without full page refreshes, providing smooth transitions between views.

The UI is organized into modular pages stored in the `Pages/` directory, including home, history, profile, subscriptions, insights, and referral management. CSS styling is centralized in `styles.css` with component-specific stylesheets for authentication and other specialized views.

### Backend Architecture (Supabase-Only)
The application now uses a complete Supabase serverless architecture:

- **Authentication**: Supabase Auth handles user registration, login, password reset, and session management with Row Level Security
- **Database**: PostgreSQL database with comprehensive schema including user profiles, subscription data, payment transactions, referral system, AI predictions, and admin features
- **File Storage**: Supabase Storage for user profile picture uploads with automatic public URL generation
- **Real-time Features**: Built-in real-time subscriptions for live data updates
- **Security**: Row Level Security (RLS) policies ensure secure data access based on user authentication
- **API**: Auto-generated REST and GraphQL APIs with built-in authentication and authorization

### Data Storage Design
The PostgreSQL database uses a relational structure with comprehensive tables for user profiles, predictions, subscription events, payment transactions, referrals, AI predictions, and admin features. The schema includes proper indexing, foreign key relationships, and JSONB fields for flexible data storage. Row Level Security ensures data access is properly controlled based on user authentication.

### Authentication and Authorization
Supabase Auth provides secure user management with email/password authentication, password reset functionality, and session persistence. The application includes tier-based access control with Row Level Security policies that restrict features and data access based on subscription levels (Free Tier, Premium Tier, VIP Tier, VVIP Tier). Database-level security ensures data integrity and proper user isolation.

### Progressive Web App Features
The application implements PWA standards with a service worker (`sw.js`) that provides offline functionality and caching. Users can install the app on their devices, and the service worker serves an offline page when network connectivity is unavailable. The app includes a Web App Manifest for native-like installation experience.

### Push Notification System
Firebase Cloud Messaging (FCM) enables push notifications for prediction alerts and account updates. The system handles both foreground and background messages, allowing users to opt-in to receive notifications about new predictions and subscription changes.

## External Dependencies

### Payment Processing
- **Flutterwave**: Integrated for subscription payment processing with public API key configuration for frontend payment flows

### Supabase Services (Complete Migration)
- **Supabase Auth**: User authentication and session management with Row Level Security
- **PostgreSQL Database**: Complete relational database with comprehensive schema for all application data
- **Supabase Storage**: File storage for profile pictures with automatic public URL generation
- **Real-time Subscriptions**: Live data updates and notifications
- **Auto-generated APIs**: REST and GraphQL APIs with built-in authentication

### Removed Services (Firebase Migration Complete)
- All Firebase services have been completely removed and replaced with Supabase equivalents
- Firebase Authentication, Firestore, Firebase Storage, and Firebase Cloud Messaging are no longer used
- All configuration files (firebase.json, firestore.rules, etc.) have been removed

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

### September 10, 2025 - Comprehensive UI/UX Enhancements and Bug Fixes
- **Fixed Remember Me Checkbox Bug**: Resolved clicking issue by removing nested label HTML structure that was preventing user interaction
- **Restored Interactive Circles Background**: Successfully implemented 20 responsive interactive circles with mouse/touch movement response, replacing geometric shapes animation
- **Created Legal Pages**: Built comprehensive privacy policy and terms of service pages with detailed legal content and proper timestamps
- **Enhanced Visual Styling**: Added CSS custom properties for consistent borders, shadows, and border-radius values with thicker 3px borders throughout app
- **Improved Profile Page Toggles**: Enhanced switch toggles with larger size (56x28px), smoother transitions, hover effects, and enhanced shadows for better UX
- **Upgraded Theme System**: Enhanced theme switching with CSS custom properties while preserving signature StatWise blue color (#0e639c) in all theme variations
- **Visual Consistency**: Applied enhanced header styling with 3px blue border and shadow effects, improved toggle responsiveness, and better visual feedback

### September 10, 2025 - GitHub Import Configuration for Replit Environment  
- **Successful GitHub Import**: Successfully imported the StatWise PWA project from GitHub repository
- **Dependency Installation**: Installed all required Node.js dependencies including @supabase/supabase-js, @vercel/analytics, and http-server
- **Workflow Configuration**: Set up development server workflow on port 5000 with CORS enabled for proper Replit proxy support
- **Production Deployment Setup**: Configured autoscale deployment target for production-ready hosting
- **Environment Verification**: Confirmed Supabase integration, authentication system, and all static assets are loading correctly
- **PWA Features Verified**: Service worker registration, offline support, and progressive web app features are functioning
- **Performance Optimization**: Development server configured with cache-control disabled (-c-1) for immediate updates during development

### September 9, 2025 - Complete Supabase Migration and GitHub Import Setup
- **Complete Firebase Removal**: All Firebase dependencies, configuration files, and code have been removed
- **Supabase-Only Architecture**: Migrated to 100% Supabase backend with PostgreSQL database, Auth, and Storage
- **Comprehensive Database Schema**: Implemented complete database schema with user profiles, subscriptions, payments, referrals, AI predictions, and admin features
- **Enhanced Security**: Added Row Level Security policies for all tables ensuring proper data access control
- **Authentication Rewrite**: Completely rewrote authentication system to use Supabase Auth with email/password authentication
- **Modern Architecture**: Streamlined to single backend provider eliminating complexity of hybrid Firebase/Supabase setup
- **Performance Improvements**: Direct database queries with proper indexing and relationships for better performance
- **Scalability Ready**: PostgreSQL database with JSONB support and real-time capabilities for future growth
- **GitHub Import Success**: Successfully imported project from GitHub and configured for Replit environment
- **Profile Picture Upload**: Added complete profile picture upload functionality using Supabase Storage bucket 'profile-pictures' with file validation, size limits, and immediate UI updates
- **Enhanced Avatar UI**: Improved avatar display with hover effects, camera icon overlay, and clickable upload functionality
- **Production Deployment**: Configured autoscale deployment target for production-ready hosting

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
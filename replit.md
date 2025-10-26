# StatWise - AI Sports Prediction PWA

## Overview
StatWise is a Progressive Web App (PWA) that provides AI-powered sports predictions with a tiered subscription model. Built as a Single Page Application (SPA) using vanilla JavaScript and a complete Supabase serverless architecture, it offers users match predictions with confidence ratings, subscription management, referral programs, and comprehensive user profiles. The application is designed for offline functionality and native-like installation on user devices, aiming for a scalable solution with secure data handling and real-time capabilities.

## User Preferences
Preferred communication style: Simple, everyday language.

## Recent Updates
- Removed inactive page feature completely from the application (October 26, 2025)
  - Deleted activity-manager.js and Pages/inactive.html
  - Removed all inactive page UI controls and CSS styling
  - Cleaned up all references from codebase
- Fixed multiple UX issues:
  - Removed annoying console spam from manager.js
  - Fixed signup form validation for privacy policy checkbox
  - Fixed loader hiding on all signup error paths
  - Fixed authentication page scrolling functionality
- Completely converted History feature to Forum with real-time messaging capabilities using Supabase real-time subscriptions
- Created forum_messages database table with proper user relationships and message threading
- Implemented comprehensive forum UI with message composition, real-time message display, and responsive design
- Fixed interactive background animation theme compatibility for both light and dark modes with proper color adaptation

## System Architecture

### Frontend Architecture
The application is a Single Page Application (SPA) using vanilla JavaScript ES modules, with `index.html` as the entry point and `main.js` managing dynamic content loading and client-side routing. UI components are modularized within the `Pages/` directory, and styling is handled by `styles.css` with component-specific additions.

### Backend Architecture (Supabase-Only)
The application utilizes a complete Supabase serverless architecture:
-   **Authentication**: Supabase Auth manages user authentication, including registration, login, password reset, and session management, reinforced with Row Level Security.
-   **Database**: A PostgreSQL database underpins the system, featuring a comprehensive schema for user profiles, predictions, subscription data, payment transactions, referral systems, and administrative functions, with proper indexing and foreign key relationships.
-   **File Storage**: Supabase Storage is used for user profile picture uploads, generating public URLs automatically.
-   **Real-time Features**: Built-in real-time subscriptions enable live data updates.
-   **Security**: Row Level Security (RLS) policies enforce secure data access based on user authentication and subscription tiers.
-   **API**: Auto-generated REST and GraphQL APIs include built-in authentication and authorization.

### Data Storage Design
The PostgreSQL database employs a relational structure with tables for users, predictions, subscriptions, payments, referrals, and admin data. It incorporates indexing, foreign keys, and JSONB fields for flexibility, with RLS ensuring data security.

### Authentication and Authorization
Supabase Auth provides secure user management. Access control is tier-based (Free, Premium, VIP, VVIP) via Row Level Security policies, restricting features and data according to subscription levels.

### Progressive Web App Features
The application is a PWA, utilizing a service worker (`sw.js`) for offline capabilities and caching. It supports installation on user devices and provides an offline page. A Web App Manifest ensures a native-like installation experience.

### Push Notification System
Firebase Cloud Messaging (FCM) is used for push notifications, sending prediction alerts and account updates, supporting both foreground and background messages.

## External Dependencies

### Payment Processing
-   **Flutterwave**: Integrated for subscription payment processing.

### Supabase Services
-   **Supabase Auth**: User authentication and session management.
-   **PostgreSQL Database**: Primary database for all application data.
-   **Supabase Storage**: File storage for profile pictures.
-   **Real-time Subscriptions**: Live data updates.
-   **Auto-generated APIs**: REST and GraphQL APIs.

### Third-Party Libraries
-   **Vercel Analytics**: User behavior tracking and performance monitoring.

### External APIs
-   **IP Geolocation**: api.ipify.org for public IP detection.

### Development Tools
-   **Node.js/NPM**: Package management.
-   **HTTP Server**: Development server for local testing.
-   **Replit**: Development environment and deployment platform.
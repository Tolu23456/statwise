-- StatWise Complete Supabase Database Schema
-- Complete schema for StatWise AI Sports Prediction PWA
-- Includes user management, payments, referrals, AI predictions, and admin features
-- Run this SQL in your Supabase SQL Editor to create the required tables

-- Enable UUID extensions if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Drop existing tables to ensure clean setup
DROP TABLE IF EXISTS forum_messages CASCADE;
DROP TABLE IF EXISTS prediction_accuracy CASCADE;
DROP TABLE IF EXISTS team_stats CASCADE;
DROP TABLE IF EXISTS matches CASCADE;
DROP TABLE IF EXISTS predictions CASCADE;
DROP TABLE IF EXISTS user_prediction_history CASCADE;
DROP TABLE IF EXISTS rewards CASCADE;
DROP TABLE IF EXISTS system_logs CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;
DROP TABLE IF EXISTS admin_users CASCADE;
DROP TABLE IF EXISTS account_history CASCADE;
DROP TABLE IF EXISTS admin_urls CASCADE;
DROP TABLE IF EXISTS referrals CASCADE;
DROP TABLE IF EXISTS referral_codes CASCADE;
DROP TABLE IF EXISTS payment_transactions CASCADE;
DROP TABLE IF EXISTS subscription_events CASCADE;
DROP TABLE IF EXISTS user_profiles CASCADE;

-- User profiles table (main user data)
CREATE TABLE user_profiles (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(100), -- User display name/username
    display_name VARCHAR(100), -- Alternative display name field
    current_tier VARCHAR(20) DEFAULT 'Free Tier',
    tier VARCHAR(20) DEFAULT 'Free Tier', -- Alternative tier field for compatibility
    subscription_period VARCHAR(20),
    subscription_start TIMESTAMP WITH TIME ZONE,
    subscription_end TIMESTAMP WITH TIME ZONE,
    subscription_status VARCHAR(20) DEFAULT 'active',
    tier_expiry TIMESTAMP WITH TIME ZONE, -- Alternative expiry field for compatibility
    referral_code VARCHAR(20),
    referred_by UUID, -- ID of user who referred this user
    total_referrals INTEGER DEFAULT 0,
    profile_picture_url TEXT, -- Supabase Storage URL for profile picture
    total_referral_rewards DECIMAL(10, 2) DEFAULT 0.00,
    is_admin BOOLEAN DEFAULT FALSE,
    is_new_user BOOLEAN DEFAULT TRUE,
    notifications BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Subscription events log
CREATE TABLE subscription_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL, -- 'subscription_purchase', 'upgrade', 'downgrade', 'renewal', 'cancellation'
    event_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Payment transactions log
CREATE TABLE payment_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    transaction_id VARCHAR(100) UNIQUE NOT NULL,
    tx_ref VARCHAR(100) NOT NULL,
    amount DECIMAL(10, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'NGN',
    status VARCHAR(20) NOT NULL, -- 'successful', 'failed', 'pending'
    payment_type VARCHAR(20) DEFAULT 'flutterwave',
    tier VARCHAR(20),
    period VARCHAR(20),
    user_ip VARCHAR(45),
    user_location TEXT,
    browser_info TEXT,
    admin_notification BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Referral system tables
CREATE TABLE referrals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    referred_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    referral_code VARCHAR(20) NOT NULL,
    reward_claimed BOOLEAN DEFAULT FALSE,
    reward_amount DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    claimed_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(referrer_id, referred_id)
);

-- Referral codes table for unique code management
CREATE TABLE referral_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE UNIQUE,
    code VARCHAR(20) UNIQUE NOT NULL,
    username VARCHAR(100),
    total_referrals INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Admin URLs table for secure admin access
CREATE TABLE admin_urls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_code VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    used_count INTEGER DEFAULT 0
);

-- ===== ADMIN MANAGEMENT TABLES =====

-- Admin users table for managing admin accounts
CREATE TABLE admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(100) NOT NULL,
    role VARCHAR(50) DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- System logs table for admin activity tracking
CREATE TABLE system_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id UUID REFERENCES admin_users(id),
    action TEXT NOT NULL,
    target_table VARCHAR(100),
    target_id UUID,
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- App settings table for application configuration
CREATE TABLE app_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    setting_key VARCHAR(100) UNIQUE NOT NULL,
    setting_value JSONB,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    updated_by UUID REFERENCES admin_users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Account history table for user action logging
CREATE TABLE account_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    ip VARCHAR(45),
    action_type VARCHAR(50) DEFAULT 'user_action',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Rewards table for referral reward tracking
CREATE TABLE rewards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    reward_tier VARCHAR(50),
    reward_duration_days INTEGER,
    claimed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    claimed_at TIMESTAMP WITH TIME ZONE,
    type VARCHAR(50) DEFAULT 'referral'
);

-- ===== AI PREDICTION SYSTEM TABLES =====

-- AI Predictions table for sports predictions
CREATE TABLE predictions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id VARCHAR NOT NULL,
    home_team VARCHAR NOT NULL,
    away_team VARCHAR NOT NULL,
    prediction VARCHAR NOT NULL,
    confidence INTEGER CHECK (confidence >= 0 AND confidence <= 100),
    odds DECIMAL(5,2),
    reasoning TEXT,
    kickoff_time TIMESTAMP WITH TIME ZONE NOT NULL,
    league VARCHAR NOT NULL,
    tier VARCHAR CHECK (tier IN ('free', 'premium', 'vip', 'vvip')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Matches table for tracking sports matches
CREATE TABLE matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_match_id VARCHAR UNIQUE NOT NULL,
    home_team VARCHAR NOT NULL,
    away_team VARCHAR NOT NULL,
    league VARCHAR NOT NULL,
    kickoff_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR DEFAULT 'upcoming',
    home_score INTEGER,
    away_score INTEGER,
    statistics JSONB,
    odds JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Team statistics table for AI analysis
CREATE TABLE team_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_name VARCHAR NOT NULL,
    league VARCHAR NOT NULL,
    current_form JSONB,
    season_stats JSONB,
    player_data JSONB,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Prediction accuracy tracking
CREATE TABLE prediction_accuracy (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date DATE NOT NULL,
    correct_predictions INTEGER DEFAULT 0,
    total_predictions INTEGER DEFAULT 0,
    confidence_accuracy JSONB,
    tier VARCHAR,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- User prediction history
CREATE TABLE user_prediction_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    prediction_id UUID REFERENCES predictions(id) ON DELETE CASCADE,
    saved_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    notes TEXT,
    UNIQUE(user_id, prediction_id)
);

-- ===== FORUM SYSTEM TABLES =====

-- Forum messages table for community discussions
CREATE TABLE forum_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    message TEXT NOT NULL CHECK (length(message) <= 500),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for better performance
CREATE INDEX idx_user_profiles_email ON user_profiles(email);
CREATE INDEX idx_user_profiles_referral_code ON user_profiles(referral_code);
CREATE INDEX idx_user_profiles_username ON user_profiles(username);
CREATE INDEX idx_user_profiles_current_tier ON user_profiles(current_tier);
CREATE INDEX idx_user_profiles_tier_expiry ON user_profiles(tier_expiry);
CREATE INDEX idx_user_profiles_referred_by ON user_profiles(referred_by);
CREATE INDEX idx_subscription_events_user_id ON subscription_events(user_id);
CREATE INDEX idx_subscription_events_created_at ON subscription_events(created_at);
CREATE INDEX idx_payment_transactions_user_id ON payment_transactions(user_id);
CREATE INDEX idx_payment_transactions_transaction_id ON payment_transactions(transaction_id);
CREATE INDEX idx_payment_transactions_created_at ON payment_transactions(created_at);
CREATE INDEX idx_referrals_referrer_id ON referrals(referrer_id);
CREATE INDEX idx_referrals_referred_id ON referrals(referred_id);
CREATE INDEX idx_referrals_created_at ON referrals(created_at);
CREATE INDEX idx_referral_codes_code ON referral_codes(code);
CREATE INDEX idx_referral_codes_user_id ON referral_codes(user_id);
CREATE INDEX idx_admin_urls_admin_code ON admin_urls(admin_code);
CREATE INDEX idx_admin_urls_expires_at ON admin_urls(expires_at);

-- Admin Management Table Indexes
CREATE INDEX idx_admin_users_email ON admin_users(email);
CREATE INDEX idx_system_logs_admin_user_id ON system_logs(admin_user_id);
CREATE INDEX idx_system_logs_created_at ON system_logs(created_at);
CREATE INDEX idx_app_settings_setting_key ON app_settings(setting_key);
CREATE INDEX idx_account_history_user_id ON account_history(user_id);
CREATE INDEX idx_account_history_created_at ON account_history(created_at);
CREATE INDEX idx_rewards_referrer_id ON rewards(referrer_id);
CREATE INDEX idx_rewards_created_at ON rewards(created_at);

-- AI Prediction System Indexes
CREATE INDEX idx_predictions_tier ON predictions(tier);
CREATE INDEX idx_predictions_kickoff_time ON predictions(kickoff_time);
CREATE INDEX idx_predictions_league ON predictions(league);
CREATE INDEX idx_predictions_created_at ON predictions(created_at);
CREATE INDEX idx_matches_external_match_id ON matches(external_match_id);
CREATE INDEX idx_matches_kickoff_time ON matches(kickoff_time);
CREATE INDEX idx_matches_league ON matches(league);
CREATE INDEX idx_matches_status ON matches(status);
CREATE INDEX idx_team_stats_team_name ON team_stats(team_name);
CREATE INDEX idx_team_stats_league ON team_stats(league);
CREATE INDEX idx_prediction_accuracy_date ON prediction_accuracy(date);
CREATE INDEX idx_prediction_accuracy_tier ON prediction_accuracy(tier);
CREATE INDEX idx_user_prediction_history_user_id ON user_prediction_history(user_id);
CREATE INDEX idx_user_prediction_history_saved_at ON user_prediction_history(saved_at);

-- Forum Message Indexes
CREATE INDEX idx_forum_messages_user_id ON forum_messages(user_id);
CREATE INDEX idx_forum_messages_created_at ON forum_messages(created_at);
CREATE INDEX idx_forum_messages_username ON forum_messages(username);

-- Enable Row Level Security (RLS) for all tables
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_urls ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE rewards ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE prediction_accuracy ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_prediction_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE forum_messages ENABLE ROW LEVEL SECURITY;

-- Comprehensive Security Policies for User and Admin Operations
-- Drop existing policies first to avoid conflicts
DROP POLICY IF EXISTS "Users can view their own profile" ON user_profiles;
DROP POLICY IF EXISTS "Users can insert their own profile" ON user_profiles;
DROP POLICY IF EXISTS "Users can update their own profile" ON user_profiles;
DROP POLICY IF EXISTS "Allow public read for referral validation" ON user_profiles;

-- User Profiles Policies
-- Use id::text = auth.uid() to avoid UUID vs text mismatch when auth.uid() returns text
CREATE POLICY "Users can view their own profile" ON user_profiles
    FOR SELECT USING (id::text = auth.uid());

CREATE POLICY "Users can insert their own profile" ON user_profiles
    FOR INSERT WITH CHECK (id::text = auth.uid());

CREATE POLICY "Users can update their own profile" ON user_profiles
    FOR UPDATE USING (id::text = auth.uid());

CREATE POLICY "Allow public read for referral validation" ON user_profiles
    FOR SELECT USING (true);

-- Subscription Events Policies
DROP POLICY IF EXISTS "Users can view their own subscription events" ON subscription_events;
DROP POLICY IF EXISTS "Users can insert their own subscription events" ON subscription_events;
DROP POLICY IF EXISTS "Allow system updates to subscription events" ON subscription_events;

CREATE POLICY "Users can view their own subscription events" ON subscription_events
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own subscription events" ON subscription_events
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Allow system updates to subscription events" ON subscription_events
    FOR UPDATE USING (true);

-- Payment Transactions Policies
DROP POLICY IF EXISTS "Users can view their own payment transactions" ON payment_transactions;
DROP POLICY IF EXISTS "Users can insert their own payment transactions" ON payment_transactions;
DROP POLICY IF EXISTS "Allow system updates to payment transactions" ON payment_transactions;

CREATE POLICY "Users can view their own payment transactions" ON payment_transactions
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own payment transactions" ON payment_transactions
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Allow system updates to payment transactions" ON payment_transactions
    FOR UPDATE USING (true);

-- Referrals Policies
DROP POLICY IF EXISTS "Users can view referrals they made or received" ON referrals;
DROP POLICY IF EXISTS "Users can insert referrals" ON referrals;
DROP POLICY IF EXISTS "Users can update their own referrals" ON referrals;

CREATE POLICY "Users can view referrals they made or received" ON referrals
    FOR SELECT USING (referrer_id = auth.uid() OR referred_id = auth.uid());

CREATE POLICY "Users can insert referrals" ON referrals
    FOR INSERT WITH CHECK (true);

CREATE POLICY "Users can update their own referrals" ON referrals
    FOR UPDATE USING (referrer_id = auth.uid() OR referred_id = auth.uid());

-- Referral Codes Policies
DROP POLICY IF EXISTS "Users can view their own referral codes" ON referral_codes;
DROP POLICY IF EXISTS "Allow public read for referral code validation" ON referral_codes;
DROP POLICY IF EXISTS "Users can insert their own referral codes" ON referral_codes;
DROP POLICY IF EXISTS "Users can update their own referral codes" ON referral_codes;

CREATE POLICY "Users can view their own referral codes" ON referral_codes
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Allow public read for referral code validation" ON referral_codes
    FOR SELECT USING (active = true);

CREATE POLICY "Users can insert their own referral codes" ON referral_codes
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own referral codes" ON referral_codes
    FOR UPDATE USING (user_id = auth.uid());

-- Admin URLs Policies (unrestricted for system operations)
DROP POLICY IF EXISTS "Allow all operations on admin urls" ON admin_urls;

CREATE POLICY "Allow all operations on admin urls" ON admin_urls
    FOR ALL USING (true);

-- ===== ADMIN MANAGEMENT POLICIES =====

-- Admin Users Policies
DROP POLICY IF EXISTS "Admin users can view all admin accounts" ON admin_users;
DROP POLICY IF EXISTS "Admin users can update their own profile" ON admin_users;

CREATE POLICY "Admin users can view all admin accounts" ON admin_users
    FOR SELECT USING (true);

CREATE POLICY "Admin users can update their own profile" ON admin_users
    FOR UPDATE USING (email = (auth.jwt() ->> 'email'));

-- System Logs Policies
DROP POLICY IF EXISTS "Admin users can view all system logs" ON system_logs;
DROP POLICY IF EXISTS "Admin users can insert system logs" ON system_logs;

CREATE POLICY "Admin users can view all system logs" ON system_logs
    FOR SELECT USING (true);

CREATE POLICY "Admin users can insert system logs" ON system_logs
    FOR INSERT WITH CHECK (true);

-- App Settings Policies
DROP POLICY IF EXISTS "All users can view active app settings" ON app_settings;
DROP POLICY IF EXISTS "Admin users can manage app settings" ON app_settings;

CREATE POLICY "All users can view active app settings" ON app_settings
    FOR SELECT USING (is_active = true);

CREATE POLICY "Admin users can manage app settings" ON app_settings
    FOR ALL USING (true);

-- Account History Policies
DROP POLICY IF EXISTS "Users can view their own account history" ON account_history;
DROP POLICY IF EXISTS "Users can insert their own account history" ON account_history;

CREATE POLICY "Users can view their own account history" ON account_history
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert their own account history" ON account_history
    FOR INSERT WITH CHECK (user_id = auth.uid());

-- Rewards Policies
DROP POLICY IF EXISTS "Users can view their own rewards" ON rewards;
DROP POLICY IF EXISTS "Users can insert rewards" ON rewards;
DROP POLICY IF EXISTS "Users can update their own rewards" ON rewards;

CREATE POLICY "Users can view their own rewards" ON rewards
    FOR SELECT USING (referrer_id = auth.uid());

CREATE POLICY "Users can insert rewards" ON rewards
    FOR INSERT WITH CHECK (true);

CREATE POLICY "Users can update their own rewards" ON rewards
    FOR UPDATE USING (referrer_id = auth.uid());

-- ===== AI PREDICTION SYSTEM POLICIES =====

-- Predictions Policies (tier-based access)
DROP POLICY IF EXISTS "Users can view predictions based on their subscription tier" ON predictions;
DROP POLICY IF EXISTS "Allow system operations on predictions" ON predictions;

CREATE POLICY "Users can view predictions based on their subscription tier" ON predictions
    FOR SELECT USING (
        CASE 
            WHEN tier = 'free' THEN true
            WHEN tier = 'premium' THEN EXISTS (
                SELECT 1 FROM user_profiles 
                WHERE id = auth.uid() 
                AND current_tier IN ('Premium Tier', 'VIP Tier', 'VVIP Tier')
            )
            WHEN tier = 'vip' THEN EXISTS (
                SELECT 1 FROM user_profiles 
                WHERE id = auth.uid() 
                AND current_tier IN ('VIP Tier', 'VVIP Tier')
            )
            WHEN tier = 'vvip' THEN EXISTS (
                SELECT 1 FROM user_profiles 
                WHERE id = auth.uid() 
                AND current_tier = 'VVIP Tier'
            )
            ELSE false
        END
    );

-- System can insert/update predictions (for AI service)
CREATE POLICY "Allow system operations on predictions" ON predictions
    FOR ALL USING (true);

-- Matches Policies (public read for sports data)
DROP POLICY IF EXISTS "Users can view match data" ON matches;
DROP POLICY IF EXISTS "Allow system operations on matches" ON matches;

CREATE POLICY "Users can view match data" ON matches
    FOR SELECT USING (true);

CREATE POLICY "Allow system operations on matches" ON matches
    FOR ALL USING (true);

-- Team Stats Policies (public read for analysis)
DROP POLICY IF EXISTS "Users can view team statistics" ON team_stats;
DROP POLICY IF EXISTS "Allow system operations on team stats" ON team_stats;

CREATE POLICY "Users can view team statistics" ON team_stats
    FOR SELECT USING (true);

CREATE POLICY "Allow system operations on team stats" ON team_stats
    FOR ALL USING (true);

-- Prediction Accuracy Policies (public read)
DROP POLICY IF EXISTS "Users can view prediction accuracy" ON prediction_accuracy;
DROP POLICY IF EXISTS "Allow system operations on prediction accuracy" ON prediction_accuracy;

CREATE POLICY "Users can view prediction accuracy" ON prediction_accuracy
    FOR SELECT USING (true);

CREATE POLICY "Allow system operations on prediction accuracy" ON prediction_accuracy
    FOR ALL USING (true);

-- User Prediction History Policies
DROP POLICY IF EXISTS "Users can view their own prediction history" ON user_prediction_history;
DROP POLICY IF EXISTS "Users can save predictions to their history" ON user_prediction_history;
DROP POLICY IF EXISTS "Users can update their own prediction notes" ON user_prediction_history;
DROP POLICY IF EXISTS "Users can delete from their prediction history" ON user_prediction_history;

CREATE POLICY "Users can view their own prediction history" ON user_prediction_history
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can save predictions to their history" ON user_prediction_history
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Users can update their own prediction notes" ON user_prediction_history
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY "Users can delete from their prediction history" ON user_prediction_history
    FOR DELETE USING (user_id = auth.uid());

-- ===== FORUM MESSAGE POLICIES =====

-- Forum Messages Policies (public read, authenticated write)
DROP POLICY IF EXISTS "Users can view all forum messages" ON forum_messages;
DROP POLICY IF EXISTS "Authenticated users can send forum messages" ON forum_messages;
DROP POLICY IF EXISTS "Users can update their own forum messages" ON forum_messages;
DROP POLICY IF EXISTS "Users can delete their own forum messages" ON forum_messages;

CREATE POLICY "Users can view all forum messages" ON forum_messages
    FOR SELECT USING (true);

CREATE POLICY "Authenticated users can send forum messages" ON forum_messages
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own forum messages" ON forum_messages
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own forum messages" ON forum_messages
    FOR DELETE USING (auth.uid() = user_id);

-- ===== FUNCTIONS AND TRIGGERS =====

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Add trigger for forum_messages
CREATE TRIGGER update_forum_messages_updated_at
    BEFORE UPDATE ON forum_messages
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Storage bucket policies for profile pictures
-- Note: Create bucket named 'profile-pictures' in Supabase Storage UI and make it public
-- Drop existing policies first to avoid conflicts
DROP POLICY IF EXISTS "Users can upload their own profile pictures" ON storage.objects;
DROP POLICY IF EXISTS "Users can view profile pictures" ON storage.objects;
DROP POLICY IF EXISTS "Users can update their own profile pictures" ON storage.objects;
DROP POLICY IF EXISTS "Users can delete their own profile pictures" ON storage.objects;

CREATE POLICY "Users can upload their own profile pictures" ON storage.objects
    FOR INSERT WITH CHECK (bucket_id = 'profile-pictures' AND auth.uid()::text = (storage.foldername(name))[1]);

CREATE POLICY "Users can view profile pictures" ON storage.objects
    FOR SELECT USING (bucket_id = 'profile-pictures');

CREATE POLICY "Users can update their own profile pictures" ON storage.objects
    FOR UPDATE USING (bucket_id = 'profile-pictures' AND auth.uid()::text = (storage.foldername(name))[1]);

CREATE POLICY "Users can delete their own profile pictures" ON storage.objects
    FOR DELETE USING (bucket_id = 'profile-pictures' AND auth.uid()::text = (storage.foldername(name))[1]);

-- Clean up expired admin URLs (run periodically to maintain database hygiene)
DELETE FROM admin_urls WHERE expires_at < NOW() AND is_active = false;

-- Grant necessary permissions for authenticated users
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;

-- Grant permissions for anonymous users (for public operations like referral validation and predictions)
GRANT USAGE ON SCHEMA public TO anon;
GRANT SELECT ON user_profiles TO anon;
GRANT SELECT ON referral_codes TO anon;
GRANT SELECT ON predictions TO anon;
GRANT SELECT ON matches TO anon;
GRANT SELECT ON team_stats TO anon;
GRANT SELECT ON prediction_accuracy TO anon;
GRANT SELECT ON app_settings TO anon;

-- Example data cleanup function (optional)
CREATE OR REPLACE FUNCTION cleanup_expired_admin_urls()
RETURNS void AS $$
BEGIN
    DELETE FROM admin_urls 
    WHERE expires_at < NOW() AND is_active = false;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
-- Supabase Database Schema for StatWise
-- Run this SQL in your Supabase SQL Editor to create the required tables

-- User profiles table (synced from Firebase)
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    display_name VARCHAR(100),
    current_tier VARCHAR(20) DEFAULT 'Free Tier',
    subscription_period VARCHAR(20),
    subscription_start TIMESTAMP WITH TIME ZONE,
    subscription_end TIMESTAMP WITH TIME ZONE,
    subscription_status VARCHAR(20) DEFAULT 'active',
    referral_code VARCHAR(20),
    total_referrals INTEGER DEFAULT 0,
    profile_picture_url TEXT, -- Supabase Storage URL for profile picture
    total_referral_rewards DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Subscription events log
CREATE TABLE IF NOT EXISTS subscription_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL, -- 'subscription_purchase', 'upgrade', 'downgrade', 'renewal', 'cancellation'
    event_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Payment transactions log
CREATE TABLE IF NOT EXISTS payment_transactions (
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Referral system tables
CREATE TABLE IF NOT EXISTS referrals (
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
CREATE TABLE IF NOT EXISTS referral_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE UNIQUE,
    code VARCHAR(20) UNIQUE NOT NULL,
    total_uses INTEGER DEFAULT 0,
    total_rewards DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Storage bucket policy for profile pictures
-- Note: This needs to be set up in Supabase Storage UI as well
-- CREATE POLICY "Users can upload their own profile pictures" ON storage.objects
-- FOR INSERT WITH CHECK (bucket_id = 'profile-pictures' AND auth.uid()::text = (storage.foldername(name))[1]);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);
CREATE INDEX IF NOT EXISTS idx_subscription_events_user_id ON subscription_events(user_id);
CREATE INDEX IF NOT EXISTS idx_subscription_events_created_at ON subscription_events(created_at);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_user_id ON payment_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_transaction_id ON payment_transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_created_at ON payment_transactions(created_at);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referred_id ON referrals(referred_id);
CREATE INDEX IF NOT EXISTS idx_referrals_created_at ON referrals(created_at);
CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON referral_codes(code);
CREATE INDEX IF NOT EXISTS idx_referral_codes_user_id ON referral_codes(user_id);

-- Row Level Security (RLS) policies
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_transactions ENABLE ROW LEVEL SECURITY;

-- Note: For testing purposes, you might want to temporarily disable RLS
-- or create policies that allow all operations. In production, you should
-- implement proper RLS policies based on your authentication setup.

-- Example policies (uncomment when you have proper auth setup):
-- CREATE POLICY "Users can view their own profile" ON user_profiles
--     FOR SELECT USING (id = auth.uid()::text::uuid);

-- CREATE POLICY "Users can update their own profile" ON user_profiles
--     FOR UPDATE USING (id = auth.uid()::text::uuid);

-- CREATE POLICY "Users can view their own subscription events" ON subscription_events
--     FOR SELECT USING (user_id = auth.uid()::text::uuid);

-- CREATE POLICY "Users can view their own payment transactions" ON payment_transactions
--     FOR SELECT USING (user_id = auth.uid()::text::uuid);

-- For now, let's allow all operations for testing (REMOVE IN PRODUCTION):
CREATE POLICY "Allow all operations on user_profiles" ON user_profiles
    FOR ALL USING (true);

CREATE POLICY "Allow all operations on subscription_events" ON subscription_events
    FOR ALL USING (true);

CREATE POLICY "Allow all operations on payment_transactions" ON payment_transactions
    FOR ALL USING (true);
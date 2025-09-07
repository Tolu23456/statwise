-- Supabase Database Schema for StatWise (Safe Version)
-- This version handles existing tables and policies gracefully

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
    profile_picture_url TEXT,
    total_referral_rewards DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Subscription events log
CREATE TABLE IF NOT EXISTS subscription_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
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
    status VARCHAR(20) NOT NULL,
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

-- Create indexes for better performance (IF NOT EXISTS not needed for indexes)
DO $$ BEGIN
    CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);
EXCEPTION
    WHEN duplicate_table THEN NULL;
END $$;

DO $$ BEGIN
    CREATE INDEX IF NOT EXISTS idx_subscription_events_user_id ON subscription_events(user_id);
EXCEPTION
    WHEN duplicate_table THEN NULL;
END $$;

DO $$ BEGIN
    CREATE INDEX IF NOT EXISTS idx_payment_transactions_user_id ON payment_transactions(user_id);
EXCEPTION
    WHEN duplicate_table THEN NULL;
END $$;

DO $$ BEGIN
    CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON referrals(referrer_id);
EXCEPTION
    WHEN duplicate_table THEN NULL;
END $$;

DO $$ BEGIN
    CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON referral_codes(code);
EXCEPTION
    WHEN duplicate_table THEN NULL;
END $$;

-- Enable Row Level Security
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE referral_codes ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist (to avoid conflicts)
DROP POLICY IF EXISTS "Allow all operations on user_profiles" ON user_profiles;
DROP POLICY IF EXISTS "Allow all operations on subscription_events" ON subscription_events;
DROP POLICY IF EXISTS "Allow all operations on payment_transactions" ON payment_transactions;
DROP POLICY IF EXISTS "Allow all operations on referrals" ON referrals;
DROP POLICY IF EXISTS "Allow all operations on referral_codes" ON referral_codes;

-- Create policies for testing (REMOVE IN PRODUCTION)
CREATE POLICY "Allow all operations on user_profiles" ON user_profiles
    FOR ALL USING (true);

CREATE POLICY "Allow all operations on subscription_events" ON subscription_events
    FOR ALL USING (true);

CREATE POLICY "Allow all operations on payment_transactions" ON payment_transactions
    FOR ALL USING (true);

CREATE POLICY "Allow all operations on referrals" ON referrals
    FOR ALL USING (true);

CREATE POLICY "Allow all operations on referral_codes" ON referral_codes
    FOR ALL USING (true);
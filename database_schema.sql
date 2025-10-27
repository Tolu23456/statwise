-- StatWise PWA Database Schema for Supabase
-- This file contains the complete database schema for the StatWise sports prediction application

-- Enable UUID extensions if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Enable Row Level Security (RLS)
ALTER DATABASE postgres SET "app.jwt_secret" TO 'your-jwt-secret';

-- ===== USERS AND PROFILES =====

-- User Profiles Table
CREATE TABLE IF NOT EXISTS public.user_profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE,
    display_name TEXT,
    current_tier TEXT DEFAULT 'Free Tier' CHECK (current_tier IN ('Free Tier', 'Premium Tier', 'VIP Tier', 'VVIP Tier')),
    tier TEXT DEFAULT 'Free Tier' CHECK (tier IN ('Free Tier', 'Premium Tier', 'VIP Tier', 'VVIP Tier')),
    subscription_status TEXT DEFAULT 'active' CHECK (subscription_status IN ('active', 'expired', 'cancelled')),
    subscription_period TEXT CHECK (subscription_period IN ('daily', 'monthly')),
    subscription_start TIMESTAMPTZ,
    subscription_end TIMESTAMPTZ,
    tier_expiry TIMESTAMPTZ,
    is_new_user BOOLEAN DEFAULT true,
    notifications BOOLEAN DEFAULT true,
    total_referrals INTEGER DEFAULT 0,
    total_rewards DECIMAL(10,2) DEFAULT 0.00,
    profile_picture TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_login TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ===== PREDICTIONS AND AI DATA =====

-- AI Predictions Table
CREATE TABLE IF NOT EXISTS public.predictions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    team1 TEXT NOT NULL,
    team2 TEXT NOT NULL,
    prediction TEXT NOT NULL,
    confidence INTEGER CHECK (confidence >= 0 AND confidence <= 100),
    odds DECIMAL(5,2),
    kickoff_time TIMESTAMPTZ NOT NULL,
    league TEXT NOT NULL,
    tier TEXT DEFAULT 'free' CHECK (tier IN ('free', 'premium', 'vip', 'vvip')),
    match_type TEXT,
    reasoning TEXT,
    ai_model TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'won', 'lost', 'void')),
    result TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- User Prediction History (Saved Predictions)
CREATE TABLE IF NOT EXISTS public.user_prediction_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    prediction_id UUID NOT NULL REFERENCES public.predictions(id) ON DELETE CASCADE,
    saved_at TIMESTAMPTZ DEFAULT now(),
    notes TEXT,
    UNIQUE(user_id, prediction_id)
);

-- ===== FORUM SYSTEM =====

-- Forum Messages Table
CREATE TABLE IF NOT EXISTS public.forum_messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    message TEXT NOT NULL CHECK (length(message) <= 500),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ===== SUBSCRIPTION AND PAYMENTS =====

-- Subscription Events
CREATE TABLE IF NOT EXISTS public.subscription_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('upgrade', 'downgrade', 'renewal', 'cancellation', 'expiry')),
    from_tier TEXT,
    to_tier TEXT,
    period TEXT CHECK (period IN ('daily', 'monthly')),
    amount DECIMAL(10,2),
    currency TEXT DEFAULT 'NGN',
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Payment Transactions
CREATE TABLE IF NOT EXISTS public.payment_transactions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    transaction_id TEXT UNIQUE NOT NULL,
    tx_ref TEXT,
    amount DECIMAL(10,2) NOT NULL,
    currency TEXT DEFAULT 'NGN',
    status TEXT NOT NULL CHECK (status IN ('pending', 'successful', 'failed', 'cancelled')),
    payment_type TEXT DEFAULT 'flutterwave',
    tier TEXT NOT NULL,
    period TEXT NOT NULL CHECK (period IN ('daily', 'monthly')),
    payment_data JSONB,
    flutterwave_data JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- ===== REFERRAL SYSTEM =====

-- Referral Codes
CREATE TABLE IF NOT EXISTS public.referral_codes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    code TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    total_referrals INTEGER DEFAULT 0,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Referrals Relationships
CREATE TABLE IF NOT EXISTS public.referrals (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    referrer_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    referred_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
    referral_code TEXT NOT NULL,
    reward_claimed BOOLEAN DEFAULT false,
    reward_amount DECIMAL(10,2) DEFAULT 500.00,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(referrer_id, referred_id)
);

-- ===== INDEXES FOR PERFORMANCE =====

-- User profiles indexes
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON public.user_profiles(email);
CREATE INDEX IF NOT EXISTS idx_user_profiles_username ON public.user_profiles(username);
CREATE INDEX IF NOT EXISTS idx_user_profiles_tier ON public.user_profiles(current_tier);

-- Predictions indexes
CREATE INDEX IF NOT EXISTS idx_predictions_kickoff ON public.predictions(kickoff_time);
CREATE INDEX IF NOT EXISTS idx_predictions_league ON public.predictions(league);
CREATE INDEX IF NOT EXISTS idx_predictions_tier ON public.predictions(tier);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON public.predictions(status);

-- User prediction history indexes
CREATE INDEX IF NOT EXISTS idx_user_history_user_id ON public.user_prediction_history(user_id);
CREATE INDEX IF NOT EXISTS idx_user_history_saved_at ON public.user_prediction_history(saved_at);

-- Forum messages indexes
CREATE INDEX IF NOT EXISTS idx_forum_messages_user_id ON public.forum_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_forum_messages_created_at ON public.forum_messages(created_at);
CREATE INDEX IF NOT EXISTS idx_forum_messages_username ON public.forum_messages(username);

-- Payment transactions indexes
CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON public.payment_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON public.payment_transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_created_at ON public.payment_transactions(created_at);

-- Referral indexes
CREATE INDEX IF NOT EXISTS idx_referral_codes_code ON public.referral_codes(code);
CREATE INDEX IF NOT EXISTS idx_referral_codes_user_id ON public.referral_codes(user_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON public.referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referred_id ON public.referrals(referred_id);

-- ===== ROW LEVEL SECURITY (RLS) POLICIES =====

-- Enable RLS on all tables
ALTER TABLE public.user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_prediction_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forum_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subscription_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.referral_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.referrals ENABLE ROW LEVEL SECURITY;

-- User profiles policies
CREATE POLICY "Users can view their own profile" ON public.user_profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update their own profile" ON public.user_profiles
    FOR UPDATE USING (auth.uid() = id);

CREATE POLICY "Users can insert their own profile" ON public.user_profiles
    FOR INSERT WITH CHECK (auth.uid() = id);

-- Predictions policies - tier-based access
CREATE POLICY "Free users can view free predictions" ON public.predictions
    FOR SELECT USING (
        tier = 'free' OR 
        (auth.uid() IN (SELECT id FROM public.user_profiles WHERE current_tier IN ('Premium Tier', 'VIP Tier', 'VVIP Tier') AND tier IN ('free', 'premium'))) OR
        (auth.uid() IN (SELECT id FROM public.user_profiles WHERE current_tier IN ('VIP Tier', 'VVIP Tier') AND tier IN ('free', 'premium', 'vip'))) OR
        (auth.uid() IN (SELECT id FROM public.user_profiles WHERE current_tier = 'VVIP Tier'))
    );

-- User prediction history policies
CREATE POLICY "Users can view their own prediction history" ON public.user_prediction_history
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own prediction history" ON public.user_prediction_history
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own prediction history" ON public.user_prediction_history
    FOR DELETE USING (auth.uid() = user_id);

-- Forum messages policies
CREATE POLICY "Users can view all forum messages" ON public.forum_messages
    FOR SELECT USING (true);

CREATE POLICY "Authenticated users can send forum messages" ON public.forum_messages
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own forum messages" ON public.forum_messages
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own forum messages" ON public.forum_messages
    FOR DELETE USING (auth.uid() = user_id);

-- Subscription events policies
CREATE POLICY "Users can view their own subscription events" ON public.subscription_events
    FOR SELECT USING (auth.uid() = user_id);

-- Payment transactions policies
CREATE POLICY "Users can view their own transactions" ON public.payment_transactions
    FOR SELECT USING (auth.uid() = user_id);

-- Referral codes policies
CREATE POLICY "Users can view their own referral codes" ON public.referral_codes
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own referral codes" ON public.referral_codes
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own referral codes" ON public.referral_codes
    FOR UPDATE USING (auth.uid() = user_id);

-- Referrals policies
CREATE POLICY "Users can view referrals they made" ON public.referrals
    FOR SELECT USING (auth.uid() = referrer_id);

CREATE POLICY "Users can view referrals where they were referred" ON public.referrals
    FOR SELECT USING (auth.uid() = referred_id);

-- ===== FUNCTIONS AND TRIGGERS =====

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
CREATE TRIGGER update_user_profiles_updated_at BEFORE UPDATE ON public.user_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_predictions_updated_at BEFORE UPDATE ON public.predictions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_payment_transactions_updated_at BEFORE UPDATE ON public.payment_transactions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_referral_codes_updated_at BEFORE UPDATE ON public.referral_codes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_forum_messages_updated_at BEFORE UPDATE ON public.forum_messages
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ===== SAMPLE DATA (Optional) =====

-- Insert sample AI predictions for testing
INSERT INTO public.predictions (team1, team2, prediction, confidence, odds, kickoff_time, league, tier, reasoning) VALUES
('Arsenal', 'Chelsea', 'Arsenal Win', 80, 1.85, now() + interval '2 hours', 'Premier League', 'free', 'Arsenal has better form and home advantage'),
('Real Madrid', 'Barcelona', 'Over 2.5 Goals', 72, 1.95, now() + interval '1 day', 'La Liga', 'premium', 'Both teams have strong attacking records'),
('Bayern Munich', 'Dortmund', 'Draw', 65, 3.10, now() + interval '2 days', 'Bundesliga', 'vip', 'Evenly matched teams with defensive strengths'),
('Manchester City', 'Liverpool', 'Both Teams to Score', 85, 1.75, now() + interval '3 days', 'Premier League', 'vvip', 'Both teams have excellent attacking capabilities')
ON CONFLICT DO NOTHING;

-- Grant necessary permissions
GRANT USAGE ON SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
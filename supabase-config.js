// supabase-config.js
// Supabase database configuration and schema setup

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

// Get Supabase credentials from Replit environment variables
// In Replit, environment variables are accessible via a global fetch to a special endpoint
let supabaseUrl = null;
let supabaseKey = null;

// Function to get environment variables in Replit browser environment
async function getReqlitEnvVars() {
    try {
        const response = await fetch('/__replit/env.json');
        if (response.ok) {
            const env = await response.json();
            return env;
        }
    } catch (error) {
        // Fallback: try to get from URL parameters or other methods
        const urlParams = new URLSearchParams(window.location.search);
        return {
            SUPABASE_URL: urlParams.get('supabase_url'),
            SUPABASE_ANON_KEY: urlParams.get('supabase_key')
        };
    }
    return {};
}

// Initialize environment variables
const env = await getReqlitEnvVars();
supabaseUrl = env.SUPABASE_URL;
supabaseKey = env.SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseKey) {
    console.warn('Supabase credentials not found. Some features may be limited.');
}

// Create Supabase client
export const supabase = supabaseUrl && supabaseKey ? 
    createClient(supabaseUrl, supabaseKey) : null;

// Database table schemas and operations
export const SupabaseService = {
    // Initialize database tables if they don't exist
    async initializeTables() {
        if (!supabase) {
            console.warn('Supabase not configured');
            return false;
        }

        try {
            // Check if tables exist by trying to query them
            const { error } = await supabase.from('user_profiles').select('id').limit(1);
            
            if (error && error.code === '42P01') {
                // Tables don't exist, they need to be created in Supabase dashboard
                console.log('Supabase tables need to be created. Please run the setup SQL in your Supabase dashboard.');
                return false;
            }
            
            return true;
        } catch (error) {
            console.error('Error checking Supabase tables:', error);
            return false;
        }
    },

    // Sync user profile data
    async syncUserProfile(firebaseUser, additionalData = {}) {
        if (!supabase || !firebaseUser) return null;

        try {
            const userData = {
                id: firebaseUser.uid,
                email: firebaseUser.email,
                display_name: firebaseUser.displayName || '',
                created_at: new Date().toISOString(),
                last_login: new Date().toISOString(),
                ...additionalData
            };

            const { data, error } = await supabase
                .from('user_profiles')
                .upsert(userData, { onConflict: 'id' })
                .select()
                .single();

            if (error) throw error;
            return data;
        } catch (error) {
            console.error('Error syncing user profile to Supabase:', error);
            return null;
        }
    },

    // Track subscription events
    async logSubscriptionEvent(userId, eventType, eventData) {
        if (!supabase) return null;

        try {
            const { data, error } = await supabase
                .from('subscription_events')
                .insert({
                    user_id: userId,
                    event_type: eventType,
                    event_data: eventData,
                    created_at: new Date().toISOString()
                })
                .select()
                .single();

            if (error) throw error;
            return data;
        } catch (error) {
            console.error('Error logging subscription event:', error);
            return null;
        }
    },

    // Track payment transactions
    async logPaymentTransaction(userId, transactionData) {
        if (!supabase) return null;

        try {
            const { data, error } = await supabase
                .from('payment_transactions')
                .insert({
                    user_id: userId,
                    transaction_id: transactionData.transaction_id,
                    tx_ref: transactionData.tx_ref,
                    amount: transactionData.amount,
                    currency: transactionData.currency || 'NGN',
                    status: transactionData.status,
                    payment_type: 'flutterwave',
                    tier: transactionData.tier,
                    period: transactionData.period,
                    created_at: new Date().toISOString()
                })
                .select()
                .single();

            if (error) throw error;
            return data;
        } catch (error) {
            console.error('Error logging payment transaction:', error);
            return null;
        }
    },

    // Get user's subscription history
    async getUserSubscriptionHistory(userId) {
        if (!supabase) return [];

        try {
            const { data, error } = await supabase
                .from('subscription_events')
                .select('*')
                .eq('user_id', userId)
                .order('created_at', { ascending: false });

            if (error) throw error;
            return data || [];
        } catch (error) {
            console.error('Error fetching subscription history:', error);
            return [];
        }
    },

    // Get user's payment history
    async getUserPaymentHistory(userId) {
        if (!supabase) return [];

        try {
            const { data, error } = await supabase
                .from('payment_transactions')
                .select('*')
                .eq('user_id', userId)
                .order('created_at', { ascending: false });

            if (error) throw error;
            return data || [];
        } catch (error) {
            console.error('Error fetching payment history:', error);
            return [];
        }
    },

    // Update user subscription status
    async updateUserSubscription(userId, subscriptionData) {
        if (!supabase) return null;

        try {
            const { data, error } = await supabase
                .from('user_profiles')
                .update({
                    current_tier: subscriptionData.tier,
                    subscription_period: subscriptionData.period,
                    subscription_start: subscriptionData.start_date,
                    subscription_end: subscriptionData.end_date,
                    subscription_status: subscriptionData.status,
                    updated_at: new Date().toISOString()
                })
                .eq('id', userId)
                .select()
                .single();

            if (error) throw error;
            return data;
        } catch (error) {
            console.error('Error updating user subscription:', error);
            return null;
        }
    }
};

// SQL schema to be run in Supabase dashboard
export const SUPABASE_SCHEMA_SQL = `
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
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Subscription events log
CREATE TABLE IF NOT EXISTS subscription_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL, -- 'upgrade', 'downgrade', 'renewal', 'cancellation'
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

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_user_profiles_email ON user_profiles(email);
CREATE INDEX IF NOT EXISTS idx_subscription_events_user_id ON subscription_events(user_id);
CREATE INDEX IF NOT EXISTS idx_subscription_events_created_at ON subscription_events(created_at);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_user_id ON payment_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_transaction_id ON payment_transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_created_at ON payment_transactions(created_at);

-- Row Level Security (RLS) policies
ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE payment_transactions ENABLE ROW LEVEL SECURITY;

-- Policies for user_profiles
CREATE POLICY "Users can view their own profile" ON user_profiles
    FOR SELECT USING (id = auth.uid()::text::uuid);

CREATE POLICY "Users can update their own profile" ON user_profiles
    FOR UPDATE USING (id = auth.uid()::text::uuid);

-- Policies for subscription_events
CREATE POLICY "Users can view their own subscription events" ON subscription_events
    FOR SELECT USING (user_id = auth.uid()::text::uuid);

-- Policies for payment_transactions
CREATE POLICY "Users can view their own payment transactions" ON payment_transactions
    FOR SELECT USING (user_id = auth.uid()::text::uuid);
`;

console.log('Supabase configuration loaded');
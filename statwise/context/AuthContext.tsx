import React, { createContext, useContext, useEffect, useState } from 'react';
import { supabase, UserProfile } from '@/lib/supabase';
import { Session, User } from '@supabase/supabase-js';

type AuthContextType = {
  session: Session | null;
  user: User | null;
  profile: UserProfile | null;
  loading: boolean;
  signOut: () => Promise<void>;
  refreshProfile: () => Promise<void>;
};

const AuthContext = createContext<AuthContextType>({
  session: null,
  user: null,
  profile: null,
  loading: true,
  signOut: async () => {},
  refreshProfile: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      if (session?.user) {
        loadProfile(session.user.id);
      } else {
        setLoading(false);
      }
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        setSession(session);
        setUser(session?.user ?? null);
        if (session?.user) {
          await ensureUserProfile(session.user);
          await loadProfile(session.user.id);
        } else {
          setProfile(null);
          setLoading(false);
        }
      }
    );

    return () => subscription.unsubscribe();
  }, []);

  async function ensureUserProfile(user: User) {
    const { data: existing } = await supabase
      .from('user_profiles')
      .select('id')
      .eq('id', user.id)
      .single();

    if (!existing) {
      const username = user.user_metadata?.display_name || user.email?.split('@')[0] || 'User';
      await supabase.from('user_profiles').upsert({
        id: user.id,
        email: user.email,
        username,
        display_name: username,
        current_tier: 'Free Tier',
        tier: 'Free Tier',
        subscription_status: 'active',
        notifications: true,
        last_login: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }, { onConflict: 'id' });

      const code = user.id.substring(0, 8).toUpperCase();
      await supabase.from('referral_codes').upsert({
        user_id: user.id,
        code,
        username,
        total_referrals: 0,
        active: true,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }, { onConflict: 'user_id' });

      const referredBy = user.user_metadata?.referred_by as string | undefined;
      if (referredBy) {
        const { data: referrerCode } = await supabase
          .from('referral_codes')
          .select('user_id, code, total_referrals')
          .eq('code', referredBy.toUpperCase())
          .eq('active', true)
          .single();

        if (referrerCode && referrerCode.user_id !== user.id) {
          await supabase.from('referrals').upsert({
            referrer_id: referrerCode.user_id,
            referred_id: user.id,
            referral_code: referrerCode.code,
            reward_claimed: false,
            reward_amount: 500,
            created_at: new Date().toISOString(),
          }, { onConflict: 'referrer_id,referred_id' });

          await supabase
            .from('referral_codes')
            .update({
              total_referrals: (referrerCode.total_referrals ?? 0) + 1,
              updated_at: new Date().toISOString(),
            })
            .eq('code', referrerCode.code);
        }
      }
    }
  }

  async function loadProfile(userId: string) {
    try {
      const { data } = await supabase
        .from('user_profiles')
        .select('*')
        .eq('id', userId)
        .single();
      if (data) setProfile(data as UserProfile);
    } finally {
      setLoading(false);
    }
  }

  async function refreshProfile() {
    if (user) await loadProfile(user.id);
  }

  async function signOut() {
    await supabase.auth.signOut();
  }

  return (
    <AuthContext.Provider value={{ session, user, profile, loading, signOut, refreshProfile }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);

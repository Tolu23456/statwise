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
    let cancelled = false;
    // getSession() rejects when secure storage is locked/corrupted or the
    // network is down.  Without a .catch the promise never resolved into
    // state, `loading` stayed true forever and the app rendered a blank
    // screen with no error and no way out short of clearing app storage.
    supabase.auth.getSession()
      .then(({ data: { session } }) => {
        if (cancelled) return;
        setSession(session);
        setUser(session?.user ?? null);
        if (session?.user) {
          loadProfile(session.user.id).catch(() => setLoading(false));
        } else {
          setLoading(false);
        }
      })
      .catch((e) => {
        // Still listen for auth state changes — the user may have a valid
        // refresh token even if the initial read failed.
        console.warn('[auth] initial getSession failed:', e?.message ?? e);
        if (!cancelled) setLoading(false);
      });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        setSession(session);
        setUser(session?.user ?? null);
        if (session?.user) {
          try {
            await ensureUserProfile(session.user);
          } catch (e) {
            // Never let bootstrap failure strand the user on a spinner.
            console.warn('[auth] profile bootstrap failed:', e instanceof Error ? e.message : e);
          }
          await loadProfile(session.user.id).catch(() => {});
        } else {
          setProfile(null);
          setLoading(false);
        }
      }
    );

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, []);

  async function ensureUserProfile(user: User) {
    // Read errors must NOT be treated as "profile is missing": the old code
    // treated any failure as absence and upserted `Free Tier`, which
    // downgraded a paying user on a transient network blip.
    const { data: existing, error: readErr } = await supabase
      .from('user_profiles')
      .select('id')
      .eq('id', user.id)
      .maybeSingle();

    if (readErr) {
      throw new Error(`Could not read profile (${readErr.code ?? readErr.message})`);
    }
    if (existing) return;

    const username = user.user_metadata?.display_name || user.email?.split('@')[0] || 'User';
    // `ifNotExists` semantics: the INSERT only fires when the row is really
    // absent.  A concurrent bootstrap (or the auth trigger) is not an error.
    const { error: insertErr } = await supabase.from('user_profiles').upsert({
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
    if (insertErr) {
      throw new Error(`Could not create profile: ${insertErr.message}`);
    }

    const { error: codeErr } = await supabase.from('referral_codes').upsert({
      user_id: user.id,
      code: user.id.substring(0, 8).toUpperCase(),
      username,
      total_referrals: 0,
      active: true,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }, { onConflict: 'user_id' });
    if (codeErr) console.warn('[auth] referral code create failed:', codeErr.message);

    const referredBy = user.user_metadata?.referred_by as string | undefined;
    if (referredBy) {
      // The counter bump goes through a SECURITY DEFINER RPC so it actually
      // succeeds for the new user (RLS filtered the old client-side UPDATE
      // to zero rows and supabase-js reported no error — every honest
      // referral silently failed to count).  Requires
      // statwise/supabase-audit-hardening.sql.
      const { data: ok, error: refErr } = await supabase.rpc('register_referral', {
        referrer_code: referredBy.toUpperCase(),
      });
      if (refErr) {
        console.warn('[auth] referral credit failed:', refErr.message);
      } else if (!ok) {
        console.info('[auth] referral code not found or already credited');
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

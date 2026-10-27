import React, { useState, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  Alert, ActivityIndicator, Platform, Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import Ionicons from '@expo/vector-icons/Ionicons';
import * as Haptics from 'expo-haptics';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, FLUTTERWAVE_PUBLIC_KEY } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useQueryClient } from '@tanstack/react-query';
import { useTheme } from '@/context/ThemeContext';
import { assignmentFor } from '@/lib/experimentConfig';
import { logConversion } from '@/lib/experiments';

type Plan = {
  id: string;
  tier: string;
  name: string;
  price: number;
  period: 'daily' | 'monthly';
  predictions: number;
  features: string[];
  badge?: string;
  highlight?: string;
};

/** Certainty score band each tier unlocks — mirrors ai/model/predictor.py. */
const AI_PICK_CERTAINTY = 65;
const TIER_CERTAINTY_BAND: Record<string, { from: number; to: number }> = {
  'Free Tier':    { from: 0,  to: 40 },
  'Premium Tier': { from: 40, to: 55 },
  'VIP Tier':     { from: 55, to: 65 },
  'VVIP Tier':    { from: 65, to: 100 },
};

const PLANS: Plan[] = [
  {
    id: 'free', tier: 'Free Tier', name: 'Free Tier', price: 0, period: 'daily',
    predictions: 5, badge: 'STARTER', highlight: 'Perfect for beginners',
    features: ['5 Daily AI Predictions', 'Basic Match Analysis', 'Community Access'],
  },
  {
    id: 'premium_daily', tier: 'Premium Tier', name: 'Premium', price: 500, period: 'daily',
    predictions: 25, badge: 'POPULAR', highlight: 'Best value for regular users',
    features: ['25 Daily Predictions', 'Advanced Match Analysis', 'All Major Leagues', 'No Ads', 'Priority Support', 'Win Rate Analytics'],
  },
  {
    id: 'vip_daily', tier: 'VIP Tier', name: 'VIP / Elite', price: 2000, period: 'daily',
    predictions: 75, badge: 'ELITE', highlight: 'For serious bettors',
    features: ['75 Daily Predictions', 'All Premium Features', 'Exclusive VIP Insights', 'Live Match Updates', 'Expert Analysis Reports', 'VIP-Only Leagues', 'Advanced Statistics', 'Direct VIP Support'],
  },
  {
    id: 'vvip_daily', tier: 'VVIP Tier', name: 'VVIP', price: 5000, period: 'daily',
    predictions: 999, badge: 'ULTIMATE', highlight: 'For professional bettors',
    features: ['Unlimited Predictions', 'All VIP Features', 'Personal Analyst', 'Real-time Alerts', 'Custom Reports', 'VVIP-Only Tips', 'Dedicated Support', 'Early Access'],
  },
  {
    id: 'premium_monthly', tier: 'Premium Tier', name: 'Premium Monthly', price: 12000, period: 'monthly',
    predictions: 25, badge: 'POPULAR', highlight: 'Save 20% vs daily',
    features: ['Everything in Premium Daily', 'Monthly Billing (Save 20%)', 'Priority Support'],
  },
  {
    id: 'vip_monthly', tier: 'VIP Tier', name: 'VIP Monthly', price: 45000, period: 'monthly',
    predictions: 75, badge: 'ELITE', highlight: 'Save 25% vs daily',
    features: ['Everything in VIP Daily', 'Monthly Billing (Save 25%)', 'Dedicated VIP Manager'],
  },
];

function loadFlutterwaveScript(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (typeof window === 'undefined') return reject(new Error('Not web'));
    if ((window as any).FlutterwaveCheckout) return resolve();
    const script = document.createElement('script');
    script.src = 'https://checkout.flutterwave.com/v3.js';
    script.onload = () => resolve();
    script.onerror = () => reject(new Error('Failed to load Flutterwave'));
    document.head.appendChild(script);
  });
}

/** Honest accuracy ranges measured on out-of-time holdout data. */
const ACCURACY_BY_TIER: Record<string, string> = {
  'Free Tier': '~50–55% on all 1X2 picks',
  'Premium Tier': '~55–60% on all 1X2 picks',
  'VIP Tier': '~60–70% on higher-certainty picks',
  'VVIP Tier': '~70–80% on AI Pick matches',
};

export default function SubscriptionsScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { profile, user, refreshProfile } = useAuth();
  const queryClient = useQueryClient();

  const [period, setPeriod] = useState<'daily' | 'monthly'>('daily');
  const [loading, setLoading] = useState<string | null>(null);

  const toggleAnim = useRef(new Animated.Value(0)).current;

  const topInset = Platform.OS === 'web' ? 0 : insets.top;
  const currentTier = profile?.current_tier ?? 'Free Tier';
  const tierColors = TierBadgeColors[currentTier] ?? TierBadgeColors['Free Tier'];

  useEffect(() => {
    Animated.spring(toggleAnim, {
      toValue: period === 'daily' ? 0 : 1,
      useNativeDriver: true,
      tension: 300,
      friction: 30,
    }).start();
  }, [period]);

  const displayPlans = period === 'monthly'
    ? PLANS.filter(p => p.period === 'monthly')
    : PLANS.filter(p => p.period === 'daily');

  async function processPaymentSuccess(plan: Plan, txRef: string) {
    if (!user) return;
    try {
      await supabase.from('user_profiles').update({
        current_tier: plan.tier,
        tier: plan.tier,
        subscription_status: 'active',
        updated_at: new Date().toISOString(),
      }).eq('id', user.id);

      // Plan 4.3 audit — this insert used to send `plan_id`, a column the
      // live schema does not have (PGRST204, probed via the API), so every
      // payment row failed silently and the purchase left no audit record.
      // Live columns verified present: transaction_id (NOT NULL, UNIQUE),
      // period, created_at. txRef doubles as transaction_id.
      const { error: txError } = await supabase.from('payment_transactions').insert({
        user_id: user.id,
        transaction_id: txRef,
        tx_ref: txRef,
        amount: plan.price,
        currency: 'NGN',
        period: plan.period,
        tier: plan.tier,
        status: 'successful',
        created_at: new Date().toISOString(),
      }).select();
      if (txError) {
        // Never roll back a paying customer's tier over the audit row — but
        // make the failure visible instead of swallowing it.
        console.warn('payment_transactions insert failed:', txError.message);
      }

      await refreshProfile();
      // Plan 4.1 — payment is a mutation: reward points and anything keyed
      // on the old tier must not keep showing the pre-purchase state.
      queryClient.invalidateQueries({ queryKey: ['referral'] });
      // Plan 4.5 — conversion event for the A/B funnel, attributed to the
      // user's current experiment assignments (no assignment → plain
      // conversion, still counted in the funnel totals).
      const bookingAssignment = assignmentFor('booking_cta_v1', user.id);
      logConversion('subscription_purchase', {
        plan: plan.id,
        tier: plan.tier,
        price: plan.price,
        ...(bookingAssignment
          ? { experiment: 'booking_cta_v1', variant: bookingAssignment.variant }
          : {}),
      });
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      Alert.alert('Payment Successful', `You are now on the ${plan.name} plan!`);
    } catch (e) {
      console.warn('Failed to update tier after payment:', e);
    }
  }

  async function handleSubscribe(plan: Plan) {
    if (plan.price === 0) {
      Alert.alert('Free Tier', 'You are already on the free plan.');
      return;
    }
    if (plan.tier === currentTier) {
      Alert.alert('Current Plan', 'You are already on this plan.');
      return;
    }
    if (!user?.email) {
      Alert.alert('Error', 'Please log in to subscribe.');
      return;
    }

    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);

    if (Platform.OS === 'web') {
      setLoading(plan.id);
      try {
        await loadFlutterwaveScript();
        const txRef = `statwise_${user.id}_${Date.now()}`;
        (window as any).FlutterwaveCheckout({
          public_key: FLUTTERWAVE_PUBLIC_KEY,
          tx_ref: txRef,
          amount: plan.price,
          currency: 'NGN',
          payment_options: 'card,ussd,bank_transfer',
          customer: {
            email: user.email,
            name: profile?.display_name ?? user.email,
          },
          customizations: {
            title: 'StatWise Subscription',
            description: `${plan.name} - ₦${plan.price.toLocaleString()}/${plan.period}`,
            logo: 'https://fwpriiugfjhtoymcbmso.supabase.co/storage/v1/object/public/app-assets/logo.png',
          },
          callback: async (response: any) => {
            if (response.status === 'successful' || response.status === 'completed') {
              await processPaymentSuccess(plan, txRef);
            }
          },
          onclose: () => { setLoading(null); },
        });
      } catch (e) {
        setLoading(null);
        Alert.alert('Payment Error', 'Could not initialize payment. Please try again.');
      }
    } else {
      Alert.alert(
        `Upgrade to ${plan.name}`,
        `₦${plan.price.toLocaleString()}/${plan.period}\n\nPayment is available on web. Please visit our website to subscribe.`,
      );
    }
  }

  const TOGGLE_WIDTH = 260;
  const pillTranslate = toggleAnim.interpolate({
    inputRange: [0, 1],
    outputRange: [2, TOGGLE_WIDTH / 2],
  });

  return (
    <View style={[styles.container, { backgroundColor: C.background }]}>
      <View style={[styles.header, { paddingTop: topInset + 8 }]}>
        <Text style={[styles.title, { color: C.text }]}>Subscription Plans</Text>
        <View style={[styles.currentBadge, { backgroundColor: tierColors.bg }]}>
          <Text style={[styles.currentBadgeText, { color: tierColors.text }]}>
            {currentTier}
          </Text>
        </View>
      </View>

      <View style={styles.toggleContainer}>
        <View style={[styles.toggleTrack, { backgroundColor: C.inputBg, borderColor: C.border, width: TOGGLE_WIDTH }]}>
          <Animated.View
            style={[
              styles.togglePill,
              {
                backgroundColor: C.primary,
                width: TOGGLE_WIDTH / 2 - 4,
                transform: [{ translateX: pillTranslate }],
              },
            ]}
          />
          <TouchableOpacity
            style={[styles.toggleOption, { width: TOGGLE_WIDTH / 2 }]}
            onPress={() => { setPeriod('daily'); Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); }}
          >
            <Text style={[styles.toggleText, { color: period === 'daily' ? '#fff' : C.textSecondary }]}>
              Daily
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.toggleOption, { width: TOGGLE_WIDTH / 2 }]}
            onPress={() => { setPeriod('monthly'); Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); }}
          >
            <Text style={[styles.toggleText, { color: period === 'monthly' ? '#fff' : C.textSecondary }]}>
              Monthly{' '}
              <Text style={{ fontSize: 11, opacity: period === 'monthly' ? 1 : 0.7 }}>-20%</Text>
            </Text>
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingBottom: insets.bottom + (Platform.OS === 'web' ? 24 : 80) },
        ]}
        showsVerticalScrollIndicator={false}
      >
        {displayPlans.map(plan => {
          const isActive = plan.tier === currentTier;
          const tc = TierBadgeColors[plan.tier] ?? TierBadgeColors['Free Tier'];
          return (
            <View
              key={plan.id}
              style={[
                styles.planCard,
                {
                  backgroundColor: C.card,
                  borderColor: isActive ? tc.text : C.border,
                  borderWidth: isActive ? 2 : 1,
                },
              ]}
            >
              <View style={styles.planHeader}>
                <View style={[styles.badgePill, { backgroundColor: tc.bg }]}>
                  <Text style={[styles.badgeText, { color: tc.text }]}>{plan.badge}</Text>
                </View>
                {isActive && (
                  <View style={[styles.activePill, { backgroundColor: C.successLight }]}>
                    <Ionicons name="checkmark-circle" size={14} color={C.success} />
                    <Text style={[styles.activeText, { color: C.success }]}>Current Plan</Text>
                  </View>
                )}
              </View>

              <Text style={[styles.planName, { color: C.text }]}>{plan.name}</Text>
              <Text style={[styles.planPrice, { color: tc.text }]}>
                ₦{plan.price.toLocaleString()}
                <Text style={[styles.planPeriod, { color: C.textSecondary }]}>/{plan.period}</Text>
              </Text>
              <Text style={[styles.predLimit, { color: C.textSecondary }]}>
                {plan.predictions === 999 ? 'Unlimited' : plan.predictions} predictions/day
              </Text>

              {/* Honest, model-derived band instead of fabricated win-rate claims */}
              <View style={[styles.certaintyBand, { backgroundColor: C.inputBg, borderColor: C.border }]}>
                <View style={styles.certaintyBandHeader}>
                  <Ionicons name="shield-checkmark-outline" size={13} color={tc.text} />
                  <Text style={[styles.certaintyBandLabel, { color: C.textSecondary }]}>
                    Typical Pico certainty
                  </Text>
                </View>
                <View style={[styles.certaintyTrack, { backgroundColor: C.border }]}>
                  <View
                    style={[
                      styles.certaintyFill,
                      {
                        left: `${TIER_CERTAINTY_BAND[plan.tier]?.from ?? 0}%`,
                        width: `${(TIER_CERTAINTY_BAND[plan.tier]?.to ?? 0) - (TIER_CERTAINTY_BAND[plan.tier]?.from ?? 0)}%`,
                        backgroundColor: tc.text,
                      },
                    ]}
                  />
                  <View style={[styles.certaintyTick, { left: `${AI_PICK_CERTAINTY}%` }]} />
                </View>
                <View style={styles.certaintyScale}>
                  <Text style={[styles.certaintyScaleText, { color: C.textMuted }]}>Unsure</Text>
                  <Text style={[styles.certaintyScaleText, { color: C.textMuted }]}>
                    AI Pick ≥ {AI_PICK_CERTAINTY}
                  </Text>
                  <Text style={[styles.certaintyScaleText, { color: C.textMuted }]}>Certain</Text>
                </View>
              </View>

              {ACCURACY_BY_TIER[plan.tier] && (
                <Text style={[styles.accuracyNote, { color: C.textMuted }]}>
                  Expected accuracy · {ACCURACY_BY_TIER[plan.tier]}
                </Text>
              )}

              <View style={styles.featureList}>
                {plan.features.map((f, i) => (
                  <View key={i} style={styles.featureRow}>
                    <Ionicons name="checkmark" size={14} color={C.success} />
                    <Text style={[styles.featureText, { color: C.textSecondary }]}>{f}</Text>
                  </View>
                ))}
              </View>

              {plan.highlight && (
                <Text style={[styles.highlight, { color: C.textMuted }]}>{plan.highlight}</Text>
              )}

              <TouchableOpacity
                style={[
                  styles.subBtn,
                  {
                    backgroundColor: isActive ? C.successLight : tc.text,
                    opacity: loading === plan.id ? 0.7 : 1,
                  },
                ]}
                onPress={() => handleSubscribe(plan)}
                disabled={loading === plan.id || isActive || (currentTier !== 'Free Tier' && plan.tier !== currentTier)}
                activeOpacity={0.85}
              >
                {loading === plan.id ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <>
                    {isActive && <Ionicons name="checkmark-circle" size={16} color={C.success} />}
                    <Text style={[styles.subBtnText, { color: isActive ? C.success : '#fff' }]}>
                      {isActive
                        ? 'Current Plan'
                        : plan.price === 0
                          ? 'Get Free'
                          : currentTier !== 'Free Tier' && plan.tier !== currentTier
                            ? 'Upgrade from here'
                            : 'Pay Now'}
                    </Text>
                  </>
                )}
              </TouchableOpacity>

              {!isActive && currentTier !== 'Free Tier' && plan.tier !== currentTier && (
                <Text style={[styles.lockNote, { color: C.textMuted }]}>
                  Switch plans from your current tier on the account screen.
                </Text>
              )}
            </View>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  header: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingHorizontal: 16, paddingBottom: 12,
  },
  title: { fontSize: 22, fontFamily: 'Inter_700Bold', flex: 1 },
  currentBadge: { paddingHorizontal: 12, paddingVertical: 4, borderRadius: 20 },
  currentBadgeText: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  toggleContainer: { paddingHorizontal: 16, marginBottom: 16 },
  certaintyBand: { borderRadius: 12, borderWidth: 1, padding: 12, marginTop: 12 },
  certaintyBandHeader: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 10 },
  certaintyBandLabel: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  certaintyTrack: { height: 8, borderRadius: 4, overflow: 'hidden' },
  certaintyFill: { position: 'absolute', top: 0, bottom: 0, borderRadius: 4, opacity: 0.85 },
  certaintyTick: { position: 'absolute', top: -3, width: 2, height: 14, backgroundColor: '#fff', opacity: 0.7 },
  certaintyScale: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
  certaintyScaleText: { fontSize: 10, fontFamily: 'Inter_400Regular' },
  accuracyNote: { fontSize: 11, marginTop: 10, lineHeight: 16 },
  lockNote: { fontSize: 11, textAlign: 'center', marginTop: 8 },
  toggleTrack: {
    flexDirection: 'row', borderRadius: 12, borderWidth: 1,
    height: 44, alignItems: 'center', position: 'relative', overflow: 'hidden',
  },
  togglePill: {
    position: 'absolute', height: 38, borderRadius: 10,
    top: 2, bottom: 2,
  },
  toggleOption: {
    height: '100%', alignItems: 'center', justifyContent: 'center', zIndex: 1,
  },
  toggleText: { fontSize: 14, fontFamily: 'Inter_600SemiBold' },
  scroll: { paddingHorizontal: 16, paddingTop: 4 },
  planCard: { borderRadius: 20, padding: 20, marginBottom: 16 },
  planHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 },
  badgePill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20 },
  badgeText: { fontSize: 11, fontFamily: 'Inter_700Bold', letterSpacing: 0.5 },
  activePill: {
    flexDirection: 'row', alignItems: 'center', gap: 4,
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 20,
  },
  activeText: { fontSize: 12, fontFamily: 'Inter_600SemiBold' },
  planName: { fontSize: 20, fontFamily: 'Inter_700Bold', marginBottom: 4 },
  planPrice: { fontSize: 28, fontFamily: 'Inter_700Bold', marginBottom: 4 },
  planPeriod: { fontSize: 16, fontFamily: 'Inter_400Regular' },
  predLimit: { fontSize: 13, fontFamily: 'Inter_400Regular', marginBottom: 14 },
  statsBlock: { gap: 8, marginBottom: 14 },
  statCard: { borderRadius: 10, padding: 10 },
  statHeader: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  statLabel: { flex: 1, fontSize: 12, fontFamily: 'Inter_400Regular' },
  statValue: { fontSize: 14, fontFamily: 'Inter_700Bold' },
  featureList: { gap: 8, marginBottom: 14 },
  featureRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  featureText: { fontSize: 14, fontFamily: 'Inter_400Regular', flex: 1 },
  highlight: { fontSize: 12, fontFamily: 'Inter_400Regular', marginBottom: 16, fontStyle: 'italic' },
  subBtn: {
    borderRadius: 14, padding: 14, alignItems: 'center',
    flexDirection: 'row', justifyContent: 'center', gap: 8,
  },
  subBtnText: { fontSize: 16, fontFamily: 'Inter_600SemiBold' },
});

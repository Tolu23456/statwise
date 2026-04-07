import React, { useState, useEffect, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  Alert, ActivityIndicator, Platform, Animated,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import * as Haptics from 'expo-haptics';
import { Colors, TierBadgeColors } from '@/constants/colors';
import { supabase, FLUTTERWAVE_PUBLIC_KEY } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';

type Plan = {
  id: string;
  tier: string;
  name: string;
  price: number;
  period: 'daily' | 'monthly';
  predictions: number;
  features: string[];
  badge?: string;
  successRate?: number;
  roi?: string;
  highlight?: string;
};

const PLANS: Plan[] = [
  {
    id: 'free', tier: 'Free Tier', name: 'Free Tier', price: 0, period: 'daily',
    predictions: 5, badge: 'STARTER', highlight: 'Perfect for beginners',
    features: ['5 Daily AI Predictions', 'Basic Match Analysis', 'Community Access'],
  },
  {
    id: 'premium_daily', tier: 'Premium Tier', name: 'Premium', price: 500, period: 'daily',
    predictions: 25, badge: 'POPULAR', successRate: 78, highlight: 'Best value for regular users',
    features: ['25 Daily Predictions', 'Advanced Match Analysis', 'All Major Leagues', 'No Ads', 'Priority Support', 'Win Rate Analytics'],
  },
  {
    id: 'vip_daily', tier: 'VIP Tier', name: 'VIP / Elite', price: 2000, period: 'daily',
    predictions: 75, badge: 'ELITE', successRate: 85, roi: '150% Avg ROI', highlight: 'For serious bettors',
    features: ['75 Daily Predictions', 'All Premium Features', 'Exclusive VIP Insights', 'Live Match Updates', 'Expert Analysis Reports', 'VIP-Only Leagues', 'Advanced Statistics', 'Direct VIP Support'],
  },
  {
    id: 'vvip_daily', tier: 'VVIP Tier', name: 'VVIP', price: 5000, period: 'daily',
    predictions: 999, badge: 'ULTIMATE', successRate: 91, roi: '200% Avg ROI', highlight: 'For professional bettors',
    features: ['Unlimited Predictions', 'All VIP Features', 'Personal Analyst', 'Real-time Alerts', 'Custom Reports', 'VVIP-Only Tips', 'Dedicated Support', 'Early Access'],
  },
  {
    id: 'premium_monthly', tier: 'Premium Tier', name: 'Premium Monthly', price: 12000, period: 'monthly',
    predictions: 25, badge: 'POPULAR', successRate: 78, highlight: 'Save 20% vs daily',
    features: ['Everything in Premium Daily', 'Monthly Billing (Save 20%)', 'Priority Support'],
  },
  {
    id: 'vip_monthly', tier: 'VIP Tier', name: 'VIP Monthly', price: 45000, period: 'monthly',
    predictions: 75, badge: 'ELITE', successRate: 85, highlight: 'Save 25% vs daily',
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

function WinRateBar({ rate, color, bgColor }: { rate: number; color: string; bgColor: string }) {
  const anim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.spring(anim, { toValue: rate / 100, useNativeDriver: false, tension: 60, friction: 12 }).start();
  }, [rate]);
  const width = anim.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] });
  return (
    <View style={[winBarStyles.track, { backgroundColor: bgColor }]}>
      <Animated.View style={[winBarStyles.fill, { width: width as any, backgroundColor: color }]} />
    </View>
  );
}

const winBarStyles = StyleSheet.create({
  track: { height: 5, borderRadius: 3, overflow: 'hidden', marginTop: 5 },
  fill: { height: '100%', borderRadius: 3 },
});

export default function SubscriptionsScreen() {
  const { scheme } = useTheme();
  const C = Colors[scheme];
  const insets = useSafeAreaInsets();
  const { profile, user, refreshProfile } = useAuth();

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

      await supabase.from('payment_transactions').insert({
        user_id: user.id,
        tx_ref: txRef,
        amount: plan.price,
        currency: 'NGN',
        plan_id: plan.id,
        tier: plan.tier,
        status: 'successful',
        created_at: new Date().toISOString(),
      }).select();

      await refreshProfile();
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
            logo: 'https://pdrcyuzfdqjnsltqqxvr.supabase.co/storage/v1/object/public/app-assets/logo.png',
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

              {(plan.successRate !== undefined || plan.roi) && (
                <View style={styles.statsBlock}>
                  {plan.successRate !== undefined && (
                    <View style={[styles.statCard, { backgroundColor: C.successLight }]}>
                      <View style={styles.statHeader}>
                        <Ionicons name="trending-up" size={13} color={C.success} />
                        <Text style={[styles.statLabel, { color: C.textSecondary }]}>Win Rate</Text>
                        <Text style={[styles.statValue, { color: C.success }]}>{plan.successRate}%</Text>
                      </View>
                      <WinRateBar rate={plan.successRate} color={C.success} bgColor={C.border} />
                    </View>
                  )}
                  {plan.roi && (
                    <View style={[styles.statCard, { backgroundColor: C.primaryLight }]}>
                      <View style={styles.statHeader}>
                        <Ionicons name="stats-chart" size={13} color={C.primary} />
                        <Text style={[styles.statLabel, { color: C.textSecondary }]}>Avg ROI</Text>
                        <Text style={[styles.statValue, { color: C.primary }]}>{plan.roi.replace(' Avg ROI', '')}</Text>
                      </View>
                    </View>
                  )}
                </View>
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
                disabled={loading === plan.id || isActive}
                activeOpacity={0.85}
              >
                {loading === plan.id ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <>
                    {isActive && <Ionicons name="checkmark-circle" size={16} color={C.success} />}
                    <Text style={[styles.subBtnText, { color: isActive ? C.success : '#fff' }]}>
                      {isActive ? 'Current Plan' : plan.price === 0 ? 'Get Free' : 'Pay Now'}
                    </Text>
                  </>
                )}
              </TouchableOpacity>
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

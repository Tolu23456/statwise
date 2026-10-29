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
import { PLANS, buildTxRef, isTerminalEventStatus, type Plan } from '@/lib/paymentCore';
import { useAuth } from '@/context/AuthContext';
import { useQueryClient } from '@tanstack/react-query';
import { useTheme } from '@/context/ThemeContext';
import { assignmentFor } from '@/lib/experimentConfig';
import { logConversion } from '@/lib/experiments';

/** Certainty score band each tier unlocks — mirrors ai/model/predictor.py. */
const AI_PICK_CERTAINTY = 65;
const TIER_CERTAINTY_BAND: Record<string, { from: number; to: number }> = {
  'Free Tier':    { from: 0,  to: 40 },
  'Premium Tier': { from: 40, to: 55 },
  'VIP Tier':     { from: 55, to: 65 },
  'VVIP Tier':    { from: 65, to: 100 },
};

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

/**
 * Max concurrently-pending checkout rows per account (audit H-4): a burst of
 * abandoned checkouts used to fill the verifier's per-run window and starve
 * genuine purchases.  The server-side poller enforces the same ceiling
 * (scripts/verify-payments.ts) — this one just fails fast for the user.
 */
const MAX_PENDING_CHECKOUTS = 3;

/** Client-side verification polling windows (audit M-24). */
const FAST_TICKS = 150;              // 150 × 3 s   = 7.5 min
const SLOW_EVERY = 5;                // fetch every 5th slow tick (15 s)
const SLOW_TICKS = 120 * SLOW_EVERY; // 120 × 15 s  = 30 min

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

  // ── Plan 5.1: server-side verification (queue + poll) ────────────────────
  // The client NEVER writes tier fields (a DB trigger blocks user-JWT tier
  // updates) and never writes payment_transactions (RLS INSERT dropped) —
  // it queues ONE payment_events row, opens the hosted checkout, and polls
  // until the GitHub Actions verifier (every ~3 min) credits it server-side.

  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const paidRef = useRef(false);

  function stopPolling() {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  // Unmount must not leave an interval ticking against a dead screen.
  useEffect(() => stopPolling, []);

  /** The verifier credited the tier — refresh everything keyed on it. */
  async function onPaymentVerified(plan: Plan) {
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    Alert.alert('Payment Successful', `You are now on the ${plan.name} plan!`);
    try {
      await refreshProfile();
      // Plan 4.1 — payment is a mutation: reward points and anything keyed
      // on the old tier must not keep showing the pre-purchase state.
      queryClient.invalidateQueries({ queryKey: ['referral'] });
      // Plan 4.5 — conversion event for the A/B funnel, attributed to the
      // user's current experiment assignments (no assignment → plain
      // conversion, still counted in the funnel totals).
      const bookingAssignment = user ? assignmentFor('booking_cta_v1', user.id) : null;
      logConversion('subscription_purchase', {
        plan: plan.id,
        tier: plan.tier,
        price: plan.price,
        ...(bookingAssignment
          ? { experiment: 'booking_cta_v1', variant: bookingAssignment.variant }
          : {}),
      });
    } catch (e) {
      // The tier IS granted server-side; only the local refresh failed —
      // say so instead of swallowing it (the alert above already fired).
      console.warn('post-payment refresh failed:', e);
    }
  }

  /**
   * Watch this checkout's verification queue.
   *
   * Phase 1: every 3 s for 7.5 min — instant card payments plus at least two
   * server-verifier ticks (the verifier runs every 3 min).
   * Phase 2: every 15 s for another 30 min — a slow bank transfer keeps
   * getting re-checked while the screen is open (audit M-24: the client used
   * to give up at 7.5 min and declare "taking longer than usual" while the
   * user's money was still in flight, with no further feedback).
   * Transient fetch failures are swallowed and retried — they can never kill
   * the loop or surface as errors.
   */
  function startPolling(plan: Plan, txRef: string) {
    stopPolling();
    let ticks = 0;
    pollTimer.current = setInterval(async () => {
      ticks += 1;
      if (ticks > FAST_TICKS + SLOW_TICKS) {
        stopPolling();
        setLoading(null);
        Alert.alert(
          'Verification still in progress',
          `We could not confirm this payment within ${Math.round((FAST_TICKS + SLOW_TICKS) * 3 / 60)} minutes.\n\n` +
          'Verification continues in the background (up to 24 hours) — a bank transfer is credited ' +
          'automatically as soon as Flutterwave confirms it, and your plan activates on your next visit.\n\n' +
          `Reference: ${txRef}`,
        );
        return;
      }
      // Slow phase: skip ticks without a network call.
      const fast = ticks <= FAST_TICKS;
      if (!fast && (ticks - FAST_TICKS) % SLOW_EVERY !== 0) return;
      try {
        const { data } = await supabase
          .from('payment_events')
          .select('status, last_error')
          .eq('tx_ref', txRef)
          .maybeSingle();
        const status = typeof data?.status === 'string' ? data.status : '';
        if (!status || !isTerminalEventStatus(status)) return;
        stopPolling();
        setLoading(null);
        if (status === 'verified') {
          await onPaymentVerified(plan);
        } else if (status === 'failed') {
          const why = data?.last_error ? `\n\nReason: ${data.last_error}` : '';
          Alert.alert(
            'Payment not verified',
            `Flutterwave could not confirm this payment, so no plan was granted.${why}\n\nReference: ${txRef}`,
          );
        } else if (status === 'expired') {
          Alert.alert('Checkout Expired', 'This checkout session expired before a payment completed.');
        }
      } catch {
        /* transient read failure — keep polling */
      }
    }, 3000);
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

    if (Platform.OS !== 'web') {
      Alert.alert(
        `Upgrade to ${plan.name}`,
        `₦${plan.price.toLocaleString()}/${plan.period}\n\nPayment is available on web. Please visit our website to subscribe.`,
      );
      return;
    }

    setLoading(plan.id);
    paidRef.current = false;
    try {
      const txRef = buildTxRef(plan.id, user.id, Date.now());

      // 1. Queue the checkout — the verifier will only ever honour THIS ref.
      //    Fail CLOSED on error: never open a checkout nothing can verify
      //    (missing migration, offline, RLS surprise).
      //
      //    Queue-flood guard (audit H-4): one account could previously
      //    insert an unbounded number of pending rows, filling the verifier's
      //    per-run window so genuine purchases were never looked at and aged
      //    out unpaid.  At most MAX_PENDING_CHECKOUTS may be pending per
      //    account at once.  Fails OPEN on read errors (a broken count must
      //    not block payments) — the poller's per-run/per-user caps are the
      //    backstop.
      const { count: pendingCount, error: countErr } = await supabase
        .from('payment_events')
        .select('id', { count: 'exact', head: true })
        .eq('user_id', user.id)
        .eq('status', 'pending');
      if (countErr) {
        console.warn('pending-checkout count check failed:', countErr.message);
      } else if ((pendingCount ?? 0) >= MAX_PENDING_CHECKOUTS) {
        setLoading(null);
        Alert.alert(
          'Checkout Already Pending',
          'You already have a checkout awaiting verification. Give it a few minutes — ' +
          'any payment you completed is still credited automatically.',
        );
        return;
      }

      const { error: evErr } = await supabase.from('payment_events').insert({
        user_id: user.id,
        plan_id: plan.id,
        tx_ref: txRef,
        amount: plan.price,
        currency: 'NGN',
      });
      if (evErr) {
        console.warn('payment_events queue failed:', evErr.message);
        setLoading(null);
        Alert.alert(
          'Payments Temporarily Unavailable',
          'Subscriptions are being upgraded — please try again in a few minutes.',
        );
        return;
      }

      // 2. Poll from now: a missed modal callback can no longer strand a
      //    completed payment (the server verifies it regardless).
      startPolling(plan, txRef);

      // 3. Hosted checkout — Flutterwave collects the money; the server
      //    decides what it bought (shared catalog in lib/paymentCore.ts).
      await loadFlutterwaveScript();
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
        callback: () => {
          // Money moved. Verification is server-side from here — keep the
          // poller running until the event reaches a terminal state.
          paidRef.current = true;
        },
        onclose: () => {
          if (!paidRef.current) {
            // Closed without completing: stop spinner + poll; the pending
            // event expires server-side after 24 h. (A late-completing
            // transfer still gets credited by the verifier — the tier shows
            // up on the next profile refresh.)
            stopPolling();
            setLoading(null);
          }
        },
      });
    } catch (e) {
      console.warn('checkout init failed:', e);
      stopPolling();
      setLoading(null);
      Alert.alert('Payment Error', 'Could not initialize payment. Please try again.');
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
          // Paid users cannot buy a different plan from this screen (the
          // flow is "switch from your current tier"); those buttons are
          // disabled and must LOOK disabled — full-opacity tier colours
          // read as clickable (user report, 2026-10-27).
          const blocked = !isActive && currentTier !== 'Free Tier' && plan.tier !== currentTier;
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
                    // Disabled states (current plan / locked) sit on the
                    // neutral surface with muted text instead of the vivid
                    // tier colour, so they can't be mistaken for actions.
                    backgroundColor: isActive || blocked ? C.inputBg : tc.text,
                    opacity: loading === plan.id ? 0.7 : isActive || blocked ? 0.8 : 1,
                  },
                  isActive || blocked
                    ? ({ cursor: 'not-allowed' } as object)
                    : null,
                ]}
                onPress={() => handleSubscribe(plan)}
                disabled={loading === plan.id || isActive || blocked}
                activeOpacity={0.85}
              >
                {loading === plan.id ? (
                  <ActivityIndicator color="#fff" size="small" />
                ) : (
                  <>
                    {isActive && <Ionicons name="checkmark-circle" size={16} color={C.success} />}
                    <Text
                      style={[
                        styles.subBtnText,
                        { color: isActive ? C.success : blocked ? C.textMuted : '#fff' },
                      ]}
                    >
                      {isActive
                        ? 'Current Plan'
                        : plan.price === 0
                          ? 'Get Free'
                          : blocked
                            ? 'Upgrade from here'
                            : 'Pay Now'}
                    </Text>
                  </>
                )}
              </TouchableOpacity>

              {blocked && (
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

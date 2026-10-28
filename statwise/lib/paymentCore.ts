/**
 * StatWise payment core (plan 5.1) — the ONE source of truth shared by the
 * app's checkout screen (`app/(tabs)/subscriptions.tsx`) and the server-side
 * verification poller (`scripts/verify-payments.ts`, run by
 * `.github/workflows/verify-payments.yml`).
 *
 * Security model (why this file exists):
 *   * The plan catalog (price / tier / period) lives HERE, compiled into both
 *     the client and the poller — a tampered client cannot choose what a
 *     payment buys, because the poller re-prices every Flutterwave
 *     transaction against this catalog before crediting anything.
 *   * The client NEVER writes tier fields (a DB trigger blocks it) and never
 *     writes payment_transactions (RLS INSERT dropped). It only queues a
 *     `payment_events` row and polls it; the poller credits via the
 *     service-role-only `apply_payment_verification` RPC.
 *   * Pure TypeScript: no React Native imports, no secrets — compiles on a
 *     bare Node runner with `tsc --strict`.
 *
 *   tsc statwise/lib/paymentCore.ts statwise/lib/paymentCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/paymentCore.test.js
 */

export type PlanPeriod = 'daily' | 'monthly';

export type Plan = {
  id: string;
  tier: string;
  name: string;
  price: number;
  period: PlanPeriod;
  predictions: number;
  features: string[];
  badge?: string;
  highlight?: string;
};

/**
 * The catalog. Prices in NGN major units — Flutterwave's `amount` for NGN is
 * quoted the same way, so the poller's equality check is a straight compare.
 * Adding a plan here is the ONLY way a plan becomes purchasable: the screen
 * renders this list, the poller prices against it.
 */
export const PLANS: Plan[] = [
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

export function planById(id: string): Plan | null {
  return PLANS.find((p) => p.id === id) ?? null;
}

// ─────────────────────────────────────────────────────────────────────────
// tx_ref — `statwise_{planId}_{userPrefix}_{epochMs}`
// ─────────────────────────────────────────────────────────────────────────

/**
 * Flutterwave truncates/rejects over-long references; keep every generated
 * ref within 50 chars (longest current: premium_monthly → 47).
 */
export const MAX_TX_REF_LEN = 50;

/** First 8 hex chars of the user id (uuid without hyphens). */
export function userPrefixOf(userId: string): string {
  return userId.replace(/-/g, '').slice(0, 8);
}

/**
 * Builds the checkout reference. Throws when the shape would exceed the
 * Flutterwave limit — a new catalog id that is too long must fail CI (this
 * function is exercised by paymentCore.test.ts) instead of failing live
 * payments.
 */
export function buildTxRef(planId: string, userId: string, ts: number): string {
  const ref = `statwise_${planId}_${userPrefixOf(userId)}_${ts}`;
  if (ref.length > MAX_TX_REF_LEN) {
    throw new Error(
      `tx_ref too long (${ref.length} > ${MAX_TX_REF_LEN}): shorten plan id '${planId}'`,
    );
  }
  return ref;
}

export type ParsedTxRef = {
  planId: string;
  userPrefix: string;
  ts: number;
};

/**
 * Deterministic inverse of buildTxRef — the split is unique because the
 * tail is fixed-width (exactly 8 chars, then an underscore, then exactly
 * 13 digits at the end). Returns null for anything we didn't generate.
 */
export function parseTxRef(ref: string): ParsedTxRef | null {
  const m = /^statwise_(.+)_(.{8})_(\d{13})$/.exec(ref);
  if (!m) return null;
  const [, planId, userPrefix, tsStr] = m;
  if (!/^[a-z][a-z0-9_]*$/.test(planId)) return null;
  if (!/^[0-9a-f]{8}$/.test(userPrefix)) return null;
  return { planId, userPrefix, ts: Number(tsStr) };
}

// ─────────────────────────────────────────────────────────────────────────
// Verification queue
// ─────────────────────────────────────────────────────────────────────────

/** A `payment_events` row as PostgREST returns it. */
export type PaymentEventRow = {
  id: string;
  user_id: string;
  plan_id: string;
  tx_ref: string;
  amount: number | string;
  currency: string;
  status: string;
};

/** Pending events older than this are expired by the poller. */
export const EVENT_TTL_MS = 24 * 60 * 60 * 1000;

/** Statuses that end the client's polling loop. */
export function isTerminalEventStatus(status: string): boolean {
  return status === 'verified' || status === 'failed' || status === 'expired';
}

/** The poller's view of Flutterwave's verify_by_reference answer. */
export type FlwVerification =
  | { kind: 'not_found' }                          // no transaction for this ref (yet)
  | { kind: 'transport_error'; message: string }   // network/5xx — retry next run
  | {
      kind: 'transaction';
      id: number;
      tx_ref: string;
      status: string;
      amount: number;
      currency: string;
    };

export type Adjudication =
  | { action: 'leave_pending'; reason: string }
  | { action: 'credit'; plan: Plan; flwId: number; paidAmount: number }
  | { action: 'fail'; reason: string };

const AMOUNT_EPSILON = 0.01;
const TERMINAL_FAILED_STATUSES = ['failed', 'reverted', 'cancelled'];
const STILL_PROCESSING_STATUSES = ['pending', 'processing', 'ongoing'];

/**
 * The whole security decision in one pure function: given a queued event and
 * Flutterwave's answer, decide credit / fail / wait. Rules, in order:
 *   1. transient answers (not_found, transport) → wait — never fail a pay
 *      that just hasn't landed in Flutterwave's index yet;
 *   2. the reference must round-trip: FLW's echo == the event's tx_ref, and
 *      it must parse back to THIS event's plan + user (no cross-planting);
 *   3. the plan must exist in the catalog (client can't invent ids);
 *   4. NGN only, and the charged amount must equal the catalog price;
 *   5. only `successful` transactions credit; dead ones fail terminally;
 *      still-processing ones wait (bank transfers).
 */
export function adjudicateVerification(
  event: PaymentEventRow,
  flw: FlwVerification,
): Adjudication {
  if (flw.kind === 'not_found') {
    return { action: 'leave_pending', reason: 'no transaction yet' };
  }
  if (flw.kind === 'transport_error') {
    return { action: 'leave_pending', reason: `transient: ${flw.message}` };
  }

  if (flw.tx_ref !== event.tx_ref) {
    return { action: 'fail', reason: `FLW tx_ref echo mismatch: ${flw.tx_ref}` };
  }

  const parsed = parseTxRef(event.tx_ref);
  if (!parsed) {
    return { action: 'fail', reason: 'unparseable tx_ref' };
  }
  if (parsed.planId !== event.plan_id) {
    return { action: 'fail', reason: `tx_ref plan ${parsed.planId} != event plan ${event.plan_id}` };
  }
  if (parsed.userPrefix !== userPrefixOf(event.user_id)) {
    return { action: 'fail', reason: 'tx_ref user prefix != event user' };
  }

  const plan = planById(event.plan_id);
  if (!plan) {
    return { action: 'fail', reason: `unknown plan '${event.plan_id}'` };
  }

  if (flw.currency !== 'NGN') {
    return { action: 'fail', reason: `currency ${flw.currency} != NGN` };
  }
  if (!Number.isFinite(flw.amount)) {
    return { action: 'fail', reason: 'missing/non-numeric amount in FLW payload' };
  }

  const status = flw.status.toLowerCase();
  if (status === 'successful') {
    if (Math.abs(flw.amount - plan.price) > AMOUNT_EPSILON) {
      return {
        action: 'fail',
        reason: `amount ${flw.amount} != catalog ${plan.price} (${plan.id})`,
      };
    }
    return { action: 'credit', plan, flwId: flw.id, paidAmount: flw.amount };
  }
  if (TERMINAL_FAILED_STATUSES.includes(status)) {
    return { action: 'fail', reason: `payment ${status}` };
  }
  if (STILL_PROCESSING_STATUSES.includes(status)) {
    return { action: 'leave_pending', reason: `payment ${status}` };
  }
  return { action: 'leave_pending', reason: `unrecognised FLW status '${flw.status}'` };
}

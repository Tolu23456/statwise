/**
 * Unit tests for the payment core (plan 5.1): catalog integrity, tx_ref
 * round-trips and length budget, and every branch of the verification
 * adjudication. Pure inputs — runs on plain Node, wired into CI via
 * test-app.yml.
 *
 *   tsc statwise/lib/paymentCore.ts statwise/lib/paymentCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/paymentCore.test.js
 */
import {
  EVENT_TTL_MS,
  MAX_TX_REF_LEN,
  PLANS,
  PaymentEventRow,
  FlwVerification,
  adjudicateVerification,
  buildTxRef,
  isTerminalEventStatus,
  parseTxRef,
  planById,
  userPrefixOf,
} from './paymentCore';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

const USER_A = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d';
const USER_B = 'ffeeddccbbaa99887766554433221100';

// --- catalog integrity -----------------------------------------------------
check('six plans in catalog', PLANS.length, 6);
check(
  'plan ids unique',
  new Set(PLANS.map((p) => p.id)).size,
  PLANS.length,
);
check('planById finds premium_daily', planById('premium_daily')?.price, 500);
check('planById finds vip_daily', planById('vip_daily')?.price, 2000);
check('planById finds vvip_daily', planById('vvip_daily')?.price, 5000);
check('planById finds premium_monthly', planById('premium_monthly')?.price, 12000);
check('planById finds vip_monthly', planById('vip_monthly')?.price, 45000);
check('planById free is 0', planById('free')?.price, 0);
check('planById unknown id -> null', planById('hax'), null);
check(
  'every paid plan has a tier',
  PLANS.filter((p) => p.price > 0 && p.tier.endsWith('Tier')).length,
  5,
);
check(
  'periods correct',
  PLANS.map((p) => `${p.id}:${p.period}`),
  [
    'free:daily',
    'premium_daily:daily',
    'vip_daily:daily',
    'vvip_daily:daily',
    'premium_monthly:monthly',
    'vip_monthly:monthly',
  ],
);
// The catalog is what the poller prices against — pin the exact NGN prices
// so a typo here (a "small" edit) can never silently sell a tier cheap.
check(
  'catalog prices pinned',
  PLANS.map((p) => p.price),
  [0, 500, 2000, 5000, 12000, 45000],
);

// --- tx_ref build / parse --------------------------------------------------
const TS = 1761560000000; // 13-digit epoch ms

check('userPrefix strips hyphens', userPrefixOf(USER_A), 'a1b2c3d4');

for (const plan of PLANS) {
  const ref = buildTxRef(plan.id, USER_A, TS);
  check(`buildTxRef shape (${plan.id})`, /^statwise_[a-z0-9_]+_[0-9a-f]{8}_\d{13}$/.test(ref), true);
  check(
    `buildTxRef length within budget (${plan.id})`,
    ref.length <= MAX_TX_REF_LEN,
    true,
  );
  const parsed = parseTxRef(ref);
  check(`parseTxRef round-trip plan (${plan.id})`, parsed?.planId, plan.id);
  check(`parseTxRef round-trip user (${plan.id})`, parsed?.userPrefix, 'a1b2c3d4');
  check(`parseTxRef round-trip ts (${plan.id})`, parsed?.ts, TS);
}

let threw = false;
try {
  buildTxRef('an_extremely_long_plan_identifier_x', USER_A, TS);
} catch {
  threw = true;
}
check('buildTxRef rejects over-long plan id', threw, true);

check(
  'shortest plan ref length',
  buildTxRef('free', USER_B, TS).length,
  `statwise_free_${userPrefixOf(USER_B)}_${TS}`.length,
);
check('free ref within budget', buildTxRef('free', USER_B, TS).length <= MAX_TX_REF_LEN, true);
check('premium_monthly ref = 47 chars', buildTxRef('premium_monthly', USER_A, TS).length, 47);

check('parseTxRef empty -> null', parseTxRef(''), null);
check('parseTxRef garbage -> null', parseTxRef('statwise_'), null);
check('parseTxRef wrong prefix -> null', parseTxRef('other_premium_daily_a1b2c3d4_1761560000000'), null);
check('parseTxRef 12-digit ts -> null', parseTxRef('statwise_premium_daily_a1b2c3d4_761560000000'), null);
check('parseTxRef non-hex user -> null', parseTxRef('statwise_premium_daily_ZZZZZZZZ_1761560000000'), null);
check(
  'parseTxRef trailing junk -> null',
  parseTxRef('statwise_premium_daily_a1b2c3d4_1761560000000x'),
  null,
);

// --- queue helpers ---------------------------------------------------------
check('TTL is 24h', EVENT_TTL_MS, 24 * 60 * 60 * 1000);
check('verified is terminal', isTerminalEventStatus('verified'), true);
check('failed is terminal', isTerminalEventStatus('failed'), true);
check('expired is terminal', isTerminalEventStatus('expired'), true);
check('pending is not terminal', isTerminalEventStatus('pending'), false);

// --- adjudication ----------------------------------------------------------
function event(over: Partial<PaymentEventRow> = {}): PaymentEventRow {
  const planId = over.plan_id ?? 'premium_daily';
  return {
    id: 'ev1',
    user_id: USER_A,
    plan_id: planId,
    tx_ref: buildTxRef(planId, over.user_id ?? USER_A, TS),
    amount: 500,
    currency: 'NGN',
    status: 'pending',
    ...over,
  };
}
function okTx(over: Partial<Extract<FlwVerification, { kind: 'transaction' }>> = {}): FlwVerification {
  return {
    kind: 'transaction',
    id: 998877,
    tx_ref: event().tx_ref,
    status: 'successful',
    amount: 500,
    currency: 'NGN',
    ...over,
  };
}

// wait branches
check(
  'not_found waits',
  adjudicateVerification(event(), { kind: 'not_found' }).action,
  'leave_pending',
);
check(
  'transport error waits',
  adjudicateVerification(event(), { kind: 'transport_error', message: 'boom' }).action,
  'leave_pending',
);
check(
  'FLW pending waits',
  adjudicateVerification(event(), okTx({ status: 'pending' })).action,
  'leave_pending',
);
check(
  'unrecognised status waits',
  adjudicateVerification(event(), okTx({ status: 'weird' })).action,
  'leave_pending',
);

// credit branch
const credit = adjudicateVerification(event(), okTx());
check('valid success credits', credit.action, 'credit');
check(
  'credit carries catalog plan',
  credit.action === 'credit' ? credit.plan.id : null,
  'premium_daily',
);
check(
  'credit carries tier',
  credit.action === 'credit' ? credit.plan.tier : null,
  'Premium Tier',
);
check(
  'credit carries flw id',
  credit.action === 'credit' ? credit.flwId : null,
  998877,
);
check(
  'credit carries paid amount',
  credit.action === 'credit' ? credit.paidAmount : null,
  500,
);
check(
  'vvip credits vvip tier',
  (() => {
    const ev = event({ plan_id: 'vvip_daily', amount: 5000 });
    const r = adjudicateVerification(ev, okTx({
      tx_ref: ev.tx_ref, amount: 5000,
    }));
    return r.action === 'credit' ? r.plan.tier : r.action;
  })(),
  'VVIP Tier',
);
check(
  'amount equal within epsilon credits',
  adjudicateVerification(event(), okTx({ amount: 500.005 })).action,
  'credit',
);

// fail branches
check(
  'amount mismatch fails',
  adjudicateVerification(event(), okTx({ amount: 1 })).action,
  'fail',
);
check(
  'amount mismatch reason names prices',
  (() => {
    const r = adjudicateVerification(event(), okTx({ amount: 1 }));
    return r.action === 'fail' && /amount 1 != catalog 500/.test(r.reason);
  })(),
  true,
);
check(
  'wrong currency fails',
  adjudicateVerification(event(), okTx({ currency: 'USD' })).action,
  'fail',
);
check(
  'failed payment fails terminally',
  adjudicateVerification(event(), okTx({ status: 'failed' })).action,
  'fail',
);
check(
  'reverted payment fails terminally',
  adjudicateVerification(event(), okTx({ status: 'reverted' })).action,
  'fail',
);
check(
  'FLW echo mismatch fails',
  adjudicateVerification(event(), okTx({ tx_ref: 'statwise_other_a1b2c3d4_1761560000000' })).action,
  'fail',
);
check(
  'unknown plan fails',
  adjudicateVerification(
    event({ plan_id: 'hax_daily' }),
    okTx({ tx_ref: buildTxRef('hax_daily', USER_A, TS) }),
  ).action,
  'fail',
);
check(
  'cross-user tx_ref fails',
  (() => {
    // tx_ref carries user B's prefix while the event belongs to user A.
    const ev = event({ user_id: USER_A });
    const forged = `statwise_premium_daily_${userPrefixOf(USER_B)}_${TS}`;
    const r = adjudicateVerification({ ...ev, tx_ref: forged },
      okTx({ tx_ref: forged }));
    return r.action === 'fail' && /user prefix/.test(r.reason);
  })(),
  true,
);
check(
  'cross-plan tx_ref fails',
  (() => {
    const ev = event({ plan_id: 'premium_daily' });
    const forged = buildTxRef('vvip_daily', USER_A, TS);
    const r = adjudicateVerification({ ...ev, tx_ref: forged },
      okTx({ tx_ref: forged }));
    return r.action === 'fail' && /plan/.test(r.reason);
  })(),
  true,
);
check(
  'unparseable tx_ref fails',
  adjudicateVerification(
    { ...event(), tx_ref: 'nope' },
    okTx({ tx_ref: 'nope' }),
  ).action,
  'fail',
);
check(
  'NaN amount fails instead of crediting',
  adjudicateVerification(event(), okTx({ amount: Number.NaN })).action,
  'fail',
);

console.log(`paymentCore.test: ${passed} checks passed`);

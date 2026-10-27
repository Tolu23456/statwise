/**
 * Unit tests for the cache policy (plan step 4.1): freshness budgets,
 * garbage collection, retry bounds and the invalidation keys.
 * Pure values — plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/queryConfig.ts statwise/lib/queryConfig.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/queryConfig.test.js
 */
import { GC_TIME, QUERY_DEFAULTS, QUERY_KEYS, STALE_TIME, type QueryKind } from './queryConfig';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

const KINDS: QueryKind[] = ['live-feed', 'aggregate', 'user-data', 'backtest'];

// --- every kind has a sane, finite budget ---------------------------------
check('all four query kinds are covered',
  KINDS.every(k => Number.isFinite(STALE_TIME[k])), true);
for (const kind of KINDS) {
  const t = STALE_TIME[kind];
  check(`${kind}: positive`, t > 0, true);
  check(`${kind}: not longer than 15 min`, t <= 15 * 60_000, true);
  check(`${kind}: whole seconds`, t % 1000 === 0, true);
}

// --- ordering: live data refreshes first, derived data can lag ------------
check('live-feed is the freshest kind',
  STALE_TIME['live-feed'] === Math.min(...KINDS.map(k => STALE_TIME[k])), true);
check('live-feed refreshes within a minute',
  STALE_TIME['live-feed'] <= 60_000, true);
check('aggregates lag behind the feed on purpose',
  STALE_TIME.aggregate > STALE_TIME['live-feed'], true);

// --- gcTime must outlive staleness (else data expires the moment it
//     becomes stale, and gcTime is decoration) -----------------------------
check('gcTime covers the longest stale time',
  GC_TIME >= Math.max(...KINDS.map(k => STALE_TIME[k])), true);
check('gcTime keeps data across a tab switch', GC_TIME >= 5 * 60_000, true);
check('gcTime is bounded (no session-long memory)', GC_TIME <= 60 * 60_000, true);

// --- defaults: no focus-refetch storms, bounded retries -------------------
const q = QUERY_DEFAULTS.queries;
check('focus refetch is off by default (home opts in explicitly)',
  q.refetchOnWindowFocus, false);
check('refetch on reconnect is on', q.refetchOnReconnect, true);
check('default staleTime is non-zero', q.staleTime > 0, true);
check('default staleTime is at least the live feed budget',
  q.staleTime >= STALE_TIME['live-feed'], true);
check('retries are bounded (1, not react-query\'s 3)', q.retry, 1);
check('mutations never retry (no double payments)', QUERY_DEFAULTS.mutations.retry, 0);
check('default gcTime matches the policy constant', q.gcTime, GC_TIME);

// --- invalidation keys are stable -----------------------------------------
check('predictions key', QUERY_KEYS.predictions('vip'), ['predictions', 'vip']);
check('backtesting key', QUERY_KEYS.backtesting('free'), ['backtesting', 'free']);
check('insights key', QUERY_KEYS.insights(), ['insights']);
check('referral key', QUERY_KEYS.referral('u1'), ['referral', 'u1']);
check('referral key without a user still forms a valid key',
  QUERY_KEYS.referral(), ['referral', '']);
check('referral keys differ per user (no cross-account cache hit)',
  QUERY_KEYS.referral('u1').join() !== QUERY_KEYS.referral('u2').join(), true);
check('keys are distinct (invalidating one must not hit the others)',
  new Set([
    QUERY_KEYS.predictions('x').join(),
    QUERY_KEYS.backtesting('x').join(),
    QUERY_KEYS.insights().join(),
    QUERY_KEYS.referral('x').join(),
  ]).size, 4);

console.log(`queryConfig.test: ${passed} checks passed`);

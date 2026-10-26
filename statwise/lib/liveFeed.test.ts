/**
 * Unit tests for the live-state helpers (plan step 2.2): parsing the enricher's
 * live_state, freshness gating, chip/score formatting. Pure inputs — runs on
 * plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/liveFeed.ts statwise/lib/liveFeed.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/liveFeed.test.js
 */
import {
  LIVE_FRESH_MS,
  formatMinute,
  formatScore,
  getLiveState,
  isFresh,
  liveChip,
} from './liveFeed';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  }
  passed += 1;
}

const NOW = Date.parse('2026-10-26T14:00:00Z');
const detailsWith = (live_state: unknown) => ({ live_state } as never);

// --- parsing --------------------------------------------------------------
check(
  'full live_state parsed',
  getLiveState(detailsWith({
    status: 'in', minute: 67, score: { home: 1, away: 0 },
    as_of: '2026-10-26T13:58:00Z',
  })),
  {
    status: 'in',
    minute: 67,
    score: { home: 1, away: 0 },
    asOfMs: Date.parse('2026-10-26T13:58:00Z'),
  },
);
check('no details -> null', getLiveState(undefined), null);
check('null details -> null', getLiveState(null), null);
check('details without live_state -> null', getLiveState({}), null);
check('live_state without status -> null', getLiveState(detailsWith({ minute: 5 })), null);
check(
  'missing score -> null score',
  getLiveState(detailsWith({ status: 'in', minute: 12, as_of: '2026-10-26T13:58:00Z' })),
  { status: 'in', minute: 12, score: null, asOfMs: Date.parse('2026-10-26T13:58:00Z') },
);
check(
  'string minute (bad data) -> null minute',
  getLiveState(detailsWith({ status: 'in', minute: "67'", score: null, as_of: 'x' })),
  { status: 'in', minute: null, score: null, asOfMs: null },
);
check(
  'unparseable as_of -> asOfMs null (never fresh)',
  isFresh(getLiveState(detailsWith({ status: 'in', minute: 3, as_of: 'garbage' })), NOW),
  false,
);

// --- freshness ------------------------------------------------------------
const stamped = (minsAgo: number) =>
  getLiveState(detailsWith({
    status: 'in', minute: 67, score: { home: 1, away: 0 },
    as_of: new Date(NOW - minsAgo * 60_000).toISOString(),
  }));
check('2 min old is fresh', isFresh(stamped(2), NOW), true);
check('9 min old is fresh', isFresh(stamped(9), NOW), true);
check('10 min old is fresh (boundary)', isFresh(stamped(10), NOW), true);
check('11 min old is stale', isFresh(stamped(11), NOW), false);
check('30 min old is stale', isFresh(stamped(30), NOW), false);
check(
  'small future skew tolerated (<10 min)',
  isFresh(getLiveState(detailsWith({
    status: 'in', minute: 5, score: null,
    as_of: new Date(NOW + 60_000).toISOString(),
  })), NOW),
  true,
);
check(
  'big future skew is stale',
  isFresh(getLiveState(detailsWith({
    status: 'in', minute: 5, score: null,
    as_of: new Date(NOW + 30 * 60_000).toISOString(),
  })), NOW),
  false,
);
check('null state is never fresh', isFresh(null, NOW), false);
check('LIVE_FRESH_MS is 10 minutes', LIVE_FRESH_MS, 10 * 60 * 1000);

// --- chips ----------------------------------------------------------------
const live = getLiveState(detailsWith({
  status: 'in', minute: 67, score: { home: 1, away: 0 },
  as_of: new Date(NOW - 2 * 60_000).toISOString(),
}));
check(
  'in-play chip: LIVE, minute + score',
  liveChip(live, NOW),
  { label: 'LIVE', live: true, showMinute: true, showScore: true },
);
const liveNoClock = getLiveState(detailsWith({
  status: 'in', minute: null, score: { home: 0, away: 0 },
  as_of: new Date(NOW - 2 * 60_000).toISOString(),
}));
check(
  'in-play without clock hides minute',
  liveChip(liveNoClock, NOW),
  { label: 'LIVE', live: true, showMinute: false, showScore: true },
);
const post = getLiveState(detailsWith({
  status: 'post', minute: null, score: { home: 2, away: 1 },
  as_of: new Date(NOW - 2 * 60_000).toISOString(),
}));
check(
  'post chip: FT with score',
  liveChip(post, NOW),
  { label: 'FT', live: false, showMinute: false, showScore: true },
);
const pre = getLiveState(detailsWith({
  status: 'pre', minute: null, score: null,
  as_of: new Date(NOW - 2 * 60_000).toISOString(),
}));
check('pre chip -> null (keep scheduled countdown)', liveChip(pre, NOW), null);
check(
  'stale chip -> null (fall back to time guess)',
  liveChip(stamped(45), NOW),
  null,
);
check('null chip -> null', liveChip(null, NOW), null);

// --- formatting -----------------------------------------------------------
check('formatMinute 67', formatMinute(67), "67'");
check('formatMinute 0 (kickoff)', formatMinute(0), "0'");
check('formatMinute null', formatMinute(null), null);
check('formatMinute NaN', formatMinute(Number.NaN), null);
check('formatScore 2-1', formatScore({ home: 2, away: 1 }), '2 - 1');
check('formatScore 0-0', formatScore({ home: 0, away: 0 }), '0 - 0');
check('formatScore missing away', formatScore({ home: 2, away: null }), null);
check('formatScore null', formatScore(null), null);

console.log(`liveFeed.test: ${passed} checks passed`);

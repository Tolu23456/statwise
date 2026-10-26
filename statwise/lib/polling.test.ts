/**
 * Unit tests for the polling policy (plan step 2.1): tiered cadence,
 * visibility gating, network gating. Pure inputs, no network, no React —
 * runs on plain Node so it can be wired into CI cheaply.
 *
 *   tsc statwise/lib/polling.ts statwise/lib/polling.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/polling.test.js
 */
import { POLLING, kickoffMs, pollIntervalMs } from './polling';
import { currentNetworkState, subscribeNetwork } from './network';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  }
  passed += 1;
}

const NOW = Date.parse('2026-10-26T12:00:00Z');
const HOUR = 60 * 60 * 1000;

/** Active, online, fast, no fixtures — the neutral baseline. */
const NEUTRAL = {
  appActive: true,
  online: true,
  slow: false,
  kickoffs: [] as number[],
  now: NOW,
};

// --- Hard stops: hidden app and offline must make ZERO requests -----------
check(
  'hidden app never polls (even mid-live-match)',
  pollIntervalMs({ ...NEUTRAL, appActive: false, kickoffs: [NOW - 60_000] }),
  false,
);
check(
  'hidden app never polls (even offline+slow+live)',
  pollIntervalMs({
    ...NEUTRAL,
    appActive: false,
    online: false,
    slow: true,
    kickoffs: [NOW - 60_000],
  }),
  false,
);
check(
  'offline never polls (even active and live)',
  pollIntervalMs({ ...NEUTRAL, online: false, kickoffs: [NOW - 60_000] }),
  false,
);

// --- Tiered cadence ------------------------------------------------------
check('no fixtures -> quiet tier', pollIntervalMs(NEUTRAL), POLLING.QUIET_MS);
check(
  'kickoff in 6h -> quiet tier',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW + 6 * HOUR] }),
  POLLING.QUIET_MS,
);
check(
  'kickoff in 1h -> soon tier',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW + HOUR] }),
  POLLING.SOON_MS,
);
check(
  'kickoff 30min ago -> live tier',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW - 30 * 60_000] }),
  POLLING.LIVE_MS,
);
check(
  'live beats soon regardless of list order (live first)',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW - 60_000, NOW + 60_000] }),
  POLLING.LIVE_MS,
);
check(
  'live beats soon regardless of list order (soon first)',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW + 60_000, NOW - 60_000] }),
  POLLING.LIVE_MS,
);

// --- Window edges (half-open: [now - LIVE_WINDOW, now + SOON_WINDOW)) ----
check(
  'kickoff exactly 2h ago is no longer live',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW - 2 * HOUR] }),
  POLLING.QUIET_MS,
);
check(
  'kickoff 1ms inside the 2h live window is live',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW - 2 * HOUR + 1] }),
  POLLING.LIVE_MS,
);
check(
  'kickoff exactly 2h away is not soon yet',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW + 2 * HOUR] }),
  POLLING.QUIET_MS,
);
check(
  'kickoff 1ms inside the 2h soon window is soon',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW + 2 * HOUR - 1] }),
  POLLING.SOON_MS,
);
check(
  'kickoff yesterday (long past) -> quiet tier',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW - 26 * HOUR] }),
  POLLING.QUIET_MS,
);
check(
  'invalid kickoff values are ignored',
  pollIntervalMs({ ...NEUTRAL, kickoffs: [Number.NaN, Number.POSITIVE_INFINITY] }),
  POLLING.QUIET_MS,
);

// --- Slow connections halve the frequency --------------------------------
check('slow + live', pollIntervalMs({ ...NEUTRAL, slow: true, kickoffs: [NOW - 60_000] }), POLLING.LIVE_MS * 2);
check('slow + soon', pollIntervalMs({ ...NEUTRAL, slow: true, kickoffs: [NOW + HOUR] }), POLLING.SOON_MS * 2);
check('slow + quiet', pollIntervalMs({ ...NEUTRAL, slow: true }), POLLING.QUIET_MS * 2);
check(
  'slow never applies when the hard stop fires',
  pollIntervalMs({ ...NEUTRAL, slow: true, online: false }),
  false,
);

// --- The old flat 5s cadence is gone -------------------------------------
check('quiet tier is not the old flat 5s', pollIntervalMs(NEUTRAL) === 5_000, false);
check('live tier is not the old flat 5s', pollIntervalMs({ ...NEUTRAL, kickoffs: [NOW - 60_000] }) === 5_000, false);

// --- kickoffMs extraction -------------------------------------------------
check(
  'kickoffMs parses an ISO kickoff',
  kickoffMs({ kickoff_time: '2026-10-26T15:00:00Z' }),
  Date.parse('2026-10-26T15:00:00Z'),
);
check('kickoffMs missing kickoff -> null', kickoffMs({}), null);
check('kickoffMs empty kickoff -> null', kickoffMs({ kickoff_time: '' }), null);
check('kickoffMs garbage -> null', kickoffMs({ kickoff_time: 'not-a-date' }), null);
check('kickoffMs null -> null', kickoffMs({ kickoff_time: null }), null);

// --- Network awareness module (degrades to assume-online/assume-fast) -----
check('node env reports online+fast fallback', currentNetworkState(), { online: true, slow: false });
const unsubscribe = subscribeNetwork(() => {});
check('subscribeNetwork returns an unsubscribe fn', typeof unsubscribe, 'function');
unsubscribe();
check('unsubscribe is idempotent', (unsubscribe(), typeof unsubscribe), 'function');

console.log(`polling.test: ${passed} checks passed`);

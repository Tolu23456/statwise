/**
 * Unit tests for the animation math (entrance stagger, skeleton rows).
 * Pure inputs — plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/animCore.ts statwise/lib/animCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/animCore.test.js
 */
import {
  ENTRANCE_DURATION_MS,
  SHIMMER_CYCLE_MS,
  SKELETON_OPACITY_MAX,
  SKELETON_OPACITY_MIN,
  STAGGER_MAX_INDEX,
  STAGGER_MS,
  entranceDelay,
  skeletonRowCount,
} from './animCore';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- entrance stagger -------------------------------------------------------

check('first item enters immediately', entranceDelay(0), 0);
check('second item waits one beat', entranceDelay(1), STAGGER_MS);
check('third item waits two beats', entranceDelay(2), STAGGER_MS * 2);
check('stagger caps at the max index',
  entranceDelay(STAGGER_MAX_INDEX), STAGGER_MAX_INDEX * STAGGER_MS);
check('deep items do not waterfall (cap, not index * step)',
  entranceDelay(500), STAGGER_MAX_INDEX * STAGGER_MS);
check('cap never exceeds the entrance duration (items overlap, not queue)',
  STAGGER_MAX_INDEX * STAGGER_MS <= ENTRANCE_DURATION_MS, true);
check('negative index degrades to 0', entranceDelay(-3), 0);
check('NaN index degrades to 0', entranceDelay(NaN), 0);
check('float index floors instead of propagating', entranceDelay(2.7), STAGGER_MS * 2);

// --- skeleton rows ----------------------------------------------------------

check('known count renders exactly that many rows', skeletonRowCount(3), 3);
check('zero items falls back to the default', skeletonRowCount(0), 4);
check('negative count falls back to the default', skeletonRowCount(-2), 4);
check('NaN count falls back to the default', skeletonRowCount(NaN), 4);
check('absurd count is capped', skeletonRowCount(9999), 6);
check('custom fallback is honoured', skeletonRowCount(0, 2), 2);
check('custom cap is honoured', skeletonRowCount(50, 4, 3), 3);

// --- timing constants sanity ------------------------------------------------

check('stagger beat is snappy (< 100ms)', STAGGER_MS < 100, true);
check('shimmer cycle positive even', SHIMMER_CYCLE_MS > 0 && SHIMMER_CYCLE_MS % 2 === 0, true);
check('opacity range ordered and never fully hidden',
  SKELETON_OPACITY_MIN > 0 && SKELETON_OPACITY_MIN < SKELETON_OPACITY_MAX, true);

console.log(`animCore.test: ${passed} checks passed`);

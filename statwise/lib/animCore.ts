/**
 * Animation timing/plumbing math — pure, so every stagger and skeleton
 * decision is unit-testable on plain Node (the Animated/Reanimated
 * components in components/ consume these values).
 *
 *   tsc statwise/lib/animCore.ts statwise/lib/animCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/animCore.test.js
 */

/** Base delay between consecutive list items entering the viewport. */
export const STAGGER_MS = 45;

/**
 * Items past this index all enter together (no "waterfall" where item
 * 20 waits ~1s — below-the-fold rows should already be there when the
 * user scrolls to them).
 */
export const STAGGER_MAX_INDEX = 8;

/** Full entrance travel+fade duration for one list item. */
export const ENTRANCE_DURATION_MS = 360;

/** One direction of the skeleton pulse (ms); full cycle = 2x this. */
export const SHIMMER_CYCLE_MS = 800;

/** Skeleton opacity range: floor, ceiling (never fully invisible). */
export const SKELETON_OPACITY_MIN = 0.35;
export const SKELETON_OPACITY_MAX = 0.75;

/**
 * Entrance delay for list item `index` (ms). Pure and total: bad input
 * (negative, NaN, float) degrades to 0 rather than NaN-propagating into
 * an Animated config. Capped at STAGGER_MAX_INDEX * STAGGER_MS (360ms).
 */
export function entranceDelay(index: number): number {
  if (!Number.isFinite(index) || index < 0) return 0;
  return Math.min(Math.floor(index), STAGGER_MAX_INDEX) * STAGGER_MS;
}

/**
 * How many skeleton placeholder rows to render while loading: the real
 * item count when known, otherwise `fallback`, always >= 0 and capped so
 * a bogus count can never render hundreds of pulsing cards.
 */
export function skeletonRowCount(itemCount: number, fallback = 4, cap = 6): number {
  const n = Number.isFinite(itemCount) && itemCount > 0 ? Math.floor(itemCount) : fallback;
  return Math.min(Math.max(n, 0), cap);
}

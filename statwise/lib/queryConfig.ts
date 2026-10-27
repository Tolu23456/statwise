/**
 * React Query cache policy (plan step 4.1).
 *
 * One place for "how stale is stale" and "how long do we keep it", so the
 * numbers are reviewable (and unit-tested) instead of scattered across
 * screens. Why each value:
 *
 *   live-feed (30s)   predictions — realtime events + the tiered polling
 *                     policy (plan 2.1) do the actual refreshing; staleTime
 *                     only decides when a *mount* counts as free data.
 *   aggregate (5min)  counts and insights: they change when the model
 *                     writes, not second by second.
 *   user-data (5min)  referral code / profile-adjacent reads.
 *   backtest (5min)   accuracy history, which only moves when matches
 *                     settle.
 *
 * GC_TIME (10 min) keeps every kind cached across tab switches — the memory
 * is a handful of small JSON arrays. Defaults also turn **off** refetch on
 * window focus: the home feed opts back in explicitly (it's live data),
 * every other screen otherwise re-fires on each tab back-and-forth, which
 * was the main source of duplicate requests.
 *
 * Deliberately free of React / react-query imports so it compiles and is
 * testable on plain Node — queryConfig.test.ts is wired into test-app.yml.
 */

const MINUTE = 60_000;

export type QueryKind = 'live-feed' | 'aggregate' | 'user-data' | 'backtest';

/** Freshness budget per query kind (milliseconds). */
export const STALE_TIME: Record<QueryKind, number> = {
  'live-feed': 30_000,
  aggregate: 5 * MINUTE,
  'user-data': 5 * MINUTE,
  backtest: 5 * MINUTE,
};

/** How long unused data stays in cache (survives tab switches, not sessions). */
export const GC_TIME = 10 * MINUTE;

/**
 * `defaultOptions` for `new QueryClient(...)` — plain object on purpose:
 * no react-query import keeps this file unit-testable outside the app.
 *
 * - refetchOnWindowFocus: false → only live data opts in (see home screen)
 * - retry: 1 with a short delay → a dead network costs one retry, not four
 *   stacked ones from every mounted query
 * - mutations never retry → a payment or profile write must not double-apply
 */
export const QUERY_DEFAULTS = {
  queries: {
    staleTime: 60_000,
    gcTime: GC_TIME,
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
    retry: 1,
  },
  mutations: {
    retry: 0,
  },
};

/**
 * Query keys the invalidation logic touches. Kept here so the realtime
 * handler, the payment callback and the tests all agree on the strings.
 */
export const QUERY_KEYS = {
  predictions: (tier: string) => ['predictions', tier] as const,
  backtesting: (tier: string) => ['backtesting', tier] as const,
  insights: () => ['insights'] as const,
  /** User-scoped so switching accounts can't show the previous user's code. */
  referral: (userId?: string) => ['referral', userId ?? ''] as const,
};

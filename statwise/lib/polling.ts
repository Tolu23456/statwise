/**
 * Polling policy for the predictions feed (plan step 2.1).
 *
 * Pure functions with no React / React Native imports so the policy can be
 * unit-tested with plain Node (see `polling.test.ts`).
 *
 * Tiered cadence (from the plan):
 *   match in play  (kickoff within the last 2h) -> 15s
 *   starting soon  (kickoff within the next 2h) -> 60s
 *   everything else                            -> 300s
 *
 * Savings switches:
 *   app hidden / backgrounded -> no interval polling at all (`false`)
 *   offline                   -> no interval polling at all (`false`)
 *   slow connection           -> double every interval (half the frequency)
 *
 * `false` means "no interval": react-query simply never schedules a refetch,
 * so a hidden tab or a dropped connection costs zero requests. The Supabase
 * realtime channel still pushes new rows, and refetch-on-window-focus covers
 * the return to visibility.
 */

export const POLLING = {
  /** Cadence while a match is in play. */
  LIVE_MS: 15_000,
  /** Cadence when a kickoff is less than SOON_WINDOW_MS away. */
  SOON_MS: 60_000,
  /** Cadence when nothing is close to kicking off. */
  QUIET_MS: 300_000,
  /** Kickoffs up to 2h in the future count as "starting soon". */
  SOON_WINDOW_MS: 2 * 60 * 60 * 1000,
  /** A match counts as live for 2h from kickoff (90 min + stoppage/HT). */
  LIVE_WINDOW_MS: 2 * 60 * 60 * 1000,
  /** Slow connections poll at half frequency. */
  SLOW_MULTIPLIER: 2,
} as const;

export type PollInputs = {
  /** False when the app/tab is backgrounded (AppState 'background'/'inactive'). */
  appActive: boolean;
  /** False when the device/browser reports no connectivity. */
  online: boolean;
  /** True on slow connections (2g/3g-class effective type). */
  slow: boolean;
  /** Epoch-ms kickoff times of the fixtures currently in the feed. */
  kickoffs: number[];
  /** Epoch ms "now" — injected so tests are deterministic. */
  now: number;
};

/**
 * How often the feed should poll, in milliseconds — or `false` for
 * "don't poll at all". Ordered by cost: hard stops (hidden, offline) win
 * over urgency, and live beats soon beats quiet.
 */
export function pollIntervalMs({
  appActive,
  online,
  slow,
  kickoffs,
  now,
}: PollInputs): number | false {
  // Hard stops: a hidden app or a dead connection must make zero requests.
  if (!appActive) return false;
  if (!online) return false;

  let base: number = POLLING.QUIET_MS;
  for (const t of kickoffs) {
    if (!Number.isFinite(t)) continue;
    const sinceKickoff = now - t;
    if (sinceKickoff >= 0 && sinceKickoff < POLLING.LIVE_WINDOW_MS) {
      // A match is in play — the most urgent tier wins outright.
      base = POLLING.LIVE_MS;
      break;
    }
    if (sinceKickoff < 0 && -sinceKickoff < POLLING.SOON_WINDOW_MS) {
      // Kickoff is imminent; the soonest upcoming fixture sets the cadence.
      base = Math.min(base, POLLING.SOON_MS);
    }
  }

  return slow ? base * POLLING.SLOW_MULTIPLIER : base;
}

/**
 * Extract the epoch-ms kickoff of a feed row. Only `kickoff_time` is trusted
 * (a bare `match_date` has no time-of-day, so treating midnight UTC as a
 * kickoff would misclassify rows as live around 00:00–02:00 UTC).
 * Returns `null` when missing or unparseable — such rows simply don't
 * contribute urgency and the feed polls at the quiet tier.
 */
export function kickoffMs(row: {
  kickoff_time?: string | null;
}): number | null {
  const raw = row.kickoff_time;
  if (!raw) return null;
  const t = Date.parse(raw);
  return Number.isFinite(t) ? t : null;
}

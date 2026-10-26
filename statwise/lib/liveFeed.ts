/**
 * Live-state helpers for plan step 2.2 (Live Match Enrichment).
 *
 * `ai/live_enricher.py` stamps `stats.details.live_state` from the real ESPN
 * scoreboard every 5 minutes while a fixture sits in its enrichment window:
 *
 *   { status: 'pre' | 'in' | 'post', minute, score: {home, away}, as_of }
 *
 * The prediction card / detail modal use these helpers to supersede the
 * time-based LIVE guesses (`getMatchStatus`, `isLive` — which only know the
 * kickoff clock) with the real status, minute and score. Pure functions, no
 * React — unit-tested in `liveFeed.test.ts` with plain Node.
 */

/** A live_state older than this is too stale to trust (enricher cadence is
 *  5 min, so 10 min = two missed ticks before we fall back to time guesses). */
export const LIVE_FRESH_MS = 10 * 60 * 1000;

export type LiveState = {
  status: 'pre' | 'in' | 'post' | string;
  minute: number | null;
  score: { home: number | null; away: number | null } | null;
  asOfMs: number | null;
};

/**
 * Extract live state from a prediction's stats.details. Returns null when the
 * enricher hasn't stamped the row yet (all pre-2.2 rows) — callers then keep
 * their existing time-based chips.
 */
export function getLiveState(
  details: { live_state?: {
    status?: string | null;
    minute?: number | null;
    score?: { home: number | null; away: number | null } | null;
    as_of?: string;
  } | null } | null | undefined,
): LiveState | null {
  const ls = details?.live_state;
  if (!ls || typeof ls !== 'object') return null;
  const status = typeof ls.status === 'string' ? ls.status : null;
  if (!status) return null;
  const asOfMs = ls.as_of ? Date.parse(ls.as_of) : Number.NaN;
  return {
    status,
    minute: typeof ls.minute === 'number' ? ls.minute : null,
    score:
      ls.score && typeof ls.score === 'object'
        ? {
            home: typeof ls.score.home === 'number' ? ls.score.home : null,
            away: typeof ls.score.away === 'number' ? ls.score.away : null,
          }
        : null,
    asOfMs: Number.isFinite(asOfMs) ? asOfMs : null,
  };
}

/** Fresh enough to supersede the time-based guess (see LIVE_FRESH_MS). */
export function isFresh(state: LiveState | null, nowMs: number = Date.now()): boolean {
  if (!state) return false;
  if (state.asOfMs === null) return false; // no stamp — never trust it
  return nowMs - state.asOfMs <= LIVE_FRESH_MS && nowMs >= state.asOfMs - LIVE_FRESH_MS;
}

/**
 * What the card should show for a *fresh* live state:
 *   in   → { label: "LIVE" | "67'", live: true,  showMinute, showScore }
 *   post → { label: "FT",    live: false, ... }
 *   pre  → null (kickoff hasn't happened — keep the scheduled countdown)
 *
 * Returns null for stale states too, so callers fall back to getMatchStatus.
 */
export function liveChip(
  state: LiveState | null,
  nowMs: number = Date.now(),
): { label: string; live: boolean; showMinute: boolean; showScore: boolean } | null {
  if (!state || !isFresh(state, nowMs)) return null;
  if (state.status === 'in') {
    return {
      label: 'LIVE',
      live: true,
      showMinute: state.minute !== null,
      showScore: state.score !== null,
    };
  }
  if (state.status === 'post') {
    return { label: 'FT', live: false, showMinute: false, showScore: state.score !== null };
  }
  return null; // 'pre'
}

/** "67'" for the minute badge; null when unknown (pre kickoff / no clock). */
export function formatMinute(minute: number | null): string | null {
  if (minute === null || !Number.isFinite(minute)) return null;
  return `${minute}'`;
}

/** "2 - 1" from a live score; null unless both sides are known. */
export function formatScore(
  score: { home: number | null; away: number | null } | null,
): string | null {
  if (!score || score.home === null || score.away === null) return null;
  return `${score.home} - ${score.away}`;
}

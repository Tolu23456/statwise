/**
 * Quick actions: favourites + share copy (plan step 3.5).
 *
 * Favourites live in AsyncStorage on the device — no account, no backend —
 * so everything here is pure list/string logic that can be unit-tested on
 * plain Node: idempotent toggling, defensive parsing of whatever the
 * device actually stored, and the share text (one wording, always with the
 * responsibility line).
 *
 *   tsc statwise/lib/favorites.ts statwise/lib/favorites.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/favorites.test.js
 */
import { formatKickoff } from './format';

/** Storage key + a sane cap so a device can't grow an unbounded list. */
export const FAVORITES_KEY = 'statwise_favorites_v1';
export const FAVORITES_MAX = 200;

/** Add/remove one id. Never mutates the input; duplicates are impossible. */
export function toggleFavorite(list: readonly string[], id: string): string[] {
  if (!id) return [...list];
  return list.includes(id) ? list.filter(x => x !== id) : [...list, id];
}

export function isFavorite(list: readonly string[], id: string): boolean {
  return !!id && list.includes(id);
}

/**
 * Parse whatever is actually in storage. Anything unexpected (null, corrupt
 * JSON, a number, objects mixed in) degrades to a usable list rather than
 * crashing the home screen on boot.
 */
export function parseFavorites(raw: string | null | undefined): string[] {
  if (!raw) return [];
  let val: unknown;
  try {
    val = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(val)) return [];
  const out: string[] = [];
  for (const item of val) {
    if (typeof item !== 'string' || !item) continue;
    if (!out.includes(item)) out.push(item);
    if (out.length >= FAVORITES_MAX) break;
  }
  return out;
}

/** Persistable snapshot (the caller writes the result to AsyncStorage). */
export function serializeFavorites(list: readonly string[]): string {
  return JSON.stringify([...new Set(list)].slice(0, FAVORITES_MAX));
}

/** Chip copy: "3 saved" / "1 saved" / "None saved yet". */
export function favoritesLabel(count: number): string {
  if (count <= 0) return 'None saved yet';
  return count === 1 ? '1 saved' : `${count} saved`;
}

export type FavoriteSyncPlan = { toAdd: string[]; toRemove: string[] };

/**
 * Reconciliation plan between the device list (authoritative — the user
 * tapped it) and the server copy (user_favorites, needed by the notify
 * cron to alert the RIGHT user about THEIR bookmarked match).
 *
 *   add    = local \ server   (bookmarks the cron has never seen)
 *   remove = server \ local   (un-bookmarks done on this or another device)
 *
 * The device list is never modified by this function: a failed sync must
 * not silently change what the user sees — the next reconcile retries.
 */
export function favoriteSyncPlan(
  local: readonly string[],
  server: readonly string[],
): FavoriteSyncPlan {
  const localSet = new Set(local.filter(Boolean));
  const serverSet = new Set(server.filter(Boolean));
  return {
    toAdd: [...localSet].filter(id => !serverSet.has(id)),
    toRemove: [...serverSet].filter(id => !localSet.has(id)),
  };
}

export type ShareableMatch = {
  home_team?: string | null;
  away_team?: string | null;
  prediction?: string | null;
  confidence?: number | null;
  league?: string | null;
  kickoff_time?: string | null;
  stats?: { is_ai_pick?: boolean } | null;
};

/**
 * The share text. Deterministic (kickoff is formatted from an injected-ish
 * helper), always names both teams, the pick, and ends with the
 * responsibility line — sharing an AI Pick without it would read as a
 * guarantee.
 */
export function shareText(p: ShareableMatch): string {
  const teams = `${p.home_team ?? '?'} vs ${p.away_team ?? '?'}`;
  const head = `StatWise ${p.stats?.is_ai_pick ? 'AI Pick' : 'prediction'}: ${p.prediction ?? '?'}`;
  const conf = typeof p.confidence === 'number' ? ` (${Math.round(p.confidence)}%)` : '';
  // Only surface the kickoff when it actually parses — sharing the raw
  // "not-a-date" string would look broken.
  const rawKo = p.kickoff_time ?? '';
  const kickoff = rawKo && !Number.isNaN(Date.parse(rawKo)) ? formatKickoff(rawKo) : '';
  const meta = [p.league, kickoff ? `kickoff ${kickoff}` : null]
    .filter(Boolean)
    .join(' · ');
  return [
    `${head}${conf}`,
    teams + (meta ? ` — ${meta}` : ''),
    'Play responsibly — predictions are probabilistic, not guarantees.',
  ].join('\n');
}

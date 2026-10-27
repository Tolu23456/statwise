/**
 * Pure core for the forum feed (background loading + local cache work).
 *
 * Kept free of AsyncStorage/supabase/React imports so the merge rules and
 * the message envelope builder are unit-testable on plain Node like every
 * other lib module here (wired into test-app.yml).
 *
 *   tsc statwise/lib/forumFeedCore.ts statwise/lib/forumFeedCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/forumFeedCore.test.js
 */

/**
 * Server-side hard cap: send_forum_message does `left(btrim(msg), 1000)`
 * (supabase-security-migration.sql). A media envelope whose JSON exceeds
 * this gets TRUNCATED — invalid JSON that would render as raw source — so
 * the client must reject before sending.
 */
export const SERVER_MESSAGE_CAP = 1000;

/** Newest rows kept in the local cache (bounded storage + payload). */
export const FORUM_CACHE_CAP = 200;

/** Optimistic (not-yet-confirmed) rows older than this are dropped. */
export const OPTIMISTIC_TTL_MS = 5 * 60_000;

/** The row shape these rules need — structurally compatible with ForumMessage. */
export type ForumFeedRow = {
  id: string;
  user_id: string;
  message: string;
  created_at: string;
};

export const isOptimisticId = (id: string): boolean => id.startsWith('optimistic_');

/**
 * Merge feed rows: upsert by id (later wins), reconcile optimistic bubbles
 * against the confirmed row they became, drop stale optimistic leftovers,
 * sort chronologically (stable id tiebreak), keep the newest FORUM_CACHE_CAP.
 *
 * Reconciliation rules (why a bubble can't double-render):
 *  - a real row removes an optimistic row from the SAME user with the SAME
 *    message text — that is the confirmed copy of the bubble;
 *  - a real row NEVER removes an optimistic row with different text;
 *  - any optimistic row older than OPTIMISTIC_TTL_MS is dropped on merge —
 *    if its send never confirmed, it is gone rather than lingering forever.
 */
export function mergeForumRows<T extends ForumFeedRow>(
  existing: readonly T[],
  incoming: readonly T[],
  now: number = Date.now(),
): T[] {
  const merged = new Map<string, T>();
  for (const row of [...existing, ...incoming]) {
    merged.set(row.id, row);
  }

  const realMessagesByUser = new Map<string, Set<string>>();
  for (const row of merged.values()) {
    if (isOptimisticId(row.id)) continue;
    let set = realMessagesByUser.get(row.user_id);
    if (!set) {
      set = new Set();
      realMessagesByUser.set(row.user_id, set);
    }
    set.add(row.message);
  }

  const cutoff = now - OPTIMISTIC_TTL_MS;
  for (const row of [...merged.values()]) {
    if (!isOptimisticId(row.id)) continue;
    const confirmed = realMessagesByUser.get(row.user_id)?.has(row.message) ?? false;
    const stale = Date.parse(row.created_at) <= cutoff;
    if (confirmed || stale) merged.delete(row.id);
  }

  return [...merged.values()]
    .sort(
      (a, b) =>
        (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0) ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    )
    .slice(-FORUM_CACHE_CAP);
}

export type ForumContent = {
  text?: string;
  image?: string;
  video?: string;
};

/**
 * Wire format for one message. Text-only messages go over the RPC as the
 * plain string (readable history stays readable); any media attachment
 * wraps in JSON, which parseMessage() on the client understands — an image
 * AND a video can ride in the SAME message.
 *
 * Returns null when the encoded content would exceed SERVER_MESSAGE_CAP
 * (caller shows an honest "shorten by N" error instead of letting the
 * server silently truncate the JSON into garbage).
 */
export function buildForumContent(
  text: string,
  image?: string | null,
  video?: string | null,
): { content: string; overflow: number } {
  const trimmed = text.trim();
  if (!image && !video) {
    const overflow = Math.max(0, trimmed.length - SERVER_MESSAGE_CAP);
    return { content: trimmed, overflow };
  }
  const payload: ForumContent = {
    ...(image ? { image } : {}),
    ...(video ? { video } : {}),
    ...(trimmed ? { text: trimmed } : {}),
  };
  const content = JSON.stringify(payload);
  return { content, overflow: Math.max(0, content.length - SERVER_MESSAGE_CAP) };
}

/** Persistable subset: confirmed rows only, newest first, capped. */
export function cacheableForumRows<T extends ForumFeedRow>(rows: readonly T[]): T[] {
  return rows.filter(r => !isOptimisticId(r.id)).slice(-FORUM_CACHE_CAP);
}

// ── mentions + reply previews ──────────────────────────────────────────────

/**
 * Mention token. Mirrors the SQL side (notify_forum_participants uses
 * '@([A-Za-z0-9_]{3,30})') — if these two regexes ever diverge, the text
 * highlighted as a mention would not notify (or vice versa).
 */
export const MENTION_PATTERN = '@([A-Za-z0-9_]{3,30})';

export type MentionSegment = { text: string; mention: boolean };

/**
 * Split message text into mention / non-mention segments for highlighting
 * (RN <Text> renders each segment as a styled child span). Pure + total:
 * empty text → empty list, never throws.
 */
export function tokenizeMentions(text: string): MentionSegment[] {
  if (!text) return [];
  const segments: MentionSegment[] = [];
  const re = new RegExp(MENTION_PATTERN, 'g');
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segments.push({ text: text.slice(last, m.index), mention: false });
    segments.push({ text: m[0], mention: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) segments.push({ text: text.slice(last), mention: false });
  return segments;
}

/**
 * One-line preview of a message for the reply quote bar — from the RAW
 * stored value: JSON envelopes become "[Image]"/"[Video]"/"[Media]"
 * markers, plain text is shown as-is, and the result is capped so a
 * quoted wall of text cannot blow up the composer area.
 */
export function replyPreview(raw: string, max = 60): string {
  if (!raw) return '';
  let text = raw;
  if (raw.trimStart().startsWith('{')) {
    try {
      const parsed = JSON.parse(raw) as ForumContent;
      const markers = [
        parsed.image ? '[Image]' : null,
        parsed.video ? '[Video]' : null,
        parsed.text ?? '',
      ].filter(Boolean) as string[];
      text = markers.join(' ') || '[Media]';
    } catch {
      text = '[Message]';
    }
  }
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? flat.slice(0, max - 1) + '…' : flat;
}

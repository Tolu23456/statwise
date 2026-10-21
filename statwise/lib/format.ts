/**
 * Shared display helpers so dates, times and odds read the same everywhere
 * in the app (home feed, cards, detail modal).
 */

const DAY_MS = 24 * 60 * 60 * 1000;

function safeDate(raw?: string | null): Date | null {
  if (!raw) return null;
  const d = new Date(raw);
  return isNaN(d.getTime()) ? null : d;
}

/** Start of the local calendar day for a Date. */
function startOfDay(d: Date): Date {
  const c = new Date(d);
  c.setHours(0, 0, 0, 0);
  return c;
}

/** Whole days between the start of today and the start of the target day. */
export function dayOffset(raw?: string | null, now: Date = new Date()): number | null {
  const d = safeDate(raw);
  if (!d) return null;
  const diff = startOfDay(d).getTime() - startOfDay(now).getTime();
  return Math.round(diff / DAY_MS);
}

/** "Today" / "Tomorrow" / "Yesterday" / "Sat 25 Oct" */
export function formatDayLabel(raw?: string | null, now: Date = new Date()): string {
  const offset = dayOffset(raw, now);
  if (offset === null) return '';
  if (offset === 0) return 'Today';
  if (offset === 1) return 'Tomorrow';
  if (offset === -1) return 'Yesterday';
  const d = safeDate(raw)!;
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
}

/** True when the match is in the past (kickoff + ~2h grace). */
export function hasStarted(raw?: string | null, now: Date = new Date()): boolean {
  const d = safeDate(raw);
  if (!d) return false;
  return d.getTime() < now.getTime();
}

/** True when the match is currently being played (within 2h of kickoff). */
export function isLive(raw?: string | null, now: Date = new Date()): boolean {
  const d = safeDate(raw);
  if (!d) return false;
  const t = d.getTime();
  return t <= now.getTime() && now.getTime() - t < 2 * 60 * 60 * 1000;
}

/** Clock time, e.g. "19:45". */
export function formatClock(raw?: string | null): string {
  const d = safeDate(raw);
  if (!d) return raw ?? '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Human-friendly kickoff label:
 *   "Today 19:45" · "Tomorrow 16:00" · "Sat 25 Oct 16:00" · "in 2h 15m" · "Live"
 */
export function formatKickoff(raw?: string | null, now: Date = new Date()): string {
  const d = safeDate(raw);
  if (!d) return raw ?? '';
  if (isLive(raw, now)) return 'Live';
  const offset = dayOffset(raw, now);
  if (offset === 0) return `Today ${formatClock(raw)}`;
  if (offset === 1) return `Tomorrow ${formatClock(raw)}`;
  if (offset !== null && offset < 0) return formatClock(raw);
  const mins = Math.round((d.getTime() - now.getTime()) / 60000);
  if (mins > 0 && offset !== null && offset < 7) {
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    return h > 0 ? `in ${h}h ${m}m` : `in ${m}m`;
  }
  return `${formatDayLabel(raw, now)} ${formatClock(raw)}`;
}

/** Compact countdown used for "starts soon" chips. */
export function countdownLabel(raw?: string | null, now: Date = new Date()): string | null {
  const d = safeDate(raw);
  if (!d) return null;
  const mins = Math.round((d.getTime() - now.getTime()) / 60000);
  if (mins <= 0 || mins > 24 * 60) return null;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** Implied probability (with vig removed) from decimal odds. */
export function deVigProbabilities(
  home?: number | null,
  draw?: number | null,
  away?: number | null,
): [number, number, number] | null {
  if (!home || !draw || !away) return null;
  if (home <= 1 || draw <= 1 || away <= 1) return null;
  const raw = [1 / home, 1 / draw, 1 / away];
  const total = raw.reduce((a, b) => a + b, 0);
  if (total <= 0) return null;
  return [raw[0] / total, raw[1] / total, raw[2] / total];
}

/** Format a fractional edge as "+5.2%". */
export function formatEdge(edge?: number | null): string | null {
  if (edge === null || edge === undefined || !isFinite(edge)) return null;
  const sign = edge > 0 ? '+' : '';
  return `${sign}${edge.toFixed(1)}%`;
}

/** Number with thousands separators. */
export function formatNumber(n: number): string {
  return n.toLocaleString();
}

/** Chat-style timestamp: "14:32" today, "Yesterday 14:32", "12 Oct 14:32". */
export function formatChatTime(iso?: string | null, now: Date = new Date()): string {
  const d = safeDate(iso);
  if (!d) return '';
  const offset = dayOffset(iso, now);
  if (offset === 0) return formatClock(iso);
  if (offset === -1) return `Yesterday ${formatClock(iso)}`;
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], {
    day: 'numeric',
    month: 'short',
    ...(sameYear ? {} : { year: 'numeric' }),
  }) + ` ${formatClock(iso)}`;
}

/** Compact relative time for "updated 3m ago". */
export function timeAgo(iso?: string | null, now: Date = new Date()): string {
  const d = safeDate(iso);
  if (!d) return '';
  const secs = Math.max(0, Math.round((now.getTime() - d.getTime()) / 1000));
  if (secs < 60) return 'just now';
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

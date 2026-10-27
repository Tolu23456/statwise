import { isAllowedHost } from './validation';

/**
 * Booking-code guards (plan step 4.3 — "Booking code protection").
 *
 * Pure module (no supabase import) so the rules unit-test on plain Node;
 * lib/bookings.ts re-exports them for the screens.
 *
 * The threat model: the code row lives in `app_settings`, which — before
 * supabase-security-migration.sql — any anon-key holder could overwrite.
 * Even with the table locked down server-side, the client refuses to put a
 * malformed code into a URL and refuses to share anything that isn't a
 * genuine https://sportybet.com link, so a tampered row degrades to the
 * honest "not generated" state instead of an arbitrary destination.
 */

/**
 * SportyBet share codes are short alphanumeric tokens (optionally `-`/`_`).
 * Anything else arriving in a `code` field is not something we will put in a
 * URL or render on screen.
 */
const BOOKING_CODE_RE = /^[A-Za-z0-9_-]{4,32}$/;

export function sanitizeBookingCode(raw: unknown): string {
  return typeof raw === 'string' && BOOKING_CODE_RE.test(raw) ? raw : '';
}

/** Where a valid code loads on SportyBet Nigeria. */
export function bookingCodeLoadUrl(code: string): string {
  return `https://www.sportybet.com/ng/?shareCode=${encodeURIComponent(code)}`;
}

/** Hosts a booking share URL may legitimately point at. */
const SHARE_URL_HOSTS = ['sportybet.com'] as const;

type CodePayload = { code: string; shareUrl: string | null };

/**
 * The only URL the app will share or render for a booking code: either the
 * payload's `share_url` — when it is https AND genuinely on sportybet.com —
 * or a URL we build ourselves from the validated code. `evil-sportybet.com`
 * and `http://` do not pass (exact host or dot-suffix match, https only).
 */
export function safeShareUrl(payload: CodePayload): string {
  const code = sanitizeBookingCode(payload.code);
  if (!code) return '';
  if (isAllowedHost(payload.shareUrl, SHARE_URL_HOSTS)) return payload.shareUrl as string;
  return bookingCodeLoadUrl(code);
}

/**
 * Central input validation + sanitization (plan step 4.3 — "Input validation").
 *
 * Every user-typed value in the app goes through one of these functions
 * before it is used for a lookup, stored, or sent to Supabase. Pure module:
 * no react-native / storage imports, so it unit-tests on plain Node.
 *
 * Covered inputs (wired in the screens):
 *   email            → isValidEmail / normalizeEmail      (login, signup, forgot-password)
 *   password         → passwordError                      (signup)
 *   display name     → displayNameError                   (signup, profile)
 *   referral code    → isValidReferralCodeFormat          (signup)
 *   forum message    → sanitizeUserText + MESSAGE_MAX     (forum)
 *   honeypot fields  → isHoneypotTripped                  (signup, forgot-password)
 *   booking share URL→ isAllowedHost                      (bookings, BookingCodeModal)
 * Search on the home screen only filters an in-memory list client-side and
 * never reaches a query or a URL, so it needs a cap, not a validator — the
 * TextInput carries maxLength.
 */

export const LIMITS = {
  displayNameMin: 2,
  displayNameMax: 50,
  emailMax: 254,
  passwordMin: 6,
  referralCodeMax: 20,
  /** Longest forum message the server RPC accepts (kept in sync there). */
  messageMax: 1000,
  searchMax: 80,
} as const;

/** Format-only email check (cheap client gate; Supabase re-validates). */
export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

/** Email as the auth endpoints should receive it. */
export function normalizeEmail(email: string): string {
  return email.trim();
}

/** Returns a user-facing error, or null when the name is acceptable. */
export function displayNameError(name: string): string | null {
  const trimmed = name.trim();
  if (!trimmed) return 'Display name is required.';
  if (trimmed.length < LIMITS.displayNameMin) return 'Name must be at least 2 characters.';
  if (trimmed.length > LIMITS.displayNameMax) return `Name must be ${LIMITS.displayNameMax} characters or fewer.`;
  return null;
}

/** Password rules (length only — strength display stays a UI concern). */
export function passwordError(password: string): string | null {
  if (!password) return 'Password is required.';
  if (password.length < LIMITS.passwordMin) return `Password must be at least ${LIMITS.passwordMin} characters.`;
  return null;
}

/**
 * Referral codes are the signup-time value shape: upper-case alphanumerics,
 * 4–20 chars (backend mints `user.id.substring(0, 8).toUpperCase()`).
 */
export function isValidReferralCodeFormat(code: string): boolean {
  const trimmed = code.trim().toUpperCase();
  return trimmed.length >= 4 && trimmed.length <= LIMITS.referralCodeMax && /^[A-Z0-9]+$/.test(trimmed);
}

// Control characters (C0 minus tab/newline, plus DEL) — these are the classic
// "invisible payload" carriers in chat logs and stored values.
const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;
// Zero-width + bidi-override + line/paragraph separators: spoofing material
// (fake RTL text, invisible zero-width spam) that has no business in a chat
// message or a display name.
const INVISIBLE_CHARS = /[\u200B-\u200F\u202A-\u202E\u2028\u2029]/g;

/**
 * Strip characters no message ever legitimately needs, then enforce the
 * length cap. Mirrored server-side in `send_forum_message` (the SQL is the
 * authority; this is the fast path that avoids a wasted round-trip).
 */
export function sanitizeUserText(text: string, maxLen: number = LIMITS.messageMax): string {
  return text
    .replace(CONTROL_CHARS, '')
    .replace(INVISIBLE_CHARS, '')
    .slice(0, maxLen);
}

/** True when the raw input would be cut by the cap (surfaced as an error). */
export function exceedsLimit(text: string, maxLen: number): boolean {
  return text.length > maxLen;
}

/**
 * Bot trap: a field humans never see (positioned off-screen, no autofill)
 * and therefore never fill. If it has content, the submitter is a script.
 * The caller should drop the action silently — telling the bot it failed
 * only teaches it to retry.
 */
export function isHoneypotTripped(value: string | null | undefined): boolean {
  return (value ?? '').trim().length > 0;
}

/**
 * https-only host allowlist check for URLs that came out of stored payloads
 * rather than out of our own code (e.g. the booking code's share_url).
 * Exact host or dot-suffix match — `evil-sportybet.com` does not pass.
 */
export function isAllowedHost(url: unknown, allowedHosts: readonly string[]): boolean {
  if (typeof url !== 'string' || url.length > 2048) return false;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== 'https:') return false;
  const host = parsed.hostname.toLowerCase();
  return allowedHosts.some(
    allowed => host === allowed.toLowerCase() || host.endsWith('.' + allowed.toLowerCase()),
  );
}

/**
 * User-facing error copy (plan step 3.4).
 *
 * Raw Supabase / fetch errors leak internals ("JWT expired", "relation
 * public.foo does not exist", stack-ish messages) and read as if the app is
 * broken. Every message the UI shows goes through `friendlyError` first:
 * known failures get an actionable sentence, unknown ones get an honest
 * generic one — never the raw string.
 *
 * Pure string logic — unit-tested in errors.test.ts on plain Node and wired
 * into CI via test-app.yml.
 */

/** True for the error shapes that mean "the request never reached the server". */
export function isNetworkError(raw: unknown): boolean {
  const msg = String(raw ?? '').toLowerCase();
  return (
    msg.includes('failed to fetch') ||
    msg.includes('network request failed') ||
    msg.includes('networkerror') ||
    msg.includes('load failed') ||
    msg.includes('econnrefused') ||
    msg.includes('econnreset') ||
    msg.includes('etimedout') ||
    msg.includes('timeout of') ||
    msg.includes('timed out') ||
    msg.includes('other side closed') ||
    msg.includes('socket hang up') ||
    // The browser's own wording when the tab is offline:
    msg.includes('internet') ||
    msg.includes('unable to connect') ||
    msg.includes('offline')
  );
}

/** Ordered rules: first match wins. Keep specific before generic. */
const RULES: Array<[RegExp, string]> = [
  [/(failed to fetch|network request failed|networkerror|load failed|econnrefused|econnreset|etimedout|timeout of|timed out|other side closed|socket hang up|internet|unable to connect|offline)/,
    "You appear to be offline — check your connection and try again."],
  [/(too many requests|rate limit|429)/,
    "Too many attempts — wait a moment and try again."],
  [/(invalid login credentials|invalid credentials|wrong password)/,
    "Email or password is incorrect."],
  [/(email not confirmed|confirm your email|confirmation link)/,
    "Confirm your email first — check your inbox for the link."],
  [/(already (?:been )?registered|already exists|user already)/,
    "An account with that email already exists — try signing in instead."],
  [/(password should be at least|password must be at least|weak password)/,
    "Password must be at least 8 characters long."],
  [/(token (?:has )?expired|expired token|invalid token|otp has expired|jwt)/,
    "That link has expired — request a new one."],
  [/(row-level security|permission denied|not authorized|forbidden|403)/,
    "You don't have permission to do that."],
  [/(relation .* does not exist|schema cache|column .* does not exist)/,
    "The app hit a server-side setup problem — please try again later."],
  [/(unique constraint|duplicate key)/,
    "That has already been created — try something else."],
];

/**
 * Map any thrown/returned error to a sentence we are happy to show a user.
 * Unknown input (null, objects, empty strings) gets the generic fallback.
 */
export function friendlyError(raw: unknown): string {
  const msg = String(raw ?? '').trim();
  if (!msg) return 'Something went wrong — please try again.';
  // Rules are written in lower case; match case-insensitively so
  // "Failed to fetch" behaves like "failed to fetch".
  const lower = msg.toLowerCase();
  for (const [pattern, copy] of RULES) {
    if (pattern.test(lower)) return copy;
  }
  return 'Something went wrong — please try again.';
}

/** Retry affordance copy — one definition so every retry says the same thing. */
export const RETRY_COPY = {
  label: 'Try again',
  pending: 'Retrying…',
  banner: "Couldn't load predictions — tap to retry",
} as const;

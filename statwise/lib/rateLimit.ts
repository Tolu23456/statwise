/**
 * Client-side rate limiting (plan step 4.3 — "Rate limiting").
 *
 * The app has no server routes of its own: every write goes through Supabase
 * REST, and the one bespoke endpoint — the `send_forum_message` RPC — is
 * rate-limited *server-side* in supabase-security-migration.sql. This module
 * is the client half: it stops a misbehaving tab, an accidental double-tap
 * storm or a scripted form from hammering auth/RLS endpoints before the
 * request ever leaves the device, and gives the user a real "wait N seconds"
 * message instead of a raw 429.
 *
 * Honest scope: a determined attacker talks to Supabase directly and is
 * bounded by Supabase's own per-IP limits (Auth) and the RPC check (forum).
 * This layer is UX + defense in depth — which is exactly what a client
 * can provide.
 *
 * Pure logic (`evaluateHits`) takes the clock as an argument so the tests
 * never sleep; the wrapper keeps per-key hit lists in memory for the session.
 */

export type RateRule = {
  /** How many attempts are allowed inside one window. */
  max: number;
  /** Window length in milliseconds. */
  windowMs: number;
};

export type RateVerdict = {
  allowed: boolean;
  /** How long until the next attempt would be allowed (0 when allowed). */
  retryAfterMs: number;
  /** Attempts left in the current window after this one. */
  remaining: number;
};

/**
 * Shared budgets — the forum rule is intentionally identical to the
 * server-side check in send_forum_message (6 / 60s per user) so the client
 * and server never disagree about what "too fast" means.
 */
export const RATE_RULES = {
  /** Login + signup + password reset share one budget (credential stuffing). */
  authAttempt: { max: 5, windowMs: 5 * 60_000 },
  /** Forum sends, mirrored by the SQL RPC check. */
  forumSend: { max: 6, windowMs: 60_000 },
  /** Profile saves (display name / avatar / notification toggles). */
  profileSave: { max: 5, windowMs: 60_000 },
} as const;

/**
 * Pure sliding-window evaluation: drop hits older than the window, then
 * decide. Returns the pruned list so the caller can persist exactly what
 * remains (no hidden state).
 */
export function evaluateHits(
  times: readonly number[],
  rule: RateRule,
  now: number,
): RateVerdict & { kept: number[] } {
  const kept = times.filter(t => now - t < rule.windowMs);
  if (kept.length >= rule.max) {
    const oldest = Math.min(...kept);
    return {
      allowed: false,
      retryAfterMs: Math.max(1, oldest + rule.windowMs - now),
      remaining: 0,
      kept,
    };
  }
  const recorded = [...kept, now];
  return {
    allowed: true,
    retryAfterMs: 0,
    remaining: rule.max - recorded.length,
    kept: recorded,
  };
}

/** Session-scoped hit store (module-level: one per JS context). */
const hits = new Map<string, number[]>();

/**
 * Record an attempt against `key` and report whether it is allowed.
 * Every call counts — a blocked attempt still consumes nothing extra but
 * does not silently reset the window either.
 */
export function checkRateLimit(key: string, rule: RateRule, now: number = Date.now()): RateVerdict {
  const verdict = evaluateHits(hits.get(key) ?? [], rule, now);
  // Persist the pruned window whether or not the attempt was allowed: a
  // blocked attempt must not extend the lockout (sliding it forever would
  // punish a user who keeps tapping), but the real hits must survive it.
  hits.set(key, verdict.kept);
  return { allowed: verdict.allowed, retryAfterMs: verdict.retryAfterMs, remaining: verdict.remaining };
}

/** User-facing wait copy, shared by every screen that throttles a submit. */
export function rateLimitMessage(verdict: RateVerdict): string {
  const seconds = Math.max(1, Math.ceil(verdict.retryAfterMs / 1000));
  return `Too many attempts. Try again in ${seconds} second${seconds === 1 ? '' : 's'}.`;
}

/** Tests only — clear state between cases. */
export function resetRateLimits(): void {
  hits.clear();
}

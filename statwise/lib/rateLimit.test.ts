/**
 * Unit tests for the sliding-window rate limiter (plan step 4.3).
 * The clock is injected — no sleeps, deterministic on any machine.
 * Plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/rateLimit.ts statwise/lib/rateLimit.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/rateLimit.test.js
 */
import {
  RATE_RULES,
  checkRateLimit,
  evaluateHits,
  rateLimitMessage,
  resetRateLimits,
} from './rateLimit';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- rule sanity: budgets are real throttles, not decoration ---------------
for (const [name, rule] of Object.entries(RATE_RULES)) {
  check(`${name}: allows at least one attempt`, rule.max >= 1, true);
  check(`${name}: window is finite and <= 5 min`, rule.windowMs > 0 && rule.windowMs <= 300_000, true);
}
check('login/signup/reset share one auth budget',
  RATE_RULES.authAttempt.max <= 10, true);
check('forum client budget matches the server RPC check (6 / 60s)',
  RATE_RULES.forumSend.max === 6 && RATE_RULES.forumSend.windowMs === 60_000, true);

// --- pure sliding window --------------------------------------------------
const rule = { max: 3, windowMs: 1000 };

check('first attempt allowed', evaluateHits([], rule, 0).allowed, true);
check('attempt recorded, room for two more in a fresh window',
  evaluateHits([], rule, 0).remaining, 2);

const three = evaluateHits([], rule, 0);                 // attempt 1
const twoMore = evaluateHits(three.kept, rule, 100);      // attempt 2
const full = evaluateHits(twoMore.kept, rule, 200);       // attempt 3 — budget now full
check('third attempt is still allowed (window not yet exceeded)', full.allowed, true);
check('but nothing remains after it', full.remaining, 0);
const denied = evaluateHits(full.kept, rule, 300);
check('the attempt after max is blocked', denied.allowed, false);
check('blocked verdict reports remaining 0', denied.remaining, 0);
check('blocked verdict waits until the oldest hit expires',
  denied.retryAfterMs, 1000 - 300);

// Oldest hit ages out → allowed again with exactly one slot.
const afterWindow = evaluateHits(full.kept, rule, 1001);
check('window slides: allowed once the oldest hit expires', afterWindow.allowed, true);
check('the freed slot is consumed by this attempt (no room left)', afterWindow.remaining, 0);

// Blocked attempts do not extend the lockout (retryAfter never grows).
const blockedAt500 = evaluateHits(full.kept, rule, 500);
check('retryAfter is computed from real hits only', blockedAt500.retryAfterMs, 500);
const blockedLater = evaluateHits(blockedAt500.kept, rule, 800);
check('waiting longer shortens the remaining wait', blockedLater.retryAfterMs < 500, true);

// --- wrapper state (session store) ----------------------------------------
resetRateLimits();
const t0 = 1_000_000;
check('wrapper allows under budget',
  checkRateLimit('k1', rule, t0).allowed, true);
checkRateLimit('k1', rule, t0 + 10);
checkRateLimit('k1', rule, t0 + 20);
const blocked = checkRateLimit('k1', rule, t0 + 30);
check('wrapper blocks at max', blocked.allowed, false);
check('other keys unaffected (per-action budgets)',
  checkRateLimit('k2', rule, t0 + 30).allowed, true);
const recovered = checkRateLimit('k1', rule, t0 + 1001);
check('after the window the key recovers', recovered.allowed, true);
check('recovery consumes the one freed slot', recovered.remaining, 0);
check('and the window is full again on the next attempt',
  checkRateLimit('k1', rule, t0 + 1002).allowed, false);

// --- message copy ---------------------------------------------------------
const msg = rateLimitMessage({ allowed: false, retryAfterMs: 700, remaining: 0 });
check('message rounds up to whole seconds', msg.includes('1 second'), true);
check('message tells the user to wait', msg.startsWith('Too many attempts.'), true);
check('plural seconds', rateLimitMessage({ allowed: false, retryAfterMs: 5000, remaining: 0 }).includes('5 seconds'), true);

resetRateLimits();
console.log(`rateLimit.test: ${passed} checks passed`);

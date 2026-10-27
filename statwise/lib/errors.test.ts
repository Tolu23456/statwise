/**
 * Unit tests for the user-facing error copy (plan step 3.4).
 *
 * Two things must never happen: a raw error leaking into the UI (JWT,
 * relation names, stack-ish text) and a real failure showing a message that
 * tells the user nothing. Pure string checks — plain Node, wired into CI
 * via test-app.yml.
 *
 *   tsc statwise/lib/errors.ts statwise/lib/errors.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/errors.test.js
 */
import { friendlyError, isNetworkError, RETRY_COPY } from './errors';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- network failures (the case this step is really about) ----------------
const NETWORK_RAW = [
  'TypeError: Failed to fetch',
  'Network request failed',
  'NetworkError when attempting to fetch resource.',
  'Timeout of 30000ms exceeded',
  'Unable to connect to the internet',
  'fetch failed, you are offline',
];
for (const raw of NETWORK_RAW) {
  check(`isNetworkError: ${raw.slice(0, 28)}`, isNetworkError(raw), true);
  const copy = friendlyError(raw);
  check(`network copy is actionable: ${raw.slice(0, 20)}`,
    /offline|connection/i.test(copy) && copy.endsWith('.'), true);
}

// --- every known category gets its own actionable sentence ----------------
const CASES: Array<[string, RegExp]> = [
  ['Rate limit', /Too many attempts/],
  ['Too many requests', /Too many attempts/],
  ['Invalid login credentials', /Email or password is incorrect/],
  ['Email not confirmed', /Confirm your email/],
  ['User already registered', /already exists/],
  ['Password should be at least 8 characters', /at least 8 characters/],
  ['JWT expired', /link has expired/],
  ['jwt expired', /link has expired/],
  ['new row violates row-level security policy', /permission/],
  ['permission denied for table predictions', /permission/],
  ['relation "public.push_subscriptions" does not exist', /server-side setup problem/],
  ['duplicate key value violates unique constraint', /already been created/],
];
for (const [raw, pattern] of CASES) {
  const copy = friendlyError(raw);
  check(`category copy: ${raw.slice(0, 32)}`, pattern.test(copy), true);
  check(`category copy is a full sentence: ${raw.slice(0, 20)}`,
    copy.length > 10 && copy.endsWith('.'), true);
}

// --- internals must never leak --------------------------------------------
const LEAKY = [
  'JWT expired: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def',
  'relation "public.user_profiles" does not exist',
  'column "stats" of relation "predictions" does not exist',
  'error: password authentication failed for user "service_role"',
];
for (const raw of LEAKY) {
  const copy = friendlyError(raw);
  check(`no leak: ${raw.slice(0, 26)}`,
    !/eyJ|relation|column|service_role/i.test(copy), true);
}

// --- unknown / empty input: honest generic, never "undefined" -------------
check('null -> generic', friendlyError(null), 'Something went wrong — please try again.');
check('undefined -> generic', friendlyError(undefined), 'Something went wrong — please try again.');
check('empty string -> generic', friendlyError(''), 'Something went wrong — please try again.');
check('whitespace -> generic', friendlyError('   '), 'Something went wrong — please try again.');
check('number-like input is safe', friendlyError(500), 'Something went wrong — please try again.');
check('unmapped odd message -> generic',
  friendlyError('weird bespoke failure'), 'Something went wrong — please try again.');

// --- non-network errors are classified correctly --------------------------
check('auth failure is not a network error', isNetworkError('Invalid login credentials'), false);
check('empty is not a network error', isNetworkError(''), false);
check('null is not a network error', isNetworkError(null), false);

// --- retry copy: consistent across the app --------------------------------
check('retry label present', RETRY_COPY.label.length > 0, true);
check('retry banner is user-friendly',
  RETRY_COPY.banner.startsWith("Couldn't") && RETRY_COPY.banner.endsWith('retry'), true);

console.log(`errors.test: ${passed} checks passed`);

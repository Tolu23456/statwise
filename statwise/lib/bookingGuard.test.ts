/**
 * Unit tests for the booking-code guards (plan step 4.3 — "Booking code
 * protection"). Plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/bookingGuard.ts statwise/lib/validation.ts \
 *       statwise/lib/bookingGuard.test.ts --outDir <dir> --module commonjs \
 *       --target es2019 --strict --skipLibCheck
 *   node <dir>/bookingGuard.test.js
 */
import {
  bookingCodeLoadUrl,
  safeShareUrl,
  sanitizeBookingCode,
} from './bookingGuard';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- code shape -----------------------------------------------------------
check('plain code accepted', sanitizeBookingCode('AB12CD34'), 'AB12CD34');
check('code with hyphen accepted', sanitizeBookingCode('AB12-CD34'), 'AB12-CD34');
check('code with underscore accepted', sanitizeBookingCode('ab_12_cd'), 'ab_12_cd');
check('3-char code rejected (min 4)', sanitizeBookingCode('ABC'), '');
check('33-char code rejected (max 32)', sanitizeBookingCode('A'.repeat(33)), '');
check('space rejected', sanitizeBookingCode('AB12 CD34'), '');
check('slash/path traversal rejected', sanitizeBookingCode('../../etc/passwd'), '');
check('query-injection characters rejected', sanitizeBookingCode('AB&x=1#frag'), '');
check('URL scheme smuggling rejected', sanitizeBookingCode('javascript:alert(1)'), '');
check('quote rejected (payload echo)', sanitizeBookingCode(`AB'><img>`), '');
check('empty rejected', sanitizeBookingCode(''), '');
check('non-string rejected', sanitizeBookingCode({ code: 'AB12' }), '');
check('null rejected', sanitizeBookingCode(null), '');

// --- load URL building ----------------------------------------------------
check('load URL points at SportyBet NG',
  bookingCodeLoadUrl('AB12CD34'), 'https://www.sportybet.com/ng/?shareCode=AB12CD34');
check('code is query-encoded when embedded',
  bookingCodeLoadUrl('AB&CD'), 'https://www.sportybet.com/ng/?shareCode=AB%26CD');

// --- share URL decision ---------------------------------------------------
const GOOD = { code: 'AB12CD34', shareUrl: 'https://www.sportybet.com/ng/?shareCode=AB12CD34' };
check('trusted shareUrl used verbatim', safeShareUrl(GOOD), GOOD.shareUrl);
check('subdomain of sportybet.com allowed',
  safeShareUrl({ code: 'AB12CD34', shareUrl: 'https://m.sportybet.com/x' }),
  'https://m.sportybet.com/x');
check('missing shareUrl falls back to a URL we build',
  safeShareUrl({ code: 'AB12CD34', shareUrl: null }),
  bookingCodeLoadUrl('AB12CD34'));
check('lookalike host falls back (never shared as-is)',
  safeShareUrl({ code: 'AB12CD34', shareUrl: 'https://sportybet.com.evil.io/steal' }),
  bookingCodeLoadUrl('AB12CD34'));
check('prefixed lookalike host falls back',
  safeShareUrl({ code: 'AB12CD34', shareUrl: 'https://evilsportybet.com/x' }),
  bookingCodeLoadUrl('AB12CD34'));
check('http falls back to the https URL we build',
  safeShareUrl({ code: 'AB12CD34', shareUrl: 'http://www.sportybet.com/x' }),
  bookingCodeLoadUrl('AB12CD34'));
check('javascript: scheme falls back',
  safeShareUrl({ code: 'AB12CD34', shareUrl: 'javascript:alert(1)' }),
  bookingCodeLoadUrl('AB12CD34'));
check('invalid code yields no URL at all (nothing to share)',
  safeShareUrl({ code: '../evil', shareUrl: 'https://www.sportybet.com/x' }), '');
check('invalid code + null shareUrl also yields nothing',
  safeShareUrl({ code: '', shareUrl: null }), '');

console.log(`bookingGuard.test: ${passed} checks passed`);

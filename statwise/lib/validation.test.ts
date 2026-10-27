/**
 * Unit tests for input validation + sanitization (plan step 4.3).
 * Plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/validation.ts statwise/lib/validation.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/validation.test.js
 */
import {
  LIMITS,
  displayNameError,
  exceedsLimit,
  isAllowedHost,
  isHoneypotTripped,
  isValidEmail,
  isValidReferralCodeFormat,
  normalizeEmail,
  passwordError,
  sanitizeUserText,
} from './validation';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- email ----------------------------------------------------------------
check('plain email ok', isValidEmail('user@example.com'), true);
check('plus-tag email ok', isValidEmail('user+tag@example.co.uk'), true);
check('no domain rejected', isValidEmail('user@localhost'), false);
check('missing @ rejected', isValidEmail('userexample.com'), false);
check('empty rejected', isValidEmail(''), false);
check('spaces rejected', isValidEmail('us er@example.com'), false);
check('surrounding whitespace still validates after normalize',
  isValidEmail('  user@example.com '), true);
check('normalize trims only (auth must see the exact address)',
  normalizeEmail('  user@example.com '), 'user@example.com');

// --- display name ---------------------------------------------------------
check('missing name', displayNameError(''), 'Display name is required.');
check('whitespace-only name', displayNameError('   '), 'Display name is required.');
check('one char too short', displayNameError('A'), 'Name must be at least 2 characters.');
check('two chars accepted', displayNameError('Al'), null);
check('normal name accepted', displayNameError('Ada Lovelace'), null);
check('51 chars rejected', displayNameError('x'.repeat(51)),
  `Name must be ${LIMITS.displayNameMax} characters or fewer.`);
check('50 chars accepted', displayNameError('x'.repeat(50)), null);

// --- password -------------------------------------------------------------
check('missing password', passwordError(''), 'Password is required.');
check('five chars too short', passwordError('12345'), 'Password must be at least 6 characters.');
check('six chars accepted', passwordError('123456'), null);

// --- referral code --------------------------------------------------------
check('valid 8-char code', isValidReferralCodeFormat('ABCD1234'), true);
check('lowercase normalized then accepted', isValidReferralCodeFormat('abcd1234'), true);
check('too short rejected', isValidReferralCodeFormat('ABC'), false);
check('too long rejected', isValidReferralCodeFormat('A'.repeat(21)), false);
check('punctuation rejected', isValidReferralCodeFormat('ABCD-123'), false);
check('emoji rejected', isValidReferralCodeFormat('ABCD\u{1F600}'), false);

// --- text sanitization ----------------------------------------------------
check('newline preserved', sanitizeUserText('line1\nline2'), 'line1\nline2');
check('tab preserved', sanitizeUserText('a\tb'), 'a\tb');
check('NUL and C0 stripped', sanitizeUserText('a\u0000b\u0007c\u001Fd'), 'abcd');
check('DEL stripped', sanitizeUserText('a\u007Fb'), 'ab');
check('zero-width stripped', sanitizeUserText('in\u200Bvis\u200Dible'), 'invisible');
check('bidi override stripped', sanitizeUserText('ok\u202Eevil'), 'okevil');
check('line/paragraph separators stripped', sanitizeUserText('a\u2028b\u2029c'), 'abc');
check('default cap = message limit',
  sanitizeUserText('x'.repeat(2000)).length, LIMITS.messageMax);
check('cap parameter honoured',
  sanitizeUserText('abcdef', 3), 'abc');
check('exceedsLimit flags oversized input', exceedsLimit('x'.repeat(1001), LIMITS.messageMax), true);
check('exceedsLimit passes normal input', exceedsLimit('hello', LIMITS.messageMax), false);

// --- honeypot -------------------------------------------------------------
check('empty honeypot passes', isHoneypotTripped(''), false);
check('whitespace honeypot passes', isHoneypotTripped('   '), false);
check('null honeypot passes', isHoneypotTripped(null), false);
check('filled honeypot trips', isHoneypotTripped('http://spam.example'), true);

// --- share-URL allowlist (booking code protection) -------------------------
const ALLOWED = ['sportybet.com'] as const;
check('official https URL allowed', isAllowedHost('https://www.sportybet.com/ng/?shareCode=AB12', ALLOWED), true);
check('bare domain allowed', isAllowedHost('https://sportybet.com/x', ALLOWED), true);
check('lookalike domain rejected', isAllowedHost('https://www.sportybet.com.evil.io/x', ALLOWED), false);
check('prefix trick rejected', isAllowedHost('https://evilsportybet.com/x', ALLOWED), false);
check('http rejected', isAllowedHost('http://www.sportybet.com/x', ALLOWED), false);
check('javascript scheme rejected', isAllowedHost('javascript:alert(1)', ALLOWED), false);
check('data URL rejected', isAllowedHost('data:text/html,<script>', ALLOWED), false);
check('non-string rejected', isAllowedHost(undefined, ALLOWED), false);
check('garbage rejected', isAllowedHost('not a url', ALLOWED), false);
check('oversized URL rejected', isAllowedHost('https://www.sportybet.com/' + 'a'.repeat(3000), ALLOWED), false);

console.log(`validation.test: ${passed} checks passed`);

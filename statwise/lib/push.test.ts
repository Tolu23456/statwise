/**
 * Unit tests for the pure push helpers (native Expo push): token shape
 * validation, row building, permission gating, notification deep-link
 * validation.  Imports ONLY ./pushCore — that module has no imports, so this
 * compiles and runs on plain Node with no npm install (wired into
 * test-app.yml).
 *
 *   tsc statwise/lib/pushCore.ts statwise/lib/push.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/push.test.js
 */
import {
  buildTokenRow,
  isExpoToken,
  notificationTargetUrl,
  notificationsEnabled,
  pushBlockReason,
} from './pushCore';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- Expo token shape -----------------------------------------------------
check('modern Expo token accepted', isExpoToken('ExpoPushToken[abc123]'), true);
check('legacy ExponentPushToken accepted',
  isExpoToken('ExponentPushToken[abc123]'), true);
check('surrounding whitespace tolerated',
  isExpoToken('  ExpoPushToken[abc]  '), true);
check('arbitrary string rejected', isExpoToken('https://push.example/e'), false);
check('empty string rejected', isExpoToken(''), false);
check('null rejected', isExpoToken(null), false);
check('non-string rejected', isExpoToken(12345), false);
check('token without brackets rejected', isExpoToken('ExpoPushTokenabc'), false);
check('unrelated bracketed value rejected',
  isExpoToken('SomethingElse[x]'), false);

// --- row building ---------------------------------------------------------
const row = buildTokenRow('user-1', 'ExpoPushToken[abc]', 'android', '2026-10-29T00:00:00.000Z');
check('row maps user, token, platform and timestamp', row, {
  user_id: 'user-1',
  expo_token: 'ExpoPushToken[abc]',
  platform: 'android',
  updated_at: '2026-10-29T00:00:00.000Z',
});
check('token is trimmed', buildTokenRow('u', '  ExpoPushToken[x]  ', 'android',
  't').expo_token, 'ExpoPushToken[x]');
check('platform is free-form (android/ios/web)',
  buildTokenRow('u', 'ExpoPushToken[x]', 'ios', 't').platform, 'ios');

// --- permission gating ----------------------------------------------------
check(
  'unsupported reason on web mentions the app',
  /Android app/.test(pushBlockReason('unsupported', 'web') ?? ''),
  true,
);
check(
  'unsupported reason on native mentions device',
  /device/.test(pushBlockReason('unsupported', 'native') ?? ''),
  true,
);
check('web unsupported reason differs from native',
  pushBlockReason('unsupported', 'web') === pushBlockReason('unsupported', 'native'),
  false,
);
check(
  'denied reason mentions device settings',
  /device settings/.test(pushBlockReason('denied') ?? ''),
  true,
);
check('granted has no block reason', pushBlockReason('granted'), null);
check('default (prompt dismissed) has no permanent block reason',
  pushBlockReason('default'), null);

// --- notification opt-out -------------------------------------------------
check('notifications default ON when profile missing', notificationsEnabled(undefined), true);
check('notifications default ON for null profile', notificationsEnabled(null), true);
check('notifications default ON when key absent', notificationsEnabled({}), true);
check('explicit true keeps notifications on', notificationsEnabled({ notifications: true }), true);
check('explicit false turns notifications off',
  notificationsEnabled({ notifications: false }), false);

// --- deep-link validation -------------------------------------------------
const ORIGIN = 'https://statwise-flax.vercel.app';
check('absolute same-origin https URL accepted',
  notificationTargetUrl({ url: 'https://statwise-flax.vercel.app/picks' }, ORIGIN),
  'https://statwise-flax.vercel.app/picks');
check('relative path accepted',
  notificationTargetUrl({ url: '/picks' }, ORIGIN), '/picks');
check('javascript: URL rejected',
  notificationTargetUrl({ url: 'javascript:alert(1)' }, ORIGIN), null);
check('data: URL rejected',
  notificationTargetUrl({ url: 'data:text/html,<script>x</script>' }, ORIGIN), null);
check('http (non-TLS) URL rejected',
  notificationTargetUrl({ url: 'http://statwise-flax.vercel.app/picks' }, ORIGIN), null);
check('off-origin https URL rejected',
  notificationTargetUrl({ url: 'https://evil.example/picks' }, ORIGIN), null);
check('subdomain of the trusted origin rejected (exact origin only)',
  notificationTargetUrl({ url: 'https://evil.statwise-flax.vercel.app/' }, ORIGIN), null);
check('missing url rejected', notificationTargetUrl({}, ORIGIN), null);
check('non-string url rejected', notificationTargetUrl({ url: 42 }, ORIGIN), null);
check('null data rejected', notificationTargetUrl(null, ORIGIN), null);
check('empty url rejected', notificationTargetUrl({ url: '   ' }, ORIGIN), null);

console.log(`push.test: ${passed} checks passed`);

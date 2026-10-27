/**
 * Unit tests for the web-push helpers (plan step 3.2): base64url decoding,
 * subscription row building, support/permission gating. Pure inputs — runs
 * on plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/push.ts statwise/lib/push.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/push.test.js
 */
import {
  buildSubscriptionRow,
  currentPermission,
  pushBlockReason,
  pushSupported,
  urlBase64ToUint8Array,
} from './push';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- support detection (Node has no window/Notification) ------------------
check('pushSupported() false in Node', pushSupported(), false);
check('currentPermission() unsupported in Node', currentPermission(), 'unsupported');

// --- base64url decoding ---------------------------------------------------
// 'AQID' is [1,2,3]; base64url variant of standard 'AQID'.
const bytes = urlBase64ToUint8Array('AQID');
check('urlBase64ToUint8Array decodes base64url', Array.from(bytes), [1, 2, 3]);
// '-'/'_' variants: bytes [251, 239] = '++8' in standard, '--8' in url-safe
// ('+' → '-'). Both must decode identically.
check(
  'url-safe alphabet handled (- and _)',
  Array.from(urlBase64ToUint8Array('--8')),
  Array.from(urlBase64ToUint8Array('++8')),
);
check('url-safe decode values', Array.from(urlBase64ToUint8Array('--8')), [251, 239]);
check(
  "url-safe '_' handled like '/'",
  Array.from(urlBase64ToUint8Array('AQ_')),
  Array.from(urlBase64ToUint8Array('AQ/')),
);
check(
  'padding added for lengths %4 != 0',
  Array.from(urlBase64ToUint8Array('QQ')),   // 'QQ==' -> [65]
  [65],
);
check('round-trips a real VAPID-shaped key', (() => {
  // Realistic uncompressed P-256 point: 65 bytes → 87-char base64url, starts 'B' (0x04).
  const key = 'BDBVep_E6Q4zWH2ix-wRNluApcrvFDleg6jN8hc8YYar0PUaP2SJrtP4HUJnjLHW-yBFao-02f4jSG2St9wBJks';
  const out = urlBase64ToUint8Array(key);
  return out.length === 65 && out[0] === 0x04; // uncompressed EC point prefix
})(), true);

// --- subscription row building --------------------------------------------
const row = buildSubscriptionRow(
  'user-1',
  { endpoint: 'https://push.example/abc', keys: { p256dh: 'p', auth: 'a' } },
  'Mozilla/5.0',
);
check('row maps endpoint + keys + agent', row, {
  user_id: 'user-1',
  endpoint: 'https://push.example/abc',
  p256dh: 'p',
  auth: 'a',
  user_agent: 'Mozilla/5.0',
});
const rowNoAgent = buildSubscriptionRow(
  'u2',
  { endpoint: 'e', keys: { p256dh: 'p', auth: 'a' } },
  null,
);
check('null user agent preserved', rowNoAgent.user_agent, null);

// --- block reasons --------------------------------------------------------
check(
  'unsupported reason mentions browsers',
  /browser/.test(pushBlockReason('unsupported') ?? ''),
  true,
);
check(
  'denied reason mentions browser settings',
  /settings/.test(pushBlockReason('denied') ?? ''),
  true,
);
check('granted has no block reason', pushBlockReason('granted'), null);
check('default (prompt dismissed) has no permanent block reason', pushBlockReason('default'), null);

console.log(`push.test: ${passed} checks passed`);

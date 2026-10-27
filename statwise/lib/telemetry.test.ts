/**
 * Unit tests for the telemetry core (plan step 4.4): event construction,
 * caps, the no-op-before-migration contract, the rate budget and the
 * slow-query predicate. The sink is injected — no network, deterministic.
 * Plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/telemetry.ts statwise/lib/telemetry.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/telemetry.test.js
 */
import {
  APP_VERSION,
  TELEMETRY,
  TELEMETRY_KINDS,
  buildEvent,
  captureEvent,
  getSessionId,
  isSlowResource,
  makeSessionId,
  pathOf,
  setEventSink,
  type TelemetryEvent,
} from './telemetry';
// The telemetry budget rides on the shared session limiter; the test resets
// it to measure the budget deterministically.
import { resetRateLimits } from './rateLimit';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

const sid = 'sess-1';

// --- buildEvent: shape + kind validation ----------------------------------
const basic = buildEvent({ kind: 'render_error', message: 'boom', sessionId: sid })!;
check('basic event keeps its kind', basic.kind, 'render_error');
check('basic event carries the session', basic.session_id, sid);
check('basic event carries the app version', basic.app_version, APP_VERSION);
check('no context stays null', basic.context, null);
check('no path stays null', basic.path, null);

for (const kind of TELEMETRY_KINDS) {
  check(`kind '${kind}' accepted`, buildEvent({ kind, message: 'x', sessionId: sid })?.kind, kind);
}
check('unknown kind rejected',
  buildEvent({ kind: 'dropped_pii_field', message: 'x', sessionId: sid }), null);
check('kind must be a string', buildEvent({ kind: 42 as any, message: 'x', sessionId: sid }), null);
check('missing session rejected',
  buildEvent({ kind: 'custom', message: 'x', sessionId: '' }), null);

// --- buildEvent: message hygiene ------------------------------------------
check('long message capped at the hard limit',
  buildEvent({ kind: 'custom', message: 'x'.repeat(5000), sessionId: sid })!.message.length,
  TELEMETRY.maxMessageLen);
check('control chars stripped from message',
  buildEvent({ kind: 'custom', message: 'a\u0000b\u0007c', sessionId: sid })!.message, 'abc');
check('zero-width chars stripped from message',
  buildEvent({ kind: 'custom', message: 'a\u200Bb', sessionId: sid })!.message, 'ab');
check('null message becomes the placeholder',
  buildEvent({ kind: 'custom', message: null, sessionId: sid })!.message, '(no message)');
check('path capped and sanitized',
  buildEvent({ kind: 'custom', message: 'x', path: '/a\u0000b', sessionId: sid })!.path, '/ab');

// --- buildEvent: context caps ---------------------------------------------
const smallCtx = buildEvent({ kind: 'custom', message: 'x', context: { a: 1 }, sessionId: sid })!;
check('small context preserved', smallCtx.context, { a: 1 });

const bigCtx = buildEvent({
  kind: 'custom', message: 'x', context: { blob: 'y'.repeat(10_000) }, sessionId: sid,
})!.context as any;
check('oversized context truncated, never dropped', bigCtx?.truncated, true);
check('truncated context still under the byte cap',
  JSON.stringify(bigCtx).length <= TELEMETRY.maxContextBytes, true);

const circular: any = { name: 'loop' };
circular.self = circular;
const cycCtx = buildEvent({ kind: 'custom', message: 'x', context: circular, sessionId: sid })!.context;
check('circular context degrades instead of throwing', cycCtx, { note: 'context not serializable' });

const nonObj = buildEvent({ kind: 'custom', message: 'x', context: [1, 2] as any, sessionId: sid })!.context;
check('array context wrapped as {value:...}', nonObj, { value: [1, 2] });

// --- session ids -----------------------------------------------------------
const id1 = makeSessionId();
const id2 = makeSessionId();
check('session ids are non-empty', id1.length > 5, true);
check('session ids differ', id1 !== id2, true);
check('getSessionId is stable within a run',
  getSessionId() === getSessionId(), true);

// --- captureEvent: transport contract -------------------------------------
// Budget is global to the module (one session per page load in production),
// so the transport tests run first against a fresh budget.

setEventSink(null);
check('no sink → capture is a no-op (pre-migration behaviour)',
  captureEvent({ kind: 'custom', message: 'x' }), false);

const received: TelemetryEvent[] = [];
let sinkCalls = 0;
setEventSink(async events => { sinkCalls += 1; received.push(...events); });

check('capture returns true once a sink exists',
  captureEvent({ kind: 'custom', message: 'hello' }), true);
check('event delivered to the sink', received.length, 1);
check('delivered event is well-formed', received[0].kind, 'custom');

check('capture with an unknown kind is rejected before the sink',
  captureEvent({ kind: 'made_up', message: 'x' }), false);
check('the rejected capture did not reach the sink', received.length, 1);

let throwingCalls = 0;
setEventSink(() => { throwingCalls += 1; throw new Error('network down'); });
check('a throwing sink never surfaces to the caller',
  captureEvent({ kind: 'custom', message: 'x' }), false);
check('the throwing sink was still invoked once (no retry loop)', throwingCalls, 1);

// Budget: the limiter is charged per capture attempt (thrown or not), so a
// failing sink cannot be retry-stormed either. Reset first — the two
// captures above legitimately consumed slots of the same session budget.
resetRateLimits();
let budgetCalls = 0;
setEventSink(async () => { budgetCalls += 1; });
let allowed = 0;
for (let i = 0; i < TELEMETRY.budget.max + 5; i += 1) {
  if (captureEvent({ kind: 'custom', message: `msg ${i}` })) allowed += 1;
}
check('session budget caps captures at the configured max', allowed, TELEMETRY.budget.max);
check('sink only ever saw the budgeted events', budgetCalls, TELEMETRY.budget.max);

setEventSink(null);

// --- slow-query predicate --------------------------------------------------
const BASE = 'https://fwpriiugfjhtoymcbmso.supabase.co/rest/v1/predictions?select=tier';
check('slow Supabase REST call flagged', isSlowResource(BASE, 2500), true);
check('fast Supabase REST call ignored', isSlowResource(BASE, 120), false);
check('exactly at threshold flagged',
  isSlowResource(BASE, TELEMETRY.slowQueryThresholdMs), true);
check('auth endpoint flagged', isSlowResource('https://x.supabase.co/auth/v1/token', 1500), true);
check('static asset ignored even when slow',
  isSlowResource('https://cdn.example.com/app.js', 9000), false);
check('our own bundle chunk ignored when slow',
  isSlowResource('/_expo/static/js/web/main-abc.js', 5000), false);
check('data: URL ignored', isSlowResource('data:application/json,{}', 5000), false);
check('blob: URL ignored', isSlowResource('blob:https://x/123', 5000), false);
check('relative supabase path matched', isSlowResource('/rest/v1/forum_messages', 1200), true);
check('non-string URL rejected', isSlowResource(undefined as any, 5000), false);
check('NaN duration rejected', isSlowResource(BASE, NaN), false);
check('negative duration rejected', isSlowResource(BASE, -5), false);
check('infinity duration rejected', isSlowResource(BASE, Infinity), false);

// --- pathOf: never leaks query strings ------------------------------------
check('pathOf strips the query string',
  pathOf('https://x.supabase.co/rest/v1/predictions?email=leak@x.com'), '/rest/v1/predictions');
check('pathOf handles relative URLs', pathOf('/rest/v1/matches?a=1'), '/rest/v1/matches');
check('pathOf on garbage stays bounded', pathOf('???'.repeat(200)).length <= 200, true);

console.log(`telemetry.test: ${passed} checks passed`);

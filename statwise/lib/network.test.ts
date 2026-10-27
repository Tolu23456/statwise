/**
 * Unit tests for network awareness (plan step 3.4: offline indicators).
 *
 * The offline banner and the polling back-off both trust
 * `currentNetworkState()` / `subscribeNetwork()`, so these tests fake the
 * `navigator` and `window` globals and drive the real browser events at
 * them. Plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/network.ts statwise/lib/network.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/network.test.js
 */
import { currentNetworkState, subscribeNetwork } from './network';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

const g = globalThis as Record<string, unknown>;
const origWindow = g.window;
const origNavigatorDesc = Object.getOwnPropertyDescriptor(globalThis, 'navigator');

function setNavigator(value: unknown): void {
  Object.defineProperty(globalThis, 'navigator', {
    value, configurable: true, writable: true,
  });
}
function restoreNavigator(): void {
  if (origNavigatorDesc) Object.defineProperty(globalThis, 'navigator', origNavigatorDesc);
}

try {
  // --- currentNetworkState ------------------------------------------------
  setNavigator({ onLine: true });
  check('online baseline', currentNetworkState(), { online: true, slow: false });

  setNavigator({ onLine: false });
  check('navigator.onLine false -> offline', currentNetworkState().online, false);

  setNavigator({ onLine: true, connection: { effectiveType: '3g' } });
  check('3g counts as slow', currentNetworkState(), { online: true, slow: true });
  setNavigator({ onLine: true, connection: { effectiveType: '2g' } });
  check('2g counts as slow', currentNetworkState().slow, true);
  setNavigator({ onLine: true, connection: { effectiveType: '4g' } });
  check('4g is not slow', currentNetworkState().slow, false);
  setNavigator({ onLine: false, connection: { effectiveType: '3g' } });
  check('offline + slow can both be true', currentNetworkState(), { online: false, slow: true });

  // No navigator at all (SSR / tests): degrade honestly to "assume online".
  setNavigator(undefined);
  check('missing navigator -> assume online/fast',
    currentNetworkState(), { online: true, slow: false });

  // --- subscribeNetwork ---------------------------------------------------
  const listeners: Record<string, () => void> = {};
  const removed: string[] = [];
  setNavigator({ onLine: true });
  g.window = {
    addEventListener: (type: string, fn: () => void) => { listeners[type] = fn; },
    removeEventListener: (type: string) => { removed.push(type); },
  };

  const seen: unknown[] = [];
  const unsubscribe = subscribeNetwork(state => seen.push(state));
  check('subscribes to both browser events',
    Object.keys(listeners).sort(), ['offline', 'online']);

  // The browser fires 'offline' — the callback must see the new snapshot.
  setNavigator({ onLine: false });
  listeners['offline']();
  check('offline event pushes the new state', seen[seen.length - 1], { online: false, slow: false });

  setNavigator({ onLine: true });
  listeners['online']();
  check('online event pushes the new state', seen[seen.length - 1], { online: true, slow: false });
  check('one callback per event', seen.length, 2);

  unsubscribe();
  check('unsubscribe detaches the listeners',
    removed.sort(), ['offline', 'online']);

  // No window (SSR / React Native without DOM): must be a safe no-op.
  delete g.window;
  const noop = subscribeNetwork(() => { throw new Error('must not fire'); });
  check('missing window -> unsubscribe is callable', typeof noop, 'function');
  noop();
  check('missing window never throws', true, true);
} finally {
  restoreNavigator();
  if (origWindow === undefined) delete g.window;
  else g.window = origWindow;
}

console.log(`network.test: ${passed} checks passed`);

/**
 * Network awareness for the polling policy (plan step 2.1).
 *
 * Uses the browser's `online`/`offline` events and, where available, the
 * Network Information API (`navigator.connection.effectiveType`) to flag
 * slow (2g/3g-class) connections so polling can back off.
 *
 * No React / React Native imports: this is plain DOM-capable JS so it runs
 * in the PWA (our deployment target: static web on :5000 + gh-pages).
 * Where the APIs don't exist (Safari/Firefox, native without NetInfo) it
 * degrades honestly to "assume online, assume fast" — the worst case there
 * is the cadence we already had, never a stall.
 */

export type NetworkState = {
  online: boolean;
  slow: boolean;
};

type ConnectionLike = {
  effectiveType?: string;
  addEventListener?: (type: string, listener: () => void) => void;
  removeEventListener?: (type: string, listener: () => void) => void;
};

function connection(): ConnectionLike | undefined {
  if (typeof navigator === 'undefined') return undefined;
  return (navigator as Navigator & { connection?: ConnectionLike }).connection;
}

function isSlowEffectiveType(effectiveType: string | undefined): boolean {
  return effectiveType === 'slow-2g' || effectiveType === '2g' || effectiveType === '3g';
}

/** Snapshot of the current network state. Safe to call anywhere. */
export function currentNetworkState(): NetworkState {
  if (typeof navigator === 'undefined') return { online: true, slow: false };
  const online = navigator.onLine !== false;
  const slow = isSlowEffectiveType(connection()?.effectiveType);
  return { online, slow };
}

/**
 * Subscribe to network changes. Returns an unsubscribe function.
 * On platforms without the events the callback simply never fires after
 * the initial state (which the caller already has).
 */
export function subscribeNetwork(
  onChange: (state: NetworkState) => void,
): () => void {
  if (typeof window === 'undefined') return () => {};

  const update = () => onChange(currentNetworkState());
  window.addEventListener('online', update);
  window.addEventListener('offline', update);

  const conn = connection();
  conn?.addEventListener?.('change', update);

  return () => {
    window.removeEventListener('online', update);
    window.removeEventListener('offline', update);
    conn?.removeEventListener?.('change', update);
  };
}

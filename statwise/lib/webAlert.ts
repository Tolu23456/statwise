/**
 * Web alert patch (plan 5.1 support) — makes `Alert.alert` actually show
 * something on web.
 *
 * react-native-web exports `class Alert { static alert() {} }`: a no-op.
 * Every alert in the app (20 call sites — payment results, booking errors,
 * profile actions…) is invisible on web, and the Flutterwave checkout runs
 * ONLY on web, so "Payment Successful" / "Payment not verified" feedback
 * would never be seen. `installWebAlert()` (called once from
 * `app/_layout.tsx`) replaces the static with a FIFO overlay rendered into
 * a single fixed-position div — zero new packages, matching the app's
 * zero-dep rule. Native platforms are left untouched (RN's own Alert).
 *
 * Queue/normalisation logic lives in `webAlertCore.ts` and is unit-tested;
 * this file is the DOM half.
 */
import { Alert, Platform } from 'react-native';
import {
  AlertQueue,
  AlertSpec,
  createAlertQueue,
  currentAlert,
  dismissAlert,
  enqueueAlert,
} from './webAlertCore';

let queue: AlertQueue = createAlertQueue();
let root: HTMLDivElement | null = null;
let installed = false;

function css(el: HTMLElement, styles: Partial<CSSStyleDeclaration>): void {
  Object.assign(el.style, styles);
}

function ensureRoot(): HTMLDivElement {
  if (root && document.body.contains(root)) return root;
  root = document.createElement('div');
  css(root, {
    position: 'fixed',
    inset: '0',
    display: 'none',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(0,0,0,0.45)',
    zIndex: '2147483000',
    padding: '24px',
    fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  });
  document.body.appendChild(root);
  return root;
}

function render(): void {
  const host = ensureRoot();
  const entry = currentAlert(queue);
  host.innerHTML = '';
  if (!entry) {
    host.style.display = 'none';
    return;
  }
  host.style.display = 'flex';

  const card = document.createElement('div');
  css(card, {
    background: '#ffffff',
    borderRadius: '14px',
    padding: '18px 16px 14px',
    width: '100%',
    maxWidth: '340px',
    boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
    textAlign: 'center',
  });

  const title = document.createElement('div');
  title.textContent = entry.title;
  css(title, { fontSize: '16px', fontWeight: '600', color: '#111418' });
  card.appendChild(title);

  if (entry.message) {
    const msg = document.createElement('div');
    msg.textContent = entry.message;
    css(msg, {
      fontSize: '14px',
      lineHeight: '20px',
      color: '#444a52',
      marginTop: '8px',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
    });
    card.appendChild(msg);
  }

  const row = document.createElement('div');
  css(row, { display: 'flex', gap: '8px', marginTop: '16px' });
  entry.buttons.forEach((b, i) => {
    const btn = document.createElement('button');
    btn.textContent = b.text;
    const destructive = b.style === 'destructive';
    const primary = !destructive && i === 0;
    css(btn, {
      flex: '1',
      padding: '10px 6px',
      borderRadius: '10px',
      border: primary ? '1px solid #111418' : '1px solid #d6d9de',
      background: primary ? '#111418' : '#f4f5f7',
      color: destructive ? '#b3261e' : primary ? '#ffffff' : '#111418',
      fontSize: '14px',
      fontWeight: '600',
      cursor: 'pointer',
      lineHeight: '18px',
    });
    btn.onclick = () => {
      try {
        b.onPress?.();
      } catch {
        /* a throwing handler must still dismiss the alert */
      }
      queue = dismissAlert(queue);
      render();
    };
    row.appendChild(btn);
  });
  card.appendChild(row);
  host.appendChild(card);
}

/**
 * Idempotent. On web it swaps the no-op static for the overlay impl; on
 * native (and during SSR, before a DOM exists) it is a no-op.
 */
export function installWebAlert(): void {
  if (installed) return;
  installed = true;
  if (Platform.OS !== 'web' || typeof document === 'undefined') return;
  (Alert as unknown as { alert: (t: string, m?: string, b?: unknown) => void }).alert = (
    title,
    message,
    buttons,
  ) => {
    queue = enqueueAlert(queue, { title, message, buttons } as AlertSpec);
    render();
  };
}

/** Test seam: the overlay's current queue length (0 when nothing shows). */
export function webAlertDepth(): number {
  return queue.entries.length;
}

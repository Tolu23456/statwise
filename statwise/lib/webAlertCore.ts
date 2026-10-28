/**
 * Web-alert core (plan 5.1 support) — the pure queue/normalisation model
 * behind the react-native-web `Alert.alert` patch (`lib/webAlert.ts`).
 *
 * Why it exists: react-native-web ships `class Alert { static alert() {} }`
 * — a NO-OP. All 20 `Alert.alert(...)` calls in the app are invisible on
 * web, including "Payment Successful" on the checkout, which runs ONLY on
 * web. The installer patches the static once at app start; this file holds
 * everything worth unit-testing (normalisation + FIFO queue), with no DOM
 * and no react-native imports so it runs on plain Node.
 *
 *   tsc statwise/lib/webAlertCore.ts statwise/lib/webAlertCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/webAlertCore.test.js
 */

export type WebAlertButton = {
  text: string;
  onPress?: () => void;
  style?: 'default' | 'cancel' | 'destructive';
};

/** What Alert.alert receives (loose mirrors of RN's signature). */
export type AlertSpec = {
  title?: string | null;
  message?: string | null;
  buttons?: Array<{
    text?: string;
    onPress?: () => void;
    style?: string;
  }> | null;
};

export type NormalizedAlert = {
  title: string;
  message: string;
  buttons: WebAlertButton[];
};

export type AlertQueue = {
  entries: Array<NormalizedAlert & { id: number }>;
  seq: number;
};

/** RN-style alerts render at most three buttons (iOS rule); keep layout sane. */
export const MAX_ALERT_BUTTONS = 3;

export function createAlertQueue(): AlertQueue {
  return { entries: [], seq: 0 };
}

export function normalizeAlert(spec: AlertSpec): NormalizedAlert {
  const raw = (spec.buttons ?? []).slice(0, MAX_ALERT_BUTTONS);
  const buttons: WebAlertButton[] = (raw.length > 0 ? raw : [{ text: 'OK' }]).map((b) => ({
    text: b.text ?? 'OK',
    ...(b.onPress ? { onPress: b.onPress } : {}),
    ...(b.style ? { style: b.style as WebAlertButton['style'] } : {}),
  }));
  return {
    title: spec.title ?? '',
    message: spec.message ?? '',
    buttons,
  };
}

export function enqueueAlert(q: AlertQueue, spec: AlertSpec): AlertQueue {
  const seq = q.seq + 1;
  return { entries: [...q.entries, { ...normalizeAlert(spec), id: seq }], seq };
}

export function currentAlert(q: AlertQueue): (NormalizedAlert & { id: number }) | null {
  return q.entries.length > 0 ? q.entries[0] : null;
}

export function dismissAlert(q: AlertQueue): AlertQueue {
  return { entries: q.entries.slice(1), seq: q.seq };
}

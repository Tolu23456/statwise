/**
 * Web push registration (plan step 3.2).
 *
 * The app ships as Expo static web, so push uses the browser Push API
 * directly — no expo-notifications package needed (it isn't installed and
 * installing it would bloat the web bundle for a native-only feature).
 *
 * Flow:
 *   1. registerForPush(supabase, userId) — permission → service worker →
 *      pushManager.subscribe with the VAPID public key stored in
 *      app_settings.webpush_vapid_public_key → upsert the subscription
 *      into push_subscriptions (RLS: own rows only).
 *   2. ai/notify.py reads push_subscriptions (service role), respects
 *      user_profiles.notifications, sends via the Web Push protocol.
 *
 * Pure helpers (urlBase64ToUint8Array, buildSubscriptionRow, …) are
 * unit-tested in push.test.ts with plain Node.
 */

export type PushPermission = 'granted' | 'denied' | 'default' | 'unsupported';

export type PushSubscriptionJSON = {
  endpoint: string;
  keys: { p256dh: string; auth: string };
};

/** Feature-detect: web push needs service worker + PushManager. */
export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'Notification' in window &&
    'serviceWorker' in navigator &&
    'PushManager' in window
  );
}

/** Current permission without prompting. */
export function currentPermission(): PushPermission {
  if (!pushSupported()) return 'unsupported';
  return Notification.permission as PushPermission;
}

/** base64url → Uint8Array (the VAPID key format the Push API wants). */
export function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = typeof atob === 'function'
    ? atob(base64)
    : Buffer.from(base64, 'base64').toString('binary');
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
  return output;
}

/** The row shape the push_subscriptions table expects. */
export function buildSubscriptionRow(
  userId: string,
  sub: PushSubscriptionJSON,
  userAgent: string | null,
): {
  user_id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
  user_agent: string | null;
} {
  return {
    user_id: userId,
    endpoint: sub.endpoint,
    p256dh: sub.keys.p256dh,
    auth: sub.keys.auth,
    user_agent: userAgent,
  };
}

/** Human-readable reason when push can't be enabled (shown in the UI). */
export function pushBlockReason(permission: PushPermission): string | null {
  if (permission === 'unsupported') {
    return 'This browser does not support push notifications. Try Chrome, Edge or Safari 16.4+.';
  }
  if (permission === 'denied') {
    return 'Notifications are blocked for this site — allow them in your browser settings, then try again.';
  }
  return null;
}

/**
 * Full registration: permission prompt → service worker → subscription →
 * upsert into Supabase. Returns the endpoint on success, or a reason
 * string on failure (never throws — the UI shows the reason verbatim).
 */
export async function registerForPush(
  supabase: {
    from: (t: string) => any;
  },
  userId: string,
): Promise<{ ok: true; endpoint: string } | { ok: false; reason: string }> {
  if (!pushSupported()) {
    return { ok: false, reason: pushBlockReason('unsupported')! };
  }

  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    return { ok: false, reason: pushBlockReason(permission as PushPermission)
      ?? 'Notification permission was not granted.' };
  }

  // VAPID public key from app_settings (written by the backend, plan 3.2).
  const { data: settingRows, error: settingErr } = await supabase
    .from('app_settings')
    .select('setting_value')
    .eq('setting_key', 'webpush_vapid_public_key')
    .eq('is_active', true)
    .limit(1);
  const rawKey = settingRows?.[0]?.setting_value;
  const vapidPublicKey = typeof rawKey === 'string' ? rawKey : String(rawKey ?? '');
  if (settingErr || !vapidPublicKey) {
    return {
      ok: false,
      reason: 'Push is not configured on the server yet (missing VAPID key).',
    };
  }

  try {
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      // Cast: lib.dom wants Uint8Array<ArrayBuffer>; our helper returns a plain
      // Uint8Array (never a SharedArrayBuffer view).
      applicationServerKey: urlBase64ToUint8Array(vapidPublicKey) as BufferSource,
    });
    const json = sub.toJSON() as PushSubscriptionJSON;
    const row = buildSubscriptionRow(userId, json, navigator.userAgent);

    // Upsert on the unique endpoint: re-subscribing refreshes the keys.
    const { error } = await supabase
      .from('push_subscriptions')
      .upsert(row, { onConflict: 'endpoint' });
    if (error) {
      // Most likely cause: the migration hasn't been run yet.
      await sub.unsubscribe().catch(() => {});
      return {
        ok: false,
        reason: `Could not save this device (${error.message}). If this is a fresh setup, run statwise/supabase-notifications-migration.sql.`,
      };
    }
    return { ok: true, endpoint: json.endpoint };
  } catch (e: any) {
    return {
      ok: false,
      reason: e?.message || 'Push subscription failed — please try again.',
    };
  }
}

/** Remove this browser's subscription (profile toggle off / sign-out). */
export async function unregisterPush(
  supabase: { from: (t: string) => any },
): Promise<void> {
  if (!pushSupported()) return;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    if (!sub) return;
    const endpoint = sub.toJSON().endpoint;
    await sub.unsubscribe();
    await supabase.from('push_subscriptions').delete().eq('endpoint', endpoint);
  } catch {
    // best effort — a stale row is harmless (sender drops 404/410)
  }
}

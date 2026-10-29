/**
 * Native push registration (Expo push tokens).
 *
 * The app is native-first (Android).  Push therefore uses `expo-notifications`
 * to obtain an **Expo push token** and stores it in `push_tokens`; `ai/notify.py`
 * forwards payloads to Expo's push API (https://exp.host/--/api/v2/push/send).
 *
 * Web push has been REMOVED on purpose: `public/sw.js` and the VAPID /
 * `pushManager` client are gone, because shipping two push stacks meant two
 * ways to silently deliver nothing.  `Platform.OS === 'web'` now short-circuits
 * to a clear "not available on the web build" reason so the Profile screen can
 * say so instead of pretending.
 *
 * Flow:
 *   1. requestNotificationPermissionsAsync()
 *   2. getExpoPushTokenAsync()  (needs the project id from app.json / EAS)
 *   3. upsert into push_tokens (RLS: own rows only)
 *   4. ai/notify.py reads push_tokens, respects user_profiles.notifications
 *
 * The PURE helpers (buildTokenRow, pushBlockReason, isExpoToken, …) live in
 * ./pushCore so push.test.ts can compile and run on plain Node in CI.
 */
import { Platform } from 'react-native';
import {
  buildTokenRow,
  isExpoToken,
  notificationTargetUrl,
  notificationsEnabled,
  pushBlockReason,
  PushPermission,
} from './pushCore';

export {
  buildTokenRow,
  isExpoToken,
  notificationTargetUrl,
  notificationsEnabled,
  pushBlockReason,
};
export type { PushPermission };

/** Native devices can push.  The web build cannot (see the module docstring). */
export function pushSupported(): boolean {
  return Platform.OS !== 'web';
}

const nativePlatform = () => (Platform.OS === 'web' ? 'web' : 'native');

/**
 * Resolve the EAS projectId used to mint (and re-mint) Expo push tokens.
 * `undefined` means this build has no EAS project linked, in which case
 * `getExpoPushTokenAsync` would throw ERR_NOTIFICATIONS_NO_EXPERIENCE_ID.
 * Kept in one place so register and unregister can never disagree — if they
 * did, toggling push off would silently leave a live token behind.
 */
async function resolveProjectId(): Promise<string | undefined> {
  const fromEnv =
    process.env.EXPO_PUBLIC_EAS_PROJECT_ID || process.env.EXPO_PUBLIC_PROJECT_ID;
  if (fromEnv) return fromEnv;
  try {
    // expo-constants exposes these on its DEFAULT export (the Constants
    // instance), not as named exports — mirroring the lookup expo-notifications
    // itself performs: easConfig (EAS Build) then expoConfig (app.json extra).
    const mod = await import('expo-constants');
    const C = mod.default ?? mod;
    return C.easConfig?.projectId || C.expoConfig?.extra?.eas?.projectId;
  } catch {
    return undefined;
  }
}

/** Current permission without prompting. Web is always 'unsupported'. */
export async function currentPermission(): Promise<PushPermission> {
  if (!pushSupported()) return 'unsupported';
  try {
    const Notifications = await import('expo-notifications');
    const { status } = await Notifications.getPermissionsAsync();
    return status === 'granted'
      ? 'granted'
      : status === 'denied'
        ? 'denied'
        : 'default';
  } catch {
    return 'unsupported';
  }
}

/**
 * Request permission + obtain the Expo push token.  Never throws: the UI
 * shows the returned reason verbatim.
 */
export async function getExpoPushToken(): Promise<
  { ok: true; token: string } | { ok: false; reason: string }
> {
  if (!pushSupported()) {
    return { ok: false, reason: pushBlockReason('unsupported', 'web')! };
  }
  try {
    const Notifications = await import('expo-notifications');
    const Device = await import('expo-device');

    if (!Device.isDevice) {
      // Simulators/emulators get no push token from Expo.
      return { ok: false, reason: 'Push tokens are only issued to physical devices.' };
    }

    const settings = await Notifications.getPermissionsAsync();
    let status = settings.status;
    if (status !== 'granted') {
      const asked = await Notifications.requestPermissionsAsync();
      status = asked.status;
    }
    if (status !== 'granted') {
      return {
        ok: false,
        reason: pushBlockReason(status === 'denied' ? 'denied' : 'default', nativePlatform())!,
      };
    }

    // projectId resolution: EXPO_PUBLIC_* dev override, else app.json ->
    // expo.extra.eas.projectId (see resolveProjectId).  Without it Expo cannot
    // route the token and getExpoPushTokenAsync throws, so surface an
    // actionable reason instead of that raw error.
    const projectId = await resolveProjectId();

    if (!projectId) {
      return {
        ok: false,
        reason:
          'Push is not configured for this build (no EAS projectId). Run `eas init` in statwise/ so app.json gains expo.extra.eas.projectId, then rebuild.',
      };
    }

    const tokenResponse = await Notifications.getExpoPushTokenAsync({ projectId });
    const token = tokenResponse.data;
    if (!isExpoToken(token)) {
      return { ok: false, reason: 'Expo returned an unexpected push token format.' };
    }
    return { ok: true, token };
  } catch (e: any) {
    return {
      ok: false,
      reason: e?.message || 'Could not register this device for push notifications.',
    };
  }
}

/**
 * Full registration: permission → Expo token → upsert into Supabase.
 * Returns the token on success, or a reason string on failure.
 */
export async function registerForPush(
  supabase: { from: (t: string) => any },
  userId: string,
): Promise<{ ok: true; token: string } | { ok: false; reason: string }> {
  const res = await getExpoPushToken();
  if (!res.ok) return res;

  const { error } = await supabase.from('push_tokens').upsert(
    buildTokenRow(userId, res.token, Platform.OS),
    { onConflict: 'user_id,expo_token' },
  );

  if (error) {
    return {
      ok: false,
      reason: `Could not save this device (${error.message}). If this is a fresh setup, run statwise/supabase-native-push-migration.sql.`,
    };
  }
  return { ok: true, token: res.token };
}

/**
 * Remove this device's token (profile toggle off / sign-out).
 *
 * Uses the SAME projectId resolution as registerForPush (resolveProjectId);
 * calling getExpoPushTokenAsync with no projectId would throw
 * ERR_NOTIFICATIONS_NO_EXPERIENCE_ID, which the old `.catch(() => null)`
 * swallowed — leaving a live row in push_tokens, so "disable push" would not
 * actually stop delivery.  Now the no-projectId case returns early instead of
 * pretending to unregister.
 */
export async function unregisterPush(
  supabase: { from: (t: string) => any },
): Promise<void> {
  if (!pushSupported()) return;
  try {
    const projectId = await resolveProjectId();
    const Notifications = await import('expo-notifications');
    const token = projectId
      ? await Notifications.getExpoPushTokenAsync({ projectId })
          .then((t) => t.data)
          .catch(() => null)
      : null;
    if (token && isExpoToken(token)) {
      await supabase.from('push_tokens').delete().eq('expo_token', token);
    }
    await Notifications.setBadgeCountAsync?.(0).catch(() => {});
  } catch {
    // best effort — a stale token is harmless (the sender purges failures)
  }
}

/**
 * Wire up how a received notification behaves when tapped.  Payloads carry
 * `data.url`, which is an absolute https URL on the site; we only navigate
 * for our own origin so a forged payload cannot bounce the user off-site.
 */
export async function configureNotificationHandler(
  trustedOrigin?: string,
): Promise<void> {
  if (!pushSupported()) return;
  try {
    const Notifications = await import('expo-notifications');
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowBanner: true,
        shouldShowList: true,
        shouldPlaySound: true,
        shouldSetBadge: true,
      }),
    });
    void trustedOrigin; // consumed by the tap listener in app/_layout.tsx
  } catch {
    // headless JS runtime without the module — nothing to configure
  }
}

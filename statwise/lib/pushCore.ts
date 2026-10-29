/**
 * Pure push helpers — deliberately free of ANY import (no react-native, no
 * expo-*), so `tsc --strict` + plain Node can run push.test.ts in CI without
 * installing dependencies.  lib/push.ts owns the expo-notifications wiring
 * and re-exports everything here.
 */

export type PushPermission = 'granted' | 'denied' | 'default' | 'unsupported';

/**
 * Expo push tokens look like `ExponentPushToken[xxxx]` (legacy) or
 * `ExpoPushToken[xxxx]`.  Anything else is not a token this backend can use.
 */
export function isExpoToken(value: unknown): value is string {
  return typeof value === 'string' && /^(Expo|Exponent)PushToken\[.+\]$/.test(value.trim());
}

/** Human-readable reason when push can't be enabled (shown in the UI). */
export function pushBlockReason(
  permission: PushPermission,
  platform: 'web' | 'native' = 'native',
): string | null {
  if (permission === 'unsupported') {
    return platform === 'web'
      ? 'Push notifications are not available on the web build — install the Android app to get them.'
      : 'Notifications are not available on this device.';
  }
  if (permission === 'denied') {
    return 'Notifications are blocked — enable them for StatWise in your device settings, then try again.';
  }
  return null;
}

/** The row shape the push_tokens table expects. */
export function buildTokenRow(
  userId: string,
  token: string,
  platform: string,
  now: string = new Date().toISOString(),
): { user_id: string; expo_token: string; platform: string; updated_at: string } {
  return {
    user_id: userId,
    expo_token: token.trim(),
    platform,
    updated_at: now,
  };
}

/**
 * Whether a notification should be delivered at all: the sender gates on
 * `user_profiles.notifications`, but the client re-checks so a device that
 * was opted out never even registers.
 */
export function notificationsEnabled(profile: unknown): boolean {
  if (profile && typeof profile === 'object') {
    const p = profile as { notifications?: unknown };
    // Missing key = default ON (matches the signup default).
    return p.notifications !== false;
  }
  return true;
}

/**
 * Deep-link target for a tapped notification.  Only same-origin https URLs
 * are trusted — a forged `data.url` must not bounce the user to a phishing
 * site from the OS notification shade.
 */
export function notificationTargetUrl(
  data: unknown,
  trustedOrigin: string,
): string | null {
  if (!data || typeof data !== 'object') return null;
  const raw = (data as { url?: unknown }).url;
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  // Relative paths stay inside the app's site.
  if (trimmed.startsWith('/')) return trimmed;
  try {
    const url = new URL(trimmed);
    if (url.protocol !== 'https:') return null;
    const origin = new URL(trustedOrigin).origin;
    if (url.origin !== origin) return null;
    return url.toString();
  } catch {
    return null;
  }
}

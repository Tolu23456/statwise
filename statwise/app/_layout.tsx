import 'react-native-url-polyfill/auto';
import { useEffect } from 'react';
import { Stack, useRouter, useSegments } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { Platform } from 'react-native';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { QUERY_DEFAULTS } from '@/lib/queryConfig';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { useFonts } from 'expo-font';
// Per-weight entry points: the package barrel re-exports all 22 Inter TTFs (~7.5 MB),
// these four are the only faces the app renders with.
import { Inter_400Regular } from '@expo-google-fonts/inter/400Regular';
import { Inter_500Medium } from '@expo-google-fonts/inter/500Medium';
import { Inter_600SemiBold } from '@expo-google-fonts/inter/600SemiBold';
import { Inter_700Bold } from '@expo-google-fonts/inter/700Bold';
import * as SplashScreen from 'expo-splash-screen';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { ThemeProvider, useTheme } from '@/context/ThemeContext';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { supabase } from '@/lib/supabase';
import { installWebTelemetry, setEventSink } from '@/lib/telemetry';
import { installWebAlert } from '@/lib/webAlert';
import { startForumFeed } from '@/lib/forumFeed';
import { configureNotificationHandler, notificationTargetUrl } from '@/lib/push';

/**
 * Origin the push payloads point at (ai/notify.py absolutises `data.url`
 * against its SITE_BASE_URL — keep the two in sync via EXPO_PUBLIC_SITE_URL /
 * SITE_BASE_URL).  A payload for any other origin is dropped by
 * notificationTargetUrl and never reaches the router.
 */
const SITE_ORIGIN = process.env.EXPO_PUBLIC_SITE_URL || 'https://statwise-flax.vercel.app';

/**
 * Routes this app can actually open, mapped from the site URLs a payload may
 * carry.  Anything else (unknown path, /insight-detail without params, an
 * off-site link) lands on the home tab instead of a dead screen.
 */
const APP_ROUTES = new Set([
  '/', '/insights', '/forum', '/profile', '/subscriptions', '/backtesting',
]);

function routeForNotification(target: string): string {
  try {
    const url = new URL(target, SITE_ORIGIN);
    if (url.origin !== new URL(SITE_ORIGIN).origin) return '/'; // belt + braces
    const path = url.pathname.replace(/\/+$/, '') || '/';
    return APP_ROUTES.has(path) ? path + url.search : '/';
  } catch {
    return '/';
  }
}

SplashScreen.preventAutoHideAsync();

// Plan 5.1 — react-native-web ships `Alert.alert` as an empty static method,
// so every alert in the app (including payment results on the web-only
// checkout) is invisible there. Patch it once, before any screen renders;
// native keeps the platform implementation untouched.
installWebAlert();

const queryClient = new QueryClient({ defaultOptions: QUERY_DEFAULTS });

// Plan 4.4 — telemetry transport. Events become `client_events` rows that
// only the service role reads (metrics-daily.yml). Until the telemetry
// migration is run the insert fails — captured and swallowed here, which is
// exactly the no-op the module guarantees; a telemetry problem must never
// surface in the UI or spawn rejection loops.
setEventSink(async events => {
  try {
    const { error } = await supabase.from('client_events').insert(events);
    if (error) console.debug('telemetry upload skipped:', error.message);
  } catch {
    /* never throw from the sink */
  }
});

function RootLayoutNav() {
  const { session, loading } = useAuth();
  const segments = useSegments();
  const router = useRouter();
  const { scheme } = useTheme();

  useEffect(() => {
    if (loading) return;
    const inAuthGroup = segments[0] === '(auth)';
    if (!session && !inAuthGroup) {
      router.replace('/(auth)/login');
    } else if (session && inAuthGroup) {
      router.replace('/(tabs)');
    }
  }, [session, loading, segments]);

  useEffect(() => {
    // Native push wiring: foreground presentation (the handler decides the
    // banner/sound) and tap-to-navigate (payload `data.url`, validated
    // same-origin and mapped onto a route that exists — a forged payload can
    // neither leave the site nor land on a dead screen).  Web push was
    // removed with the PWA → Android conversion, so nothing runs here on web
    // (the old auto permission-prompt effect went with it: it asked for a
    // permission no code used anymore).
    if (Platform.OS === 'web') return;
    let cancelled = false;
    let responseSub: { remove: () => void } | undefined;
    (async () => {
      await configureNotificationHandler();
      try {
        const Notifications = await import('expo-notifications');
        responseSub = Notifications.addNotificationResponseReceivedListener((response) => {
          if (cancelled) return;
          const data = response.notification.request.content.data;
          const target = notificationTargetUrl(data, SITE_ORIGIN);
          if (!target) return; // off-origin or malformed — ignore the tap
          router.replace(routeForNotification(target) as never);
        });
      } catch (e) {
        console.warn('[push] tap listener not installed:', e);
      }
    })();
    return () => {
      cancelled = true;
      responseSub?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Plan 4.4 — global capture (uncaught errors, unhandled rejections,
    // React minified invariants such as the #418 hydration mismatch, and
    // slow Supabase calls). Web only; cleans up on hot reload.
    if (Platform.OS !== 'web') return;
    return installWebTelemetry();
  }, []);

  useEffect(() => {
    // Forum feed runs app-wide (user request 2026-10-27): hydrate the local
    // cache, fetch fresh rows and keep them warm in the BACKGROUND — the
    // forum screen never waits on a network round-trip to open, and updates
    // land while the user is on any other tab.
    return startForumFeed();
  }, []);

  return (
    <>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(auth)" />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="insight-detail" options={{ presentation: 'card', animation: 'slide_from_right' }} />
        <Stack.Screen name="privacy-policy" options={{ presentation: 'modal' }} />
        <Stack.Screen name="terms-of-service" options={{ presentation: 'modal' }} />
        <Stack.Screen name="backtesting" options={{ presentation: 'card', animation: 'slide_from_right' }} />
      </Stack>
      <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
    </>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    Inter_700Bold,
  });

  useEffect(() => {
    if (fontsLoaded || fontError) {
      SplashScreen.hideAsync();
    }
  }, [fontsLoaded, fontError]);

  if (!fontsLoaded && !fontError) return null;

  return (
    <SafeAreaProvider>
      <ErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <ThemeProvider>
            <AuthProvider>
              <RootLayoutNav />
            </AuthProvider>
          </ThemeProvider>
        </QueryClientProvider>
      </ErrorBoundary>
    </SafeAreaProvider>
  );
}

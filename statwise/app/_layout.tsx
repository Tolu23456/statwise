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

SplashScreen.preventAutoHideAsync();

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
    if (Platform.OS === 'web' && 'Notification' in window) {
      if (Notification.permission === 'default') {
        Notification.requestPermission();
      }
    }
  }, []);

  useEffect(() => {
    // Plan 4.4 — global capture (uncaught errors, unhandled rejections,
    // React minified invariants such as the #418 hydration mismatch, and
    // slow Supabase calls). Web only; cleans up on hot reload.
    if (Platform.OS !== 'web') return;
    return installWebTelemetry();
  }, []);

  return (
    <>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(auth)" />
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="insight-detail" options={{ presentation: 'card' }} />
        <Stack.Screen name="privacy-policy" options={{ presentation: 'modal' }} />
        <Stack.Screen name="terms-of-service" options={{ presentation: 'modal' }} />
        <Stack.Screen name="backtesting" options={{ presentation: 'card' }} />
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

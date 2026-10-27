/**
 * Onboarding gate (plan step 4.2 — lazy modals).
 *
 * The AsyncStorage check used to live in OnboardingModal.tsx, which forced
 * the home screen to import that module eagerly (killing any lazy-load
 * win). The helper is now its own tiny module: the home screen imports
 * *this* for the check, and only pulls the modal itself — slides, styles,
 * icons — the first time it actually opens.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export const ONBOARDING_KEY = 'statwise_onboarding_v1';

/** True until the user has completed/skipped the onboarding slides. */
export async function shouldShowOnboarding(): Promise<boolean> {
  try {
    const val = await AsyncStorage.getItem(ONBOARDING_KEY);
    return val !== 'true';
  } catch {
    return false;
  }
}

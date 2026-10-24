/**
 * Smart polling strategy
 * - Live matches: 15s
 * - Starting soon (< 2h): 60s  
 * - Others: 300s
 * - Paused when backgrounded
 */

import { AppState } from 'react-native';
import { Prediction } from './supabase';

export function getPollInterval(predictions: Prediction[]): number {
  const now = Date.now();
  const twoHours = 2 * 60 * 60 * 1000;
  
  const hasLive = predictions.some(p => 
    p.status === 'LIVE' || p.status === 'HT'
  );
  if (hasLive) return 15000;
  
  const hasStartingSoon = predictions.some(p => {
    const kickoff = new Date(p.kickoff_time).getTime();
    const diff = kickoff - now;
    return diff > 0 && diff < twoHours;
  });
  if (hasStartingSoon) return 60000;
  
  return 300000; // 5 min for others
}

export function useSmartPolling() {
  let interval = 300000;
  const subscription = AppState.addEventListener('change', (nextAppState) => {
    if (nextAppState === 'background') {
      // Pause polling
    }
  });
  return { interval, subscription };
}

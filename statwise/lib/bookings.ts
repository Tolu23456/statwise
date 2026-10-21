import { Prediction } from './supabase';

export type Bookmaker = {
  id: 'sportybet_ng';
  name: string;
  country: string;
  domain: string;
  enabled: boolean;
};

export const BOOKMAKERS: Bookmaker[] = [
  {
    id: 'sportybet_ng',
    name: 'SportyBet Nigeria',
    country: 'NG',
    domain: 'https://www.sportybet.com',
    enabled: true,
  },
];

export function getPredictionSelection(prediction: Prediction): 'home' | 'draw' | 'away' {
  const pred = (prediction.prediction || '').toLowerCase();
  if (pred.includes('home') || pred === 'h' || pred === '1') return 'home';
  if (pred.includes('draw') || pred === 'd' || pred === 'x') return 'draw';
  if (pred.includes('away') || pred === 'a' || pred === '2') return 'away';
  // Fallback based on team names in match_title
  if (prediction.match_title?.toLowerCase().startsWith(prediction.home_team?.toLowerCase())) {
    return 'home';
  }
  if (prediction.match_title?.toLowerCase().startsWith(prediction.away_team?.toLowerCase())) {
    return 'away';
  }
  return 'home';
}

export function formatSelectionLabel(prediction: Prediction): string {
  const sel = getPredictionSelection(prediction);
  if (sel === 'home') return prediction.home_team;
  if (sel === 'away') return prediction.away_team;
  return 'Draw';
}

// Team logo mapping with fallbacks
export const TEAM_LOGOS: Record<string, string> = {
  "FC Barcelona": "https://images.fotmob.com/image_resources/logo/teamlogo/8634.png",
  "Barcelona": "https://images.fotmob.com/image_resources/logo/teamlogo/8634.png",
  "Real Madrid CF": "https://images.fotmob.com/image_resources/logo/teamlogo/8633.png",
  "Real Madrid": "https://images.fotmob.com/image_resources/logo/teamlogo/8633.png",
  "Manchester United FC": "https://images.fotmob.com/image_resources/logo/teamlogo/10260.png",
  "Manchester United": "https://images.fotmob.com/image_resources/logo/teamlogo/10260.png",
  "Manchester City FC": "https://images.fotmob.com/image_resources/logo/teamlogo/8456.png",
  "Manchester City": "https://images.fotmob.com/image_resources/logo/teamlogo/8456.png",
  "Liverpool FC": "https://images.fotmob.com/image_resources/logo/teamlogo/8650.png",
  "Liverpool": "https://images.fotmob.com/image_resources/logo/teamlogo/8650.png",
  "Arsenal FC": "https://images.fotmob.com/image_resources/logo/teamlogo/9825.png",
  "Arsenal": "https://images.fotmob.com/image_resources/logo/teamlogo/9825.png",
  "Chelsea FC": "https://images.fotmob.com/image_resources/logo/teamlogo/8455.png",
  "Chelsea": "https://images.fotmob.com/image_resources/logo/teamlogo/8455.png",
  "Tottenham Hotspur FC": "https://images.fotmob.com/image_resources/logo/teamlogo/8586.png",
  "Tottenham Hotspur": "https://images.fotmob.com/image_resources/logo/teamlogo/8586.png",
  "Como 1907": "https://images.fotmob.com/image_resources/logo/teamlogo/9888.png",
  "Como": "https://images.fotmob.com/image_resources/logo/teamlogo/9888.png",
  "US Sassuolo Calcio": "https://images.fotmob.com/image_resources/logo/teamlogo/6488.png",
  "Sassuolo": "https://images.fotmob.com/image_resources/logo/teamlogo/6488.png",
  "AC Milan": "https://images.fotmob.com/image_resources/logo/teamlogo/8564.png",
  "Milan": "https://images.fotmob.com/image_resources/logo/teamlogo/8564.png",
  "Inter Milan": "https://images.fotmob.com/image_resources/logo/teamlogo/8636.png",
  "Inter": "https://images.fotmob.com/image_resources/logo/teamlogo/8636.png",
  "Juventus FC": "https://images.fotmob.com/image_resources/logo/teamlogo/9885.png",
  "Juventus": "https://images.fotmob.com/image_resources/logo/teamlogo/9885.png",
  "AS Roma": "https://images.fotmob.com/image_resources/logo/teamlogo/8686.png",
  "Roma": "https://images.fotmob.com/image_resources/logo/teamlogo/8686.png",
  "Napoli": "https://images.fotmob.com/image_resources/logo/teamlogo/9875.png",
  "SSC Napoli": "https://images.fotmob.com/image_resources/logo/teamlogo/9875.png",
  "AFC Bournemouth": "https://images.fotmob.com/image_resources/logo/teamlogo/8678.png",
  "Bournemouth": "https://images.fotmob.com/image_resources/logo/teamlogo/8678.png",
  "Atalanta BC": "https://images.fotmob.com/image_resources/logo/teamlogo/8524.png",
  "Atalanta": "https://images.fotmob.com/image_resources/logo/teamlogo/8524.png",
  "AFC Ajax": "https://images.fotmob.com/image_resources/logo/teamlogo/8593.png",
  "Ajax Amsterdam": "https://images.fotmob.com/image_resources/logo/teamlogo/8593.png",
  "Ajax": "https://images.fotmob.com/image_resources/logo/teamlogo/8593.png",
  "SC Freiburg": "https://images.fotmob.com/image_resources/logo/teamlogo/8358.png",
  "Freiburg": "https://images.fotmob.com/image_resources/logo/teamlogo/8358.png",
  "AS Monaco": "https://images.fotmob.com/image_resources/logo/teamlogo/9829.png",
  "Monaco": "https://images.fotmob.com/image_resources/logo/teamlogo/9829.png",
  "Olympique Lyonnais": "https://images.fotmob.com/image_resources/logo/teamlogo/9748.png",
  "Lyon": "https://images.fotmob.com/image_resources/logo/teamlogo/9748.png",
  "TSG 1899 Hoffenheim": "https://images.fotmob.com/image_resources/logo/teamlogo/10189.png",
  "TSG Hoffenheim": "https://images.fotmob.com/image_resources/logo/teamlogo/10189.png",
  "Hoffenheim": "https://images.fotmob.com/image_resources/logo/teamlogo/10189.png",
  "SL Benfica": "https://images.fotmob.com/image_resources/logo/teamlogo/9781.png",
  "Benfica": "https://images.fotmob.com/image_resources/logo/teamlogo/9781.png",
  "Real Sociedad": "https://images.fotmob.com/image_resources/logo/teamlogo/8560.png",
  "FC Twente": "https://images.fotmob.com/image_resources/logo/teamlogo/8611.png",
  "Twente": "https://images.fotmob.com/image_resources/logo/teamlogo/8611.png",
  "Bayern Munich": "https://images.fotmob.com/image_resources/logo/teamlogo/9823.png",
  "FC Bayern München": "https://images.fotmob.com/image_resources/logo/teamlogo/9823.png",
  "Borussia Dortmund": "https://images.fotmob.com/image_resources/logo/teamlogo/9789.png",
  "Dortmund": "https://images.fotmob.com/image_resources/logo/teamlogo/9789.png",
  "Paris Saint-Germain": "https://images.fotmob.com/image_resources/logo/teamlogo/9847.png",
  "PSG": "https://images.fotmob.com/image_resources/logo/teamlogo/9847.png",
};

export function getTeamLogo(team: string): string | null {
  if (!team) return null;
  if (TEAM_LOGOS[team]) return TEAM_LOGOS[team];
  // Try exact case-insensitive match
  const lower = team.toLowerCase();
  const exact = Object.keys(TEAM_LOGOS).find(k => k.toLowerCase() === lower);
  if (exact) return TEAM_LOGOS[exact];
  // Try partial match
  const key = Object.keys(TEAM_LOGOS).find(k => k.toLowerCase().includes(lower) || lower.includes(k.toLowerCase()));
  return key ? TEAM_LOGOS[key] : null;
}

export function getMatchStatus(pred: { kickoff_time: string; status?: string; actual_result?: string | null; home_score?: number | null; away_score?: number | null }) {
  const now = Date.now();
  const kickoff = new Date(pred.kickoff_time).getTime();
  const live = now >= kickoff && now <= kickoff + 3*60*60*1000;
  const ht = false; // would need more data
  const finished = pred.status === 'completed' || pred.actual_result != null || (now > kickoff + 3*60*60*1000 && pred.status === 'completed');
  if (finished) return { state: 'FT', live: false };
  if (live) return { state: 'LIVE', live: true };
  const diff = kickoff - now;
  if (diff < 0) return { state: 'LIVE', live: true };
  return { state: 'SCHEDULED', live: false };
}

// Simple team logo mapping - can be extended
export const TEAM_LOGOS: Record<string, string> = {
  "FC Barcelona": "https://images.fotmob.com/image_resources/logo/teamlogo/8634.png",
  "Real Madrid CF": "https://images.fotmob.com/image_resources/logo/teamlogo/8633.png",
  "Manchester United FC": "https://images.fotmob.com/image_resources/logo/teamlogo/10260.png",
  "Como 1907": "https://images.fotmob.com/image_resources/logo/teamlogo/9888.png",
  "US Sassuolo Calcio": "https://images.fotmob.com/image_resources/logo/teamlogo/6488.png",
  "AC Milan": "https://images.fotmob.com/image_resources/logo/teamlogo/8564.png",
  "AFC Bournemouth": "https://images.fotmob.com/image_resources/logo/teamlogo/8678.png",
  "Atalanta": "https://images.fotmob.com/image_resources/logo/teamlogo/8524.png",
  "Ajax Amsterdam": "https://images.fotmob.com/image_resources/logo/teamlogo/8593.png",
  "SC Freiburg": "https://images.fotmob.com/image_resources/logo/teamlogo/8358.png",
  "AS Monaco": "https://images.fotmob.com/image_resources/logo/teamlogo/9829.png",
  "Lyon": "https://images.fotmob.com/image_resources/logo/teamlogo/9748.png",
  "TSG Hoffenheim": "https://images.fotmob.com/image_resources/logo/teamlogo/10189.png",
  "Benfica": "https://images.fotmob.com/image_resources/logo/teamlogo/9781.png",
  "Real Sociedad": "https://images.fotmob.com/image_resources/logo/teamlogo/8560.png",
  "FC Twente": "https://images.fotmob.com/image_resources/logo/teamlogo/8611.png",
};

export function getTeamLogo(team: string): string | null {
  if (!team) return null;
  if (TEAM_LOGOS[team]) return TEAM_LOGOS[team];
  // Try partial match
  const key = Object.keys(TEAM_LOGOS).find(k => k.toLowerCase().includes(team.toLowerCase()) || team.toLowerCase().includes(k.toLowerCase()));
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

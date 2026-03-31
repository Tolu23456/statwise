const { createClient } = require('@supabase/supabase-js');

const FOOTBALL_DATA_TOKEN = process.env.FOOTBALL_DATA_TOKEN;
const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const missing = [];
if (!FOOTBALL_DATA_TOKEN) missing.push('FOOTBALL_DATA_TOKEN');
if (!SUPABASE_URL) missing.push('SUPABASE_URL');
if (!SUPABASE_SERVICE_ROLE_KEY) missing.push('SUPABASE_SERVICE_ROLE_KEY');

if (missing.length > 0) {
  console.error(`Missing required GitHub secrets: ${missing.join(', ')}`);
  console.error('Make sure these are set under: GitHub repo → Settings → Secrets and variables → Actions → Repository secrets (NOT Environment secrets)');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false },
});

const COMPETITIONS = [
  { code: 'PL',  name: 'Premier League',    slug: 'premier-league',    tier: 'Free Tier' },
  { code: 'PD',  name: 'La Liga',           slug: 'la-liga',           tier: 'Premium Tier' },
  { code: 'BL1', name: 'Bundesliga',        slug: 'bundesliga',        tier: 'Premium Tier' },
  { code: 'SA',  name: 'Serie A',           slug: 'serie-a',           tier: 'Premium Tier' },
  { code: 'FL1', name: 'Ligue 1',           slug: 'ligue1',            tier: 'VIP Tier' },
  { code: 'CL',  name: 'Champions League',  slug: 'champions-league',  tier: 'VIP Tier' },
  { code: 'MLS', name: 'MLS',               slug: 'mls',               tier: 'Free Tier' },
  { code: 'ELC', name: 'Championship',      slug: 'championship',      tier: 'Premium Tier' },
  { code: 'DED', name: 'Eredivisie',        slug: 'eredivisie',        tier: 'VIP Tier' },
  { code: 'PPL', name: 'Primeira Liga',     slug: 'primeira-liga',     tier: 'VIP Tier' },
];

const PREDICTIONS_POOL = ['Home Win', 'Away Win', 'Draw', 'Over 2.5 Goals', 'Both Teams to Score', 'Home Win or Draw', 'Away Win or Draw'];
const REASONING_TEMPLATES = [
  '{home} have been strong at home this season, making a home win the most likely outcome.',
  '{away} are in excellent form away from home and are slight favourites here.',
  'Both sides are evenly matched; a draw is the most probable result.',
  'Recent matches between these sides have produced plenty of goals — expect over 2.5 goals.',
  'Both attack-minded squads have been scoring freely, expect both teams to find the net.',
  '{home} are unbeaten at home this season, and the double chance offers good value.',
  '{away} have lost just once on the road recently; the double chance looks strong.',
];

function seededRandom(seed) {
  let s = seed;
  return function () {
    s = (s * 16807 + 0) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

function generatePrediction(matchId, homeTeam, awayTeam) {
  const seed = [...matchId].reduce((acc, c) => acc + c.charCodeAt(0), 0);
  const rand = seededRandom(seed);

  const r = rand();
  let prediction;
  if (r < 0.38) prediction = 'Home Win';
  else if (r < 0.62) prediction = 'Away Win';
  else if (r < 0.74) prediction = 'Draw';
  else if (r < 0.84) prediction = 'Over 2.5 Goals';
  else if (r < 0.93) prediction = 'Both Teams to Score';
  else prediction = PREDICTIONS_POOL[Math.floor(rand() * PREDICTIONS_POOL.length)];

  const confidence = Math.floor(55 + rand() * 27);

  const oddsMap = {
    'Home Win': 1.5 + rand() * 1.5,
    'Away Win': 1.8 + rand() * 2.0,
    'Draw': 2.8 + rand() * 1.2,
    'Over 2.5 Goals': 1.6 + rand() * 0.8,
    'Both Teams to Score': 1.7 + rand() * 0.9,
    'Home Win or Draw': 1.3 + rand() * 0.7,
    'Away Win or Draw': 1.4 + rand() * 0.8,
  };
  const odds = parseFloat((oddsMap[prediction] ?? 1.8 + rand()).toFixed(2));

  const reasoningIdx = Math.floor(rand() * REASONING_TEMPLATES.length);
  const reasoning = REASONING_TEMPLATES[reasoningIdx]
    .replace('{home}', homeTeam)
    .replace('{away}', awayTeam);

  return { prediction, confidence, odds, reasoning };
}

function formatKickoffTime(utcDate) {
  const d = new Date(utcDate);
  const hours = d.getUTCHours().toString().padStart(2, '0');
  const mins = d.getUTCMinutes().toString().padStart(2, '0');
  return `${hours}:${mins} UTC`;
}

function getMatchDate(utcDate) {
  return utcDate.split('T')[0];
}

async function fetchMatches(dateFrom, dateTo) {
  const url = `https://api.football-data.org/v4/matches?dateFrom=${dateFrom}&dateTo=${dateTo}`;
  const response = await fetch(url, {
    headers: { 'X-Auth-Token': FOOTBALL_DATA_TOKEN },
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`football-data.org API error ${response.status}: ${text}`);
  }

  const data = await response.json();
  return data.matches ?? [];
}

async function run() {
  const today = new Date();
  const weekAhead = new Date(today);
  weekAhead.setDate(today.getDate() + 7);

  const dateFrom = today.toISOString().split('T')[0];
  const dateTo = weekAhead.toISOString().split('T')[0];

  console.log(`Fetching matches from ${dateFrom} to ${dateTo}...`);

  let matches;
  try {
    matches = await fetchMatches(dateFrom, dateTo);
  } catch (err) {
    console.error('Failed to fetch matches:', err.message);
    process.exit(1);
  }

  console.log(`Fetched ${matches.length} scheduled matches.`);

  const competitionMap = Object.fromEntries(COMPETITIONS.map(c => [c.code, c]));

  const rows = [];

  const SKIP_STATUSES = ['FINISHED', 'CANCELLED', 'POSTPONED', 'AWARDED', 'SUSPENDED'];

  for (const match of matches) {
    if (SKIP_STATUSES.includes(match.status)) continue;

    const compCode = match.competition?.code;
    const comp = competitionMap[compCode];
    if (!comp) continue;

    const matchId = String(match.id);
    const homeTeam = match.homeTeam?.name ?? 'Home Team';
    const awayTeam = match.awayTeam?.name ?? 'Away Team';
    const utcDate = match.utcDate;

    if (!homeTeam || !awayTeam || !utcDate) continue;

    const { prediction, confidence, odds, reasoning } = generatePrediction(matchId, homeTeam, awayTeam);

    rows.push({
      match_id: matchId,
      match_title: `${homeTeam} vs ${awayTeam}`,
      home_team: homeTeam,
      away_team: awayTeam,
      league: comp.name,
      league_slug: comp.slug,
      prediction,
      confidence,
      odds,
      reasoning,
      kickoff_time: utcDate,
      match_date: getMatchDate(utcDate),
      tier_required: comp.tier,
      status: 'upcoming',
      updated_at: new Date().toISOString(),
    });
  }

  if (rows.length === 0) {
    console.log('No matches found for supported competitions. Nothing to insert.');
    return;
  }

  console.log(`Prepared ${rows.length} prediction rows. Upserting into Supabase...`);

  const BATCH_SIZE = 50;
  let totalUpserted = 0;

  for (let i = 0; i < rows.length; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);
    const { error } = await supabase
      .from('predictions')
      .upsert(batch, { onConflict: 'match_id', ignoreDuplicates: false });

    if (error) {
      console.error(`Batch ${Math.floor(i / BATCH_SIZE) + 1} upsert error:`, error.message);
    } else {
      totalUpserted += batch.length;
      console.log(`Batch ${Math.floor(i / BATCH_SIZE) + 1}: upserted ${batch.length} rows.`);
    }
  }

  console.log(`Done. Total rows upserted: ${totalUpserted}`);
}

run().catch(err => {
  console.error('Unexpected error:', err);
  process.exit(1);
});

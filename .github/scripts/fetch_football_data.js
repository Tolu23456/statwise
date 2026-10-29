const { createClient } = require('@supabase/supabase-js');

// football-data.org's free tier allows 10 requests/min and 10k requests/month
// PER TOKEN. Rotating across a pool shares that monthly allowance and keeps a
// single rate-limited key from failing the whole run.
const FDORG_TOKENS = [
  ...new Set(
    [
      ...(process.env.FOOTBALL_DATA_TOKENS || '')
        .split(/[,;\s]+/)
        .map((token) => token.trim())
        .filter(Boolean),
      process.env.FOOTBALL_DATA_TOKEN,
      process.env.FOOTBALL_API_TOKEN,
    ].filter(Boolean),
  ),
];

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const missing = [];
if (FDORG_TOKENS.length === 0) missing.push('FOOTBALL_DATA_TOKENS');
if (!SUPABASE_URL) missing.push('SUPABASE_URL');
if (!SUPABASE_SERVICE_ROLE_KEY) missing.push('SUPABASE_SERVICE_ROLE_KEY');

if (missing.length > 0) {
  console.error(`Missing required GitHub secrets: ${missing.join(', ')}`);
  console.error('Make sure these are set under: GitHub repo → Settings → Secrets and variables → Actions → Repository secrets (NOT Environment secrets)');
  process.exit(1);
}

// ── outbound-fetch hygiene ──────────────────────────────────────────────
// Every network call gets a hard timeout and a response-size cap: CI used
// to hang silently on a stalled socket until the job-level timeout killed
// it (with no useful error).
const FDORG_TIMEOUT_MS = 30_000;
const SUPABASE_TIMEOUT_MS = 30_000;
// A week of fixtures from football-data.org is well under 1 MiB; 10 MiB is
// a generous ceiling that still trips long before memory becomes a problem.
const MAX_RESPONSE_BYTES = 10 * 1024 * 1024;

// Read a response body with a hard size cap (checks the declared
// Content-Length AND the actual bytes, in case the header lies or is absent).
async function readBoundedBody(response) {
  const declared = Number(response.headers.get('content-length') || 0);
  if (declared > MAX_RESPONSE_BYTES) {
    throw new Error(
      `response too large: content-length ${declared} > ${MAX_RESPONSE_BYTES}`,
    );
  }
  const buf = await response.arrayBuffer();
  if (buf.byteLength > MAX_RESPONSE_BYTES) {
    throw new Error(
      `response too large: ${buf.byteLength} bytes > ${MAX_RESPONSE_BYTES}`,
    );
  }
  return buf;
}

async function readBoundedJson(response) {
  return JSON.parse(Buffer.from(await readBoundedBody(response)).toString('utf8'));
}

async function readBoundedText(response) {
  return Buffer.from(await readBoundedBody(response)).toString('utf8');
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false },
  // Every supabase call is an outbound fetch too — give them a hard timeout
  // so a hung socket fails loudly instead of stalling the whole run.
  global: {
    fetch: (url, init = {}) =>
      fetch(url, { ...init, signal: AbortSignal.timeout(SUPABASE_TIMEOUT_MS) }),
  },
});

// The app filters predictions with .in('tier', ['free','premium','vip','vvip']),
// so every row must carry the short db tier code alongside the display label.
const TIER_DB_CODE = {
  'Free Tier': 'free',
  'Premium Tier': 'premium',
  'VIP Tier': 'vip',
  'VVIP Tier': 'vvip',
};

const COMPETITIONS = [
  { code: 'PL',  name: 'Premier League',    slug: 'premier-league',    tier: 'Free Tier' },
  { code: 'PD',  name: 'La Liga',           slug: 'la-liga',           tier: 'Premium Tier' },
  { code: 'BL1', name: 'Bundesliga',        slug: 'bundesliga',        tier: 'Premium Tier' },
  { code: 'SA',  name: 'Serie A',           slug: 'serie-a',           tier: 'Premium Tier' },
  { code: 'FL1', name: 'Ligue 1',           slug: 'ligue1',            tier: 'VIP Tier' },
  { code: 'CL',  name: 'Champions League',  slug: 'champions-league',  tier: 'VIP Tier' },
  // European cup nights. These have no football-data.co.uk season CSV, so the
  // model predicts them from pooled priors — predictor.py flags the resulting
  // rows with stats.reduced_confidence and holds them to a higher AI-Pick bar.
  { code: 'EL',  name: 'Europa League',     slug: 'europa-league',     tier: 'VIP Tier' },
  { code: 'ECL', name: 'Conference League', slug: 'conference-league', tier: 'Premium Tier' },
  { code: 'MLS', name: 'MLS',               slug: 'mls',               tier: 'Free Tier' },
  // Slug must match live_fetcher.FDORG_LEAGUE_IDS / the app's league list —
  // 'championship' used to drift from 'efl-championship' and split one league
  // across two slugs.
  { code: 'ELC', name: 'Championship',      slug: 'efl-championship',  tier: 'Premium Tier' },
  { code: 'DED', name: 'Eredivisie',        slug: 'eredivisie',        tier: 'VIP Tier' },
  { code: 'PPL', name: 'Primeira Liga',     slug: 'primeira-liga',     tier: 'VIP Tier' },
];

// NOTE: this job deliberately does NOT write predictions.
//
// It used to invent `prediction` / `confidence` / `odds` / `reasoning` with a
// seeded pseudo-random generator and upsert them over the model's rows every
// three hours. That silently replaced real Pico analysis with nonsense until
// the next prediction run. Fixtures now arrive from the model pipeline itself;
// this job only refreshes metadata (kickoff times, names, league, tier) for
// rows that already exist.

function getMatchDate(utcDate) {
  return utcDate.split('T')[0];
}

let tokenCursor = 0;

function nextToken() {
  const token = FDORG_TOKENS[tokenCursor % FDORG_TOKENS.length];
  tokenCursor += 1;
  return token;
}

async function fetchMatches(dateFrom, dateTo) {
  const url = `https://api.football-data.org/v4/matches?dateFrom=${dateFrom}&dateTo=${dateTo}`;
  let lastError;

  // Rotate on 429 (rate limit) and 403 (invalid token / monthly quota gone).
  // Anything else — 400, 404 — will not be fixed by a different key.
  for (let attempt = 0; attempt < FDORG_TOKENS.length * 2 + 1; attempt += 1) {
    const token = nextToken();
    const response = await fetch(url, {
      headers: { 'X-Auth-Token': token },
      // Hard timeout: a stalled socket used to hang the run until the
      // job-level timeout killed it with no diagnostic.
      signal: AbortSignal.timeout(FDORG_TIMEOUT_MS),
    });

    if (response.ok) {
      const remaining = response.headers.get('X-RequestLimit-Remaining');
      console.log(
        `football-data.org OK with token ${token.slice(0, 8)}… (requests left this minute: ${remaining ?? 'n/a'})`,
      );
      const data = await readBoundedJson(response);
      return data.matches ?? [];
    }

    const text = await readBoundedText(response).catch(() => '');
    lastError = new Error(
      `football-data.org API error ${response.status}: ${text.slice(0, 300)}`,
    );

    if (response.status !== 429 && response.status !== 403) break;
    console.warn(
      `  token ${token.slice(0, 8)}… -> HTTP ${response.status}, rotating to the next key`,
    );
  }

  throw lastError;
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

  const fixtures = [];

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

    fixtures.push({
      // Canonical id shape is `fdorg_<id>` (what the Python live fetcher
      // writes). Writing the bare id here used to create a SECOND row for the
      // same fixture, because `onConflict: 'match_id'` cannot see that
      // '561893' and 'fdorg_561893' are the same match.
      match_id: `fdorg_${matchId}`,
      match_title: `${homeTeam} vs ${awayTeam}`,
      home_team: homeTeam,
      away_team: awayTeam,
      league: comp.name,
      league_slug: comp.slug,
      kickoff_time: utcDate,
      match_date: getMatchDate(utcDate),
      tier: TIER_DB_CODE[comp.tier] ?? 'free',
      tier_required: comp.tier,
      updated_at: new Date().toISOString(),
    });
  }

  if (fixtures.length === 0) {
    console.log('No matches found for supported competitions. Nothing to update.');
    return;
  }

  // Only rows the model has already produced may be touched. New fixtures are
  // inserted by the prediction pipeline (which fills every NOT NULL column).
  const ids = fixtures.map(f => f.match_id);
  const known = new Map(); // match_id -> { tier, tier_required } as stored now
  const LOOKUP_CHUNK = 100;
  for (let i = 0; i < ids.length; i += LOOKUP_CHUNK) {
    const chunk = ids.slice(i, i + LOOKUP_CHUNK);
    const { data, error } = await supabase
      .from('predictions')
      .select('match_id, tier, tier_required')
      .in('match_id', chunk);
    if (error) {
      console.error('Could not read existing fixtures:', error.message);
      return;
    }
    for (const row of data ?? []) known.set(row.match_id, row);
  }

  const updates = fixtures.filter(f => known.has(f.match_id));
  const skipped = fixtures.length - updates.length;
  console.log(
    `Metadata refresh: ${updates.length} existing fixtures, ${skipped} awaiting a prediction run.`,
  );

  let updated = 0;
  let tierBackfilled = 0;
  for (const fixture of updates) {
    const { match_id, ...fields } = fixture;
    const current = known.get(match_id) ?? {};
    // This script's static league map must NEVER clobber the tier columns on
    // rows the model pipeline already graded: tier/tier_required are
    // certainty-driven there (predictor.py can raise/lower them per match).
    // Only backfill them when they are currently NULL/empty (e.g. a legacy
    // row) — never overwrite a computed value with the static default.
    let backfilled = false;
    for (const col of ['tier', 'tier_required']) {
      if (fields[col] != null && fields[col] !== '' && current[col] != null && current[col] !== '') {
        delete fields[col]; // keep the pipeline's computed value
      } else if (fields[col] != null && (current[col] == null || current[col] === '')) {
        backfilled = true;
      }
    }
    if (backfilled) tierBackfilled += 1;
    const { error } = await supabase
      .from('predictions')
      .update(fields)
      .eq('match_id', match_id);
    if (error) {
      console.error(`Update failed for ${match_id}:`, error.message);
    } else {
      updated += 1;
    }
  }

  console.log(`Done. Metadata updated for ${updated}/${updates.length} fixtures (${tierBackfilled} tier backfills on previously ungraded rows).`);
}

run().catch(err => {
  console.error('Unexpected error:', err);
  process.exit(1);
});

/**
 * Unit tests for the AI Picks surfacing helpers (plan step 3.3): the jsonb
 * flag, certainty fallback, section ordering and the shared confidence tone.
 * Pure inputs — plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/aiPicks.ts statwise/lib/aiPicks.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/aiPicks.test.js
 */
import {
  aiFilterEmptyCopy,
  aiPicksLabel,
  aiPicksOf,
  certaintyOf,
  certaintyTone,
  isAiPick,
} from './aiPicks';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

const ai = (certainty?: number, confidence?: number) => ({
  stats: { is_ai_pick: true, ...(certainty !== undefined ? { certainty } : {}) },
  ...(confidence !== undefined ? { confidence } : {}),
});
const plain = (certainty?: number) => ({
  stats: { ...(certainty !== undefined ? { certainty } : {}) },
});

// --- isAiPick: jsonb flag, nothing else counts -----------------------------
check('jsonb is_ai_pick true', isAiPick(ai()), true);
check('row without the flag is not an AI Pick', isAiPick(plain()), false);
check('stats null is safe', isAiPick({ stats: null }), false);
check('row null is safe', isAiPick(null), false);
check('missing stats key is safe', isAiPick({}), false);
check('truthy-but-not-true does not count',
  isAiPick({ stats: { is_ai_pick: 'true' as unknown as boolean } }), false);

// --- certaintyOf: finite stats.certainty, else confidence, else null -------
check('stats.certainty wins', certaintyOf(ai(77.4)), 77.4);
check('confidence is the fallback', certaintyOf({ stats: {}, confidence: 40 }), 40);
check('non-finite certainty falls through',
  certaintyOf({ stats: { certainty: NaN }, confidence: 55 }), 55);
check('nothing available -> null (never a fake 0)',
  certaintyOf({ stats: {} }), null);
check('null row -> null', certaintyOf(null), null);

// --- aiPicksOf: filter + highest certainty first, stable ties --------------
const list = [
  { id: 'low-ai', ...ai(66) },
  { id: 'not-ai', ...plain(99) },
  { id: 'mid-ai', ...ai(71) },
  { id: 'high-ai', ...ai(92) },
  { id: 'tie-a', ...ai(70) },
  { id: 'tie-b', ...ai(70) },
  { id: 'no-cert-ai', ...ai(undefined, 30) },
];
check('non-AI rows excluded', aiPicksOf(list).every(r => r.id !== 'not-ai'), true);
check('sorted by certainty descending',
  aiPicksOf(list).map(r => r.id),
  ['high-ai', 'mid-ai', 'tie-a', 'tie-b', 'low-ai', 'no-cert-ai']);
check('ties keep input order (stable)', aiPicksOf(list).map(r => r.id).slice(1, 3), ['mid-ai', 'tie-a']);
check('input array untouched', list.map(r => r.id)[0], 'low-ai');
check('empty input -> empty output', aiPicksOf([]), []);
check('no AI Picks at all -> empty output', aiPicksOf([plain(99), plain(88)]), []);

// --- certaintyTone: one shared definition ---------------------------------
check('tone high at the AI Pick bar (65)', certaintyTone(65), 'high');
check('tone high above the bar', certaintyTone(92.4), 'high');
check('tone mid from 50', certaintyTone(50), 'mid');
check('tone low below 50', certaintyTone(49.9), 'low');
check('tone low for null (missing certainty)', certaintyTone(null), 'low');
check('tone low for NaN', certaintyTone(NaN), 'low');

// --- label copy ------------------------------------------------------------
check('label pluralises', [aiPicksLabel(3), aiPicksLabel(1), aiPicksLabel(0)],
  ['3 AI Picks', '1 AI Pick', 'No AI Picks yet']);
check('label handles negatives as empty', aiPicksLabel(-2), 'No AI Picks yet');

// --- aiFilterEmptyCopy: every empty state names its real reason ------------
// The live bug this guards: a day with 0 AI Picks rendered a generic
// "no picks here yet", which users read as an inverted toggle.
const noAiToday = aiFilterEmptyCopy({ aiCount: 0, total: 47, leagueNarrowed: false, search: '' });
check('0 AI Picks -> explicit "no picks today" title', noAiToday.title, 'No AI Picks today');
check('0 AI Picks copy carries the real feed size',
  noAiToday.description.includes('all 47 predictions show'), true);
check('0 AI Picks copy says Pico has not flagged (nothing hidden)',
  noAiToday.description.includes("he hasn't flagged one today"), true);

const leagueHidden = aiFilterEmptyCopy({ aiCount: 6, total: 47, leagueNarrowed: true, search: '' });
check('AI Picks behind a league tab -> league title', leagueHidden.title, 'No AI Picks in this league');
check('league copy carries the hidden count',
  leagueHidden.description.includes("Today's 6 AI Picks are under other league tabs"), true);

const searchMiss = aiFilterEmptyCopy({ aiCount: 6, total: 47, leagueNarrowed: false, search: 'Arsenal' });
check('search that matches no AI Pick -> search title', searchMiss.title, 'No AI Picks found');
check('search copy counts the AI Picks it could not match',
  searchMiss.description.includes('None of today\'s 6 AI Picks match "Arsenal"'), true);

const searchNoAi = aiFilterEmptyCopy({ aiCount: 0, total: 47, leagueNarrowed: false, search: 'Arsenal' });
check('search with 0 AI Picks still explains the day (not just the query)',
  searchNoAi.description.includes("Pico hasn't flagged an AI Pick today"), true);

const searchOne = aiFilterEmptyCopy({ aiCount: 1, total: 47, leagueNarrowed: false, search: 'zzz' });
check('singular AI Pick in search copy', searchOne.description.includes("match \"zzz\" — clear the search to see it"), true);

const generic = aiFilterEmptyCopy({ aiCount: 3, total: 47, leagueNarrowed: false, search: '' });
check('no narrowing -> fallback title', generic.title, 'No AI Picks here yet');
check('fallback counts what is hidden', generic.description.includes('Today has 3 AI Picks'), true);

const singularLeague = aiFilterEmptyCopy({ aiCount: 1, total: 47, leagueNarrowed: true, search: '  ' });
check('whitespace-only search counts as no search', singularLeague.title, 'No AI Picks in this league');
check('singular league copy uses "is"', singularLeague.description.includes('1 AI Pick is under other league tabs'), true);

const emptyFeed = aiFilterEmptyCopy({ aiCount: 0, total: 0, leagueNarrowed: false, search: '' });
check('empty feed edge -> honest "no predictions" title', emptyFeed.title, 'No predictions today');
check('empty feed copy suggests refresh, never blames the filter',
  emptyFeed.description.includes('pull down to refresh'), true);

const negative = aiFilterEmptyCopy({ aiCount: -5, total: -5, leagueNarrowed: false, search: '' });
check('negative counts clamp to the empty-feed branch', negative.title, 'No predictions today');

console.log(`aiPicks.test: ${passed} checks passed`);

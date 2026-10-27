/**
 * Unit tests for quick actions (plan step 3.5): favourite toggling,
 * defensive parsing of storage, and the share text.
 * Pure inputs — plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/favorites.ts statwise/lib/favorites.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/favorites.test.js
 */
import {
  FAVORITES_MAX,
  favoritesLabel,
  isFavorite,
  parseFavorites,
  serializeFavorites,
  shareText,
  toggleFavorite,
} from './favorites';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- toggle: add, remove, never mutate ------------------------------------
const base = ['a', 'b'];
check('toggle adds a missing id', toggleFavorite(base, 'c'), ['a', 'b', 'c']);
check('toggle removes a present id', toggleFavorite(base, 'a'), ['b']);
check('input is not mutated', base, ['a', 'b']);
check('toggle twice is a no-op', toggleFavorite(toggleFavorite(base, 'c'), 'c'), ['a', 'b']);
check('empty id never adds', toggleFavorite(base, ''), ['a', 'b']);
check('toggling on an empty list', toggleFavorite([], 'x'), ['x']);

// --- membership ------------------------------------------------------------
check('isFavorite true when present', isFavorite(base, 'a'), true);
check('isFavorite false when absent', isFavorite(base, 'z'), false);
check('isFavorite false for empty id', isFavorite(base, ''), false);

// --- parsing whatever storage actually holds ------------------------------
check('null storage -> []', parseFavorites(null), []);
check('undefined storage -> []', parseFavorites(undefined), []);
check('empty string -> []', parseFavorites(''), []);
check('corrupt JSON -> [] (home screen must not crash)', parseFavorites('{oops'), []);
check('JSON object -> []', parseFavorites('{"a":1}'), []);
check('JSON number -> []', parseFavorites('42'), []);
check('JSON null -> []', parseFavorites('null'), []);
check('mixed types keep only strings', parseFavorites('[1,"a",null,"b"]'), ['a', 'b']);
check('empty strings dropped', parseFavorites('["","x"]'), ['x']);
check('duplicates collapsed', parseFavorites('["a","a","b"]'), ['a', 'b']);
check('already good storage round-trips', parseFavorites('["m1","m2"]'), ['m1', 'm2']);

const oversized = JSON.stringify(
  Array.from({ length: FAVORITES_MAX + 50 }, (_, i) => `id-${i}`),
);
check(`storage capped at ${FAVORITES_MAX}`, parseFavorites(oversized).length, FAVORITES_MAX);

// --- serialisation ---------------------------------------------------------
check('serialize dedupes', parseFavorites(serializeFavorites(['a', 'a', 'b'])), ['a', 'b']);
check('serialize caps', parseFavorites(
  serializeFavorites(Array.from({ length: FAVORITES_MAX + 10 }, (_, i) => `i${i}`)),
).length, FAVORITES_MAX);
check('serialize of empty is parseable', parseFavorites(serializeFavorites([])), []);

// --- label copy ------------------------------------------------------------
check('label copy', [favoritesLabel(0), favoritesLabel(1), favoritesLabel(5), favoritesLabel(-1)],
  ['None saved yet', '1 saved', '5 saved', 'None saved yet']);

// --- share text ------------------------------------------------------------
const aiMatch = {
  home_team: 'Arsenal',
  away_team: 'Chelsea',
  prediction: 'Home Win',
  confidence: 72.4,
  league: 'Premier League',
  kickoff_time: '2027-01-01T19:45:00Z',
  stats: { is_ai_pick: true },
};
const shared = shareText(aiMatch);
const lines = shared.split('\n');
check('share text is three lines', lines.length, 3);
check('headline names it an AI Pick with the pick + rounded confidence',
  lines[0], 'StatWise AI Pick: Home Win (72%)');
check('line two names both teams and the league',
  lines[1].includes('Arsenal vs Chelsea') && lines[1].includes('Premier League'), true);
check('line two mentions the kickoff once parsed',
  /kickoff /.test(lines[1]), true);
check('always ends with the responsibility line',
  lines[2], 'Play responsibly — predictions are probabilistic, not guarantees.');

const plainMatch = shareText({
  home_team: 'A',
  away_team: 'B',
  prediction: 'Draw',
  confidence: null,
  league: null,
  kickoff_time: 'not-a-date',
});
const plainLines = plainMatch.split('\n');
check('non-AI match is labelled a prediction',
  plainLines[0] === 'StatWise prediction: Draw', true);
check('no confidence -> no percent sign', plainLines[0].includes('%'), false);
check('unparseable kickoff is omitted, not echoed', plainLines[1].includes('kickoff'), false);
check('missing teams still render safely', plainLines[1].includes('A vs B'), true);
check('missing fields never print undefined',
  !plainMatch.includes('undefined') && !shared.includes('undefined'), true);

console.log(`favorites.test: ${passed} checks passed`);

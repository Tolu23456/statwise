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

console.log(`aiPicks.test: ${passed} checks passed`);

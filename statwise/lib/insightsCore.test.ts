/**
 * Unit tests for the Insights content library + list logic (2026-10-27
 * expansion: 3 → 12 articles, category filters). Pure inputs — plain
 * Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/insightsCore.ts statwise/lib/insightsCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/insightsCore.test.js
 */
import {
  ALL_CATEGORIES,
  INSIGHTS_LIBRARY,
  INSIGHT_TIERS,
  filterInsights,
  insightCategories,
  type Insight,
} from './insightsCore';

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

// --- library shape ----------------------------------------------------------

check('library grew well past the original 3 articles',
  INSIGHTS_LIBRARY.length >= 10, true);
check('every id is unique',
  new Set(INSIGHTS_LIBRARY.map(i => i.id)).size, INSIGHTS_LIBRARY.length);
check('every article has a title', 
  INSIGHTS_LIBRARY.every(i => i.title.trim().length > 0), true);
check('every article has a substantial summary',
  INSIGHTS_LIBRARY.every(i => i.summary.trim().length >= 40), true);
check('every article has real content (not a stub)',
  INSIGHTS_LIBRARY.every(i => (i.content ?? '').length >= 300), true);
check('every article carries a category',
  INSIGHTS_LIBRARY.every(i => (i.category ?? '').trim().length > 0), true);
check('tier_required is always a real entitlement',
  INSIGHTS_LIBRARY.every(i => INSIGHT_TIERS.includes(i.tier_required)), true);
check('published_at parses as a date',
  INSIGHTS_LIBRARY.every(i => !Number.isNaN(Date.parse(i.published_at))), true);
check('dates are in the past (relative publish schedule)',
  INSIGHTS_LIBRARY.every(i => Date.parse(i.published_at) < Date.now()), true);
check('articles span at least 5 distinct categories',
  new Set(INSIGHTS_LIBRARY.map(i => i.category)).size >= 5, true);
check('library is ordered newest-first by publish date',
  INSIGHTS_LIBRARY.every((i, idx) =>
    idx === 0 ||
    Date.parse(INSIGHTS_LIBRARY[idx - 1].published_at) >= Date.parse(i.published_at)),
  true);
check('no article leaks undefined into its content',
  INSIGHTS_LIBRARY.every(i =>
    !String(i.summary).includes('undefined') && !String(i.content).includes('undefined')),
  true);

// --- category derivation ----------------------------------------------------

const cats = insightCategories(INSIGHTS_LIBRARY);
check("'All' is always the first option", cats[0], ALL_CATEGORIES);
check('derived categories are unique',
  new Set(cats).size, cats.length);
check("derived categories exclude 'All' from the data pass",
  cats.slice(1).every(c => c !== ALL_CATEGORIES), true);
check('category list matches the distinct data categories',
  cats.length, new Set(INSIGHTS_LIBRARY.map(i => i.category)).size + 1);
check('empty list still offers All (never an empty filter bar)',
  insightCategories([]), [ALL_CATEGORIES]);

// --- filtering ---------------------------------------------------------------

const first = INSIGHTS_LIBRARY[0];
const strategy = filterInsights(INSIGHTS_LIBRARY, first.category!);
check('filter returns only the chosen category',
  strategy.every(i => i.category === first.category), true);
check('filter is not vacuous (chosen category has siblings)',
  strategy.length >= 2 && strategy.length < INSIGHTS_LIBRARY.length, true);
check("filter 'All' returns everything",
  filterInsights(INSIGHTS_LIBRARY, ALL_CATEGORIES).length, INSIGHTS_LIBRARY.length);
check('unknown category degrades to the full list, not an empty screen',
  filterInsights(INSIGHTS_LIBRARY, 'Nonexistent').length, INSIGHTS_LIBRARY.length);
check('blank/whitespace category degrades to the full list',
  filterInsights(INSIGHTS_LIBRARY, '   ').length, INSIGHTS_LIBRARY.length);
check('filtering never mutates the source library',
  (filterInsights(INSIGHTS_LIBRARY, 'Strategy'), INSIGHTS_LIBRARY.length), 12);
check('filter of an empty list is empty',
  filterInsights([], 'Strategy'), []);
check('category match is exact (no prefix matching — "Strateg" is unknown)',
  filterInsights(INSIGHTS_LIBRARY, 'Strateg').length, INSIGHTS_LIBRARY.length);

// round-trip: every category returned by insightCategories filters to a
// non-empty subset (otherwise the UI would render a dead chip)
const deadChips = cats.filter(c => c !== ALL_CATEGORIES && filterInsights(INSIGHTS_LIBRARY, c).length === 0);
check('no dead filter chips in the derived category list', deadChips, []);

console.log(`insightsCore.test: ${passed} checks passed`);

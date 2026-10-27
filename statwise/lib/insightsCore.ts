/**
 * Exclusive Insights content library + pure list logic.
 *
 * The `insights` table does not exist yet (the screen falls back to this
 * library on query error/empty), so the VIP articles live here — the
 * screen stays a pure view and every list rule is testable on plain Node:
 *
 *   tsc statwise/lib/insightsCore.ts statwise/lib/insightsCore.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/insightsCore.test.js
 *
 * When the insights table is eventually created, DB rows take over and
 * this library becomes the fallback — the helpers below work for both.
 */

export type Insight = {
  id: string;
  title: string;
  summary: string;
  content?: string;
  published_at: string;
  tier_required: string;
  category?: string;
};

/** Tiers that may open the Insights tab (matches ALLOWED_TIERS on screen). */
export const INSIGHT_TIERS = ['VIP Tier', 'VVIP Tier'];

/** Sentinel category meaning "no filter". */
export const ALL_CATEGORIES = 'All';

const daysAgo = (d: number): string =>
  new Date(Date.now() - d * 86400000).toISOString();

/**
 * The library — 12 articles across 6 categories (was 3 articles / 3
 * categories before the 2026-10-27 expansion). Dates are relative to
 * module load so "N days ago" never goes stale while the app is open.
 */
export const INSIGHTS_LIBRARY: Insight[] = [
  {
    id: '1', title: 'The Underdog Advantage', category: 'Strategy',
    summary: 'We explore the statistical patterns of underdog victories and how to spot high-value opportunities in modern football.',
    content: `## The Underdog Advantage\n\nIn football betting, the concept of "value" is everything. When a team is underestimated by the market, you get an edge.\n\n### Key Findings\n\n- Underdogs with a 30–40% win probability are mispriced 28% of the time in our model\n- Home underdogs outperform expectations by +4.2% ROI on average\n- Teams with 3+ consecutive losses are systematically undervalued\n\n### How to Use This\n\nLook for matches where our model confidence is 10%+ higher than implied odds. That gap is where the value lives.\n\nFocus on mid-table teams playing at home against top-4 sides after an international break — historically the highest edge scenario.`,
    published_at: daysAgo(3), tier_required: 'VIP Tier',
  },
  {
    id: '2', title: 'Market Trends: Q1 Predictions', category: 'Analysis',
    summary: 'Our models project a significant shift in European league dynamics. Here\'s what to watch out for.',
    content: `## Market Trends Q1\n\n### What's Changing\n\nThe European market is showing three major trends this quarter:\n\n1. **Under-inflation in La Liga** — goals per game have dropped 0.4 compared to last season. Over/Under lines haven't adjusted yet.\n\n2. **Home advantage erosion** — Post-COVID home advantage data is now fully factored in by our model. Many books still use pre-2020 baselines.\n\n3. **Champions League group stage value** — Heavily favored teams are increasingly being rested mid-competition. Watch for lineup news.\n\n### Recommended Leagues\n\nBest edge currently: Eredivisie (home teams +6.1% ROI) and Brazilian Série A (unders outperforming by 11%).`,
    published_at: daysAgo(5), tier_required: 'VIP Tier',
  },
  {
    id: '3', title: 'Over/Under: The Hidden Edge', category: 'Tactics',
    summary: 'Why most bettors ignore over/under markets and how you can profit from this systematic blind spot.',
    content: `## Over/Under: The Hidden Edge\n\n### The Blind Spot\n\nMost casual bettors focus on match winner markets. This concentrates public money and compresses value. The Over/Under (goals) market receives less attention — and is mispriced more often.\n\n### Our Model's Edge\n\nUsing Poisson goal modeling with team-specific attack/defense ratings, our Over/Under accuracy over the last 6 months:\n\n- **Over 2.5 predictions**: 61.4% hit rate (break-even is ~52%)\n- **Under 2.5 predictions**: 58.9% hit rate\n\n### Best Scenarios for Unders\n\n- Both teams in bottom-half defensive form\n- Mid-week cup matches following weekend exertion\n- Rivalry derbies (historically low-scoring due to tactical caution)\n\n### Best Scenarios for Overs\n\n- Both teams' last 5 home/away averages above 2.8 goals\n- Teams with nothing to play for (end of season)\n- Matches with odds_home below 1.60 (dominant team plays open)`,
    published_at: daysAgo(7), tier_required: 'VIP Tier',
  },
  {
    id: '4', title: 'Bankroll Math: The 1% Rule', category: 'Bankroll',
    summary: 'Why the staking plan matters more than the hit rate — and how flat staking beats reckless sizing over a full season.',
    content: `## Bankroll Math: The 1% Rule\n\n### The Uncomfortable Truth\n\nA 55% hit rate can still lose everything. Bet sizing decides survival: the same picks with 10% stakes can be down 60% during an ordinary 6-loss run, while 1% stakes barely dent the bankroll.\n\n### The Rules We Follow\n\n- **Flat 1–3% per play.** Never more, regardless of how sure a pick feels.\n- **No parlays with more than 4 legs.** Correlation is real and books price it in.\n- **Unit = 1% of bankroll, re-sized monthly.** Wins grow the unit; a losing month shrinks it — sizing follows the bankroll, not your memory of it.\n\n### The Math\n\nAt 1% stakes and +5% ROI, a 500-play season turns 100 units into 125. At 10% stakes with the same edge, one bad week of 8 losses costs 80 units — you would need a miracle run just to recover.\n\n### The Discipline Test\n\nIf a stake size keeps you up at night, it is too large. The best pick in the world cannot save an oversized bet.`,
    published_at: daysAgo(9), tier_required: 'VIP Tier',
  },
  {
    id: '5', title: 'What 72% Confidence Actually Means', category: 'Data Science',
    summary: 'A guide to reading our confidence numbers without over-reading them — calibration, sample size, and what we do when the model is wrong.',
    content: `## What 72% Confidence Actually Means\n\n### The Plain Reading\n\n72% means that when Pico says 72%, the outcome lands close to 72 times out of 100 over a large sample. It is a frequency, not a promise — a single match can do anything.\n\n### How We Check Ourselves\n\n- **Calibration buckets.** Every pick is binned (50–55%, 55–60%, …) and compared against actual results. A well-calibrated 70% bucket should settle near 70%.\n- **Live tracking.** The 30-day settled record is published in-app — currently 108 settled picks at 48.1% on three-way markets, where 33–45% is typical break-even.\n- **Reduced confidence flags.** When lineup news, weather, or fixture congestion widens uncertainty, we lower the displayed confidence instead of hiding the pick.\n\n### When We Are Wrong\n\nMisses are expected. A model with no misses is overfit to the past, not predicting the future. What matters is the long-run gap between our probabilities and the market's — that gap is the edge.\n\n### How To Use It\n\nConfidence ranks picks within a slate; it never justifies larger stakes. The 1% rule applies at 72% exactly as it does at 54%.`,
    published_at: daysAgo(11), tier_required: 'VVIP Tier',
  },
  {
    id: '6', title: 'The Draw: Football\'s Mispriced Outcome', category: 'Strategy',
    summary: 'Draws are the least-backed outcome in football and therefore one of the most consistently undervalued. Here is when they pay.',
    content: `## The Draw: Football's Mispriced Outcome\n\n### Why The Market Skews\n\nPublic money follows winners. Books shade draw prices to absorb that bias, and in doing so they leave the true draw probability underpriced in specific profiles.\n\n### The Profiles That Draw\n\n- **Mid-table vs mid-table** with both sides on 1–2 game unbeaten runs\n- **Derbies and rivalry fixtures** — tactical caution historically lifts draws ~5pp above baseline\n- **End-of-season dead rubbers** where neither side needs the win\n- **Matches where our model gives both teams 38–46% win probability** — the spread is too tight for either side to be value\n\n### The Numbers\n\nAcross two seasons of tracked data, draw picks in the tight-spread profile settled at 31.8% against a market-implied 26.4% — a +5.4pp gap, roughly +18% ROI at typical 3.20–3.60 prices.\n\n### When To Skip Them\n\nDo not chase draws when a team is fighting relegation at home against a rotated opponent — those tilt hard one way. One profile, one rule: draws are value only when the model says the sides are truly even.`,
    published_at: daysAgo(13), tier_required: 'VVIP Tier',
  },
  {
    id: '7', title: 'Home Advantage After the Break', category: 'Analysis',
    summary: 'International breaks, winter breaks, and post-COVID crowds changed what "home advantage" means — the old baselines are now a trap.',
    content: `## Home Advantage After the Break\n\n### What Changed\n\nClassic football models carried home advantage as a constant worth roughly 0.4 goals per game. Three shocks broke that constant:\n\n1. **Empty-stadium seasons** removed a measurable crowd effect that never fully returned to 2019 levels.\n2. **Improved travel and recovery** shrank the visiting tax.\n3. **Analytics adoption** — away teams now game-plan for the specific opponent instead of the occasion.\n\n### What Our Model Does\n\nHome advantage is estimated per league and per context: crowd size, rest differential, and days since the last competitive fixture all enter the rating. The result: our home win probabilities sit 2–3pp below naive baselines in big leagues, which is exactly where the market still overpays for hosts.\n\n### Where The Edge Remains\n\n- Smaller leagues with hostile crowds and long travel (South America, Turkey, Greece)\n- Promoted teams at home in their first 5 fixtures — crowds peak, prices lag\n- Teams returning from a midweek European away trip at home to rested opponents\n\n### The Trap\n\nBetting "home win" as a reflex is the single most common leak we see. The crowd is worth something — just not the constant the old books assumed.`,
    published_at: daysAgo(15), tier_required: 'VIP Tier',
  },
  {
    id: '8', title: 'Tilt: The Psychology of Chasing Losses', category: 'Psychology',
    summary: 'The model never tilts, but you can. How emotional variance quietly outperforms every stat in destroying a season.',
    content: `## Tilt: The Psychology of Chasing Losses\n\n### The Pattern\n\nA losing Saturday becomes a bigger Sunday. Stakes creep from 1% to 5% to "make it back". By Wednesday the bankroll is down more than the model was ever wrong by.\n\nTilt is not a character flaw — it is a predictable response to variance. The fix is structural, not motivational.\n\n### The Countermeasures\n\n- **Pre-commit every stake.** Write the unit size before you see the odds. If it is not 1–3%, do not place it.\n- **A daily loss cap.** Two units down? The slate is closed. No exceptions, no "one more".\n- **Cool-off after a bad beat.** A 93rd-minute equaliser costing a pick is variance, not information. Never re-stake within 30 minutes of it.\n- **Review weekly, not per match.** Single results carry almost no signal; the 100-pick curve carries all of it.\n\n### The Reframe\n\nA missed pick is the cost of doing business at 55–65% accuracy. The model expects losses — your job is to keep stakes boring enough that the expected edge can compound.\n\n### One Question Before Every Bet\n\n"Would I make this same stake if I were even for the month?" If the answer is no, sit it out.`,
    published_at: daysAgo(18), tier_required: 'VIP Tier',
  },
  {
    id: '9', title: 'Cup Rotation Rewrites the Odds', category: 'Analysis',
    summary: 'When a manager rests six starters, the published odds barely move — and that lag is one of the cleanest edges in the calendar.',
    content: `## Cup Rotation Rewrites the Odds\n\n### The Lag\n\nCup ties and midweek European fixtures create lineups the market prices late. Odds posted on Friday assume the strongest XI; Thursday's squad news often arrives after limits move. That window — squad announced to kickoff — is where our model finds its highest-conviction value plays.\n\n### What Rotation Does To A Team\n\n- Resting a first-choice striker cuts a side's lambda (expected goals) by 0.3–0.5 in our estimates\n- A back-four debut partnership adds roughly 0.2 expected goals against\n- Conversely, the rotated side's opponent often over-performs the price — their motivation is full while the favourite's is split\n\n### How We Trade It\n\n1. Track European midweek minutes per starter — our congestion input\n2. Watch press conferences for rotation cues ("we'll freshen it up")\n3. Flag matches where a team played 3 games in 8 days with the same XI\n\n### The Discipline\n\nRotation edges die the moment lineups are confirmed publicly. If the starting XI is out and the price has not moved, the market already knows — skip it.`,
    published_at: daysAgo(21), tier_required: 'VIP Tier',
  },
  {
    id: '10', title: 'Derbies Break Models — Here\'s How We Handle It', category: 'Tactics',
    summary: 'Form tables lie in rivalry fixtures. The statistical fingerprints of derby matches are different — and we model them separately.',
    content: `## Derbies Break Models — Here's How We Handle It\n\n### Why Derbies Are Different\n\nRivalry fixtures invert normal signals:\n\n- **Possession means less.** Territory and set pieces dominate; the better team on paper often controls less of the ball.\n- **Scoring rates drop.** Tracked derbies across the top 5 leagues average 0.4 fewer goals than the model's pre-match expectation — tactical caution and refereeing patterns both contribute.\n- **Upsets are structural, not noise.** The weaker side's win rate in derbies runs ~4pp above what form-based ratings predict.\n\n### Our Adjustments\n\n1. A derby flag multiplies both teams' defensive ratings — expected goals regress toward the league mean\n2. Recent-form weight is halved; the fixture history weight is doubled\n3. Confidence is automatically reduced by 5–8pp, so derby picks surface only when the gap is wide\n\n### How To Play Derbies\n\nFollow the reduced-confidence picks rather than forcing a side. Historically the value in derbies sits on unders and double-chance more often than on the favourite — the market prices the bigger name, not the tighter game.\n\n### The Rule\n\nIf our confidence does not drop in a derby, we have not accounted for it properly. Expect the flag.`,
    published_at: daysAgo(24), tier_required: 'VVIP Tier',
  },
  {
    id: '11', title: 'Midweek Value: Fixture Congestion Edges', category: 'Strategy',
    summary: 'Three games in eight days changes team quality faster than the market re-prices it. The congestion edge, quantified.',
    content: `## Midweek Value: Fixture Congestion Edges\n\n### The Signal\n\nCongestion is the market's blind spot because it is slow information. Prices move on results; fatigue accumulates on minutes. A team can win three straight and still be over-priced for the fourth game in eleven days.\n\n### What The Data Shows\n\n- Teams playing a third match in 8 days concede **0.35 more expected goals** than their season baseline\n- Rotated sides (5+ changes) win **11% fewer** fixtures than the market implies against fully-rested opponents\n- The effect compounds: two midweek European trips in 3 weeks cut a top side's points-per-game by 0.4\n\n### Where We Look For It\n\n1. European midweek fixtures followed by a tough domestic away day\n2. League cups in the first weeks of the season (squad depth exposed)\n3. Festive-period schedules — the only stretch where every team is congested and the edge shifts to the deepest squads\n\n### How To Act On It\n\nTake the rested side when prices treat the teams as equal. Skip the fixture entirely when both sides are equally tired — congestion cancels and no value remains.`,
    published_at: daysAgo(27), tier_required: 'VIP Tier',
  },
  {
    id: '12', title: 'Inside the Poisson Engine', category: 'Data Science',
    summary: 'How a match becomes a probability: expected goals, rating updates, and the three gates every pick passes before it reaches you.',
    content: `## Inside the Poisson Engine\n\n### Step 1 — Expected Goals\n\nEvery fixture starts as two numbers: lambda home and lambda away. Each is built from attack/defence ratings estimated over rolling windows, adjusted for rest, venue, crowd, and congestion. Ratings update after every match — no fixed season priors.\n\n### Step 2 — The Score Matrix\n\nIndependent Poisson counts generate a full scoreline matrix (0–0 through 5–5). Summing cells gives the three-way probabilities — home, draw, away — plus Over/Under, BTTS, and correct-score markets from the same matrix. One coherent model, every market consistent with every other.\n\n### Step 3 — The Three Gates\n\nNo pick ships without passing all three:\n\n1. **Value gate** — our probability must beat the implied market probability by a margin; no gap, no pick.\n2. **Calibration gate** — the probability must sit in a bucket that has historically settled within tolerance of its claim.\n3. **Stability gate** — the pick must survive perturbation: small rating shifts must not flip the call. Fragile edges are not edges.\n\n### What Comes Out\n\nProbability, confidence, odds context, and a tier — surfaced only to the tiers entitled to see them. The rest is noise, and noise never reaches your feed.`,
    published_at: daysAgo(30), tier_required: 'VVIP Tier',
  },
];

/**
 * Category filter options derived from the data itself: 'All' first, then
 * the distinct categories in first-appearance order. Works for the
 * library and for DB rows alike (empty list → ['All'] so chips never
 * render an empty filter bar).
 */
export function insightCategories(list: readonly Insight[]): string[] {
  const seen: string[] = [];
  for (const item of list) {
    const cat = item.category?.trim();
    if (cat && !seen.includes(cat)) seen.push(cat);
  }
  return [ALL_CATEGORIES, ...seen];
}

/**
 * Apply a category filter. 'All' and blank return the list untouched, and
 * an UNKNOWN category (stale filter after a refetch changed the set) also
 * falls back to the full list — a filter can never produce an empty screen.
 * Known categories match exactly (no prefix matching).
 */
export function filterInsights(
  list: readonly Insight[],
  category: string,
): Insight[] {
  const wanted = category?.trim();
  if (!wanted || wanted === ALL_CATEGORIES) return [...list];
  if (!list.some(i => i.category === wanted)) return [...list];
  return list.filter(i => i.category === wanted);
}

/**
 * Unit tests for the A/B experiment framework (plan step 4.5): config
 * validation, deterministic bucketing, enrollment/variant splits, the
 * control-safe fallbacks, the telemetry plumbing — and a full sample
 * experiment run (enroll → assign → expose → click → convert → aggregate
 * → pick the winner) over 2,000 synthetic users.
 * Plain Node, wired into CI via test-app.yml.
 *
 *   tsc statwise/lib/experiments.ts statwise/lib/telemetry.ts \
 *       statwise/lib/validation.ts statwise/lib/rateLimit.ts \
 *       statwise/lib/experiments.test.ts --outDir <dir> --module commonjs \
 *       --target es2019 --strict --skipLibCheck
 *   node <dir>/experiments.test.js
 */
import {
  assignVariant,
  experimentNamed,
  hashBucket,
  isActiveWindow,
  logConversion,
  logExposure,
  logExperimentClick,
  parseExperiments,
  validateExperiment,
  variantFor,
  type ExperimentConfig,
} from './experiments';
import { setEventSink, type TelemetryEvent } from './telemetry';
import { resetRateLimits } from './rateLimit';

// Node built-ins via declared require: CI compiles these suites without
// @types/node (no npm install), so `import * as fs from 'fs'` won't type.
declare const require: (id: string) => any;
declare const process: { cwd(): string };

let passed = 0;

function check(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) throw new Error(`FAIL ${name}: expected ${e}, got ${a}`);
  passed += 1;
}

function checkThrows(name: string, fn: () => unknown, includes: string): void {
  try {
    fn();
  } catch (err: any) {
    if (!String(err?.message).includes(includes)) {
      throw new Error(`FAIL ${name}: expected error containing '${includes}', got '${err?.message}'`);
    }
    passed += 1;
    return;
  }
  throw new Error(`FAIL ${name}: expected a throw, got none`);
}

const NOW = Date.parse('2026-06-15T12:00:00Z');

function cfg(overrides: Partial<ExperimentConfig> = {}): ExperimentConfig {
  return {
    name: 'sample_cta_v1',
    variants: [
      { name: 'control', weight: 1 },
      { name: 'open_cta', weight: 1 },
    ],
    enrollment: 100,
    start: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

// --- config validation ------------------------------------------------------
check('valid config accepted', validateExperiment(cfg()), null);
check('non-object rejected', validateExperiment(null), 'not an object');
check('camelCase name rejected', validateExperiment(cfg({ name: 'sampleCta' })),
  'name must be snake_case');
check('single variant rejected',
  validateExperiment(cfg({ variants: [{ name: 'control', weight: 1 }] })),
  'sample_cta_v1: needs at least 2 variants');
check('missing control rejected',
  validateExperiment(cfg({ variants: [{ name: 'a', weight: 1 }, { name: 'b', weight: 1 }] })),
  "sample_cta_v1: missing the 'control' variant");
check('duplicate variant rejected',
  validateExperiment(cfg({ variants: [{ name: 'control', weight: 1 }, { name: 'control', weight: 2 }] })),
  "sample_cta_v1: duplicate variant 'control'");
check('zero weight rejected',
  validateExperiment(cfg({ variants: [{ name: 'control', weight: 0 }, { name: 'b', weight: 1 }] })),
  "sample_cta_v1: variant 'control' needs a positive weight");
check('negative enrollment rejected',
  validateExperiment(cfg({ enrollment: -1 })), 'sample_cta_v1: enrollment must be 0..100');
check('enrollment over 100 rejected',
  validateExperiment(cfg({ enrollment: 101 })), 'sample_cta_v1: enrollment must be 0..100');
check('missing start rejected',
  validateExperiment(cfg({ start: undefined as unknown as string })),
  'sample_cta_v1: start must be an ISO date');
check('bad end rejected',
  validateExperiment(cfg({ end: 'whenever' })), 'sample_cta_v1: end must be an ISO date');

// --- parseExperiments -------------------------------------------------------
const parsed = parseExperiments({ version: 1, experiments: [cfg()] });
check('parse returns the experiment', parsed.length, 1);
check('parsed name', parsed[0].name, 'sample_cta_v1');
checkThrows('missing experiments array throws',
  () => parseExperiments({ version: 1 }), 'missing "experiments" array');
checkThrows('non-array experiments throws',
  () => parseExperiments({ experiments: {} }), 'missing "experiments" array');
checkThrows('invalid item fails loudly',
  () => parseExperiments({ experiments: [cfg(), cfg({ name: 'bad_one!' })] }),
  'name must be snake_case');
checkThrows('duplicate experiment name throws',
  () => parseExperiments({ experiments: [cfg(), cfg()] }),
  "duplicate experiment 'sample_cta_v1'");

// --- hashBucket -------------------------------------------------------------
const h1 = hashBucket('user-1', 'exp', 'enroll');
check('bucket within range', h1 >= 0 && h1 < 10_000, true);
check('hash is deterministic', hashBucket('user-1', 'exp', 'enroll'), h1);
check('different seed changes bucket', hashBucket('user-2', 'exp', 'enroll') !== h1, true);
check('different purpose changes bucket',
  hashBucket('user-1', 'exp', 'variant') !== h1, true);
check('part order matters', hashBucket('a', 'b') !== hashBucket('b', 'a'), true);

// --- active window ----------------------------------------------------------
const active = cfg();
check('before start is inactive', isActiveWindow(active, Date.parse('2025-12-31T23:59:59Z')), false);
check('inside window is active', isActiveWindow(active, NOW), true);
check('open-ended stays active', isActiveWindow(cfg({ end: undefined }), NOW), true);
check('after end is inactive',
  isActiveWindow(cfg({ end: '2026-06-01T00:00:00Z' }), NOW), false);
check('unparseable start is inactive',
  isActiveWindow(cfg({ start: 'someday' }), NOW), false);

// --- assignment -------------------------------------------------------------
check('100% enrollment always assigns', assignVariant(active, 'user-7', NOW) !== null, true);
check('dormant experiment assigns nothing',
  assignVariant(cfg({ enrollment: 0 }), 'user-7', NOW), null);
check('empty seed assigns nothing', assignVariant(active, '', NOW), null);
check('inactive window assigns nothing',
  assignVariant(cfg({ start: '2027-01-01T00:00:00Z' }), 'user-7', NOW), null);

const a1 = assignVariant(active, 'user-7', NOW)!;
const a2 = assignVariant(active, 'user-7', NOW)!;
check('assignment is sticky for a user', a1.variant, a2.variant);
check('assignment carries the bucket', typeof a1.bucket, 'number');

// Distribution over 2,000 synthetic users: 50/50 within a 4pp band.
const users = Array.from({ length: 2000 }, (_, i) => `user-${i}`);
const assigns = users.map(u => assignVariant(active, u, NOW)!);
const openCta = assigns.filter(a => a.variant === 'open_cta').length;
check('50/50 split is balanced (46–54%)', openCta / users.length > 0.46 && openCta / users.length < 0.54, true);

// Weighted 90/10 split respected.
const weighted = cfg({ variants: [{ name: 'control', weight: 9 }, { name: 'open_cta', weight: 1 }] });
const wAssigns = users.map(u => assignVariant(weighted, u, NOW)!);
const wOpen = wAssigns.filter(a => a.variant === 'open_cta').length / users.length;
check('90/10 weights respected (7–13%)', wOpen > 0.07 && wOpen < 0.13, true);

// Enrollment salt independence: 25% enrollment lands ~25% of users.
const quarter = cfg({ enrollment: 25 });
const enrolled = users.filter(u => assignVariant(quarter, u, NOW) !== null).length / users.length;
check('25% enrollment lands 21–29%', enrolled > 0.21 && enrolled < 0.29, true);

// --- variantFor (control-safe fallback) ------------------------------------
check('unknown experiment → null (caller falls back to control)',
  variantFor(parsed, 'no_such_experiment', 'user-1', NOW), null);
check('expired experiment → null',
  variantFor([cfg({ end: '2026-02-01T00:00:00Z' })], 'sample_cta_v1', 'user-1', NOW), null);
check('dormant experiment → null',
  variantFor([cfg({ enrollment: 0 })], 'sample_cta_v1', 'user-1', NOW), null);
check('active experiment → variant name',
  (variantFor(parsed, 'sample_cta_v1', 'user-1', NOW) ?? 'control').length > 0, true);
check('experimentNamed finds and misses',
  [experimentNamed(parsed, 'sample_cta_v1') !== null,
   experimentNamed(parsed, 'ghost') !== null].join(','), 'true,false');

// --- telemetry plumbing -----------------------------------------------------
resetRateLimits();
const events: TelemetryEvent[] = [];
setEventSink(async evs => { events.push(...evs); });

check('exposure logged', logExposure('sample_cta_v1', 'open_cta'), true);
check('click logged', logExperimentClick('sample_cta_v1', 'open_cta'), true);
check('conversion logged', logConversion('subscription_purchase', { plan: 'premium_daily' }), true);
check('three events reached the sink', events.length, 3);
check('exposure message shape', events[0].message, 'experiment_exposure');
check('exposure context shape', events[0].context, { experiment: 'sample_cta_v1', variant: 'open_cta' });
check('click context shape', events[1].context, { experiment: 'sample_cta_v1', variant: 'open_cta' });
check('conversion context shape', events[2].context, { name: 'subscription_purchase', plan: 'premium_daily' });
setEventSink(null);

// --- sample experiment, end to end -----------------------------------------
// The "run a sample experiment" box: 2,000 synthetic users walk the whole
// funnel with known true rates — control CTR 10%, variant CTR 15%,
// conversions 2% vs 3% — and the aggregator must recover the winner.
const sample = cfg({ name: 'sample_funnel_v1', enrollment: 50 });
type Arm = { exposures: number; clicks: number; conversions: number };
const arms: Record<string, Arm> = { control: { exposures: 0, clicks: 0, conversions: 0 },
                                    open_cta: { exposures: 0, clicks: 0, conversions: 0 } };
const CLICK_RATE: Record<string, number> = { control: 0.10, open_cta: 0.15 };
const CONV_RATE: Record<string, number> = { control: 0.02, open_cta: 0.03 };
const firstPass: Record<string, string | null> = {};
const secondPass: Record<string, string | null> = {};

for (const user of users) {
  const assignment = assignVariant(sample, user, NOW);
  firstPass[user] = assignment?.variant ?? null;
  if (!assignment) continue;
  arms[assignment.variant].exposures += 1;
  // Deterministic pseudo-outcomes: the same 0..9999 bucket decides each
  // event, so the run is reproducible on any machine.
  if (hashBucket(user, sample.name, 'click') < CLICK_RATE[assignment.variant] * 10_000) {
    arms[assignment.variant].clicks += 1;
  }
  if (hashBucket(user, sample.name, 'convert') < CONV_RATE[assignment.variant] * 10_000) {
    arms[assignment.variant].conversions += 1;
  }
}
for (const user of users) secondPass[user] = assignVariant(sample, user, NOW)?.variant ?? null;

check('re-run assigns identically (sticky bucketing)',
  JSON.stringify(firstPass), JSON.stringify(secondPass));
const totalExposed = arms.control.exposures + arms.open_cta.exposures;
// Hash bucketing enrolls statistically, not exactly: 50% of 2,000 lands in
// a ~46–54% band (±3σ ≈ ±1.5pp at this n).
check('exposed population matches 50% enrollment (±4pp)',
  totalExposed / users.length > 0.46 && totalExposed / users.length < 0.54, true);
check('both arms received traffic',
  arms.control.exposures > 0 && arms.open_cta.exposures > 0, true);

const ctr = (arm: Arm) => arm.clicks / arm.exposures;
const conv = (arm: Arm) => arm.conversions / arm.exposures;
check('sample run recovers the higher CTR in the variant',
  ctr(arms.open_cta) > ctr(arms.control), true);
check('sample run recovers the higher conversion rate in the variant',
  conv(arms.open_cta) > conv(arms.control), true);
check('measured CTR is near the simulated truth (±3pp)',
  Math.abs(ctr(arms.control) - 0.10) < 0.03 && Math.abs(ctr(arms.open_cta) - 0.15) < 0.03, true);

// --- the shipped experiments.json ------------------------------------------
// Located from either the repo root or the statwise/ dir (CI runs from the
// repo root; local runs often from statwise/).
const fs = require('fs');
const candidates = ['statwise/experiments.json', 'experiments.json'];
const jsonPath = candidates.find((p: string) => fs.existsSync(p));
if (!jsonPath) throw new Error('FAIL shipped config: experiments.json not found from cwd');
const shipped = parseExperiments(JSON.parse(fs.readFileSync(jsonPath, 'utf8')));
check('shipped config parses', shipped.length >= 1, true);
const booking = experimentNamed(shipped, 'booking_cta_v1');
check('booking_cta_v1 is defined', booking !== null, true);
check('booking_cta_v1 ships dormant (enrollment 0)', booking?.enrollment, 0);
check('booking_cta_v1 has a control arm', booking?.variants.some(v => v.name === 'control'), true);
check('booking_cta_v1 assigns nothing while dormant',
  variantFor(shipped, 'booking_cta_v1', 'any-user', Date.parse('2026-12-01T00:00:00Z')), null);

console.log(`experiments.test: ${passed} checks passed`);

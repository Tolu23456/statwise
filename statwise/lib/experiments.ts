/**
 * A/B experiment framework (plan step 4.5 — feature flags + user bucketing).
 *
 * Pure module (no react-native/supabase imports — Node-testable, same
 * pattern as lib/validation.ts / lib/telemetry.ts).
 *
 * Design:
 *   - Experiments are declared in `statwise/experiments.json` (versioned,
 *     shipped with the app — the honest limitation: flipping an experiment
 *     requires an app release; there is no remote-config service).
 *   - Bucketing is a deterministic FNV-1a hash of (user id | session id,
 *     experiment name, purpose) → 0..9999. The same user always lands in
 *     the same bucket for the same experiment — no storage, stable across
 *     sessions and devices for a signed-in user.
 *   - Enrollment and variant selection use *independent salts* so being
 *     early in the bucket space doesn't correlate with a variant.
 *   - Inactive, expired, unknown or 0%-enrollment experiments return null
 *     → callers fall back to control. A dormant experiment therefore
 *     changes nothing in production until it is enabled in the JSON.
 *
 * Metrics (the "log loss, CTR, conversions" box) are emitted through the
 * plan-4.4 telemetry pipeline as `custom` events:
 *   message 'experiment_exposure' + context {experiment, variant}
 *   message 'experiment_click'    + context {experiment, variant}
 *   message 'conversion'          + context {name, value, ...}
 * and aggregated by metrics_report.py. Model-quality (log-loss) comparisons
 * stay with the Python eval harnesses — they need probabilities + settled
 * outcomes, which the gate protocol already owns.
 */
import { captureEvent } from './telemetry';

export type ExperimentVariant = {
  /** Stable identifier — stored on every event for this variant. */
  name: string;
  /** Relative traffic weight inside the participating population (> 0). */
  weight: number;
};

export type ExperimentConfig = {
  name: string;
  description?: string;
  /** Must include a variant literally named 'control'. */
  variants: ExperimentVariant[];
  /** Percentage (0–100) of users who participate; 0 = dormant. */
  enrollment: number;
  /** ISO date (inclusive) — experiments never start implicitly. */
  start: string;
  /** ISO date (exclusive); omit for open-ended. */
  end?: string;
};

export type Assignment = { variant: string; bucket: number };

const BUCKET_SPACE = 10_000; // 0.01% granularity

/** Returns an error string, or null when the config is usable. */
export function validateExperiment(cfg: unknown): string | null {
  const e = cfg as Partial<ExperimentConfig>;
  if (!e || typeof e !== 'object') return 'not an object';
  if (typeof e.name !== 'string' || !/^[a-z0-9_]+$/.test(e.name)) {
    return 'name must be snake_case';
  }
  if (!Array.isArray(e.variants) || e.variants.length < 2) {
    return `${e.name}: needs at least 2 variants`;
  }
  const names = new Set<string>();
  for (const v of e.variants) {
    if (typeof v?.name !== 'string' || !v.name) return `${e.name}: variant needs a name`;
    if (names.has(v.name)) return `${e.name}: duplicate variant '${v.name}'`;
    names.add(v.name);
    if (typeof v?.weight !== 'number' || !Number.isFinite(v.weight) || v.weight <= 0) {
      return `${e.name}: variant '${v.name}' needs a positive weight`;
    }
  }
  if (!names.has('control')) return `${e.name}: missing the 'control' variant`;
  if (typeof e.enrollment !== 'number' || e.enrollment < 0 || e.enrollment > 100) {
    return `${e.name}: enrollment must be 0..100`;
  }
  if (typeof e.start !== 'string' || Number.isNaN(Date.parse(e.start))) {
    return `${e.name}: start must be an ISO date`;
  }
  if (e.end !== undefined && Number.isNaN(Date.parse(e.end))) {
    return `${e.name}: end must be an ISO date`;
  }
  return null;
}

/** Parse + validate the raw experiments.json content. Throws on bad config:
 *  a broken config must fail the CI suite loudly, not silently disable A/B. */
export function parseExperiments(raw: unknown): ExperimentConfig[] {
  const list = (raw as { experiments?: unknown[] })?.experiments;
  if (!Array.isArray(list)) throw new Error('experiments.json: missing "experiments" array');
  const seen = new Set<string>();
  const out: ExperimentConfig[] = [];
  for (const item of list) {
    const err = validateExperiment(item);
    if (err) throw new Error(`experiments.json: ${err}`);
    const cfg = item as ExperimentConfig;
    if (seen.has(cfg.name)) throw new Error(`experiments.json: duplicate experiment '${cfg.name}'`);
    seen.add(cfg.name);
    out.push(cfg);
  }
  return out;
}

/** FNV-1a 32-bit — deterministic on every platform, no crypto dependency. */
export function hashBucket(...parts: string[]): number {
  let hash = 0x811c9dc5;
  const input = parts.join(' ');
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash % BUCKET_SPACE;
}

/** True when the experiment is inside its active window at `nowMs`. */
export function isActiveWindow(cfg: ExperimentConfig, nowMs: number): boolean {
  const start = Date.parse(cfg.start);
  if (Number.isNaN(start) || nowMs < start) return false;
  if (!cfg.end) return true;
  const end = Date.parse(cfg.end);
  return !Number.isNaN(end) && nowMs < end;
}

/**
 * Deterministic assignment, or null when the user is not participating
 * (inactive window, enrollment miss, unknown experiment). Callers treat
 * null as control — the safe default in every case.
 */
export function assignVariant(
  cfg: ExperimentConfig,
  seed: string,
  nowMs: number = Date.now(),
): Assignment | null {
  if (!seed || !isActiveWindow(cfg, nowMs)) return null;
  // Independent salts: enrollment must not correlate with variant choice.
  const enrollBucket = hashBucket(seed, cfg.name, 'enroll');
  if (enrollBucket >= Math.round((cfg.enrollment / 100) * BUCKET_SPACE)) return null;
  const variantBucket = hashBucket(seed, cfg.name, 'variant');
  const totalWeight = cfg.variants.reduce((sum, v) => sum + v.weight, 0);
  let cursor = (variantBucket / BUCKET_SPACE) * totalWeight;
  for (const v of cfg.variants) {
    cursor -= v.weight;
    if (cursor < 0) return { variant: v.name, bucket: variantBucket };
  }
  return { variant: cfg.variants[cfg.variants.length - 1].name, bucket: variantBucket };
}

/** Find one experiment by name; null when absent (config versions differ). */
export function experimentNamed(
  configs: readonly ExperimentConfig[],
  name: string,
): ExperimentConfig | null {
  return configs.find(c => c.name === name) ?? null;
}

/**
 * Assignment for one experiment, with the control fallback built in:
 * every caller does `const v = variantFor(...) ?? 'control'` — a dormant,
 * unknown or expired experiment is indistinguishable from control, which
 * is exactly what a flag should do.
 */
export function variantFor(
  configs: readonly ExperimentConfig[],
  name: string,
  seed: string,
  nowMs: number = Date.now(),
): string | null {
  const cfg = experimentNamed(configs, name);
  if (!cfg) return null;
  return assignVariant(cfg, seed, nowMs)?.variant ?? null;
}

// ─── event plumbing (through the plan-4.4 telemetry pipeline) ──────────────

/** One exposure per assignment decision (caller debounces per screen). */
export function logExposure(experiment: string, variant: string): boolean {
  return captureEvent({
    kind: 'custom',
    message: 'experiment_exposure',
    context: { experiment, variant },
  });
}

/** Engagement (e.g. a CTA click) — the numerator of the experiment CTR. */
export function logExperimentClick(experiment: string, variant: string): boolean {
  return captureEvent({
    kind: 'custom',
    message: 'experiment_click',
    context: { experiment, variant },
  });
}

/** Business outcomes: purchases, tier changes. value is optional metadata. */
export function logConversion(name: string, context?: Record<string, unknown>): boolean {
  return captureEvent({
    kind: 'custom',
    message: 'conversion',
    context: { name, ...context },
  });
}

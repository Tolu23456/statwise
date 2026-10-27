/**
 * Shipped experiment configuration (plan step 4.5 — "Add experiment config").
 *
 * `experiments.json` is the single source of truth (validated by
 * lib/experiments.test.ts in CI). This module is the app-side loader — the
 * only file that imports the JSON, so the pure framework stays free of
 * bundler-specific imports.
 *
 * Honest limitation: the config ships with the bundle. Enabling, changing
 * or ending an experiment requires an app release — there is deliberately
 * no remote-config fetch (a remote flag service is a new dependency and a
 * new attack surface; the plan's constraints forbid new packages).
 *
 * A broken config degrades to "no experiments" in the app (control for
 * everything) and fails loudly in CI instead — the test reads the file
 * directly and throws on any validation error.
 */
import raw from '../experiments.json';
import {
  assignVariant,
  experimentNamed,
  parseExperiments,
  type Assignment,
  type ExperimentConfig,
} from './experiments';

let parsed: ExperimentConfig[] = [];
try {
  parsed = parseExperiments(raw);
} catch (err) {
  // Never crash the app over experiment config; CI fails on this instead.
  console.warn('experiments.json invalid — running with no experiments:', err);
  parsed = [];
}

export const EXPERIMENTS: ExperimentConfig[] = parsed;

/**
 * Assignment for one experiment for a given seed (user id, or the session
 * id for signed-out visitors). Null = not participating → control.
 */
export function assignmentFor(name: string, seed: string): Assignment | null {
  const cfg = experimentNamed(EXPERIMENTS, name);
  if (!cfg) return null;
  return assignVariant(cfg, seed);
}

/**
 * AI Picks surfacing helpers (plan step 3.3).
 *
 * The AI Pick flag lives in the jsonb `stats` blob (`stats.is_ai_pick`),
 * written by ai/model/predictor.py when certainty clears the pick bar —
 * there is no column, and `certainty` is the number that actually matters
 * for ranking. Keeping the logic here (rather than inline in the home
 * screen) means the filter, the section ordering and the confidence tones
 * are unit-testable on plain Node — wired into test-app.yml.
 *
 *   tsc statwise/lib/aiPicks.ts statwise/lib/aiPicks.test.ts \
 *       --outDir <dir> --module commonjs --target es2019 --strict --skipLibCheck
 *   node <dir>/aiPicks.test.js
 */

/** The slice of a prediction row these helpers read. */
export type AiPickRow = {
  stats?: {
    is_ai_pick?: boolean;
    certainty?: number;
  } | null;
  confidence?: number | null;
};

/** Is this row one of Pico's AI Picks? */
export function isAiPick(row: AiPickRow | null | undefined): boolean {
  return row?.stats?.is_ai_pick === true;
}

/**
 * Certainty % used for ranking and display: `stats.certainty` when it is a
 * finite number, otherwise the row's `confidence`, otherwise null (never a
 * fabricated 0 — a missing number must not look like "no confidence").
 */
export function certaintyOf(row: AiPickRow | null | undefined): number | null {
  const c = row?.stats?.certainty;
  if (typeof c === 'number' && Number.isFinite(c)) return c;
  const conf = row?.confidence;
  if (typeof conf === 'number' && Number.isFinite(conf)) return conf;
  return null;
}

/** AI Picks only, highest certainty first. Stable: ties keep input order. */
export function aiPicksOf<T extends AiPickRow>(list: readonly T[]): T[] {
  return list
    .map((row, index) => ({ row, index }))
    .filter(({ row }) => isAiPick(row))
    .sort((a, b) => {
      const ca = certaintyOf(a.row) ?? -1;
      const cb = certaintyOf(b.row) ?? -1;
      return cb - ca || a.index - b.index;
    })
    .map(({ row }) => row);
}

/**
 * Visual tone for a certainty number — one definition for every badge and
 * bar so the section, the card and the modal never disagree.
 *   high ≥ 65 (the AI Pick bar), mid ≥ 50, low below that.
 */
export function certaintyTone(value: number | null): 'high' | 'mid' | 'low' {
  if (value === null || !Number.isFinite(value)) return 'low';
  if (value >= 65) return 'high';
  if (value >= 50) return 'mid';
  return 'low';
}

/** Chip label: "3 AI Picks" / "1 AI Pick" / "No AI Picks yet". */
export function aiPicksLabel(count: number): string {
  if (count <= 0) return 'No AI Picks yet';
  if (count === 1) return '1 AI Pick';
  return `${count} AI Picks`;
}

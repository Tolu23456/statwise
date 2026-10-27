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

/** Inputs for {@link aiFilterEmptyCopy} — all counts come from real state. */
export type AiFilterEmptyInput = {
  /** AI Picks in the day's full feed (before the AI/league/search filters). */
  aiCount: number;
  /** Total predictions in the day's full feed (before filters). */
  total: number;
  /** True when a league tab other than "All" is active. */
  leagueNarrowed: boolean;
  /** Active search text; '' (or whitespace) when no search. */
  search: string;
};

/**
 * Empty-state copy for the AI Picks filter (follow-up 2026-10-27).
 *
 * Users read an empty filtered list as "the toggle is inverted" unless the
 * screen says *why* it is empty, so the three honest reasons get distinct
 * copy, each carrying the real counts:
 *   1. no AI Picks flagged today — nothing is being hidden from the user,
 *   2. AI Picks exist but a league tab hides them,
 *   3. AI Picks exist but the search matches none of them,
 * plus the edge where the day's feed itself is empty. Pure counts-in,
 * copy-out — unit-tested on plain Node like the rest of this module.
 */
export function aiFilterEmptyCopy(
  input: AiFilterEmptyInput,
): { title: string; description: string } {
  const total = Math.max(0, Math.floor(input.total));
  const aiCount = Math.max(0, Math.floor(input.aiCount));
  const query = input.search.trim();

  if (aiCount <= 0 && total <= 0) {
    return {
      title: 'No predictions today',
      description:
        'There are no predictions for today yet — pull down to refresh. AI Picks appear here once Pico flags one.',
    };
  }

  if (query) {
    return {
      title: 'No AI Picks found',
      description:
        aiCount <= 0
          ? `Pico hasn't flagged an AI Pick today, so nothing matches "${query}" either — clear the search to see all ${total} predictions.`
          : `None of today's ${aiPicksLabel(aiCount)} match "${query}" — clear the search to see ${aiCount === 1 ? 'it' : 'them'}.`,
    };
  }

  if (aiCount <= 0) {
    return {
      title: 'No AI Picks today',
      description:
        `Pico flags a match only when his certainty clears the bar, and ` +
        `he hasn't flagged one today — all ${total} predictions show ` +
        `with the filter off.`,
    };
  }

  if (input.leagueNarrowed) {
    return {
      title: 'No AI Picks in this league',
      description:
        `Today's ${aiPicksLabel(aiCount)} ${aiCount === 1 ? 'is' : 'are'} ` +
        `under other league tabs — switch to All leagues or turn the ` +
        `filter off.`,
    };
  }

  return {
    title: 'No AI Picks here yet',
    description:
      `Today has ${aiPicksLabel(aiCount)}, but the current filters hide ` +
      `${aiCount === 1 ? 'it' : 'them'} — tap Show all predictions to ` +
      `see the day.`,
  };
}

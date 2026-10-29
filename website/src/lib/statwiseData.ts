// Live data layer for the marketing site — plain `fetch` against the same
// Supabase REST endpoint the app uses, with the same public anon key.
// Zero new packages: this runs client-side in the browser (the site is a
// static export, so there is no server to hide the key behind — and the
// anon key is already public inside the app bundle anyway).

// Re-use the app's team-logo map directly (single source of truth, no copy).
import { getTeamLogo } from '../../../statwise/lib/teamLogos';

export { getTeamLogo };

const SUPABASE_URL = 'https://fwpriiugfjhtoymcbmso.supabase.co';
const ANON_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZ3cHJpaXVnZmpodG95bWNibXNvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTY5MDc3MTgsImV4cCI6MjA3MjQ4MzcxOH0.Sy3iehhGx2UNi28UBEjfNd81TjMxOVXJjY9HJS3xVjs';

/** A fixture on the board — slim projection of a `predictions` row. */
export type UpcomingPick = {
  id: string;
  home_team: string;
  away_team: string;
  league: string;
  prediction: string;
  confidence: number;
  odds: number | null;
  kickoff_time: string;
  is_ai_pick: boolean;
  value_edge: number | null;
};

/** One settled pick used to derive the published hit rate. */
export type SettledPick = {
  prediction: string;
  home_score: number;
  away_score: number;
};

export type AccuracyStats = {
  /** Settled picks inside the window (denominator). */
  total: number;
  /** Picks where the model's call matched the score-derived result. */
  hits: number;
  /** 0–100, one decimal; 0 when there is no sample. */
  pct: number;
  /** Per-pick-type hit rates (only types with at least one sample). */
  byType: { type: string; total: number; hits: number; pct: number }[];
};

/** The published window: rolling30 days of settled fixtures. */
export const ACCURACY_WINDOW_DAYS = 30;

/** Per-request budget: a stalled connection must not leave the skeleton up. */
const REST_TIMEOUT_MS = 10_000;
/** Response-size cap — every query here is limit-bounded, so real payloads
 *  are far smaller; anything bigger is treated as bad input. */
const REST_MAX_CHARS = 1_000_000;

async function rest<T>(query: string): Promise<T | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REST_TIMEOUT_MS);
  try {
    const res = await fetch(`${SUPABASE_URL}/rest/v1/predictions?${query}`, {
      headers: { apikey: ANON_KEY, Authorization: `Bearer ${ANON_KEY}` },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const text = await res.text();
    if (text.length > REST_MAX_CHARS) return null; // size guard
    return JSON.parse(text) as T;
  } catch {
    // Network/offline/timeout/oversized — callers render their fallback,
    // never a broken page.
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Fixtures the model has already priced, soonest first. */
export async function fetchUpcoming(limit = 8): Promise<UpcomingPick[]> {
  const now = new Date().toISOString();
  const rows = await rest<
    {
      id: string;
      home_team: string;
      away_team: string;
      league: string;
      prediction: string;
      confidence: number;
      odds: number | null;
      kickoff_time: string;
      is_ai_pick: boolean | null;
      value_edge: number | null;
    }[]
  >(
    'select=id,home_team,away_team,league,prediction,confidence,odds,kickoff_time,' +
      'stats->is_ai_pick,stats->value_edge' +
      `&kickoff_time=gte.${encodeURIComponent(now)}` +
      '&order=kickoff_time.asc' +
      `&limit=${limit}`,
  );
  if (!rows) return [];
  return rows.map(r => ({
    id: r.id,
    home_team: r.home_team,
    away_team: r.away_team,
    league: r.league,
    prediction: r.prediction,
    confidence: r.confidence,
    odds: r.odds,
    kickoff_time: r.kickoff_time,
    is_ai_pick: Boolean(r.is_ai_pick),
    value_edge: r.value_edge ?? null,
  }));
}

/** The score-derived outcome of a settled fixture (mirrors settle logic). */
export function outcomeOf(p: SettledPick): string {
  if (p.home_score > p.away_score) return 'Home Win';
  if (p.away_score > p.home_score) return 'Away Win';
  return 'Draw';
}

/** Rolling-window hit rate over settled fixtures with a recorded score. */
export async function fetchAccuracy(
  days = ACCURACY_WINDOW_DAYS,
): Promise<AccuracyStats | null> {
  const since = new Date(Date.now() - days * 86400_000)
    .toISOString()
    .slice(0, 10);
  const rows = await rest<SettledPick[]>(
    'select=prediction,home_score,away_score' +
      `&match_date=gte.${since}` +
      '&status=eq.completed' +
      '&home_score=not.is.null' +
      '&limit=500',
  );
  if (!rows || rows.length === 0) return null;

  let hits = 0;
  const byType = new Map<string, { total: number; hits: number }>();
  for (const r of rows) {
    const hit = r.prediction === outcomeOf(r);
    if (hit) hits += 1;
    const slot = byType.get(r.prediction) ?? { total: 0, hits: 0 };
    slot.total += 1;
    if (hit) slot.hits += 1;
    byType.set(r.prediction, slot);
  }
  return {
    total: rows.length,
    hits,
    pct: Math.round((1000 * hits) / rows.length) / 10,
    byType: Array.from(byType.entries())
      .map(([type, s]) => ({
        type,
        total: s.total,
        hits: s.hits,
        pct: Math.round((1000 * s.hits) / s.total) / 10,
      }))
      .sort((a, b) => b.total - a.total),
  };
}

/** How many fixtures on the board currently carry a positive value edge. */
export async function fetchValueFindCount(): Promise<number | null> {
  const now = new Date().toISOString();
  const rows = await rest<{ value_edge: number | null }[]>(
    'select=stats->value_edge' +
      `&kickoff_time=gte.${encodeURIComponent(now)}` +
      '&limit=200',
  );
  if (!rows) return null;
  return rows.filter(r => (r.value_edge ?? 0) > 0).length;
}

/** Client-side date label: "Sat 31 Oct · 12:30" in the viewer's timezone. */
export function formatKickoff(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

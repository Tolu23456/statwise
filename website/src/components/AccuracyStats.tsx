'use client';

import { useEffect, useState } from 'react';
import { Target, CalendarDays, TrendingUp } from 'lucide-react';
import {
  ACCURACY_WINDOW_DAYS,
  fetchAccuracy,
  fetchUpcoming,
  fetchValueFindCount,
  type AccuracyStats,
} from '@/lib/statwiseData';

/**
 * Analytics strip computed live from the app's own settled predictions —
 * the same rows the in-app backtesting reads, so the website and the app
 * never disagree about the hit rate. Degrades tile-by-tile: a failed query
 * shows "—" instead of a fabricated number.
 */
export function AccuracyStats({ compact = false }: { compact?: boolean }) {
  const [stats, setStats] = useState<AccuracyStats | null>(null);
  const [board, setBoard] = useState<number | null>(null);
  const [valueFinds, setValueFinds] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const [acc, upcoming, values] = await Promise.all([
        fetchAccuracy(),
        fetchUpcoming(50),
        fetchValueFindCount(),
      ]);
      if (!alive) return;
      setStats(acc);
      setBoard(upcoming.length);
      setValueFinds(values);
    };
    load();
    const timer = setInterval(load, 10 * 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const na = (v: number | null, suffix = '') =>
    v === null ? '—' : `${v}${suffix}`;

  return (
    <div className={compact ? 'grid grid-cols-3 gap-3' : 'grid md:grid-cols-3 gap-6'}>
      <StatTile
        icon={<Target className="w-5 h-5" />}
        label={`${ACCURACY_WINDOW_DAYS}-day hit rate`}
        value={stats ? `${stats.pct}%` : '—'}
        sub={
          stats
            ? `${stats.hits}/${stats.total} settled picks correct`
            : 'loading model results…'
        }
        bar={stats ? stats.pct : null}
        compact={compact}
      />
      <StatTile
        icon={<CalendarDays className="w-5 h-5" />}
        label="Fixtures on the board"
        value={na(board)}
        sub="upcoming matches priced right now"
        compact={compact}
      />
      <StatTile
        icon={<TrendingUp className="w-5 h-5" />}
        label="Value finds"
        value={na(valueFinds)}
        sub="picks where the model beats the market"
        compact={compact}
      />
      {!compact && stats && stats.byType.length > 1 && (
        <div className="md:col-span-3 rounded-xl border border-border bg-card p-5">
          <div className="text-xs uppercase tracking-wider text-gray-500 mb-4">
            Hit rate by call · last {ACCURACY_WINDOW_DAYS} days
          </div>
          <div className="grid sm:grid-cols-3 gap-4">
            {stats.byType.map(row => (
              <div key={row.type}>
                <div className="flex justify-between text-sm mb-1.5">
                  <span className="text-gray-400">{row.type}</span>
                  <span className="font-semibold text-white">
                    {row.pct}%
                  </span>
                </div>
                <div className="h-1.5 rounded-full bg-border overflow-hidden">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${row.pct}%` }}
                  />
                </div>
                <div className="text-[11px] text-gray-600 mt-1">
                  {row.hits}/{row.total} correct
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatTile({
  icon,
  label,
  value,
  sub,
  bar,
  compact,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  bar?: number | null;
  compact?: boolean;
}) {
  return (
    <div
      className={`rounded-xl border border-border bg-card ${
        compact ? 'p-4' : 'p-6'
      } hover:border-primary/40 transition`}
    >
      <div className="flex items-center gap-2 text-primary mb-2">
        {icon}
        <span
          className={`uppercase tracking-wider text-gray-500 ${
            compact ? 'text-[10px]' : 'text-xs'
          }`}
        >
          {label}
        </span>
      </div>
      <div
        className={`font-bold text-white ${
          compact ? 'text-2xl' : 'text-4xl'
        }`}
      >
        {value}
      </div>
      <div className={`text-gray-400 mt-1 ${compact ? 'text-[11px]' : 'text-sm'}`}>
        {sub}
      </div>
      {bar != null && (
        <div className="mt-3 h-1.5 rounded-full bg-border overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary to-emerald-400"
            style={{ width: `${Math.max(2, Math.min(100, bar))}%` }}
          />
        </div>
      )}
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { Sparkles, RefreshCw } from 'lucide-react';
import {
  fetchUpcoming,
  formatKickoff,
  getTeamLogo,
  type UpcomingPick,
} from '@/lib/statwiseData';

/**
 * Live fixture board pulled from the app's predictions table (same Supabase
 * source the app renders). Client-side fetch so the static export always
 * shows current data — no redeploy needed. Refreshes every 5 minutes and
 * degrades to a friendly empty state if the network says no.
 */
export function LivePredictions({
  limit = 8,
  compact = false,
}: {
  limit?: number;
  compact?: boolean;
}) {
  const [picks, setPicks] = useState<UpcomingPick[] | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const rows = await fetchUpcoming(limit);
      if (!alive) return;
      setPicks(rows);
      setUpdatedAt(new Date());
    };
    load();
    const timer = setInterval(load, 5 * 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [limit]);

  if (picks === null) {
    // Loading: structural skeleton, not a spinner.
    return (
      <div className={compact ? 'grid md:grid-cols-3 gap-4' : 'space-y-4'}>
        {Array.from({ length: compact ? 3 : 5 }).map((_, i) => (
          <div
            key={i}
            className="animate-pulse rounded-xl border border-border bg-card p-5"
          >
            <div className="flex items-center justify-between gap-4">
              <div className="h-10 w-40 rounded-lg bg-border/60" />
              <div className="h-6 w-24 rounded-lg bg-border/60" />
              <div className="h-10 w-40 rounded-lg bg-border/60" />
            </div>
            <div className="mt-4 h-3 w-56 rounded bg-border/40" />
          </div>
        ))}
      </div>
    );
  }

  if (picks.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-card p-8 text-center text-gray-400">
        The board is being refreshed for the next slate of fixtures — open the
        app for the full schedule.
      </div>
    );
  }

  return (
    <div>
      <div className={compact ? 'grid md:grid-cols-3 gap-4' : 'space-y-4'}>
        {picks.map(p => (
          <PickCard key={p.id} pick={p} compact={compact} />
        ))}
      </div>
      {updatedAt && (
        <div className="mt-4 flex items-center gap-2 text-xs text-gray-500">
          <RefreshCw className="w-3 h-3" />
          Live from the StatWise app · updated{' '}
          {updatedAt.toLocaleTimeString('en-GB', {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
      )}
    </div>
  );
}

function PickCard({ pick, compact }: { pick: UpcomingPick; compact: boolean }) {
  const homeLogo = getTeamLogo(pick.home_team);
  const awayLogo = getTeamLogo(pick.away_team);
  const positive = (pick.value_edge ?? 0) > 0;

  return (
    <div className="rounded-xl border border-border bg-card p-4 sm:p-5 hover:border-primary/50 transition group">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[11px] uppercase tracking-wider text-gray-500">
          {pick.league}
        </span>
        <span className="text-[11px] text-gray-500">
          {formatKickoff(pick.kickoff_time)}
        </span>
      </div>

      <div className="flex items-center gap-3">
        <TeamSide logo={homeLogo} name={pick.home_team} align="left" />
        <div className="flex flex-col items-center shrink-0 px-1">
          <span className="text-xs font-semibold text-gray-500">VS</span>
          {pick.odds != null && (
            <span className="text-[11px] text-primary mt-1">
              @{pick.odds.toFixed(2)}
            </span>
          )}
        </div>
        <TeamSide logo={awayLogo} name={pick.away_team} align="right" />
      </div>

      <div className="mt-4 flex items-center gap-2 flex-wrap">
        <span className="px-2.5 py-1 rounded-lg bg-primary/15 border border-primary/30 text-primary text-xs font-semibold">
          {pick.prediction}
        </span>
        {pick.is_ai_pick && (
          <span className="px-2.5 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-xs font-semibold inline-flex items-center gap-1">
            <Sparkles className="w-3 h-3" />
            AI Pick
          </span>
        )}
        {positive && (
          <span className="px-2.5 py-1 rounded-lg bg-purple-500/15 border border-purple-500/30 text-purple-300 text-xs font-semibold">
            Value +{pick.value_edge!.toFixed(1)}
          </span>
        )}
        {!compact && (
          <span className="ml-auto text-xs text-gray-500">
            confidence {pick.confidence}%
          </span>
        )}
      </div>

      {!compact && (
        <div className="mt-3 h-1.5 rounded-full bg-border overflow-hidden">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary to-purple-400 transition-all"
            style={{ width: `${Math.max(4, Math.min(100, pick.confidence))}%` }}
          />
        </div>
      )}
    </div>
  );
}

function TeamSide({
  logo,
  name,
  align,
}: {
  logo: string | null;
  name: string;
  align: 'left' | 'right';
}) {
  return (
    <div
      className={`flex items-center gap-2.5 min-w-0 flex-1 ${
        align === 'right' ? 'flex-row-reverse text-right' : 'text-left'
      }`}
    >
      {logo ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={logo}
          alt=""
          className="w-9 h-9 shrink-0 object-contain"
          loading="lazy"
          onError={e => {
            (e.currentTarget as HTMLImageElement).style.visibility = 'hidden';
          }}
        />
      ) : (
        <div className="w-9 h-9 shrink-0 rounded-full border border-border bg-background flex items-center justify-center text-gray-500 text-xs">
          ⚽
        </div>
      )}
      <span className="text-sm font-medium text-white truncate">{name}</span>
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { Newspaper, RefreshCw } from 'lucide-react';
import bundled from '@/data/news.json';
import { NEWS_DISPLAY_COUNT, NEWS_JSON_URL } from '@/lib/siteConfig';

type NewsItem = {
  slug: string;
  title: string;
  url: string;
  image: string;
  excerpt: string;
  published_label: string;
  published_iso: string;
  byline: string;
};

/**
 * Football news & insights — headlines, excerpts and thumbnails syndicated
 * from SportyTrader's public news listing (scraped hourly by the
 * sync-news workflow into website/src/data/news.json).
 *
 * Two layers, so the section is NEVER empty:
 *   1. bundled snapshot (committed at build time) renders instantly;
 *   2. a runtime fetch of the raw file on GitHub picks up every sync
 *      WITHOUT a redeploy (raw.githubusercontent serves `Access-Control-
 *      Allow-Origin: *`, so plain fetch works from the static export).
 * Every card links to the ORIGINAL article with attribution — we carry
 * the headline and a short teaser only, never full article text.
 */
export function NewsFeed() {
  const [items, setItems] = useState<NewsItem[]>(bundled.items);
  const [syncedAt, setSyncedAt] = useState<string>(bundled.synced_at);
  const [live, setLive] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(NEWS_JSON_URL, { cache: 'no-store' });
        if (!res.ok) return;
        const data = (await res.json()) as { items?: NewsItem[]; synced_at?: string };
        if (alive && Array.isArray(data.items) && data.items.length > 0) {
          setItems(data.items);
          if (data.synced_at) setSyncedAt(data.synced_at);
          setLive(true);
        }
      } catch {
        // Offline / raw host unreachable — the bundled snapshot stays.
      }
    };
    load();
    const timer = setInterval(load, 30 * 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const shown = items.slice(0, NEWS_DISPLAY_COUNT);
  const syncedLabel = syncedAt
    ? new Date(syncedAt).toLocaleString('en-GB', {
        day: 'numeric',
        month: 'short',
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'UTC',
      })
    : '';

  return (
    <div>
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
        {shown.map(item => (
          <a
            key={item.slug}
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="group rounded-xl border border-border bg-card overflow-hidden hover:border-primary/50 transition flex flex-col"
          >
            <div className="relative aspect-[16/9] bg-background">
              {item.image ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={item.image}
                  alt=""
                  loading="lazy"
                  className="w-full h-full object-cover group-hover:scale-[1.03] transition duration-300"
                  onError={e => {
                    // No thumbnail → branded fallback tile, layout intact.
                    const el = e.currentTarget as HTMLImageElement;
                    el.style.display = 'none';
                  }}
                />
              ) : null}
              <span className="absolute left-2 top-2 px-2 py-0.5 rounded bg-black/70 text-[10px] uppercase tracking-wider text-gray-300">
                {item.published_label.split(' · ')[0]}
              </span>
            </div>
            <div className="p-4 flex flex-col gap-2 flex-1">
              <h3 className="font-semibold leading-snug group-hover:text-primary transition line-clamp-2">
                {item.title}
              </h3>
              <p className="text-sm text-gray-400 line-clamp-3 flex-1">
                {item.excerpt}
              </p>
              <div className="flex items-center justify-between text-xs text-gray-500 pt-1 border-t border-border/60">
                <span>
                  {item.byline ? `by ${item.byline} · ` : ''}via SportyTrader
                </span>
                <span className="text-primary font-semibold group-hover:underline">
                  Read original →
                </span>
              </div>
            </div>
          </a>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-500">
        <span className="inline-flex items-center gap-2">
          <Newspaper className="w-3.5 h-3.5" />
          Headlines syndicated from{' '}
          <a
            href="https://www.sportytrader.com/en/news/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-400 hover:text-primary underline"
          >
            SportyTrader
          </a>{' '}
          — every story links to the original article.
        </span>
        <span className="inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" />
          Updated {syncedLabel} UTC{live ? '' : ' · snapshot'}
        </span>
      </div>
    </div>
  );
}

'use client';

import { useEffect, useState } from 'react';
import bundled from '@/data/news.json';
import { NEWS_JSON_URL } from '@/lib/siteConfig';

/**
 * One story as stored in website/src/data/news.json: listing metadata
 * plus `body` — the story's opening text, fetched by the sync-news
 * workflow so readers get the text HERE instead of on the source site
 * (source credited + linked on every story view).
 */
export type NewsItem = {
  slug: string;
  title: string;
  url: string;
  /** Publisher name, e.g. "Sky Sports" / "SportyTrader". */
  source: string;
  image: string;
  excerpt: string;
  published_label: string;
  published_iso: string;
  byline: string;
  body?: string[];
  /** 'video' when the original is a video page (watched on the source). */
  kind?: string;
};

export type NewsFeedState = {
  items: NewsItem[];
  /** Publisher name → its news-desk home URL (per-story credit links). */
  sources: Record<string, string>;
  syncedAt: string;
  live: boolean;
};

type FeedPayload = {
  items?: NewsItem[];
  sources?: Record<string, string>;
  synced_at?: string;
};

/**
 * Single source of truth for news data, shared by the home-page feed and
 * the /news reader:
 *   1. the bundled snapshot (committed at build time) renders instantly;
 *   2. a runtime fetch of the raw file on GitHub picks up every sync
 *      WITHOUT a redeploy (raw.githubusercontent serves `Access-Control-
 *      Allow-Origin: *`), re-checking every 30 minutes.
 */
export function useNewsFeed(): NewsFeedState {
  const initial = bundled as FeedPayload;
  const [items, setItems] = useState<NewsItem[]>(initial.items ?? []);
  const [sources, setSources] = useState<Record<string, string>>(
    initial.sources ?? {}
  );
  const [syncedAt, setSyncedAt] = useState<string>(initial.synced_at ?? '');
  const [live, setLive] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch(NEWS_JSON_URL, { cache: 'no-store' });
        if (!res.ok) return;
        const data = (await res.json()) as FeedPayload;
        if (alive && Array.isArray(data.items) && data.items.length > 0) {
          setItems(data.items);
          if (data.sources) setSources(data.sources);
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

  return { items, sources, syncedAt, live };
}

'use client';

import { useEffect, useState } from 'react';
import bundled from '@/data/news.json';
import { NEWS_JSON_URL } from '@/lib/siteConfig';
import { safeHttpUrl } from '@/lib/urlSafety';

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

export type FeedPayload = {
  items?: NewsItem[];
  sources?: Record<string, string>;
  synced_at?: string;
};

/** Runtime fetch budget — a stalled connection must not hang the feed. */
const NEWS_FETCH_TIMEOUT_MS = 10_000;
/** Hard cap on the feed document (the real file is well under 1 MB). */
const NEWS_MAX_CHARS = 2_000_000;

/**
 * news.json is untrusted: written by a scraping script and refreshed at
 * runtime from raw.githubusercontent.com. Sanitise BOTH sources identically
 * — only http(s) URLs survive (blocks `javascript:`/`data:` hrefs before
 * they can reach the DOM; React only *warns* about those), and structural
 * fields are coerced so a corrupt feed degrades instead of crashing.
 */
function sanitizeFeed(payload: FeedPayload | null | undefined): FeedPayload {
  if (!payload || typeof payload !== 'object') return { items: [], sources: {} };

  const rawItems = Array.isArray(payload.items) ? payload.items : [];
  const items: NewsItem[] = [];
  for (const raw of rawItems) {
    if (!raw || typeof raw !== 'object') continue;
    const url = safeHttpUrl(raw.url) ?? '';
    // A story with an unsafe link still renders — the card links to the
    // in-site reader; only the "View original" anchor is dropped.
    items.push({
      ...raw,
      slug: String(raw.slug ?? ''),
      title: String(raw.title ?? '').slice(0, 300),
      url,
      source: String(raw.source ?? ''),
      image: safeHttpUrl(raw.image) ?? '',
      excerpt: String(raw.excerpt ?? ''),
      published_label: String(raw.published_label ?? ''),
      published_iso: String(raw.published_iso ?? ''),
      byline: String(raw.byline ?? ''),
    });
  }

  const sources: Record<string, string> = {};
  if (payload.sources && typeof payload.sources === 'object') {
    for (const [name, home] of Object.entries(payload.sources)) {
      const safe = safeHttpUrl(home);
      if (safe) sources[String(name)] = safe;
    }
  }

  return {
    items,
    sources,
    synced_at: typeof payload.synced_at === 'string' ? payload.synced_at : '',
  };
}

/**
 * Single source of truth for news data, shared by the home-page feed and
 * the /news reader:
 *   1. the snapshot bundled at build time (passed in by the server-rendered
 *      /news page, or the committed news.json import) renders instantly;
 *   2. a runtime fetch of the raw file on GitHub picks up every sync
 *      WITHOUT a redeploy (raw.githubusercontent serves `Access-Control-
 *      Allow-Origin: *`), re-checking every 30 minutes — with a 10 s
 *      abort timeout and a response-size cap so a stalled or hostile
 *      response can never hang the page.
 */
export function useNewsFeed(initial?: FeedPayload): NewsFeedState {
  const [feed, setFeed] = useState<FeedPayload>(() =>
    sanitizeFeed(initial ?? (bundled as FeedPayload)),
  );
  const [live, setLive] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), NEWS_FETCH_TIMEOUT_MS);
      try {
        const res = await fetch(NEWS_JSON_URL, {
          cache: 'no-store',
          signal: controller.signal,
        });
        if (!res.ok) return;
        const text = await res.text();
        if (text.length > NEWS_MAX_CHARS) return; // size guard
        const data = JSON.parse(text) as FeedPayload;
        if (alive && Array.isArray(data.items) && data.items.length > 0) {
          setFeed(sanitizeFeed(data));
          setLive(true);
        }
      } catch {
        // Offline / timeout / oversized / unparsable — bundled snapshot stays.
      } finally {
        clearTimeout(timer);
      }
    };
    load();
    const timer = setInterval(load, 30 * 60_000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return {
    items: feed.items ?? [],
    sources: feed.sources ?? {},
    syncedAt: feed.synced_at ?? '',
    live,
  };
}

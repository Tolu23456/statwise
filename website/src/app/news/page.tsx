import fs from 'fs';
import path from 'path';
import type { Metadata } from 'next';
import { NewsReader } from '@/components/NewsReader';
import { SiteHeader } from '@/components/SiteHeader';
import { SiteFooter } from '@/components/SiteFooter';
import { pageMetadata } from '@/lib/seo';
import type { FeedPayload } from '@/lib/useNewsFeed';

export const metadata: Metadata = pageMetadata({
  title: 'Football News & Insights',
  description:
    'Football news synced from Sky Sports and SportyTrader: headlines, images, bylines, video stories and story text, read inside StatWise — with the source credited on every story.',
  path: '/news/',
});

/**
 * The site's news reader (static route + client-side view state, so the
 * page works with `output: 'export'` and picks up newly synced stories
 * without a redeploy):
 *   /news/          → full list of synced stories
 *   /news/?a=slug   → one story, read here (source credited + linked)
 *
 * This is a SERVER component: it reads the news.json snapshot written by
 * website/scripts/sync_news.py synchronously at BUILD time and hands it to
 * the reader, so the exported HTML contains real story cards (and exactly
 * one <h1>) instead of the old `animate-pulse` skeleton. If the file is
 * missing or unreadable the page degrades to a graceful empty state — the
 * build never fails on absent data.
 */
function loadNewsFeed(): FeedPayload {
  try {
    const file = path.join(process.cwd(), 'src', 'data', 'news.json');
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8')) as FeedPayload;
    return parsed && typeof parsed === 'object' ? parsed : { items: [], sources: {} };
  } catch {
    // Absent/corrupt snapshot → the reader renders its empty state.
    return { items: [], sources: {}, synced_at: '' };
  }
}

export default function NewsPage() {
  const feed = loadNewsFeed();

  return (
    <>
      <SiteHeader active="news" />
      <main className="min-h-screen">
        <NewsReader initial={feed} />
      </main>
      <SiteFooter />
    </>
  );
}

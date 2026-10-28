'use client';

import Link from 'next/link';
import { ArrowRight, Newspaper, RefreshCw } from 'lucide-react';
import { useNewsFeed } from '@/lib/useNewsFeed';
import { NEWS_DISPLAY_COUNT } from '@/lib/siteConfig';

/**
 * Football news preview for the home page — headlines, thumbnails and
 * teasers synced from SportyTrader into website/src/data/news.json.
 *
 * Reading happens INSIDE this site: cards open /news/?a=<slug>, the
 * site's own reader (headline, image, byline, date and the story's
 * opening text), which credits the source and links to the original.
 * The feed never navigates readers away.
 */
export function NewsFeed() {
  const { items, syncedAt, live } = useNewsFeed();

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
          <Link
            key={item.slug}
            href={`/news/?a=${item.slug}`}
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
                    (e.currentTarget as HTMLImageElement).style.display = 'none';
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
                <span className="text-primary font-semibold group-hover:underline inline-flex items-center gap-1">
                  Read <ArrowRight className="w-3 h-3" />
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-500">
        <span className="inline-flex items-center gap-2">
          <Newspaper className="w-3.5 h-3.5" />
          Stories from{' '}
          <a
            href="https://www.sportytrader.com/en/news/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-400 hover:text-primary underline"
          >
            SportyTrader
          </a>
          , read right here on StatWise — source credited on every story.
        </span>
        <span className="inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" />
          Updated {syncedLabel} UTC{live ? '' : ' · snapshot'}
        </span>
      </div>

      <div className="mt-4">
        <Link
          href="/news"
          className="text-primary text-sm font-semibold hover:underline inline-flex items-center gap-1"
        >
          All news <ArrowRight className="w-4 h-4" />
        </Link>
      </div>
    </div>
  );
}

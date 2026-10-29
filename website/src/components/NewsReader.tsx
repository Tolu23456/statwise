'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  ArrowLeft,
  ArrowRight,
  Newspaper,
  PlayCircle,
  RefreshCw,
  UserRound,
} from 'lucide-react';
import { useNewsFeed, type FeedPayload, type NewsItem } from '@/lib/useNewsFeed';
import { safeHttpUrl, safeUrlOrEmpty } from '@/lib/urlSafety';

/**
 * In-site news reader. `/news/` lists every synced story;
 * `/news/?a=<slug>` renders one story here — image, byline, date and the
 * story's opening text — with source credit + original link at the end.
 *
 * Static-first design for `output: 'export'`: the server-rendered page
 * passes the news.json snapshot from BUILD time in `initial`, and the
 * Suspense fallback below is the REAL list view — so the exported
 * /news/index.html contains the <h1> and every story card (previously it
 * was an `animate-pulse` skeleton because the prerender bailed out).
 * `useSearchParams` (the only bailout) lives inside the boundary: after
 * hydration it resolves to the same list, or to the single-story view for
 * /news/?a=<slug>. New slugs arriving from a sync still work without a
 * redeploy because they resolve client-side.
 */
export function NewsReader({ initial }: { initial?: FeedPayload }) {
  const { items, sources, syncedAt, live } = useNewsFeed(initial);
  const syncedLabel = formatSyncedAt(syncedAt);

  return (
    <Suspense
      fallback={
        <NewsListView items={items} sources={sources} syncedLabel={syncedLabel} live={live} />
      }
    >
      <ReaderContent items={items} sources={sources} syncedLabel={syncedLabel} live={live} />
    </Suspense>
  );
}

function formatSyncedAt(syncedAt: string): string {
  if (!syncedAt) return '';
  return new Date(syncedAt).toLocaleString('en-GB', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  });
}

function ReaderContent({
  items,
  sources,
  syncedLabel,
  live,
}: {
  items: NewsItem[];
  sources: Record<string, string>;
  syncedLabel: string;
  live: boolean;
}) {
  const slug = useSearchParams().get('a');
  const story = slug
    ? items.find(it => it.slug === slug)
    : undefined;

  if (slug && story) {
    return (
      <ArticleView
        story={story}
        sourceHome={sources[story.source]}
        siblings={items.filter(it => it.slug !== story.slug)}
      />
    );
  }

  if (slug && !story) {
    return (
      <div className="px-6 py-16 max-w-3xl mx-auto text-center">
        <h1 className="text-3xl font-bold mb-3">Story not found</h1>
        <p className="text-gray-400 mb-6">
          It may have rotated out of the window. The latest headlines are
          still here.
        </p>
        <Link
          href="/news"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary hover:bg-primary/90 text-white font-semibold transition"
        >
          Browse all news
        </Link>
      </div>
    );
  }

  return (
    <NewsListView
      items={items}
      sources={sources}
      syncedLabel={syncedLabel}
      live={live}
    />
  );
}

/**
 * The story grid — also the prerendered HTML of /news (it doubles as the
 * Suspense fallback), so it always renders the h1 + real cards even before
 * any JavaScript runs.
 */
function NewsListView({
  items,
  sources,
  syncedLabel,
  live,
}: {
  items: NewsItem[];
  sources: Record<string, string>;
  syncedLabel: string;
  live: boolean;
}) {
  const sourceNames = Object.keys(sources);

  return (
    <section className="px-6 py-12 max-w-6xl mx-auto">
      <div className="flex items-center gap-3 mb-2">
        <Newspaper className="w-6 h-6 text-primary" aria-hidden="true" />
        <h1 className="text-3xl sm:text-4xl font-bold">Football News &amp; Insights</h1>
      </div>
      <p className="text-gray-400 max-w-3xl mb-8">
        Headlines, images and story text syndicated from Sky Sports and
        SportyTrader&apos;s newsrooms and synced automatically every two
        hours — but read right here, on StatWise. Match reports, transfer
        stories, video reaction: the context around the fixtures our model
        prices. Every story credits its source and links to the original.
      </p>

      {items.length === 0 ? (
        <div className="rounded-xl border border-border bg-card p-8 text-center">
          <p className="text-gray-400">
            The news desk is between syncs right now — fresh stories land here
            automatically every couple of hours. The sources below publish in
            the meantime.
          </p>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {items.map(item => (
            <Link
              key={`${item.source}-${item.slug}`}
              href={`/news/?a=${item.slug}`}
              className="group rounded-xl border border-border bg-card overflow-hidden hover:border-primary/50 transition flex flex-col"
            >
              <div className="relative aspect-[16/9] bg-background">
                {item.image ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={item.image}
                    alt={item.title}
                    loading="lazy"
                    className="w-full h-full object-cover group-hover:scale-[1.03] transition duration-300"
                    onError={e => {
                      (e.currentTarget as HTMLImageElement).style.display = 'none';
                    }}
                  />
                ) : null}
                {item.kind === 'video' && (
                  <span className="absolute inset-0 flex items-center justify-center bg-black/30" aria-hidden="true">
                    <PlayCircle className="w-10 h-10 text-white/90" />
                  </span>
                )}
                <span className="absolute left-2 top-2 px-2 py-0.5 rounded bg-black/70 text-[10px] uppercase tracking-wider text-gray-300">
                  {item.kind === 'video' ? 'Video' : item.source}
                </span>
              </div>
              <div className="p-4 flex flex-col gap-2 flex-1">
                <h2 className="font-semibold leading-snug group-hover:text-primary transition line-clamp-2">
                  {item.title}
                </h2>
                <p className="text-sm text-gray-400 line-clamp-3 flex-1">
                  {item.excerpt}
                </p>
                <div className="flex items-center justify-between text-xs text-gray-400 pt-1 border-t border-border/60">
                  <span>
                    {item.byline ? `by ${item.byline} · ` : ''}
                    {item.source}
                  </span>
                  <span className="text-primary font-semibold inline-flex items-center gap-1">
                    Read <ArrowRight className="w-3 h-3" aria-hidden="true" />
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-400">
        <span className="inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
          {syncedLabel ? (
            <>Updated {syncedLabel} UTC{live ? '' : ' · snapshot'}</>
          ) : (
            <>Waiting for the first sync</>
          )}
        </span>
        <span>
          Syndicated from{' '}
          {sourceNames.map((name, i) => (
            <span key={name}>
              {i > 0 && (i === sourceNames.length - 1 ? ' & ' : ', ')}
              <a
                href={safeUrlOrEmpty(sources[name]) || undefined}
                target="_blank"
                rel="noopener noreferrer"
                className="text-gray-400 hover:text-primary underline"
              >
                {name}
              </a>
            </span>
          ))}
        </span>
      </div>
    </section>
  );
}

function ArticleView({
  story,
  sourceHome,
  siblings,
}: {
  story: NewsItem;
  sourceHome?: string;
  siblings: NewsItem[];
}) {
  const body =
    story.body && story.body.length > 0 ? story.body : [story.excerpt];
  const isVideo = story.kind === 'video';
  // Scheme-validated at render time too (defense in depth on top of the
  // feed sanitiser): only http(s) may reach an href.
  const originalUrl = safeHttpUrl(story.url);
  const homeUrl = safeHttpUrl(sourceHome);

  return (
    <article className="px-6 py-10 max-w-3xl mx-auto">
      <Link
        href="/news"
        className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition mb-6"
      >
        <ArrowLeft className="w-4 h-4" aria-hidden="true" />
        All news
      </Link>

      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-400 mb-4">
        <span className="px-2.5 py-1 rounded-lg bg-primary/10 border border-primary/30 text-primary font-semibold uppercase tracking-wider inline-flex items-center gap-1">
          {isVideo ? (
            <>
              <PlayCircle className="w-3 h-3" aria-hidden="true" /> Video
            </>
          ) : (
            'News'
          )}
        </span>
        <span>{story.published_label}</span>
        {story.byline && (
          <span className="inline-flex items-center gap-1">
            <UserRound className="w-3.5 h-3.5" aria-hidden="true" />
            {story.byline}
          </span>
        )}
        <span>· {story.source}</span>
      </div>

      <h1 className="text-3xl md:text-4xl font-bold leading-tight mb-6">
        {story.title}
      </h1>

      <div className="relative mb-6">
        {story.image && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={safeUrlOrEmpty(story.image) || undefined}
            alt={story.title}
            className="w-full rounded-xl border border-border"
            onError={e => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
        )}
        {isVideo && (
          <span className="absolute inset-0 flex items-center justify-center pointer-events-none" aria-hidden="true">
            <span className="rounded-full bg-black/60 p-4">
              <PlayCircle className="w-12 h-12 text-white" />
            </span>
          </span>
        )}
      </div>

      <div className="space-y-5">
        {body.map((para, i) => (
          <p
            key={i}
            className={`leading-relaxed ${
              i === 0 ? 'text-gray-200 text-lg' : 'text-gray-400'
            }`}
          >
            {para}
          </p>
        ))}
        {isVideo && (
          <p className="text-sm text-gray-400 italic">
            The video itself plays on {story.source} — follow the link
            below to watch it.
          </p>
        )}
      </div>

      {/* Source credit — the reading happened here; the original stays linked. */}
      <div className="mt-8 rounded-xl border border-border bg-card p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <p className="text-xs text-gray-400">
          {isVideo ? (
            <>
              Video story from {story.source}. Watch it on their site.
            </>
          ) : (
            <>
              Story syndicated from {story.source}. Visit the original for
              any updates or the remainder of the article.
            </>
          )}
        </p>
        {originalUrl && (
          <a
            href={originalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary/15 border border-primary/40 text-primary text-xs font-semibold hover:bg-primary/25 transition shrink-0"
          >
            {isVideo ? `Watch on ${story.source}` : 'View original'}
            {isVideo ? (
              <PlayCircle className="w-3.5 h-3.5" aria-hidden="true" />
            ) : (
              <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
            )}
          </a>
        )}
      </div>

      {homeUrl && (
        <p className="mt-3 text-xs text-gray-400">
          More from{' '}
          <a
            href={homeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-400 hover:text-primary underline"
          >
            {story.source}
          </a>
        </p>
      )}

      {siblings.length > 0 && (
        <div className="mt-12 pt-8 border-t border-border">
          <h2 className="text-xl font-bold mb-4">More stories</h2>
          <div className="grid sm:grid-cols-2 gap-4">
            {siblings.slice(0, 4).map(s => (
              <Link
                key={`${s.source}-${s.slug}`}
                href={`/news/?a=${s.slug}`}
                className="rounded-xl border border-border bg-card p-4 hover:border-primary/50 transition"
              >
                <div className="text-[11px] text-gray-400 mb-1">
                  {s.published_label} · {s.source}
                </div>
                <div className="text-sm font-semibold leading-snug hover:text-primary transition line-clamp-2">
                  {s.title}
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </article>
  );
}

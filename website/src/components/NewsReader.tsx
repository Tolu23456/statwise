'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Newspaper,
  RefreshCw,
  UserRound,
} from 'lucide-react';
import { useNewsFeed, type NewsItem } from '@/lib/useNewsFeed';

/**
 * In-site news reader. `/news/` lists every synced story;
 * `/news/?a=<slug>` renders one story here — image, byline, date and the
 * story's opening text — with source credit + original link at the end.
 *
 * The slug comes from useSearchParams (reactive to same-tab Link
 * navigation; basePath-aware on GitHub Pages) and must live under a
 * Suspense boundary for `output: 'export'` — the bare route exports the
 * fallback HTML and the client resolves the param after hydration. No
 * slug is baked at build time because syncs bring new ones in without a
 * redeploy.
 */
export function NewsReader() {
  const { items, syncedAt, live } = useNewsFeed();
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
    <main className="min-h-screen">
      <nav className="px-6 py-4 border-b border-border sticky top-0 bg-background/90 backdrop-blur z-20">
        <div className="max-w-6xl mx-auto flex justify-between items-center">
          <Link href="/" className="text-xl font-bold inline-flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-primary" />
            StatWise
          </Link>
          <div className="flex gap-6 text-sm">
            <Link href="/predictions" className="text-gray-400 hover:text-white">
              Predictions
            </Link>
            <Link href="/#pricing" className="text-gray-400 hover:text-white">
              Pricing
            </Link>
            <Link href="/news" className="text-white font-semibold">
              News
            </Link>
            <Link href="/download" className="text-primary font-semibold">
              Download
            </Link>
          </div>
        </div>
      </nav>

      <Suspense
        fallback={
          <div className="px-6 py-12 max-w-6xl mx-auto">
            <div className="animate-pulse rounded-xl border border-border bg-card p-6">
              <div className="h-6 w-56 rounded bg-border/60 mb-4" />
              <div className="h-3 w-full rounded bg-border/40 mb-2" />
              <div className="h-3 w-4/5 rounded bg-border/40" />
            </div>
          </div>
        }
      >
        <ReaderContent items={items} syncedLabel={syncedLabel} live={live} />
      </Suspense>
    </main>
  );
}

function ReaderContent({
  items,
  syncedLabel,
  live,
}: {
  items: NewsItem[];
  syncedLabel: string;
  live: boolean;
}) {
  const slug = useSearchParams().get('a');
  const story = slug ? items.find(it => it.slug === slug) : undefined;

  if (slug && story) {
    return (
      <ArticleView
        story={story}
        siblings={items.filter(it => it.slug !== story.slug)}
      />
    );
  }

  if (slug && !story) {
    return (
      <div className="px-6 py-16 max-w-3xl mx-auto text-center">
        <h1 className="text-3xl font-bold mb-3">Story not found</h1>
        <p className="text-gray-400 mb-6">
          It may have rotated out of the 30-story window. The latest
          headlines are still here.
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

  return <ListView items={items} syncedLabel={syncedLabel} live={live} />;
}

function ListView({
  items,
  syncedLabel,
  live,
}: {
  items: NewsItem[];
  syncedLabel: string;
  live: boolean;
}) {
  return (
    <section className="px-6 py-12 max-w-6xl mx-auto">
      <div className="flex items-center gap-3 mb-2">
        <Newspaper className="w-6 h-6 text-primary" />
        <h1 className="text-4xl font-bold">Football News & Insights</h1>
      </div>
      <p className="text-gray-400 max-w-3xl mb-8">
        Headlines, images and story text syndicated from SportyTrader&apos;s
        newsroom and synced automatically every two hours — but read right
        here, on StatWise. Transfers, injuries, tactical previews: the
        context around the fixtures our model prices. Every story credits
        its source and links to the original article.
      </p>

      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
        {items.map(item => (
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
              <h2 className="font-semibold leading-snug group-hover:text-primary transition line-clamp-2">
                {item.title}
              </h2>
              <p className="text-sm text-gray-400 line-clamp-3 flex-1">
                {item.excerpt}
              </p>
              <div className="flex items-center justify-between text-xs text-gray-500 pt-1 border-t border-border/60">
                <span>
                  {item.byline ? `by ${item.byline}` : 'via SportyTrader'}
                </span>
                <span className="text-primary font-semibold inline-flex items-center gap-1">
                  Read <ArrowRight className="w-3 h-3" />
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 text-xs text-gray-500">
        <span className="inline-flex items-center gap-2">
          <RefreshCw className="w-3.5 h-3.5" />
          Updated {syncedLabel} UTC{live ? '' : ' · snapshot'}
        </span>
        <span>
          Syndicated from{' '}
          <a
            href="https://www.sportytrader.com/en/news/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-gray-400 hover:text-primary underline"
          >
            SportyTrader
          </a>
        </span>
      </div>
    </section>
  );
}

function ArticleView({
  story,
  siblings,
}: {
  story: NewsItem;
  siblings: NewsItem[];
}) {
  const body =
    story.body && story.body.length > 0 ? story.body : [story.excerpt];

  return (
    <article className="px-6 py-10 max-w-3xl mx-auto">
      <Link
        href="/news"
        className="inline-flex items-center gap-1.5 text-sm text-gray-400 hover:text-white transition mb-6"
      >
        <ArrowLeft className="w-4 h-4" />
        All news
      </Link>

      <div className="flex flex-wrap items-center gap-3 text-xs text-gray-500 mb-4">
        <span className="px-2.5 py-1 rounded-lg bg-primary/10 border border-primary/30 text-primary font-semibold uppercase tracking-wider">
          News
        </span>
        <span>{story.published_label}</span>
        {story.byline && (
          <span className="inline-flex items-center gap-1">
            <UserRound className="w-3.5 h-3.5" />
            {story.byline}
          </span>
        )}
      </div>

      <h1 className="text-3xl md:text-4xl font-bold leading-tight mb-6">
        {story.title}
      </h1>

      {story.image && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={story.image}
          alt=""
          className="w-full rounded-xl border border-border mb-6"
          onError={e => {
            (e.currentTarget as HTMLImageElement).style.display = 'none';
          }}
        />
      )}

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
      </div>

      {/* Source credit — the reading happened here; the original stays linked. */}
      <div className="mt-8 rounded-xl border border-border bg-card p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <p className="text-xs text-gray-500">
          Story syndicated from SportyTrader. Visit the original for any
          updates or the remainder of the article.
        </p>
        <a
          href={story.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary/15 border border-primary/40 text-primary text-xs font-semibold hover:bg-primary/25 transition shrink-0"
        >
          View original
          <ArrowRight className="w-3.5 h-3.5" />
        </a>
      </div>

      {siblings.length > 0 && (
        <div className="mt-12 pt-8 border-t border-border">
          <h2 className="text-xl font-bold mb-4">More stories</h2>
          <div className="grid sm:grid-cols-2 gap-4">
            {siblings.slice(0, 4).map(s => (
              <Link
                key={s.slug}
                href={`/news/?a=${s.slug}`}
                className="rounded-xl border border-border bg-card p-4 hover:border-primary/50 transition"
              >
                <div className="text-[11px] text-gray-500 mb-1">
                  {s.published_label}
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

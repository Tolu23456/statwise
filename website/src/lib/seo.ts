import type { Metadata } from 'next';
import { SITE_URL } from './siteConfig';

/**
 * Per-page SEO bundle. Every route builds its canonical + OpenGraph +
 * Twitter block from the single SITE_URL constant (siteConfig.ts), so the
 * production origin can never drift between page metadata, robots.txt and
 * the sitemap.
 *
 * The layout title template (`%s - StatWise`) applies to `title` but NOT
 * to openGraph.title — so OG/Twitter get the fully branded string here.
 */
export function pageMetadata({
  title,
  description,
  path,
}: {
  /** <title> text WITHOUT the brand suffix — the layout template adds it. */
  title: string;
  /** Meta description, reused for OG/Twitter. */
  description: string;
  /** Route path with leading slash and a trailing slash (trailingSlash: true), e.g. '/news/'. */
  path: string;
}): Metadata {
  const url = path === '/' ? `${SITE_URL}/` : `${SITE_URL}${path.replace(/\/?$/, '/')}`;
  const fullTitle = `${title} - StatWise`;
  const image = `${SITE_URL}/icon.png`;

  return {
    title,
    description,
    alternates: {
      canonical: url,
    },
    openGraph: {
      type: 'website',
      url,
      siteName: 'StatWise',
      title: fullTitle,
      description,
      images: [
        {
          url: image,
          width: 512,
          height: 512,
          alt: 'StatWise — AI football predictions',
        },
      ],
    },
    twitter: {
      card: 'summary',
      title: fullTitle,
      description,
      images: [image],
    },
  };
}

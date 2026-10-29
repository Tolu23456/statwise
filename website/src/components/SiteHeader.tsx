'use client';

import { useRef } from 'react';
import Link from 'next/link';
import { BarChart3, Menu, X } from 'lucide-react';

/**
 * Shared site header — the single nav for every page (previously each route
 * carried its own slightly-different copy).
 *
 * Responsive behaviour: the five links sit in a horizontal row from `md:`
 * up; below that they collapse into a hamburger panel. The disclosure is a
 * plain `<details>` element, so it works WITHOUT JavaScript (CSS-only) and
 * needs no dependency — React only closes it after a link click so
 * same-page anchors (Pricing/FAQ) don't leave the panel hanging open.
 */

type ActivePage = 'predictions' | 'news' | 'download' | null;

const NAV_LINKS: { href: string; label: string; page: Exclude<ActivePage, null> | null }[] = [
  { href: '/predictions', label: 'Predictions', page: 'predictions' },
  { href: '/news', label: 'News', page: 'news' },
  { href: '/#pricing', label: 'Pricing', page: null },
  { href: '/#faq', label: 'FAQ', page: null },
  { href: '/download', label: 'Download', page: 'download' },
];

export function SiteHeader({ active = null }: { active?: ActivePage }) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  const closeMenu = () => {
    if (detailsRef.current) detailsRef.current.open = false;
  };

  const linkClass = (link: (typeof NAV_LINKS)[number]) => {
    // Keep the original per-page styling: Download is always the primary
    // CTA, the current page is highlighted white, everything else muted.
    if (link.page === 'download') return 'text-primary font-semibold hover:text-primary/80';
    if (link.page && link.page === active) return 'text-white font-semibold';
    return 'text-gray-400 hover:text-white';
  };

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/90 backdrop-blur">
      <nav aria-label="Main" className="relative px-6 py-4">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
          <Link
            href="/"
            className="inline-flex shrink-0 items-center gap-2 text-xl font-bold"
          >
            <BarChart3 className="h-5 w-5 text-primary" aria-hidden="true" />
            StatWise
          </Link>

          {/* Desktop: original horizontal row (unchanged look). */}
          <div className="hidden gap-6 text-sm md:flex">
            {NAV_LINKS.map(link => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={link.page && link.page === active ? 'page' : undefined}
                className={`transition ${linkClass(link)}`}
              >
                {link.label}
              </Link>
            ))}
          </div>

          {/* Mobile: hamburger panel. <details> toggles natively in CSS, so
              the links are reachable even before hydration. */}
          <details ref={detailsRef} className="group md:hidden">
            <summary className="flex h-9 w-9 cursor-pointer list-none items-center justify-center rounded-lg border border-border text-gray-300 transition hover:text-white [&::-webkit-details-marker]:hidden">
              <Menu className="h-5 w-5 group-open:hidden" aria-hidden="true" />
              <X className="hidden h-5 w-5 group-open:block" aria-hidden="true" />
              <span className="sr-only">Open menu</span>
            </summary>
            <div className="absolute left-0 right-0 top-full z-30 flex flex-col border-b border-border bg-background/95 px-6 py-2 backdrop-blur">
              {NAV_LINKS.map(link => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={closeMenu}
                  aria-current={link.page && link.page === active ? 'page' : undefined}
                  className={`py-2.5 text-sm transition ${linkClass(link)}`}
                >
                  {link.label}
                </Link>
              ))}
            </div>
          </details>
        </div>
      </nav>
    </header>
  );
}

'use client';

import Link from 'next/link';
import { useEffect, useRef } from 'react';
import { Download, Megaphone } from 'lucide-react';
import { ADSENSE_CLIENT, AD_SLOTS } from '@/lib/siteConfig';

type Placement = keyof typeof AD_SLOTS;

declare global {
  interface Window {
    adsbygoogle?: unknown[];
  }
}

/**
 * Ad slot: renders a manual Google AdSense unit ONLY when a real slot id
 * exists for the placement (siteConfig.AD_SLOTS). With Auto ads enabled
 * in the dashboard, Google's script places ads on its own — pushing a
 * manual unit with placeholder slot '0' would be an INVALID ad request
 * (console errors, account-quality noise), so placeholder slots render
 * the house ad instead: the layout stays honest and every placement is
 * already wired for when real slot ids arrive.
 *
 * Zero new packages: AdSense loads via a plain <script> injected in
 * layout.tsx; this component only renders the <ins> container and pushes
 * the slot once it is in the DOM.
 */
export function AdSlot({
  placement,
  className = '',
}: {
  placement: Placement;
  className?: string;
}) {
  const slot = AD_SLOTS[placement];
  const live = Boolean(ADSENSE_CLIENT) && Boolean(slot) && slot !== '0';
  const pushed = useRef(false);

  useEffect(() => {
    if (!live || pushed.current) return;
    pushed.current = true;
    try {
      (window.adsbygoogle = window.adsbygoogle || []).push({});
    } catch {
      // AdSense script not loaded yet (blocked/async) — the <ins> stays
      // empty; never let ad machinery break the page.
    }
  }, [live]);

  if (!live) {
    return (
      <div
        className={`rounded-xl border border-dashed border-border bg-card/60 px-5 py-4 ${className}`}
      >
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <Megaphone className="w-4 h-4 text-gray-500 shrink-0" />
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-widest text-gray-500">
                Advertisement
              </div>
              <p className="text-sm text-gray-300 truncate">
                Get the full StatWise board — 25+ AI predictions daily in the app.
              </p>
            </div>
          </div>
          <Link
            href="/download"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary/15 border border-primary/40 text-primary text-sm font-semibold hover:bg-primary/25 transition shrink-0"
          >
            <Download className="w-4 h-4" />
            Get the App
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className={`overflow-hidden rounded-xl ${className}`}>
      <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-1">
        Advertisement
      </div>
      <ins
        className="adsbygoogle block"
        style={{ display: 'block' }}
        data-ad-client={ADSENSE_CLIENT}
        data-ad-slot={AD_SLOTS[placement]}
        data-ad-format="auto"
        data-full-width-responsive="true"
      />
    </div>
  );
}

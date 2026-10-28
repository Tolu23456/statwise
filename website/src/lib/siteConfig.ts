/**
 * Central knobs for the marketing site — the ONLY file to edit for ads and
 * the free/paid funnel.
 *
 * Ads: the AdSlot component renders a real Google AdSense unit when
 * ADSENSE_CLIENT is set (value: "ca-pub-…", from an approved AdSense
 * account) plus per-placement slot ids below. While the client id is
 * empty, slots render house ads that promote the app instead — the site
 * never shows an empty/broken ad frame, and every slot is already wired.
 */

/** Production origin used for canonical/OG metadata. */
export const SITE_URL = 'https://statwise-flax.vercel.app';

/** AdSense publisher id from the account (script injected in layout.tsx). */
export const ADSENSE_CLIENT = 'ca-pub-9868946535437166';

/**
 * AdSense slot ids per placement. Filled automatically by AdSense when a
 * unit is created; leave '0' while ADSENSE_CLIENT is empty.
 */
export const AD_SLOTS: Record<'top' | 'mid' | 'bottom', string> = {
  top: '0',
  mid: '0',
  bottom: '0',
};

/**
 * How many fixtures on the web board show their full prediction for free.
 * Everything past this renders as a locked teaser that points at the app
 * (which has its own free tier — see the pricing section).
 */
export const FREE_PICK_COUNT = 3;

/** Runtime URL for the news feed (committed by the sync-news workflow). */
export const NEWS_JSON_URL =
  'https://raw.githubusercontent.com/Tolu23456/statwise/main/website/src/data/news.json';

/** How many news cards the home page shows. */
export const NEWS_DISPLAY_COUNT = 6;

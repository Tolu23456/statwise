/**
 * Central knobs for the marketing site — the ONLY file to edit for ads and
 * the free/paid funnel.
 *
 * Ads: Auto ads is ENABLED in the AdSense dashboard — Google's script
 * (layout.tsx) places ads on its own from the ADSENSE_CLIENT id alone.
 * AD_SLOTS below are for MANUAL units: the AdSlot component renders a
 * real <ins> only for non-'0' ids and shows a house ad (app promo) in
 * the meantime, because pushing a manual unit with slot '0' would be an
 * invalid ad request. Fill the ids if you ever want fixed-position ads
 * in addition to Auto ads.
 */

/** Production origin used for canonical/OG metadata. */
export const SITE_URL = 'https://statwise-flax.vercel.app';

/** AdSense publisher id from the account (script injected in layout.tsx). */
export const ADSENSE_CLIENT = 'ca-pub-9868946535437166';

/**
 * AdSense slot ids per manual unit. '0' = not created yet (Auto ads
 * handles placement meanwhile; AdSlot renders a house ad there).
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

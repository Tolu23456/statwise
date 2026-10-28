import type { Metadata } from 'next'
import { NewsReader } from '@/components/NewsReader'

export const metadata: Metadata = {
  title: 'Football News & Insights',
  description:
    'Football news synced from Sky Sports and SportyTrader: headlines, images, bylines, video stories and story text, read inside StatWise — with the source credited on every story.',
}

/**
 * The site's news reader (static route + client-side view state, so the
 * page works with `output: 'export'` and picks up newly synced stories
 * without a redeploy):
 *   /news/          → full list of synced stories
 *   /news/?a=slug   → one story, read here (source credited + linked)
 */
export default function NewsPage() {
  return <NewsReader />
}

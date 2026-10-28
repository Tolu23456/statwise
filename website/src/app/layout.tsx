import './globals.css'
import type { Metadata } from 'next'
import { ADSENSE_CLIENT, SITE_URL } from '@/lib/siteConfig'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'StatWise - AI Football Predictions with a Public Track Record',
    template: '%s - StatWise',
  },
  description:
    'Free AI football predictions priced by a LightGBM model trained on 331,949 matches: calibrated probabilities, confidence, value edge and a public live track record. Full board and booking codes in the StatWise app.',
  keywords: [
    'football predictions',
    'AI football predictions',
    'soccer predictions',
    'betting predictions',
    '1x2 predictions',
    'over under predictions',
    'both teams to score',
    'football tips today',
    'match prediction AI',
    'StatWise',
  ],
  openGraph: {
    type: 'website',
    url: SITE_URL,
    siteName: 'StatWise',
    title: 'StatWise - AI Football Predictions with a Public Track Record',
    description:
      'Free AI football predictions from a model trained on 331,949 matches. Calibrated probabilities, value edge, live track record — and the full board in the app.',
    images: [
      {
        url: `${SITE_URL}/icon.png`,
        width: 512,
        height: 512,
        alt: 'StatWise',
      },
    ],
  },
  twitter: {
    card: 'summary',
    title: 'StatWise - AI Football Predictions',
    description:
      'Calibrated AI football probabilities, value edge and a public live track record. Free picks on the web, full board in the app.',
    images: [`${SITE_URL}/icon.png`],
  },
  // Branding: the same "S" monogram the app uses (multi-size ICO for
  // legacy browsers, PNG for modern ones, apple-touch for iOS Safari).
  icons: {
    icon: ['/favicon.ico', '/icon.png'],
    apple: '/apple-touch-icon.png',
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        {/* Google AdSense — official async snippet, rendered as a PLAIN
            script tag so the literal snippet exists in the static HTML
            (next/script only injects after hydration, which made AdSense's
            site-verification fetch unable to find the code). Ad units render
            via AdSlot; Auto ads, if enabled in the dashboard, use this
            same script. */}
        <script
          async
          src={`https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`}
          crossOrigin="anonymous"
        />
        {children}
      </body>
    </html>
  )
}

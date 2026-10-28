import './globals.css'
import type { Metadata } from 'next'
import Script from 'next/script'
import { ADSENSE_CLIENT } from '@/lib/siteConfig'

export const metadata: Metadata = {
  title: 'StatWise - AI Football Predictions',
  description: 'AI-powered football predictions with real analysis. Download the StatWise app.',
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
        {/* Google AdSense — official async snippet (publisher from
            siteConfig). Ad units render via AdSlot; Auto ads, if enabled in
            the AdSense dashboard, pick up the same script. */}
        <Script
          async
          src={`https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`}
          crossOrigin="anonymous"
          strategy="afterInteractive"
        />
        {children}
      </body>
    </html>
  )
}

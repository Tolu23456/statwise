import './globals.css'
import type { Metadata } from 'next'

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
      <body>{children}</body>
    </html>
  )
}

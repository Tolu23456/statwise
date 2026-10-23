import './globals.css'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'StatWise - AI Football Predictions',
  description: 'AI-powered football predictions with real analysis. Download the StatWise app.',
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

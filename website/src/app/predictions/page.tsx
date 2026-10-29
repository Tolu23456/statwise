import { Download, Lock, Sparkles, TrendingUp } from 'lucide-react'
import Link from 'next/link'
import { LivePredictions } from '@/components/LivePredictions'
import { AccuracyStats } from '@/components/AccuracyStats'
import { AdSlot } from '@/components/AdSlot'
import { SiteHeader } from '@/components/SiteHeader'
import { SiteFooter } from '@/components/SiteFooter'
import { pageMetadata } from '@/lib/seo'
import { FREE_PICK_COUNT } from '@/lib/siteConfig'
import type { Metadata } from 'next'

// Note: the layout title template appends " - StatWise" — keeping the bare
// title here avoids the old "… - StatWise - StatWise" double suffix.
export const metadata: Metadata = pageMetadata({
  title: 'Free AI Football Predictions',
  description:
    'Live AI football predictions from the StatWise model: fixture board with confidence and value edge, plus a public rolling track record computed from settled picks.',
  path: '/predictions/',
})

const WHATS_IN_APP = [
  {
    icon: Sparkles,
    title: 'AI Picks with booking codes',
    text: 'Highest-confidence, value-checked selections delivered as ready-made SportyBet Nigeria booking codes (single accumulators, no affiliate markup).',
  },
  {
    icon: TrendingUp,
    title: 'Value-edge alerts',
    text: 'The model flags fixtures where its blended probability beats the implied market price — the situations where historical closing-line gains concentrate.',
  },
  {
    icon: Lock,
    title: 'The rest of the board',
    text: '25 priced fixtures daily on Premium, 75 on VIP, unlimited on VVIP — each with confidence, full analysis and market context. Free tier in the app: 5 predictions every day.',
  },
]

export default function PredictionsPage() {
  return (
    <>
      <SiteHeader active="predictions" />

      <main className="min-h-screen">
        {/* Hero */}
        <section className="px-6 pt-16 pb-8 max-w-6xl mx-auto text-center">
          <h1 className="text-4xl md:text-6xl font-bold mb-6 bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
            Free AI Football Predictions
          </h1>
          <p className="text-lg text-gray-400 mb-4 max-w-3xl mx-auto leading-relaxed">
            This board is the real thing — the same predictions table the StatWise app renders,
            priced by a LightGBM model trained on 331,949 matches and evaluated on a 180-day
            chronological holdout it never saw during training. No hand-tipped exceptions, no
            retroactive edits: settled results feed the public track record above and below.
          </p>
          <p className="text-sm text-gray-400 max-w-2xl mx-auto">
            The first{' '}
            <strong className="text-gray-300">{FREE_PICK_COUNT} fixtures</strong> below are
            completely free — prediction, confidence and value edge included. Fixtures past that
            are visible but locked: the app unlocks them (and its own free tier covers 5
            predictions a day).
          </p>
        </section>

        {/* Track record first — judge before you read */}
        <section className="px-6 pb-10 max-w-6xl mx-auto">
          <AccuracyStats />
        </section>

        <div className="max-w-6xl mx-auto px-6">
          <AdSlot placement="top" className="mb-6" />
        </div>

        {/* The board */}
        <section className="px-6 pb-12 max-w-6xl mx-auto">
          <LivePredictions limit={12} freeCount={FREE_PICK_COUNT} />
        </section>

        {/* Why the app */}
        <section className="px-6 py-12 max-w-6xl mx-auto border-t border-border">
          <h2 className="text-3xl font-bold text-center mb-2">What the App Adds</h2>
          <p className="text-gray-400 text-center max-w-2xl mx-auto mb-8">
            The web is a free window into the model. The app is the full terminal.
          </p>
          <div className="grid md:grid-cols-3 gap-5">
            {WHATS_IN_APP.map(item => (
              <div key={item.title} className="rounded-2xl border border-border bg-card p-6">
                <item.icon className="w-6 h-6 text-primary mb-3" />
                <h3 className="font-bold mb-2">{item.title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{item.text}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 text-center">
            <Link
              href="/download"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-primary hover:bg-primary/90 text-white font-semibold transition"
            >
              <Download className="w-5 h-5" />
              Download StatWise
            </Link>
          </div>
        </section>

      </main>

      <SiteFooter />
    </>
  )
}

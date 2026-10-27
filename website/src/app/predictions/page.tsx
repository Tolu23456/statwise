import { BarChart3, Calendar, Download, TrendingUp } from 'lucide-react'
import Link from 'next/link'
import { LivePredictions } from '@/components/LivePredictions'
import { AccuracyStats } from '@/components/AccuracyStats'

export const metadata = {
  title: 'Predictions - StatWise',
  description:
    'Live AI football predictions from the StatWise app: fixture board, model calls, confidence and published hit rate.',
}

export default function PredictionsPage() {
  return (
    <main className="min-h-screen">
      <nav className="px-6 py-4 border-b border-border">
        <div className="max-w-6xl mx-auto flex justify-between items-center">
          <Link href="/" className="text-xl font-bold">StatWise</Link>
          <div className="flex gap-6">
            <Link href="/predictions" className="text-white">Predictions</Link>
            <Link href="/download" className="text-gray-400 hover:text-white">Download</Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="px-6 pt-16 pb-8 max-w-6xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 border border-primary/30 mb-6">
          <BarChart3 className="w-4 h-4 text-primary" />
          <span className="text-sm text-primary">Live from the StatWise app</span>
        </div>
        <h1 className="text-4xl md:text-5xl font-bold mb-4">Predictions</h1>
        <p className="text-xl text-gray-400 max-w-2xl mx-auto">
          The same fixture board the app renders — model calls, confidence and value finds,
          updated as the model re-prices each slate.
        </p>
      </section>

      {/* Live fixture board (fetched client-side from the app's data) */}
      <section className="px-6 pb-8 max-w-4xl mx-auto">
        <LivePredictions limit={10} />
        <div className="mt-8 text-center">
          <Link
            href="/download"
            className="inline-flex items-center gap-2 px-8 py-4 bg-primary rounded-xl font-semibold hover:bg-primary/90 transition"
          >
            <Download className="w-5 h-5" />
            Get Every Pick in the App
          </Link>
        </div>
      </section>

      {/* Analytics computed from settled app results */}
      <section className="px-6 py-16 bg-card/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-3">The Track Record</h2>
          <p className="text-center text-gray-400 mb-10 max-w-2xl mx-auto">
            Numbers computed live from settled fixtures in the app — not a hand-typed
            marketing figure. Updated with every settled match.
          </p>
          <AccuracyStats />
        </div>
      </section>

      {/* Feature cards */}
      <section className="px-6 py-16 max-w-4xl mx-auto">
        <div className="grid md:grid-cols-3 gap-6">
          <div className="p-6 bg-card border border-border rounded-xl text-center">
            <Calendar className="w-8 h-8 mx-auto mb-2 text-primary" />
            <div className="font-semibold">Daily Updates</div>
            <div className="text-sm text-gray-400">Fresh predictions every day</div>
          </div>
          <div className="p-6 bg-card border border-border rounded-xl text-center">
            <TrendingUp className="w-8 h-8 mx-auto mb-2 text-primary" />
            <div className="font-semibold">AI Picks</div>
            <div className="text-sm text-gray-400">Highest confidence selections</div>
          </div>
          <div className="p-6 bg-card border border-border rounded-xl text-center">
            <BarChart3 className="w-8 h-8 mx-auto mb-2 text-primary" />
            <div className="font-semibold">Full Analysis</div>
            <div className="text-sm text-gray-400">Detailed reasoning for every pick</div>
          </div>
        </div>
      </section>
    </main>
  )
}

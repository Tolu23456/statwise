import { BarChart3, Calendar, TrendingUp } from 'lucide-react'
import Link from 'next/link'

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

      <section className="px-6 py-20 max-w-4xl mx-auto text-center">
        <BarChart3 className="w-16 h-16 mx-auto mb-6 text-primary" />
        <h1 className="text-4xl md:text-5xl font-bold mb-4">Predictions</h1>
        <p className="text-xl text-gray-400 mb-8">Access AI-powered predictions through the StatWise app</p>
        <Link href="/download" className="inline-flex items-center gap-2 px-8 py-4 bg-primary rounded-xl font-semibold hover:bg-primary/90 transition">
          Download App to View
        </Link>
      </section>

      <section className="px-6 py-12 max-w-4xl mx-auto">
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

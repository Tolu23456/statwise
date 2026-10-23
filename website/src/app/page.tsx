import { Download, BarChart3, Brain, Trophy, ArrowRight } from 'lucide-react'
import Link from 'next/link'

export default function Home() {
  return (
    <main className="min-h-screen">
      {/* Hero */}
      <section className="px-6 py-20 max-w-6xl mx-auto">
        <div className="text-center">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 border border-primary/30 mb-8">
            <Brain className="w-4 h-4 text-primary" />
            <span className="text-sm text-primary">AI-Powered Football Predictions</span>
          </div>
          <h1 className="text-5xl md:text-7xl font-bold mb-6 bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
            Smarter Picks.<br />Better Results.
          </h1>
          <p className="text-xl text-gray-400 mb-10 max-w-2xl mx-auto">
            StatWise uses advanced machine learning to analyze form, H2H, xG, and market odds to deliver data-driven football predictions with real insights.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link href="/download" className="inline-flex items-center gap-2 px-8 py-4 bg-primary rounded-xl font-semibold hover:bg-primary/90 transition">
              <Download className="w-5 h-5" />
              Download App
            </Link>
            <Link href="/predictions" className="inline-flex items-center gap-2 px-8 py-4 bg-card border border-border rounded-xl font-semibold hover:bg-card/80 transition">
              View Predictions
              <ArrowRight className="w-5 h-5" />
            </Link>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="px-6 py-20 bg-card/50">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl md:text-4xl font-bold text-center mb-12">Why StatWise?</h2>
          <div className="grid md:grid-cols-3 gap-8">
            <FeatureCard icon={<Brain />} title="AI Analysis" desc="Real data-driven reasoning, not generic templates. Form, H2H, and market edge analyzed for every pick." />
            <FeatureCard icon={<BarChart3 />} title="Transparent Stats" desc="Win probability breakdown, model certainty, and value detection so you know the edge." />
            <FeatureCard icon={<Trophy />} title="AI Picks" desc="Our highest-confidence selections flagged daily with full transparency and trackable results." />
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="px-6 py-20">
        <div className="max-w-4xl mx-auto text-center bg-gradient-to-r from-primary/20 to-purple-500/20 border border-primary/30 rounded-3xl p-12">
          <h2 className="text-3xl md:text-4xl font-bold mb-4">Get the StatWise App</h2>
          <p className="text-gray-400 mb-8">Available on iOS and Android. Download now to access AI picks and booking codes.</p>
          <Link href="/download" className="inline-flex items-center gap-2 px-8 py-4 bg-primary rounded-xl font-semibold hover:bg-primary/90 transition">
            <Download className="w-5 h-5" />
            Download Now
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="px-6 py-12 border-t border-border">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row justify-between items-center gap-4">
          <div className="text-xl font-bold">StatWise</div>
          <div className="flex gap-6 text-gray-400">
            <Link href="/download" className="hover:text-white">Download</Link>
            <Link href="/predictions" className="hover:text-white">Predictions</Link>
            <Link href="/privacy" className="hover:text-white">Privacy</Link>
            <Link href="/terms" className="hover:text-white">Terms</Link>
          </div>
          <div className="text-gray-500">© 2026 StatWise. All rights reserved.</div>
        </div>
      </footer>
    </main>
  )
}

function FeatureCard({ icon, title, desc }: { icon: React.ReactNode, title: string, desc: string }) {
  return (
    <div className="p-8 bg-background border border-border rounded-2xl hover:border-primary/50 transition">
      <div className="w-12 h-12 bg-primary/10 rounded-xl flex items-center justify-center text-primary mb-4">
        {icon}
      </div>
      <h3 className="text-xl font-semibold mb-2">{title}</h3>
      <p className="text-gray-400">{desc}</p>
    </div>
  )
}

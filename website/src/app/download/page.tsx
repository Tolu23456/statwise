import { Apple, BarChart3, Check, Download, Smartphone, Sparkles } from 'lucide-react'
import Link from 'next/link'

export const metadata = {
  title: 'Download the App',
  description:
    'Get the full StatWise board on your device: 25+ AI predictions a day, value-edge alerts, SportyBet booking codes and a free tier of 5 predictions daily.',
}

const APP_BENEFITS = [
  {
    icon: Sparkles,
    title: 'AI Picks as booking codes',
    text: 'Ready-made SportyBet Nigeria booking codes for our highest-confidence, value-checked selections — single accumulators, tap to book.',
  },
  {
    icon: Download,
    title: 'The full board',
    text: '25 priced fixtures a day on Premium (₦500), 75 on VIP (₦2,000), unlimited on VVIP (₦5,000) — each with confidence, value edge and full match analysis.',
  },
  {
    icon: Check,
    title: 'A real free tier',
    text: '5 predictions every day without paying a kobo — the web shows a free preview, the app gives you a daily allowance of your own.',
  },
]

export default function DownloadPage() {
  return (
    <main className="min-h-screen">
      <nav className="px-6 py-4 border-b border-border sticky top-0 bg-background/90 backdrop-blur z-20">
        <div className="max-w-6xl mx-auto flex justify-between items-center">
          <Link href="/" className="text-xl font-bold inline-flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-primary" />
            StatWise
          </Link>
          <div className="flex gap-6 text-sm">
            <Link href="/predictions" className="text-gray-400 hover:text-white">
              Predictions
            </Link>
            <Link href="/news" className="text-gray-400 hover:text-white">
              News
            </Link>
            <Link href="/#pricing" className="text-gray-400 hover:text-white">
              Pricing
            </Link>
            <Link href="/download" className="text-primary font-semibold">
              Download
            </Link>
          </div>
        </div>
      </nav>

      <section className="px-6 py-16 max-w-4xl mx-auto text-center">
        <h1 className="text-4xl md:text-5xl font-bold mb-4">Download StatWise</h1>
        <p className="text-xl text-gray-400 mb-6 max-w-2xl mx-auto">
          The website shows a free sample of the model&apos;s work. The app is where the full
          board lives — plus AI Picks delivered as ready-made SportyBet Nigeria booking codes.
        </p>
        <p className="text-sm text-gray-500 mb-12">
          Start on the free tier (5 predictions daily, no card needed) and upgrade only when
          the live track record convinces you.
        </p>

        <div className="grid md:grid-cols-3 gap-5 mb-12 text-left">
          {APP_BENEFITS.map(item => (
            <div key={item.title} className="rounded-2xl border border-border bg-card p-6">
              <item.icon className="w-6 h-6 text-primary mb-3" />
              <h3 className="font-bold mb-2">{item.title}</h3>
              <p className="text-sm text-gray-400 leading-relaxed">{item.text}</p>
            </div>
          ))}
        </div>

        <div className="grid md:grid-cols-2 gap-6 mb-12">
          <div className="p-8 bg-card border border-border rounded-2xl">
            <Apple className="w-12 h-12 mx-auto mb-4" />
            <h2 className="text-xl font-semibold mb-2">iOS App</h2>
            <p className="text-gray-400 mb-6">Coming soon to the App Store</p>
            <button
              disabled
              className="w-full py-3 bg-gray-700 rounded-xl font-semibold opacity-50 cursor-not-allowed"
            >
              Coming Soon
            </button>
          </div>
          <div className="p-8 bg-card border border-border rounded-2xl">
            <Smartphone className="w-12 h-12 mx-auto mb-4" />
            <h2 className="text-xl font-semibold mb-2">Android App</h2>
            <p className="text-gray-400 mb-6">Coming soon to Google Play</p>
            <button
              disabled
              className="w-full py-3 bg-gray-700 rounded-xl font-semibold opacity-50 cursor-not-allowed"
            >
              Coming Soon
            </button>
          </div>
        </div>

        <div className="p-6 bg-card border border-border rounded-xl">
          <p className="text-gray-400">
            The mobile app is in final development. In the meantime, the free prediction board
            on this site is live and updates with every new slate of fixtures.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center mt-4">
            <Link
              href="/predictions"
              className="inline-flex items-center justify-center gap-2 text-primary hover:underline font-semibold"
            >
              View Free Predictions <Download className="w-4 h-4" />
            </Link>
            <Link
              href="/#pricing"
              className="inline-flex items-center justify-center gap-2 text-gray-400 hover:text-white underline"
            >
              See Full Pricing
            </Link>
          </div>
        </div>
      </section>

      <footer className="px-6 py-8 border-t border-border">
        <div className="max-w-6xl mx-auto text-xs text-gray-500 flex flex-col sm:flex-row justify-between gap-3">
          <span>
            18+ · Statistical predictions for informational purposes — never stake more than
            you can afford to lose.
          </span>
          <span className="flex gap-4">
            <Link href="/privacy" className="hover:text-white">
              Privacy
            </Link>
            <Link href="/terms" className="hover:text-white">
              Terms
            </Link>
            <Link href="/" className="hover:text-white">
              Home
            </Link>
          </span>
        </div>
      </footer>
    </main>
  )
}

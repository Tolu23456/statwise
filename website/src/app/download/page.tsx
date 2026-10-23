import { Download, Smartphone, Apple } from 'lucide-react'
import Link from 'next/link'

export default function DownloadPage() {
  return (
    <main className="min-h-screen">
      <nav className="px-6 py-4 border-b border-border">
        <div className="max-w-6xl mx-auto flex justify-between items-center">
          <Link href="/" className="text-xl font-bold">StatWise</Link>
          <div className="flex gap-6">
            <Link href="/predictions" className="text-gray-400 hover:text-white">Predictions</Link>
            <Link href="/download" className="text-white">Download</Link>
          </div>
        </div>
      </nav>

      <section className="px-6 py-20 max-w-4xl mx-auto text-center">
        <h1 className="text-4xl md:text-5xl font-bold mb-4">Download StatWise</h1>
        <p className="text-xl text-gray-400 mb-12">Get AI-powered football predictions on your device</p>
        
        <div className="grid md:grid-cols-2 gap-6 mb-12">
          <div className="p-8 bg-card border border-border rounded-2xl">
            <Apple className="w-12 h-12 mx-auto mb-4" />
            <h2 className="text-xl font-semibold mb-2">iOS App</h2>
            <p className="text-gray-400 mb-6">Coming soon to App Store</p>
            <button disabled className="w-full py-3 bg-gray-700 rounded-xl font-semibold opacity-50 cursor-not-allowed">
              Coming Soon
            </button>
          </div>
          <div className="p-8 bg-card border border-border rounded-2xl">
            <Smartphone className="w-12 h-12 mx-auto mb-4" />
            <h2 className="text-xl font-semibold mb-2">Android App</h2>
            <p className="text-gray-400 mb-6">Coming soon to Google Play</p>
            <button disabled className="w-full py-3 bg-gray-700 rounded-xl font-semibold opacity-50 cursor-not-allowed">
              Coming Soon
            </button>
          </div>
        </div>

        <div className="p-6 bg-card border border-border rounded-xl">
          <p className="text-gray-400">
            The mobile app is currently in development. Sign up for updates or access predictions via the web app.
          </p>
          <Link href="/predictions" className="inline-flex items-center gap-2 mt-4 text-primary hover:underline">
            View Web Predictions <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>
    </main>
  )
}

function ArrowRight({ className }: { className?: string }) {
  return <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
}

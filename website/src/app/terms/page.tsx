import Link from 'next/link'

export default function TermsPage() {
  return (
    <main className="min-h-screen">
      <nav className="px-6 py-4 border-b border-border">
        <div className="max-w-6xl mx-auto flex justify-between items-center">
          <Link href="/" className="text-xl font-bold">StatWise</Link>
          <div className="flex gap-6">
            <Link href="/predictions" className="text-gray-400 hover:text-white">Predictions</Link>
            <Link href="/download" className="text-gray-400 hover:text-white">Download</Link>
          </div>
        </div>
      </nav>

      <section className="px-6 py-16 max-w-3xl mx-auto">
        <h1 className="text-4xl font-bold mb-8">Terms of Service</h1>
        <div className="prose prose-invert max-w-none space-y-6 text-gray-300">
          <p>Last updated: October 2026</p>
          <h2 className="text-2xl font-semibold text-white">Acceptance of Terms</h2>
          <p>By accessing and using StatWise, you accept and agree to be bound by these terms.</p>
          <h2 className="text-2xl font-semibold text-white">Use of Service</h2>
          <p>StatWise provides AI-generated football predictions for informational purposes only.</p>
          <h2 className="text-2xl font-semibold text-white">No Gambling Advice</h2>
          <p>Our predictions are not gambling advice. Users are responsible for their own decisions.</p>
          <h2 className="text-2xl font-semibold text-white">Limitation of Liability</h2>
          <p>StatWise is not liable for any losses incurred from using our predictions.</p>
        </div>
      </section>
    </main>
  )
}

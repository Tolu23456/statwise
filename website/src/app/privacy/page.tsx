import Link from 'next/link'

export default function PrivacyPage() {
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
        <h1 className="text-4xl font-bold mb-8">Privacy Policy</h1>
        <div className="prose prose-invert max-w-none space-y-6 text-gray-300">
          <p>Last updated: October 2026</p>
          <h2 className="text-2xl font-semibold text-white">Information We Collect</h2>
          <p>We collect information you provide directly to us, including account information and usage data.</p>
          <h2 className="text-2xl font-semibold text-white">How We Use Your Information</h2>
          <p>We use the information we collect to provide, maintain, and improve our services.</p>
          <h2 className="text-2xl font-semibold text-white">Data Security</h2>
          <p>We implement appropriate security measures to protect your personal information.</p>
          <h2 className="text-2xl font-semibold text-white">Contact Us</h2>
          <p>If you have questions about this Privacy Policy, please contact us.</p>
        </div>
      </section>
    </main>
  )
}

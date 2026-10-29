import type { Metadata } from 'next'
import Link from 'next/link'
import { SiteHeader } from '@/components/SiteHeader'
import { SiteFooter } from '@/components/SiteFooter'
import { pageMetadata } from '@/lib/seo'

export const metadata: Metadata = pageMetadata({
  title: 'Terms of Service',
  description:
    'The terms that govern use of the StatWise website and predictions: informational service only, no gambling advice, no warranty on model output, and limits of liability.',
  path: '/terms/',
})

export default function TermsPage() {
  return (
    <>
      <SiteHeader />

      <main className="min-h-screen">
        <section className="px-6 py-16 max-w-3xl mx-auto">
          <h1 className="text-3xl sm:text-4xl font-bold mb-8">Terms of Service</h1>
          <div className="max-w-none space-y-6 text-gray-300">
            <p>Last updated: October 2026</p>
            <h2 className="text-2xl font-semibold text-white">Acceptance of Terms</h2>
            <p>By accessing and using StatWise, you accept and agree to be bound by these terms.</p>
            <h2 className="text-2xl font-semibold text-white">Use of Service</h2>
            <p>StatWise provides AI-generated football predictions for informational purposes only.</p>
            <h2 className="text-2xl font-semibold text-white">No Gambling Advice</h2>
            <p>Our predictions are not gambling advice. Users are responsible for their own decisions.</p>
            <h2 className="text-2xl font-semibold text-white">Limitation of Liability</h2>
            <p>StatWise is not liable for any losses incurred from using our predictions.</p>
            <p className="text-gray-400 text-sm">
              See also our{' '}
              <Link href="/privacy" className="text-primary hover:underline">
                Privacy Policy &amp; Cookie Disclosure
              </Link>
              .
            </p>
          </div>
        </section>
      </main>

      <SiteFooter />
    </>
  )
}

import type { Metadata } from 'next'
import Link from 'next/link'
import { SiteHeader } from '@/components/SiteHeader'
import { SiteFooter } from '@/components/SiteFooter'
import { pageMetadata } from '@/lib/seo'

export const metadata: Metadata = pageMetadata({
  title: 'Privacy Policy & Cookie Disclosure',
  description:
    'What data the StatWise website collects, how cookies and Google AdSense advertising cookies are used, which third parties are involved, and the rights you have over your data.',
  path: '/privacy/',
})

const CONTACT_URL = 'https://github.com/Tolu23456/statwise/issues'

export default function PrivacyPage() {
  return (
    <>
      <SiteHeader />

      <main className="min-h-screen">
        <section className="px-6 py-16 max-w-3xl mx-auto">
          <h1 className="text-3xl sm:text-4xl font-bold mb-8">
            Privacy Policy &amp; Cookie Disclosure
          </h1>
          <div className="max-w-none space-y-6 text-gray-300">
            <p>Last updated: October 2026</p>

            <h2 className="text-2xl font-semibold text-white">Who we are</h2>
            <p>
              This policy covers the StatWise marketing website at{' '}
              <span className="text-white">statwise-flax.vercel.app</span> (the
              &ldquo;site&rdquo;). The site is informational: it shows free AI football
              predictions, a live track record and syndicated football news. Purchases and
              accounts live in the StatWise app, not on this website. To ask us anything
              about this policy,{' '}
              <a
                href={CONTACT_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                open an issue on our GitHub repository
              </a>
              .
            </p>

            <h2 className="text-2xl font-semibold text-white">
              Information we collect
            </h2>
            <p>
              The site has no sign-up form, no account system and no contact form — we do
              not ask you for any personal information here, and no payment is taken on
              this website. Like any website, our hosting provider (Vercel) automatically
              processes standard request logs (IP address, browser user agent, page
              requested and timestamp) to run the service securely, prevent abuse and
              produce aggregate traffic statistics. The site also loads public data —
              upcoming fixtures and the settled-picks track record — from our Supabase
              database, which contains no personal data about you.
            </p>

            <h2 className="text-2xl font-semibold text-white">
              Cookies &amp; local storage used by this site
            </h2>
            <p>
              The site&apos;s own code sets <strong className="text-white">no first-party
              cookies</strong> and stores nothing in your browser&apos;s cookies or
              localStorage — there is no login or preference to remember. Cookies that do
              appear on this domain come from the third-party services described below,
              mainly advertising.
            </p>

            <h2 className="text-2xl font-semibold text-white">
              Advertising &amp; third-party cookies (Google AdSense)
            </h2>
            <p>
              We display advertising through Google AdSense. Google and its partners may
              set or read cookies and similar identifiers on your device to serve, measure
              and limit ads — including based on your prior visits to this or other
              websites — to cap how often you see the same ad, to prevent fraudulent
              clicks, and to report ad performance to us. This is the standard Google
              advertising cookie behaviour (including the DoubleClick DART cookie). Google
              never receives information from us that identifies you personally through
              these ads; we only see aggregate reports.
            </p>
            <p>
              You can control personalised advertising through{' '}
              <a
                href="https://adssettings.google.com/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                Google Ads Settings
              </a>
              , and opt out of many vendors&apos; cookies via{' '}
              <a
                href="https://www.aboutads.info/choices/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                aboutads.info (NAI)
              </a>{' '}
              or{' '}
              <a
                href="https://www.youronlinechoices.eu/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                Your Online Choices (EU/EEA)
              </a>
              . Google&apos;s own handling is described in its{' '}
              <a
                href="https://policies.google.com/technologies/ads"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                technologies used for advertising
              </a>{' '}
              page. We currently do not run an on-site consent banner; where local law
              requires consent for non-essential cookies, please use these controls (or
              your browser&apos;s cookie blocking) before using the site.
            </p>

            <h2 className="text-2xl font-semibold text-white">
              Other third parties involved
            </h2>
            <ul className="space-y-3 list-disc pl-5">
              <li>
                <strong className="text-white">News sync (GitHub).</strong> The news feed
                refreshes by fetching a public JSON file from raw.githubusercontent.com —
                GitHub processes standard request logs for that fetch like any web host.
              </li>
              <li>
                <strong className="text-white">Syndicated news (Sky Sports, SportyTrader).</strong> Story
                headlines, images and opening text are republished with attribution under
                our syndication practice; every story links to the original. Following
                those links takes you to their sites, which set their own cookies under
                their own policies.
              </li>
              <li>
                <strong className="text-white">Supabase.</strong> Our prediction data is
                read from a Supabase REST endpoint; no personal data about you is sent.
              </li>
            </ul>

            <h2 className="text-2xl font-semibold text-white">
              Your rights
            </h2>
            <p>
              If you are in the EEA, the UK or another region with similar law, you may
              have the right to access, correct, delete or restrict the processing of any
              personal data we hold about you, to object to processing (including
              advertising based on legitimate interest), to data portability, and to
              withdraw consent at any time — plus the right to lodge a complaint with your
              supervisory authority. Because the site collects so little directly (see
              above), most requests concern hosting logs or advertising identifiers; the
              fastest route is the opt-out links above or the contact link, and we will
              respond as required by applicable law.
            </p>

            <h2 className="text-2xl font-semibold text-white">Children</h2>
            <p>
              StatWise is an 18+ service about sports betting probabilities and is not
              directed at children; we do not knowingly collect data from anyone under 18.
            </p>

            <h2 className="text-2xl font-semibold text-white">Changes to this policy</h2>
            <p>
              When we add or change how the site handles data (a new analytics tool, a
              consent banner, a new ad unit), we update this page and its date. The
              current version is always the one published here.
            </p>

            <p className="text-gray-400 text-sm">
              Questions about this policy?{' '}
              <a
                href={CONTACT_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                Contact us via GitHub
              </a>
              . You can also read our{' '}
              <Link href="/terms" className="text-primary hover:underline">
                Terms of Service
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

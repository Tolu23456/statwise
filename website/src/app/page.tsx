import {
  ArrowRight,
  BarChart3,
  Brain,
  CalendarClock,
  CheckCircle2,
  Download,
  Lock,
  Newspaper,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  Trophy,
} from 'lucide-react'
import Link from 'next/link'
import { LivePredictions } from '@/components/LivePredictions'
import { AccuracyStats } from '@/components/AccuracyStats'
import { NewsFeed } from '@/components/NewsFeed'
import { PricingSection } from '@/components/PricingSection'
import { FaqSection } from '@/components/FaqSection'
import { AdSlot } from '@/components/AdSlot'
import { FREE_PICK_COUNT } from '@/lib/siteConfig'

const NAV_LINKS = [
  { href: '/predictions', label: 'Predictions' },
  { href: '/news', label: 'News' },
  { href: '/#pricing', label: 'Pricing' },
  { href: '/#faq', label: 'FAQ' },
  { href: '/download', label: 'Download' },
]

const MARKETS = [
  {
    icon: Target,
    name: 'Match Result (1X2)',
    text: 'Calibrated home/draw/away probabilities — the model core, benchmarked against the closing market.',
  },
  {
    icon: TrendingUp,
    name: 'Over/Under 2.5 & 3.5',
    text: 'Goal-total distributions per fixture built from league scoring environment and team rates.',
  },
  {
    icon: Trophy,
    name: 'Both Teams to Score',
    text: 'BTTS probabilities from attack/defence strength and clean-sheet trends with shrinkage.',
  },
  {
    icon: Sparkles,
    name: 'AI Picks + Booking Codes',
    text: 'Highest-confidence, value-checked selections delivered as ready-made SportyBet Nigeria booking codes in the app.',
  },
]

const METHOD_STEPS = [
  {
    step: '01',
    title: 'Ingest',
    text: 'Fixtures, form, head-to-head history, goals trends and live bookmaker odds flow in from 7 data sources continuously through the day.',
  },
  {
    step: '02',
    title: 'Model',
    text: 'A LightGBM model trained on 331,949 historical matches turns 161 features per fixture into calibrated outcome probabilities — not a bare tip.',
  },
  {
    step: '03',
    title: 'Blend & price',
    text: 'Model output is blended with the betting market. On the 180-day holdout the blend hit 0.995 log loss — better than the closing-market benchmark (0.998).',
  },
  {
    step: '04',
    title: 'Publish & settle',
    text: 'Every pick ships with confidence and value-edge, then settles automatically after full-time into the public live track record you see on this page.',
  },
]

const TRUST_CHIPS = [
  { value: '48.3%', label: 'holdout 1X2 accuracy' },
  { value: '1.011', label: 'raw model log loss' },
  { value: '331,949', label: 'matches trained on' },
  { value: '161', label: 'features per fixture' },
  { value: '7', label: 'data sources' },
  { value: '180d', label: 'chronological holdout' },
]

export default function Home() {
  return (
    <main className="min-h-screen">
      {/* Nav — same pattern as the inner pages */}
      <nav className="px-6 py-4 border-b border-border sticky top-0 bg-background/90 backdrop-blur z-20">
        <div className="max-w-6xl mx-auto flex justify-between items-center">
          <Link href="/" className="text-xl font-bold inline-flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-primary" />
            StatWise
          </Link>
          <div className="flex gap-6 text-sm">
            {NAV_LINKS.map(link => (
              <Link
                key={link.href}
                href={link.href}
                className={`transition ${
                  link.href === '/download'
                    ? 'text-primary font-semibold hover:text-primary/80'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {link.label}
              </Link>
            ))}
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="px-6 pt-16 pb-10 max-w-6xl mx-auto">
        <div className="text-center">
          <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 border border-primary/30 mb-8">
            <Brain className="w-4 h-4 text-primary" />
            <span className="text-sm text-primary">AI-Powered Football Predictions</span>
          </div>
          <h1 className="text-5xl md:text-7xl font-bold mb-6 bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">
            Smarter Picks.
            <br />
            Better Results.
          </h1>
          <p className="text-xl text-gray-400 mb-6 max-w-3xl mx-auto leading-relaxed">
            StatWise is a machine-learning football prediction engine, not a tipster page. A
            LightGBM model trained on <strong className="text-gray-200">331,949 historical
            matches</strong> from 7 data sources prices every fixture with 161 features — form
            with shrinkage, head-to-head, goals trends, league environment and the bookmaker
            market itself — and outputs calibrated probabilities you can actually bet against.
          </p>
          <p className="text-sm text-gray-500 mb-10 max-w-2xl mx-auto">
            On a strict 180-day chronological holdout of 2,250 matches the model hit{' '}
            <strong className="text-gray-300">48.3% of 1X2 outcomes</strong> (log loss 1.011),
            and blended with the market reached <strong className="text-gray-300">0.995</strong>{' '}
            — past the closing-line benchmark of 0.998. The live track record below is computed
            from settled picks in the app, so you judge current form, not just the lab number.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link
              href="/download"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-primary hover:bg-primary/90 text-white font-semibold transition"
            >
              <Download className="w-5 h-5" />
              Download the App
            </Link>
            <Link
              href="/predictions"
              className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl border border-border hover:border-primary/50 text-white font-semibold transition"
            >
              See Free Predictions
              <ArrowRight className="w-5 h-5" />
            </Link>
          </div>
        </div>

        {/* Stat chips — the honest numbers, up front */}
        <div className="mt-12 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {TRUST_CHIPS.map(chip => (
            <div
              key={chip.label}
              className="rounded-xl border border-border bg-card px-4 py-3 text-center"
            >
              <div className="text-lg font-bold text-white">{chip.value}</div>
              <div className="text-[11px] text-gray-500 leading-tight">{chip.label}</div>
            </div>
          ))}
        </div>
      </section>

      <div className="max-w-6xl mx-auto px-6">
        <AdSlot placement="top" className="my-4" />
      </div>

      {/* Free predictions — the funnel surface */}
      <section className="px-6 py-12 max-w-6xl mx-auto" id="free-picks">
        <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3 mb-6">
          <div>
            <h2 className="text-3xl font-bold">Today&apos;s Free Predictions</h2>
            <p className="text-gray-400 mt-2 max-w-2xl">
              A live sample of the board, straight from the same predictions table the app
              renders. The first{' '}
              <strong className="text-gray-200">{FREE_PICK_COUNT} fixtures</strong> show the
              full model call — prediction, confidence and value edge. The rest of the board
              (25+ priced fixtures a day, AI Picks and booking codes) lives in the app, which
              also has its own free tier of 5 predictions daily.
            </p>
          </div>
          <Link
            href="/predictions"
            className="text-primary text-sm font-semibold hover:underline whitespace-nowrap inline-flex items-center gap-1"
          >
            Full free board <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
        <LivePredictions limit={8} freeCount={FREE_PICK_COUNT} />
      </section>

      {/* Live track record */}
      <section className="px-6 py-12 max-w-6xl mx-auto">
        <h2 className="text-3xl font-bold text-center mb-2">Live Track Record</h2>
        <p className="text-gray-400 text-center max-w-2xl mx-auto mb-8">
          Every settled pick from the app, rolling 30 days. No cherry-picking: this number
          updates as results come in, win or lose.
        </p>
        <AccuracyStats />
      </section>

      {/* How it works — long-form trust copy */}
      <section className="px-6 py-12 max-w-6xl mx-auto" id="how">
        <h2 className="text-3xl font-bold text-center mb-2">How StatWise Predicts</h2>
        <p className="text-gray-400 text-center max-w-3xl mx-auto mb-10">
          Most prediction sites sell you a feeling. StatWise publishes its pipeline. Four stages
          turn raw football data into calibrated probabilities, and every stage is reproducible
          from the repo that builds this site.
        </p>
        <div className="grid md:grid-cols-2 gap-5">
          {METHOD_STEPS.map(item => (
            <div
              key={item.step}
              className="rounded-2xl border border-border bg-card p-6 flex gap-4"
            >
              <div className="text-3xl font-bold text-primary/40 shrink-0">{item.step}</div>
              <div>
                <h3 className="font-bold text-lg mb-1">{item.title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{item.text}</p>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap justify-center gap-3 text-xs text-gray-400">
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-border bg-card">
            <ShieldCheck className="w-3.5 h-3.5 text-primary" />
            Weekly retrains ship only if they beat the incumbent on the same holdout
          </span>
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-border bg-card">
            <CalendarClock className="w-3.5 h-3.5 text-primary" />
            Odds ingested continuously; board re-priced every new slate
          </span>
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-border bg-card">
            <CheckCircle2 className="w-3.5 h-3.5 text-primary" />
            Automatic settlement after full-time feeds the public record
          </span>
        </div>
      </section>

      {/* Markets */}
      <section className="px-6 py-12 max-w-6xl mx-auto" id="markets">
        <h2 className="text-3xl font-bold text-center mb-2">Markets Covered</h2>
        <p className="text-gray-400 text-center max-w-2xl mx-auto mb-8">
          Each market is a modelled head with published probability quality — not odds copied
          from a bookie.
        </p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {MARKETS.map(market => (
            <div
              key={market.name}
              className="rounded-2xl border border-border bg-card p-5 hover:border-primary/40 transition"
            >
              <market.icon className="w-6 h-6 text-primary mb-3" />
              <h3 className="font-bold mb-2">{market.name}</h3>
              <p className="text-sm text-gray-400 leading-relaxed">{market.text}</p>
            </div>
          ))}
        </div>
      </section>

      <div className="max-w-6xl mx-auto px-6">
        <AdSlot placement="mid" className="my-4" />
      </div>

      {/* News */}
      <section className="px-6 py-12 max-w-6xl mx-auto" id="news">
        <div className="flex items-center gap-3 mb-2">
          <Newspaper className="w-6 h-6 text-primary" />
          <h2 className="text-3xl font-bold">Football News & Insights</h2>
        </div>
        <p className="text-gray-400 max-w-3xl mb-8">
          Headlines, images and story text from the Sky Sports and
          SportyTrader newsrooms, synced automatically every two hours — and
          read right here on StatWise, never bounced off-site. Match reports,
          transfers, video reaction: the context around the fixtures the model
          prices. Every story credits its source.
        </p>
        <NewsFeed />
        <div className="mt-6 text-right">
          <Link
            href="/news"
            className="text-primary text-sm font-semibold hover:underline inline-flex items-center gap-1"
          >
            Open the news desk <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>

      {/* Pricing */}
      <section className="px-6 py-12 max-w-6xl mx-auto" id="pricing">
        <h2 className="text-3xl font-bold text-center mb-2">Pricing</h2>
        <p className="text-gray-400 text-center max-w-2xl mx-auto mb-8">
          Straight from the app&apos;s payment catalog — the same list checkout and the payment
          poller price against, so what you see here is exactly what you&apos;re charged.
          Payments run through Flutterwave (card, bank transfer, USSD) in naira, and access
          starts the moment payment verifies — usually within three minutes.
        </p>
        <PricingSection />
        <p className="text-xs text-gray-500 text-center mt-6 max-w-2xl mx-auto">
          The web previews {FREE_PICK_COUNT} full picks; the app&apos;s own free tier gives 5
          predictions every day with no card needed. 18+ — never stake more than you can
          afford to lose.
        </p>
      </section>

      {/* FAQ */}
      <section className="px-6 py-12 max-w-6xl mx-auto" id="faq">
        <h2 className="text-3xl font-bold text-center mb-8">Frequently Asked Questions</h2>
        <FaqSection />
      </section>

      {/* Final CTA */}
      <section className="px-6 py-16 max-w-6xl mx-auto text-center">
        <div className="rounded-2xl border border-primary/30 bg-gradient-to-b from-primary/10 to-transparent p-10">
          <Trophy className="w-10 h-10 text-primary mx-auto mb-4" />
          <h2 className="text-3xl font-bold mb-3">Get the Full Board in the App</h2>
          <p className="text-gray-400 max-w-2xl mx-auto mb-8">
            25 predictions a day on Premium, 75 on VIP, unlimited on VVIP — each with
            confidence, value-edge detection, full match analysis and instant SportyBet
            Nigeria booking codes for AI Picks. Start on the free tier and upgrade whenever
            the record convinces you.
          </p>
          <Link
            href="/download"
            className="inline-flex items-center justify-center gap-2 px-8 py-4 rounded-xl bg-primary hover:bg-primary/90 text-white font-semibold transition"
          >
            <Download className="w-5 h-5" />
            Download StatWise
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="px-6 py-10 border-t border-border">
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row justify-between gap-6">
          <div>
            <div className="font-bold text-lg mb-2 inline-flex items-center gap-2">
              <BarChart3 className="w-5 h-5 text-primary" />
              StatWise
            </div>
            <p className="text-xs text-gray-500 max-w-md leading-relaxed">
              Statistical football predictions for informational purposes. Football is
              unpredictable, models can be wrong, and no probability is a promise. 18+ only —
              never stake more than you can afford to lose. Please bet responsibly.
            </p>
          </div>
          <div className="flex gap-12 text-sm">
            <div className="flex flex-col gap-2">
              <span className="text-white font-semibold text-xs uppercase tracking-wider">
                Product
              </span>
              <Link href="/predictions" className="text-gray-400 hover:text-white">
                Free Predictions
              </Link>
              <Link href="/#pricing" className="text-gray-400 hover:text-white">
                Pricing
              </Link>
              <Link href="/download" className="text-gray-400 hover:text-white">
                Download App
              </Link>
            </div>
            <div className="flex flex-col gap-2">
              <span className="text-white font-semibold text-xs uppercase tracking-wider">
                Legal
              </span>
              <Link href="/privacy" className="text-gray-400 hover:text-white">
                Privacy
              </Link>
              <Link href="/terms" className="text-gray-400 hover:text-white">
                Terms
              </Link>
              <Link href="/news" className="text-gray-400 hover:text-white">
                News Desk
              </Link>
            </div>
          </div>
        </div>
        <div className="max-w-6xl mx-auto mt-8 pt-6 border-t border-border/50 text-xs text-gray-600">
          © {new Date().getFullYear()} StatWise. News stories syndicated from
          Sky Sports and SportyTrader with attribution — read in our news
          desk, with a link to every original article or video.
        </div>
      </footer>
    </main>
  )
}

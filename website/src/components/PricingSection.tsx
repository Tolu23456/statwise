import Link from 'next/link';
import { Check, Download, Sparkles } from 'lucide-react';
import { PLANS } from '../../../statwise/lib/paymentCore';

/**
 * Pricing — rendered DIRECTLY from statwise/lib/paymentCore.ts, the same
 * catalog the app's checkout and the payment poller price against, so the
 * website can never drift from what is actually purchasable. (paymentCore
 * is a pure module — the same trick statwiseData.ts uses for team logos.)
 *
 * CTA policy: free tier and paid tiers are both bought/inited in the app;
 * the web's own free surface is the 3-pick board higher up the page.
 */
export function PricingSection() {
  return (
    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
      {PLANS.map(plan => {
        const highlighted = plan.badge === 'POPULAR';
        const isFree = plan.price === 0;
        return (
          <div
            key={plan.id}
            className={`rounded-2xl border p-6 flex flex-col bg-card transition ${
              highlighted
                ? 'border-primary/70 shadow-[0_0_40px_-12px] shadow-primary/40'
                : 'border-border hover:border-primary/40'
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-bold tracking-widest text-primary">
                {plan.badge}
              </span>
              {plan.period === 'monthly' && (
                <span className="text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 font-semibold">
                  SAVE 20%+
                </span>
              )}
            </div>
            <h3 className="text-xl font-bold">{plan.name}</h3>
            <p className="text-sm text-gray-400 mb-4">{plan.highlight}</p>

            <div className="flex items-baseline gap-1 mb-4">
              {isFree ? (
                <span className="text-4xl font-bold">Free</span>
              ) : (
                <>
                  <span className="text-gray-500 text-lg">₦</span>
                  <span className="text-4xl font-bold">
                    {plan.price.toLocaleString('en-NG')}
                  </span>
                  <span className="text-gray-500 text-sm">
                    /{plan.period}
                  </span>
                </>
              )}
            </div>

            <div className="text-sm text-gray-300 mb-4 flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-primary shrink-0" />
              {plan.predictions >= 999
                ? 'Unlimited predictions'
                : `${plan.predictions} predictions / day`}
            </div>

            <ul className="space-y-2 mb-6 flex-1">
              {plan.features.map(f => (
                <li key={f} className="flex items-start gap-2 text-sm text-gray-400">
                  <Check className="w-4 h-4 text-primary mt-0.5 shrink-0" />
                  {f}
                </li>
              ))}
            </ul>

            <Link
              href="/download"
              className={`inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl font-semibold transition ${
                highlighted
                  ? 'bg-primary hover:bg-primary/90 text-white'
                  : 'bg-background border border-border hover:border-primary/50 text-white'
              }`}
            >
              <Download className="w-4 h-4" />
              {isFree ? 'Get the Free Tier' : 'Get in the App'}
            </Link>
          </div>
        );
      })}
    </div>
  );
}

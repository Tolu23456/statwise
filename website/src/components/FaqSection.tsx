/**
 * FAQ — static, crawlable text (good for search engines and for users who
 * want the honest version before downloading). Every number here is a real
 * one from the model's evaluation record; see MODEL_REPORT.md in the repo.
 */
const FAQS: { q: string; a: string }[] = [
  {
    q: 'How does StatWise predict matches?',
    a: 'A LightGBM gradient-boosted model trained on 331,949 historical matches from 7 data sources. Every fixture is described by 161 features — team attack/defence strength, home advantage, recent form with shrinkage, head-to-head history, goals trends (including over/under 2.5, over/under 3.5 and both-teams-to-score rates), league scoring environment and the bookmaker market odds — and the model outputs calibrated outcome probabilities rather than a bare tip.',
  },
  {
    q: 'How accurate is it, really?',
    a: 'On a 180-day holdout of 2,250 matches the raw model hit 48.3% of 1X2 outcomes with a log loss of 1.011. Blended with the betting market it reaches 0.995 — better than the closing-market benchmark itself (0.998). The website and app also publish a rolling live track record computed from settled picks in the app (the figure at the top of this page), so you can judge current form, not just the lab number. Past performance never guarantees future results.',
  },
  {
    q: 'What is free and what needs the app?',
    a: 'The website shows a rotating free preview of upcoming fixtures with full predictions — no sign-up needed. The StatWise app has its own free tier (5 predictions every day) and unlocks the rest of the board: 25+ picks a day on Premium, 75 on VIP and unlimited on VVIP, each with confidence, value-edge detection and full match analysis.',
  },
  {
    q: 'How much does it cost?',
    a: 'Pricing is in naira and shown in full above: Premium ₦500/day or ₦12,000/month, VIP ₦2,000/day or ₦45,000/month, VVIP ₦5,000/day. Payments run through Flutterwave inside the app (card, bank transfer, USSD), and access starts the moment payment verifies — usually within three minutes.',
  },
  {
    q: 'Which markets are covered?',
    a: 'Match result (1X2), over/under 2.5 goals, over/under 3.5 goals and both-teams-to-score are modelled heads with published probability quality for each. Exact-score and scoreline distributions are used inside the match analysis, and AI Picks — our highest-confidence, value-checked selections — are delivered in the app as ready-made SportyBet Nigeria booking codes for single accumulators.',
  },
  {
    q: 'How often do predictions update?',
    a: 'The pipeline ingests fixtures and odds continuously through the day, the model re-prices every new slate, and results are settled automatically after full-time. Team strengths are refreshed on a weekly retraining cycle that only ships if it beats the previous model on the same chronological holdout.',
  },
  {
    q: 'Where does the news on this site come from?',
    a: 'Football News & Insights is syndicated from SportyTrader: the sync action pulls new stories as they appear on their site (every two hours, checked automatically), including headline, image, byline, date and the opening text of each article, and renders them in our own /news reader — so you read here, not on a dozen tabs. Every story is credited to SportyTrader and links to the original article for the remainder and any late updates. We never claim their reporting as our own.',
  },
  {
    q: 'Is this betting advice?',
    a: 'StatWise provides statistical football predictions for informational purposes. Football is unpredictable, models can be wrong, and no probability is a promise — never stake more than you can afford to lose. 18+ only. Please bet responsibly.',
  },
];

export function FaqSection() {
  return (
    <div className="max-w-3xl mx-auto divide-y divide-border border border-border rounded-2xl bg-card overflow-hidden">
      {FAQS.map(item => (
        <details key={item.q} className="group p-5">
          <summary className="flex cursor-pointer items-center justify-between font-semibold list-none">
            {item.q}
            <span className="text-primary transition group-open:rotate-45 text-xl leading-none ml-4">
              +
            </span>
          </summary>
          <p className="mt-3 text-gray-400 leading-relaxed">{item.a}</p>
        </details>
      ))}
    </div>
  );
}

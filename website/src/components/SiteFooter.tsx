import Link from 'next/link';

/**
 * Compact legal footer shared by the inner pages (predictions, download,
 * news, privacy, terms) so EVERY page carries the 18+ notice and a Privacy
 * link. The home page keeps its richer footer (which also links Privacy).
 *
 * Secondary text uses text-gray-400 (~7.7:1 on the #0a0a0f background) —
 * the old text-gray-500/600 failed WCAG AA (4.0:1 / 2.6:1).
 */
export function SiteFooter() {
  return (
    <footer className="border-t border-border px-6 py-8">
      <div className="mx-auto flex max-w-6xl flex-col justify-between gap-3 text-xs text-gray-400 sm:flex-row">
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
  );
}

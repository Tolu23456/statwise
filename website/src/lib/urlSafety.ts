/**
 * Scheme validation for URLs that come from data files.
 *
 * website/src/data/news.json is written by a scraping script AND re-fetched
 * at RUNTIME from raw.githubusercontent.com, so every URL inside it is
 * untrusted input. React 18 only WARNS on `javascript:` hrefs (it does not
 * block them), so the guard lives here: only http(s) ever reaches an href or
 * img src. `javascript:`, `data:`, `vbscript:`, `blob:` and everything else
 * is rejected.
 */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  // Cheap pre-check so obviously-relative or exotic-scheme values never
  // even reach URL parsing.
  if (!/^https?:\/\//i.test(trimmed)) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return parsed.toString();
    }
  } catch {
    // Malformed URL — treat as unsafe.
  }
  return null;
}

/** JSX-friendly variant: returns '' for unsafe/missing URLs. */
export function safeUrlOrEmpty(value: unknown): string {
  return safeHttpUrl(value) ?? '';
}

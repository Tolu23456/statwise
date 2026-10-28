#!/usr/bin/env python3
"""
Sync football news from SportyTrader's public news listing into
website/src/data/news.json (the site's news cache).

Design constraints:
  - listing-page fetch for discovery (2 requests), then a bounded fetch of
    each NOT-YET-FETCHED article page for its lead text — the site renders
    headlines, images, bylines, dates and the opening paragraphs in its
    OWN /news reader, with source credit on every story;
  - stdlib only (runs on any ubuntu-latest runner, no pip);
  - merge, don't clobber: unseen items from earlier runs are kept (cap 30)
    and carried-over items keep their already-fetched lead text;
  - the workflow only commits when this script actually changed the file,
    and pushes with GITHUB_TOKEN (which does NOT trigger other workflows),
    so news updates never cascade into Vercel rebuilds — the site reads the
    raw file at runtime instead.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LISTING_URLS = [
    "https://www.sportytrader.com/en/news/",
    "https://www.sportytrader.com/en/news/2/",
]
OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "news.json"
MAX_ITEMS = 30
# Lead-text enrichment: how many ARTICLE pages one run may download.
# Override with SYNC_BODY_LIMIT for a one-off full backfill.
BODY_FETCH_LIMIT = int(os.environ.get("SYNC_BODY_LIMIT", "8"))
LEAD_PARAS = 4          # opening paragraphs stored per article
LEAD_CHARS = 1200       # …and a hard character cap across them
BODY_SLEEP = 0.4        # politeness gap between article requests
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 StatWiseNewsSync/1.0"
)

# Article links: the site's markup puts whitespace INSIDE the href attribute
# ("href=\"\n            /en/news/slug/\n\""), so both sides of the value
# allow \s*. Slugs are >=5 chars of [a-z0-9-]; bare pagination numbers
# (/en/news/2/) are too short and never match.
LINK_RE = re.compile(
    r'href="\s*(?:https://www\.sportytrader\.com)?\s*'
    r'(/en(?:-[a-z]{2})?/news/([a-z0-9][a-z0-9-]{4,})/)\s*"'
)
IMG_RE = re.compile(r'(?:src|data-src)="(https://static\.sportytrader\.com/images/news/[^"]+)"')
DATE_RE = re.compile(
    r"(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\s+at\s+(\d{2}:\d{2})(?:\s+by\s+([^<\n]+))?"
)
TAG_RE = re.compile(r"<[^>]+>")
# Article-page structures (verified against live pages 2026-10-28):
# a single <article itemscope NewsArticle> wraps the body; JSON-LD and
# share widgets leak into <p> matches, so filter them by content too.
ARTICLE_RE = re.compile(r"<article\b[^>]*>(.*?)</article>", re.S | re.I)
SCRIPT_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.S | re.I)
OG_IMG_RE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"')
LEAD_NOISE = (
    "commercial content",
    "@context",
    "sharer.php",
    "odds-format",
    "data-navigation",
    "read the article",
    "newsletter",
    "cookie",
    "published on",      # "Published on 27/10/2026 by … | Modified on …"
    "modified on",
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 (fixed https host)
        return res.read().decode("utf-8", errors="replace")


def strip_tags(fragment: str) -> str:
    text = TAG_RE.sub(" ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_article(url: str) -> tuple[list[str] | None, str]:
    """Lead paragraphs + og:image for one article page.

    Returns (None, "") when the PAGE couldn't be fetched (the item stays
    body-less and the next sync run retries it), or (paras, image) on a
    successful fetch — paras may legitimately be empty if a redesign hides
    them; that counts as done so we don't refetch forever.
    """
    try:
        page = fetch(url)
    except Exception as exc:
        print(f"::warning::article fetch failed for {url}: {exc}")
        return None, ""

    og = OG_IMG_RE.search(page)
    image = og.group(1) if og else ""

    m = ARTICLE_RE.search(page)
    if not m:
        return [], image
    block = SCRIPT_RE.sub(" ", m.group(1))
    paras: list[str] = []
    total = 0
    for pm in re.finditer(r"<p\b[^>]*>(.*?)</p>", block, flags=re.S):
        text = strip_tags(pm.group(1))
        low = text.lower()
        if len(text) < 80:            # meta lines, labels, one-liners
            continue
        if any(noise in low for noise in LEAD_NOISE):
            continue
        if DATE_RE.match(text):       # the date/byline <p>
            continue
        paras.append(text)
        total += len(text)
        if len(paras) >= LEAD_PARAS or total >= LEAD_CHARS:
            break
    return paras, image


def parse_listing(page: str) -> list[dict]:
    """Pull article cards out of a listing page.

    Strategy: split the page on article link occurrences and inspect the
    window around each link for image, date/byline and excerpt — tolerant
    of the exact card markup so minor site redesigns keep working.
    """
    items: list[dict] = []
    seen: set[str] = set()

    # Prefer the link's own <a>...</a> block for the title.
    for m in re.finditer(
        r'<a[^>]+href="[^"]*?(/en(?:-[a-z]{2})?/news/[a-z0-9][a-z0-9-]{4,})/[^"]*"[^>]*>(.*?)</a>',
        page,
        flags=re.S,
    ):
        slug_path, inner = m.group(1), m.group(2)
        slug_key = slug_path.rsplit("/", 1)[-1]  # path has no trailing slash
        if slug_key in seen:
            continue
        title = strip_tags(inner)
        if len(title) < 20 or title.lower() == "news":  # nav/see-more links
            continue

        window = page[m.start(): m.start() + 4000]
        img = IMG_RE.search(window)
        # Tags → newline (not space) so the byline's [^<\n]+ stops at the
        # end of its own line instead of swallowing the next card's text.
        date = DATE_RE.search(TAG_RE.sub("\n", window))
        # Excerpt: first substantial paragraph that is NOT the meta line
        # (the date/byline is wrapped in its own <p> in this markup).
        excerpt = ""
        for pm in re.finditer(r"<p[^>]*>(.{40,400}?)</p>", window, flags=re.S):
            text = strip_tags(pm.group(1))
            if DATE_RE.match(text) or text.lower().startswith(
                ("read the article", "see more")
            ):
                continue
            if len(text) >= 40:
                excerpt = text
                break

        iso = ""
        published_label = ""
        byline = ""
        if date:
            day, clock, by = date.group(1), date.group(2), (date.group(3) or "")
            published_label = f"{day} · {clock}"
            byline = by.split("Read the article")[0].strip()
            if len(byline) > 60:  # runaway capture → drop rather than show junk
                byline = ""
            try:
                iso = datetime.strptime(f"{day} {clock}", "%d %B %Y %H:%M").replace(
                    tzinfo=timezone.utc
                ).isoformat()
            except ValueError:
                iso = ""

        seen.add(slug_key)
        items.append(
            {
                "slug": slug_key,
                "title": title.rstrip("…").strip(),
                "url": f"https://www.sportytrader.com{slug_path}/",
                "image": img.group(1) if img else "",
                "excerpt": excerpt.rstrip("…").strip(),
                "published_label": published_label,
                "published_iso": iso,
                "byline": byline,
            }
        )
    return items


def main() -> int:
    fresh: list[dict] = []
    for url in LISTING_URLS:
        try:
            page = fetch(url)
        except Exception as exc:  # network hiccups must not break the job
            print(f"::warning::fetch failed for {url}: {exc}")
            continue
        fresh.extend(parse_listing(page))

    # Dedupe within this run, then merge with previously synced items.
    deduped: list[dict] = []
    local_seen: set[str] = set()
    for it in fresh:
        if it["url"] not in local_seen:
            local_seen.add(it["url"])
            deduped.append(it)

    existing: list[dict] = []
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text()).get("items", [])
        except json.JSONDecodeError:
            existing = []

    # A re-parsed listing card replaces its old copy — but keep the lead
    # text we already downloaded for it (parse_listing never sets body).
    existing_by_url = {it["url"]: it for it in existing}
    for it in deduped:
        prev = existing_by_url.get(it["url"])
        if prev and "body" in prev:
            it["body"] = prev["body"]

    # This run's listings first, then stories that fell off the listing
    # pages but were synced earlier (they keep their lead text too).
    fresh_urls = {it["url"] for it in deduped}
    merged = deduped + [it for it in existing if it["url"] not in fresh_urls]
    merged.sort(
        key=lambda it: it.get("published_iso") or "",
        reverse=True,
    )
    merged = merged[:MAX_ITEMS]

    if not merged:
        print("::error::no items parsed from any listing page — keeping previous file")
        return 1

    # Snapshot equality BEFORE enrichment: enrichment mutates `merged`, and
    # carried-over items are shared dict refs with `existing` — comparing
    # after would mask writes (content changed but refs identical).
    unchanged_listing = existing == merged

    # Lead-text enrichment: download article pages for stories we don't
    # have body text for yet (bounded per run; page failures retry next
    # run since the item then still lacks "body").
    enriched = 0
    need = [it for it in merged if "body" not in it][:BODY_FETCH_LIMIT]
    for i, it in enumerate(need):
        if i:
            time.sleep(BODY_SLEEP)
        paras, image = fetch_article(it["url"])
        if paras is None:  # fetch failed → leave body-less for a retry
            continue
        it["body"] = paras
        enriched += 1
        if paras and not it.get("image") and image:
            it["image"] = image

    # Nothing changed → don't rewrite the file: synced_at only moves when
    # content moves, so the workflow has nothing to commit on quiet runs.
    if unchanged_listing and enriched == 0:
        print(f"unchanged: {len(merged)} items ({len(need)} lead fetches)")
        return 0

    payload = {
        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "SportyTrader",
        "source_home": "https://www.sportytrader.com/en/news/",
        "items": merged,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    with_body = sum(1 for it in merged if it.get("body"))
    print(
        f"wrote {len(merged)} items ({len(deduped)} fresh, "
        f"{with_body} with lead text) -> {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

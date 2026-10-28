#!/usr/bin/env python3
"""
Sync football news from SportyTrader and Sky Sports into
website/src/data/news.json (the site's news cache).

Design constraints:
  - discovery via public listing pages only: SportyTrader's two listing
    pages, Sky Sports' /football/news page plus its JSON "load more"
    endpoints (/listing/sport/football/<page>, 6 tiles per page);
  - then a bounded fetch of each NOT-YET-FETCHED story page for its lead
    text — SportyTrader via the <article> block, Sky via JSON-LD
    NewsArticle/VideoObject (headline, articleBody, datePublished,
    author, image — video pages carry a description instead of a body
    and are stored as kind:"video" items the reader badges with a
    "Watch" link; Sky's player is proprietary so nothing is embedded);
  - the site renders headlines, images, bylines, dates and the opening
    text in its OWN /news reader, with per-story source credit;
  - stdlib only (runs on any ubuntu-latest runner, no pip);
  - merge, don't clobber: unseen items from earlier runs are kept
    (cap 40) and carried-over items keep their already-fetched lead
    text; per-URL dedupe across the two sources;
  - the workflow only commits when this script actually changed the
    file, and pushes with GITHUB_TOKEN (which does NOT trigger other
    workflows), so news updates never cascade into Vercel rebuilds —
    the site reads the raw file at runtime instead.
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

# ---------------------------------------------------------------- sources
SPORTY_LISTING_URLS = [
    "https://www.sportytrader.com/en/news/",
    "https://www.sportytrader.com/en/news/2/",
]
SKY_LISTING_URL = "https://www.skysports.com/football/news"
# Sky's "Show More" button hits this JSON endpoint (discovered from the
# listing page's data-ajax-config): {"page":N,"items":"<escaped html>"}.
# Page 1's tiles are server-rendered on the listing page itself; these
# AJAX pages yield the next tiles (6 per page, some overlap → URL dedupe).
SKY_AJAX_URL = "https://www.skysports.com/listing/sport/football/{page}"
SKY_AJAX_PAGES = int(os.environ.get("SYNC_SKY_PAGES", "2"))

SOURCES = {
    "SportyTrader": "https://www.sportytrader.com/en/news/",
    "Sky Sports": "https://www.skysports.com/football/news",
}

OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "news.json"
MAX_ITEMS = 40
# Lead-text enrichment: how many STORY pages one run may download.
# Override with SYNC_BODY_LIMIT for a one-off full backfill.
BODY_FETCH_LIMIT = int(os.environ.get("SYNC_BODY_LIMIT", "8"))
LEAD_PARAS = 4          # opening paragraphs stored per article
LEAD_CHARS = 1200       # …and a hard character cap across them
BODY_SLEEP = 0.4        # politeness gap between article requests
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 StatWiseNewsSync/1.0"
)

# ------------------------------------------------- SportyTrader parsing
# Card markup (verified against live pages 2026-10-28): the site puts
# whitespace INSIDE href attributes, dates read "27 October 2026 at 14:00
# by Name", and the byline rides in its own <p> above the excerpt.
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

# ------------------------------------------------------- Sky Sports parsing
# Tiles (verified live 2026-10-28): one <div class="sdc-site-tiles__item
# sdc-site-tile …"> per story, carrying an <img class="sdc-site-tile__image">
# and an <h3 class="sdc-site-tile__headline"><a class="sdc-site-tile__headline-link"
# href="/football/{news|video|transfer-paper-talk}/<id>/<slug>"><span
# class="sdc-site-tile__headline-text">…</span>. Nav links never sit in a
# tile, so splitting on the item class avoids them entirely.
SKY_HREF_RE = re.compile(
    r'href="(/football/(?:news|video|transfer-paper-talk)/(\d+)/[^"]+)"'
)
SKY_TITLE_RE = re.compile(
    r'sdc-site-tile__headline-text"[^>]*>(.*?)</span>', re.S
)
SKY_IMG_RE = re.compile(r'<img[^>]+src="(https://e\d\.365dm\.com[^"]+)"')
# Story pages carry one or more <script type="application/ld+json"> blocks;
# NewsArticle has articleBody/datePublished/author, VideoObject has
# description/thumbnail/uploadDate (embedUrl is EMPTY — proprietary
# player, so videos are credited+linked, never embedded).
JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S
)
SKY_BODY_SPLIT_RE = re.compile(r"<p[^>]*>")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as res:  # noqa: S310 (fixed https host)
        return res.read().decode("utf-8", errors="replace")


def strip_tags(fragment: str) -> str:
    text = TAG_RE.sub(" ", fragment)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fmt_label(iso: str) -> str:
    """'2026-10-27T21:50:00+00:00' → '27 October 2026 · 21:50' (match ST)."""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    return dt.strftime("%d %B %Y · %H:%M")


# ------------------------------------------------------ SportyTrader fetch
def fetch_sportytrader_article(url: str) -> tuple[list[str] | None, dict]:
    """Lead paragraphs + og:image for one SportyTrader article page.

    Returns (None, {}) when the PAGE couldn't be fetched (the item stays
    body-less and the next sync run retries it), or (paras, extra) on a
    successful fetch — paras may legitimately be empty if a redesign
    hides them; that counts as done so we don't refetch forever.
    """
    try:
        page = fetch(url)
    except Exception as exc:
        print(f"::warning::article fetch failed for {url}: {exc}")
        return None, {}

    og = OG_IMG_RE.search(page)
    extra = {"image": og.group(1) if og else ""}

    m = ARTICLE_RE.search(page)
    if not m:
        return [], extra
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
    return paras, extra


# --------------------------------------------------------- Sky Sports fetch
def parse_jsonld_blocks(page: str) -> list[dict]:
    blocks: list[dict] = []
    for raw in JSONLD_RE.findall(page):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            blocks.append(data)
        elif isinstance(data, list):  # some pages wrap entities in a list
            blocks.extend(d for d in data if isinstance(d, dict))
    return blocks


def lead_from_bodytext(article_body: str) -> list[str]:
    """articleBody is 'Lede. <p>Para 2</p><p>Para 3</p>…' — split, clean,
    bound to LEAD_PARAS/LEAD_CHARS like every other source."""
    paras: list[str] = []
    total = 0
    for chunk in SKY_BODY_SPLIT_RE.split(article_body):
        text = strip_tags(chunk)
        if len(text) < 60:
            continue
        paras.append(text)
        total += len(text)
        if len(paras) >= LEAD_PARAS or total >= LEAD_CHARS:
            break
    return paras


def fetch_sky_story(url: str) -> tuple[list[str] | None, dict]:
    """Lead text + metadata for one Sky story (article OR video page).

    Same retry contract as fetch_sportytrader_article: (None, {}) on a
    page-level fetch failure, ([], extra) when the page simply has no
    parseable body (counts as done).
    """
    try:
        page = fetch(url)
    except Exception as exc:
        print(f"::warning::sky story fetch failed for {url}: {exc}")
        return None, {}

    lds = parse_jsonld_blocks(page)
    news = next((d for d in lds if d.get("@type") == "NewsArticle"), None)
    video = next((d for d in lds if d.get("@type") == "VideoObject"), None)

    og = OG_IMG_RE.search(page)
    extra: dict = {"image": og.group(1) if og else ""}

    if news:
        iso = news.get("datePublished", "")
        if iso:
            try:
                # "2026-10-27T21:50:00+0000" → normalized UTC isoformat
                dt = datetime.fromisoformat(iso).astimezone(timezone.utc)
                iso = dt.isoformat()
                extra["published_iso"] = iso
                extra["published_label"] = fmt_label(iso)
            except ValueError:
                pass
        author = news.get("author")
        name = ""
        if isinstance(author, dict):
            name = author.get("name", "")
        elif isinstance(author, list) and author and isinstance(author[0], dict):
            name = author[0].get("name", "")
        extra["byline"] = name if name else "Sky Sports"
        img = news.get("image")
        if isinstance(img, dict) and img.get("url"):
            extra["image"] = img["url"]  # 1600x900 beats the 768px tile
        elif isinstance(img, list) and img and isinstance(img[0], dict):
            extra["image"] = img[0].get("url", extra["image"])
        paras = lead_from_bodytext(news.get("articleBody", ""))
        if paras:
            return paras, extra

    if video:
        # Video page: no article body — store the official description as
        # the story text and let the reader badge it as a video with a
        # "watch on source" link (embedUrl is empty; can't be embedded).
        extra["kind"] = "video"
        desc = video.get("description", "")
        if not desc and news:
            desc = news.get("description", "")
        if not desc:
            m = re.search(
                r'<meta[^>]+name="description"[^>]+content="([^"]+)"', page
            )
            desc = m.group(1) if m else ""
        thumb = video.get("thumbnailUrl")
        if not extra["image"] and isinstance(thumb, str) and thumb:
            extra["image"] = thumb
        if desc:
            return [strip_tags(desc)], extra
        return [], extra

    return [], extra


# ------------------------------------------------------------- listing parse
def parse_sportytrader_listing(page: str) -> list[dict]:
    """Pull article cards out of a SportyTrader listing page.

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
                "source": "SportyTrader",
                "image": img.group(1) if img else "",
                "excerpt": excerpt.rstrip("…").strip(),
                "published_label": published_label,
                "published_iso": iso,
                "byline": byline,
            }
        )
    return items


def parse_sky_tiles(chunk: str) -> list[dict]:
    """Story tiles out of one Sky HTML chunk (full listing page or an
    AJAX fragment). Dedupe by URL within the chunk; cross-chunk dedupe
    happens in main()."""
    items: list[dict] = []
    for block in chunk.split("sdc-site-tiles__item")[1:]:
        href = SKY_HREF_RE.search(block)
        title_m = SKY_TITLE_RE.search(block)
        if not href or not title_m:
            continue
        path, story_id = href.group(1), href.group(2)
        title = strip_tags(title_m.group(1))
        if not title:
            continue
        img = SKY_IMG_RE.search(block[:3000])
        kind = "video" if "/football/video/" in path else ""
        item = {
            # Sky's URL ids are globally unique; word slugs can collide
            # across sources, the numeric id never does.
            "slug": story_id,
            "title": title,
            "url": f"https://www.skysports.com{path}",
            "source": "Sky Sports",
            "image": img.group(1) if img else "",
            "excerpt": "",
            "published_label": "",
            "published_iso": "",
            "byline": "",
        }
        if kind:
            item["kind"] = kind
        items.append(item)
    return items


def fetch_sky_listings() -> list[dict]:
    """Server-rendered page 1 tiles + bounded AJAX 'load more' pages."""
    items: list[dict] = []
    try:
        items.extend(parse_sky_tiles(fetch(SKY_LISTING_URL)))
    except Exception as exc:
        print(f"::warning::fetch failed for {SKY_LISTING_URL}: {exc}")
    for page_no in range(2, 2 + SKY_AJAX_PAGES):
        url = SKY_AJAX_URL.format(page=page_no)
        try:
            payload = json.loads(fetch(url))
        except Exception as exc:  # network hiccups must not break the job
            print(f"::warning::fetch failed for {url}: {exc}")
            continue
        if not payload.get("items"):
            break
        items.extend(parse_sky_tiles(payload["items"]))
        if not payload.get("hasNext"):
            break
        time.sleep(BODY_SLEEP)
    return items


# ------------------------------------------------------------------- main
def main() -> int:
    fresh: list[dict] = []
    for url in SPORTY_LISTING_URLS:
        try:
            page = fetch(url)
        except Exception as exc:  # network hiccups must not break the job
            print(f"::warning::fetch failed for {url}: {exc}")
            continue
        fresh.extend(parse_sportytrader_listing(page))
    fresh.extend(fetch_sky_listings())

    # Dedupe within this run (both sources share the URL keyspace), then
    # merge with previously synced items.
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

    # A re-parsed card replaces its old copy — but keep the lead text and
    # enrichment we already downloaded for it (parsers never set body;
    # Sky tiles also lack date/byline/excerpt until their page is fetched,
    # so for Sky the previously enriched copy is strictly better).
    existing_by_url = {it["url"]: it for it in existing}
    merged_fresh: list[dict] = []
    for it in deduped:
        prev = existing_by_url.get(it["url"])
        if prev and it["source"] == "Sky Sports" and prev.get("published_iso"):
            merged_fresh.append(prev)  # keep enriched copy wholesale
        else:
            if prev and "body" in prev:
                it["body"] = prev["body"]
            merged_fresh.append(it)

    # This run's listings first, then stories that fell off the listing
    # pages but were synced earlier (they keep their lead text too).
    fresh_urls = {it["url"] for it in merged_fresh}
    merged = merged_fresh + [it for it in existing if it["url"] not in fresh_urls]
    # Dateless items (fresh Sky tiles awaiting enrichment) sort FIRST so
    # the cap can't drop stories before their page has been fetched.
    merged.sort(
        key=lambda it: it.get("published_iso") or "9999",
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

    # Lead-text enrichment: download story pages for items we don't have
    # body text for yet (bounded per run, newest/dateless first since the
    # sort puts them up front; page failures retry next run since the item
    # then still lacks "body").
    enriched = 0
    need = [it for it in merged if "body" not in it][:BODY_FETCH_LIMIT]
    for i, it in enumerate(need):
        if i:
            time.sleep(BODY_SLEEP)
        if it["source"] == "Sky Sports":
            paras, extra = fetch_sky_story(it["url"])
        else:
            paras, extra = fetch_sportytrader_article(it["url"])
        if paras is None:  # fetch failed → leave body-less for a retry
            continue
        it["body"] = paras
        enriched += 1
        for key, value in extra.items():
            if value and not it.get(key):
                it[key] = value
        # Excerpt for the card: opening paragraph, trimmed.
        if paras and not it.get("excerpt"):
            head = paras[0]
            it["excerpt"] = (head[:240].rstrip() + "…") if len(head) > 240 else head

    # Re-sort + re-cap now that enrichment filled in publish dates.
    merged.sort(
        key=lambda it: it.get("published_iso") or "9999",
        reverse=True,
    )
    merged = merged[:MAX_ITEMS]

    # Nothing changed → don't rewrite the file: synced_at only moves when
    # content moves, so the workflow has nothing to commit on quiet runs.
    if unchanged_listing and enriched == 0:
        print(f"unchanged: {len(merged)} items ({len(need)} lead fetches)")
        return 0

    payload = {
        "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "sources": SOURCES,
        "items": merged,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    with_body = sum(1 for it in merged if it.get("body"))
    with_source = {}
    for it in merged:
        with_source[it["source"]] = with_source.get(it["source"], 0) + 1
    breakdown = ", ".join(f"{n}×{s}" for s, n in sorted(with_source.items()))
    print(
        f"wrote {len(merged)} items ({breakdown}; {with_body} with lead "
        f"text; {len(need)} lead fetches) -> {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

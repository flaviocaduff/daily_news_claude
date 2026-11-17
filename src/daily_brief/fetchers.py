"""Source fetchers. Each returns a list of Items for one configured source."""
from __future__ import annotations

import html as htmllib
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup

from .models import Item

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; daily-brief/0.1; +https://github.com/)"

# Trailing "(5 minute read)", "(GitHub Repo)", "(Sponsor)", "(2 minute form)" ...
_SUFFIX_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
_NON_NEWS_SUFFIX = re.compile(r"sponsor|form|survey|hiring|giveaway", re.I)
_NON_NEWS_HOSTS = ("typeform.com", "tldr.tech", "jobs.", "refer.", "a.tldrnewsletter.com")
_PAID_MARKERS = ("utm_medium=paid", "utm_medium=newsletter_ad", "utm_campaign=sponsor")


def make_client(timeout: float) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,de;q=0.8"},
        timeout=timeout,
        follow_redirects=True,
    )


# --------------------------------------------------------------------------- TLDR


def tldr_candidate_dates(now: datetime, lookback_days: int = 1) -> list[date]:
    """Newest first: today back to `lookback_days` ago, local time (issues are dated, not timestamped)."""
    today = now.date()
    return [today - timedelta(days=n) for n in range(lookback_days + 1)]


def fetch_tldr(client: httpx.Client, source: dict, now: datetime, lookback_days: int = 1) -> list[Item]:
    slug = source["newsletter"]
    for d in tldr_candidate_dates(now, lookback_days):
        url = f"https://tldr.tech/{slug}/{d.isoformat()}"
        # No issue for that day -> TLDR redirects (307) to the newsletter landing page.
        # Under bursty load it sometimes answers 404/429/5xx for real issues, so retry those.
        for attempt in range(4):
            r = client.get(url, follow_redirects=False)
            if r.status_code in (200, 301, 302, 307, 308):
                break
            time.sleep(1.5 * 2**attempt)
        if r.status_code != 200 or "<article" not in r.text:
            log.info("tldr %s: no issue for %s (%s)", slug, d, r.status_code)
            continue
        items = parse_tldr_issue(r.text, source, d, now.tzinfo or timezone.utc)
        log.info("tldr %s: %d items from %s", slug, len(items), d)
        return items
    return []


def parse_tldr_issue(page: str, source: dict, issue_date: date, tz) -> list[Item]:
    soup = BeautifulSoup(page, "html.parser")
    published = datetime(issue_date.year, issue_date.month, issue_date.day, tzinfo=tz)
    items: list[Item] = []
    for section in soup.find_all("section"):
        header = section.find("header")
        if header and "together with" in header.get_text(" ", strip=True).lower():
            continue  # sponsor block
        for art in section.find_all("article"):
            a = art.find("a", href=True)
            h3 = art.find("h3")
            if not a or not h3:
                continue
            raw_title = h3.get_text(" ", strip=True)
            # hrefs are double-escaped in TLDR pages (&amp;amp;)
            link = htmllib.unescape(a["href"])
            if is_tldr_non_news(raw_title, link):
                continue
            title = strip_suffix(raw_title)
            body = art.find(class_="newsletter-html")
            teaser = body.get_text(" ", strip=True) if body else ""
            items.append(
                Item(
                    source_id=source["id"],
                    source_name=source["name"],
                    title=title,
                    link=link,
                    teaser=teaser,
                    published=published,
                )
            )
    return items


def strip_suffix(title: str) -> str:
    return _SUFFIX_RE.sub("", title).strip()


def is_tldr_non_news(title: str, link: str) -> bool:
    m = _SUFFIX_RE.search(title)
    if m and _NON_NEWS_SUFFIX.search(m.group(1)):
        return True
    low = link.lower()
    if any(p in low for p in _PAID_MARKERS):
        return True
    host = re.sub(r"^https?://", "", low).split("/", 1)[0]
    return any(h in host for h in _NON_NEWS_HOSTS)


# --------------------------------------------------------------------------- RSS


def fetch_rss(client: httpx.Client, source: dict, now: datetime, window_hours: int) -> list[Item]:
    r = client.get(source["url"])
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    cutoff = now - timedelta(hours=window_hours)
    items: list[Item] = []
    for e in feed.entries:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        if not ts:
            continue
        published = datetime(*ts[:6], tzinfo=timezone.utc)
        if published < cutoff:
            continue
        teaser = _html_to_text(e.get("summary", ""))
        items.append(
            Item(
                source_id=source["id"],
                source_name=source["name"],
                title=_html_to_text(e.get("title", "")),
                link=e.get("link", ""),
                teaser=teaser,
                published=published,
                image=_feed_image(e),
            )
        )
    log.info("rss %s: %d items in window (of %d)", source["id"], len(items), len(feed.entries))
    return items


def _html_to_text(s: str) -> str:
    if "<" not in s:
        return htmllib.unescape(s).strip()
    return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)


def _feed_image(e) -> str | None:
    for key in ("media_content", "media_thumbnail"):
        for m in e.get(key, []) or []:
            if m.get("url"):
                return m["url"]
    for enc in e.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image") and enc.get("href"):
            return enc["href"]
    m = re.search(r'<img[^>]+src="([^"]+)"', e.get("summary", "") or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------- dispatch


def fetch_source(client: httpx.Client, source: dict, now: datetime, window_hours: int, tldr_lookback: int = 1) -> list[Item]:
    kind = source["type"]
    if kind == "tldr":
        return fetch_tldr(client, source, now, tldr_lookback)
    if kind == "rss":
        return fetch_rss(client, source, now, window_hours)
    raise ValueError(f"unknown source type {kind!r} for {source['id']}")


def local_now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))

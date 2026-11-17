"""Full article text for the stories on the page (all sources of a story), for the tap-to-open summary.

Paywalled or blocked pages simply yield no text; the summary then falls back to the teasers.
"""
from __future__ import annotations

import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx

from .models import Item

log = logging.getLogger(__name__)

MAX_BYTES = 2_000_000    # stop reading huge pages
MAX_CHARS = 6000         # text per source sent to Claude
MIN_CHARS = 1000         # less than this is a teaser/paywall stub, not an article
MAX_SOURCES = 3          # per story: the main item + up to 2 duplicates


def extract_text(html: str, url: str) -> str:
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True) or ""
    text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())
    return text[:MAX_CHARS] if len(text) >= MIN_CHARS else ""


def fetch_text(client: httpx.Client, url: str) -> str:
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return ""
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    break
        return extract_text(buf.decode(r.encoding or "utf-8", "replace"), str(r.url))
    except Exception as e:  # network errors, bad TLS, parser errors -> no text
        log.debug("article text failed for %s: %s", url, e)
        return ""


def story_sources(story: Item) -> list[Item]:
    return [story, *story.dupes][:MAX_SOURCES]


def add_texts(client: httpx.Client, stories: list[Item], workers: int = 12) -> None:
    todo = [src for s in stories for src in story_sources(s) if not src.text]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        texts = list(ex.map(lambda it: fetch_text(client, it.link), todo))
    total, full = Counter(), Counter()
    for it, text in zip(todo, texts):
        it.text = text
        total[it.source_name] += 1
        full[it.source_name] += bool(text)
    for name in sorted(total):
        log.info("full text %s: %d of %d", name, full[name], total[name])
    for s in stories:
        s.points_from_preview = not any(src.text for src in story_sources(s))

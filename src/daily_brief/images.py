"""og:image lookup, done concurrently with short timeouts; failures mean no thumbnail."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import httpx

from .models import Item

log = logging.getLogger(__name__)

_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_PROP_RE = re.compile(r'(?:property|name)\s*=\s*["\'](og:image(?::secure_url)?|twitter:image(?::src)?)["\']', re.I)
_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']+)["\']', re.I)
_MAX_BYTES = 400_000


def extract_og_image(page: str, base_url: str) -> str | None:
    head = page.split("</head>", 1)[0]
    found: dict[str, str] = {}
    for tag in _META_RE.findall(head):
        p, c = _PROP_RE.search(tag), _CONTENT_RE.search(tag)
        if p and c:
            found.setdefault("og" if p.group(1).lower().startswith("og") else "tw", c.group(1))
    url = found.get("og") or found.get("tw")
    if not url:
        return None
    url = urljoin(base_url, url.replace("&amp;", "&").strip())
    return url if url.startswith(("https://", "http://")) else None


_YT_RE = re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)([\w-]{11})")


def known_thumbnail(url: str) -> str | None:
    """Thumbnails derivable from the URL alone (no page fetch)."""
    m = _YT_RE.search(url)
    return f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg" if m else None


def _fetch_one(client: httpx.Client, item: Item) -> None:
    if (thumb := known_thumbnail(item.link)):
        item.image = thumb
        return
    try:
        with client.stream("GET", item.link) as r:
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if b"</head>" in buf or len(buf) > _MAX_BYTES:
                    break
        item.image = extract_og_image(buf.decode(r.encoding or "utf-8", "replace"), str(r.url))
    except Exception as e:  # network errors, bad TLS, etc. -> no image
        log.debug("og:image failed for %s: %s", item.link, e)


def add_images(client: httpx.Client, items: list[Item], workers: int = 16) -> None:
    todo = [i for i in items if not i.image]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda it: _fetch_one(client, it), todo))
    log.info("images: %d/%d items have a thumbnail", sum(1 for i in items if i.image), len(items))

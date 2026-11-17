"""URL canonicalisation and near-duplicate headline detection."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

from .models import Item

_TRACKING = re.compile(r"^(utm_|mc_|fbclid$|gclid$|ref$|ref_src$|source$|s$|cmpid$|xtor$)", re.I)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), ""))


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(t.split())


def dedupe(items: list[Item], threshold: int = 90) -> list[Item]:
    """Drop items with the same canonical URL or a near-identical headline.

    The first occurrence wins (sources are processed in config order); the
    names of sources that also carried the story are kept in `also_in`.
    """
    kept: list[Item] = []
    by_url: dict[str, Item] = {}
    titles: list[tuple[str, Item]] = []
    for it in items:
        it.canonical = canonical_url(it.link)
        it.uid = hashlib.sha1(it.canonical.encode()).hexdigest()[:10]
        dup = by_url.get(it.canonical)
        if dup is None:
            nt = norm_title(it.title)
            if len(nt) >= 12:
                for other_t, other in titles:
                    if fuzz.token_set_ratio(nt, other_t) >= threshold and fuzz.ratio(nt, other_t) >= threshold - 15:
                        dup = other
                        break
        if dup is not None:
            if it.source_name != dup.source_name and it.source_name not in dup.also_in:
                dup.also_in.append(it.source_name)
            if not dup.image and it.image:
                dup.image = it.image
            continue
        by_url[it.canonical] = it
        titles.append((norm_title(it.title), it))
        kept.append(it)
    return kept

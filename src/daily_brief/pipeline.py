from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .dedup import dedupe
from .fetchers import fetch_source, local_now, make_client
from .images import add_images
from .models import Item

log = logging.getLogger(__name__)


@dataclass
class Brief:
    now: datetime
    top: list[Item]
    sections: list[tuple[dict, list[Item]]]
    empty_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    llm: bool = True

    @property
    def item_count(self) -> int:
        return len(self.top) + sum(len(i) for _, i in self.sections)


def build_brief(cfg: Config, use_llm: bool = True, now: datetime | None = None) -> Brief:
    now = now or local_now(cfg.get("timezone", "Europe/Zurich"))
    window = int(cfg.get("window_hours", 24))
    client = make_client(float(cfg.get("http_timeout", 10)))

    # 1. fetch (concurrently, one source failing never kills the run)
    def _fetch(src):
        try:
            return src, fetch_source(client, src, now, window, int(cfg.get("tldr_lookback_days", 1))), None
        except Exception as e:
            log.error("source %s failed: %s", src["id"], e)
            return src, [], e

    with ThreadPoolExecutor(max_workers=3) as ex:  # TLDR returns spurious 404s under bursts
        results = list(ex.map(_fetch, cfg.sources))
    failed = [s["name"] for s, _, err in results if err]
    raw: list[Item] = [it for _, items, _ in results for it in items]
    log.info("fetched %d raw items", len(raw))

    # 2. dedupe (URL + fuzzy title)
    items = dedupe(raw, int(cfg.get("dedup_threshold", 90)))
    log.info("%d items after dedup", len(items))

    # 3. summarise + score
    summarizer = None
    if use_llm and items:
        from .summarize import Summarizer

        summarizer = Summarizer(cfg.settings)
        summarizer.summarize(items)
        if not any(i.summary for i in items):
            # wrong model id, bad key, outage... don't silently publish raw teasers
            raise RuntimeError("LLM produced no summaries; check ANTHROPIC_API_KEY and models in sources.yaml")

    # 4. keep the best N per source
    per_source: dict[str, list[Item]] = {s["id"]: [] for s in cfg.sources}
    for it in items:
        per_source[it.source_id].append(it)
    for src in cfg.sources:
        lst = sorted(per_source[src["id"]], key=lambda i: -i.score)  # stable: keeps feed order on ties
        per_source[src["id"]] = [i for i in lst if not summarizer or i.score != 1][: cfg.max_items(src)]
    kept = [i for s in cfg.sources for i in per_source[s["id"]]]

    # 5. Top-N + cross-language duplicates
    top_n = int(cfg.get("top_n", 3))
    top_ids: list[str] = []
    if summarizer and kept:
        top_ids, dup_groups = summarizer.rank(kept, top_n)
        by_id = {i.uid: i for i in kept}
        drop: set[str] = set()
        for group in dup_groups:
            members = [by_id[g] for g in group if g in by_id]
            if len(members) < 2:
                continue
            keeper = members[0]
            for other in members[1:]:
                drop.add(other.uid)
                if other.source_name != keeper.source_name and other.source_name not in keeper.also_in:
                    keeper.also_in.append(other.source_name)
                keeper.image = keeper.image or other.image
        top_ids = [t for t in top_ids if t not in drop and t in by_id]
        for sid in per_source:
            per_source[sid] = [i for i in per_source[sid] if i.uid not in drop]
        kept = [i for i in kept if i.uid not in drop]
    # backfill (rank failed, returned too few, or picks were duplicates): highest score,
    # or without LLM the first item of the first sources
    ranked = sorted(kept, key=lambda i: -i.score) if summarizer else [l[0] for l in per_source.values() if l]
    for it in ranked:
        if len(top_ids) >= top_n:
            break
        if it.uid not in top_ids:
            top_ids.append(it.uid)

    by_id = {i.uid: i for i in kept}
    top = [by_id[t] for t in top_ids if t in by_id]
    top_set = {i.uid for i in top}

    # 6. thumbnails for everything that will be shown
    if cfg.get("fetch_images", True):
        add_images(client, kept)
    client.close()

    sections = [(s, [i for i in per_source[s["id"]] if i.uid not in top_set]) for s in cfg.sources]
    empty = [s["name"] for s, lst in sections if not lst and s["name"] not in failed and not any(t.source_id == s["id"] for t in top)]
    sections = [(s, lst) for s, lst in sections if lst]
    return Brief(now=now, top=top, sections=sections, empty_sources=empty, failed_sources=failed, llm=bool(summarizer))

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .dedup import add_dupe, dedupe
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
    script: str = ""          # spoken audio overview (podcast.py)
    audio: str | None = None  # page-relative MP3 path, set only once the file exists
    audio_seconds: float = 0.0

    @property
    def stories(self) -> list[Item]:
        return self.top + [i for _, lst in self.sections for i in lst]

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
            # usage limit, wrong model id, bad token, outage: publish a marked teaser-only page rather than none.
            # The page says so, and the workflow's gate rebuilds a marked page on its next run.
            log.error("LLM produced no summaries (usage limit? token? models in sources.yaml?): "
                      "publishing a teaser-only page")
            summarizer = None
            for it in items:
                it.headline = it.summary = ""
                it.score = 0


    sections_conf = cfg.sections()
    src_section = {s["id"]: cfg.section_of(s) for s in cfg.sources}
    for it in items:
        it.section = src_section[it.source_id]
    top_n = int(cfg.get("top_n", 3))

    def by_importance(lst: list[Item]) -> list[Item]:
        # score first, then items with a thumbnail; stable, so feed order breaks remaining ties
        return sorted(lst, key=lambda i: (-i.score, i.image is None))

    # 4. candidate pool per section (enough for the section + possible Top-N picks)
    pool: dict[str, list[Item]] = {}
    for sec in sections_conf:
        lst = [i for i in items if i.section == sec["name"] and not (summarizer and i.score == 1)]
        pool[sec["name"]] = by_importance(lst)[: 2 * (sec["max_items"] + top_n)]
    candidates = [i for sec in sections_conf for i in pool[sec["name"]]]

    # 5. thumbnails for candidates, so ties can prefer stories with an image
    if cfg.get("fetch_images", True):
        add_images(client, candidates)

    # 6. Top-N + cross-language duplicates (LLM), then backfill by score
    top_ids: list[str] = []
    by_id = {i.uid: i for i in candidates}
    drop: set[str] = set()
    if summarizer and candidates:
        top_ids, dup_groups = summarizer.rank(candidates, top_n)
        for group in dup_groups:
            members = [by_id[g] for g in group if g in by_id]
            if len(members) < 2:
                continue
            keeper = members[0]
            for other in members[1:]:
                drop.add(other.uid)
                add_dupe(keeper, other)
                if other.source_name != keeper.source_name and other.source_name not in keeper.also_in:
                    keeper.also_in.append(other.source_name)
                keeper.image = keeper.image or other.image
        top_ids = [t for t in dict.fromkeys(top_ids) if t in by_id and t not in drop]
    alive = [i for i in candidates if i.uid not in drop]
    if summarizer:
        fallback = by_importance(alive)
    else:  # no scores: the first story of each section, round-robin
        fallback = [lst[0] for lst in ([i for i in alive if i.section == s["name"]] for s in sections_conf) if lst]
    for it in fallback:
        if len(top_ids) >= top_n:
            break
        if it.uid not in top_ids:
            top_ids.append(it.uid)
    top = [by_id[t] for t in top_ids]
    taken = set(top_ids)

    # 7. fill each section: best first, capped per source for variety
    sections: list[tuple[dict, list[Item]]] = []
    for sec in sections_conf:
        per_src_cap = int(sec.get("max_per_source") or 10**6)
        counts: dict[str, int] = {}
        chosen: list[Item] = []
        for it in pool[sec["name"]]:
            if it.uid in taken or it.uid in drop or counts.get(it.source_id, 0) >= per_src_cap:
                continue
            chosen.append(it)
            counts[it.source_id] = counts.get(it.source_id, 0) + 1
            if len(chosen) >= sec["max_items"]:
                break
        if chosen:
            sections.append((sec, chosen))

    # 8. full article text (all sources of each shown story) -> key points for the tap-to-open view
    if cfg.get("fetch_articles", True):
        from .articles import add_texts

        shown = top + [i for _, lst in sections for i in lst]
        try:
            add_texts(client, shown)
            if summarizer:
                summarizer.key_points(shown)
        except Exception as e:  # never lose the brief over the extra detail
            log.error("article step failed: %s", e)
    client.close()

    # 9. German version of everything shown, for the page's EN/DE toggle (translates the finished English text)
    tr = cfg.get("translate") or {}
    if summarizer and tr.get("enabled", True):
        shown = top + [i for _, lst in sections for i in lst]
        try:
            summarizer.translate(shown, tr.get("language", "German"), timeout=float(tr.get("timeout", 120)))
        except Exception as e:  # the English page never depends on the translation
            log.error("translation step failed: %s", e)

    with_items = {i.source_id for i in items}
    empty = [s["name"] for s in cfg.sources if s["id"] not in with_items and s["name"] not in failed]
    brief = Brief(now=now, top=top, sections=sections, empty_sources=empty, failed_sources=failed, llm=bool(summarizer))

    # 10. spoken script for the audio overview (TTS runs later, in podcast.add_audio)
    if summarizer and (cfg.get("podcast") or {}).get("enabled", True):
        from .podcast import write_script

        try:
            model = (cfg.get("models") or {}).get("podcast", summarizer.rank_model)
            brief.script = write_script(summarizer._ask, brief, cfg.settings, model)
        except Exception as e:  # the page never depends on the podcast
            log.error("podcast script failed: %s", e)
    return brief

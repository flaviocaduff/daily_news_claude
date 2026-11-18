from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Item:
    source_id: str
    source_name: str
    title: str
    link: str
    teaser: str = ""
    published: datetime | None = None
    image: str | None = None
    # filled by the pipeline
    section: str = ""
    canonical: str = ""
    headline: str = ""   # display headline (possibly translated)
    summary: str = ""    # one sentence
    score: int = 0       # 1-5 importance
    also_in: list[str] = field(default_factory=list)  # other sources that carried the story
    uid: str = ""
    dupes: list[Item] = field(default_factory=list)  # same story from other URLs/sources
    text: str = ""       # full article text, when it could be read
    points: list[str] = field(default_factory=list)  # key points across all sources
    gist: str = ""       # plain-language summary for the story view
    why: str = ""        # one line: why it matters
    points_from_preview: bool = False  # no full text was readable; points come from teasers

    def display_headline(self) -> str:
        return self.headline or self.title

    def display_summary(self) -> str:
        return self.summary or first_sentence(self.teaser)


def first_sentence(text: str, max_chars: int = 240) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    for sep in (". ", "! ", "? "):
        i = text.find(sep)
        if 0 < i < max_chars:
            return text[: i + 1]
    return text if len(text) <= max_chars else text[: max_chars - 1].rsplit(" ", 1)[0] + "…"

"""End-to-end pipeline with a stubbed Claude client (no network, no API key)."""
import json
from datetime import datetime, timezone

import pytest

import daily_brief.pipeline as pl
import daily_brief.summarize as sm
from daily_brief.config import Config
from daily_brief.models import Item

SOURCES = [{"id": "a", "name": "A", "type": "rss"}, {"id": "b", "name": "B", "type": "rss"}]


TOPICS = {
    "a": ["Swiss bank merger approved by regulator", "New GPU launched with record memory",
          "Ransomware gang hits hospital network", "Startup raises seed round for robotics"],
    # German-language twin of a's first story: only the LLM rank call can spot it
    "b": ["Bankenfusion von Aufsicht genehmigt", "Zinsentscheid der Notenbank vertagt",
          "Autobauer kuendigt Stellenabbau an", "Immobilienpreise in Zuerich steigen weiter"],
}


def _items(src):
    return [
        Item(source_id=src["id"], source_name=src["name"], title=t,
             link=f"https://{src['id']}.com/{k}", teaser=f"Teaser {k}. More.")
        for k, t in enumerate(TOPICS[src["id"]])
    ]


class FakeSummarizer(sm.Summarizer):
    def __init__(self, settings, mode):
        self.settings, self.language, self.system = settings, "English", ""
        self.sum_model, self.rank_model, self.mode = "s", "r", mode

    def _ask(self, model, prompt, max_tokens):
        if model == "s":
            if self.mode == "broken":
                return '[{"id": "x", "headl'  # truncated
            ids = [d["id"] for d in json.loads(prompt[prompt.index("["):])]
            # last item of every batch is fluff (score 1)
            return json.dumps([{"id": i, "headline": f"H {i}", "summary": f"S {i}.", "score": 1 if n == len(ids) - 1 else 5 - n % 4}
                               for n, i in enumerate(ids)])
        items = json.loads(prompt[prompt.index("Items:") + 6:])
        a0, b0 = items[0]["id"], next(i["id"] for i in items if i["source"] == "B")
        # top includes b0, which the duplicate group drops -> must be backfilled
        return json.dumps({"top": [b0, a0], "duplicates": [[a0, b0]]})


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(pl, "fetch_source", lambda c, src, now, w, lb=1: _items(src))

    def _run(mode):
        monkeypatch.setattr(sm, "Summarizer", lambda s: FakeSummarizer(s, mode))
        cfg = Config(settings={"max_items_per_source": 2, "top_n": 3, "fetch_images": False, "fetch_articles": False, "summarize_batch_size": 3},
                     sources=SOURCES)
        return pl.build_brief(cfg, use_llm=True, now=datetime(2026, 11, 16, 7, tzinfo=timezone.utc))
    return _run


def test_llm_path_trims_dedups_and_backfills(run):
    b = run("good")
    assert len(b.top) == 3
    assert b.top[0].source_name == "A" and b.top[0].also_in == ["B"]
    shown = b.top + [i for _, lst in b.sections for i in lst]
    assert all(i.score != 1 for i in shown)
    assert all(i.summary.startswith("S ") for i in shown)
    assert len({i.uid for i in shown}) == len(shown)
    # section caps exclude the Top-N picks
    assert all(len(lst) <= 2 for _, lst in b.sections)


def test_grouped_section_caps_per_source(monkeypatch):
    srcs = [{"id": f"t{k}", "name": f"TLDR {k}", "section": "TLDR", "type": "rss"} for k in range(3)]
    titles = iter([
        "Apple ships new laptop chip", "Rust 2.0 roadmap published", "Kubernetes adds gang scheduling",
        "Bitcoin ETF inflows surge", "Postgres 19 beta released", "Figma launches AI layouts",
        "Ransomware hits Swiss hospital", "Stripe raises at record valuation", "Linux kernel drops i486",
        "Nvidia unveils datacenter GPU", "Google settles antitrust case", "SpaceX catches Starship booster"])
    monkeypatch.setattr(pl, "fetch_source", lambda c, src, now, w, lb=1: [
        Item(source_id=src["id"], source_name=src["name"], title=next(titles), link=f"https://{src['id']}.com/{k}")
        for k in range(4)])
    cfg = Config(settings={"top_n": 1, "fetch_images": False, "fetch_articles": False,
                           "sections": [{"name": "TLDR", "max_items": 4, "max_per_source": 2}]}, sources=srcs)
    b = pl.build_brief(cfg, use_llm=False, now=datetime(2026, 11, 16, 7, tzinfo=timezone.utc))
    assert [s["name"] for s, _ in b.sections] == ["TLDR"]
    items = b.sections[0][1]
    assert len(items) == 4 and len(b.top) == 1
    assert all(sum(1 for i in items if i.source_id == s["id"]) <= 2 for s in srcs)


def test_total_llm_failure_is_loud(run):
    with pytest.raises(RuntimeError):
        run("broken")


def test_translate_fills_german_and_keeps_english():
    s = FakeSummarizer({}, "ok")
    s.translate_model = "t"
    a = Item("a", "A", "T", "https://a.com/1", uid="1", headline="Bank merger", summary="Approved.",
             gist="G.", points=["p1", "p2"], why="W.")
    b = Item("a", "A", "T2", "https://a.com/2", uid="2", headline="GPU", summary="Fast.", points=["p1", "p2"])

    def ask(model, prompt, max_tokens, system=None, timeout=600):
        assert model == "t" and "German" in system and timeout == 5
        rows = json.loads(prompt[prompt.index("Stories:") + 8:])
        # story 2 comes back with a bullet missing -> its points stay English
        return json.dumps([{"id": r["id"], "headline": "DE " + r["headline"], "summary": "DE", "gist": "DE G",
                            "points": ["d1", "d2"] if r["id"] == "1" else ["d1"], "why": "DE W"} for r in rows])

    s._ask = ask
    s.translate([a, b], "German", batch_size=1, timeout=5)
    assert a.de == {"headline": "DE Bank merger", "summary": "DE", "gist": "DE G", "why": "DE W", "points": ["d1", "d2"]}
    assert a.headline == "Bank merger" and a.points == ["p1", "p2"]  # English untouched
    assert b.de["headline"] == "DE GPU" and b.de["points"] == [] and b.de["gist"] == "" and b.de["why"] == ""


def test_translation_failure_keeps_english_brief(run):
    # FakeSummarizer has no translate model and a 3-arg _ask: the translate step fails, the brief must not
    b = run("ok")
    assert b.top and all(not it.de for it in b.stories)


def test_translate_bad_json_is_contained():
    s = FakeSummarizer({}, "ok")
    s.translate_model = "t"
    it = Item("a", "A", "T", "https://a.com/1", uid="1", headline="H", summary="S.")
    s._ask = lambda *a, **k: '[{"id": "1", "headline": "Er sagte "Hallo"'
    s.translate([it])
    assert it.de == {}

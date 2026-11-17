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
        cfg = Config(settings={"max_items_per_source": 2, "top_n": 3, "fetch_images": False, "summarize_batch_size": 3},
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
    per_source = {s["id"]: [i for i in shown if i.source_id == s["id"]] for s in SOURCES}
    assert all(len(v) <= 2 for v in per_source.values())


def test_total_llm_failure_is_loud(run):
    with pytest.raises(RuntimeError):
        run("broken")

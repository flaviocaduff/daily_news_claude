"""Audio overview: script limits, TTS failure isolation, player paths on both pages (no network)."""
from datetime import datetime, timezone
from pathlib import Path

import daily_brief.podcast as pc
from daily_brief.models import Item
from daily_brief.pipeline import Brief
from daily_brief.render import write_site

NOW = datetime(2026, 11, 18, 6, 30, tzinfo=timezone.utc)


def _brief(script="Good morning. Two stories today."):
    items = [Item(source_id="a", source_name="A", title=f"Story {k}", link=f"https://a.com/{k}", summary=f"S {k}.")
             for k in range(4)]
    return Brief(now=NOW, top=items[:1], sections=[({"name": "A"}, items[1:])], script=script)


def test_script_is_cleaned_and_capped():
    calls = []

    def ask(model, prompt, max_tokens):
        calls.append(prompt)
        return "## Intro\n- **Good** morning https://x.y\n" + "word. " * 900

    script = pc.write_script(ask, _brief(""), {"podcast": {"max_words": 100}}, "m")
    assert len(calls) == 2 and "at most 90 words" in calls[0]  # aim under the cap, then one shorter retry
    assert "Story 3" in calls[0] and "ALL 4 stories" in calls[0]
    assert pc.word_count(script) <= 100
    assert not any(c in script for c in "#*") and "http" not in script


def test_tts_failure_leaves_no_player(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("NoAudioReceived")

    monkeypatch.setattr(pc, "_tts", boom)
    b = _brief()
    settings = {"output_dir": str(tmp_path)}
    pc.add_audio(b, settings)
    assert b.audio is None and not list(tmp_path.rglob("*.mp3")) and not list(tmp_path.rglob("*.part"))
    write_site(b, settings)
    assert "<audio" not in (tmp_path / "index.html").read_text()


def _fake_tts(seconds):
    async def fake(text, voice, rate):
        return b"\0" * int(seconds * pc.BITRATE / 8)
    return fake


def test_audio_paths_on_index_and_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "_tts", _fake_tts(250))
    b = _brief()
    settings = {"output_dir": str(tmp_path)}
    pc.add_audio(b, settings)
    assert b.audio == "archive/2026-11-18.mp3" and round(b.audio_seconds) == 250
    write_site(b, settings)
    assert 'src="archive/2026-11-18.mp3"' in (tmp_path / "index.html").read_text()
    assert 'src="2026-11-18.mp3"' in (tmp_path / "archive/2026-11-18.html").read_text()
    assert "4:10" in (tmp_path / "index.html").read_text()


def test_overlong_audio_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "_tts", _fake_tts(320))
    b = _brief()
    pc.add_audio(b, {"output_dir": str(tmp_path)})
    assert b.audio is None and not list(tmp_path.rglob("*.mp3"))


def test_tts_stall_is_bounded(tmp_path, monkeypatch):
    import asyncio
    import time

    async def stall(*a, **k):
        await asyncio.sleep(60)

    monkeypatch.setattr(pc, "_tts", stall)
    b = _brief()
    t0 = time.monotonic()
    pc.add_audio(b, {"output_dir": str(tmp_path), "podcast": {"tts_budget_seconds": 7}})
    assert b.audio is None and time.monotonic() - t0 < 10


def test_split_chunks_keeps_all_text():
    text = " ".join(f"Sentence number {k} is here." for k in range(60))
    chunks = pc.split_chunks(text, 40)
    assert len(chunks) > 1 and all(len(c.split()) <= 40 for c in chunks)
    assert " ".join(chunks) == text


def test_prune_keeps_only_todays_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "_tts", _fake_tts(100))
    settings = {"output_dir": str(tmp_path)}
    old = Brief(now=datetime(2026, 11, 17, 6, tzinfo=timezone.utc), top=_brief().top, sections=[], script="Hi there.")
    pc.add_audio(old, settings)
    write_site(old, settings)
    assert "<audio" in (tmp_path / "archive/2026-11-17.html").read_text()
    new = _brief()
    pc.add_audio(new, settings)
    write_site(new, settings)
    assert pc.prune_old_audio(tmp_path, "2026-11-18") == ["2026-11-17.mp3"]
    assert [p.name for p in (tmp_path / "archive").glob("*.mp3")] == ["2026-11-18.mp3"]
    old_page = (tmp_path / "archive/2026-11-17.html").read_text()
    assert "<audio" not in old_page and 'class="pod"' not in old_page and "</footer>" in old_page
    assert "<audio" in (tmp_path / "index.html").read_text()


def test_german_script_prompt():
    seen = []
    pc.write_script(lambda m, p, n: seen.append(p) or "Guten Morgen.", _brief(""), {"podcast": {"language": "German"}}, "m")
    assert "Mittwoch, 18. November 2026" in seen[0] and "Plain spoken German" in seen[0]

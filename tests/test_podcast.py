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
    assert len(calls) == 2  # one shorter retry before trimming
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
    async def fake(text, path: Path, voice, rate):
        path.write_bytes(b"\0" * int(seconds * pc.BITRATE / 8))
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

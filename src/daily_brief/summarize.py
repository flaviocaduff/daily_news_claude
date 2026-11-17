"""Claude calls: per-item one-sentence summaries + importance score, and the Top-N pick."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .models import Item

log = logging.getLogger(__name__)

SUMMARY_SYSTEM = """You write a daily news brief. Be short, precise and factual. No filler, no hype, no "this article discusses".
Reader: {profile}
Output language: {language}."""

SUMMARY_PROMPT = """For each news item below, return:
- "id": the given id
- "headline": a clear headline in {language}, max 12 words (keep the original if it is already clear and in {language})
- "summary": exactly ONE sentence in {language}, max 30 words, stating the concrete news/fact (who, what, key number)
- "score": importance for this reader, integer 1-5 (5 = must know today; sponsored/marketing/fluff = 1)

Return ONLY a JSON array, no prose.

Items:
{items}"""

RANK_PROMPT = """Below are today's summarised news items from several sources (some in different original languages).

1. Pick the {top_n} most important, distinct stories for this reader. Prefer hard news with real impact over opinion pieces.
2. Find groups of items that report the SAME story (e.g. the same event in an English and a German source).

Return ONLY JSON: {{"top": ["id", ...], "duplicates": [["id_keep", "id_drop", ...], ...]}}
In each duplicate group put the best item first.

Items:
{items}"""


def _parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)
    end = max(text.rfind("]"), text.rfind("}"))
    if start < 0 or end < start:
        raise ValueError("no JSON found")
    return json.loads(text[start : end + 1])


def find_claude_cli(settings: dict) -> str:
    path = settings.get("claude_cli_path") or shutil.which("claude")
    if not path:  # launchd has a minimal PATH
        for cand in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
            if cand.exists():
                path = str(cand)
                break
    if not path:
        raise RuntimeError("claude CLI not found; install Claude Code or set settings.claude_cli_path")
    return path


class Summarizer:
    """backend 'claude-cli': headless Claude Code (`claude -p`) on your Claude subscription, no API key.
    backend 'api': Anthropic API with ANTHROPIC_API_KEY (billed per token)."""

    def __init__(self, settings: dict):
        self.backend = settings.get("llm_backend", "claude-cli")
        if self.backend == "api":
            import anthropic  # imported lazily so other backends work without the key

            self.client = anthropic.Anthropic(max_retries=4)
        else:
            self.cli = find_claude_cli(settings)
            self.workdir = tempfile.mkdtemp(prefix="daily-brief-")  # no project CLAUDE.md in cwd
        self.settings = settings
        self.language = settings.get("language", "English")
        self.system = SUMMARY_SYSTEM.format(profile=settings.get("reader_profile", "").strip(), language=self.language)
        models = settings.get("models", {})
        self.sum_model = models.get("summarize", "claude-haiku-5-5")
        self.rank_model = models.get("rank", "claude-sonnet-5-5")

    def _ask(self, model: str, prompt: str, max_tokens: int) -> str:
        if self.backend != "api":
            return self._ask_cli(model, prompt)
        msg = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.stop_reason == "max_tokens":
            log.warning("%s hit max_tokens (%d); reply may be truncated", model, max_tokens)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def _ask_cli(self, model: str, prompt: str) -> str:
        cmd = [
            self.cli, "-p",
            "--model", model,
            "--output-format", "json",
            "--system-prompt", self.system,
            "--tools", "",               # plain text completion, no tool use
            "--strict-mcp-config",       # don't start the user's MCP servers
            "--no-session-persistence",
        ]
        # Drop any API key so the CLI uses the subscription login, never per-token billing.
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600, cwd=self.workdir, env=env)
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude CLI failed (exit {r.returncode}): {(r.stderr or r.stdout)[:300]}")
        if out.get("is_error") or r.returncode != 0:
            raise RuntimeError(f"claude CLI error: {str(out.get('result'))[:300]}")
        return out.get("result", "")

    def _summarize_batch(self, batch: list[Item]) -> None:
        payload = [
            {"id": it.uid, "source": it.source_name, "title": it.title, "teaser": it.teaser[:700]} for it in batch
        ]
        prompt = SUMMARY_PROMPT.format(language=self.language, items=json.dumps(payload, ensure_ascii=False))
        for attempt in (1, 2):
            try:
                data = _parse_json(self._ask(self.sum_model, prompt, max_tokens=1000 + 250 * len(batch)))
                by_id = {it.uid: it for it in batch}
                for row in data:
                    it = by_id.get(str(row.get("id")))
                    if not it:
                        continue
                    it.headline = str(row.get("headline", "")).strip()
                    it.summary = str(row.get("summary", "")).strip()
                    try:
                        it.score = max(1, min(5, int(row.get("score", 0))))
                    except (TypeError, ValueError):
                        it.score = 0
                return
            except Exception as e:
                log.warning("summary batch failed (attempt %d): %s", attempt, e)
        # fallback: teaser's first sentence is used at render time

    def summarize(self, items: list[Item]) -> None:
        n = int(self.settings.get("summarize_batch_size", 25))
        batches = [items[i : i + n] for i in range(0, len(items), n)]
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(self._summarize_batch, batches))
        log.info("summarised %d items in %d batches", sum(1 for i in items if i.summary), len(batches))

    def rank(self, items: list[Item], top_n: int) -> tuple[list[str], list[list[str]]]:
        payload = [
            {"id": it.uid, "source": it.source_name, "headline": it.display_headline(), "summary": it.summary, "score": it.score}
            for it in items
        ]
        prompt = RANK_PROMPT.format(top_n=top_n, items=json.dumps(payload, ensure_ascii=False))
        try:
            data = _parse_json(self._ask(self.rank_model, prompt, max_tokens=1500))
            top = [str(x) for x in data.get("top", [])][:top_n]
            dups = [[str(x) for x in g] for g in data.get("duplicates", []) if isinstance(g, list) and len(g) > 1]
            return top, dups
        except Exception as e:
            log.warning("rank call failed, falling back to scores: %s", e)
            return [], []

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import load_config
from .pipeline import build_brief
from .render import write_site


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="daily-brief", description="Build the daily news one-pager.")
    p.add_argument("-c", "--config", default="sources.yaml")
    p.add_argument("--no-llm", action="store_true", help="skip Claude; use source teasers")
    p.add_argument("--no-images", action="store_true", help="skip og:image lookups")
    p.add_argument("--notify", action="store_true", help="send the link via configured channels")
    p.add_argument("--open", action="store_true", help="open the page in the browser when done")
    p.add_argument("--notify-only", action="store_true", help="only send the notification for the last build")
    p.add_argument("--only", help="comma-separated source ids to include (for testing)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    load_dotenv()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = load_config(args.config)
    latest = Path(cfg.get("output_dir", "site")) / "latest.json"
    if args.notify_only:
        return _send(json.loads(latest.read_text(encoding="utf-8")))
    if args.only:
        wanted = set(args.only.split(","))
        cfg.sources = [s for s in cfg.sources if s["id"] in wanted]
    if args.no_images:
        cfg.settings["fetch_images"] = False

    use_llm = not args.no_llm
    if use_llm and cfg.get("llm_backend", "claude-cli") == "api" and not os.getenv("ANTHROPIC_API_KEY"):
        logging.error("llm_backend is 'api' but ANTHROPIC_API_KEY is not set; use claude-cli or --no-llm")
        return 2

    brief = build_brief(cfg, use_llm=use_llm)
    path = write_site(brief, cfg.settings)
    print(f"wrote {path} ({brief.item_count} stories)")
    if args.open:
        import webbrowser

        webbrowser.open(path.resolve().as_uri())

    top = "\n".join(f"• {it.display_headline()}" for it in brief.top)
    msg = {
        "title": f"{cfg.get('title', 'Daily Brief')} · {brief.now.strftime('%a %-d %b')}",
        "text": f"{top}\n{brief.item_count} stories" if top else f"{brief.item_count} stories",
    }
    latest.write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
    return _send(msg) if args.notify else 0


def _send(msg: dict) -> int:
    from .notify import notify_all

    sent = notify_all(msg["title"], msg["text"], os.getenv("SITE_URL", ""))
    print("notified via: " + (", ".join(sent) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

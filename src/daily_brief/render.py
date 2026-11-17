from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader

from .pipeline import Brief


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


# Fixed hues for the default sections; anything else gets a stable hash-based hue.
_HUES = {"TLDR": 215, "Handelsblatt": 18, "Inside Paradeplatz": 152, "South China Morning Post": 275, "SRF News": 0, "BelTA": 350}


def _hue(name: str) -> int:
    return _HUES.get(name, int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 360)


def render(brief: Brief, settings: dict, archive_dates: list[str] | None = None) -> str:
    env = Environment(loader=PackageLoader("daily_brief", "templates"), autoescape=True)
    env.filters["slug"] = _slug
    env.filters["domain"] = _domain
    env.filters["hue"] = _hue
    tpl = env.get_template("brief.html.j2")
    return tpl.render(
        b=brief,
        title=settings.get("title", "Daily Brief"),
        lang_code=settings.get("lang_code", "en"),
        date_long=brief.now.strftime("%A, %-d %B %Y"),
        generated=brief.now.strftime("%H:%M %Z"),
        archive_dates=archive_dates or [],
    )


def write_site(brief: Brief, settings: dict) -> Path:
    out = Path(settings.get("output_dir", "site"))
    archive = out / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    day = brief.now.strftime("%Y-%m-%d")
    dates = sorted({p.stem for p in archive.glob("*.html") if p.stem != "index"} | {day}, reverse=True)
    html = render(brief, settings, archive_dates=dates[:14])
    (archive / f"{day}.html").write_text(html.replace('href="archive/', 'href="'), encoding="utf-8")
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").touch()
    return out / "index.html"

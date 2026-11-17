from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Config:
    settings: dict[str, Any]
    sources: list[dict[str, Any]]

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def section_of(self, source: dict[str, Any]) -> str:
        return source.get("section", source["name"])

    def sections(self) -> list[dict[str, Any]]:
        """Page sections in display order: configured ones first, then any a source introduces."""
        conf = {s["name"]: dict(s) for s in self.get("sections", []) or []}
        order = [s["name"] for s in self.get("sections", []) or []]
        for src in self.sources:
            name = self.section_of(src)
            if name not in conf:
                conf[name] = {"name": name, "max_items": src.get("max_items")}
                order.append(name)
        out = []
        for name in order:
            if any(self.section_of(s) == name for s in self.sources):
                c = conf[name]
                c["max_items"] = int(c.get("max_items") or self.get("max_items_per_source", 5))
                out.append(c)
        return out


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = [s for s in data.get("sources", []) if s.get("enabled", True)]
    ids = [s["id"] for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source ids in config")
    return Config(settings=data.get("settings", {}), sources=sources)

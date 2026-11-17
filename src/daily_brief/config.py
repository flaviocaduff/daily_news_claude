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

    def max_items(self, source: dict[str, Any]) -> int:
        return int(source.get("max_items", self.get("max_items_per_source", 5)))


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = [s for s in data.get("sources", []) if s.get("enabled", True)]
    ids = [s["id"] for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate source ids in config")
    return Config(settings=data.get("settings", {}), sources=sources)

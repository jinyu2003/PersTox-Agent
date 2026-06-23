from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "config" / "kb_sources.json"


@dataclass(frozen=True)
class SourceFilter:
    source_ids: set[str] | None = None
    strategies: set[str] | None = None
    modules: set[str] | None = None
    mvp_only: bool = False
    include_manual: bool = False
    include_large: bool = False
    include_api_examples: bool = False


def load_sources(path: Path = DEFAULT_MANIFEST) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"Invalid manifest: {path}")
    return sources


def iter_sources(sources: Iterable[dict], filters: SourceFilter) -> Iterable[dict]:
    for source in sources:
        source_id = source["id"]
        if filters.source_ids and source_id not in filters.source_ids:
            continue
        if filters.strategies and source.get("agent_strategy") not in filters.strategies:
            continue
        if filters.modules and source.get("module") not in filters.modules:
            continue
        if filters.mvp_only and not source.get("mvp", False):
            continue
        if source.get("access") == "manual_license" and not filters.include_manual:
            continue
        if source.get("large", False) and not filters.include_large:
            continue
        if source.get("access") == "api" and not filters.include_api_examples:
            continue
        yield source


def source_summary(source: dict) -> str:
    return (
        f"{source['id']:<28} "
        f"{source.get('tier', '-'):<8} "
        f"{source.get('agent_strategy', '-'):<24} "
        f"{source.get('access', '-'):<16} "
        f"{source.get('module', '-')}"
    )

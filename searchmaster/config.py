"""Run configuration: search modes, limits and API keys (from environment)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .http import Http
from .textutil import split_names

MODES = {
    # per-source limit, pages to analyse, expansion queries, files to download
    "quick": dict(limit=10, fetch_pages=0, expansions=0, download_images=15, download_files=0),
    "normal": dict(limit=25, fetch_pages=25, expansions=0, download_images=40, download_files=10),
    "deep": dict(limit=50, fetch_pages=60, expansions=6, download_images=120, download_files=30),
}

# "What to look for": a group of result categories (and the sources that can produce them).
ONLY_GROUPS: dict[str, list[str]] = {
    "image": ["image"],
    "video": ["video"],
    "audio": ["audio"],
    "code": ["code"],
    "file": ["file", "paper", "book"],
    "mod": ["mod"],
    "discussion": ["discussion"],
    "text": ["wiki", "article", "news", "paper", "book"],
}

# Optional keys. Nothing is required: sources without keys are skipped.
ENV_KEYS = [
    "GITHUB_TOKEN",
    "GH_SEARCH_TOKEN",
    "BRAVE_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_CSE_ID",
    "YOUTUBE_API_KEY",
    "RAWG_API_KEY",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "SEARXNG_URL",
    "STACKEXCHANGE_KEY",
    "ANTHROPIC_API_KEY",
]


@dataclass
class Config:
    query: str
    mode: str = "normal"
    langs: list[str] = field(default_factory=lambda: ["ru", "en"])
    limit: int = 25
    fetch_pages: int = 25
    expansions: int = 0
    download: bool = True
    download_images: int = 40
    download_files: int = 10
    code_zips: int = 0
    max_file_mb: float = 25.0
    max_total_mb: float = 400.0
    allow_ext: set[str] = field(default_factory=set)
    min_score: float = 0.12
    names: list[str] = field(default_factory=list)  # several names of one subject ("A / B")
    exact: bool = False  # only results containing a name as a whole phrase
    only: list[str] = field(default_factory=list)  # ONLY_GROUPS keys
    ai: str = "auto"  # auto | on | off
    ai_model: str = "claude-opus-5-5"
    workers: int = 8
    keys: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, query: str, mode: str = "normal", **overrides) -> "Config":
        preset = MODES.get(mode, MODES["normal"])
        values = {**preset, **{k: v for k, v in overrides.items() if v is not None}}
        keys = {name: os.environ[name].strip() for name in ENV_KEYS if os.environ.get(name, "").strip()}
        names, quoted = split_names(query)
        values["exact"] = bool(values.get("exact")) or quoted
        values["only"] = [g for g in (values.get("only") or []) if g in ONLY_GROUPS]
        return cls(query=query.strip(), mode=mode, keys=keys, names=names, **values)

    @property
    def wanted_categories(self) -> set[str]:
        return {c for g in self.only for c in ONLY_GROUPS[g]}

    def key(self, name: str) -> str | None:
        return self.keys.get(name)


@dataclass
class Context:
    config: Config
    http: Http
    log: logging.Logger

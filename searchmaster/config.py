"""Run configuration.  Optional API keys come from the environment or a .env file."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Honest bot-style User-Agent: we identify ourselves and never pretend to be a browser.
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; SearchMaster/1.0; +https://github.com/AbyzeR-1337/SearchMaster)"
# Wikimedia and some APIs ask for a descriptive, contactable User-Agent.
BOT_USER_AGENT = "SearchMaster/1.0 (+https://github.com/AbyzeR-1337/SearchMaster; research tool)"

FACETS = [
    "general",      # overview, wiki, reviews
    "images",       # screenshots, artwork, concept art
    "video",        # trailers, gameplay, talks
    "code",         # source code, mods, tools, decompilation projects
    "files",        # manuals, pdf, soundtracks, mod files, file formats
    "discussions",  # forums, reddit, Q&A
    "technical",    # engine, file formats, reverse engineering, datamining
    "history",      # development history, beta, cut content, archives
    "news",         # patch notes, updates, announcements
]


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines); never overrides real env vars."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass
class Config:
    query: str
    aliases: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["en", "ru"])
    facets: list[str] = field(default_factory=lambda: list(FACETS))
    sources: list[str] | None = None          # None = every available source
    exclude_sources: list[str] = field(default_factory=list)

    max_per_source: int = 30                 # results per (source, query)
    web_queries_per_engine: int = 14         # how many expanded queries web engines run
    deep: int = 25                           # pages to download & analyse in depth
    crawl_depth: int = 0                     # follow relevant links N levels from deep pages
    rounds: int = 2                          # 2 = snowball: re-search with discovered terms
    strict: bool = True                      # drop results that never mention the topic
    min_score: float = 0.0

    workers: int = 8
    timeout: float = 20.0
    source_budget: float = 240.0             # max seconds one source may spend per round
    per_host_delay: float = 1.0              # politeness: seconds between hits to one host
    max_page_bytes: int = 3_000_000
    respect_robots: bool = True
    cache_dir: str | None = ".cache/searchmaster"
    cache_ttl_hours: float = 24.0

    download: list[str] = field(default_factory=list)  # categories to download
    max_downloads: int = 200
    max_file_mb: float = 50.0

    ai: bool = False
    ai_model: str = "claude-opus-5-5"
    ai_effort: str = "medium"

    out_dir: str = "results"
    formats: list[str] = field(default_factory=lambda: ["html", "md", "json"])
    verbose: bool = False

    user_agent: str = DEFAULT_USER_AGENT
    bot_user_agent: str = BOT_USER_AGENT

    # --- optional credentials --------------------------------------------
    github_token: str | None = field(default_factory=lambda: os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))
    brave_api_key: str | None = field(default_factory=lambda: os.getenv("BRAVE_API_KEY"))
    google_api_key: str | None = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY"))
    google_cse_id: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CSE_ID"))
    searxng_url: str | None = field(default_factory=lambda: os.getenv("SEARXNG_URL"))
    youtube_api_key: str | None = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY"))
    stackexchange_key: str | None = field(default_factory=lambda: os.getenv("STACKEXCHANGE_KEY"))

    @property
    def all_terms(self) -> list[str]:
        return [self.query, *self.aliases]


# Depth presets shared by the CLI (--preset), the web UI and GitHub Actions.
PRESETS: dict[str, dict[str, Any]] = {
    "fast": {"web_queries_per_engine": 6, "deep": 8, "crawl_depth": 0, "max_per_source": 15, "rounds": 1},
    "normal": {"web_queries_per_engine": 14, "deep": 25, "crawl_depth": 0, "max_per_source": 30, "rounds": 2},
    "deep": {"web_queries_per_engine": 22, "deep": 50, "crawl_depth": 1, "max_per_source": 40, "rounds": 2},
}
DOWNLOAD_CHOICES = ("images", "video", "audio", "docs", "code", "archives", "gamedata", "repos")
_LANG_RE = re.compile(r"^[a-z]{2,3}$")


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,;\n]", value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def config_from_params(params: dict[str, Any], *, out_dir: str, base: dict[str, Any] | None = None,
                       max_downloads_cap: int = 300) -> Config:
    """Validate untrusted parameters (web form, issue form, workflow inputs) into a Config."""
    query = " ".join(str(params.get("query", "")).split())
    if not query or len(query) > 200:
        raise ValueError("Введите запрос (до 200 символов)")
    aliases = [a[:100] for a in _as_list(params.get("aliases"))][:5]
    preset = PRESETS.get(str(params.get("preset") or "normal").strip().lower(), PRESETS["normal"])
    facets = [f for f in _as_list(params.get("facets")) if f in FACETS] or list(FACETS)
    langs = [lang for lang in _as_list(params.get("languages")) if _LANG_RE.match(lang)][:5]
    download = [d for d in _as_list(params.get("download")) if d in DOWNLOAD_CHOICES or d == "all"]
    try:
        max_downloads = int(params.get("max_downloads") or 60)
    except (TypeError, ValueError):
        raise ValueError("max_downloads должно быть числом") from None
    cfg: dict[str, Any] = dict(base or {})
    cfg.update(preset)
    return Config(query=query, aliases=aliases, facets=facets, languages=langs or ["en", "ru"],
                  download=download, max_downloads=max(0, min(max_downloads, max_downloads_cap)),
                  ai=str(params.get("ai")).lower() in ("true", "1", "yes", "on"), out_dir=out_dir, **cfg)

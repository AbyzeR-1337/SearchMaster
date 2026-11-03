"""Run configuration.  Optional API keys come from the environment or a .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

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

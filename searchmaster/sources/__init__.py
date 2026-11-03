"""Registry of every search source."""

from __future__ import annotations

from ..config import Config
from .archive import InternetArchiveSource, WaybackSource
from .base import Source, classify_url
from .code import CodebergSource, GitHubSource, GitLabSource, SourcegraphSource
from .community import HackerNewsSource, RedditSource, StackExchangeSource
from .games import SteamSource
from .media import BingImagesSource, OpenverseSource, YouTubeSource
from .misc import OpenAlexSource, PackagesSource
from .web import BingSource, BraveSource, DuckDuckGoSource, GoogleCSESource, SearxngSource
from .wiki import CommonsSource, FandomSource, PCGamingWikiSource, WikipediaSource

ALL_SOURCES: list[type[Source]] = [
    # web engines
    BingSource, DuckDuckGoSource, BraveSource, GoogleCSESource, SearxngSource,
    # knowledge
    WikipediaSource, FandomSource, PCGamingWikiSource, SteamSource,
    # code
    GitHubSource, SourcegraphSource, GitLabSource, CodebergSource, PackagesSource,
    # media
    YouTubeSource, BingImagesSource, CommonsSource, OpenverseSource,
    # community
    RedditSource, HackerNewsSource, StackExchangeSource,
    # archives / research
    InternetArchiveSource, OpenAlexSource,
]
# Sources that run only in the follow-up phase (they need data from round 1).
FOLLOWUP_SOURCES: list[type[Source]] = [WaybackSource]

SOURCE_BY_NAME = {cls.name: cls for cls in ALL_SOURCES + FOLLOWUP_SOURCES}


def select_sources(config: Config) -> tuple[list[type[Source]], list[str]]:
    """Return (enabled source classes, notes about skipped ones)."""
    notes: list[str] = []
    wanted = set(config.sources) if config.sources else None
    unknown = (wanted or set()) - set(SOURCE_BY_NAME)
    if unknown:
        notes.append(f"неизвестные источники проигнорированы: {', '.join(sorted(unknown))}")
    chosen: list[type[Source]] = []
    for cls in ALL_SOURCES:
        if wanted is not None and cls.name not in wanted:
            continue
        if cls.name in config.exclude_sources:
            continue
        if not cls.available(config):
            if wanted is not None:
                notes.append(f"{cls.name}: нужен ключ ({', '.join(cls.requires)}) — пропущен")
            continue
        chosen.append(cls)
    return chosen, notes


__all__ = ["ALL_SOURCES", "FOLLOWUP_SOURCES", "SOURCE_BY_NAME", "Source", "classify_url", "select_sources"]

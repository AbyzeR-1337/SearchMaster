"""Registry of all search sources and handy presets."""

from __future__ import annotations

from .archives import Arxiv, InternetArchive, OpenLibrary, Wayback
from .base import Source
from .code import Codeberg, GitHub, GitLab
from .community import HackerNews, Reddit, StackExchange
from .games import GameBanana, Modrinth, Rawg, Speedrun, Steam
from .media import Openverse, YouTube
from .web import WebSearch
from .wiki import Commons, Fandom, Wikipedia

ALL_SOURCES: dict[str, type[Source]] = {
    cls.name: cls
    for cls in (
        WebSearch,
        Wikipedia,
        Fandom,
        Steam,
        Rawg,
        GitHub,
        GitLab,
        Codeberg,
        InternetArchive,
        Commons,
        Openverse,
        YouTube,
        Reddit,
        HackerNews,
        StackExchange,
        GameBanana,
        Modrinth,
        Speedrun,
        Arxiv,
        OpenLibrary,
    )
}

PRESETS: dict[str, list[str]] = {
    "all": list(ALL_SOURCES),
    "game": ["web", "steam", "rawg", "wikipedia", "fandom", "commons", "archive", "youtube", "github", "gamebanana",
             "speedrun", "reddit", "stackexchange", "openverse"],
    "code": ["web", "github", "gitlab", "codeberg", "stackexchange", "hackernews", "archive", "wikipedia"],
    "media": ["web", "commons", "openverse", "steam", "rawg", "fandom", "youtube", "archive", "reddit"],
    "files": ["web", "archive", "github", "gitlab", "codeberg", "openlibrary", "commons", "arxiv"],
    "text": ["web", "wikipedia", "fandom", "hackernews", "reddit", "stackexchange", "arxiv", "openlibrary", "archive"],
}


def resolve_sources(spec: str) -> list[str]:
    """``"all"``, a preset name, or a comma list; ``-name`` removes a source."""
    chosen: list[str] = []
    removed: set[str] = set()
    for part in (spec or "all").replace(" ", "").split(","):
        if not part:
            continue
        if part.startswith("-"):
            removed.add(part[1:])
        elif part in PRESETS:
            chosen += PRESETS[part]
        elif part in ALL_SOURCES:
            chosen.append(part)
        else:
            raise ValueError(f"Неизвестный источник или набор: {part!r}. См. --list-sources")
    if not chosen:
        chosen = list(PRESETS["all"])
    seen: set[str] = set()
    return [s for s in chosen if not (s in seen or seen.add(s)) and s not in removed]


__all__ = ["ALL_SOURCES", "PRESETS", "Source", "Wayback", "resolve_sources"]

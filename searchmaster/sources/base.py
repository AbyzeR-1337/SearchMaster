"""Base class for sources plus URL -> result-kind classification."""

from __future__ import annotations

import logging
import re
from typing import Iterable, Iterator
from urllib.parse import urlparse

from ..config import Config
from ..models import (
    ARCHIVE, CODE, DISCUSSION, FILE, GAME, IMAGE, NEWS, PAGE, PAPER, REPO, VIDEO, WIKI, Result,
)
from ..net import HttpClient
from ..utils import domain_of, file_category

_DISCUSSION_HOSTS = (
    "reddit.com", "stackexchange.com", "stackoverflow.com", "news.ycombinator.com",
    "steamcommunity.com", "resetera.com", "neogaf.com", "gamefaqs.gamespot.com",
    "4pda.to", "forum.", "forums.", "discourse.", "boards.", "playground.ru", "dtf.ru",
    "pikabu.ru", "habr.com", "quora.com", "tieba.baidu.com",
)
_WIKI_HOSTS = ("wikipedia.org", "fandom.com", "wikia.com", "wiki.gg", "pcgamingwiki.com",
               "tcrf.net", "wikidata.org", "miraheze.org", "wiki.")
_VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "twitch.tv", "dailymotion.com",
                "rutube.ru", "bilibili.com", "nicovideo.jp", "vk.com/video")
_CODE_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org", "sourceforge.net",
               "sourcegraph.com", "gitee.com", "sr.ht")
_GAME_HOSTS = ("store.steampowered.com", "gog.com", "itch.io", "mobygames.com", "igdb.com",
               "howlongtobeat.com", "metacritic.com", "epicgames.com", "nintendo.com", "playstation.com",
               "xbox.com", "steamdb.info", "rawg.io")
_NEWS_RE = re.compile(r"(patch|release)[-_ ]?notes|/news/|changelog|/announcements?/", re.I)


def classify_url(url: str) -> str:
    """Best-effort guess of what a URL points at."""
    host = domain_of(url)
    path = urlparse(url).path
    cat = file_category(url)
    if host == "raw.githubusercontent.com" and cat in (None, "code", "document"):
        return CODE
    if any(host.endswith(h) for h in _CODE_HOSTS):
        parts = [p for p in path.split("/") if p]
        if host.endswith(("github.com", "gitlab.com", "codeberg.org", "bitbucket.org")):
            if len(parts) >= 3 and parts[2] in ("issues", "discussions", "pull", "pulls"):
                return DISCUSSION
            if len(parts) > 3 and parts[2] in ("blob", "tree", "-", "src", "raw"):
                return IMAGE if cat == "image" else CODE
            if len(parts) >= 3 and parts[2] == "releases":
                return FILE if cat else REPO
            if len(parts) == 2:
                return REPO
        if not cat:
            return REPO if "sourceforge.net" in host else CODE
    if cat == "image":
        return IMAGE
    if cat == "video":
        return VIDEO
    if cat:
        return FILE
    if any(h in host or h in url for h in _VIDEO_HOSTS):
        return VIDEO
    if "archive.org" in host:
        return ARCHIVE
    if any(h in host for h in _WIKI_HOSTS) or "/wiki/" in path:
        return WIKI
    if any(h in host for h in _DISCUSSION_HOSTS) or re.search(r"/(forum|forums|thread|threads|topic|t)/", path):
        return DISCUSSION
    if any(host.endswith(h) or host == h for h in _GAME_HOSTS):
        return GAME
    if host.endswith(("arxiv.org", "doi.org", "researchgate.net", "semanticscholar.org")):
        return PAPER
    if _NEWS_RE.search(url):
        return NEWS
    return PAGE


class Source:
    """A place we can search.  Subclasses implement `search`."""

    name: str = "base"
    description: str = ""
    facets: frozenset[str] = frozenset({"general"})
    requires: tuple[str, ...] = ()          # config attributes that must be set (API keys)
    default_enabled: bool = True

    def __init__(self, http: HttpClient, config: Config):
        self.http = http
        self.config = config
        self.log = logging.getLogger(f"searchmaster.{self.name}")

    @classmethod
    def available(cls, config: Config) -> bool:
        return all(getattr(config, attr, None) for attr in cls.requires)

    @property
    def limit(self) -> int:
        return self.config.max_per_source

    def relevant(self, plan) -> bool:
        return bool(self.facets & set(plan.facets)) or plan.snowball_only

    def search(self, plan) -> Iterator[Result]:  # pragma: no cover - interface
        raise NotImplementedError

    # helpers --------------------------------------------------------------
    def result(self, url: str, title: str, *, query: str = "", kind: str | None = None, **kw) -> Result:
        return Result(url=url, title=title, kind=kind or classify_url(url), source=self.name, query=query, **kw)


def take(it: Iterable[Result], n: int) -> list[Result]:
    out = []
    for r in it:
        out.append(r)
        if len(out) >= n:
            break
    return out

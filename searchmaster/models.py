"""Data model shared by every source, the analyzer and the reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .utils import canonical_url, domain_of

# Result kinds.  Every source maps what it finds onto one of these.
PAGE = "page"            # generic web page / article
WIKI = "wiki"            # encyclopedia / wiki article
IMAGE = "image"          # screenshot, artwork, photo
VIDEO = "video"          # trailer, gameplay, talk
CODE = "code"            # a code snippet / file inside a repository
REPO = "repo"            # a whole source repository
FILE = "file"            # direct link to a downloadable file (pdf, zip, pak, ...)
DISCUSSION = "discussion"  # forum thread, Reddit post, Q&A
ARCHIVE = "archive"      # Internet Archive item / Wayback snapshot
NEWS = "news"            # patch notes, announcements
PAPER = "paper"          # scholarly work
PACKAGE = "package"      # library on npm / crates.io
GAME = "game"            # store / database entry describing the game itself

ALL_KINDS = [PAGE, WIKI, IMAGE, VIDEO, CODE, REPO, FILE, DISCUSSION, ARCHIVE, NEWS, PAPER, PACKAGE, GAME]

KIND_LABELS_RU = {
    PAGE: "Страницы",
    WIKI: "Вики",
    IMAGE: "Скриншоты и изображения",
    VIDEO: "Видео",
    CODE: "Фрагменты кода",
    REPO: "Репозитории",
    FILE: "Файлы",
    DISCUSSION: "Обсуждения",
    ARCHIVE: "Архивы",
    NEWS: "Новости и патчи",
    PAPER: "Научные работы",
    PACKAGE: "Пакеты/библиотеки",
    GAME: "Карточки игры",
}


@dataclass
class Result:
    url: str
    title: str
    kind: str = PAGE
    source: str = ""
    snippet: str = ""
    content: str = ""                 # full extracted text (filled by the crawler)
    thumbnail: str | None = None      # preview image (for images / videos / repos)
    date: str | None = None           # ISO date if known
    author: str | None = None
    query: str = ""                   # the concrete query string that found it
    meta: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    found_via: list[str] = field(default_factory=list)  # "source: query" pairs
    crawled: bool = False

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip() or self.url
        if self.source and not self.found_via:
            self.found_via = [f"{self.source}: {self.query}" if self.query else self.source]

    @property
    def key(self) -> str:
        return canonical_url(self.url)

    @property
    def domain(self) -> str:
        return domain_of(self.url)

    def text_for_scoring(self) -> str:
        parts = [self.title, self.snippet, self.url, " ".join(map(str, self.meta.get("tags", [])))]
        if self.content:
            parts.append(self.content[:20000])
        return " ".join(p for p in parts if p)

    def merge(self, other: "Result") -> None:
        """Fold a duplicate found by another source/query into this one."""
        for via in other.found_via:
            if via not in self.found_via:
                self.found_via.append(via)
        if len(other.snippet) > len(self.snippet):
            self.snippet = other.snippet
        if len(other.content) > len(self.content):
            self.content = other.content
            self.crawled = self.crawled or other.crawled
        if (not self.title or self.title == self.url) and other.title:
            self.title = other.title
        self.thumbnail = self.thumbnail or other.thumbnail
        self.date = self.date or other.date
        self.author = self.author or other.author
        for k, v in other.meta.items():
            self.meta.setdefault(k, v)
        # A specialised kind beats the generic "page".
        if self.kind == PAGE and other.kind != PAGE:
            self.kind = other.kind

    def to_dict(self, *, content_limit: int | None = 4000) -> dict[str, Any]:
        d = asdict(self)
        if content_limit is not None and len(d["content"]) > content_limit:
            d["content"] = d["content"][:content_limit] + "…"
        d["domain"] = self.domain
        return d


class SourceBlocked(Exception):
    """Raised when a site answers with a captcha / 403 / rate-limit wall."""

"""Core data structures shared by every part of SearchMaster."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from requests.utils import requote_uri

# Category id -> human readable title (Russian UI).
CATEGORIES: dict[str, str] = {
    "game": "🎮 Игры (карточки)",
    "wiki": "📚 Вики и справка",
    "article": "📰 Статьи и страницы",
    "news": "🗞 Новости и патчноуты",
    "image": "🖼 Изображения и скриншоты",
    "video": "🎬 Видео",
    "audio": "🎵 Аудио и музыка",
    "code": "💻 Исходный код и репозитории",
    "file": "📦 Файлы и архивы",
    "mod": "🧩 Моды и фан-контент",
    "discussion": "💬 Обсуждения",
    "paper": "🎓 Научные работы",
    "book": "📖 Книги",
}

CATEGORY_ORDER = list(CATEGORIES)


@dataclass
class Result:
    url: str
    title: str
    source: str
    category: str
    snippet: str = ""
    thumbnail: str | None = None
    date: str | None = None  # ISO-8601 (or just a year) when known
    author: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    content: str = ""  # text extracted from the page (not exported in full)
    local_path: str | None = None  # path of a downloaded copy, relative to output dir

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            self.category = "article"
        if not self.sources:
            self.sources = [self.source]
        self.url = requote_uri((self.url or "").strip())
        if self.thumbnail:
            self.thumbnail = requote_uri(self.thumbnail.strip())
        self.title = str(self.title or self.url).strip()
        self.snippet = (self.snippet or "").strip()

    def to_dict(self, content_chars: int = 600) -> dict[str, Any]:
        data = asdict(self)
        data["content"] = self.content[:content_chars]
        return data


@dataclass
class SourceStatus:
    name: str
    title: str
    state: str = "pending"  # ok | empty | error | skipped
    count: int = 0
    message: str = ""
    seconds: float = 0.0
    slow: bool = False  # timed out once: not asked again during this search

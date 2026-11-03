"""Base class for all search sources."""

from __future__ import annotations

from ..config import Context
from ..models import Result


class Source:
    #: unique id used on the command line (``--sources github,steam``)
    name: str = "base"
    #: human readable title
    title: str = ""
    #: short description shown by ``--list-sources``
    description: str = ""
    #: env variables required for the source to work (all of them)
    requires: tuple[str, ...] = ()
    #: authority multiplier for relevance scoring
    weight: float = 1.0
    #: whether the source is useful for follow-up (expanded) queries in deep mode
    expandable: bool = False

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.http = ctx.http
        self.config = ctx.config

    def available(self) -> tuple[bool, str]:
        missing = [key for key in self.requires if not self.config.key(key)]
        if missing:
            return False, "нужен ключ: " + ", ".join(missing)
        return True, ""

    def search(self, query: str, limit: int) -> list[Result]:  # pragma: no cover - interface
        raise NotImplementedError

    def result(self, **kwargs) -> Result:
        kwargs.setdefault("source", self.name)
        return Result(**kwargs)

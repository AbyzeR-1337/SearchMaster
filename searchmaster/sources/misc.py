"""Packages (npm, crates.io) and scholarly works (OpenAlex)."""

from __future__ import annotations

from typing import Iterator

from ..models import PACKAGE, PAPER, Result
from ..utils import truncate
from .base import Source


class PackagesSource(Source):
    name = "packages"
    description = "npm и crates.io: библиотеки/парсеры форматов, связанные с темой"
    facets = frozenset({"code", "technical"})

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("code", limit=2):
            data = self.http.get_json("https://registry.npmjs.org/-/v1/search",
                                      params={"text": q, "size": min(self.limit, 25)})
            for obj in data.get("objects", []):
                p = obj.get("package", {})
                links = p.get("links", {})
                yield self.result(links.get("npm") or f"https://www.npmjs.com/package/{p.get('name')}",
                                  f"npm: {p.get('name')}", query=q, kind=PACKAGE,
                                  snippet=p.get("description") or "", date=(p.get("date") or "")[:10] or None,
                                  meta={"repository": links.get("repository"), "tags": p.get("keywords", []),
                                        "version": p.get("version")})
            data = self.http.get_json("https://crates.io/api/v1/crates",
                                      params={"q": q, "per_page": min(self.limit, 25)},
                                      headers={"User-Agent": self.config.bot_user_agent})
            for c in data.get("crates", []):
                yield self.result(f"https://crates.io/crates/{c.get('id')}", f"crate: {c.get('name')}", query=q,
                                  kind=PACKAGE, snippet=c.get("description") or "",
                                  date=(c.get("updated_at") or "")[:10] or None,
                                  meta={"repository": c.get("repository"), "downloads": c.get("downloads", 0),
                                        "tags": c.get("keywords") or []})


class OpenAlexSource(Source):
    name = "openalex"
    description = "OpenAlex: научные статьи и исследования по теме (250+ млн работ)"
    facets = frozenset({"history", "technical"})

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("general", limit=1):
            data = self.http.get_json("https://api.openalex.org/works",
                                      params={"search": q, "per-page": min(self.limit, 25)})
            for w in data.get("results", []):
                loc = (w.get("primary_location") or {})
                url = (w.get("open_access") or {}).get("oa_url") or loc.get("landing_page_url") or w.get("doi") or w.get("id")
                if not url:
                    continue
                authors = ", ".join(a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])[:3])
                yield self.result(url, w.get("display_name") or "", query=q, kind=PAPER,
                                  snippet=truncate(_abstract(w.get("abstract_inverted_index")), 500),
                                  date=str(w.get("publication_year") or "") or None, author=authors,
                                  meta={"cited_by": w.get("cited_by_count", 0), "doi": w.get("doi"),
                                        "open_access": (w.get("open_access") or {}).get("is_oa")})


def _abstract(inv: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            pos[i] = word
    return " ".join(pos[i] for i in sorted(pos))

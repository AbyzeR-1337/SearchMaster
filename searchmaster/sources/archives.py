"""Internet Archive (files, software, texts, media), arXiv papers, Open Library books."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..models import Result
from ..textutil import strip_html, truncate
from .base import Source

IA_CATEGORY = {
    "texts": "file",
    "data": "file",
    "collection": "file",
    "image": "image",
    "movies": "video",
    "audio": "audio",
    "etree": "audio",
    "web": "article",
}


class InternetArchive(Source):
    name = "archive"
    title = "Internet Archive"
    description = "Документы, книги, изображения, видео и аудио с archive.org"
    weight = 1.15
    expandable = True

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://archive.org/advancedsearch.php",
            params={
                # Software items for commercial games are very often unauthorized copies,
                # so programs/games themselves are deliberately left out of the search.
                "q": f"({query}) AND NOT mediatype:software",
                "fl[]": ["identifier", "title", "description", "mediatype", "date", "downloads", "creator", "item_size"],
                "sort[]": "downloads desc",
                "rows": min(limit * 2, 100),
                "output": "json",
            },
        )
        results = []
        for doc in data.get("response", {}).get("docs", []):
            ident = doc.get("identifier")
            if not ident:
                continue
            desc = doc.get("description")
            if isinstance(desc, list):
                desc = " ".join(map(str, desc))
            creator = doc.get("creator")
            if isinstance(creator, list):
                creator = ", ".join(map(str, creator[:3]))
            mediatype = doc.get("mediatype", "")
            title = doc.get("title") or ident
            if isinstance(title, list):
                title = title[0]
            results.append(
                self.result(
                    url=f"https://archive.org/details/{ident}",
                    title=str(title),
                    category=IA_CATEGORY.get(mediatype, "file"),
                    snippet=truncate(strip_html(desc), 400),
                    thumbnail=f"https://archive.org/services/img/{ident}",
                    date=(doc.get("date") or "")[:10] or None,
                    author=creator,
                    meta={
                        "ia_identifier": ident,
                        "mediatype": mediatype,
                        "downloads": doc.get("downloads"),
                        "size": doc.get("item_size"),
                    },
                )
            )
        return results


class Wayback(Source):
    """Not a search engine by itself: finds archived copies of the query's domain(s)."""

    name = "wayback"
    title = "Wayback Machine"
    description = "Архивные копии сайтов, найденных в ходе поиска (официальный сайт и т.п.)"

    def search(self, query: str, limit: int) -> list[Result]:
        return []  # used through `snapshots_for` by the pipeline

    def snapshots_for(self, urls: list[str]) -> list[Result]:
        results = []
        for url in urls[:5]:
            try:
                data = self.http.get_json("https://archive.org/wayback/available", params={"url": url, "timestamp": "2000"})
            except Exception:
                continue
            snap = (data.get("archived_snapshots") or {}).get("closest") or {}
            if not snap.get("available"):
                continue
            ts = snap.get("timestamp", "")
            results.append(
                self.result(
                    url=f"https://web.archive.org/web/*/{url}",
                    title=f"Архив сайта {url} (Wayback Machine)",
                    category="article",
                    snippet=f"Самая ранняя доступная копия: {ts[:4]}-{ts[4:6]}-{ts[6:8]} — {snap.get('url')}",
                    date=f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else None,
                    meta={"trusted": True, "first_snapshot": snap.get("url")},
                )
            )
        return results


class Arxiv(Source):
    name = "arxiv"
    title = "arXiv"
    description = "Научные статьи (полезно для технических тем)"

    NS = {"a": "http://www.w3.org/2005/Atom"}

    def search(self, query: str, limit: int) -> list[Result]:
        text = self.http.get_text(
            "https://export.arxiv.org/api/query",
            params={"search_query": f'all:"{query}"', "max_results": min(limit, 50), "sortBy": "relevance"},
        )
        root = ET.fromstring(text)
        results = []
        for entry in root.findall("a:entry", self.NS):
            link = entry.findtext("a:id", default="", namespaces=self.NS)
            pdf = next(
                (l.get("href") for l in entry.findall("a:link", self.NS) if l.get("title") == "pdf"), None
            )
            authors = [a.findtext("a:name", default="", namespaces=self.NS) for a in entry.findall("a:author", self.NS)]
            results.append(
                self.result(
                    url=link,
                    title=" ".join(entry.findtext("a:title", default="", namespaces=self.NS).split()),
                    category="paper",
                    snippet=truncate(" ".join(entry.findtext("a:summary", default="", namespaces=self.NS).split()), 400),
                    date=entry.findtext("a:published", default="", namespaces=self.NS)[:10] or None,
                    author=", ".join(authors[:4]),
                    meta={"pdf": pdf, "file_url": pdf},
                )
            )
        return results


class OpenLibrary(Source):
    name = "openlibrary"
    title = "Open Library"
    description = "Книги, артбуки, гайды и руководства"

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json("https://openlibrary.org/search.json", params={"q": query, "limit": min(limit, 50)})
        results = []
        for doc in data.get("docs", []):
            cover = doc.get("cover_i")
            results.append(
                self.result(
                    url=f"https://openlibrary.org{doc.get('key')}",
                    title=doc.get("title"),
                    category="book",
                    snippet=", ".join(doc.get("subject", [])[:8]) if doc.get("subject") else "",
                    thumbnail=f"https://covers.openlibrary.org/b/id/{cover}-M.jpg" if cover else None,
                    date=str(doc.get("first_publish_year")) if doc.get("first_publish_year") else None,
                    author=", ".join(doc.get("author_name", [])[:3]) or None,
                    meta={"editions": doc.get("edition_count"), "ia": (doc.get("ia") or [])[:3]},
                )
            )
        return results

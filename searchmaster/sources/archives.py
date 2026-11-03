"""Internet Archive (files, software, texts, media), arXiv papers, Open Library books."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from ..models import Result
from ..textutil import strip_html, truncate
from .base import Source

FILE_LIKE_RE = re.compile(r"\.(png|jpe?g|gif|bmp|ico|css|js|swf|zip|rar|7z|exe|pdf|mp3|mp4|wmv|avi|xml|txt)$", re.I)

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
    phrase_ok = True
    categories = ('file', 'image', 'video', 'audio', 'article')
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

    CDX = "https://web.archive.org/cdx/search/cdx"
    PAGE_HINTS = re.compile(r"news|notice|update|patch|event|guide|board|gallery|screen|media|download|about|class|"
                            r"world|story|faq|wallpaper|artwork|novost|obnovl", re.I)
    NEWS_HINTS = re.compile(r"news|notice|update|patch|event|novost|obnovl", re.I)

    def search(self, query: str, limit: int) -> list[Result]:
        return []  # used through `snapshots_for` / `archive_search` by the pipeline

    def _cdx(self, domain: str, mime: str, limit: int, collapse: str = "digest") -> list[list[str]]:
        # matchType=domain: the site and all its subdomains (pictures often lived on img./static. hosts)
        params = [("url", domain), ("matchType", "domain"), ("output", "json"), ("fl", "timestamp,original,mimetype,length"),
                  ("filter", "statuscode:200"), ("filter", f"mimetype:{mime}"), ("collapse", collapse), ("limit", str(limit))]
        # only the years the site belonged to the subject: later the domain may serve something else
        if self.config.archive_from:
            params.append(("from", str(self.config.archive_from)))
        if self.config.archive_to:
            params.append(("to", str(self.config.archive_to)))
        resp = self.http.get(self.CDX, params=params, timeout=90)
        if resp.status_code != 200:
            raise RuntimeError(f"Wayback CDX HTTP {resp.status_code}")
        rows = resp.json() if resp.text.strip() else []
        return rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows

    @staticmethod
    def _date(ts: str) -> str | None:
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if len(ts) >= 8 else None

    def archive_search(self, domain: str, trusted: bool, images: int = 150, pages: int = 60, files: int = 40) -> list[Result]:
        """Everything the Wayback Machine kept from one site: pages, pictures, documents and media."""
        out: list[Result] = []
        label = f"архив {domain}"
        try:
            for ts, original, mime, length in self._cdx(domain, "image/(jpeg|png|gif)", images * 3):
                if not length.isdigit() or int(length) < 10000:  # skip buttons, icons and spacers
                    continue
                raw = f"https://web.archive.org/web/{ts}id_/{original}"
                name = original.rstrip("/").rsplit("/", 1)[-1][:80]
                out.append(self.result(url=f"https://web.archive.org/web/{ts}/{original}", title=f"{name} ({label})",
                                       category="image", thumbnail=raw, date=self._date(ts),
                                       meta={"file_url": raw, "trusted": trusted, "site": label, "archived": True}))
                if sum(1 for r in out if r.category == "image") >= images:
                    break
        except Exception as exc:
            self.ctx.log.debug("wayback images %s: %s", domain, exc)
        try:
            rows = self._cdx(domain, "text/html", 2000, collapse="urlkey")
            # an "HTML" answer at a picture/file address is an error page, not content
            rows = [r for r in rows if not FILE_LIKE_RE.search(r[1].split("?", 1)[0])]
            ranked = sorted(rows, key=lambda r: (not self.PAGE_HINTS.search(r[1]), len(r[1])))
            for ts, original, _mime, _len in ranked[:pages]:
                path = original.split("://", 1)[-1]
                out.append(self.result(url=f"https://web.archive.org/web/{ts}/{original}", title=f"{path[:110]}",
                                       category="news" if self.NEWS_HINTS.search(original) else "article",
                                       snippet=f"Архивная копия официальной страницы от {self._date(ts)}", date=self._date(ts),
                                       meta={"trusted": trusted, "site": label, "archived": True}))
        except Exception as exc:
            self.ctx.log.debug("wayback pages %s: %s", domain, exc)
        try:
            media = "(application/pdf|application/zip|application/x-zip-compressed|video/.*|audio/.*|application/x-shockwave-flash)"
            for ts, original, mime, _len in self._cdx(domain, media, files):
                raw = f"https://web.archive.org/web/{ts}id_/{original}"
                category = "video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file"
                out.append(self.result(url=f"https://web.archive.org/web/{ts}/{original}",
                                       title=f"{original.rstrip('/').rsplit('/', 1)[-1][:80]} ({label})",
                                       category=category, snippet=f"{mime}, сохранено {self._date(ts)}", date=self._date(ts),
                                       meta={"file_url": raw, "trusted": trusted, "site": label, "archived": True,
                                             # only documents are fetched automatically; clients, music and video stay links
                                             "no_download": mime != "application/pdf"}))
        except Exception as exc:
            self.ctx.log.debug("wayback files %s: %s", domain, exc)
        return out

    def snapshots_for(self, urls: list[str], trusted: bool = True) -> list[Result]:
        results = []
        when = str(self.config.archive_from or 2000)
        for url in urls[:30]:
            try:
                data = self.http.get_json("https://archive.org/wayback/available", params={"url": url, "timestamp": when})
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
                    meta={"trusted": trusted, "first_snapshot": snap.get("url"), "site": "Wayback Machine",
                          "domain": url.split("://", 1)[-1].split("/", 1)[0]},
                )
            )
        return results


class Arxiv(Source):
    name = "arxiv"
    title = "arXiv"
    description = "Научные статьи (полезно для технических тем)"
    phrase_ok = False
    categories = ('paper',)

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
    phrase_ok = True
    categories = ('book',)

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

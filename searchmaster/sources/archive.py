"""Internet Archive (items + file listings) and the Wayback Machine (old official pages)."""

from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import quote as urlquote

from ..models import ARCHIVE, FILE, IMAGE, VIDEO, Result
from ..utils import domain_of, file_category, file_extension, human_size, strip_html, truncate
from .base import Source

_SKIP_FILE_CATEGORIES = {"executable", "game-data", "archive", "other"}
_DISK_IMAGES = {"iso", "cso", "chd", "cue", "bin", "img", "mdf", "nrg", "nsp", "xci", "wbfs", "rvz", "gcm",
                "3ds", "cia", "nds", "gba", "sfc", "smc", "n64", "z64", "vpk", "pkg"}
_INTERESTING_PATH = re.compile(
    r"screen|media|press|gallery|image|img|download|wallpaper|art|dev|blog|news|faq|manual|forum|patch", re.I
)


class InternetArchiveSource(Source):
    name = "archive_org"
    description = "Internet Archive: журналы, мануалы, саундтреки, видео — и списки файлов в найденных коллекциях"
    facets = frozenset({"files", "history", "general", "video", "images"})
    items_with_files = 5

    def search(self, plan) -> Iterator[Result]:
        names = plan.search_names[:2] if not plan.snowball_only else []
        queries = [f'"{n}"' for n in names] + [f'"{plan.topic}" {t}' for t in plan.extra_terms[:2]]
        listed = 0
        for q in queries:
            # Software items on IA are very often uploaded copies of commercial games/apps:
            # leave them out and keep manuals, magazines, video, audio, images and docs.
            data = self.http.get_json("https://archive.org/advancedsearch.php", params={
                "q": f"({q}) AND NOT mediatype:software", "fl[]": ["identifier", "title", "description", "mediatype", "date", "downloads",
                                 "creator", "subject", "item_size"],
                "rows": min(self.limit, 100), "sort[]": "downloads desc", "output": "json",
            })
            for doc in data.get("response", {}).get("docs", []):
                ident = doc.get("identifier")
                if not ident or doc.get("mediatype") == "software":
                    continue
                subj = doc.get("subject") or []
                r = self.result(
                    f"https://archive.org/details/{ident}", _first(doc.get("title")) or ident, query=q, kind=ARCHIVE,
                    snippet=truncate(strip_html(_first(doc.get("description")) or ""), 500),
                    thumbnail=f"https://archive.org/services/img/{ident}",
                    date=(_first(doc.get("date")) or "")[:10] or None, author=_first(doc.get("creator")),
                    meta={"mediatype": doc.get("mediatype"), "downloads": doc.get("downloads", 0),
                          "tags": subj if isinstance(subj, list) else [subj], "identifier": ident,
                          "size": human_size(doc.get("item_size"))},
                )
                yield r
                if listed < self.items_with_files and _mentions(r, plan):
                    listed += 1
                    yield from self._files(ident, r.title, q)

    def _files(self, ident: str, item_title: str, q: str) -> Iterator[Result]:
        try:
            meta = self.http.get_json(f"https://archive.org/metadata/{ident}")
        except Exception as exc:
            self.log.debug("metadata failed for %s: %s", ident, exc)
            return
        count = 0
        for f in meta.get("files", []):
            if f.get("source") != "original" or count >= 40:
                continue
            name = f.get("name", "")
            if name.endswith(("_meta.xml", "_files.xml", "_meta.sqlite", ".torrent", "_reviews.xml")):
                continue
            url = f"https://archive.org/download/{ident}/{urlquote(name)}"
            cat = file_category(url) or "other"
            if cat in _SKIP_FILE_CATEGORIES or file_extension(url) in _DISK_IMAGES:
                continue  # program/game binaries and disk images are not what we index
            kind = IMAGE if cat == "image" else VIDEO if cat == "video" else FILE
            count += 1
            yield self.result(url, f"{name} — {item_title}", query=q, kind=kind,
                              thumbnail=url if kind == IMAGE else None,
                              meta={"file_category": cat, "format": f.get("format"),
                                    "size": human_size(int(f["size"])) if str(f.get("size", "")).isdigit() else None,
                                    "size_bytes": int(f["size"]) if str(f.get("size", "")).isdigit() else None,
                                    "page_url": f"https://archive.org/details/{ident}", "identifier": ident})


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _mentions(r: Result, plan) -> bool:
    text = f"{r.title} {r.snippet}".lower()
    return any(n.lower() in text for n in plan.names)


class WaybackSource(Source):
    """Runs only when the engine knows official sites (from Steam, repos, Wikipedia...)."""

    name = "wayback"
    description = "Wayback Machine: архивные снимки официальных сайтов (пресс-киты, скриншоты, старые страницы)"
    facets = frozenset({"history", "images", "files"})

    def search(self, plan) -> Iterator[Result]:
        for site in plan.official_sites[:3]:
            domain = domain_of(site) or site
            rows = self.http.get_json("https://web.archive.org/cdx/search/cdx", params={
                "url": f"{domain}/*", "output": "json", "fl": "timestamp,original,mimetype",
                "filter": "statuscode:200", "collapse": "urlkey", "limit": 1500,
            }, timeout=45)
            if not rows:
                continue
            picked = 0
            for ts, original, mime in rows[1:]:
                is_media = mime.startswith("image/") or mime == "application/pdf"
                if not (is_media or (mime == "text/html" and _INTERESTING_PATH.search(original))):
                    continue
                kind = IMAGE if mime.startswith("image/") else FILE if is_media else ARCHIVE
                snap = f"https://web.archive.org/web/{ts}/{original}"
                raw = f"https://web.archive.org/web/{ts}id_/{original}"
                yield self.result(raw if kind != ARCHIVE else snap, f"[{ts[:4]}] {original}", query=domain, kind=kind,
                                  date=f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", thumbnail=raw if kind == IMAGE else None,
                                  meta={"page_url": snap, "mime": mime, "original": original, "official_site": domain})
                picked += 1
                if picked >= self.limit * 3:
                    break

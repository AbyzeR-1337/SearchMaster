"""Wikipedia (several languages), Wikimedia Commons and Fandom game wikis."""

from __future__ import annotations

from urllib.parse import quote

from ..models import Result
from ..textutil import STOPWORDS, similarity, slugify, strip_html, tokenize
from .base import Source


def _mediawiki_search(src: Source, api: str, base: str, query: str, limit: int, category: str, label: str) -> list[Result]:
    data = src.http.get_json(
        api,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": min(limit, 50),
            "srprop": "snippet|timestamp|wordcount",
            "format": "json",
            "utf8": 1,
        },
    )
    results = []
    for item in data.get("query", {}).get("search", []):
        title = item["title"]
        results.append(
            src.result(
                url=f"{base}/wiki/{quote(title.replace(' ', '_'))}",
                title=title,
                category=category,
                snippet=strip_html(item.get("snippet")),
                date=(item.get("timestamp") or "")[:10] or None,
                meta={"words": item.get("wordcount"), "page": title, "site": label},
            )
        )
    return results


def _mediawiki_images(src: Source, api: str, query: str, limit: int, label: str) -> list[Result]:
    data = src.http.get_json(
        api,
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": min(limit, 50),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 400,
            "format": "json",
        },
    )
    pages = sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    results = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        if not info.get("url"):
            continue
        category = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else \
            "audio" if mime.startswith("audio/") else "file"
        ext = info.get("extmetadata") or {}
        license_name = (ext.get("LicenseShortName") or {}).get("value")
        artist = strip_html((ext.get("Artist") or {}).get("value"))
        title = page.get("title", "").removeprefix("File:").removeprefix("Файл:")
        results.append(
            src.result(
                url=info.get("descriptionurl") or info["url"],
                title=title,
                category=category,
                snippet=strip_html((ext.get("ImageDescription") or {}).get("value"))[:300],
                thumbnail=info.get("thumburl") or info["url"],
                author=artist or None,
                date=((ext.get("DateTimeOriginal") or {}).get("value") or "")[:10] or None,
                meta={
                    "file_url": info["url"],
                    "mime": mime,
                    "size": info.get("size"),
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "license": license_name,
                    "site": label,
                },
            )
        )
    return results


class Wikipedia(Source):
    name = "wikipedia"
    title = "Википедия"
    description = "Статьи Википедии на выбранных языках + краткая сводка по главной статье"
    weight = 1.3
    expandable = True

    def search(self, query: str, limit: int) -> list[Result]:
        results: list[Result] = []
        errors = []
        for lang in self.config.langs:
            base = f"https://{lang}.wikipedia.org"
            try:
                found = _mediawiki_search(self, f"{base}/w/api.php", base, query, limit, "wiki", f"Википедия ({lang})")
            except Exception as exc:  # one language failing must not kill the others
                errors.append(f"{lang}: {exc}")
                continue
            if found and similarity(found[0].meta.get("page", ""), query) >= 0.3:
                # Only treat the top hit as "the" article when its title really matches the query.
                self._add_summary(base, found[0])
            results.extend(found)
        if not results and errors:
            raise RuntimeError("; ".join(errors))
        return results

    def _add_summary(self, base: str, top: Result) -> None:
        page = top.meta.get("page")
        if not page:
            return
        try:
            data = self.http.get_json(f"{base}/api/rest_v1/page/summary/{quote(page.replace(' ', '_'), safe='')}")
        except Exception:
            return
        extract = data.get("extract")
        if extract:
            top.snippet = extract
            top.content = extract
            top.meta["summary"] = True
            top.meta["description"] = data.get("description")
        image = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
        if image:
            top.thumbnail = (data.get("thumbnail") or {}).get("source") or image
            top.meta["image"] = image
        top.meta["aliases"] = self._langlinks(base, page)

    def _langlinks(self, base: str, page: str) -> dict[str, str]:
        """Titles of the same article in other languages: the game/topic name in English, etc."""
        try:
            data = self.http.get_json(
                f"{base}/w/api.php",
                params={"action": "query", "prop": "langlinks", "titles": page, "lllimit": 500, "format": "json"},
            )
        except Exception:
            return {}
        wanted = {"en", "ru", *self.config.langs}
        aliases = {base.split("//")[1].split(".")[0]: page}
        for p in data.get("query", {}).get("pages", {}).values():
            for link in p.get("langlinks", []):
                if link.get("lang") in wanted and link.get("*"):
                    aliases[link["lang"]] = link["*"]
        return aliases


class Commons(Source):
    name = "commons"
    title = "Wikimedia Commons"
    description = "Свободные изображения, скриншоты, видео и аудио"
    weight = 1.1
    expandable = True

    def search(self, query: str, limit: int) -> list[Result]:
        return _mediawiki_images(self, "https://commons.wikimedia.org/w/api.php", query, limit, "Commons")


class Fandom(Source):
    name = "fandom"
    title = "Fandom-вики"
    description = "Фанатские вики по игре: статьи и картинки (адрес вики подбирается автоматически)"
    weight = 1.2

    def candidates(self, query: str) -> list[str]:
        words = [w for w in tokenize(query) if w not in STOPWORDS]
        slug = slugify(query)
        longest = slugify(max(words, key=len)) if words else ""
        names = [slug.replace("-", ""), slug, longest]
        seen, out = set(), []
        for name in names:
            if name and len(name) >= 3 and name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _wiki_matches(self, sub: str, query: str) -> bool:
        """Make sure the guessed wiki exists and is really about the query."""
        try:
            data = self.http.get_json(
                f"https://{sub}.fandom.com/api.php",
                params={"action": "query", "meta": "siteinfo", "siprop": "general", "format": "json"},
            )
        except Exception:
            return False
        sitename = data.get("query", {}).get("general", {}).get("sitename", "")
        terms = {w for w in tokenize(query) if w not in STOPWORDS}
        site_tokens = set(tokenize(sitename))
        squashed = "".join(tokenize(sitename))
        return bool(terms & site_tokens) or slugify(query).replace("-", "") in squashed

    def search(self, query: str, limit: int) -> list[Result]:
        langs = [lang for lang in self.config.langs if lang != "en"]
        for sub in self.candidates(query):
            if not self._wiki_matches(sub, query):
                continue
            results: list[Result] = []
            for lang in ["en", *langs]:
                path = "" if lang == "en" else f"/{lang}"
                base = f"https://{sub}.fandom.com{path}"
                api = f"{base}/api.php"
                try:
                    pages = _mediawiki_search(self, api, base, query, limit, "wiki", f"{sub}.fandom ({lang})")
                except Exception:
                    continue
                for item in pages:
                    item.meta["trusted"] = True  # it's the dedicated wiki
                results.extend(pages)
                try:
                    images = _mediawiki_images(self, api, query, max(5, limit // 2), f"{sub}.fandom")
                    for item in images:
                        item.meta["trusted"] = True
                    results.extend(images)
                except Exception:
                    pass
            if results:
                return results
        return []

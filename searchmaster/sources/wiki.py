"""Wikis: Wikipedia (many languages), Fandom, PCGamingWiki, Wikimedia Commons."""

from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import quote as urlquote

from ..models import IMAGE, WIKI, Result, SourceBlocked
from ..utils import slugify, strip_html
from .base import Source


class MediaWikiMixin:
    """Shared helpers for any MediaWiki installation."""

    def mw_search(self, api: str, q: str, limit: int, *, namespace: int = 0) -> list[dict]:
        data = self.http.get_json(api, params={
            "action": "query", "list": "search", "srsearch": q, "srlimit": min(limit, 50),
            "srnamespace": namespace, "format": "json", "srprop": "snippet|timestamp|size|wordcount",
        }, headers={"User-Agent": self.config.bot_user_agent})
        return data.get("query", {}).get("search", [])

    def mw_images(self, api: str, q: str, limit: int) -> Iterator[dict]:
        data = self.http.get_json(api, params={
            "action": "query", "generator": "search", "gsrsearch": q, "gsrnamespace": 6,
            "gsrlimit": min(limit, 50), "prop": "imageinfo", "iiprop": "url|size|mime|timestamp",
            "iiurlwidth": 400, "format": "json",
        }, headers={"User-Agent": self.config.bot_user_agent})
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("url"):
                yield {"title": page.get("title", ""), **info}


class WikipediaSource(MediaWikiMixin, Source):
    name = "wikipedia"
    description = "Википедия на выбранных языках: статьи, краткие выжимки, главные изображения"
    facets = frozenset({"general", "history", "technical"})

    def search(self, plan) -> Iterator[Result]:
        for lang in plan.languages:
            api = f"https://{lang}.wikipedia.org/w/api.php"
            for q in plan.api_queries("general", limit=2):
                hits = self.mw_search(api, q, min(self.limit, 10))
                for i, hit in enumerate(hits):
                    title = hit["title"]
                    url = f"https://{lang}.wikipedia.org/wiki/{urlquote(title.replace(' ', '_'))}"
                    r = self.result(url, title, query=q, kind=WIKI,
                                    snippet=strip_html(hit.get("snippet")),
                                    date=(hit.get("timestamp") or "")[:10] or None)
                    r.meta.update({"lang": lang, "words": hit.get("wordcount")})
                    if i < 3:  # enrich top hits with the REST summary (+ lead image)
                        self._enrich(r, lang, title)
                    yield r
                    if r.meta.get("image"):
                        img = r.meta["image"]
                        yield self.result(img, f"{title} — изображение из Википедии", query=q, kind=IMAGE,
                                          thumbnail=r.thumbnail, meta={"page_url": url, "lang": lang})

    def _enrich(self, r: Result, lang: str, title: str) -> None:
        try:
            s = self.http.get_json(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urlquote(title.replace(' ', '_'), safe='')}",
                headers={"User-Agent": self.config.bot_user_agent},
            )
        except Exception as exc:  # summary is a bonus, never fatal
            self.log.debug("summary failed for %s: %s", title, exc)
            return
        if s.get("extract"):
            r.snippet = s["extract"]
        if s.get("description"):
            r.meta["description"] = s["description"]
        if (s.get("thumbnail") or {}).get("source"):
            r.thumbnail = s["thumbnail"]["source"]
        if (s.get("originalimage") or {}).get("source"):
            r.meta["image"] = s["originalimage"]["source"]


class CommonsSource(MediaWikiMixin, Source):
    name = "commons"
    description = "Wikimedia Commons — свободные изображения, логотипы, фото"
    facets = frozenset({"images"})

    def search(self, plan) -> Iterator[Result]:
        api = "https://commons.wikimedia.org/w/api.php"
        for q in plan.api_queries("images", limit=2):
            for img in self.mw_images(api, q, self.limit):
                page = "https://commons.wikimedia.org/wiki/" + urlquote(img["title"].replace(" ", "_"))
                yield self.result(img["url"], img["title"].removeprefix("File:"), query=q, kind=IMAGE,
                                  thumbnail=img.get("thumburl"), date=(img.get("timestamp") or "")[:10] or None,
                                  meta={"page_url": page, "width": img.get("width"), "height": img.get("height"),
                                        "mime": img.get("mime"), "size": img.get("size")})


def fandom_candidates(names: list[str]) -> list[str]:
    """Guess Fandom sub-domains for a topic: "Half-Life 2" -> halflife2, half-life, halflife..."""
    out: list[str] = []
    for name in names:
        base = slugify(name)
        words = [w for w in re.split(r"[-\s]+", base) if w]
        no_digits = [w for w in words if not w.isdigit()]
        cands = [
            "".join(words), "-".join(words), "".join(no_digits), "-".join(no_digits),
            words[0] if words else "",
        ]
        for c in cands:
            c = re.sub(r"[^a-z0-9-]", "", c)
            if len(c) >= 3 and c not in out:
                out.append(c)
    return out[:6]


class FandomSource(MediaWikiMixin, Source):
    name = "fandom"
    description = "Фанатские вики Fandom: статьи, вырезанный контент, файлы и изображения"
    facets = frozenset({"general", "images", "history", "technical"})

    def search(self, plan) -> Iterator[Result]:
        wikis = list(dict.fromkeys(plan_fandoms(plan) + fandom_candidates(plan.search_names)))
        found_any = 0
        for slug in wikis:
            if found_any >= 2:  # two real wikis is plenty
                break
            api = f"https://{slug}.fandom.com/api.php"
            try:
                hits = self.mw_search(api, plan.topic, self.limit)
            except SourceBlocked:
                raise
            except Exception:
                continue  # wiki does not exist
            if not hits:
                continue
            found_any += 1
            for hit in hits:
                url = f"https://{slug}.fandom.com/wiki/{urlquote(hit['title'].replace(' ', '_'))}"
                yield self.result(url, f"{hit['title']} ({slug}.fandom)", query=plan.topic, kind=WIKI,
                                  snippet=strip_html(hit.get("snippet")),
                                  date=(hit.get("timestamp") or "")[:10] or None, meta={"wiki": slug})
            if plan.wants("images"):
                try:
                    for img in self.mw_images(api, plan.topic, self.limit):
                        page = f"https://{slug}.fandom.com/wiki/{urlquote(img['title'].replace(' ', '_'))}"
                        yield self.result(img["url"], img["title"].removeprefix("File:"), query=plan.topic,
                                          kind=IMAGE, thumbnail=img.get("thumburl"),
                                          meta={"page_url": page, "wiki": slug, "width": img.get("width"),
                                                "height": img.get("height")})
                except Exception as exc:
                    self.log.debug("fandom images failed: %s", exc)


def plan_fandoms(plan) -> list[str]:
    """Fandom wikis discovered in earlier rounds (stored on the plan by the engine)."""
    return [s for s in getattr(plan, "discovered_fandoms", []) if s]


class PCGamingWikiSource(MediaWikiMixin, Source):
    name = "pcgamingwiki"
    description = "PCGamingWiki: движок, расположение файлов и сохранений, конфиги, фиксы"
    facets = frozenset({"technical", "files", "general"})

    def search(self, plan) -> Iterator[Result]:
        api = "https://www.pcgamingwiki.com/w/api.php"
        for q in plan.api_queries("general", limit=2):
            for hit in self.mw_search(api, q, min(self.limit, 10)):
                url = "https://www.pcgamingwiki.com/wiki/" + urlquote(hit["title"].replace(" ", "_"))
                yield self.result(url, hit["title"] + " — PCGamingWiki", query=q, kind=WIKI,
                                  snippet=strip_html(hit.get("snippet")))

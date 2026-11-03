"""Images and video: YouTube, Bing Images, Openverse."""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from bs4 import BeautifulSoup

from ..models import IMAGE, VIDEO, Result, SourceBlocked
from ..net import looks_blocked
from ..utils import strip_html
from .base import Source


def _runs(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    if "simpleText" in obj:
        return obj["simpleText"]
    return "".join(r.get("text", "") for r in obj.get("runs", []))


def parse_youtube_html(html: str, q: str, source: str) -> list[Result]:
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S) or \
        re.search(r'window\["ytInitialData"\]\s*=\s*(\{.*?\});', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out: list[Result] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            v = o.get("videoRenderer")
            if isinstance(v, dict) and v.get("videoId"):
                vid = v["videoId"]
                desc = ""
                for d in v.get("detailedMetadataSnippets", []) or []:
                    desc = _runs(d.get("snippetText"))
                    break
                desc = desc or _runs(v.get("descriptionSnippet"))
                out.append(Result(
                    url=f"https://www.youtube.com/watch?v={vid}", title=_runs(v.get("title")), kind=VIDEO,
                    source=source, query=q, snippet=desc, thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    author=_runs(v.get("ownerText")),
                    meta={"views": _runs(v.get("viewCountText")), "duration": _runs(v.get("lengthText")),
                          "published": _runs(v.get("publishedTimeText")), "video_id": vid},
                ))
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(data)
    return out


class YouTubeSource(Source):
    name = "youtube"
    description = "YouTube: трейлеры, геймплей, доклады (с YOUTUBE_API_KEY — через официальный API)"
    facets = frozenset({"video", "general", "history", "technical"})

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("video", limit=4):
            if self.config.youtube_api_key:
                yield from self._api(q)
                continue
            resp = self.http.get("https://www.youtube.com/results",
                                 params={"search_query": q, "hl": "en"}, delay=1.5)
            results = parse_youtube_html(resp.text, q, self.name)
            if not results and looks_blocked(resp.text):
                raise SourceBlocked("YouTube consent/captcha page")
            yield from results[: self.limit]

    def _api(self, q: str) -> Iterator[Result]:
        data = self.http.get_json("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": q, "type": "video", "maxResults": min(self.limit, 50),
            "key": self.config.youtube_api_key,
        })
        for it in data.get("items", []):
            vid = it.get("id", {}).get("videoId")
            sn = it.get("snippet", {})
            if not vid:
                continue
            yield self.result(f"https://www.youtube.com/watch?v={vid}", sn.get("title", ""), query=q, kind=VIDEO,
                              snippet=sn.get("description", ""),
                              thumbnail=((sn.get("thumbnails") or {}).get("high") or {}).get("url"),
                              date=(sn.get("publishedAt") or "")[:10] or None, author=sn.get("channelTitle"),
                              meta={"video_id": vid})


def parse_bing_images(html: str, q: str, source: str) -> list[Result]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a.iusc"):
        try:
            m = json.loads(a.get("m") or "{}")
        except ValueError:
            continue
        murl = m.get("murl")
        if not murl:
            continue
        out.append(Result(url=murl, title=strip_html(m.get("t") or ""), kind=IMAGE, source=source, query=q,
                          snippet=strip_html(m.get("desc") or ""), thumbnail=m.get("turl"),
                          meta={"page_url": m.get("purl")}))
    return out


class BingImagesSource(Source):
    name = "bing_images"
    description = "Bing Images: скриншоты, арты, обои (с ссылкой на страницу-источник)"
    facets = frozenset({"images"})

    def search(self, plan) -> Iterator[Result]:
        queries = [q for f, q in plan.web_queries(operators=False) if f in ("images", "snowball")][:4]
        if not queries:
            queries = plan.api_queries("images", limit=3)
        for q in queries:
            resp = self.http.get("https://www.bing.com/images/search",
                                 params={"q": q, "first": 1, "count": 35}, delay=1.5)
            results = parse_bing_images(resp.text, q, self.name)
            if not results and looks_blocked(resp.text):
                raise SourceBlocked("Bing Images captcha")
            yield from results[: self.limit]


class OpenverseSource(Source):
    name = "openverse"
    description = "Openverse: изображения под свободными лицензиями (CC)"
    facets = frozenset({"images"})

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("images", limit=2):
            data = self.http.get_json("https://api.openverse.org/v1/images/",
                                      params={"q": q, "page_size": min(self.limit, 50)}, timeout=25)
            for it in data.get("results", []):
                yield self.result(it["url"], it.get("title") or "", query=q, kind=IMAGE,
                                  thumbnail=it.get("thumbnail"), author=it.get("creator"),
                                  meta={"page_url": it.get("foreign_landing_url"), "license": it.get("license"),
                                        "width": it.get("width"), "height": it.get("height")})

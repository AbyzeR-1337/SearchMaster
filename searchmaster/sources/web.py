"""General web search engines."""

from __future__ import annotations

import base64
from typing import Iterator
from urllib.parse import parse_qs, urlparse

from ..config import FACETS
from ..models import IMAGE, Result, SourceBlocked
from ..net import looks_blocked
from ..utils import make_soup, strip_html
from .base import Source

ALL_FACETS = frozenset(FACETS)


def decode_bing_url(href: str) -> str:
    """Bing wraps results in bing.com/ck/a?...&u=a1<base64url>; unwrap it."""
    if "bing.com/ck/a" not in href:
        return href
    u = parse_qs(urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        b = u[2:]
        b += "=" * (-len(b) % 4)
        try:
            return base64.urlsafe_b64decode(b).decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            return href
    return href


def decode_ddg_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        return parse_qs(urlparse(href).query).get("uddg", [href])[0]
    return href


class WebEngine(Source):
    facets = ALL_FACETS
    pages_per_query = 1

    def queries(self, plan) -> list[tuple[str, str]]:
        return plan.web_queries(self.config.web_queries_per_engine)

    def search(self, plan) -> Iterator[Result]:
        for facet, q in self.queries(plan):
            for page in range(self.pages_per_query):
                found = 0
                for r in self.search_one(q, page):
                    r.meta.setdefault("facet", facet)
                    found += 1
                    yield r
                if found == 0:
                    break

    def search_one(self, q: str, page: int) -> Iterator[Result]:  # pragma: no cover
        raise NotImplementedError


class BingSource(WebEngine):
    name = "bing"
    description = "Bing (HTML) — основной веб-поиск, поддерживает filetype:/site:"

    def search_one(self, q: str, page: int) -> Iterator[Result]:
        resp = self.http.get(
            "https://www.bing.com/search",
            params={"q": q, "first": 1 + page * 10, "count": 30, "form": "QBLH"},
            delay=1.5,
        )
        results = list(parse_bing(resp.text, q, self.name))
        if not results and looks_blocked(resp.text):
            raise SourceBlocked("Bing captcha")
        yield from results[: self.limit]


def parse_bing(html: str, q: str, source: str) -> Iterator[Result]:
    from .base import classify_url

    soup = make_soup(html)
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        url = decode_bing_url(a["href"])
        if not url.startswith("http"):
            continue
        snip_el = li.select_one(".b_caption p") or li.select_one("p") or li.select_one(".b_caption")
        snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
        yield Result(url=url, title=a.get_text(" ", strip=True), snippet=snippet,
                     kind=classify_url(url), source=source, query=q)


class DuckDuckGoSource(WebEngine):
    name = "duckduckgo"
    description = "DuckDuckGo (HTML) — второй независимый веб-поиск"

    def search_one(self, q: str, page: int) -> Iterator[Result]:
        resp = self.http.get(
            "https://html.duckduckgo.com/html/", method="POST",
            data={"q": q, "kl": "wt-wt"}, delay=2.0,
        )
        results = list(parse_ddg(resp.text, q, self.name))
        if not results and (resp.status == 202 or looks_blocked(resp.text) or "image-check" in resp.text):
            raise SourceBlocked("DuckDuckGo anti-bot check")
        yield from results[: self.limit]


def parse_ddg(html: str, q: str, source: str) -> Iterator[Result]:
    from .base import classify_url

    soup = make_soup(html)
    for div in soup.select(".result"):
        if "result--ad" in (div.get("class") or []):
            continue
        a = div.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        url = decode_ddg_url(a["href"])
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue
        sn = div.select_one(".result__snippet")
        yield Result(url=url, title=a.get_text(" ", strip=True),
                     snippet=sn.get_text(" ", strip=True) if sn else "",
                     kind=classify_url(url), source=source, query=q)


class BraveSource(WebEngine):
    name = "brave"
    description = "Brave Search API (нужен BRAVE_API_KEY, есть бесплатный тариф)"
    requires = ("brave_api_key",)

    def search_one(self, q: str, page: int) -> Iterator[Result]:
        data = self.http.get_json(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": q, "count": min(20, self.limit), "offset": page},
            headers={"X-Subscription-Token": self.config.brave_api_key or ""},
        )
        for item in data.get("web", {}).get("results", []):
            yield self.result(item["url"], item.get("title", ""), query=q,
                              snippet=strip_html(item.get("description")),
                              date=item.get("page_age"))
        for item in data.get("videos", {}).get("results", []):
            yield self.result(item["url"], item.get("title", ""), query=q, kind="video",
                              snippet=strip_html(item.get("description")),
                              thumbnail=(item.get("thumbnail") or {}).get("src"))


class GoogleCSESource(WebEngine):
    name = "google"
    description = "Google Programmable Search (нужны GOOGLE_API_KEY и GOOGLE_CSE_ID)"
    requires = ("google_api_key", "google_cse_id")

    def search_one(self, q: str, page: int) -> Iterator[Result]:
        data = self.http.get_json(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": self.config.google_api_key, "cx": self.config.google_cse_id,
                    "q": q, "num": 10, "start": 1 + page * 10},
        )
        for item in data.get("items", []):
            thumb = None
            for img in (item.get("pagemap", {}) or {}).get("cse_image", [])[:1]:
                thumb = img.get("src")
            yield self.result(item["link"], item.get("title", ""), query=q,
                              snippet=item.get("snippet", ""), thumbnail=thumb)


class SearxngSource(WebEngine):
    name = "searxng"
    description = "Ваш SearXNG-инстанс (SEARXNG_URL) — метапоиск по десяткам движков"
    requires = ("searxng_url",)

    def search_one(self, q: str, page: int) -> Iterator[Result]:
        base = (self.config.searxng_url or "").rstrip("/")
        for category in ("general", "images", "videos", "it"):
            data = self.http.get_json(
                f"{base}/search",
                params={"q": q, "format": "json", "pageno": page + 1, "categories": category},
                delay=0.5,
            )
            for item in data.get("results", [])[: self.limit]:
                url = item.get("url")
                if not url:
                    continue
                kind = IMAGE if item.get("img_src") and category == "images" else None
                r = self.result(item.get("img_src") if kind == IMAGE else url, item.get("title", ""),
                                query=q, kind=kind, snippet=strip_html(item.get("content")),
                                thumbnail=item.get("thumbnail_src") or item.get("thumbnail"),
                                date=item.get("publishedDate"))
                if kind == IMAGE:
                    r.meta["page_url"] = url
                r.meta["engines"] = item.get("engines", [])
                yield r

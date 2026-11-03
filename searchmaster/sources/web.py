"""General web search through official APIs: Brave Search, Google Programmable
Search and any SearXNG instance. At least one key/URL is needed."""

from __future__ import annotations

from ..classify import classify_url
from ..models import Result
from ..textutil import strip_html
from .base import Source


class WebSearch(Source):
    name = "web"
    title = "Веб-поиск"
    description = "Весь интернет: Brave Search API, Google Programmable Search или SearXNG"
    weight = 1.0
    expandable = True

    def available(self) -> tuple[bool, str]:
        if self.providers():
            return True, ""
        return False, "нужен BRAVE_API_KEY, или GOOGLE_API_KEY+GOOGLE_CSE_ID, или SEARXNG_URL"

    def providers(self) -> list[str]:
        out = []
        if self.config.key("BRAVE_API_KEY"):
            out.append("brave")
        if self.config.key("GOOGLE_API_KEY") and self.config.key("GOOGLE_CSE_ID"):
            out.append("google")
        if self.config.key("SEARXNG_URL"):
            out.append("searxng")
        return out

    def search(self, query: str, limit: int) -> list[Result]:
        results: list[Result] = []
        errors = []
        for provider in self.providers():
            try:
                results += getattr(self, f"_{provider}")(query, limit)
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
        if not results and errors:
            raise RuntimeError("; ".join(errors))
        return results

    def _make(self, url: str, title: str, snippet: str, provider: str, **extra) -> Result:
        return self.result(
            url=url,
            title=strip_html(title) or url,
            category=extra.pop("category", None) or classify_url(url),
            snippet=strip_html(snippet),
            meta={"provider": provider, **extra.pop("meta", {})},
            **extra,
        )

    # -- Brave Search API ---------------------------------------------------------
    def _brave(self, query: str, limit: int) -> list[Result]:
        headers = {"X-Subscription-Token": self.config.key("BRAVE_API_KEY"), "Accept": "application/json"}
        out = []
        offset = 0
        while len(out) < limit and offset < 5:
            data = self.http.get_json(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 20, "offset": offset},
                headers=headers,
            )
            items = (data.get("web") or {}).get("results", [])
            for r in items:
                out.append(self._make(r.get("url"), r.get("title"), r.get("description"), "brave",
                                      date=(r.get("page_age") or "")[:10] or None,
                                      thumbnail=(r.get("thumbnail") or {}).get("src")))
            for v in (data.get("videos") or {}).get("results", []):
                out.append(self._make(v.get("url"), v.get("title"), v.get("description"), "brave",
                                      category="video", thumbnail=(v.get("thumbnail") or {}).get("src")))
            if len(items) < 20:
                break
            offset += 1
        try:
            data = self.http.get_json(
                "https://api.search.brave.com/res/v1/images/search",
                params={"q": query, "count": min(limit, 100)},
                headers=headers,
            )
            for r in data.get("results", []):
                props = r.get("properties") or {}
                out.append(self._make(r.get("url"), r.get("title"), r.get("source", ""), "brave",
                                      category="image", thumbnail=(r.get("thumbnail") or {}).get("src"),
                                      meta={"file_url": props.get("url")}))
        except Exception as exc:
            self.ctx.log.debug("brave images failed: %s", exc)
        return out

    # -- Google Programmable Search (Custom Search JSON API) --------------------
    def _google(self, query: str, limit: int) -> list[Result]:
        base = {"key": self.config.key("GOOGLE_API_KEY"), "cx": self.config.key("GOOGLE_CSE_ID"), "q": query, "num": 10}
        out = []
        for start in range(1, min(limit, 30) + 1, 10):
            data = self.http.get_json("https://www.googleapis.com/customsearch/v1", params={**base, "start": start})
            items = data.get("items", [])
            for r in items:
                thumb = ((r.get("pagemap") or {}).get("cse_thumbnail") or [{}])[0].get("src")
                out.append(self._make(r.get("link"), r.get("title"), r.get("snippet"), "google", thumbnail=thumb))
            if len(items) < 10:
                break
        try:
            data = self.http.get_json("https://www.googleapis.com/customsearch/v1", params={**base, "searchType": "image"})
            for r in data.get("items", []):
                img = r.get("image") or {}
                out.append(self._make(img.get("contextLink") or r.get("link"), r.get("title"), r.get("displayLink", ""),
                                      "google", category="image", thumbnail=img.get("thumbnailLink"),
                                      meta={"file_url": r.get("link"), "width": img.get("width"), "height": img.get("height")}))
        except Exception as exc:
            self.ctx.log.debug("google images failed: %s", exc)
        return out

    # -- SearXNG (self-hosted / public instance with JSON enabled) ---------------
    def _searxng(self, query: str, limit: int) -> list[Result]:
        base = self.config.key("SEARXNG_URL").rstrip("/")
        out = []
        for cats in ("general", "images", "videos", "it", "files"):
            try:
                data = self.http.get_json(f"{base}/search", params={"q": query, "format": "json", "categories": cats})
            except Exception:
                if cats == "general":
                    raise
                continue
            category = {"images": "image", "videos": "video", "files": "file"}.get(cats)
            for r in data.get("results", [])[:limit]:
                thumb = r.get("thumbnail_src") or r.get("thumbnail") or (r.get("img_src") if category == "image" else None)
                out.append(self._make(r.get("url"), r.get("title"), r.get("content", ""), "searxng",
                                      category=category, thumbnail=thumb,
                                      date=(r.get("publishedDate") or "")[:10] or None,
                                      meta={"file_url": r.get("img_src")} if category == "image" else {}))
        return out

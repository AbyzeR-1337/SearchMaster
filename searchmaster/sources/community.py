"""Discussions: Reddit, Hacker News, Stack Exchange network."""

from __future__ import annotations

import datetime as dt
import html
from typing import Iterator

from ..models import DISCUSSION, IMAGE, VIDEO, Result
from ..utils import strip_html, truncate
from .base import Source


def _ts(epoch: float | int | None) -> str | None:
    if not epoch:
        return None
    return dt.datetime.fromtimestamp(float(epoch), tz=dt.timezone.utc).date().isoformat()


class RedditSource(Source):
    name = "reddit"
    description = "Reddit: посты, скриншоты из сабреддитов, обсуждения"
    facets = frozenset({"discussions", "images", "general", "history", "technical"})

    def search(self, plan) -> Iterator[Result]:
        headers = {"User-Agent": self.config.bot_user_agent}
        for q in plan.api_queries("discussions", limit=3):
            data = self.http.get_json(
                "https://www.reddit.com/search.json",
                params={"q": q, "limit": min(self.limit, 100), "sort": "relevance", "t": "all", "raw_json": 1},
                headers=headers, delay=2.0,
            )
            for child in data.get("data", {}).get("children", []):
                yield from reddit_results(child.get("data", {}), q, self.name)


def reddit_results(p: dict, q: str, source: str) -> Iterator[Result]:
    permalink = "https://www.reddit.com" + p.get("permalink", "")
    thumb = p.get("thumbnail") if str(p.get("thumbnail", "")).startswith("http") else None
    meta = {"subreddit": p.get("subreddit"), "upvotes": p.get("score", 0),
            "comments": p.get("num_comments", 0), "flair": p.get("link_flair_text")}
    yield Result(url=permalink, title=html.unescape(p.get("title", "")), kind=DISCUSSION, source=source, query=q,
                 snippet=truncate(p.get("selftext", ""), 500), thumbnail=thumb, date=_ts(p.get("created_utc")),
                 author=p.get("author"), meta=dict(meta))
    # Image posts are a goldmine for screenshots.
    target = p.get("url_overridden_by_dest") or p.get("url") or ""
    if p.get("post_hint") == "image" or target.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        yield Result(url=target, title=html.unescape(p.get("title", "")), kind=IMAGE, source=source, query=q,
                     thumbnail=thumb or target, date=_ts(p.get("created_utc")),
                     meta={**meta, "page_url": permalink})
    for item in (p.get("gallery_data") or {}).get("items", [])[:20]:
        media = (p.get("media_metadata") or {}).get(item.get("media_id"), {})
        src = (media.get("s") or {}).get("u")
        if src:
            yield Result(url=html.unescape(src), title=html.unescape(p.get("title", "")) + " (галерея)",
                         kind=IMAGE, source=source, query=q, date=_ts(p.get("created_utc")),
                         meta={**meta, "page_url": permalink})
    if p.get("is_video") and (p.get("media") or {}).get("reddit_video"):
        yield Result(url=permalink, title=html.unescape(p.get("title", "")) + " (видео)", kind=VIDEO,
                     source=source, query=q, thumbnail=thumb, date=_ts(p.get("created_utc")), meta=dict(meta))


class HackerNewsSource(Source):
    name = "hackernews"
    description = "Hacker News (Algolia API): техничные обсуждения, статьи разработчиков"
    facets = frozenset({"discussions", "technical", "history", "code"})

    def search(self, plan) -> Iterator[Result]:
        for q in plan.api_queries("general", limit=2):
            data = self.http.get_json("https://hn.algolia.com/api/v1/search",
                                      params={"query": q, "hitsPerPage": min(self.limit, 50), "tags": "story"})
            for h in data.get("hits", []):
                hn_url = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
                meta = {"points": h.get("points", 0), "comments": h.get("num_comments", 0), "hn_url": hn_url}
                yield self.result(hn_url, h.get("title") or "", query=q, kind=DISCUSSION,
                                  snippet=truncate(strip_html(h.get("story_text") or ""), 400),
                                  date=(h.get("created_at") or "")[:10] or None, author=h.get("author"), meta=meta)
                if h.get("url"):
                    yield self.result(h["url"], h.get("title") or "", query=q,
                                      date=(h.get("created_at") or "")[:10] or None, meta=dict(meta))


class StackExchangeSource(Source):
    name = "stackexchange"
    description = "Stack Exchange: gaming, gamedev, reverseengineering, stackoverflow"
    facets = frozenset({"discussions", "technical", "code"})
    SITES = ("gaming", "gamedev", "reverseengineering", "stackoverflow")

    def search(self, plan) -> Iterator[Result]:
        q = plan.topic if not plan.snowball_only else (plan.api_queries("general", 1) or [plan.topic])[0]
        for site in self.SITES:
            params = {"order": "desc", "sort": "relevance", "q": q, "site": site,
                      "pagesize": min(self.limit, 30)}
            if self.config.stackexchange_key:
                params["key"] = self.config.stackexchange_key
            data = self.http.get_json("https://api.stackexchange.com/2.3/search/advanced", params=params)
            for it in data.get("items", []):
                yield self.result(it["link"], html.unescape(it.get("title", "")), query=f"{site}: {q}",
                                  kind=DISCUSSION, date=_ts(it.get("creation_date")),
                                  author=(it.get("owner") or {}).get("display_name"),
                                  meta={"site": site, "score": it.get("score", 0), "answers": it.get("answer_count", 0),
                                        "answered": it.get("is_answered"), "tags": it.get("tags", [])})
            if data.get("backoff"):
                break

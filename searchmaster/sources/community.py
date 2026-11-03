"""Discussions: Hacker News, Stack Exchange network, Reddit (official API)."""

from __future__ import annotations

from ..models import Result
from ..textutil import strip_html, truncate, ts_to_iso
from .base import Source


class HackerNews(Source):
    name = "hackernews"
    title = "Hacker News"
    description = "Обсуждения и статьи из Hacker News (Algolia API)"
    expandable = True

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://hn.algolia.com/api/v1/search",
            params={"query": query, "hitsPerPage": min(limit, 50), "tags": "story"},
        )
        results = []
        for hit in data.get("hits", []):
            item_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            results.append(
                self.result(
                    url=item_url,
                    title=hit.get("title"),
                    category="discussion",
                    snippet=truncate(strip_html(hit.get("story_text")), 300) or (hit.get("url") or ""),
                    date=(hit.get("created_at") or "")[:10] or None,
                    author=hit.get("author"),
                    meta={"points": hit.get("points"), "comments": hit.get("num_comments"), "link": hit.get("url")},
                )
            )
        return results


class StackExchange(Source):
    name = "stackexchange"
    title = "Stack Exchange"
    description = "Вопросы и ответы: Stack Overflow, Arqade (игры), GameDev"
    expandable = True

    SITES = ("gaming", "gamedev", "stackoverflow")

    def search(self, query: str, limit: int) -> list[Result]:
        results: list[Result] = []
        errors = []
        for site in self.SITES:
            params = {
                "q": query,
                "site": site,
                "order": "desc",
                "sort": "relevance",
                "pagesize": min(limit, 30),
            }
            if self.config.key("STACKEXCHANGE_KEY"):
                params["key"] = self.config.key("STACKEXCHANGE_KEY")
            try:
                data = self.http.get_json("https://api.stackexchange.com/2.3/search/advanced", params=params)
            except Exception as exc:
                errors.append(f"{site}: {exc}")
                continue
            for q in data.get("items", []):
                results.append(
                    self.result(
                        url=q.get("link"),
                        title=strip_html(q.get("title")),
                        category="discussion",
                        snippet=", ".join(q.get("tags", [])),
                        date=ts_to_iso(q.get("creation_date")),
                        author=(q.get("owner") or {}).get("display_name"),
                        meta={"score": q.get("score"), "answers": q.get("answer_count"), "answered": q.get("is_answered"),
                              "site": site, "tags": q.get("tags", [])},
                    )
                )
        if not results and errors:
            raise RuntimeError("; ".join(errors))
        return results


class Reddit(Source):
    name = "reddit"
    title = "Reddit"
    description = "Посты Reddit через официальный API (нужны REDDIT_CLIENT_ID и REDDIT_CLIENT_SECRET)"
    requires = ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")
    expandable = True

    def _token(self) -> str:
        resp = self.http.request(
            "POST",
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},
            auth=(self.config.key("REDDIT_CLIENT_ID"), self.config.key("REDDIT_CLIENT_SECRET")),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Reddit auth HTTP {resp.status_code}")
        return resp.json()["access_token"]

    def search(self, query: str, limit: int) -> list[Result]:
        token = self._token()
        data = self.http.get_json(
            "https://oauth.reddit.com/search",
            params={"q": query, "limit": min(limit, 100), "sort": "relevance", "type": "link"},
            headers={"Authorization": f"Bearer {token}"},
        )
        results = []
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})
            if p.get("over_18"):
                continue
            thumb = p.get("thumbnail") if str(p.get("thumbnail", "")).startswith("http") else None
            is_image = str(p.get("post_hint")) == "image"
            results.append(
                self.result(
                    url=f"https://www.reddit.com{p.get('permalink')}",
                    title=p.get("title"),
                    category="image" if is_image else "discussion",
                    snippet=truncate(p.get("selftext") or p.get("url") or "", 300),
                    thumbnail=thumb,
                    date=ts_to_iso(p.get("created_utc")),
                    author=p.get("author"),
                    meta={"score": p.get("score"), "comments": p.get("num_comments"), "site": f"r/{p.get('subreddit')}",
                          "file_url": p.get("url") if is_image else None},
                )
            )
        return results

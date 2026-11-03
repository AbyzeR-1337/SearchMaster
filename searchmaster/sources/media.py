"""Media sources: Openverse (CC images + audio) and YouTube Data API."""

from __future__ import annotations

from ..models import Result
from ..textutil import strip_html
from .base import Source


class Openverse(Source):
    name = "openverse"
    title = "Openverse"
    description = "Открытые (Creative Commons) изображения и аудио из сотен сайтов"
    expandable = True

    def search(self, query: str, limit: int) -> list[Result]:
        results = []
        for kind, category in (("images", "image"), ("audio", "audio")):
            try:
                data = self.http.get_json(
                    f"https://api.openverse.org/v1/{kind}/",
                    params={"q": query, "page_size": min(limit, 20 if kind == "images" else 10)},
                )
            except Exception as exc:
                if kind == "images":
                    raise
                self.ctx.log.debug("openverse audio failed: %s", exc)
                continue
            for item in data.get("results", []):
                license_name = " ".join(x for x in (item.get("license"), item.get("license_version")) if x)
                results.append(
                    self.result(
                        url=item.get("foreign_landing_url") or item.get("url"),
                        title=item.get("title") or "без названия",
                        category=category,
                        thumbnail=item.get("thumbnail") if category == "image" else None,
                        author=item.get("creator"),
                        meta={
                            "file_url": item.get("url"),
                            "license": license_name.upper() or None,
                            "provider": item.get("provider"),
                            "width": item.get("width"),
                            "height": item.get("height"),
                        },
                    )
                )
        return results


class YouTube(Source):
    name = "youtube"
    title = "YouTube"
    description = "Видео: трейлеры, геймплей, разборы (нужен YOUTUBE_API_KEY)"
    requires = ("YOUTUBE_API_KEY",)
    expandable = True

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": min(limit, 50),
                "key": self.config.key("YOUTUBE_API_KEY"),
            },
        )
        results = []
        for item in data.get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            sn = item.get("snippet") or {}
            if not vid:
                continue
            results.append(
                self.result(
                    url=f"https://www.youtube.com/watch?v={vid}",
                    title=strip_html(sn.get("title")),
                    category="video",
                    snippet=strip_html(sn.get("description")),
                    thumbnail=((sn.get("thumbnails") or {}).get("medium") or {}).get("url"),
                    date=(sn.get("publishedAt") or "")[:10] or None,
                    author=sn.get("channelTitle"),
                )
            )
        return results

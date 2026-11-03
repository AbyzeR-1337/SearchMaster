"""Game-specific sources: Steam, RAWG, GameBanana, Modrinth, speedrun.com."""

from __future__ import annotations

from ..models import Result
from ..textutil import similarity, strip_html, truncate, ts_to_iso
from .base import Source


class Steam(Source):
    name = "steam"
    title = "Steam"
    description = "Карточка игры, официальные скриншоты, трейлеры и новости/патчноуты"
    weight = 1.4

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://store.steampowered.com/api/storesearch/",
            params={"term": query, "l": "english", "cc": "US"},
        )
        items = data.get("items", [])
        if not items:
            return []
        # Best title match first; only fully expand close matches.
        items.sort(key=lambda it: similarity(it.get("name", ""), query), reverse=True)
        results: list[Result] = []
        for rank, item in enumerate(items[:5]):
            sim = similarity(item.get("name", ""), query)
            if rank == 0 and sim >= 0.5:
                results += self._expand(item["id"], limit, sim)
            else:
                results.append(
                    self.result(
                        url=f"https://store.steampowered.com/app/{item['id']}/",
                        title=item.get("name"),
                        category="game",
                        thumbnail=item.get("tiny_image"),
                        meta={"appid": item["id"], "match": round(sim, 2)},
                    )
                )
        return results

    def _expand(self, appid: int, limit: int, sim: float) -> list[Result]:
        data = self.http.get_json(
            "https://store.steampowered.com/api/appdetails", params={"appids": appid, "l": "english"}
        )
        entry = data.get(str(appid), {})
        if not entry.get("success"):
            return []
        d = entry["data"]
        name = d.get("name", str(appid))
        card = self.result(
            url=f"https://store.steampowered.com/app/{appid}/",
            title=name,
            category="game",
            snippet=strip_html(d.get("short_description")),
            thumbnail=d.get("header_image"),
            date=self._release(d),
            meta={
                "appid": appid,
                "site": "Steam",
                "name": name,
                "game_card": True,
                "match": round(sim, 2),
                "trusted": True,
                "developers": d.get("developers", []),
                "publishers": d.get("publishers", []),
                "genres": [g.get("description") for g in d.get("genres", [])],
                "categories": [c.get("description") for c in d.get("categories", [])][:12],
                "release_date": (d.get("release_date") or {}).get("date"),
                "website": d.get("website"),
                "platforms": [k for k, v in (d.get("platforms") or {}).items() if v],
                "metacritic": (d.get("metacritic") or {}).get("score"),
                "price": (d.get("price_overview") or {}).get("final_formatted") or ("Free" if d.get("is_free") else None),
                "languages": strip_html(d.get("supported_languages"))[:300],
                "about": truncate(strip_html(d.get("about_the_game")), 3000),
            },
        )
        card.content = card.meta["about"]
        results = [card]
        for i, shot in enumerate(d.get("screenshots", [])[:limit], 1):
            results.append(
                self.result(
                    url=shot.get("path_full"),
                    title=f"{name} — скриншот #{i}",
                    category="image",
                    thumbnail=shot.get("path_thumbnail"),
                    meta={"file_url": shot.get("path_full"), "trusted": True, "kind": "screenshot"},
                )
            )
        for movie in d.get("movies", [])[:limit]:
            mp4 = movie.get("mp4") or {}
            webm = movie.get("webm") or {}
            direct = mp4.get("max") or mp4.get("480") or webm.get("max")
            # Newer trailers are HLS/DASH streams only: link the store page instead of a raw playlist.
            url = direct or f"https://store.steampowered.com/app/{appid}/?trailer={movie.get('id')}"
            results.append(
                self.result(
                    url=url,
                    title=f"{name} — {movie.get('name', 'трейлер')}",
                    category="video",
                    thumbnail=movie.get("thumbnail"),
                    meta={"trusted": True, "kind": "trailer", "site": "Steam",
                          "stream": movie.get("hls_h264") or movie.get("dash_h264")},
                )
            )
        results += self._news(appid, name, limit)
        return results

    @staticmethod
    def _release(d: dict) -> str | None:
        return (d.get("release_date") or {}).get("date")

    def _news(self, appid: int, name: str, limit: int) -> list[Result]:
        try:
            data = self.http.get_json(
                "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/",
                params={"appid": appid, "count": min(limit, 30), "maxlength": 400},
            )
        except Exception:
            return []
        return [
            self.result(
                url=n.get("url"),
                title=n.get("title"),
                category="news",
                snippet=strip_html(n.get("contents")),
                date=ts_to_iso(n.get("date")),
                author=n.get("author") or n.get("feedlabel"),
                meta={"trusted": True, "feed": n.get("feedlabel"), "site": f"новости {name}"},
            )
            for n in data.get("appnews", {}).get("newsitems", [])
            if n.get("url")
        ]


class Rawg(Source):
    name = "rawg"
    title = "RAWG"
    description = "База игр RAWG: карточки и скриншоты (нужен RAWG_API_KEY)"
    requires = ("RAWG_API_KEY",)
    weight = 1.2

    def search(self, query: str, limit: int) -> list[Result]:
        key = self.config.key("RAWG_API_KEY")
        data = self.http.get_json("https://api.rawg.io/api/games", params={"search": query, "key": key, "page_size": 5})
        results = []
        for game in data.get("results", []):
            sim = similarity(game.get("name", ""), query)
            results.append(
                self.result(
                    url=f"https://rawg.io/games/{game.get('slug')}",
                    title=game.get("name"),
                    category="game",
                    thumbnail=game.get("background_image"),
                    date=game.get("released"),
                    meta={
                        "rating": game.get("rating"),
                        "metacritic": game.get("metacritic"),
                        "genres": [g.get("name") for g in game.get("genres", [])],
                        "platforms": [p.get("platform", {}).get("name") for p in game.get("platforms") or []],
                        "match": round(sim, 2),
                    },
                )
            )
            for shot in (game.get("short_screenshots") or [])[1:] if sim >= 0.5 else []:
                results.append(
                    self.result(
                        url=shot.get("image"),
                        title=f"{game.get('name')} — скриншот (RAWG)",
                        category="image",
                        thumbnail=shot.get("image"),
                        meta={"file_url": shot.get("image"), "trusted": True, "kind": "screenshot"},
                    )
                )
        return results


class GameBanana(Source):
    name = "gamebanana"
    title = "GameBanana"
    description = "Моды, скины, карты и инструменты для игр"

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json(
            "https://gamebanana.com/apiv11/Util/Search/Results",
            params={
                "_sModelName": "Mod",
                "_sOrder": "best_match",
                "_sSearchString": query,
                "_nPage": 1,
                "_nPerpage": min(limit, 50),
            },
        )
        results = []
        for rec in data.get("_aRecords", []):
            images = (rec.get("_aPreviewMedia") or {}).get("_aImages") or []
            thumb = None
            if images:
                img = images[0]
                thumb = f"{img.get('_sBaseUrl')}/{img.get('_sFile220') or img.get('_sFile')}"
            game = (rec.get("_aGame") or {}).get("_sName")
            results.append(
                self.result(
                    url=rec.get("_sProfileUrl"),
                    title=f"{rec.get('_sName')}" + (f" ({game})" if game else ""),
                    category="mod",
                    snippet=f"Игра: {game}" if game else "",
                    thumbnail=thumb,
                    date=ts_to_iso(rec.get("_tsDateModified") or rec.get("_tsDateAdded")),
                    author=(rec.get("_aSubmitter") or {}).get("_sName"),
                    meta={"views": rec.get("_nViewCount"), "likes": rec.get("_nLikeCount"), "game": game},
                )
            )
        return results


class Modrinth(Source):
    name = "modrinth"
    title = "Modrinth"
    description = "Моды, ресурспаки и шейдеры (в основном Minecraft)"

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json("https://api.modrinth.com/v2/search", params={"query": query, "limit": min(limit, 100)})
        return [
            self.result(
                url=f"https://modrinth.com/{h.get('project_type', 'mod')}/{h.get('slug')}",
                title=h.get("title"),
                category="mod",
                snippet=h.get("description") or "",
                thumbnail=h.get("icon_url") or None,
                date=(h.get("date_modified") or "")[:10] or None,
                author=h.get("author"),
                meta={"downloads": h.get("downloads"), "type": h.get("project_type"), "site": "Modrinth (Minecraft)"},
            )
            for h in data.get("hits", [])
        ]


class Speedrun(Source):
    name = "speedrun"
    title = "speedrun.com"
    description = "Страницы игры на speedrun.com (рекорды, гайды, ресурсы)"

    def search(self, query: str, limit: int) -> list[Result]:
        data = self.http.get_json("https://www.speedrun.com/api/v1/games", params={"name": query, "max": min(limit, 20)})
        results = []
        for g in data.get("data", []):
            name = (g.get("names") or {}).get("international")
            cover = ((g.get("assets") or {}).get("cover-large") or {}).get("uri")
            results.append(
                self.result(
                    url=g.get("weblink"),
                    title=name,
                    category="game",
                    thumbnail=cover,
                    date=str(g.get("released")) if g.get("released") else None,
                    meta={"match": round(similarity(name or "", query), 2)},
                )
            )
        return results

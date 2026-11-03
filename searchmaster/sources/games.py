"""Game-specific databases: Steam store (screenshots, trailers, descriptions) and Steam news."""

from __future__ import annotations

import datetime as dt
from difflib import SequenceMatcher
from typing import Iterator

from ..models import GAME, IMAGE, NEWS, VIDEO, Result
from ..utils import search_form, strip_html, truncate
from .base import Source


def name_similarity(a: str, b: str) -> float:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        ratio = max(ratio, 0.75)
    return ratio


class SteamSource(Source):
    name = "steam"
    description = "Steam: карточка игры, официальные скриншоты 1920×1080, трейлеры, разработчик, патчноуты"
    facets = frozenset({"general", "images", "video", "news", "technical"})
    max_apps = 3

    def search(self, plan) -> Iterator[Result]:
        if plan.snowball_only:
            return
        seen: set[int] = set()
        candidates: list[tuple[float, dict]] = []
        terms: list[str] = []
        for name in plan.names[:3]:
            for t in (name, search_form(name)):
                if t not in terms:
                    terms.append(t)
        # Fallbacks for renamed/retitled games: progressively shorter prefixes ("STALKER Shadow", "STALKER").
        words = search_form(plan.topic).split()
        terms += [" ".join(words[:n]) for n in range(len(words) - 1, 0, -1) if len(" ".join(words[:n])) >= 4]
        for term in terms:
            if candidates and term not in plan.names:
                break  # good hits already; fallbacks are only for zero results
            data = self.http.get_json("https://store.steampowered.com/api/storesearch/",
                                      params={"term": term, "cc": "us", "l": "english"})
            for it in data.get("items", []):
                if it.get("id") in seen:
                    continue
                seen.add(it["id"])
                sim = max(name_similarity(search_form(n), search_form(it.get("name", ""))) for n in plan.names)
                candidates.append((sim, it))
        candidates.sort(key=lambda x: -x[0])
        for rank, (sim, it) in enumerate(candidates[: self.max_apps]):
            if sim < 0.5:
                continue
            yield from self._app(it["id"], plan, sim, primary=rank == 0)

    def _app(self, appid: int, plan, sim: float, primary: bool = True) -> Iterator[Result]:
        data = self.http.get_json("https://store.steampowered.com/api/appdetails",
                                  params={"appids": appid, "l": "english", "cc": "us"})
        d = (data.get(str(appid)) or {}).get("data")
        if not d:
            return
        store_url = f"https://store.steampowered.com/app/{appid}/"
        name = d.get("name", str(appid))
        meta = {
            "appid": appid, "developers": d.get("developers", []), "publishers": d.get("publishers", []),
            "release_date": (d.get("release_date") or {}).get("date"),
            "genres": [g.get("description") for g in d.get("genres", [])],
            "tags": [c.get("description") for c in d.get("categories", [])],
            "platforms": [k for k, v in (d.get("platforms") or {}).items() if v],
            "website": d.get("website"), "metacritic": (d.get("metacritic") or {}).get("score"),
            "recommendations": (d.get("recommendations") or {}).get("total"),
            "pc_requirements": strip_html((d.get("pc_requirements") or {}).get("minimum", "")) if isinstance(d.get("pc_requirements"), dict) else "",
            "name_similarity": round(sim, 2),
        }
        yield self.result(store_url, f"{name} — Steam", query=name, kind=GAME,
                          snippet=strip_html(d.get("short_description")),
                          content=strip_html(d.get("detailed_description") or d.get("about_the_game")),
                          thumbnail=d.get("header_image"), meta=meta, date=None)
        if plan.wants("images"):
            shots = d.get("screenshots", [])
            for i, s in enumerate(shots if primary else shots[:6]):
                yield self.result(s["path_full"], f"{name} — скриншот #{i + 1} (Steam)", query=name, kind=IMAGE,
                                  thumbnail=s.get("path_thumbnail"),
                                  meta={"page_url": store_url, "official": primary, "width": 1920, "height": 1080})
            if d.get("background_raw"):
                yield self.result(d["background_raw"], f"{name} — фон страницы (Steam)", query=name, kind=IMAGE,
                                  meta={"page_url": store_url})
        if plan.wants("video"):
            for mv in d.get("movies", []):
                src = (mv.get("mp4") or {}).get("max") or (mv.get("webm") or {}).get("max") or \
                      mv.get("hls_h264") or mv.get("dash_h264")
                if src:
                    yield self.result(src, f"{name} — {mv.get('name', 'трейлер')} (Steam)", query=name, kind=VIDEO,
                                      thumbnail=mv.get("thumbnail"), meta={"page_url": store_url, "official": True})
        if plan.wants("news") or plan.wants("history"):
            yield from self._news(appid, name)

    def _news(self, appid: int, name: str) -> Iterator[Result]:
        try:
            data = self.http.get_json("https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/",
                                      params={"appid": appid, "count": min(self.limit, 30), "maxlength": 600})
        except Exception as exc:
            self.log.debug("news failed: %s", exc)
            return
        for n in (data.get("appnews") or {}).get("newsitems", []):
            date = dt.datetime.fromtimestamp(n.get("date", 0), tz=dt.timezone.utc).date().isoformat()
            yield self.result(n.get("url", ""), f"{n.get('title', '')} ({name})", query=name, kind=NEWS,
                              snippet=truncate(strip_html(n.get("contents", "")), 500), date=date,
                              author=n.get("author"), meta={"feed": n.get("feedlabel")})

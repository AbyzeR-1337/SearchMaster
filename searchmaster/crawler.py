"""Deep analysis: download the best pages, pull out text, images, files, code; inspect repos."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from .config import Config
from .extract import extract_page
from .models import DISCUSSION, FILE, GAME, IMAGE, NEWS, PAGE, PAPER, REPO, WIKI, Result, SourceBlocked
from .net import HttpClient
from .utils import file_category, file_extension, normalize_text

log = logging.getLogger("searchmaster.crawler")

CRAWLABLE = {PAGE, WIKI, DISCUSSION, NEWS, PAPER, GAME}

# Files whose presence in a repository reveals the engine / toolchain.
ENGINE_MARKERS = {
    ".uproject": "Unreal Engine", ".uasset": "Unreal Engine", "projectsettings/projectversion.txt": "Unity",
    ".unity": "Unity", ".meta": "Unity", "project.godot": "Godot", ".gd": "Godot", ".tscn": "Godot",
    ".yyp": "GameMaker", ".gml": "GameMaker", ".rpy": "Ren'Py", "game.rgss3a": "RPG Maker",
    ".vmf": "Source Engine", ".vpk": "Source Engine", ".qc": "Source/GoldSrc", ".bsp": "id Tech/Source",
    "conf.lua": "LÖVE", "cargo.toml": "Rust", "cmakelists.txt": "CMake", ".csproj": ".NET/C#",
    ".sln": "Visual Studio", "build.gradle": "Gradle/Java", "package.json": "Node.js",
    ".pk3": "id Tech 3", ".wad": "Doom engine", ".mcmeta": "Minecraft", "fabric.mod.json": "Minecraft Fabric",
    "mods.toml": "Minecraft Forge", ".esp": "Creation Engine", ".pex": "Creation Engine", "defold": "Defold",
}


class Crawler:
    def __init__(self, http: HttpClient, config: Config, topic_tokens: set[str],
                 progress: Callable[[str], None] | None = None):
        self.http = http
        self.config = config
        self.topic_tokens = topic_tokens
        self.progress = progress or (lambda msg: None)
        self.pages_done = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------- public
    def enrich(self, results: list[Result]) -> list[Result]:
        """Crawl the top results in place and return newly discovered results."""
        pages = [r for r in results if r.kind in CRAWLABLE and not r.crawled and not file_category(r.url)]
        pages.sort(key=lambda r: -r.score)
        targets = pages[: self.config.deep]
        repos = [r for r in results if r.kind == REPO and "github.com" in r.url][: max(3, self.config.deep // 5)]
        new: list[Result] = []
        if not targets and not repos:
            return new

        frontier = [(r, 0) for r in targets]
        visited: set[str] = {r.key for r, _ in frontier}
        max_pages = self.config.deep * (2 if self.config.crawl_depth else 1)
        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            repo_futs = [pool.submit(self._repo, r) for r in repos]
            while frontier and self.pages_done < max_pages:
                batch, frontier = frontier, []
                futs = {pool.submit(self._page, r): (r, d) for r, d in batch}
                for fut in as_completed(futs):
                    r, depth = futs[fut]
                    try:
                        found, links = fut.result()
                    except Exception as exc:  # one bad page never kills the run
                        log.debug("crawl failed %s: %s", r.url, exc)
                        continue
                    new.extend(found)
                    if depth < self.config.crawl_depth:
                        for link in links:
                            child = Result(url=link["url"], title=link["text"] or link["url"], kind=PAGE,
                                           source="crawler", query=f"ссылка со страницы {r.url}")
                            if child.key not in visited:
                                visited.add(child.key)
                                frontier.append((child, depth + 1))
                                new.append(child)
            for fut in as_completed(repo_futs):
                try:
                    fut.result()
                except Exception as exc:
                    log.debug("repo inspect failed: %s", exc)
        return new

    # ------------------------------------------------------------ helpers
    def _relevant_link(self, link: dict) -> bool:
        text = normalize_text(f"{link['url']} {link['text']}")
        hits = sum(1 for t in self.topic_tokens if len(t) > 2 and t in text)
        return hits >= max(1, min(2, len(self.topic_tokens)))

    def _useful_image(self, img: dict, name: str) -> bool:
        """Keep screenshots/artwork, drop page chrome: needs a topical alt/filename, a big size,
        or a link to a full-size image."""
        if img.get("linked"):
            return True
        if (img.get("width") or 0) >= 300 or (img.get("height") or 0) >= 200:
            return True
        text = normalize_text(f"{img.get('alt', '')} {name}")
        return any(t in text for t in self.topic_tokens if len(t) > 2)

    def _page(self, r: Result) -> tuple[list[Result], list[dict]]:
        try:
            resp = self.http.get(r.url, check_robots=self.config.respect_robots, timeout=self.config.timeout)
        except (PermissionError, SourceBlocked) as exc:
            r.meta["crawl_error"] = str(exc)
            return [], []
        if "html" not in resp.content_type and "xml" not in resp.content_type:
            r.meta["content_type"] = resp.content_type
            return [], []
        with self._lock:
            self.pages_done += 1
        self.progress(f"анализ страницы: {r.url[:90]}")
        info = extract_page(resp.text, resp.url)
        r.crawled = True
        r.content = info.text
        if not r.snippet and info.description:
            r.snippet = info.description
        if info.title and (r.title == r.url or not r.title):
            r.title = info.title
        r.date = r.date or info.published
        r.thumbnail = r.thumbnail or info.og_image
        if info.code:
            r.meta["code_snippets"] = info.code[:6]
        if info.headings:
            r.meta["headings"] = info.headings[:15]
        r.meta["images_on_page"] = len(info.images)
        r.meta["files_on_page"] = len(info.files)
        if info.lang:
            r.meta["lang"] = info.lang

        found: list[Result] = []
        context = f"на странице: {r.title}"
        for img in info.images[:60]:
            name = img["url"].rsplit("/", 1)[-1].split("?")[0]
            if not self._useful_image(img, name):
                continue
            found.append(Result(
                url=img["url"], title=img["alt"] or name, kind=IMAGE, source="crawler", query=f"страница {r.domain}",
                snippet=context, thumbnail=img.get("thumb"),
                meta={"page_url": r.url, "width": img.get("width"), "height": img.get("height"), "alt": img["alt"]},
            ))
        for f in info.files[:60]:
            name = f["url"].rsplit("/", 1)[-1].split("?")[0]
            found.append(Result(
                url=f["url"], title=f["text"] or name, kind=FILE, source="crawler", query=f"страница {r.domain}",
                snippet=f"{context} · файл {name}",
                meta={"page_url": r.url, "file_category": f["category"], "ext": file_extension(f["url"])},
            ))
        links = [ln for ln in info.links if self._relevant_link(ln)][:20]
        return found, links

    def _repo(self, r: Result) -> None:
        """Read README and the file tree of a GitHub repository."""
        repo = r.meta.get("repo") or "/".join(r.url.rstrip("/").split("/")[3:5])
        if repo.startswith("github.com/"):
            repo = repo.removeprefix("github.com/")
        if repo.count("/") != 1:
            return
        self.progress(f"анализ репозитория: {repo}")
        for name in ("README.md", "readme.md", "README.rst", "README.txt", "README"):
            try:
                resp = self.http.get(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}", max_bytes=400_000)
                r.content = resp.text[:30000]
                r.crawled = True
                break
            except Exception:
                continue
        headers = {"Accept": "application/vnd.github+json"}
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        try:
            tree = self.http.get_json(f"https://api.github.com/repos/{repo}/git/trees/HEAD",
                                      params={"recursive": "1"}, headers=headers)
        except Exception as exc:
            log.debug("tree failed for %s: %s", repo, exc)
            return
        r.meta["tree"] = summarize_tree([e.get("path", "") for e in tree.get("tree", []) if e.get("type") == "blob"])


def summarize_tree(paths: list[str]) -> dict:
    exts = Counter(file_extension("x/" + p) or "(нет)" for p in paths)
    engines: Counter[str] = Counter()
    for p in paths:
        low = p.lower()
        for marker, engine in ENGINE_MARKERS.items():
            if low.endswith(marker):
                engines[engine] += 1
    notable = [p for p in paths if any(k in p.lower() for k in (
        "readme", "license", "docs/", "format", "spec", "tools/", "extract", "unpack", "decomp", "build", "mod",
    ))][:25]
    return {
        "files": len(paths), "top_extensions": exts.most_common(12),
        "engine_hints": [e for e, _ in engines.most_common(5)], "notable": notable,
    }


__all__ = ["Crawler", "summarize_tree"]

"""Page analysis: download found pages (respecting robots.txt), extract the main
text and the best matching passage, and discover new links to files, images,
repositories and videos on those pages."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .classify import (
    ARCHIVE_EXT, AUDIO_EXT, CODE_HOSTS, DOC_EXT, IMAGE_EXT, VIDEO_EXT, VIDEO_HOSTS, classify_url, url_ext,
)
from .config import Context
from .models import Result
from .textutil import query_terms, tokenize, truncate

MAX_PAGE_BYTES = 2_500_000
SKIP_FETCH_HOSTS = ("youtube.com", "youtu.be", "store.steampowered.com", "reddit.com", "twitter.com", "x.com",
                    "facebook.com", "instagram.com", "tiktok.com")
FETCH_CATEGORIES = {"article", "wiki", "news", "discussion", "mod", "game", "code"}
REPO_RE = re.compile(r"^https?://(?:www\.)?(github\.com|gitlab\.com|codeberg\.org)/([\w.-]+)/([\w.-]+)/?$")


class Robots:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            known = origin in self._cache
            parser = self._cache.get(origin)
        if not known:
            parser = robotparser.RobotFileParser()
            try:
                resp = self.ctx.http.get(f"{origin}/robots.txt", timeout=10)
                if resp.status_code in (401, 403):
                    parser.disallow_all = True
                elif resp.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(resp.text.splitlines())
            except Exception:
                parser = None  # network problem: be conservative, skip this host
            with self._lock:
                self._cache[origin] = parser
        if parser is None:
            return False
        agent = self.ctx.http.session.headers.get("User-Agent", "*")
        return parser.can_fetch(agent, url)


def _best_passage(text: str, terms: list[str], size: int = 420) -> str:
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    best, best_score = "", 0.0
    for i, sent in enumerate(sentences):
        window = " ".join(sentences[i : i + 3])
        toks = set(tokenize(window))
        score = sum(1 for t in terms if t in toks)
        if score > best_score:
            best, best_score = window, score
    return truncate(best, size) if best_score else ""


def _extract(html: bytes | str, base_url: str, encoding: str | None = None) -> tuple[str, str, str | None, BeautifulSoup]:
    soup = BeautifulSoup(html, "lxml", from_encoding=encoding) if isinstance(html, bytes) else BeautifulSoup(html, "lxml")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    og = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "twitter:image"})
    image = urljoin(base_url, og["content"]) if og and og.get("content") else None
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.find(id=re.compile("content|main", re.I)) or soup.body or soup
    text = " ".join(main.get_text(" ", strip=True).split())
    return title, text, image, soup


def _discover(soup: BeautifulSoup, page: Result, terms: list[str], max_links: int = 25,
              phrases: list[str] | None = None) -> list[Result]:
    found: list[Result] = []
    seen: set[str] = set()

    def add(**kw) -> None:
        if kw["url"] in seen or len(found) >= max_links:
            return
        seen.add(kw["url"])
        kw.setdefault("source", "crawl")
        kw.setdefault("meta", {})
        kw["meta"]["found_on"] = page.url
        found.append(Result(**kw))

    for a in soup.find_all("a", href=True):
        href = urljoin(page.url, a["href"]).split("#")[0]
        if not href.startswith(("http://", "https://")) or href == page.url:
            continue
        label = " ".join(a.get_text(" ", strip=True).split())[:150]
        ext = url_ext(href)
        if ext in DOC_EXT | ARCHIVE_EXT:
            add(url=href, title=label or href.rsplit("/", 1)[-1], category="file",
                snippet=f"Файл {ext} со страницы «{page.title[:80]}»", meta={"file_url": href})
        elif ext in AUDIO_EXT:
            add(url=href, title=label or href.rsplit("/", 1)[-1], category="audio", meta={"file_url": href})
        elif ext in VIDEO_EXT or any(h in href for h in VIDEO_HOSTS if "watch" in href or "youtu.be" in href):
            add(url=href, title=label or "Видео", category="video", snippet=f"Ссылка со страницы «{page.title[:80]}»")
        elif REPO_RE.match(href) and not any(x in href for x in ("/topics", "/features", "/about", "/login")):
            add(url=href, title=label or href, category="code", snippet=f"Репозиторий упомянут на «{page.title[:80]}»")
        elif phrases and label and ext in ("", ".html", ".htm", ".php", ".asp", ".aspx"):
            # depth 2: an ordinary link whose text names the subject is worth reading too
            text = " " + " ".join(tokenize(label)) + " "
            if any(f" {p} " in text for p in phrases):
                add(url=href, title=label, category=classify_url(href), snippet=f"Ссылка со страницы «{page.title[:80]}»",
                    meta={"depth": 2})

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        src = urljoin(page.url, src)
        if not src.startswith("http") or url_ext(src) not in IMAGE_EXT or url_ext(src) == ".svg":
            continue
        alt = " ".join((img.get("alt") or img.get("title") or "").split())
        try:
            width = int(str(img.get("width", "0")).rstrip("px") or 0)
        except ValueError:
            width = 0
        relevant = any(t in set(tokenize(alt)) for t in terms)
        if relevant or width >= 400:
            add(url=src, title=alt or f"Изображение со страницы {urlsplit(page.url).netloc}", category="image",
                thumbnail=src, meta={"file_url": src})
    return found


def enrich_pages(ctx: Context, results: list[Result], top_k: int, queries: list[str] | None = None,
                 follow: bool = False) -> list[Result]:
    """Fetch top pages, fill ``content`` and better snippets; return discovered results."""
    if top_k <= 0:
        return []
    terms = list(dict.fromkeys(t for q in (queries or [ctx.config.query]) for t in query_terms(q)))
    phrases = [" ".join(tokenize(q)) for q in (queries or [ctx.config.query]) if tokenize(q)] if follow else None
    robots = Robots(ctx)
    candidates = [
        r for r in results
        if r.category in FETCH_CATEGORIES
        and r.url.startswith("http")
        and not any(h in urlsplit(r.url).netloc for h in SKIP_FETCH_HOSTS)
        and not (r.category == "code" and any(h in r.url for h in CODE_HOSTS) and r.meta.get("kind") != "file")
        and len(r.content) < 500
    ][:top_k]
    discovered: list[Result] = []
    lock = threading.Lock()

    def work(r: Result) -> None:
        try:
            if not robots.allowed(r.url):
                r.meta["robots_blocked"] = True
                return
            resp = ctx.http.get(r.url, stream=True, timeout=15, headers={"Accept": "text/html,application/xhtml+xml"})
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code != 200 or "html" not in ctype:
                resp.close()
                return
            raw = resp.raw.read(MAX_PAGE_BYTES, decode_content=True)
            resp.close()
            # Let BeautifulSoup sniff <meta charset> unless the server declared one.
            encoding = resp.encoding if "charset=" in ctype.lower() else None
            title, text, image, soup = _extract(raw, r.url, encoding)
            r.content = text[:20000]
            r.thumbnail = r.thumbnail or image
            if title and (not r.title or r.title == r.url):
                r.title = title
            passage = _best_passage(text, terms)
            if passage and len(passage) > len(r.snippet) // 2:
                r.meta["passage"] = passage
                if len(r.snippet) < 80:
                    r.snippet = passage
            new = _discover(soup, r, terms, max_links=40 if follow else 25, phrases=phrases)
            with lock:
                discovered.extend(new)
        except Exception as exc:
            ctx.log.debug("fetch %s failed: %s", r.url, exc)

    with ThreadPoolExecutor(max_workers=ctx.config.workers) as pool:
        list(pool.map(work, candidates))
    for d in discovered:
        if d.category == "article":
            d.category = classify_url(d.url)
    return discovered

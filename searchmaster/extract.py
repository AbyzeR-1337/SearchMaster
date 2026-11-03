"""Turn a downloaded HTML page into structured data: text, images, file links, code."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Feeds/sitemaps sometimes come back as "pages"; lxml's HTML parser copes, so silence the nag.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .utils import absolutize, domain_of, file_category, strip_html

_ICON_HINT = re.compile(
    r"(logo|icon|sprite|avatar|badge|emoji|favicon|pixel|spacer|blank|button|banner|tracking|menu|hamburger|"
    r"arrow|close|search|share|social|header|footer|nav[_-]|[_-]bg|bg[_-]|background|loader|spinner|placeholder|"
    r"gradient|overlay|cursor|rating|flag|widget|ads?[_/-]|promo)", re.I)
_DATE_META = (
    ("meta", {"property": "article:published_time"}), ("meta", {"name": "date"}),
    ("meta", {"itemprop": "datePublished"}), ("meta", {"name": "pubdate"}),
    ("meta", {"property": "og:updated_time"}),
)


@dataclass
class PageInfo:
    url: str
    title: str = ""
    description: str = ""
    text: str = ""
    lang: str = ""
    published: str | None = None
    og_image: str | None = None
    images: list[dict] = field(default_factory=list)    # {"url", "alt", "width", "height"}
    files: list[dict] = field(default_factory=list)     # {"url", "text", "category"}
    code: list[str] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)     # {"url", "text"} same-site links
    headings: list[str] = field(default_factory=list)


def _int(v) -> int | None:
    try:
        return int(str(v).replace("px", "").strip())
    except (TypeError, ValueError):
        return None


def extract_page(html: str, url: str, *, max_text: int = 30000) -> PageInfo:
    soup = BeautifulSoup(html, "lxml")
    info = PageInfo(url=url)
    if soup.html and soup.html.get("lang"):
        info.lang = soup.html.get("lang", "")[:8]
    og_title = soup.find("meta", property="og:title")
    info.title = (og_title.get("content") if og_title else "") or (soup.title.get_text(strip=True) if soup.title else "")
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    info.description = strip_html(desc.get("content")) if desc else ""
    og_img = soup.find("meta", property="og:image")
    info.og_image = absolutize(url, og_img.get("content")) if og_img else None
    for tag, attrs in _DATE_META:
        el = soup.find(tag, attrs=attrs)
        if el and el.get("content"):
            info.published = el["content"][:10]
            break
    if not info.published:
        t = soup.find("time", attrs={"datetime": True})
        if t:
            info.published = t["datetime"][:10]

    # Code blocks first (before we strip <pre> while cleaning text).
    seen_code: set[str] = set()
    for el in soup.select("pre, code"):
        if el.name == "code" and el.find_parent("pre"):
            continue
        code = el.get_text("\n").strip()
        if len(code) >= 60 and code not in seen_code and ("\n" in code or len(code) > 120):
            seen_code.add(code)
            info.code.append(code[:3000])
        if len(info.code) >= 15:
            break

    # Images.
    seen_img: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("data-original") or img.get("src")
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[-1].strip().split(" ")[0]
        abs_src = absolutize(url, src)
        if not abs_src or abs_src in seen_img or abs_src.startswith("data:"):
            continue
        w, h = _int(img.get("width")), _int(img.get("height"))
        if (w and w < 120) or (h and h < 90):
            continue
        alt = (img.get("alt") or img.get("title") or "").strip()
        if _ICON_HINT.search(abs_src) and not alt:
            continue
        if abs_src.lower().split("?")[0].endswith(".svg"):
            continue
        seen_img.add(abs_src)
        # Prefer the full-size link if the thumbnail is wrapped in <a href=big.jpg>.
        parent = img.find_parent("a")
        full = absolutize(url, parent.get("href")) if parent and parent.get("href") else None
        linked = bool(full and file_category(full) == "image")
        if linked:
            abs_src, thumb = full, abs_src
        else:
            thumb = abs_src
        if _ICON_HINT.search(abs_src.rsplit("/", 1)[-1]) and not linked:
            continue
        info.images.append({"url": abs_src, "thumb": thumb, "alt": alt[:200], "width": w, "height": h,
                            "linked": linked})
        if len(info.images) >= 80:
            break

    # Links: direct files + same-site pages.
    host = domain_of(url)
    seen_links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = absolutize(url, a["href"])
        if not href or href in seen_links or not href.startswith("http"):
            continue
        seen_links.add(href)
        text = a.get_text(" ", strip=True)[:200]
        cat = file_category(href)
        if cat and cat != "image":
            info.files.append({"url": href, "text": text, "category": cat})
        elif cat == "image" and href not in seen_img:
            seen_img.add(href)
            info.images.append({"url": href, "thumb": href, "alt": text, "width": None, "height": None,
                                "linked": True})
        elif domain_of(href) == host and len(info.links) < 400:
            info.links.append({"url": href.split("#")[0], "text": text})

    info.headings = [h.get_text(" ", strip=True)[:200] for h in soup.select("h1, h2, h3")][:40]

    # Main text: drop boilerplate containers.
    for el in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe"]):
        el.decompose()
    main = soup.find("article") or soup.find("main") or soup.find(id=re.compile("content|main", re.I)) or soup.body or soup
    text = main.get_text("\n", strip=True) if main else ""
    lines = [ln for ln in (s.strip() for s in text.split("\n")) if len(ln) > 2]
    info.text = "\n".join(lines)[:max_text]
    return info

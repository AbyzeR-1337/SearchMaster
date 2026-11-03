"""Small helpers: URL normalisation, tokenisation, file-type detection."""

from __future__ import annotations

import hashlib
import html
import os
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

# --------------------------------------------------------------------------
# HTML parsing: lxml is fast but optional (hard to build on phones / Termux)
# --------------------------------------------------------------------------

def _pick_parser() -> str:
    forced = os.getenv("SEARCHMASTER_HTML_PARSER")
    if forced:
        return forced
    import importlib.util
    return "lxml" if importlib.util.find_spec("lxml") else "html.parser"


HTML_PARSER = _pick_parser()


def make_soup(markup: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(markup, HTML_PARSER)


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
_ACRONYM_RE = re.compile(r"(?<!\w)((?:\w\.){2,}\w?\.?)")
_NONWORD_RE = re.compile(r"[\W_]+", re.UNICODE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

STOPWORDS = frozenset(
    """
    a an and are as at be been but by can could did do does for from had has have
    he her his how i if in into is it its just me more most my no not of on or our
    out over so some such than that the their them then there these they this to too
    up us was we were what when where which while who why will with would you your
    about after all also any because before being between both down during each few
    further here him himself however itself more much must off once only other own
    same should through under until very via vs yet new one two get got use used using
    com www http https html php amp nbsp page pages click read see view more info
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
    только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если
    уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей
    может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз
    тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом
    один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец
    два об другой хоть после над больше тот через эти нас про всего них какая много разве
    три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более
    всегда конечно всю между это также года году лет
    """.split()
)


def strip_html(text: str | None) -> str:
    """Remove tags and entities, collapse whitespace."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def collapse_acronyms(text: str) -> str:
    """"S.T.A.L.K.E.R. 2" -> "STALKER 2" so dotted acronyms match plain spellings."""
    return _ACRONYM_RE.sub(lambda m: m.group(1).replace(".", ""), text)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return collapse_acronyms(text).lower().replace("ё", "е")


def search_form(name: str) -> str:
    """Spelling that search engines handle well: "S.T.A.L.K.E.R.: Shadow" -> "STALKER Shadow"."""
    name = collapse_acronyms(name.strip())
    name = re.sub(r"[:;|/\\]+", " ", name)
    return _WS_RE.sub(" ", name).strip()


def is_weak_name(name: str) -> bool:
    """Short acronyms like "HK", "HL2", "GTA" match too much on their own."""
    words = match_form(name).split()
    if len(words) != 1:
        return False
    raw = re.sub(r"[\W_]", "", collapse_acronyms(name))
    return len(words[0]) <= 3 or (raw.isupper() and len(raw) <= 4)


def match_form(text: str) -> str:
    """Normalised form for phrase matching: "Half-Life_2" and "half life 2" become equal."""
    return " " + _WS_RE.sub(" ", _NONWORD_RE.sub(" ", normalize_text(text))).strip() + " "


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    """Unicode-aware word tokenizer (works for Latin, Cyrillic, CJK runs)."""
    tokens = _WORD_RE.findall(normalize_text(text))
    tokens = [t for t in tokens if t]
    if keep_stopwords:
        return tokens
    return [
        t for t in tokens
        if (t not in STOPWORDS and len(t) > 1 and not t.isdigit()) or _is_meaningful_number(t)
    ]


def _is_meaningful_number(token: str) -> bool:
    # Keep years and short version-like numbers ("2", "64") only if they are part of
    # titles; we keep 4-digit years so they can feed the timeline.
    return token.isdigit() and len(token) == 4 and token[:2] in {"19", "20"}


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len].strip("-") or "query"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "yclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid",
    "si", "feature", "spm", "_ga", "share",
}


def canonical_url(url: str) -> str:
    """Normalise a URL so that trivially different links deduplicate."""
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url
    scheme = "https" if p.scheme in ("http", "https", "") else p.scheme
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    # Collapse YouTube variants to the video id.
    if host in {"youtube.com", "youtu.be", "music.youtube.com"}:
        vid = None
        if host == "youtu.be":
            vid = p.path.strip("/").split("/")[0]
        elif p.path == "/watch":
            vid = dict(parse_qsl(p.query)).get("v")
        elif p.path.startswith(("/shorts/", "/embed/")):
            vid = p.path.split("/")[2] if len(p.path.split("/")) > 2 else None
        if vid:
            return f"https://youtube.com/watch?v={vid}"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if k.lower() not in _TRACKING_PARAMS]
    query.sort()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    return urlunparse((scheme, host + port, path, "", urlencode(query), ""))


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def absolutize(base: str, link: str | None) -> str | None:
    if not link:
        return None
    link = link.strip()
    if link.startswith(("javascript:", "mailto:", "data:", "#", "tel:")):
        return None
    try:
        return urljoin(base, link)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# File types
# --------------------------------------------------------------------------

FILE_CATEGORIES: dict[str, set[str]] = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tga", "dds", "tif", "tiff", "avif", "ico", "svg"},
    "video": {"mp4", "webm", "mkv", "avi", "mov", "m4v", "flv", "bik", "bk2", "usm"},
    "audio": {"mp3", "ogg", "wav", "flac", "opus", "m4a", "wma", "mid", "midi", "xm", "mod", "it", "s3m"},
    "document": {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "epub", "djvu", "ppt", "pptx", "xls", "xlsx", "csv"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst"},
    "code": {
        "c", "h", "cpp", "cc", "cxx", "hpp", "hh", "cs", "java", "kt", "py", "js", "ts", "lua",
        "gd", "rs", "go", "rb", "php", "swift", "m", "mm", "asm", "s", "sh", "ps1", "glsl",
        "hlsl", "shader", "cg", "fx", "json", "xml", "yaml", "yml", "ini", "cfg", "toml", "sql",
    },
    "game-data": {
        "pak", "vpk", "wad", "bsa", "ba2", "esp", "esm", "unitypackage", "uasset", "umap",
        "pck", "big", "bsp", "mdl", "vmt", "vtf", "smd", "fbx", "obj", "blend", "gltf", "glb",
        "nif", "dat", "sav", "rpa", "rgss3a", "assets", "bundle", "upk", "mpq", "casc", "arc",
    },
    "executable": {"exe", "msi", "dll", "apk", "ipa", "dmg", "pkg", "deb", "rpm", "appimage", "jar", "so"},
}

EXT_TO_CATEGORY = {ext: cat for cat, exts in FILE_CATEGORIES.items() for ext in exts}


def file_extension(url: str) -> str:
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    # Fandom/MediaWiki CDN: /images/a/ab/Name.jpg/revision/latest?cb=...
    if "/revision/" in path:
        path = path.split("/revision/", 1)[0]
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    if name.lower().endswith((".tar.gz", ".tar.xz", ".tar.bz2")):
        return name.lower().rsplit(".", 1)[-1]
    return name.rsplit(".", 1)[-1].lower()[:12]


def file_category(url: str) -> str | None:
    """Return the category of a direct file link, or None for normal pages."""
    ext = file_extension(url)
    if ext in {"html", "htm", "php", "asp", "aspx", "jsp", "shtml", ""}:
        return None
    return EXT_TO_CATEGORY.get(ext)


_MAGIC = [
    (b"\xff\xd8\xff", "jpg"), (b"\x89PNG", "png"), (b"GIF8", "gif"), (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"), (b"Rar!", "rar"), (b"7z\xbc\xaf", "7z"), (b"\x1f\x8b", "gz"),
    (b"OggS", "ogg"), (b"ID3", "mp3"), (b"fLaC", "flac"), (b"\x1a\x45\xdf\xa3", "webm"), (b"BM", "bmp"),
]


def sniff_extension(head: bytes) -> str | None:
    """Guess a file extension from its first bytes."""
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[4:8] == b"ftyp":
        return "avif" if head[8:12] in (b"avif", b"avis") else "mp4"
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    return None


def human_size(num: float | int | None) -> str:
    if num is None:
        return ""
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"

"""Analysis: de-duplication, relevance scoring, related terms, query expansion, stats."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .classify import url_ext
from .models import CATEGORY_ORDER, Result
from .textutil import STOPWORDS, content_words, is_latin, query_terms, similarity, stem, tokenize, year_of

TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|yclid|mc_|ref$|ref_src|igshid|si$|feature$)")


def normalize_url(url: str) -> str:
    """Canonical form used for de-duplication."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    if host.endswith("wikipedia.org") and host.count(".") == 3:
        host = host.replace(".m.", ".")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)))
    path = parts.path.rstrip("/") or "/"
    if host in ("youtu.be",):
        return f"youtube.com/watch?v={path.strip('/')}"
    return urlunsplit(("", host, path, query, "")).lstrip("/")


def merge_results(results: list[Result]) -> list[Result]:
    """Merge duplicates (same normalized URL), keeping the richest data."""
    merged: dict[str, Result] = {}
    for r in results:
        if not r.url or not r.url.startswith(("http://", "https://")):
            continue
        key = normalize_url(r.url)
        cur = merged.get(key)
        if cur is None:
            merged[key] = r
            continue
        for src in r.sources:
            if src not in cur.sources:
                cur.sources.append(src)
        if len(r.snippet) > len(cur.snippet):
            cur.snippet = r.snippet
        if len(r.content) > len(cur.content):
            cur.content = r.content
        cur.thumbnail = cur.thumbnail or r.thumbnail
        cur.date = cur.date or r.date
        cur.author = cur.author or r.author
        cur.local_path = cur.local_path or r.local_path
        for k, v in r.meta.items():
            cur.meta.setdefault(k, v)
    return list(merged.values())


# --------------------------------------------------------------------------- scoring
def _popularity(meta: dict) -> float:
    best = 0.0
    for key in ("stars", "downloads", "points", "score", "views", "comments", "reactions", "likes", "editions"):
        value = meta.get(key)
        if isinstance(value, (int, float)) and value > 0:
            best = max(best, math.log10(value + 1))
    return min(best / 5.0, 1.0)  # 100k+ -> 1.0


def _recency(date_str: str | None) -> float:
    year = year_of(date_str)
    if not year:
        return 0.0
    age = max(0, date.today().year - year)
    return max(0.0, 1.0 - age / 15.0)


def required_coverage(n_terms: int) -> float:
    return 1.0 if n_terms <= 2 else 0.66 if n_terms == 3 else 0.6


def score_result(r: Result, terms: list[str], phrase: str, weights: dict[str, float], exact: bool = False) -> float:
    if not terms:
        return 0.5
    stems = [stem(t) for t in terms]
    title_tokens = set(tokenize(r.title))
    snippet_tokens = set(tokenize(r.snippet))
    content_tokens = set(tokenize(r.content[:20000])) if r.content else set()
    url_low = r.url.lower()
    squashed_url = url_low.replace("-", "").replace("_", "")

    def coverage(tokens: set[str]) -> float:
        hit = sum(1 for t, s in zip(terms, stems) if t in tokens or any(tok.startswith(s) for tok in tokens))
        return hit / len(terms)

    title_cov = coverage(title_tokens)
    text_cov = max(coverage(snippet_tokens), coverage(content_tokens))
    url_cov = sum(1 for s in stems if s in url_low) / len(terms)
    all_cov = coverage(title_tokens | snippet_tokens | content_tokens)
    hay = " " + " ".join(tokenize(f"{r.title} {r.snippet} {r.content[:8000]}")) + " "
    phrase_found = len(terms) > 1 and (f" {phrase} " in hay or phrase.replace(" ", "") in squashed_url)

    relevance = 0.5 * title_cov + 0.3 * text_cov + 0.1 * url_cov + (0.25 if phrase_found else 0.0)
    if exact:
        # the name must appear as a whole: no stems, no partial word matches
        on_topic = phrase_found if len(terms) > 1 else (f" {terms[0]} " in hay or terms[0] in squashed_url)
    else:
        on_topic = phrase_found or max(all_cov, url_cov) >= required_coverage(len(terms))
    if r.meta.get("trusted"):
        relevance, on_topic = max(relevance, 0.6), True
    if r.meta.get("summary") or r.meta.get("game_card"):
        relevance += 0.4
    authority = max(weights.get(s, 1.0) for s in r.sources)
    if r.meta.get("kind") == "file":  # single files from code search rank below whole repositories
        authority *= 0.85
    multi_source = min(len(r.sources) - 1, 3) * 0.08
    score = relevance * authority * (1 + 0.3 * _popularity(r.meta)) + 0.05 * _recency(r.date) + multi_source
    if not on_topic:
        score *= 0.05 if exact else 0.25  # exact search hides near-misses completely
    return round(score, 4)


def score_all(results: list[Result], queries: str | list[str], weights: dict[str, float], exact: bool = False) -> None:
    """Score against the query and its aliases (e.g. the English title); best match wins."""
    if isinstance(queries, str):
        queries = [queries]
    variants = [(query_terms(q), " ".join(tokenize(q))) for q in queries if q.strip()]
    variants = [(t, p) for t, p in variants if t]
    for r in results:
        r.score = max(score_result(r, terms, phrase, weights, exact) for terms, phrase in variants)
    results.sort(key=lambda r: r.score, reverse=True)


# --------------------------------------------------------------------------- terms
NOISE_TERMS = {
    "steam", "github", "gitlab", "codeberg", "fandom", "openverse", "archive", "internet", "reddit", "youtube",
    "modrinth", "gamebanana", "speedrun", "arxiv", "wikimedia", "commons", "readme", "license", "http", "https",
    "screenshot", "скриншот", "image", "jpg", "png", "file", "files", "download", "view", "views", "posted", "comments",
    "read", "more", "click", "here", "full", "article", "news", "новости", "подробнее", "читать",
    "game", "games", "игра", "игры", "игре", "игру", "года", "году", "год", "англ", "рус", "still", "free", "content",
    "version", "release", "released", "world", "official", "best", "first", "make", "many", "much", "well", "see",
    "time", "back", "page", "pages", "site", "online", "update", "updated", "назад", "обновлён", "сведения", "авторы",
}


def related_terms(results: list[Result], query: str | list[str], top: int = 30) -> list[tuple[str, int]]:
    """Most characteristic words/bigrams across the found material (document frequency)."""
    qtokens = tokenize(" ".join(query) if isinstance(query, list) else query)
    qset = set(qtokens) | {"".join(qtokens)} | NOISE_TERMS

    def usable(word: str) -> bool:
        if word in qset or "'" in word or "’" in word:  # knight's, there's
            return False
        parts = re.split(r"[-']", word)
        return not all(p in qset for p in parts if p)  # hollow-knight

    counter: Counter[str] = Counter()
    domains: dict[str, set[str]] = {}
    for r in results[:300]:
        # Titles, snippets and the best passage only: full page text is mostly site chrome.
        text = f"{r.title}. {r.snippet}. {r.meta.get('passage', '')}"
        words = [w for w in content_words(text) if usable(w)]
        doc_terms = set(words)
        doc_terms |= {f"{a} {b}" for a, b in zip(words, words[1:]) if a != b}
        counter.update(doc_terms)
        host = urlsplit(r.url).netloc
        for term in doc_terms:
            domains.setdefault(term, set()).add(host)
    # A term must appear in several documents from at least two different sites
    # (this removes boilerplate repeated by one site).
    min_df = 2 if len(results) > 10 else 1
    min_sites = 2 if len({urlsplit(r.url).netloc for r in results}) > 3 else 1
    items = [(t, c) for t, c in counter.items() if c >= min_df and len(domains[t]) >= min_sites]
    items.sort(key=lambda tc: (tc[1] * (2.0 if " " in tc[0] else 1.0), len(tc[0])), reverse=True)
    # A single word that mostly occurs inside a frequent phrase is shown as that phrase only
    # ("super smash" + "smash bros" instead of "super", "smash", "bros").
    phrases = [(t, c) for t, c in items if " " in t]
    picked: list[tuple[str, int]] = []
    for term, count in items:
        if " " not in term and any(term in p.split() and pc >= 0.5 * count for p, pc in phrases[:top * 2]):
            continue
        picked.append((term, count))
        if len(picked) >= top:
            break
    return picked


def find_aliases(query: str, results: list[Result], langs: list[str]) -> list[str]:
    """Other names of the subject: Wikipedia interlanguage titles of the main article."""
    aliases: list[str] = []
    for r in results:
        if not r.meta.get("summary"):
            continue
        for lang, title in (r.meta.get("aliases") or {}).items():
            if lang in ("en", *langs):
                aliases.append(title)
    seen = {query.lower()}
    out = []
    for a in aliases:
        if a.lower() not in seen and similarity(a, query) < 1.0:
            seen.add(a.lower())
            out.append(a)
    # Latin-script names first: most sources (Steam, GitHub, archives) are English.
    return sorted(out, key=lambda a: not is_latin(a))[:3]


FACETS = {
    "image": ["screenshots", "скриншоты", "concept art"],
    "code": ["source code", "github", "decompilation", "modding"],
    "file": ["download", "pdf", "manual", "soundtrack"],
    "wiki": ["wiki", "guide", "гайд"],
    "video": ["trailer", "gameplay"],
    "discussion": ["reddit", "forum", "форум"],
}


def expansion_queries(query: str, terms: list[tuple[str, int]], game_name: str | None, count: int) -> list[str]:
    """Queries for the second (deep) round: official name, related terms, facets."""
    if count <= 0:
        return []
    base = game_name or query
    qs: list[str] = []
    if game_name and game_name.lower() != query.lower():
        qs.append(game_name)
    for term, _ in terms:
        if len(qs) >= max(2, count // 2):
            break
        if term not in STOPWORDS and not term.isdigit():
            qs.append(f"{base} {term}")
    for facet_words in FACETS.values():
        qs.append(f"{base} {facet_words[0]}")
    seen, out = set(), []
    for q in qs:
        key = q.lower()
        if key not in seen and key != query.lower():
            seen.add(key)
            out.append(q)
    return out[:count]


# --------------------------------------------------------------------------- stats
def compute_stats(results: list[Result]) -> dict:
    by_cat = Counter(r.category for r in results)
    by_source: Counter[str] = Counter()
    for r in results:
        by_source.update(r.sources)
    domains = Counter(urlsplit(r.url).netloc.lower().removeprefix("www.") for r in results)
    years = Counter(y for y in (year_of(r.date) for r in results) if y)
    languages = Counter(r.meta.get("language") for r in results if r.category == "code" and r.meta.get("language"))
    exts = Counter(e for e in (url_ext(r.meta.get("file_url") or r.url) for r in results) if e)
    licenses = Counter(r.meta.get("license") for r in results if r.meta.get("license"))
    return {
        "total": len(results),
        "by_category": {c: by_cat[c] for c in CATEGORY_ORDER if by_cat[c]},
        "by_source": dict(by_source.most_common()),
        "top_domains": domains.most_common(15),
        "years": dict(sorted(years.items())),
        "code_languages": languages.most_common(10),
        "file_types": exts.most_common(12),
        "licenses": licenses.most_common(10),
        "downloaded": sum(1 for r in results if r.local_path),
    }


def timeline(results: list[Result], limit: int = 25) -> list[Result]:
    """Relevant dated items, oldest first (one per title)."""
    dated = [r for r in results if year_of(r.date) and r.category in ("game", "news", "wiki", "article", "code", "video", "paper")]
    dated = sorted(dated, key=lambda r: r.score, reverse=True)[: limit * 2]
    seen, out = set(), []
    for r in sorted(dated, key=lambda r: (year_of(r.date) or 0, r.date or "")):
        key = r.title.lower()[:60]
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out[:limit]

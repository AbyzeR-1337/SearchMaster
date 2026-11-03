"""Analysis: deduplication, relevance scoring, filtering, and topic intelligence
(keywords, entities, engines, platforms, file types, timeline, related queries)."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

from .models import ALL_KINDS, CODE, FILE, IMAGE, REPO, VIDEO, Result
from .utils import collapse_acronyms, domain_of, file_category, file_extension, is_weak_name, match_form, tokenize

# How much we trust / value each source (multiplies the relevance score).
SOURCE_WEIGHT = {
    "steam": 1.35, "wikipedia": 1.3, "pcgamingwiki": 1.3, "fandom": 1.2, "github": 1.15,
    "sourcegraph": 1.05, "gitlab": 1.05, "codeberg": 1.0, "youtube": 1.0, "archive_org": 1.05,
    "wayback": 1.0, "reddit": 1.0, "hackernews": 1.0, "stackexchange": 1.05, "bing": 1.0,
    "duckduckgo": 1.0, "brave": 1.05, "google": 1.1, "searxng": 1.0, "bing_images": 0.9,
    "commons": 1.0, "openverse": 0.95, "crawler": 0.85, "packages": 0.9, "openalex": 1.0,
}

ENGINE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (label, re.compile(rx, flags)) for label, rx, flags in [
        ("Unreal Engine", r"\bUnreal(?: Engine)?(?: ?[1-5])?\b|\bUE[345]\b", 0),
        ("Unity", r"\bUnity(?:3D| engine| Technologies)?\b", 0),
        ("Godot", r"\bGodot\b", re.I),
        ("Source", r"\bSource (?:engine|SDK|2)\b|\bSource Filmmaker\b", re.I),
        ("GoldSrc", r"\bGoldSrc\b|\bGoldSource\b", re.I),
        ("id Tech", r"\bid Tech ?\d?\b|\bQuake engine\b|\bDoom engine\b", re.I),
        ("CryEngine", r"\bCryEngine\b|\bCRYENGINE\b", re.I),
        ("Frostbite", r"\bFrostbite(?: engine)?\b", 0),
        ("RenderWare", r"\bRenderWare\b", re.I),
        ("Gamebryo", r"\bGamebryo\b", re.I),
        ("Creation Engine", r"\bCreation Engine\b", re.I),
        ("REDengine", r"\bREDengine\b", re.I),
        ("RE Engine", r"\bRE Engine\b", 0),
        ("Decima", r"\bDecima(?: engine)?\b", 0),
        ("Fox Engine", r"\bFox Engine\b", re.I),
        ("GameMaker", r"\bGame ?Maker(?: Studio)?\b", re.I),
        ("RPG Maker", r"\bRPG Maker\b", re.I),
        ("Ren'Py", r"\bRen'?Py\b", re.I),
        ("X-Ray", r"\bX-Ray engine\b", re.I),
        ("4A Engine", r"\b4A Engine\b", re.I),
        ("Build engine", r"\bBuild engine\b", re.I),
        ("Anvil", r"\bAnvil(?:Next)?(?: engine)?\b", 0),
        ("Snowdrop", r"\bSnowdrop(?: engine)?\b", 0),
        ("Dunia", r"\bDunia(?: engine)?\b", 0),
        ("IW engine", r"\bIW engine\b", re.I),
        ("Clausewitz", r"\bClausewitz(?: engine)?\b", 0),
        ("Luminous", r"\bLuminous (?:Studio|Engine)\b", re.I),
        ("Glacier", r"\bGlacier (?:engine|2)\b", re.I),
        ("Havok", r"\bHavok\b", 0),
        ("MonoGame/XNA", r"\bMonoGame\b|\bXNA\b", 0),
        ("Cocos2d", r"\bCocos2d(?:-x)?\b", re.I),
        ("LÖVE", r"\bL[ÖO]VE2?D?\b(?= framework| engine|2d)|\blove2d\b", re.I),
        ("Bevy", r"\bBevy(?: engine)?\b", 0),
        ("Defold", r"\bDefold\b", 0),
        ("Construct", r"\bConstruct [23]\b", 0),
        ("Phaser", r"\bPhaser(?:\.js| 3)?\b", 0),
        ("Torque", r"\bTorque (?:3D|Game Engine)\b", re.I),
        ("Lumberyard/O3DE", r"\bLumberyard\b|\bO3DE\b", re.I),
        ("Dagor", r"\bDagor(?: Engine)?\b", 0),
        ("Unigine", r"\bUnigine\b", re.I),
    ]
]

PLATFORM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (label, re.compile(rx, re.I)) for label, rx in [
        ("Windows/PC", r"\bWindows\b|\bPC\b"), ("Linux", r"\bLinux\b|\bSteamOS\b"), ("macOS", r"\bmacOS\b|\bMac OS\b|\bOS X\b"),
        ("PlayStation 5", r"\bPS5\b|\bPlayStation 5\b"), ("PlayStation 4", r"\bPS4\b|\bPlayStation 4\b"),
        ("PlayStation 3", r"\bPS3\b|\bPlayStation 3\b"), ("PlayStation 2", r"\bPS2\b|\bPlayStation 2\b"),
        ("PlayStation 1", r"\bPS1\b|\bPSX\b|\bPlayStation\b(?! ?[2-5]| Portable| Vita)"),
        ("PSP/Vita", r"\bPSP\b|\bPS Vita\b|\bPlayStation (?:Portable|Vita)\b"),
        ("Xbox Series", r"\bXbox Series\b"), ("Xbox One", r"\bXbox One\b"), ("Xbox 360", r"\bXbox 360\b"),
        ("Nintendo Switch", r"\bNintendo Switch\b|\bSwitch 2\b"), ("Wii/Wii U", r"\bWii(?: U)?\b"),
        ("GameCube", r"\bGameCube\b"), ("N64", r"\bN64\b|\bNintendo 64\b"), ("SNES/NES", r"\bS?NES\b|\bFamicom\b"),
        ("Game Boy", r"\bGame Boy(?: Advance| Color)?\b|\bGBA\b"), ("DS/3DS", r"\bNintendo 3?DS\b|\b3DS\b"),
        ("Android", r"\bAndroid\b"), ("iOS", r"\biOS\b|\biPhone\b|\biPad\b"), ("Steam Deck", r"\bSteam Deck\b"),
        ("DOS", r"\bMS-DOS\b|\bDOS\b"), ("Amiga", r"\bAmiga\b"), ("Sega", r"\bMega Drive\b|\bGenesis\b|\bDreamcast\b|\bSaturn\b"),
        ("Browser/Web", r"\bHTML5\b|\bWebGL\b|\bbrowser game\b"),
    ]
]

# Words our own generated titles/snippets add ("скриншот #3 (Steam)", "на странице: …") and
# URL/file noise: never treat them as topic keywords.
SYSTEM_WORDS = frozenset("""
steam скриншот скриншоты странице страница страницы изображение изображения википедии фон галерея
видео трейлер файл ссылка ссылки fandom wikia nocookie jpg jpeg png gif webp svg avif bmp pdf file files
image images wiki wikipedia revision latest static scale thumb thumbnail http https www com org net html
php фото cdn akamai akamaihd steamstatic shared store assets apps app sha1 md5 crc32 zip rar 7z mkv mp4
avi webm iso torrent
""".split())
# Typical video-title filler: "FULL GAME", "No Commentary", "Part 1", "PC Longplay"...
VIDEO_NOISE = frozenset("""
full game no commentary part longplay walkthrough gameplay playthrough pc hd 4k 60fps 1080p let's play
official trailer review ultra max settings rtx on off vs ending all endings
""".split())
GAME_WORDS = frozenset({"game", "games", "игра", "игры", "игру", "игре", "video", "play", "player"})
SOCIAL_DOMAINS = (
    "discord.gg", "discord.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "t.me", "vk.com", "reddit.com", "steamcommunity.com", "steampowered.com", "tiktok.com", "twitch.tv",
    "patreon.com", "linktr.ee", "bsky.app", "youtu.be", "github.com", "gitlab.com",
)

_ENTITY_RE = re.compile(
    r"\b([A-ZА-ЯЁ][\w'’\-]+(?:[ \t]+(?:of|the|de|du|von|der)?[ \t]*[A-ZА-ЯЁ0-9][\w'’\-]*){1,3})\b"
)
_YEAR_RE = re.compile(r"\b(19[7-9]\d|20[0-4]\d)\b")
_COMMON_CAPS = {
    "The", "This", "That", "These", "There", "When", "What", "How", "Why", "Who", "Where", "Read More",
    "Sign In", "Log In", "Privacy Policy", "Terms Of Service", "All Rights Reserved", "Steam", "Reddit",
    "YouTube", "Google", "Wikipedia", "GitHub", "Home", "Share", "Comments", "Reply", "Subscribe",
}


# ----------------------------------------------------------------------------
# Deduplication
# ----------------------------------------------------------------------------

def dedupe(results: Iterable[Result]) -> list[Result]:
    merged: dict[str, Result] = {}
    for r in results:
        if not r.url or not r.url.startswith(("http://", "https://")):
            continue
        k = r.key
        if k in merged:
            merged[k].merge(r)
        else:
            merged[k] = r
    return list(merged.values())


def simhash(text: str, bits: int = 64) -> int:
    v = [0] * bits
    toks = tokenize(text)
    for shingle in (" ".join(toks[i:i + 3]) for i in range(max(1, len(toks) - 2))):
        h = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for i in range(bits):
            v[i] += 1 if (h >> i) & 1 else -1
    return sum(1 << i for i in range(bits) if v[i] > 0)


def drop_near_duplicates(results: list[Result], max_distance: int = 3) -> list[Result]:
    """Collapse mirrors / syndicated copies (same text on different URLs)."""
    keep: list[Result] = []
    hashes: list[tuple[int, Result]] = []
    for r in sorted(results, key=lambda x: -x.score):
        if not r.crawled or len(r.content) < 800:
            keep.append(r)
            continue
        h = simhash(r.content)
        dup = next((orig for oh, orig in hashes if bin(oh ^ h).count("1") <= max_distance), None)
        if dup is not None:
            dup.merge(r)
            dup.meta.setdefault("mirrors", []).append(r.url)
            continue
        hashes.append((h, r))
        keep.append(r)
    return keep


# ----------------------------------------------------------------------------
# Relevance
# ----------------------------------------------------------------------------

def _popularity(r: Result) -> float:
    m = r.meta
    raw = max(
        float(m.get("stars") or 0), float(m.get("upvotes") or 0), float(m.get("points") or 0) * 2,
        float(m.get("downloads") or 0) / 50, float(m.get("recommendations") or 0) / 100,
        float(m.get("score") or 0) * 5, float(m.get("cited_by") or 0) * 3, _views(m.get("views")) / 1000,
    )
    return math.log1p(max(raw, 0.0))


def _views(v: Any) -> float:
    if not v:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    digits = re.sub(r"[^\d]", "", str(v))
    return float(digits) if digits else 0.0


_ROMAN = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii"}


def _is_number(token: str) -> bool:
    return token.isdigit() or token in _ROMAN


def topic_match(r: Result, names: list[str], topic_tokens: set[str] | None = None) -> dict[str, Any]:
    """How well a result matches the topic names.

    coverage      share of name tokens present (best name)
    phrase        exact name found anywhere
    partial       a contiguous run of >=2 name tokens covering >=60% of the name and including
                  every sequel number ("тень чернобыля" for "S.T.A.L.K.E.R.: Тень Чернобыля",
                  but not "half life" for "Half-Life 2")
    partial_title the partial run occurs in the title / URL
    """
    text = match_form(r.text_for_scoring())
    head = match_form(f"{r.title} {r.url}")
    words = set(text.split())
    out = {"coverage": 0.0, "phrase": False, "partial": False, "partial_title": False}
    for n in names:
        form = match_form(n)
        toks = [t for t in form.split() if len(t) > 1 or t.isdigit()]
        if not toks:
            continue
        if form.strip() and form in text:
            out.update(coverage=1.0, phrase=True)
            return out
        hits = sum(1 for t in toks if t in words or (len(t) > 3 and t in text))
        out["coverage"] = max(out["coverage"], hits / len(toks))
        all_toks = form.split()
        numbers = {t for t in all_toks if _is_number(t)}
        for size in range(len(all_toks) - 1, 1, -1):
            if size / len(all_toks) < 0.6:
                break
            for i in range(len(all_toks) - size + 1):
                run = all_toks[i:i + size]
                if not numbers <= set(run):
                    continue
                piece = " " + " ".join(run) + " "
                if piece in head:
                    out.update(partial=True, partial_title=True)
                elif piece in text:
                    out["partial"] = True
    return out


def _textual(results: list[Result]) -> list[Result]:
    """Results whose title/snippet is real text (not our synthetic image/file labels)."""
    return [r for r in results if r.kind not in (IMAGE, FILE)]


def context_terms(results: list[Result], topic_tokens: set[str], top: int = 60) -> set[str]:
    """Words that typically surround the topic, learned from results that contain the exact name.

    They separate "Half-Life 2" (valve, gordon, source…) from radioactive half-life, etc.
    """
    core = [r for r in _textual(results) if r.meta.get("phrase")]
    kw, _ = keywords(core, set(topic_tokens) | SYSTEM_WORDS, top=top)
    return {t for t, _ in kw}


def score_results(results: list[Result], names: list[str], topic_tokens: set[str],
                  context: set[str] | None = None) -> set[str]:
    """BM25 relevance + exact phrase + topical context + popularity + agreement + source trust.

    Returns the context-term set used (learned from exact-phrase results when not given).
    """
    docs = [tokenize(r.text_for_scoring()) for r in results]
    n_docs = max(1, len(docs))
    avg_len = sum(len(d) for d in docs) / n_docs or 1.0
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    name_forms = [match_form(n) for n in names if n.strip()]
    strong_forms = [match_form(n) for n in names if n.strip() and not is_weak_name(n)]
    weak_forms = [match_form(n) for n in names if n.strip() and is_weak_name(n)]
    multi_forms = [f for f in strong_forms if len(f.split()) > 1]
    for r in results:
        m = topic_match(r, names, topic_tokens)
        r.meta["coverage"] = round(m["coverage"], 2)
        r.meta["phrase"] = m["phrase"]
        r.meta["partial"] = m["partial"]
        r.meta["partial_title"] = m["partial_title"]
        head = match_form(f"{r.title} {r.url}")
        body = match_form(r.text_for_scoring())
        r.meta["phrase_title"] = any(f in head for f in strong_forms)
        r.meta["phrase_title_weak"] = not r.meta["phrase_title"] and any(f in head for f in weak_forms)
        r.meta["phrase_multi"] = any(f in body for f in multi_forms)
    if context is None:
        context = context_terms(results, topic_tokens)
    q_terms = [t for t in topic_tokens if len(t) > 1]
    k1, b = 1.4, 0.75
    for r, d in zip(results, docs):
        tf = Counter(d)
        bm25 = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            idf = math.log(1 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
            bm25 += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg_len))
        ctx = len(context.intersection(tf))
        r.meta["context"] = ctx
        title_bonus = 3.0 if any(f in match_form(r.title) for f in name_forms) else 0.0
        agreement = min(len(r.found_via) - 1, 6) * 0.8
        score = (bm25 + 4.0 * r.meta["coverage"] + (2.0 if r.meta["phrase"] else 0.0) + title_bonus
                 + (1.5 if r.meta["partial_title"] else 0.0)
                 + 0.25 * min(ctx, 12) + 0.6 * _popularity(r) + agreement
                 + (1.5 if r.meta.get("official") else 0.0) + (0.5 if r.crawled else 0.0))
        r.score = round(score * SOURCE_WEIGHT.get(r.source, 1.0), 3)
    return context


def is_relevant(r: Result, names: list[str] | None = None) -> bool:
    """Strict relevance rule used by the default filter.

    Multi-word names are trusted on an exact match; one-word names ("Control", "Doom") and
    short acronyms ("HK") additionally need words that usually surround the topic.
    """
    m = r.meta
    ctx = m.get("context", 0)
    if m.get("official") or m.get("phrase_title"):
        return True
    if m.get("phrase_title_weak"):
        return ctx >= 1
    if m.get("phrase_multi"):
        return True
    if m.get("phrase"):
        return ctx >= 2
    if m.get("partial_title"):
        return True
    if m.get("partial") and ctx >= 1:
        return True
    return m.get("coverage", 0) >= 0.5 and ctx >= 3


def filter_relevant(results: list[Result], *, strict: bool, min_score: float = 0.0,
                    names: list[str] | None = None) -> list[Result]:
    out = [r for r in results if (not strict or is_relevant(r, names)) and r.score >= min_score]
    out.sort(key=lambda r: -r.score)
    return out


# ----------------------------------------------------------------------------
# Topic intelligence
# ----------------------------------------------------------------------------

def _count_patterns(texts: list[str], patterns: list[tuple[str, re.Pattern[str]]]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for t in texts:
        for label, rx in patterns:
            if rx.search(t):
                counts[label] += 1
    return counts.most_common()


def keywords(results: list[Result], exclude: set[str], top: int = 40) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    docs = [tokenize(f"{r.title} {r.snippet} {r.content[:8000]}") for r in results]
    n = max(1, len(docs))
    df: Counter[str] = Counter()
    bdf: Counter[str] = Counter()
    scores: defaultdict[str, float] = defaultdict(float)
    bscores: defaultdict[str, float] = defaultdict(float)
    for d in docs:
        if not d:
            continue
        tf = Counter(t for t in d if len(t) > 2 and t not in exclude and not t.isdigit())
        bigrams = Counter(f"{a} {b}" for a, b in zip(d, d[1:])
                          if a not in exclude and b not in exclude and len(a) > 2 and len(b) > 2
                          and not a.isdigit() and not b.isdigit())
        df.update(tf.keys())
        bdf.update(bigrams.keys())
        total = sum(tf.values()) or 1
        for t, c in tf.items():
            scores[t] += c / total
        btotal = sum(bigrams.values()) or 1
        for t, c in bigrams.items():
            bscores[t] += c / btotal
    kw = [(t, round(s * math.log(1 + n / df[t]) * df[t], 3)) for t, s in scores.items() if df[t] >= 2]
    bg = [(t, round(s * math.log(1 + n / bdf[t]) * bdf[t], 3)) for t, s in bscores.items() if bdf[t] >= 2]
    kw.sort(key=lambda x: -x[1])
    bg.sort(key=lambda x: -x[1])
    return kw[:top], bg[: top // 2]


def entities(results: list[Result], names: list[str], top: int = 30) -> list[tuple[str, int]]:
    """Capitalised multi-word names (people, studios, places, sequels) seen on >= 2 different sites."""
    name_forms = [match_form(n) for n in names if n.strip()]
    topic_words = {w for f in name_forms for w in f.split()}
    counts: Counter[str] = Counter()
    domains: defaultdict[str, set[str]] = defaultdict(set)
    display: dict[str, str] = {}
    for r in _textual(results):
        seen: set[str] = set()
        for m in _ENTITY_RE.findall(collapse_acronyms(f"{r.title}. {r.snippet}. {r.content[:6000]}")):
            ent = m.strip(" -'’")
            form = match_form(ent)
            if ent in _COMMON_CAPS or len(ent) < 4 or form in seen:
                continue
            if any(form in nf for nf in name_forms):  # the topic itself or a piece of it
                continue
            words = form.split()
            if all(w in SYSTEM_WORDS or w in VIDEO_NOISE or w in topic_words or _is_number(w) for w in words):
                continue
            seen.add(form)
            counts[form] += 1
            domains[form].add(r.domain)
            display.setdefault(form, ent)
    # One site repeating its own menu/brand on every page is not evidence of a related entity.
    ranked = [(display[f], c) for f, c in counts.most_common() if c >= 2 and len(domains[f]) >= 2]
    return ranked[:top]


def timeline(results: list[Result]) -> list[tuple[int, int]]:
    this_year = dt.date.today().year
    years: Counter[int] = Counter()
    for r in results:
        found: set[int] = set()
        if r.date and r.date[:4].isdigit():
            found.add(int(r.date[:4]))
        for y in _YEAR_RE.findall(f"{r.title} {r.snippet} {r.content[:5000]}"):
            found.add(int(y))
        years.update(y for y in found if 1970 <= y <= this_year + 2)
    return sorted(years.items())


def analyze(results: list[Result], names: list[str], topic_tokens: set[str],
            source_status: dict[str, dict] | None = None) -> dict[str, Any]:
    texts = [f"{r.title}\n{r.snippet}\n{r.content[:10000]}" for r in results]
    by_kind = Counter(r.kind for r in results)
    by_source: Counter[str] = Counter()
    for r in results:
        for via in r.found_via:
            by_source[via.split(":", 1)[0]] += 1
    domains = Counter(r.domain for r in results if r.domain)
    files = [r for r in results if r.kind in (FILE, IMAGE, VIDEO)]
    file_types = Counter(file_extension(r.url) or (r.meta.get("ext") or "") for r in files)
    file_types.pop("", None)
    file_cats = Counter(file_category(r.url) or r.meta.get("file_category") or r.kind for r in files)
    code_langs = Counter(r.meta.get("language") for r in results if r.kind in (CODE, REPO) and r.meta.get("language"))
    repo_engines: Counter[str] = Counter()
    for r in results:
        for e in (r.meta.get("tree") or {}).get("engine_hints", []):
            repo_engines[e] += 1
    exclude = set(topic_tokens) | SYSTEM_WORDS | GAME_WORDS
    kw, bigrams = keywords(_textual(results), exclude)
    ents = entities(results, names)
    steam = [r for r in results if r.source == "steam" and r.kind == "game"]
    developers = Counter(d for r in steam for d in r.meta.get("developers", []))
    publishers = Counter(p for r in steam for p in r.meta.get("publishers", []))
    official_sites, _ = discovered_sites(results)
    fandoms = sorted({r.domain.split(".")[0] for r in results if r.domain.endswith(".fandom.com")})
    snippets = sum(len(r.meta.get("code_snippets", [])) for r in results)

    suggested: list[str] = []
    for term, _ in [*ents[:8], *[(b, 0) for b, _ in bigrams[:6]], *kw[:8]]:
        t = term.strip()
        if t.lower() not in {s.lower() for s in suggested} and len(suggested) < 10:
            suggested.append(t)

    highlights = {k: [r.to_dict(content_limit=0) for r in results if r.kind == k][:5] for k in ALL_KINDS}
    gaps = [k for k in (IMAGE, VIDEO, CODE, REPO, FILE) if by_kind.get(k, 0) == 0]
    return {
        "total": len(results),
        "by_kind": dict(by_kind.most_common()),
        "by_source": dict(by_source.most_common()),
        "top_domains": domains.most_common(25),
        "keywords": kw,
        "bigrams": bigrams,
        "entities": ents,
        "engines": _count_patterns(texts, ENGINE_PATTERNS)[:10],
        "repo_engines": repo_engines.most_common(),
        "platforms": _count_patterns(texts, PLATFORM_PATTERNS)[:15],
        "file_types": file_types.most_common(25),
        "file_categories": dict(file_cats.most_common()),
        "code_languages": code_langs.most_common(15),
        "code_snippets": snippets,
        "timeline": timeline(results),
        "developers": developers.most_common(),
        "publishers": publishers.most_common(),
        "official_sites": official_sites,
        "fandom_wikis": fandoms,
        "suggested_queries": suggested,
        "highlights": highlights,
        "gaps": gaps,
        "sources": source_status or {},
        "domains_count": len(domains),
    }


def discovered_sites(results: list[Result]) -> tuple[list[str], list[str]]:
    """Official sites and Fandom wikis worth exploring in the follow-up round."""
    sites: list[str] = []
    for r in results:
        # Only store pages (Steam "website" field) are trusted to name the *official* site.
        site = r.meta.get("website") if r.kind == "game" else None
        dom = domain_of(site) if site else ""
        if not dom or any(dom == s or dom.endswith("." + s) for s in SOCIAL_DOMAINS):
            continue
        if dom not in {domain_of(x) for x in sites}:
            sites.append(site)
    fandoms = sorted({r.domain.split(".")[0] for r in results if r.domain.endswith(".fandom.com")})
    return sites, fandoms

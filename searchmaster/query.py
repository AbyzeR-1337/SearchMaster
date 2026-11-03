"""Query planning: turn one topic into many targeted searches.

The idea is simple: a single query only scratches the surface, so the planner
expands the topic (plus aliases) into facet-specific queries in several
languages and with search-engine operators (filetype:, site:, intitle:).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .utils import search_form, tokenize

# Facet -> templates. "{q}" is replaced with the quoted topic.
TEMPLATES: dict[str, dict[str, list[str]]] = {
    "general": {
        "en": ["{q}", "{q} wiki", "{q} review", "{q} guide walkthrough", "{q} developer interview"],
        "ru": ["{q} обзор", "{q} вики", "{q} гайд прохождение", "{q} интервью разработчиков"],
    },
    "images": {
        "en": ["{q} screenshots", "{q} concept art", "{q} artwork", "{q} wallpaper", "{q} press kit"],
        "ru": ["{q} скриншоты", "{q} арты концепт-арт"],
    },
    "video": {
        "en": ["{q} trailer", "{q} gameplay", "{q} gdc talk", "{q} documentary"],
        "ru": ["{q} трейлер", "{q} геймплей"],
    },
    "code": {
        "en": [
            "{q} source code", "{q} github", "{q} open source reimplementation",
            "{q} decompilation project", "{q} mod tools", "{q} modding sdk",
            "site:github.com {q}", "site:gitlab.com {q}",
        ],
        "ru": ["{q} исходный код", "{q} моды инструменты"],
    },
    "files": {
        "en": [
            "{q} filetype:pdf", "{q} manual filetype:pdf", "{q} design document",
            "{q} soundtrack", "{q} mod download", "{q} game files structure",
        ],
        "ru": ["{q} руководство filetype:pdf", "{q} файлы игры"],
    },
    "discussions": {
        "en": ["{q} reddit", "{q} forum", "{q} discussion", "site:reddit.com {q}"],
        "ru": ["{q} форум", "{q} обсуждение"],
    },
    "technical": {
        "en": [
            "{q} engine", "{q} file format", "{q} reverse engineering", "{q} datamine",
            "{q} modding wiki", "{q} technical analysis", "{q} config files",
        ],
        "ru": ["{q} движок", "{q} формат файлов", "{q} реверс инжиниринг"],
    },
    "history": {
        "en": [
            "{q} development history", "{q} beta", "{q} cut content", "{q} prototype",
            "{q} unused content", "{q} postmortem",
        ],
        "ru": ["{q} история разработки", "{q} вырезанный контент", "{q} бета версия"],
    },
    "news": {
        "en": ["{q} patch notes", "{q} update", "{q} announcement"],
        "ru": ["{q} патч обновление", "{q} новости"],
    },
}

# Short, high-signal phrasings for specialised APIs (they dislike operators).
API_SUFFIXES: dict[str, list[str]] = {
    "code": ["", "mod", "tools", "engine", "decompilation"],
    "images": ["", "screenshot", "concept art"],
    "video": ["", "trailer", "gameplay"],
    "discussions": [""],
    "general": [""],
}


def quote(term: str) -> str:
    term = search_form(term)
    return f'"{term}"' if " " in term and not term.startswith('"') else term


@dataclass
class QueryPlan:
    topic: str
    aliases: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["en", "ru"])
    facets: list[str] = field(default_factory=list)
    extra_terms: list[str] = field(default_factory=list)   # discovered in round 2
    official_sites: list[str] = field(default_factory=list)
    discovered_fandoms: list[str] = field(default_factory=list)
    snowball_only: bool = False   # round 2+: only run queries built from extra_terms

    @property
    def names(self) -> list[str]:
        seen, out = set(), []
        for n in [self.topic, *self.aliases]:
            k = n.strip().lower()
            if k and k not in seen:
                seen.add(k)
                out.append(n.strip())
        return out

    @property
    def topic_tokens(self) -> set[str]:
        toks: set[str] = set()
        for n in self.names:
            toks.update(tokenize(n, keep_stopwords=True))
        return {t for t in toks if len(t) > 1 or t.isdigit()}

    # -------------------------------------------------------------- web
    def web_queries(self, limit: int | None = None, *, operators: bool = True) -> list[tuple[str, str]]:
        """(facet, query) pairs for general web search engines, most important first.

        Queries are interleaved across facets so that a small `limit` still covers
        screenshots, code, files etc. instead of only the first facet.
        """
        per_facet: list[list[tuple[str, str]]] = []
        primary = quote(self.topic)
        for facet in ([] if self.snowball_only else self.facets):
            tpl = TEMPLATES.get(facet, {})
            items: list[tuple[str, str]] = []
            for lang in self.languages:
                for t in tpl.get(lang, []):
                    if not operators and ":" in t:
                        continue
                    items.append((facet, t.format(q=primary)))
            per_facet.append(items)
        # Aliases: the plain name plus the first English template of every facet.
        if not self.snowball_only:
            for alias in self.names[1:]:
                alias_items = [("general", quote(alias))]
                for facet in self.facets:
                    en = [t for t in TEMPLATES.get(facet, {}).get("en", []) if operators or ":" not in t]
                    if en and en[0] != "{q}":
                        alias_items.append((facet, en[0].format(q=quote(alias))))
                    elif len(en) > 1:
                        alias_items.append((facet, en[1].format(q=quote(alias))))
                per_facet.append(alias_items)
        for term in self.extra_terms:
            per_facet.append([("snowball", f"{primary} {term}")])

        ordered: list[tuple[str, str]] = []
        seen: set[str] = set()
        idx = 0
        while any(idx < len(lst) for lst in per_facet):
            for lst in per_facet:
                if idx < len(lst) and lst[idx][1] not in seen:
                    seen.add(lst[idx][1])
                    ordered.append(lst[idx])
            idx += 1
        return ordered[:limit] if limit else ordered

    # -------------------------------------------------------------- APIs
    def api_queries(self, facet: str = "general", limit: int = 4) -> list[str]:
        """Short keyword queries for site APIs (GitHub, YouTube, Reddit, ...)."""
        out: list[str] = []
        suffixes = API_SUFFIXES.get(facet, [""])
        for name in ([] if self.snowball_only else self.names):
            for suf in suffixes:
                q = f"{search_form(name)} {suf}".strip()
                if q not in out:
                    out.append(q)
        for term in self.extra_terms:
            q = f"{search_form(self.topic)} {term}"
            if q not in out:
                out.append(q)
        return out[:limit]

    def wants(self, facet: str) -> bool:
        return facet in self.facets

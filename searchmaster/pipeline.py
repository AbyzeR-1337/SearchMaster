"""The search pipeline: query all sources in parallel, analyse, expand, enrich, download, report."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from .ai import ai_summary
from .analyze import (
    compute_stats, expansion_queries, find_aliases, merge_results, related_terms, score_all, timeline,
)
from .config import Context
from .download import Downloader
from .fetch import enrich_pages
from .models import Result, SourceStatus
from .report import Report
from .sources import ALL_SOURCES, Source, Wayback


class Pipeline:
    def __init__(self, ctx: Context, source_names: list[str], out_dir: Path, progress=print) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self.out_dir = out_dir
        self.progress = progress
        self.sources: dict[str, Source] = {name: ALL_SOURCES[name](ctx) for name in source_names}
        self.statuses: dict[str, SourceStatus] = {
            name: SourceStatus(name=name, title=src.title) for name, src in self.sources.items()
        }
        self.weights = {name: src.weight for name, src in self.sources.items()}
        self.weights["crawl"] = 0.9

    # ------------------------------------------------------------------ helpers
    def _run_source(self, name: str, query: str, limit: int, first_round: bool) -> list[Result]:
        src = self.sources[name]
        st = self.statuses[name]
        ok, why = src.available()
        wanted = self.cfg.wanted_categories
        if ok and wanted and src.categories and not wanted & set(src.categories):
            ok, why = False, "не нужен для выбранного типа"
        if not ok:
            if st.state == "pending":
                st.state, st.message = "skipped", why
            return []
        if st.slow:
            return []
        # exact search: send the name as a "quoted phrase" where the source understands it
        if '"' in query:  # already phrased (deep-mode follow-ups): keep quotes only where they are understood
            q = query if src.phrase_ok else query.replace('"', "")
        else:
            q = f'"{query}"' if self.cfg.exact and src.phrase_ok and " " in query.strip() else query
        started = time.monotonic()
        try:
            found = src.search(q, limit)
        except Exception as exc:
            if st.state in ("pending", "empty") and not st.count:
                st.state, st.message = "error", str(exc)[:200]
            if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
                st.slow = True
            self.ctx.log.debug("source %s failed", name, exc_info=True)
            return []
        finally:
            st.seconds += time.monotonic() - started
        st.count += len(found)
        if st.count:
            st.state, st.message = "ok", ""
        elif st.state == "pending":
            st.state = "empty"
        return found

    def _round(self, names: list[str], query: str, limit: int, first_round: bool) -> list[Result]:
        with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
            futures = {name: pool.submit(self._run_source, name, query, limit, first_round) for name in names}
            out: list[Result] = []
            for name, fut in futures.items():
                out += fut.result()
        return out

    @staticmethod
    def _game_card(results: list[Result]) -> Result | None:
        cards = [r for r in results if r.meta.get("game_card") and (r.meta.get("match") or 0) >= 0.5]
        return max(cards, key=lambda r: r.meta.get("match", 0), default=None)

    # ------------------------------------------------------------------ main
    def run(self) -> dict:
        cfg = self.cfg
        say = self.progress
        say(f"🔍 Ищу «{cfg.query}» · режим {cfg.mode} · источников: {len(self.sources)}")

        # 1. first round over every selected source, once per name of the subject
        if len(cfg.names) > 1:
            say(f"  🏷 названия: {' · '.join(cfg.names)}")
        if cfg.exact:
            say("  🎯 точный поиск: только результаты с названием целиком")
        if cfg.only:
            say(f"  🔎 только: {', '.join(cfg.only)}")
        results = []
        for i, name in enumerate(cfg.names):
            results += self._round(list(self.sources), name, cfg.limit, first_round=(i == 0))
        results = merge_results(results)
        queries = list(cfg.names)
        score_all(results, queries, self.weights, cfg.exact)
        say(f"  ✓ первый проход: {len(results)} уникальных результатов")

        # 2. other names of the subject (e.g. the English title for a Russian query) → second pass
        aliases = [a for a in find_aliases(cfg.names[0], results, cfg.langs)
                   if a.lower() not in (n.lower() for n in cfg.names)]
        if aliases:
            queries += aliases
            say(f"  🌐 другие названия: {', '.join(aliases)}")
            alias_sources = [n for n in self.sources if n != "wikipedia"]
            extra: list[Result] = []
            for alias in aliases[:2]:
                found = self._round(alias_sources, alias, cfg.limit, first_round=False)
                for r in found:
                    r.meta.setdefault("via_query", alias)
                extra += found
            results = merge_results(results + extra)
            score_all(results, queries, self.weights, cfg.exact)
            say(f"  ✓ с учётом других названий: {len(results)}")

        # 3. understand what we found: a game? related terms?
        game = self._game_card(results)
        game_name = game.meta.get("name") if game else None
        if game_name:
            say(f"  🎮 распознана игра: {game_name}")
            if game_name.lower() not in (q.lower() for q in queries):
                queries.append(game_name)
        terms = related_terms(results, queries)

        # 4. deep mode: expanded queries
        base_name = game_name or (aliases[0] if aliases else None)
        expanded = expansion_queries(cfg.names[0], terms, base_name, cfg.expansions)
        if cfg.exact:
            # keep the name itself as an exact phrase: "Seven Souls Online" screenshots
            for name in sorted({base_name or cfg.names[0], *cfg.names}, key=len, reverse=True):
                expanded = [f'"{name}"{q[len(name):]}' if q.startswith(name) and '"' not in q else q for q in expanded]
        if expanded:
            names = [n for n, s in self.sources.items() if s.expandable]
            say(f"  ↻ расширенный поиск: {len(expanded)} доп. запросов × {len(names)} источников")
            extra: list[Result] = []
            for q in expanded:
                found = self._round(names, q, max(5, cfg.limit // 3), first_round=False)
                for r in found:
                    r.meta.setdefault("via_query", q)
                extra += found
            results = merge_results(results + extra)
            score_all(results, queries, self.weights, cfg.exact)
            say(f"  ✓ после расширения: {len(results)}")

        # 5. archived copies of the official site
        if game and game.meta.get("website"):
            results = merge_results(results + Wayback(self.ctx).snapshots_for([game.meta["website"]]))

        # 6. read the best pages, find files/images/repos linked from them
        if cfg.fetch_pages:
            say(f"  📖 анализирую страницы (до {cfg.fetch_pages})…")
            discovered = enrich_pages(self.ctx, results, cfg.fetch_pages, queries)
            results = merge_results(results + discovered)
            score_all(results, queries, self.weights, cfg.exact)
            terms = related_terms(results, queries)
            say(f"  ✓ найдено на страницах: {len(discovered)} новых ссылок")

        # only the kinds of results that were asked for (the main card stays)
        wanted = cfg.wanted_categories
        if wanted:
            results = [r for r in results if r.category in wanted or r.meta.get("game_card") or r.meta.get("summary")]

        # 7. downloads
        download = None
        if cfg.download:
            say("  💾 скачиваю скриншоты и файлы…")
            download = Downloader(self.ctx, self.out_dir).run(results)
            say(f"  ✓ скачано: {len(download.files)} ({download.total_bytes / 1048576:.1f} МБ)")

        # 8. optional AI analysis
        shown = [r for r in results if r.score >= cfg.min_score]
        cited = shown[:60]
        ai_text, ai_status = ai_summary(self.ctx, cited, terms, cfg.langs[0] if cfg.langs else "ru", aliases)
        if ai_text:
            say("  🤖 AI-анализ готов")

        data = {
            "results": results,
            "min_score": cfg.min_score,
            "stats": compute_stats(shown),
            "terms": terms,
            "timeline": timeline(shown),
            "expanded_queries": expanded,
            "statuses": list(self.statuses.values()),
            "download": download,
            "ai_text": ai_text,
            "ai_cited": cited,
            "ai_status": ai_status,
            "game": game_name,
            "aliases": aliases,
            "names": cfg.names,
            "exact": cfg.exact,
            "only": cfg.only,
            "links": Report.actions_links(self.out_dir),
            "domains": sorted({urlsplit(r.url).netloc for r in shown}),
        }
        return data

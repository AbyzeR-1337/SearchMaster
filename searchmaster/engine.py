"""Orchestration: plan -> search all sources -> snowball round -> deep crawl -> analyse -> report."""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import analyze as an
from .config import Config
from .crawler import Crawler
from .models import Result, SourceBlocked
from .net import HttpClient
from .query import QueryPlan
from .sources import FOLLOWUP_SOURCES, Source, select_sources
from .sources.wiki import FandomSource
from .utils import match_form, slugify

log = logging.getLogger("searchmaster.engine")

# Sources worth re-running in the snowball round (they understand extra_terms).
SNOWBALL_SOURCES = {"bing", "duckduckgo", "brave", "google", "searxng", "github", "youtube",
                    "archive_org", "reddit", "hackernews", "bing_images"}


@dataclass
class RunResult:
    config: Config
    plan: QueryPlan
    results: list[Result]
    analysis: dict[str, Any]
    source_status: dict[str, dict]
    started: str
    finished: str
    out_dir: Path
    http_stats: dict[str, int]
    notes: list[str] = field(default_factory=list)
    ai_summary: str | None = None
    ai_expansion: dict[str, Any] | None = None
    downloads: list[dict] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)


class Engine:
    def __init__(self, config: Config, progress: Callable[[str], None] | None = None):
        self.config = config
        self.http = HttpClient(config)
        self.progress = progress or (lambda msg: None)
        self.status: dict[str, dict] = {}
        self.notes: list[str] = []
        self.rounds: list[dict] = []
        self.context: set[str] = set()

    # ------------------------------------------------------------ sources
    def _run_source(self, cls: type[Source], plan: QueryPlan, tag: str) -> list[Result]:
        src = cls(self.http, self.config)
        st = self.status.setdefault(cls.name, {"status": "ok", "count": 0, "errors": [], "seconds": 0.0})
        t0 = time.monotonic()
        out: list[Result] = []
        cap = self.config.max_per_source * 25
        try:
            for r in src.search(plan):
                if r.url:
                    out.append(r)
                if len(out) >= cap:
                    break
                if time.monotonic() - t0 > self.config.source_budget:
                    st["status"] = "partial"
                    st["errors"].append(f"{tag}: превышен лимит времени {self.config.source_budget:.0f} с")
                    break
        except SourceBlocked as exc:
            st["status"] = "blocked"
            st["errors"].append(f"{tag}: {exc}")
        except Exception as exc:  # a broken source must never kill the whole run
            st["status"] = "error" if not out else "partial"
            st["errors"].append(f"{tag}: {type(exc).__name__}: {str(exc)[:200]}")
            log.debug("source %s failed", cls.name, exc_info=True)
        st["count"] += len(out)
        st["seconds"] = round(st["seconds"] + time.monotonic() - t0, 1)
        self.progress(f"  {cls.name:<14} {tag:<9} +{len(out):<4} {'' if st['status'] == 'ok' else '[' + st['status'] + ']'}")
        return out

    def _run_many(self, classes: list[type[Source]], plan: QueryPlan, tag: str) -> list[Result]:
        results: list[Result] = []
        # A site that already showed us a captcha / 403 will not change its mind this run.
        runnable = [c for c in classes if self.status.get(c.name, {}).get("status") != "blocked"
                    and c(self.http, self.config).relevant(plan)]
        with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
            futs = [pool.submit(self._run_source, cls, plan, tag) for cls in runnable]
            for fut in as_completed(futs):
                results.extend(fut.result())
        return results

    # ------------------------------------------------------------ helpers
    def _score(self, results: list[Result], plan: QueryPlan) -> list[Result]:
        results = an.dedupe(results)
        self.context = an.score_results(results, plan.names, plan.topic_tokens)
        results.sort(key=lambda r: -r.score)
        return results

    def _snowball_terms(self, results: list[Result], plan: QueryPlan, ai_terms: list[str]) -> list[str]:
        relevant = an.filter_relevant(results, strict=True, names=plan.names)[:150]
        name_forms = " | ".join(match_form(n) for n in plan.names)
        terms: list[str] = []

        def add(t: str) -> None:
            t = t.strip()
            form = match_form(t)
            if not t or len(t) < 3 or form in name_forms or any(form == match_form(x) for x in terms):
                return
            topic_words = set(name_forms.split())
            if all(w in an.SYSTEM_WORDS or w in an.VIDEO_NOISE or w in topic_words for w in form.split()):
                return
            terms.append(t)

        for t in ai_terms[:4]:
            add(t)
        for r in relevant:
            for dev in r.meta.get("developers", [])[:1]:
                add(dev)
        for ent, _ in an.entities(relevant, plan.names, top=10)[:3]:
            add(ent)
        _, bigrams = an.keywords(an._textual(relevant), set(plan.topic_tokens) | an.SYSTEM_WORDS | an.GAME_WORDS)
        for bg, _ in bigrams[:2]:
            add(bg)
        return terms[:6]

    # ---------------------------------------------------------------- run
    def run(self) -> RunResult:
        cfg = self.config
        started = dt.datetime.now()
        plan = QueryPlan(topic=cfg.query, aliases=list(cfg.aliases), languages=cfg.languages, facets=cfg.facets)
        classes, notes = select_sources(cfg)
        self.notes.extend(notes)

        ai_expansion = None
        ai_terms: list[str] = []
        if cfg.ai:
            from . import ai
            self.progress("ИИ: подбираю альтернативные названия и связанные термины…")
            try:
                ai_expansion = ai.expand_query(cfg.query, model=cfg.ai_model)
                for a in ai_expansion.get("aliases", [])[:4]:
                    if a.lower() not in {x.lower() for x in plan.aliases}:
                        plan.aliases.append(a)
                ai_terms = ai_expansion.get("related_terms", [])
                self.progress(f"ИИ: синонимы {plan.aliases or '—'}; термины {ai_terms or '—'}")
            except ai.AIUnavailable as exc:
                self.notes.append(f"ИИ недоступен: {exc}")
                self.progress(f"ИИ недоступен: {exc}")

        # Round 1 ----------------------------------------------------------
        self.progress(f"Раунд 1: {len(classes)} источников, {len(plan.web_queries(cfg.web_queries_per_engine))} веб-запросов на движок")
        raw = self._run_many(classes, plan, "раунд 1")
        results = self._score(raw, plan)
        self.rounds.append({"round": 1, "raw": len(raw), "unique": len(results)})

        # Round 2: snowball + follow-ups --------------------------------
        if cfg.rounds >= 2:
            terms = self._snowball_terms(results, plan, ai_terms)
            sites, fandoms = an.discovered_sites(results)
            plan.official_sites = sites
            plan.discovered_fandoms = fandoms
            jobs: list[tuple[list[type[Source]], QueryPlan, str]] = []
            if terms:
                self.progress(f"Раунд 2 (снежный ком): новые термины → {', '.join(terms)}")
                snow = dataclasses.replace(plan, extra_terms=terms, snowball_only=True)
                jobs.append(([c for c in classes if c.name in SNOWBALL_SOURCES], snow, "раунд 2"))
            follow: list[type[Source]] = []
            if sites and (cfg.sources is None or "wayback" in (cfg.sources or [])) and "wayback" not in cfg.exclude_sources:
                self.progress(f"Wayback: официальные сайты {', '.join(sites[:3])}")
                follow.extend(FOLLOWUP_SOURCES)
            fandom_enabled = any(c is FandomSource for c in classes)
            if fandoms and fandom_enabled and not self.status.get("fandom", {}).get("count"):
                follow.append(FandomSource)
            if follow:
                jobs.append((follow, plan, "follow-up"))
            raw2: list[Result] = []
            with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:  # snowball and follow-ups in parallel
                for part in pool.map(lambda job: self._run_many(*job), jobs):
                    raw2.extend(part)
            if raw2:
                results = self._score(results + raw2, plan)
            self.rounds.append({"round": 2, "terms": terms, "official_sites": sites, "fandoms": fandoms,
                                "raw": len(raw2), "unique": len(results)})

        # Deep analysis -------------------------------------------------
        if cfg.deep > 0:
            self.progress(f"Глубокий анализ: загружаю и разбираю до {cfg.deep} лучших страниц…")
            crawler = Crawler(self.http, cfg, plan.topic_tokens, progress=self.progress if cfg.verbose else None)
            relevant_first = an.filter_relevant(results, strict=True, names=plan.names)
            new = crawler.enrich(relevant_first)
            self.status["crawler"] = {"status": "ok", "count": len(new), "errors": [], "seconds": 0,
                                      "pages": crawler.pages_done}
            results = self._score(results + new, plan)

        # Filter + analyse -----------------------------------------------
        kept = an.filter_relevant(results, strict=cfg.strict, min_score=cfg.min_score, names=plan.names)
        kept = an.drop_near_duplicates(kept)
        kept.sort(key=lambda r: -r.score)
        self.progress(f"Найдено уникальных: {len(results)}, релевантных после фильтра: {len(kept)}")
        analysis = an.analyze(kept, plan.names, plan.topic_tokens, self.status)
        analysis["filtered_out"] = len(results) - len(kept)
        analysis["context_terms"] = sorted(self.context)[:60]

        out_dir = Path(cfg.out_dir) / f"{slugify(cfg.query)}-{started:%Y%m%d-%H%M%S}"
        run = RunResult(
            config=cfg, plan=plan, results=kept, analysis=analysis, source_status=self.status,
            started=started.isoformat(timespec="seconds"), finished="", out_dir=out_dir,
            http_stats=dict(self.http.stats), notes=self.notes, ai_expansion=ai_expansion, rounds=self.rounds,
        )

        if cfg.ai:
            from . import ai
            self.progress("ИИ: пишу аналитическую сводку…")
            try:
                run.ai_summary = ai.summarize(cfg.query, kept, analysis, model=cfg.ai_model, effort=cfg.ai_effort)
            except ai.AIUnavailable as exc:
                self.notes.append(f"ИИ-сводка не создана: {exc}")
                self.progress(f"ИИ-сводка не создана: {exc}")

        if cfg.download:
            from .downloader import download_results
            self.progress(f"Скачиваю: {', '.join(cfg.download)} (не больше {cfg.max_downloads} файлов)…")
            run.downloads = download_results(kept, cfg, self.http, out_dir / "downloads", progress=self.progress)

        run.finished = dt.datetime.now().isoformat(timespec="seconds")
        run.http_stats = dict(self.http.stats)
        return run

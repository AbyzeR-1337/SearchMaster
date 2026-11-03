"""Command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__
from .config import FACETS, Config, load_dotenv
from .downloader import CHOICES
from .models import KIND_LABELS_RU
from .sources import ALL_SOURCES, FOLLOWUP_SOURCES

EPILOG = """
примеры:
  searchmaster "Half-Life 2"
  searchmaster "S.T.A.L.K.E.R." --alias "Сталкер" --alias "STALKER Shadow of Chernobyl"
  searchmaster "Doom 1993" --facets code,technical,files --download images,docs --deep 40
  searchmaster "Hollow Knight" --ai --rounds 2 --crawl-depth 1
  searchmaster "Minecraft" --sources github,sourcegraph,youtube --max-per-source 50
  searchmaster --list-sources

ключи API (необязательно) берутся из окружения или файла .env:
  GITHUB_TOKEN  BRAVE_API_KEY  GOOGLE_API_KEY+GOOGLE_CSE_ID  SEARXNG_URL  YOUTUBE_API_KEY
  STACKEXCHANGE_KEY  ANTHROPIC_API_KEY (для --ai)
"""


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="searchmaster",
        description="Ищет по всему интернету всё по одной теме/игре: страницы, скриншоты, видео, "
                    "исходный код, файлы, обсуждения, архивы — и анализирует найденное.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("query", nargs="?", help="тема или название игры (если не указано — спросит)")
    p.add_argument("-a", "--alias", action="append", default=[], help="альтернативное название (можно несколько раз)")
    p.add_argument("--lang", type=_csv, default=["en", "ru"], help="языки запросов и Википедии, напр. en,ru,ja (по умолчанию en,ru)")
    p.add_argument("--facets", type=_csv, default=FACETS, help=f"аспекты поиска: {','.join(FACETS)} (по умолчанию все)")
    p.add_argument("--sources", type=_csv, default=None, help="только эти источники (см. --list-sources)")
    p.add_argument("--exclude", type=_csv, default=[], help="исключить источники")
    p.add_argument("--max-per-source", type=int, default=30, help="результатов на один запрос источника (30)")
    p.add_argument("--web-queries", type=int, default=14, help="сколько расширенных запросов отправлять в каждый веб-поисковик (14)")
    p.add_argument("--deep", type=int, default=25, help="сколько лучших страниц скачать и разобрать целиком (25, 0 = выкл.)")
    p.add_argument("--crawl-depth", type=int, default=0, help="переходить по релевантным ссылкам на N уровней (0)")
    p.add_argument("--rounds", type=int, default=2, choices=(1, 2), help="2 = «снежный ком»: второй раунд по найденным терминам (2)")
    p.add_argument("--no-strict", action="store_true", help="не отбрасывать результаты, где тема не упоминается")
    p.add_argument("--download", type=_csv, default=[],
                   help=f"скачать найденное: {','.join(CHOICES)},all (all — всё, кроме исполняемых файлов)")
    p.add_argument("--max-downloads", type=int, default=200, help="максимум файлов для скачивания (200)")
    p.add_argument("--max-file-mb", type=float, default=50, help="максимальный размер одного файла, МБ (50)")
    p.add_argument("--ai", action="store_true", help="ИИ-анализ через Claude: синонимы до поиска и аналитическая сводка после")
    p.add_argument("--ai-model", default="claude-opus-5-5", help="модель Claude (claude-opus-5-5)")
    p.add_argument("--ai-effort", default="medium", choices=("low", "medium", "high", "xhigh", "max"), help="глубина рассуждений модели (medium)")
    p.add_argument("-o", "--out", default="results", help="папка для отчётов (results)")
    p.add_argument("--format", type=_csv, default=["html", "md", "json"], help="форматы отчёта: html,md,json")
    p.add_argument("--workers", type=int, default=8, help="параллельных потоков (8)")
    p.add_argument("--timeout", type=float, default=20, help="таймаут запроса, с (20)")
    p.add_argument("--delay", type=float, default=1.0, help="пауза между запросами к одному сайту, с (1.0)")
    p.add_argument("--no-cache", action="store_true", help="не использовать кеш ответов (.cache/searchmaster)")
    p.add_argument("--list-sources", action="store_true", help="показать все источники и выйти")
    p.add_argument("-v", "--verbose", action="store_true", help="подробный вывод")
    p.add_argument("--version", action="version", version=f"SearchMaster {__version__}")
    return p


def list_sources() -> None:
    cfg = Config(query="")
    print("Источники SearchMaster:\n")
    for cls in ALL_SOURCES + FOLLOWUP_SOURCES:
        ok = cls.available(cfg)
        need = f"  [нужно: {', '.join(cls.requires)}]" if cls.requires else ""
        mark = "✓" if ok else "·"
        print(f"  {mark} {cls.name:<14} {cls.description}{need}")
    print("\n  ✓ — доступен сейчас, · — нужен ключ в окружении/.env")
    print("  wayback запускается автоматически, когда найден официальный сайт.")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.list_sources:
        list_sources()
        return 0
    query = args.query
    if not query:
        try:
            query = input("Что ищем? Тема или название игры: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    if not query:
        print("Пустой запрос.", file=sys.stderr)
        return 1

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    bad_facets = set(args.facets) - set(FACETS)
    if bad_facets:
        print(f"Неизвестные аспекты: {', '.join(bad_facets)}. Доступны: {', '.join(FACETS)}", file=sys.stderr)
        return 2
    bad_dl = set(args.download) - set(CHOICES) - {"all"}
    if bad_dl:
        print(f"Неизвестные категории скачивания: {', '.join(bad_dl)}", file=sys.stderr)
        return 2

    cfg = Config(
        query=query, aliases=args.alias, languages=args.lang, facets=args.facets, sources=args.sources,
        exclude_sources=args.exclude, max_per_source=args.max_per_source, web_queries_per_engine=args.web_queries,
        deep=args.deep, crawl_depth=args.crawl_depth, rounds=args.rounds, strict=not args.no_strict,
        download=args.download, max_downloads=args.max_downloads, max_file_mb=args.max_file_mb,
        ai=args.ai, ai_model=args.ai_model, ai_effort=args.ai_effort, out_dir=args.out, formats=args.format,
        workers=args.workers, timeout=args.timeout, per_host_delay=args.delay,
        cache_dir=None if args.no_cache else ".cache/searchmaster", verbose=args.verbose,
    )

    from .engine import Engine
    from .report import write_reports

    t0 = time.monotonic()
    start = time.strftime("%H:%M:%S")

    def progress(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

    progress(f"SearchMaster {__version__}: «{query}» (старт {start})")
    try:
        run = Engine(cfg, progress=progress).run()
    except KeyboardInterrupt:
        print("\nПрервано.", file=sys.stderr)
        return 130
    files = write_reports(run)

    a = run.analysis
    print()
    print(f"Готово за {time.monotonic() - t0:.0f} с. Релевантных результатов: {a['total']}")
    for kind, n in a["by_kind"].items():
        print(f"  {KIND_LABELS_RU.get(kind, kind):<26} {n}")
    blocked = [n for n, s in run.source_status.items() if s.get("status") in ("blocked", "error")]
    if blocked:
        print(f"Недоступные источники: {', '.join(blocked)} (подробности в отчёте)")
    if a.get("engines"):
        print("Движки:   " + ", ".join(f"{e} ({c})" for e, c in a["engines"][:5]))
    if a.get("developers"):
        print("Разработчик: " + ", ".join(d for d, _ in a["developers"][:3]))
    if run.downloads:
        ok = sum(1 for d in run.downloads if "path" in d)
        print(f"Скачано файлов: {ok} → {run.out_dir / 'downloads'}")
    for fmt, path in files.items():
        print(f"Отчёт {fmt.upper():<4} → {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

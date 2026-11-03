"""Command line interface.

Examples::

    python -m searchmaster "Hollow Knight"
    python -m searchmaster "Hollow Knight" --mode deep --code-zips 3 --open
    python -m searchmaster "квантовые компьютеры" --sources text,code --langs ru,en
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from .config import MODES, Config, Context
from .http import Http
from .pipeline import Pipeline
from .report import Report, statuses_table
from .sources import ALL_SOURCES, PRESETS, resolve_sources
from .textutil import slugify


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="searchmaster",
        description="SearchMaster — ищет всё по теме или игре: тексты, вики, скриншоты, видео, исходный код, файлы, "
        "моды и обсуждения, анализирует найденное и собирает отчёт.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Наборы источников: " + ", ".join(PRESETS) + ". Пример: --sources game,-reddit",
    )
    p.add_argument("query", nargs="*", help="что искать (тема, игра, программа, человек…)")
    p.add_argument("-m", "--mode", choices=list(MODES), default="normal",
                   help="quick — быстро; normal — по умолчанию; deep — максимально глубоко (доп. запросы, больше страниц)")
    p.add_argument("-s", "--sources", default="all", help="набор или список источников через запятую (по умолчанию all)")
    p.add_argument("--langs", default="ru,en", help="языки Википедии/вики через запятую (по умолчанию ru,en)")
    p.add_argument("-n", "--limit", type=int, help="результатов на источник")
    p.add_argument("--pages", type=int, dest="fetch_pages", help="сколько найденных страниц прочитать и проанализировать")
    p.add_argument("--expansions", type=int, help="сколько дополнительных запросов сделать (режим deep)")
    p.add_argument("-o", "--out", help="папка для результатов (по умолчанию results/<дата>_<запрос>)")
    p.add_argument("--no-download", action="store_true", help="ничего не скачивать, только ссылки")
    p.add_argument("--images", type=int, dest="download_images", help="максимум скачиваемых изображений")
    p.add_argument("--files", type=int, dest="download_files", help="максимум скачиваемых файлов/документов")
    p.add_argument("--code-zips", type=int, default=0, help="скачать ZIP исходников N самых популярных репозиториев")
    p.add_argument("--max-file-mb", type=float, help="лимит размера одного файла, МБ (25)")
    p.add_argument("--max-total-mb", type=float, help="общий лимит скачивания, МБ (400)")
    p.add_argument("--allow-ext", default="", help="разрешить скачивать эти расширения (через запятую), напр. .jar")
    p.add_argument("--min-score", type=float, help="порог релевантности для отчёта (0.12)")
    p.add_argument("--ai", choices=["auto", "on", "off"], default="auto",
                   help="AI-анализ найденного через Claude (нужен ANTHROPIC_API_KEY)")
    p.add_argument("--ai-model", default=None, help="модель Claude для AI-анализа")
    p.add_argument("--open", action="store_true", help="открыть report.html в браузере после поиска")
    p.add_argument("--list-sources", action="store_true", help="показать источники и выйти")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def list_sources(cfg: Config) -> str:
    ctx = Context(config=cfg, http=Http(), log=logging.getLogger("searchmaster"))
    lines = ["Источники:"]
    for name, cls in ALL_SOURCES.items():
        ok, why = cls(ctx).available()
        lines.append(f"  {'✅' if ok else '⏭'} {name:<14} {cls.description}" + (f"  [{why}]" if not ok else ""))
    lines.append("")
    lines.append("Наборы: " + "; ".join(f"{k} = {', '.join(v)}" for k, v in PRESETS.items() if k != "all"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles
    except (AttributeError, ValueError):
        pass

    query = " ".join(args.query).strip()
    if args.list_sources:
        print(list_sources(Config.build(query or "-")))
        return 0
    if not query:
        try:
            query = input("Что искать? ").strip()
        except EOFError:
            query = ""
    if not query:
        print("Пустой запрос.", file=sys.stderr)
        return 2

    try:
        source_names = resolve_sources(args.sources)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    cfg = Config.build(
        query,
        args.mode,
        langs=[l.strip() for l in args.langs.split(",") if l.strip()] or ["en"],
        limit=args.limit,
        fetch_pages=args.fetch_pages,
        expansions=args.expansions,
        download=not args.no_download,
        download_images=args.download_images,
        download_files=args.download_files,
        code_zips=args.code_zips,
        max_file_mb=args.max_file_mb,
        max_total_mb=args.max_total_mb,
        allow_ext={("." + e.strip().lstrip(".")).lower() for e in args.allow_ext.split(",") if e.strip()},
        min_score=args.min_score,
        ai=args.ai,
        ai_model=args.ai_model,
    )
    if args.ai == "on" and not cfg.key("ANTHROPIC_API_KEY"):
        print("⚠️  --ai on, но ANTHROPIC_API_KEY не задан — AI-анализ будет пропущен.", file=sys.stderr)

    out_dir = Path(args.out or f"results/{datetime.now():%Y-%m-%d_%H%M}_{slugify(query)}")
    ctx = Context(config=cfg, http=Http(), log=logging.getLogger("searchmaster"))
    pipeline = Pipeline(ctx, source_names, out_dir, progress=lambda m: print(m, flush=True))
    data = pipeline.run()

    report = Report(out_dir, query, cfg.mode)
    paths = report.write(data)

    print()
    print(statuses_table(data["statuses"]))
    print()
    print(f"📄 Отчёт:   {paths['md']}")
    print(f"🌐 HTML:    {paths['html']}")
    print(f"🧾 Данные:  {paths['json']}  ·  {paths['csv']}")
    if data.get("ai_status"):
        print(f"🤖 AI:      {data['ai_status']}")

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write(paths["summary"].read_text(encoding="utf-8") + "\n")

    if args.open:
        webbrowser.open(paths["html"].resolve().as_uri())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

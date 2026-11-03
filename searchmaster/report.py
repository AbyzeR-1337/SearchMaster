"""Reports: Markdown (great on a phone / in GitHub), interactive HTML, JSON and CSV."""

from __future__ import annotations

import csv
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CATEGORIES, CATEGORY_ORDER, Result, SourceStatus
from .textutil import truncate

STATE_ICON = {"ok": "✅", "empty": "➖", "error": "⚠️", "skipped": "⏭"}
GALLERY_SIZE = 24


def md_escape(text: str | None) -> str:
    text = (text or "").replace("\n", " ").replace("|", "\\|")
    return re.sub(r"([\[\]<>])", r"\\\1", text)


def link_citations(text: str, cited: list[Result]) -> str:
    """Turn [3] / [3, 5] references from the AI summary into links."""

    def repl(match: re.Match) -> str:
        parts = []
        for num in re.findall(r"\d+", match.group(1)):
            idx = int(num) - 1
            if 0 <= idx < len(cited):
                parts.append(f"[{num}]({cited[idx].url})")
            else:
                parts.append(num)
        return "[" + ", ".join(parts) + "]"

    return re.sub(r"\[(\d+(?:\s*[,;–-]\s*\d+)*)\]", repl, text)


def _date_label(value: str | None) -> str:
    value = str(value or "")
    return value[:10] if len(value) >= 10 and value[4:5] == "-" else value


def _meta_line(r: Result) -> str:
    bits = []
    m = r.meta
    if m.get("site"):
        bits.append(str(m["site"]))
    if m.get("stars"):
        bits.append(f"⭐ {m['stars']}")
    if m.get("language"):
        bits.append(str(m["language"]))
    if m.get("downloads"):
        bits.append(f"⬇ {m['downloads']}")
    if m.get("points"):
        bits.append(f"▲ {m['points']}")
    if m.get("comments"):
        bits.append(f"💬 {m['comments']}")
    if m.get("license"):
        bits.append(f"© {m['license']}")
    if r.date:
        bits.append(_date_label(r.date))
    if r.author:
        bits.append(f"👤 {truncate(r.author, 40)}")
    if r.local_path:
        bits.append(f"💾 `{r.local_path}`")
    return " · ".join(bits)


class Report:
    def __init__(self, out_dir: Path, query: str, mode: str) -> None:
        self.out_dir = out_dir
        self.query = query
        self.mode = mode
        self.created = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ links (GitHub Actions)
    @staticmethod
    def actions_links(out_dir: Path) -> dict[str, str]:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        repo = os.environ.get("GITHUB_REPOSITORY")
        run_id = os.environ.get("GITHUB_RUN_ID")
        branch = os.environ.get("SM_RESULTS_BRANCH")
        links: dict[str, str] = {}
        if repo and run_id:
            links["run"] = f"{server}/{repo}/actions/runs/{run_id}"
        if repo and branch:
            links["report"] = f"{server}/{repo}/blob/{branch}/{out_dir.as_posix()}/report.md"
            links["folder"] = f"{server}/{repo}/tree/{branch}/{out_dir.as_posix()}"
        return links

    # ------------------------------------------------------------------ markdown
    def markdown(self, data: dict[str, Any], full: bool = True) -> str:
        results: list[Result] = data["results"]
        shown = [r for r in results if r.score >= data["min_score"]]
        hidden = len(results) - len(shown)
        stats = data["stats"]
        per_cat = 40 if full else 8
        out: list[str] = []
        w = out.append

        w(f"# 🔍 {md_escape(self.query)}")
        w("")
        w(f"_SearchMaster · режим **{self.mode}** · {self.created:%Y-%m-%d %H:%M} UTC · "
          f"найдено **{len(shown)}** релевантных результатов"
          + (f" (ещё {hidden} отсеяно как нерелевантные)" if hidden else "") + "_")
        w("")
        links = data.get("links") or {}
        if links:
            parts = []
            if links.get("report") and not full:
                parts.append(f"[📄 Полный отчёт]({links['report']})")
            if links.get("folder"):
                parts.append(f"[📁 Все файлы отчёта]({links['folder']})")
            if links.get("run"):
                parts.append(f"[📦 Скачать архив (артефакт)]({links['run']})")
            w(" · ".join(parts))
            w("")

        # Overview
        w("## 📊 Обзор")
        w("")
        w("| Категория | Найдено |")
        w("|---|---:|")
        for cat, count in stats["by_category"].items():
            w(f"| {CATEGORIES[cat]} | {count} |")
        if stats.get("downloaded"):
            w(f"| 💾 Скачано файлов | {stats['downloaded']} |")
        w("")

        # Key facts
        cards = [r for r in shown if r.meta.get("game_card") or r.meta.get("summary")]
        if cards:
            w("## 🧾 Главное")
            w("")
            for r in cards[:3]:
                w(self._card_md(r))
                w("")

        ai_text = data.get("ai_text")
        if ai_text:
            w("## 🤖 AI-анализ")
            w("")
            w(link_citations(ai_text, data.get("ai_cited") or []))
            w("")

        terms = data.get("terms") or []
        if terms:
            w("## 🔗 Связанные темы")
            w("")
            w(", ".join(f"`{t}`" for t, _ in terms[: 30 if full else 15]))
            w("")

        # Gallery
        images = [r for r in shown if r.category == "image" and (r.thumbnail or r.meta.get("file_url"))]
        if images:
            w(f"## 🖼 Галерея ({len(images)})")
            w("")
            w(self._gallery_md(images[: GALLERY_SIZE if full else 9], cols=3))
            w("")

        # Per category
        for cat in CATEGORY_ORDER:
            if cat in ("image",) and not full:
                continue
            items = [r for r in shown if r.category == cat]
            if not items:
                continue
            w(f"## {CATEGORIES[cat]} ({len(items)})")
            w("")
            if cat == "image":
                rest = items[GALLERY_SIZE:]
                if not rest:
                    w("_Все изображения — в галерее выше._")
                    w("")
                    continue
                items = rest
            for i, r in enumerate(items[:per_cat], 1):
                w(self._item_md(i, r))
            if len(items) > per_cat:
                w(f"\n_…и ещё {len(items) - per_cat}. Полный список — в `results.json` / `report.html`._")
            w("")

        if full:
            tl = data.get("timeline") or []
            if tl:
                w("## 🕰 Хронология")
                w("")
                for r in tl:
                    w(f"- **{md_escape(_date_label(r.date))}** — [{md_escape(truncate(r.title, 90))}]({r.url})")
                w("")
            self._stats_md(stats, w)

        w("## 🛰 Источники")
        w("")
        w("| Источник | Статус | Найдено | Время | Примечание |")
        w("|---|:-:|---:|---:|---|")
        for st in data["statuses"]:
            w(f"| {st.title} | {STATE_ICON.get(st.state, st.state)} | {st.count} | {st.seconds:.1f}s | "
              f"{md_escape(truncate(st.message, 90))} |")
        w("")
        if data.get("ai_status"):
            w(f"AI-анализ: {data['ai_status']}")
            w("")
        dl = data.get("download")
        if dl and (dl.files or dl.skipped):
            w(f"💾 Скачано файлов: **{len(dl.files)}** ({dl.total_bytes / 1048576:.1f} МБ), пропущено: {len(dl.skipped)}.")
            w("")
        w("---")
        w("_Сделано [SearchMaster](https://github.com/" + os.environ.get("GITHUB_REPOSITORY", "AbyzeR-1337/SearchMaster")
          + "). Уважайте авторские права и лицензии найденных материалов._")
        return "\n".join(out)

    @staticmethod
    def _card_md(r: Result) -> str:
        m = r.meta
        lines = []
        if r.thumbnail:
            lines.append(f'<img src="{html.escape(r.thumbnail)}" width="320" align="right" alt="">')
            lines.append("")
        lines.append(f"### [{md_escape(r.title)}]({r.url})")
        if m.get("description"):
            lines.append(f"_{md_escape(m['description'])}_")
        lines.append("")
        lines.append(truncate(r.snippet, 900))
        facts = []
        for key, label in (("developers", "Разработчик"), ("publishers", "Издатель"), ("release_date", "Дата выхода"),
                           ("genres", "Жанры"), ("platforms", "Платформы"), ("metacritic", "Metacritic"),
                           ("price", "Цена"), ("website", "Сайт")):
            value = m.get(key)
            if value:
                facts.append(f"- **{label}:** {md_escape(', '.join(map(str, value)) if isinstance(value, list) else str(value))}")
        if facts:
            lines.append("")
            lines.extend(facts)
        lines.append('<br clear="right">')
        return "\n".join(lines)

    @staticmethod
    def _gallery_md(images: list[Result], cols: int = 3) -> str:
        cells = []
        for r in images:
            thumb = r.thumbnail or r.meta.get("file_url")
            target = r.meta.get("file_url") or r.url
            cells.append(
                f'<a href="{html.escape(target)}"><img src="{html.escape(thumb)}" width="240" '
                f'alt="{html.escape(truncate(r.title, 60))}"></a><br><sub>{html.escape(truncate(r.title, 50))}</sub>'
            )
        rows = ["<table>"]
        for i in range(0, len(cells), cols):
            rows.append("<tr>" + "".join(f'<td align="center" valign="top">{c}</td>' for c in cells[i : i + cols]) + "</tr>")
        rows.append("</table>")
        return "\n".join(rows)

    @staticmethod
    def _item_md(i: int, r: Result) -> str:
        line = f"{i}. **[{md_escape(truncate(r.title, 110))}]({r.url})**"
        meta = _meta_line(r)
        src = ", ".join(r.sources)
        line += f"  \n   <sub>{md_escape(src)}{' · ' + meta if meta else ''}</sub>"
        text = r.meta.get("passage") or r.snippet
        if text:
            line += f"  \n   {md_escape(truncate(text, 260))}"
        return line

    @staticmethod
    def _stats_md(stats: dict, w) -> None:
        w("## 📈 Статистика")
        w("")
        if stats.get("top_domains"):
            w("**Сайты:** " + ", ".join(f"{d} ({c})" for d, c in stats["top_domains"][:12]))
            w("")
        if stats.get("code_languages"):
            w("**Языки кода:** " + ", ".join(f"{d} ({c})" for d, c in stats["code_languages"]))
            w("")
        if stats.get("file_types"):
            w("**Типы файлов:** " + ", ".join(f"{d} ({c})" for d, c in stats["file_types"]))
            w("")
        if stats.get("licenses"):
            w("**Лицензии:** " + ", ".join(f"{d} ({c})" for d, c in stats["licenses"]))
            w("")
        years = stats.get("years") or {}
        if years:
            peak = max(years.values())
            w("**Материалы по годам:**")
            w("")
            w("```")
            for year, count in years.items():
                w(f"{year} {'█' * max(1, round(20 * count / peak))} {count}")
            w("```")
            w("")

    # ------------------------------------------------------------------ html
    def html(self, data: dict[str, Any]) -> str:
        results: list[Result] = data["results"]
        payload = {
            "query": self.query,
            "mode": self.mode,
            "created": self.created.strftime("%Y-%m-%d %H:%M UTC"),
            "min_score": data["min_score"],
            "categories": CATEGORIES,
            "terms": [t for t, _ in (data.get("terms") or [])],
            "ai": link_citations(data.get("ai_text") or "", data.get("ai_cited") or []),
            "results": [r.to_dict(content_chars=0) for r in results],
            "statuses": [vars(s) for s in data["statuses"]],
        }
        blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        template = (Path(__file__).parent / "templates" / "report.html").read_text(encoding="utf-8")
        return template.replace("__TITLE__", html.escape(self.query)).replace("__DATA__", blob)

    # ------------------------------------------------------------------ write everything
    def write(self, data: dict[str, Any]) -> dict[str, Path]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        paths = {
            "md": self.out_dir / "report.md",
            "summary": self.out_dir / "summary.md",
            "html": self.out_dir / "report.html",
            "json": self.out_dir / "results.json",
            "csv": self.out_dir / "links.csv",
        }
        paths["md"].write_text(self.markdown(data, full=True), encoding="utf-8")
        summary = self.markdown(data, full=False)
        if len(summary) > 60000:  # GitHub comment limit is 65536
            summary = summary[:60000] + "\n\n…_(обрезано, см. полный отчёт)_"
        paths["summary"].write_text(summary, encoding="utf-8")
        paths["html"].write_text(self.html(data), encoding="utf-8")
        dl = data.get("download")
        paths["json"].write_text(
            json.dumps(
                {
                    "query": self.query,
                    "mode": self.mode,
                    "created": self.created.isoformat(),
                    "min_score": data["min_score"],
                    "game": data.get("game"),
                    "aliases": data.get("aliases") or [],
                    "links": data.get("links") or {},
                    "stats": data["stats"],
                    "terms": data.get("terms"),
                    "expanded_queries": data.get("expanded_queries"),
                    "ai_summary": data.get("ai_text"),
                    "ai_summary_linked": link_citations(data.get("ai_text") or "", data.get("ai_cited") or []) or None,
                    "sources": [vars(s) for s in data["statuses"]],
                    "downloads": dl.files if dl else [],
                    "results": [r.to_dict() for r in data["results"]],
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        with open(paths["csv"], "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["score", "category", "title", "url", "sources", "date", "author", "local_file", "snippet"])
            for r in data["results"]:
                writer.writerow([r.score, r.category, r.title, r.url, "|".join(r.sources), r.date or "", r.author or "",
                                 r.local_path or "", truncate(r.snippet, 300)])
        return paths


def statuses_table(statuses: list[SourceStatus]) -> str:
    return "\n".join(
        f"  {STATE_ICON.get(s.state, s.state)} {s.title:<18} {s.count:>4}  {s.message}" for s in statuses
    )

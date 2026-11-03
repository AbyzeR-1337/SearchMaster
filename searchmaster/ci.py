"""GitHub Actions integration.

    python -m searchmaster.ci run                      # search using the triggering event
    python -m searchmaster.ci prepare-site RUN_DIR SITE_DIR

`run` reads the event payload itself ($GITHUB_EVENT_PATH): an issue created from
the "Новый поиск" form, or the inputs of a manual "Run workflow".  Untrusted text
(issue title/body) is therefore never interpolated into shell commands.

Outputs (for later workflow steps): run_dir, dir_name, total, save_report, comment_file.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import __version__
from .config import DOWNLOAD_CHOICES, FACETS, config_from_params
from .models import KIND_LABELS_RU

REPORTS_BRANCH = "searchmaster-reports"

# Labels used by .github/ISSUE_TEMPLATE/searchmaster.yml  ->  internal codes
FORM_FACETS = {
    "общее": "general", "скриншоты": "images", "видео": "video", "код": "code", "файлы": "files",
    "обсуждения": "discussions", "техническое": "technical", "история": "history", "новости": "news",
}
FORM_DOWNLOADS = {
    "картинки": "images", "видео": "video", "аудио": "audio", "документы": "docs", "код": "code",
    "архивы": "archives", "игровые данные": "gamedata", "исходники репозиториев (zip)": "repos",
}
FORM_PRESETS = {"быстро": "fast", "обычно": "normal", "глубоко": "deep"}
_TITLE_PREFIX = re.compile(r"^\s*(search|поиск|найти|searchmaster)\s*[:\-–—]?\s*", re.I)


# ----------------------------------------------------------------------------
# Parsing the trigger
# ----------------------------------------------------------------------------

def one_line(value: Any, limit: int = 200) -> str:
    """Collapse to a single safe line (no control characters, bounded length)."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return " ".join(text.split())[:limit]


def _form_sections(body: str) -> dict[str, str]:
    """Issue forms render as '### Label\\n\\nvalue' blocks."""
    sections: dict[str, str] = {}
    for m in re.finditer(r"^###\s+(.+?)\s*$\n(.*?)(?=^###\s|\Z)", body or "", re.M | re.S):
        value = m.group(2).strip()
        sections[m.group(1).strip().lower()] = "" if value == "_No response_" else value
    return sections


def _checked(block: str) -> list[str]:
    return [m.strip().lower() for m in re.findall(r"^\s*-\s*\[[xX]\]\s*(.+?)\s*$", block or "", re.M)]


def _section(sections: dict[str, str], *starts: str) -> str:
    for key, value in sections.items():
        if any(key.startswith(s) for s in starts):
            return value
    return ""


def params_from_issue(title: str, body: str) -> dict[str, Any]:
    """Issue form (preferred) or a plain issue: title "search: Hollow Knight" + optional 'key: value' lines."""
    sections = _form_sections(body)
    params: dict[str, Any] = {}
    if sections:
        params["query"] = one_line(_section(sections, "что ищем"))
        params["aliases"] = one_line(_section(sections, "другие названия"), 500)
        params["preset"] = FORM_PRESETS.get(one_line(_section(sections, "глубина")).lower(), "normal")
        params["facets"] = [FORM_FACETS[c] for c in _checked(_section(sections, "аспекты")) if c in FORM_FACETS]
        params["download"] = [FORM_DOWNLOADS[c] for c in _checked(_section(sections, "скачать")) if c in FORM_DOWNLOADS]
        params["ai"] = bool(_checked(_section(sections, "ии")))
        langs = one_line(_section(sections, "языки"), 50)
        if langs:
            params["languages"] = langs
    else:  # free-form issue body: "aliases: a, b" / "preset: deep" / "facets: code,images" / "download: images"
        for line in (body or "").splitlines():
            m = re.match(r"^\s*(aliases|alias|синонимы|preset|глубина|facets|аспекты|download|скачать|ai|ии|languages|языки)\s*[:=]\s*(.+)$",
                         line, re.I)
            if not m:
                continue
            key, value = m.group(1).lower(), one_line(m.group(2), 500)
            key = {"alias": "aliases", "синонимы": "aliases", "глубина": "preset", "аспекты": "facets",
                   "скачать": "download", "ии": "ai", "языки": "languages"}.get(key, key)
            if key == "preset":
                value = FORM_PRESETS.get(value.lower(), value.lower())
            params[key] = value
    if not params.get("query"):
        params["query"] = one_line(_TITLE_PREFIX.sub("", title or ""))
    return params


def params_from_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    facets = one_line(inputs.get("facets"), 300)
    download = one_line(inputs.get("download"), 300)
    return {
        "query": one_line(inputs.get("query")),
        "aliases": one_line(inputs.get("aliases"), 500),
        "preset": one_line(inputs.get("preset") or "normal", 20),
        "facets": "" if facets.lower() in ("", "all", "все") else facets,
        "download": "" if download.lower() in ("", "none", "нет") else download,
        "ai": str(inputs.get("ai", "false")).lower() == "true",
        "languages": one_line(inputs.get("languages"), 50),
        "save_report": str(inputs.get("save_report", "true")).lower() != "false",
    }


def load_event() -> tuple[str, dict[str, Any]]:
    name = os.getenv("GITHUB_EVENT_NAME", "")
    path = os.getenv("GITHUB_EVENT_PATH")
    payload: dict[str, Any] = {}
    if path and Path(path).is_file():
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return name, payload


def params_from_event(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if name == "issues" or "issue" in payload:
        issue = payload.get("issue") or {}
        params = params_from_issue(issue.get("title", ""), issue.get("body") or "")
        params["save_report"] = True
        return params
    return params_from_inputs(payload.get("inputs") or {})


# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------

_MENTION = re.compile(r"(?<![\w/`])@(?=[A-Za-z0-9])")
_ISSUE_REF = re.compile(r"(?<![\w/&`])#(?=\d)")


def neutralize(text: str) -> str:
    """Stop text scraped from the web from pinging GitHub users or linking issues."""
    return _ISSUE_REF.sub("#​", _MENTION.sub("@​", text))


def set_outputs(**values: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    lines = [f"{k}={one_line(v, 2000)}" for k, v in values.items()]
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def repo_links() -> dict[str, str | None]:
    """Handy entry points: new search form, Run workflow page, all reports."""
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not repo:
        return {"new_search": None, "run_workflow": None, "all_reports": None}
    return {
        "new_search": f"{server}/{repo}/issues/new?template=searchmaster.yml",
        "run_workflow": f"{server}/{repo}/actions/workflows/search.yml",
        "all_reports": f"{server}/{repo}/tree/{REPORTS_BRANCH}",
    }


def links_for(dir_name: str, payload: dict[str, Any]) -> dict[str, str | None]:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    owner, _, name = repo.partition("/")
    has_pages = bool((payload.get("repository") or {}).get("has_pages"))
    enc = quote(dir_name)
    return {
        "report": f"{server}/{repo}/tree/{REPORTS_BRANCH}/reports/{enc}" if repo else None,
        "pages": f"https://{owner.lower()}.github.io/{name}/reports/{enc}/report.html" if repo and has_pages else None,
        "run": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None,
        **repo_links(),
    }


def _md_link(title: str, url: str) -> str:
    t = neutralize(one_line(title, 90)).replace("[", "(").replace("]", ")").replace("|", "/")
    return f"[{t}]({url.replace(' ', '%20').replace(')', '%29')})"


def comment_markdown(data: dict[str, Any], links: dict[str, str | None], *, seconds: float | None = None,
                     saved: bool = True) -> str:
    """Rich, phone-friendly summary for the issue comment and the run summary."""
    a = data["analysis"]
    results = data["results"]
    out = [f"### SearchMaster: {neutralize(one_line(data['topic']))}", ""]
    head = f"**{a['total']}** релевантных результатов"
    if seconds is not None:
        head += f" за {seconds:.0f} с"
    nav = []
    if saved and links.get("report"):
        nav.append(f"[полный отчёт]({links['report']})")
    if saved and links.get("pages"):
        nav.append(f"[HTML-отчёт]({links['pages']})")
    if links.get("run"):
        nav.append(f"[архив с файлами (Artifacts)]({links['run']})")
    out += [head + (" · " + " · ".join(nav) if nav else ""), ""]

    facts = []
    if a.get("developers"):
        facts.append("**Разработчик:** " + neutralize(", ".join(d for d, _ in a["developers"][:3])))
    if a.get("engines"):
        facts.append("**Движок (упоминания):** " + ", ".join(e for e, _ in a["engines"][:3]))
    if a.get("platforms"):
        facts.append("**Платформы:** " + ", ".join(p for p, _ in a["platforms"][:5]))
    if facts:
        out += [" · ".join(facts), ""]

    out += ["| Что найдено | Кол-во |", "|---|---|"]
    out += [f"| {KIND_LABELS_RU.get(k, k)} | {v} |" for k, v in a["by_kind"].items()]
    out.append("")

    from .report import markdown_gallery
    gallery = markdown_gallery(results, images=12, videos=4, width=160)
    if gallery:
        out += [neutralize(gallery), ""]

    def section(title: str, kinds: tuple[str, ...], n: int, extra=None) -> None:
        items = [r for r in results if r["kind"] in kinds][:n]
        if not items:
            return
        out.append(f"**{title}**")
        for r in items:
            line = "- " + _md_link(r["title"], r["url"])
            bits = [f"`{r.get('domain', '')}`"]
            if extra:
                bits += [b for b in extra(r) if b]
            out.append(line + " — " + " · ".join(bits))
        out.append("")

    section("Карточки игры и вики", ("game", "wiki"), 5)
    section("Исходный код", ("repo", "code"), 6,
            lambda r: [f"★ {r['meta']['stars']}" if r["meta"].get("stars") else None, r["meta"].get("language")])
    section("Файлы и архивы", ("file", "archive"), 6,
            lambda r: [r["meta"].get("size"), r["meta"].get("file_category")])
    section("Видео", ("video",), 4, lambda r: [neutralize(one_line(r.get("author"), 40)) if r.get("author") else None])
    section("Обсуждения", ("discussion",), 4)
    section("Статьи и страницы", ("page", "news", "paper"), 5)

    blocked = [n for n, s in data.get("sources", {}).items() if s.get("status") in ("blocked", "error")]
    if blocked:
        out += [f"<sub>Недоступные источники: {', '.join(blocked)}</sub>", ""]
    if a.get("suggested_queries"):
        out += [f"<sub>Что ещё поискать: {neutralize('; '.join(a['suggested_queries'][:8]))}</sub>", ""]
    footer = [f"SearchMaster {__version__}"]
    if links.get("new_search"):
        footer.append(f"[новый поиск]({links['new_search']})")
    if links.get("all_reports"):
        footer.append(f"[все отчёты]({links['all_reports']})")
    out.append("<sub>" + " · ".join(footer) + "</sub>")
    text = "\n".join(out)
    return text if len(text) < 60000 else text[:60000] + "\n\n…(сокращено)"


def write_step_summary(text: str) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------

def cmd_run() -> int:
    from .engine import Engine
    from .report import write_reports

    name, payload = load_event()
    params = params_from_event(name, payload)
    try:
        cfg = config_from_params(params, out_dir="results", max_downloads_cap=80,
                                 base={"max_file_mb": 15.0, "workers": 8, "cache_dir": None})
    except ValueError as exc:
        msg = (f"Не удалось запустить поиск: {exc}.\n\nСоздайте Issue по форме «Новый поиск» "
               "или назовите его так: `search: Hollow Knight`.")
        comment = Path(os.getenv("RUNNER_TEMP", ".")) / "searchmaster-comment.md"
        comment.write_text(msg, encoding="utf-8")
        set_outputs(run_dir="", dir_name="", total=0, save_report="false", comment_file=str(comment))
        print(msg, file=sys.stderr)
        return 1

    print(f"SearchMaster {__version__}: «{cfg.query}» preset={params.get('preset')} facets={cfg.facets} "
          f"download={cfg.download} ai={cfg.ai}", flush=True)
    t0 = time.monotonic()
    run = Engine(cfg, progress=lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)).run()
    files = write_reports(run)
    seconds = time.monotonic() - t0
    run_dir = run.out_dir
    # README.md makes GitHub render the report when the folder is opened (also in GitHub Mobile).
    if "md" in files:
        shutil.copyfile(files["md"], run_dir / "README.md")

    data = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    save = bool(params.get("save_report", True))
    links = links_for(run_dir.name, payload)
    comment = comment_markdown(data, links, seconds=seconds, saved=save)
    comment_path = Path(os.getenv("RUNNER_TEMP", str(run_dir))) / "searchmaster-comment.md"
    comment_path.write_text(comment, encoding="utf-8")
    write_step_summary(comment)
    set_outputs(run_dir=run_dir.as_posix(), dir_name=run_dir.name, total=data["analysis"]["total"],
                save_report="true" if save else "false", comment_file=str(comment_path))
    print(f"Готово: {data['analysis']['total']} результатов → {run_dir}")
    return 0


INDEX_CSS = """body{margin:0;background:#f6f7f9;color:#1d2330;font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){body{background:#0f1218;color:#e6e9ef}a.r{background:#171b23;border-color:#2a303b}.m{color:#98a2b3}}
main{max-width:640px;margin:0 auto;padding:16px}h1{font-size:22px;display:flex;align-items:center;gap:10px}
h1 img{width:34px;height:34px;border-radius:9px}a.r{display:block;background:#fff;border:1px solid #e2e5ea;border-radius:12px;
padding:12px 14px;margin:10px 0;text-decoration:none;color:inherit}.t{font-weight:600}.m{color:#5d6678;font-size:13px}"""


def build_index(site: Path, keep: int = 150) -> list[dict[str, Any]]:
    """README.md + index.html listing every report (newest first); prunes beyond `keep`."""
    from .web import MANIFEST, app_icon_png

    reports_dir = site / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for res in reports_dir.glob("*/results.json"):
        try:
            d = json.loads(res.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries.append({"dir": res.parent.name, "topic": d.get("topic", res.parent.name),
                        "started": d.get("started", ""), "total": (d.get("analysis") or {}).get("total", 0),
                        "by_kind": (d.get("analysis") or {}).get("by_kind", {})})
    entries.sort(key=lambda e: e["started"], reverse=True)
    for old in entries[keep:]:
        shutil.rmtree(reports_dir / old["dir"], ignore_errors=True)
    entries = entries[:keep]

    md = ["# Отчёты SearchMaster", "", "Каждая папка — один поиск; откройте её, чтобы увидеть отчёт.", ""]
    quick = repo_links()
    if quick["new_search"]:
        md += [f"**[Новый поиск]({quick['new_search']})** · [запустить кнопкой]({quick['run_workflow']})", ""]
    md += ["| Тема | Дата | Найдено |", "|---|---|---|"]
    for e in entries:
        topic = neutralize(one_line(e["topic"], 100)).replace("|", "/").replace("[", "(").replace("]", ")")
        md.append(f"| [{topic}](reports/{quote(e['dir'])}) | {e['started'].replace('T', ' ')} | {e['total']} |")
    (site / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    items = []
    for e in entries:
        kinds = " · ".join(f"{KIND_LABELS_RU.get(k, k)} {v}" for k, v in list(e["by_kind"].items())[:4])
        items.append(f'<a class="r" href="reports/{html.escape(quote(e["dir"]), quote=True)}/report.html">'
                     f'<div class="t">{html.escape(e["topic"])}</div><div class="m">{html.escape(e["started"].replace("T", " "))}'
                     f' · найдено {e["total"]}</div><div class="m">{html.escape(kinds)}</div></a>')
    manifest = dict(MANIFEST, start_url="./", scope="./",
                    icons=[{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
                           {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"}])
    (site / "manifest.webmanifest").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (site / "icon-192.png").write_bytes(app_icon_png(192))
    (site / "icon-512.png").write_bytes(app_icon_png(512))
    (site / "index.html").write_text(
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Отчёты SearchMaster</title>'
        '<link rel="manifest" href="manifest.webmanifest"><link rel="icon" href="icon-192.png">'
        '<link rel="apple-touch-icon" href="icon-192.png"><meta name="theme-color" content="#2f6fde">'
        f'<style>{INDEX_CSS}</style></head><body><main><h1><img src="icon-192.png" alt="">Отчёты SearchMaster</h1>'
        + ("".join(items) or '<p class="m">Отчётов пока нет.</p>')
        + f'<p class="m">Обновлено {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC</p></main></body></html>',
        encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return entries


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def cmd_prepare_site(run_dir: str, site_dir: str, max_download_mb: float = 20.0) -> int:
    """Copy one run into the reports branch checkout and rebuild the index."""
    src, site = Path(run_dir), Path(site_dir)
    if not (src / "results.json").is_file():
        print(f"нет results.json в {src}", file=sys.stderr)
        return 1
    dest = site / "reports" / src.name
    if dest.exists():
        shutil.rmtree(dest)
    downloads = src / "downloads"
    skip_downloads = downloads.is_dir() and dir_size(downloads) > max_download_mb * 1024 * 1024
    shutil.copytree(src, dest, ignore=(shutil.ignore_patterns("downloads") if skip_downloads else None))
    if skip_downloads:
        (dest / "DOWNLOADS.md").write_text(
            "Скачанные файлы больше лимита для ветки отчётов — они лежат в архиве (Artifacts) этого запуска.\n",
            encoding="utf-8")
    build_index(site)
    print(f"Отчёт добавлен: reports/{src.name}" + (" (без downloads/ — слишком большие)" if skip_downloads else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"]:
        return cmd_run()
    if argv[:1] == ["prepare-site"] and len(argv) == 3:
        return cmd_prepare_site(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 2


__all__ = ["DOWNLOAD_CHOICES", "FACETS", "params_from_issue", "params_from_inputs", "comment_markdown",
           "neutralize", "build_index", "main"]

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

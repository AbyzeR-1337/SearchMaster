"""Helpers for GitHub Actions.

``python -m searchmaster.gh request``  — turn the triggering event (manual run, issue,
                                          ``/search`` comment) into step outputs.
``python -m searchmaster.gh index DIR`` — rebuild DIR/README.md listing every search in DIR/results/.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import MODES
from .textutil import slugify

TITLE_PREFIXES = re.compile(r"^\s*(?:🔍|🔎|\[?search\]?:?|\[?поиск\]?:?|найти:?)\s*", re.IGNORECASE)
COMMAND_RE = re.compile(r"^\s*/(?:search|поиск|найти)\b(.*)$", re.IGNORECASE | re.DOTALL)
NO_RESPONSE = {"", "_no response_", "none", "-"}


def parse_issue_form(body: str) -> dict[str, str]:
    """Parse the Markdown produced by a GitHub issue form (### Label\\n\\nvalue)."""
    fields: dict[str, str] = {}
    for match in re.finditer(r"^###\s+(.+?)\s*\n(.*?)(?=^###\s|\Z)", body or "", re.MULTILINE | re.DOTALL):
        label = match.group(1).strip().lower()
        value = match.group(2).strip()
        fields[label] = "" if value.lower() in NO_RESPONSE else value
    return fields


def _flags(text: str) -> tuple[str, dict[str, str]]:
    """Extract --mode/--deep/--quick/--sources=… style flags from free text."""
    opts: dict[str, str] = {}
    for mode in MODES:
        if re.search(rf"(?:^|\s)--?{mode}\b", text):
            opts["mode"] = mode
            text = re.sub(rf"(?:^|\s)--?{mode}\b", " ", text)
    for key in ("mode", "sources", "langs", "code-zips"):
        m = re.search(rf"--{key}[= ](\S+)", text)
        if m:
            opts[key.replace("-", "_")] = m.group(1)
            text = text.replace(m.group(0), " ")
    if re.search(r"--no-download\b", text):
        opts["download"] = "false"
        text = re.sub(r"--no-download\b", " ", text)
    return " ".join(text.split()), opts


def request_from_env(env: dict[str, str]) -> dict[str, str]:
    event = env.get("EVENT_NAME", "workflow_dispatch")
    req = {"query": "", "mode": "normal", "sources": "all", "langs": "ru,en", "download": "true", "code_zips": "0"}

    if event == "workflow_dispatch":
        for key in req:
            value = env.get(f"INPUT_{key.upper()}", "").strip()
            if value:
                req[key] = value
    elif event == "issue_comment":
        m = COMMAND_RE.match(env.get("COMMENT_BODY", ""))
        text = m.group(1).strip().splitlines()[0] if m and m.group(1).strip() else ""
        query, opts = _flags(text)
        req.update(opts)
        req["query"] = query or _issue_query(env)[0]
        if not query:
            req.update({k: v for k, v in _issue_query(env)[1].items() if k not in opts})
    else:  # issues
        query, opts = _issue_query(env)
        req["query"] = query
        req.update(opts)

    req["query"] = " ".join(req["query"].split())[:200]
    if req["mode"] not in MODES:
        req["mode"] = "normal"
    if not re.fullmatch(r"[\w,\-]+", req["sources"]):
        req["sources"] = "all"
    if not re.fullmatch(r"[a-z,\-]+", req["langs"]):
        req["langs"] = "ru,en"
    if not req["code_zips"].isdigit():
        req["code_zips"] = "0"
    req["code_zips"] = str(min(int(req["code_zips"]), 10))
    req["download"] = "true" if req["download"].lower() in ("true", "1", "yes", "да") else "false"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    req["slug"] = slugify(req["query"])
    req["out_dir"] = f"results/{stamp}_{req['slug']}"
    return req


def _issue_query(env: dict[str, str]) -> tuple[str, dict[str, str]]:
    body = env.get("ISSUE_BODY", "") or ""
    form = parse_issue_form(body)
    opts: dict[str, str] = {}
    query = ""
    for label, value in form.items():
        if label.startswith(("запрос", "query", "что искать")):
            query = value.splitlines()[0] if value else ""
        elif label.startswith(("режим", "mode")) and value:
            opts["mode"] = value.split()[0].lower()
        elif label.startswith(("источник", "sources")) and value:
            opts["sources"] = value.split()[0]
        elif label.startswith(("язык", "lang")) and value:
            opts["langs"] = value.split()[0]
        elif label.startswith(("исходники", "zip", "code")) and value:
            opts["code_zips"] = value.split()[0]
        elif label.startswith(("опции", "options")):
            if re.search(r"-\s*\[[xX]\]\s*не\s+скачивать", value, re.IGNORECASE):
                opts["download"] = "false"
    if not query:
        title = TITLE_PREFIXES.sub("", env.get("ISSUE_TITLE", "")).strip()
        query, flag_opts = _flags(title)
        opts.update(flag_opts)
    return query, opts


def write_outputs(req: dict[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for key, value in req.items():
        delim = f"EOF_{uuid.uuid4().hex}"
        lines.append(f"{key}<<{delim}\n{value}\n{delim}\n")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("".join(lines))
    print(json.dumps(req, ensure_ascii=False, indent=1))


def build_index(root: Path) -> str:
    """Index of every search stored under ``<root>/results/*/``."""
    rows = []
    for meta_file in sorted(root.glob("results/*/results.json"), reverse=True):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        folder = meta_file.parent.name
        stats = data.get("stats", {})
        rows.append(
            f"| {data.get('created', '')[:16].replace('T', ' ')} | [{data.get('query', folder)}](results/{folder}/report.md) | "
            f"{data.get('mode', '')} | {stats.get('total', 0)} | {stats.get('downloaded', 0)} |"
        )
    head = [
        "# 🔍 SearchMaster — результаты поиска",
        "",
        "Каждая строка — отдельный поиск. Откройте `report.md`, чтобы увидеть отчёт прямо на GitHub (и с телефона).",
        "",
        "| Дата (UTC) | Запрос | Режим | Найдено | Скачано |",
        "|---|---|---|---:|---:|",
    ]
    return "\n".join(head + rows) + "\n"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "request":
        req = request_from_env(dict(os.environ))
        write_outputs(req)
        if not req["query"]:
            print("::error::Не удалось понять, что искать. Укажите запрос.")
            return 1
        return 0
    if argv[0] == "index":
        root = Path(argv[1] if len(argv) > 1 else ".")
        (root / "README.md").write_text(build_index(root), encoding="utf-8")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Download found screenshots, documents, code archives etc. into the results folder."""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .config import Config
from .models import FILE, IMAGE, REPO, VIDEO, Result
from .net import HttpClient
from .utils import file_category, file_extension, slugify, sniff_extension

log = logging.getLogger("searchmaster.download")

# CLI names -> internal file categories
CHOICES = {
    "images": {"image"},
    "video": {"video"},
    "audio": {"audio"},
    "docs": {"document"},
    "code": {"code"},
    "archives": {"archive"},
    "gamedata": {"game-data"},
    "repos": {"repo"},
}
SAFE_ALL = set().union(*CHOICES.values())  # "all" never includes executables


def wanted_categories(choices: list[str]) -> set[str]:
    cats: set[str] = set()
    for c in choices:
        c = c.strip().lower()
        if c == "all":
            cats |= SAFE_ALL
        elif c in CHOICES:
            cats |= CHOICES[c]
    return cats


def _category(r: Result) -> str | None:
    if r.kind == REPO and "github.com" in r.url:
        return "repo"
    if r.kind == IMAGE:
        return "image"
    if r.kind in (FILE, VIDEO):
        return r.meta.get("file_category") or file_category(r.url)
    return None


def _target_url(r: Result) -> str:
    if r.kind == REPO:
        repo = r.meta.get("repo") or "/".join(r.url.rstrip("/").split("/")[3:5])
        repo = repo.removeprefix("github.com/")
        return f"https://codeload.github.com/{repo}/zip/HEAD"
    return r.url


def download_results(results: list[Result], config: Config, http: HttpClient, out_dir: Path,
                     progress: Callable[[str], None] | None = None) -> list[dict]:
    cats = wanted_categories(config.download)
    if not cats:
        return []
    progress = progress or (lambda m: None)
    picked: list[tuple[Result, str]] = []
    for r in results:
        cat = _category(r)
        if cat in cats:
            picked.append((r, cat))
        if len(picked) >= config.max_downloads:
            break
    max_bytes = int(config.max_file_mb * 1024 * 1024)
    manifest: list[dict] = []
    seen_hashes: set[str] = set()

    def job(idx: int, r: Result, cat: str) -> dict:
        url = _target_url(r)
        ext = "zip" if cat == "repo" else (file_extension(url) or r.meta.get("ext") or "bin")
        name = f"{idx:04d}_{slugify(r.title, 50)}.{ext}"
        path = out_dir / cat / name
        size, ctype = http.stream_to_file(url, path, max_bytes=max_bytes, check_robots=config.respect_robots)
        with open(path, "rb") as fh:
            sniffed = sniff_extension(fh.read(16))
        real = sniffed or (mimetypes.guess_extension(ctype.split(";")[0].strip()) or "").lstrip(".") or ext
        if real == "jpeg":
            real = "jpg"
        if real != ext and (ext == "bin" or sniffed):
            new_path = path.with_suffix("." + real)
            path.replace(new_path)
            path = new_path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"url": url, "path": str(path.relative_to(out_dir.parent)), "size": size, "sha256": digest,
                "category": cat, "title": r.title, "page_url": r.meta.get("page_url"), "source": r.source}

    with ThreadPoolExecutor(max_workers=min(6, config.workers)) as pool:
        futs = {pool.submit(job, i, r, c): r for i, (r, c) in enumerate(picked, 1)}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                item = fut.result()
            except Exception as exc:
                log.debug("download failed %s: %s", r.url, exc)
                manifest.append({"url": r.url, "error": str(exc), "title": r.title})
                continue
            if item["sha256"] in seen_hashes:  # identical file from another URL
                (out_dir.parent / item["path"]).unlink(missing_ok=True)
                continue
            seen_hashes.add(item["sha256"])
            r.meta["local_path"] = item["path"]
            manifest.append(item)
            progress(f"скачано: {item['path']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest

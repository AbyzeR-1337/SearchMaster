"""Download screenshots, images, documents and (optionally) source-code archives."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .classify import ARCHIVE_EXT, AUDIO_EXT, DANGEROUS_EXT, DOC_EXT, IMAGE_EXT, url_ext
from .config import Context
from .models import Result
from .textutil import slugify

# What is fetched automatically from archive.org, per media type. Software, video and
# audio items are only listed as links: they are large and are often copyrighted works.
IA_FORMATS = {
    "texts": (".pdf", ".txt", ".epub", ".djvu", ".md"),
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp"),
    "data": (".csv", ".json", ".txt", ".pdf", ".md"),
}


@dataclass
class DownloadReport:
    files: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    total_bytes: int = 0


class Downloader:
    def __init__(self, ctx: Context, out_dir: Path) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self.out_dir = out_dir
        self.report = DownloadReport()
        self._lock = threading.Lock()
        self._names: set[str] = set()
        self.max_file = int(self.cfg.max_file_mb * 1024 * 1024)
        self.max_total = int(self.cfg.max_total_mb * 1024 * 1024)

    # ------------------------------------------------------------------ helpers
    def _allowed_ext(self, ext: str) -> bool:
        if ext in self.cfg.allow_ext:
            return True
        return ext not in DANGEROUS_EXT

    def _reserve_name(self, folder: str, title: str, ext: str) -> Path:
        base = slugify(title, 50)
        with self._lock:
            name = f"{base}{ext}"
            n = 2
            while f"{folder}/{name}" in self._names:
                name = f"{base}-{n}{ext}"
                n += 1
            self._names.add(f"{folder}/{name}")
        path = self.out_dir / "files" / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _fetch(self, url: str, folder: str, title: str, expect_image: bool = False, max_bytes: int | None = None) -> Path | None:
        limit = min(max_bytes or self.max_file, self.max_file)
        with self._lock:
            if self.report.total_bytes >= self.max_total:
                self.report.skipped.append(f"{url} (общий лимит)")
                return None
        try:
            resp = self.ctx.http.get(url, stream=True, timeout=30)
        except Exception as exc:
            self.report.skipped.append(f"{url} ({exc})")
            return None
        with resp:
            if resp.status_code != 200:
                self.report.skipped.append(f"{url} (HTTP {resp.status_code})")
                return None
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if "html" in ctype:
                self.report.skipped.append(f"{url} (это страница, а не файл)")
                return None
            if expect_image and not ctype.startswith("image/"):
                self.report.skipped.append(f"{url} (не изображение: {ctype})")
                return None
            length = int(resp.headers.get("Content-Length") or 0)
            if length and length > limit:
                self.report.skipped.append(f"{url} (слишком большой: {length // 1048576} МБ)")
                return None
            ext = url_ext(url)
            if not ext or len(ext) > 6:
                ext = mimetypes.guess_extension(ctype) or ".bin"
            if not self._allowed_ext(ext):
                self.report.skipped.append(f"{url} (тип {ext} не скачивается из соображений безопасности)")
                return None
            path = self._reserve_name(folder, title, ext)
            sha = hashlib.sha256()
            size = 0
            try:
                with open(path, "wb") as fh:
                    for chunk in resp.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("превышен лимит размера файла")
                        sha.update(chunk)
                        fh.write(chunk)
            except Exception as exc:
                path.unlink(missing_ok=True)
                self.report.skipped.append(f"{url} ({exc})")
                return None
        with self._lock:
            self.report.total_bytes += size
            self.report.files.append(
                {"url": url, "path": str(path.relative_to(self.out_dir)), "bytes": size, "sha256": sha.hexdigest()}
            )
        return path

    # ------------------------------------------------------------------ kinds
    def _image(self, r: Result) -> None:
        url = r.meta.get("file_url") or (r.url if url_ext(r.url) in IMAGE_EXT else None) or r.thumbnail
        if not url:
            return
        path = self._fetch(url, "images", r.title, expect_image=True)
        if path:
            r.local_path = str(path.relative_to(self.out_dir))

    def _file(self, r: Result) -> None:
        ident = r.meta.get("ia_identifier")
        if ident:
            self._internet_archive(r, ident)
            return
        url = r.meta.get("file_url") or r.meta.get("pdf") or r.url
        ext = url_ext(url)
        if ext in DOC_EXT | ARCHIVE_EXT | AUDIO_EXT or r.meta.get("pdf"):
            path = self._fetch(url, "documents", r.title)
            if path:
                r.local_path = str(path.relative_to(self.out_dir))

    def _internet_archive(self, r: Result, ident: str) -> None:
        formats = IA_FORMATS.get(r.meta.get("mediatype", ""))
        if not formats:
            return
        try:
            meta = self.ctx.http.get_json(f"https://archive.org/metadata/{ident}")
        except Exception as exc:
            self.report.skipped.append(f"archive.org/{ident} ({exc})")
            return
        files = [
            f for f in meta.get("files", [])
            if f.get("source") == "original"
            and str(f.get("name", "")).lower().endswith(formats)
            and 0 < int(f.get("size") or 0) <= self.max_file
        ]
        files.sort(key=lambda f: int(f.get("size") or 0))
        saved = []
        for f in files[:3]:
            name = f["name"]
            url = f"https://archive.org/download/{ident}/{name}"
            path = self._fetch(url, f"archive/{slugify(ident, 40)}", os.path.splitext(name)[0])
            if path:
                saved.append(str(path.relative_to(self.out_dir)))
        if saved:
            r.local_path = saved[0]
            r.meta["downloaded_files"] = saved

    def _repo_zip(self, r: Result) -> None:
        repo, branch = r.meta.get("repo"), r.meta.get("branch")
        if not repo or not branch or r.meta.get("kind") == "file":
            return
        if (r.meta.get("size_kb") or 0) * 1024 > self.max_file * 4:  # zip is usually much smaller than repo size
            self.report.skipped.append(f"{repo} (репозиторий слишком большой)")
            return
        url = f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}"
        path = self._fetch(url, "code", repo.replace("/", "__"))
        if path:
            r.local_path = str(path.relative_to(self.out_dir))

    # ------------------------------------------------------------------ public
    def run(self, results: list[Result]) -> DownloadReport:
        cfg = self.cfg
        relevant = [r for r in results if r.score >= cfg.min_score and not r.meta.get("no_download")]
        images = [r for r in relevant if r.category == "image"][: cfg.download_images]
        files = [r for r in relevant if r.category in ("file", "paper", "audio")
                 and (r.meta.get("ia_identifier") or r.meta.get("file_url") or r.meta.get("pdf")
                      or url_ext(r.url) in DOC_EXT | ARCHIVE_EXT)][: cfg.download_files]
        repos = [r for r in relevant if r.category == "code" and "github" in r.sources and r.meta.get("branch")]
        repos = sorted(repos, key=lambda r: r.meta.get("stars", 0), reverse=True)[: cfg.code_zips]

        jobs = [(self._image, r) for r in images] + [(self._file, r) for r in files] + [(self._repo_zip, r) for r in repos]
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            list(pool.map(lambda job: job[0](job[1]), jobs))
        return self.report

"""Mobile-friendly web interface:  python -m searchmaster --web

Standard library only (http.server), so it runs anywhere Python runs — including
Android phones via Termux.  Open the printed address in the phone's browser and
"Add to Home screen" to get an app-like icon.
"""

from __future__ import annotations

import dataclasses
import json
import mimetypes
import os
import re
import secrets
import socket
import struct
import threading
import time
import uuid
import zlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .config import FACETS, Config
from .downloader import CHOICES
from .models import KIND_LABELS_RU

PRESETS: dict[str, dict[str, Any]] = {
    "fast": {"web_queries_per_engine": 6, "deep": 8, "crawl_depth": 0, "max_per_source": 15, "rounds": 1},
    "normal": {"web_queries_per_engine": 14, "deep": 25, "crawl_depth": 0, "max_per_source": 30, "rounds": 2},
    "deep": {"web_queries_per_engine": 22, "deep": 50, "crawl_depth": 1, "max_per_source": 40, "rounds": 2},
}
_LANG_RE = re.compile(r"^[a-z]{2,3}$")

# (message prefix, stage label, progress %)
_STAGES = [
    ("ИИ: подбираю", "ИИ подбирает синонимы", 3),
    ("Раунд 1", "Раунд 1: опрос источников", 8),
    ("Раунд 2", "Раунд 2: «снежный ком»", 45),
    ("Wayback", "Раунд 2: архивы сайтов", 48),
    ("Глубокий анализ", "Разбор лучших страниц", 60),
    ("Найдено уникальных", "Анализ и фильтр шума", 85),
    ("ИИ: пишу", "ИИ пишет сводку", 88),
    ("Скачиваю", "Скачивание файлов", 92),
]


# ----------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------

@dataclasses.dataclass
class Job:
    id: str
    query: str
    params: dict[str, Any]
    created: float = dataclasses.field(default_factory=time.time)
    state: str = "running"            # running | done | error | cancelled
    stage: str = "Подготовка"
    progress: float = 1.0
    log: list[str] = dataclasses.field(default_factory=list)
    summary: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None
    finished: float | None = None
    engine: Any = None
    _sources_total: int = 0
    _sources_done: int = 0

    def add_log(self, msg: str) -> None:
        self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
        if len(self.log) > 400:
            del self.log[:100]
        text = msg.strip()
        for prefix, label, pct in _STAGES:
            if text.startswith(prefix):
                self.stage, self.progress = label, max(self.progress, pct)
                m = re.search(r"Раунд 1: (\d+) источник", text)
                if m:
                    self._sources_total = int(m.group(1))
                return
        if "раунд 1" in text and self._sources_total and self.progress < 45:
            self._sources_done += 1
            self.progress = max(self.progress, 8 + 34 * self._sources_done / self._sources_total)

    def to_dict(self, *, full_log: bool = False) -> dict[str, Any]:
        return {
            "id": self.id, "query": self.query, "state": self.state, "stage": self.stage,
            "progress": round(self.progress, 1), "log": self.log if full_log else self.log[-60:],
            "summary": self.summary, "error": self.error, "created": self.created, "finished": self.finished,
            "elapsed": round((self.finished or time.time()) - self.created),
        }


class WebApp:
    def __init__(self, out_dir: str | os.PathLike = "results", *, token: str | None = None,
                 base_config: dict[str, Any] | None = None, max_jobs: int = 2,
                 engine_factory: Callable[..., Any] | None = None):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.base_config = base_config or {}
        self.max_jobs = max_jobs
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        if engine_factory is None:
            from .engine import Engine
            engine_factory = Engine
        self.engine_factory = engine_factory

    # ---------------------------------------------------------------- jobs
    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.state == "running"]

    def build_config(self, params: dict[str, Any]) -> Config:
        query = str(params.get("query", "")).strip()
        if not query or len(query) > 200:
            raise ValueError("Введите запрос (до 200 символов)")
        aliases = params.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [a for a in re.split(r"[,;\n]", aliases)]
        aliases = [str(a).strip()[:100] for a in aliases if str(a).strip()][:5]
        preset = PRESETS.get(str(params.get("preset", "normal")), PRESETS["normal"])
        facets = [f for f in params.get("facets") or [] if f in FACETS] or list(FACETS)
        langs = [lang for lang in params.get("languages") or [] if isinstance(lang, str) and _LANG_RE.match(lang)][:5]
        download = [d for d in params.get("download") or [] if d in CHOICES or d == "all"]
        cfg = dict(self.base_config)
        cfg.update(preset)
        return Config(query=query, aliases=aliases, facets=facets, languages=langs or ["en", "ru"],
                      download=download, max_downloads=min(int(params.get("max_downloads") or 60), 300),
                      ai=bool(params.get("ai")), out_dir=str(self.out_dir), **cfg)

    def start(self, params: dict[str, Any]) -> Job:
        cfg = self.build_config(params)
        with self.lock:
            if len(self.running()) >= self.max_jobs:
                raise RuntimeError("Уже идёт поиск — дождитесь окончания или остановите его")
            job = Job(id=uuid.uuid4().hex[:12], query=cfg.query, params=params)
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, cfg), daemon=True, name=f"job-{job.id}").start()
        return job

    def _run(self, job: Job, cfg: Config) -> None:
        from .report import write_reports
        try:
            engine = self.engine_factory(cfg, progress=job.add_log)
            job.engine = engine
            run = engine.run()
            job.stage, job.progress = "Сохраняю отчёт", 97
            files = write_reports(run)
            a = run.analysis
            report = files.get("html") or next(iter(files.values()), None)
            job.summary = {
                "total": a["total"],
                "by_kind": [[KIND_LABELS_RU.get(k, k), v] for k, v in a["by_kind"].items()],
                "engines": [e for e, _ in a.get("engines", [])[:4]],
                "developers": [d for d, _ in a.get("developers", [])[:3]],
                "blocked": [n for n, s in run.source_status.items() if s.get("status") in ("blocked", "error")],
                "report": self.rel(report) if report else None,
                "files": {fmt: self.rel(p) for fmt, p in files.items()},
                "downloads": sum(1 for d in run.downloads if "path" in d),
                "suggested": a.get("suggested_queries", [])[:8],
            }
            job.state = "cancelled" if getattr(engine, "cancelled", False) else "done"
            job.stage = "Остановлено — отчёт по найденному" if job.state == "cancelled" else "Готово"
            job.progress = 100
        except Exception as exc:  # surface any failure to the phone instead of dying silently
            job.state, job.error = "error", f"{type(exc).__name__}: {exc}"
            job.stage = "Ошибка"
            job.add_log(f"Ошибка: {exc}")
        finally:
            job.finished = time.time()
            job.engine = None

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.state != "running" or job.engine is None:
            return False
        job.engine.cancel()
        job.add_log("Остановка… сохраняю то, что уже найдено")
        return True

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.out_dir.resolve()).as_posix()

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        items = []
        for res in sorted(self.out_dir.glob("*/results.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                with open(res, encoding="utf-8") as fh:
                    head = json.load(fh)
            except (OSError, ValueError):
                continue
            a = head.get("analysis", {})
            report = res.parent / "report.html"
            items.append({
                "topic": head.get("topic"), "started": head.get("started"), "total": a.get("total"),
                "by_kind": a.get("by_kind", {}), "dir": res.parent.name,
                "report": self.rel(report) if report.exists() else self.rel(res),
            })
        return items

    def info(self) -> dict[str, Any]:
        import importlib.util
        ai_ok = bool(importlib.util.find_spec("anthropic")) and bool(
            os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        return {"version": __version__, "facets": FACETS, "downloads": list(CHOICES), "ai_available": ai_ok,
                "presets": list(PRESETS), "kind_labels": KIND_LABELS_RU}

    # ---------------------------------------------------------------- http
    def make_server(self, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
        app = self

        class Handler(_Handler):
            pass

        Handler.app = app
        server = ThreadingHTTPServer((host, port), Handler)
        server.daemon_threads = True
        return server


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    app: WebApp
    server_version = f"SearchMaster/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console clean
        pass

    # helpers ---------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8")

    def _authorized(self, query: dict[str, list[str]]) -> tuple[bool, bool]:
        """(allowed, came_with_key_in_url)."""
        token = self.app.token
        if not token:
            return True, False
        if secrets.compare_digest(query.get("key", [""])[0].encode(), token.encode()):
            return True, True
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            val = cookie["sm_key"].value if "sm_key" in cookie else ""
        except Exception:
            val = ""
        return secrets.compare_digest(val.encode(), token.encode()), False

    # routes ----------------------------------------------------------------
    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if path == "/manifest.webmanifest":
            return self._send(200, json.dumps(MANIFEST, ensure_ascii=False).encode(), "application/manifest+json")
        if path in ("/icon-192.png", "/icon-512.png", "/apple-touch-icon.png", "/favicon.ico"):
            size = 512 if "512" in path else 180 if "apple" in path else 192
            return self._send(200, app_icon_png(size), "image/png", {"Cache-Control": "max-age=86400"})
        ok, via_url = self._authorized(query)
        if not ok:
            if path.startswith("/api/"):
                return self._json({"error": "нужен ключ доступа"}, 401)
            return self._send(401, NEED_KEY_HTML.encode(), "text/html; charset=utf-8")
        if via_url:  # remember the key in a cookie and drop it from the address bar
            return self._send(303, b"", "text/plain", {
                "Location": path or "/",
                "Set-Cookie": f"sm_key={self.app.token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000",
            })
        if path == "/":
            return self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8",
                              {"Referrer-Policy": "no-referrer"})
        if path == "/api/info":
            return self._json(self.app.info())
        if path == "/api/history":
            return self._json(self.app.history())
        if path == "/api/jobs":
            jobs = sorted(self.app.jobs.values(), key=lambda j: -j.created)[:20]
            return self._json([j.to_dict() for j in jobs])
        m = re.fullmatch(r"/api/job/([0-9a-f]{12})", path)
        if m:
            job = self.app.jobs.get(m.group(1))
            return self._json(job.to_dict(full_log="full" in query) if job else {"error": "нет такой задачи"},
                              200 if job else 404)
        if path.startswith("/files/"):
            return self._file(unquote(path[len("/files/"):]))
        self._json({"error": "не найдено"}, 404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        ok, _ = self._authorized(parse_qs(url.query))
        if not ok:
            return self._json({"error": "нужен ключ доступа"}, 401)
        # JSON-only API: browsers cannot send this cross-site without a CORS preflight,
        # which we never approve — protects against drive-by requests from other sites.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json({"error": "ожидается application/json"}, 415)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 20_000:
            return self._json({"error": "слишком большой запрос"}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "некорректный JSON"}, 400)
        if url.path == "/api/search":
            try:
                job = self.app.start(body if isinstance(body, dict) else {})
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            except RuntimeError as exc:
                return self._json({"error": str(exc)}, 409)
            return self._json(job.to_dict(), 201)
        m = re.fullmatch(r"/api/job/([0-9a-f]{12})/cancel", url.path)
        if m:
            return self._json({"ok": self.app.cancel(m.group(1))})
        self._json({"error": "не найдено"}, 404)

    def _file(self, rel: str) -> None:
        root = self.app.out_dir.resolve()
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            return self._json({"error": "файл не найден"}, 404)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        headers = {"Cache-Control": "max-age=300"}
        if target.suffix.lower() in (".svg", ".xml", ".xhtml"):
            # Downloaded SVG/XML from the internet must not run scripts on our origin.
            headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        self._send(200, target.read_bytes(), ctype, headers)


# ----------------------------------------------------------------------------
# Serving
# ----------------------------------------------------------------------------

def lan_ip() -> str | None:
    """Best-effort local network address (no packets are actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


def open_browser(url: str) -> None:
    import shutil
    import subprocess
    import webbrowser
    if shutil.which("termux-open-url"):  # Android / Termux
        subprocess.Popen(["termux-open-url", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def print_qr(url: str) -> None:
    try:
        import qrcode  # optional: pip install qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


def serve(out_dir: str, host: str, port: int, *, token: str | None, base_config: dict[str, Any],
          browser: bool = True) -> None:
    app = WebApp(out_dir, token=token, base_config=base_config)
    server = app.make_server(host, port)
    port = server.server_address[1]
    local = f"http://127.0.0.1:{port}/"
    print(f"SearchMaster {__version__} — веб-интерфейс запущен")
    print(f"  На этом устройстве:  {local}" + (f"?key={token}" if token else ""))
    if host not in ("127.0.0.1", "localhost", "::1"):
        ip = lan_ip()
        if ip:
            lan = f"http://{ip}:{port}/" + (f"?key={token}" if token else "")
            print(f"  С телефона (та же Wi-Fi сеть):  {lan}")
            print_qr(lan)
        print("  Ключ доступа нужен, чтобы чужие устройства в сети не пользовались вашим поиском.")
    print("Остановить: Ctrl+C")
    if browser:
        open_browser(local + (f"?key={token}" if token else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
    finally:
        server.server_close()


# ----------------------------------------------------------------------------
# App icon (generated, no binary files in the repo)
# ----------------------------------------------------------------------------

_ICON_CACHE: dict[int, bytes] = {}


def app_icon_png(size: int) -> bytes:
    """A magnifying glass on a blue rounded square, rendered with plain Python."""
    if size in _ICON_CACHE:
        return _ICON_CACHE[size]
    bg, fg = (47, 111, 222), (255, 255, 255)
    cx = cy = size * 0.43
    r_out, r_in = size * 0.24, size * 0.17
    hx0 = hy0 = cx + r_out * 0.70
    hx1 = hy1 = size * 0.80
    half_w = size * 0.055
    radius = size * 0.22
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            # rounded-square mask
            dx = max(radius - x, 0, x - (size - 1 - radius))
            dy = max(radius - y, 0, y - (size - 1 - radius))
            if dx * dx + dy * dy > radius * radius:
                row += bytes((0, 0, 0, 0))
                continue
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            ring = r_in <= d <= r_out
            # distance to the handle segment
            vx, vy = hx1 - hx0, hy1 - hy0
            t = max(0.0, min(1.0, ((x - hx0) * vx + (y - hy0) * vy) / (vx * vx + vy * vy)))
            px, py = hx0 + t * vx, hy0 + t * vy
            handle = ((x - px) ** 2 + (y - py) ** 2) ** 0.5 <= half_w
            row += bytes((*(fg if ring or handle else bg), 255))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    _ICON_CACHE[size] = png
    return png


MANIFEST = {
    "name": "SearchMaster", "short_name": "SearchMaster", "lang": "ru",
    "description": "Ищет по всему интернету всё по одной теме или игре",
    "start_url": "/", "scope": "/", "display": "standalone",
    "background_color": "#f6f7f9", "theme_color": "#2f6fde",
    "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}

NEED_KEY_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SearchMaster</title><body style="font:16px system-ui;padding:24px;max-width:520px;margin:auto">
<h2>Нужен ключ доступа</h2><p>Откройте ссылку с <code>?key=…</code>, которую SearchMaster напечатал в консоли
при запуске (или отсканируйте QR-код).</p></body>"""

INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#2f6fde">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>SearchMaster</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--text:#1d2330;--muted:#5d6678;--line:#e2e5ea;--accent:#2f6fde;--accent-soft:#e7efff;
--ok:#1f8a4c;--bad:#c0392b;--code:#f1f3f6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1218;--panel:#171b23;--text:#e6e9ef;--muted:#98a2b3;
--line:#2a303b;--accent:#6ea0ff;--accent-soft:#1d2a44;--ok:#4cc38a;--bad:#ff7b6b;--code:#11151c}}
:root[data-theme="dark"]{--bg:#0f1218;--panel:#171b23;--text:#e6e9ef;--muted:#98a2b3;--line:#2a303b;--accent:#6ea0ff;
--accent-soft:#1d2a44;--ok:#4cc38a;--bad:#ff7b6b;--code:#11151c}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
main{max-width:640px;margin:0 auto;padding:16px 16px calc(84px + env(safe-area-inset-bottom))}
header{display:flex;align-items:center;gap:10px;margin:4px 0 14px}
header img{width:34px;height:34px;border-radius:9px}
header h1{font-size:21px;margin:0}header .v{color:var(--muted);font-size:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
label.t{display:block;font-weight:600;font-size:14px;margin:14px 0 6px;color:var(--muted)}
input[type=text],input[type=search]{width:100%;font:inherit;font-size:17px;padding:13px 14px;border-radius:11px;border:1px solid var(--line);
background:var(--bg);color:var(--text);outline:none}
input:focus{border-color:var(--accent)}
.seg{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.seg button,.chip{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--bg);color:var(--text);border-radius:10px;padding:10px 6px;cursor:pointer}
.seg button.on,.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{padding:8px 12px;border-radius:999px}
.hint{color:var(--muted);font-size:13px;margin-top:6px}
.btn{display:block;width:100%;font:inherit;font-size:17px;font-weight:600;padding:14px;border-radius:12px;border:0;background:var(--accent);color:#fff;
cursor:pointer;text-align:center;text-decoration:none;margin-top:16px}
.btn.secondary{background:var(--accent-soft);color:var(--text)}.btn.danger{background:color-mix(in srgb,var(--bad) 85%,#000)}
.btn:disabled{opacity:.5}
details summary{cursor:pointer;color:var(--muted);font-size:14px;margin-top:14px}
.switch{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:14px}
.switch input{width:22px;height:22px}
.bar{height:10px;background:var(--code);border-radius:99px;overflow:hidden;margin:12px 0 6px}
.bar div{height:100%;background:var(--accent);width:0;transition:width .6s}
.stage{font-weight:600}.muted{color:var(--muted);font-size:13px}
pre.log{background:var(--code);border-radius:10px;padding:10px;font-size:12px;line-height:1.5;max-height:42vh;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:12px 0 0}
.kinds{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:15px;margin-top:10px}
.kinds b{text-align:right}
.big{font-size:34px;font-weight:700;line-height:1.1}
.hist a{display:block;text-decoration:none;color:var(--text);padding:12px 0;border-bottom:1px solid var(--line)}
.hist a:last-child{border-bottom:0}.hist .h{font-weight:600}
.err{color:var(--bad);font-size:14px;margin-top:10px;min-height:1em}
nav{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);display:flex;padding-bottom:env(safe-area-inset-bottom);z-index:9}
nav a{flex:1;text-align:center;padding:10px 0 12px;text-decoration:none;color:var(--muted);font-size:13px}
nav a.on{color:var(--accent);font-weight:600}nav a span{display:block;font-size:20px;line-height:1.2}
.hidden{display:none}
</style>
</head>
<body>
<main>
<header><img src="/icon-192.png" alt=""><div><h1>SearchMaster</h1><div class="v" id="ver"></div></div></header>

<section id="v-search">
  <form class="card" id="form" autocomplete="off">
    <label class="t" for="q" style="margin-top:0">Что ищем?</label>
    <input type="search" id="q" placeholder="Например: Half-Life 2" enterkeyhint="search" required maxlength="200">
    <label class="t" for="aliases">Другие названия (через запятую)</label>
    <input type="text" id="aliases" placeholder="Например: HL2, Халф-Лайф 2">
    <label class="t">Глубина</label>
    <div class="seg" id="preset">
      <button type="button" data-v="fast">Быстро</button>
      <button type="button" data-v="normal" class="on">Обычно</button>
      <button type="button" data-v="deep">Глубоко</button>
    </div>
    <div class="hint" id="preset-hint"></div>
    <details>
      <summary>Что искать, языки, скачивание</summary>
      <label class="t">Аспекты</label><div class="chips" id="facets"></div>
      <label class="t">Языки</label><div class="chips" id="langs"></div>
      <label class="t">Скачать на устройство</label><div class="chips" id="dl"></div>
      <div class="hint">Скачанное лежит рядом с отчётом и открывается прямо из него. Исполняемые файлы не скачиваются никогда.</div>
      <div class="switch"><div><b>ИИ-сводка (Claude)</b><div class="hint" id="ai-hint"></div></div><input type="checkbox" id="ai"></div>
    </details>
    <button class="btn" id="go" type="submit">Искать</button>
    <div class="err" id="err"></div>
  </form>
</section>

<section id="v-job" class="hidden">
  <div class="card">
    <div class="muted">Поиск</div>
    <div style="font-size:20px;font-weight:700;word-break:break-word" id="j-q"></div>
    <div class="bar"><div id="j-bar"></div></div>
    <div><span class="stage" id="j-stage"></span> <span class="muted" id="j-time"></span></div>
    <div id="j-done" class="hidden">
      <div class="big" id="j-total"></div><div class="muted">релевантных результатов</div>
      <div class="kinds" id="j-kinds"></div>
      <div class="muted" id="j-extra" style="margin-top:10px"></div>
      <a class="btn" id="j-report" href="#" target="_blank">Открыть отчёт</a>
      <a class="btn secondary" href="#/">Новый поиск</a>
    </div>
    <button class="btn danger" id="j-stop">Остановить</button>
    <details id="j-logbox"><summary>Журнал</summary><pre class="log" id="j-log"></pre></details>
  </div>
</section>

<section id="v-history" class="hidden">
  <div class="card hist" id="hist"><div class="muted">Загрузка…</div></div>
</section>
</main>
<nav>
  <a href="#/" id="n-search"><span>⌕</span>Поиск</a>
  <a href="#/job" id="n-job"><span>◔</span>Текущий</a>
  <a href="#/history" id="n-history"><span>☰</span>Отчёты</a>
</nav>
<script>
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const store = { get(k, d){ try { const v = localStorage.getItem('sm_'+k); return v ? JSON.parse(v) : d; } catch(e){ return d; } },
                set(k, v){ try { localStorage.setItem('sm_'+k, JSON.stringify(v)); } catch(e){} } };
const FACET_RU = {general:'Общее',images:'Скриншоты',video:'Видео',code:'Код',files:'Файлы',discussions:'Обсуждения',
  technical:'Техническое',history:'История',news:'Новости'};
const DL_RU = {images:'Картинки',video:'Видео',audio:'Аудио',docs:'Документы',code:'Код',archives:'Архивы',gamedata:'Игровые данные',repos:'Исходники (zip)'};
const LANGS = ['en','ru','uk','de','fr','es','ja','zh','pl'];
const HINTS = {fast:'≈ полминуты–минута, меньше трафика', normal:'≈ 1–2 минуты: 2 раунда, разбор 25 страниц', deep:'несколько минут: больше запросов, разбор 50 страниц и переход по ссылкам'};
let INFO = null, current = store.get('job', null), timer = null;

function chipGroup(el, items, labels, selected){
  el.innerHTML = items.map(v => `<button type="button" class="chip ${selected.includes(v)?'on':''}" data-v="${esc(v)}">${esc(labels[v]||v)}</button>`).join('');
  el.onclick = e => { const b = e.target.closest('button'); if (b) b.classList.toggle('on'); };
}
const picked = el => [...el.querySelectorAll('.on')].map(b => b.dataset.v);

async function api(path, body){
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)} : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}

async function init(){
  try { INFO = await api('/api/info'); } catch(e){ $('#err').textContent = e.message; return; }
  $('#ver').textContent = 'версия ' + INFO.version;
  const saved = store.get('form', {});
  chipGroup($('#facets'), INFO.facets, FACET_RU, saved.facets || INFO.facets);
  chipGroup($('#langs'), LANGS, {}, saved.languages || ['en','ru']);
  chipGroup($('#dl'), INFO.downloads, DL_RU, saved.download || []);
  setPreset(saved.preset || 'normal');
  $('#ai').disabled = !INFO.ai_available;
  $('#ai').checked = INFO.ai_available && !!saved.ai;
  $('#ai-hint').textContent = INFO.ai_available ? 'Синонимы до поиска и аналитический отчёт после (платно по тарифам Anthropic)' : 'Недоступно: нужны pip install anthropic и ANTHROPIC_API_KEY';
  route();
}
function setPreset(v){
  document.querySelectorAll('#preset button').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  $('#preset-hint').textContent = HINTS[v];
}
$('#preset').onclick = e => { const b = e.target.closest('button'); if (b) setPreset(b.dataset.v); };

$('#form').onsubmit = async e => {
  e.preventDefault();
  $('#err').textContent = '';
  const body = {
    query: $('#q').value.trim(), aliases: $('#aliases').value,
    preset: $('#preset .on').dataset.v, facets: picked($('#facets')), languages: picked($('#langs')),
    download: picked($('#dl')), ai: $('#ai').checked,
  };
  if (!body.query) { $('#err').textContent = 'Введите запрос'; return; }
  store.set('form', {preset: body.preset, facets: body.facets, languages: body.languages, download: body.download, ai: body.ai});
  $('#go').disabled = true;
  try {
    const job = await api('/api/search', body);
    current = job.id; store.set('job', current);
    location.hash = '#/job/' + job.id;
  } catch(err){ $('#err').textContent = err.message; }
  finally { $('#go').disabled = false; }
};

function show(view){
  ['search','job','history'].forEach(v => { $('#v-'+v).classList.toggle('hidden', v !== view); $('#n-'+v).classList.toggle('on', v === view); });
}
function route(){
  clearTimeout(timer);
  const h = location.hash || '#/';
  let m = h.match(/^#\/job\/([0-9a-f]{12})/);
  if (h === '#/job' && current) { location.hash = '#/job/' + current; return; }
  if (m) { current = m[1]; store.set('job', current); show('job'); poll(); return; }
  if (h.startsWith('#/history')) { show('history'); loadHistory(); return; }
  show('search');
}
window.addEventListener('hashchange', route);

async function poll(){
  let j;
  try { j = await api('/api/job/' + current); }
  catch(e){ $('#j-q').textContent = 'Задача не найдена (сервер перезапускали?)'; $('#j-stop').classList.add('hidden'); return; }
  $('#j-q').textContent = j.query;
  $('#j-bar').style.width = j.progress + '%';
  $('#j-stage').textContent = j.stage;
  $('#j-time').textContent = '· ' + j.elapsed + ' с';
  const log = $('#j-log'), atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.textContent = j.log.join('\n');
  if (atBottom) log.scrollTop = log.scrollHeight;
  const running = j.state === 'running';
  $('#j-stop').classList.toggle('hidden', !running);
  $('#j-done').classList.toggle('hidden', running || j.state === 'error');
  if (j.state === 'error') $('#j-stage').textContent = 'Ошибка: ' + (j.error || '');
  if (!running && j.summary && j.summary.report) {
    const s = j.summary;
    $('#j-total').textContent = s.total;
    $('#j-kinds').innerHTML = s.by_kind.map(([k,v]) => `<span>${esc(k)}</span><b>${v}</b>`).join('');
    const extra = [];
    if (s.developers.length) extra.push('Разработчик: ' + s.developers.join(', '));
    if (s.engines.length) extra.push('Движок: ' + s.engines.join(', '));
    if (s.downloads) extra.push('Скачано файлов: ' + s.downloads);
    if (s.blocked.length) extra.push('Недоступны: ' + s.blocked.join(', '));
    $('#j-extra').innerHTML = extra.map(esc).join('<br>');
    $('#j-report').href = '/files/' + s.report.split('/').map(encodeURIComponent).join('/');
  }
  if (running) timer = setTimeout(poll, 1000);
}
$('#j-stop').onclick = async () => { $('#j-stop').disabled = true; try { await api('/api/job/' + current + '/cancel', {}); } catch(e){} $('#j-stop').disabled = false; };

async function loadHistory(){
  try {
    const items = await api('/api/history');
    $('#hist').innerHTML = items.length ? items.map(it => {
      const labels = (INFO && INFO.kind_labels) || {};
      const kinds = Object.entries(it.by_kind || {}).slice(0,4).map(([k,v]) => `${esc(labels[k] || k)} ${v}`).join(' · ');
      return `<a href="/files/${it.report.split('/').map(encodeURIComponent).join('/')}" target="_blank"><div class="h">${esc(it.topic)}</div>
        <div class="muted">${esc((it.started||'').replace('T',' '))} · найдено ${esc(it.total)}</div><div class="muted">${kinds}</div></a>`;
    }).join('') : '<div class="muted">Отчётов пока нет — запустите первый поиск.</div>';
  } catch(e){ $('#hist').innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}
init();
</script>
</body>
</html>
"""

"""HTTP layer: one shared session with retries, per-host politeness, robots.txt and a disk cache."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config
from .models import SourceBlocked
from .utils import sha1

log = logging.getLogger("searchmaster.net")

_CAPTCHA_MARKERS = (
    "captcha", "are you a robot", "unusual traffic", "automated queries",
    "anomaly-modal", "verify you are human", "cf-challenge", "challenge-platform",
)


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str]
    text: str
    content: bytes

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


def decode_body(raw: bytes, content_type: str) -> str:
    """Decode bytes using the header charset, a <meta charset>, UTF-8, then detection."""
    candidates: list[str] = []
    m = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))
    m2 = _META_CHARSET.search(raw[:4096])
    if m2:
        candidates.append(m2.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    try:
        from requests.compat import chardet
        guess = (chardet.detect(raw[:200_000]) or {}).get("encoding") if chardet else None
    except Exception:
        guess = None
    return raw.decode(guess or "utf-8", errors="replace")


def looks_blocked(text: str) -> bool:
    """Heuristic for captcha / anti-bot interstitials (use when a parser found nothing)."""
    low = text[:30000].lower()
    return any(m in low for m in _CAPTCHA_MARKERS)


class HttpClient:
    """Thread-safe wrapper used by every source."""

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        # Retry only transient connection failures and gateway errors.  Never honour
        # Retry-After automatically: a rate-limited site may ask us to wait an hour,
        # so 403/429 surface as SourceBlocked and the source is skipped instead.
        retry = Retry(
            total=2, connect=2, read=0, status=1, backoff_factor=0.5,
            status_forcelist=(502, 503, 504), allowed_methods=("GET", "POST", "HEAD"),
            respect_retry_after_header=False, raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": config.user_agent,
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
        })
        self._host_lock = threading.Lock()
        self._host_next: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()
        self.cache_dir = Path(config.cache_dir) if config.cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0, "robots_blocked": 0}
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------ utils
    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] += 1

    def _throttle(self, host: str, delay: float) -> None:
        """Serialise requests to the same host and keep `delay` seconds between them."""
        with self._host_lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            now = time.monotonic()
            wait = self._host_next.get(host, 0.0) - now
            if wait > 0:
                time.sleep(wait)
            self._host_next[host] = time.monotonic() + delay

    def allowed_by_robots(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        with self._robots_lock:
            root_lock = self._host_locks.setdefault("robots:" + root, threading.Lock())
        with root_lock:  # one fetch per site; other sites are not blocked meanwhile
            if root not in self._robots:
                rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
                try:
                    r = self.session.get(root + "/robots.txt", timeout=min(10, self.config.timeout))
                    if r.status_code >= 400:
                        rp = None  # no robots.txt -> everything allowed
                    else:
                        rp.parse(r.text.splitlines())
                except requests.RequestException:
                    rp = None
                self._robots[root] = rp
            rp = self._robots[root]
        if rp is None:
            return True
        ok = rp.can_fetch("SearchMaster", url)
        if not ok:
            self._bump("robots_blocked")
        return ok

    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        h = sha1(key)
        return self.cache_dir / h[:2] / f"{h}.json"

    def _cache_get(self, key: str) -> Response | None:
        path = self._cache_path(key)
        if not path or not path.exists():
            return None
        try:
            if time.time() - path.stat().st_mtime > self.config.cache_ttl_hours * 3600:
                return None
            d = json.loads(path.read_text(encoding="utf-8"))
            self._bump("cache_hits")
            return Response(d["url"], d["status"], d["headers"], d["text"], d["text"].encode("utf-8"))
        except (OSError, ValueError, KeyError):
            return None

    def _cache_put(self, key: str, resp: Response) -> None:
        path = self._cache_path(key)
        if not path or resp.status != 200:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "url": resp.url, "status": resp.status,
                "headers": {"content-type": resp.headers.get("content-type", "")},
                "text": resp.text,
            }), encoding="utf-8")
        except OSError:
            pass

    # --------------------------------------------------------------- requests
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: dict[str, Any] | None = None,
        method: str = "GET",
        delay: float | None = None,
        check_robots: bool = False,
        use_cache: bool = True,
        max_bytes: int | None = None,
        timeout: float | None = None,
    ) -> Response:
        """Fetch a URL and return a decoded Response.

        Raises SourceBlocked on 403/429/captcha pages and requests.RequestException on
        network errors; callers decide whether that is fatal.
        """
        req = requests.Request(method, url, params=params, data=data).prepare()
        full_url = req.url or url
        cache_key = f"{method} {full_url} {json.dumps(data, sort_keys=True) if data else ''}"
        if use_cache and (cached := self._cache_get(cache_key)):
            return cached
        if check_robots and not self.allowed_by_robots(full_url):
            raise PermissionError(f"robots.txt disallows {full_url}")

        host = urlparse(full_url).netloc
        self._throttle(host, self.config.per_host_delay if delay is None else delay)
        self._bump("requests")
        limit = max_bytes or self.config.max_page_bytes
        try:
            r = self.session.request(
                method, url, params=params, data=data, headers=headers,
                timeout=timeout or self.config.timeout, stream=True,
            )
            chunks, size = [], 0
            for chunk in r.iter_content(64 * 1024):
                chunks.append(chunk)
                size += len(chunk)
                if size >= limit:
                    break
            r.close()
        except requests.RequestException:
            self._bump("errors")
            raise
        raw = b"".join(chunks)
        text = decode_body(raw, r.headers.get("content-type", ""))
        resp = Response(r.url, r.status_code, {k.lower(): v for k, v in r.headers.items()}, text, raw)

        if r.status_code in (403, 429, 451):
            raise SourceBlocked(f"HTTP {r.status_code} from {host}")
        if r.status_code >= 400:
            self._bump("errors")
            raise requests.HTTPError(f"HTTP {r.status_code} for {full_url}", response=r)
        if use_cache:
            self._cache_put(cache_key, resp)
        return resp

    def get_json(self, url: str, **kw: Any) -> Any:
        headers = {"Accept": "application/json", **(kw.pop("headers", None) or {})}
        return self.get(url, headers=headers, **kw).json()

    def stream_to_file(self, url: str, path: Path, *, max_bytes: int, check_robots: bool = True) -> tuple[int, str]:
        """Download a file to disk; returns (size, content-type).  Aborts above max_bytes."""
        if check_robots and not self.allowed_by_robots(url):
            raise PermissionError(f"robots.txt disallows {url}")
        host = urlparse(url).netloc
        self._throttle(host, self.config.per_host_delay)
        self._bump("requests")
        with self.session.get(url, timeout=self.config.timeout * 3, stream=True) as r:
            if r.status_code in (403, 429):
                raise SourceBlocked(f"HTTP {r.status_code} from {host}")
            r.raise_for_status()
            declared = int(r.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                raise ValueError(f"file too large ({declared} bytes)")
            size = 0
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(256 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        fh.close()
                        tmp.unlink(missing_ok=True)
                        raise ValueError("file too large")
                    fh.write(chunk)
            tmp.replace(path)
            return size, r.headers.get("content-type", "")

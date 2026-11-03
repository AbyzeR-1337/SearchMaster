"""Polite HTTP client: honest User-Agent, per-host throttling, retries."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import requests

log = logging.getLogger("searchmaster.http")

RETRY_STATUSES = {429, 500, 502, 503, 504}


def default_user_agent() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "AbyzeR-1337/SearchMaster")
    return f"SearchMaster/1.0 (+https://github.com/{repo}; research tool)"


class HttpError(Exception):
    pass


class Http:
    def __init__(
        self,
        timeout: float = 20.0,
        user_agent: str | None = None,
        min_interval: float = 0.35,
        retries: int = 3,
        max_retry_wait: float = 20.0,
    ) -> None:
        self.timeout = timeout
        self.min_interval = min_interval
        self.retries = retries
        self.max_retry_wait = max_retry_wait
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": user_agent or default_user_agent(),
                "Accept-Language": "ru,en;q=0.8",
            }
        )
        self._host_lock = threading.Lock()
        self._host_next: dict[str, float] = {}

    # -- throttling ---------------------------------------------------------
    def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        with self._host_lock:
            now = time.monotonic()
            ready_at = max(now, self._host_next.get(host, 0.0))
            self._host_next[host] = ready_at + self.min_interval
        delay = ready_at - now
        if delay > 0:
            time.sleep(delay)

    # -- core ---------------------------------------------------------------
    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait_for_host(url)
            try:
                resp = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, self.max_retry_wait))
                    continue
                raise HttpError(f"{type(exc).__name__}: {exc}") from exc
            if resp.status_code in RETRY_STATUSES and attempt < self.retries:
                wait = 2 ** (attempt + 1)
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = int(retry_after)
                if wait > self.max_retry_wait:
                    # The server asks us to back off for long: respect it by giving up.
                    break
                resp.close()
                time.sleep(wait)
                continue
            return resp
        if last_exc:
            raise HttpError(str(last_exc))
        return resp  # last (non-OK) response

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None, **kw: Any) -> Any:
        resp = self.get(url, params=params, headers={"Accept": "application/json", **(headers or {})}, **kw)
        if resp.status_code != 200:
            raise HttpError(f"HTTP {resp.status_code} for {urlsplit(url).netloc}")
        try:
            return resp.json()
        except ValueError as exc:
            raise HttpError(f"invalid JSON from {urlsplit(url).netloc}") from exc

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None, **kw: Any) -> str:
        resp = self.get(url, params=params, headers=headers, **kw)
        if resp.status_code != 200:
            raise HttpError(f"HTTP {resp.status_code} for {urlsplit(url).netloc}")
        return resp.text

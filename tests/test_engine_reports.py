import functools
import http.server
import json
import threading

import pytest

from searchmaster import engine as engine_mod
from searchmaster.config import Config
from searchmaster.downloader import download_results, wanted_categories
from searchmaster.models import Result
from searchmaster.net import HttpClient
from searchmaster.report import md_to_html, write_reports
from searchmaster.sources.base import Source


class FakeWeb(Source):
    name = "bing"
    facets = frozenset({"general", "images", "code"})

    def search(self, plan):
        if plan.snowball_only:
            for term in plan.extra_terms:
                yield self.result(f"https://snow.example/{term.replace(' ', '-')}", f"{plan.topic} {term}",
                                  snippet="Valve Gordon Freeman City 17")
            return
        for i in range(5):
            yield self.result(f"https://ex{i}.com/hl2", f"Half-Life 2 article {i}",
                              snippet="Valve game with Gordon Freeman in City 17 on the Source engine")
        yield self.result("https://noise.com/", "Shop Vac", snippet="vacuum cleaner")
        yield self.result("https://cdn.example/shot.jpg", "Half-Life 2 <script>alert(1)</script>", kind="image")


class FakeBlocked(Source):
    name = "reddit"
    facets = frozenset({"general"})

    def search(self, plan):
        from searchmaster.models import SourceBlocked
        yield self.result("https://www.reddit.com/r/hl/1", "Half-Life 2 thread", kind="discussion",
                          snippet="Valve Gordon Freeman")
        raise SourceBlocked("HTTP 403")


class FakeCrash(Source):
    name = "github"
    facets = frozenset({"code"})

    def search(self, plan):
        raise RuntimeError("boom")
        yield  # pragma: no cover


@pytest.fixture
def offline_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_mod, "select_sources", lambda cfg: ([FakeWeb, FakeBlocked, FakeCrash], []))
    cfg = Config(query="Half-Life 2", out_dir=str(tmp_path), deep=0, cache_dir=None, workers=2,
                 github_token="ghp_SECRET", brave_api_key="SECRET2")
    return cfg


def test_engine_runs_offline_and_reports(offline_engine):
    cfg = offline_engine
    msgs = []
    run = engine_mod.Engine(cfg, progress=msgs.append).run()
    titles = {r.title for r in run.results}
    assert "Shop Vac" not in titles
    assert any(t.startswith("Half-Life 2 article") for t in titles)
    assert run.source_status["reddit"]["status"] == "blocked" and run.source_status["reddit"]["count"] == 1
    assert run.source_status["github"]["status"] == "error"
    assert run.rounds[1]["terms"], "snowball round should discover terms"
    assert any(r.url.startswith("https://snow.example/") for r in run.results)

    files = write_reports(run)
    assert set(files) == {"html", "md", "json"}
    payload = json.loads(files["json"].read_text(encoding="utf-8"))
    raw = files["json"].read_text(encoding="utf-8") + files["html"].read_text(encoding="utf-8")
    assert "SECRET" not in raw                       # API keys never leak into reports
    assert payload["config"]["github_token"] == "***"
    html = files["html"].read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html   # titles are JSON-escaped, rendered via textContent/esc
    assert "Half-Life 2" in files["md"].read_text(encoding="utf-8")


def test_md_to_html_escapes():
    out = md_to_html("## Title\n- **bold** [link](https://a.b)\n<img src=x onerror=alert(1)>")
    assert "<h4>Title</h4>" in out and "<strong>bold</strong>" in out
    assert '<a href="https://a.b"' in out and "<img" not in out


def test_wanted_categories():
    assert wanted_categories(["images", "docs"]) == {"image", "document"}
    assert "executable" not in wanted_categories(["all"])


@pytest.fixture
def local_server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "shot.png").write_bytes(b"\x89PNG fake image bytes")
    (root / "copy.png").write_bytes(b"\x89PNG fake image bytes")   # identical content
    (root / "manual.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "big.zip").write_bytes(b"0" * 300_000)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_download_results(local_server, tmp_path):
    cfg = Config(query="x", download=["images", "docs", "archives"], max_file_mb=0.1, cache_dir=None,
                 per_host_delay=0)
    results = [
        Result(url=f"{local_server}/shot.png", title="Shot", kind="image", source="t"),
        Result(url=f"{local_server}/copy.png", title="Copy", kind="image", source="t"),
        Result(url=f"{local_server}/manual.pdf", title="Manual", kind="file", source="t"),
        Result(url=f"{local_server}/big.zip", title="Big", kind="file", source="t"),
        Result(url=f"{local_server}/page", title="Page", kind="page", source="t"),
    ]
    out = tmp_path / "run" / "downloads"
    manifest = download_results(results, cfg, HttpClient(cfg), out)
    ok = [m for m in manifest if "path" in m]
    errors = [m for m in manifest if "error" in m]
    assert {m["category"] for m in ok} == {"image", "document"}
    assert len([m for m in ok if m["category"] == "image"]) == 1      # duplicate content dropped
    assert errors and "too large" in errors[0]["error"]               # 300 KB > 0.1 MB limit
    assert (out / "manifest.json").exists()
    assert all((tmp_path / "run" / m["path"]).exists() for m in ok)

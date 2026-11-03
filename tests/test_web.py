import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from searchmaster import engine as engine_mod
from searchmaster import web as web_mod
from searchmaster.models import Result
from searchmaster.sources.base import Source
from searchmaster.web import WebApp, app_icon_png


class FakeSource(Source):
    name = "bing"
    facets = frozenset({"general", "images"})
    delay = 0.0

    def search(self, plan):
        for i in range(4):
            time.sleep(self.delay)
            yield self.result(f"https://ex{i}.com/doom", f"Doom article {i}",
                              snippet="id Software shooter Doomguy Mars game", kind="page")
        yield Result(url="https://cdn.ex/doom.png", title="Doom screenshot", kind="image", source="bing")


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_mod, "select_sources", lambda cfg: ([FakeSource], []))
    # keep tests offline: no deep crawling of the fake result URLs
    monkeypatch.setattr(web_mod, "PRESETS", {k: {**v, "deep": 0} for k, v in web_mod.PRESETS.items()})
    started = []

    def make(token=None):
        app = WebApp(tmp_path / "results", token=token,
                     base_config={"cache_dir": None, "workers": 2})
        srv = app.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return app, f"http://127.0.0.1:{srv.server_address[1]}"

    yield make
    for srv in started:
        srv.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(url, body=None, headers=None, opener=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={**({"Content-Type": "application/json"} if data else {}),
                                                          **(headers or {})})
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=10) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def wait_done(base, job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, _, body = call(f"{base}/api/job/{job_id}")
        job = json.loads(body)
        if job["state"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_search_flow_and_report(server):
    app, base = server()
    status, _, html = call(base + "/")
    assert status == 200 and b"SearchMaster" in html and b"manifest.webmanifest" in html
    info = json.loads(call(base + "/api/info")[2])
    assert "images" in info["facets"] and "repos" in info["downloads"]

    status, _, body = call(base + "/api/search", {"query": "Doom", "preset": "fast", "facets": ["general", "images"]})
    assert status == 201
    job = wait_done(base, json.loads(body)["id"])
    assert job["state"] == "done" and job["progress"] == 100
    s = job["summary"]
    assert s["total"] >= 4 and s["report"].endswith("report.html")

    status, headers, report = call(f"{base}/files/{s['report']}")
    assert status == 200 and headers["Content-Type"].startswith("text/html") and b"Doom article" in report
    hist = json.loads(call(base + "/api/history")[2])
    assert hist and hist[0]["topic"] == "Doom"


def test_validation_and_json_only(server):
    _, base = server()
    assert call(base + "/api/search", {"query": "  "})[0] == 400
    req = urllib.request.Request(base + "/api/search", data=b"query=Doom",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=5)
    assert err.value.code == 415  # cross-site form posts are rejected


def test_path_traversal_blocked(server, tmp_path):
    (tmp_path / "secret.txt").write_text("top secret")
    _, base = server()
    for path in ("/files/../secret.txt", "/files/%2e%2e/secret.txt", "/files/..%2fsecret.txt"):
        status, _, body = call(base + path)
        assert status == 404 and b"top secret" not in body


def test_token_required_on_lan(server):
    _, base = server(token="s3cret")
    assert call(base + "/api/info")[0] == 401
    assert call(base + "/")[0] == 401
    assert call(base + "/icon-192.png")[0] == 200               # icons/manifest stay public
    status, headers, _ = call(base + "/?key=s3cret", opener=urllib.request.build_opener(NoRedirect))
    assert status == 303 and "sm_key=s3cret" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    assert call(base + "/api/info", headers={"Cookie": "sm_key=s3cret"})[0] == 200
    assert call(base + "/api/info", headers={"Cookie": "sm_key=wrong"})[0] == 401
    assert call(base + "/api/info?key=%D1%8F")[0] == 401         # non-ASCII key must not crash


def test_cancel_produces_partial_report(server, monkeypatch):
    monkeypatch.setattr(FakeSource, "delay", 0.4)
    _, base = server()
    job_id = json.loads(call(base + "/api/search", {"query": "Doom", "preset": "fast"})[2])["id"]
    time.sleep(0.3)
    assert json.loads(call(f"{base}/api/job/{job_id}/cancel", {})[2])["ok"] is True
    job = wait_done(base, job_id)
    assert job["state"] == "cancelled" and job["summary"]["report"]


def test_one_search_at_a_time_limit(server, monkeypatch):
    monkeypatch.setattr(FakeSource, "delay", 0.3)
    app, base = server()
    app.max_jobs = 1
    first = json.loads(call(base + "/api/search", {"query": "Doom"})[2])["id"]
    assert call(base + "/api/search", {"query": "Quake"})[0] == 409
    call(f"{base}/api/job/{first}/cancel", {})
    wait_done(base, first)


def test_icon_is_valid_png():
    png = app_icon_png(64)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in png

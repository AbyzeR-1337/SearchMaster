"""Checks the Claude integration builds a valid request, using a local fake API server."""

import http.server
import json
import threading

import pytest

anthropic = pytest.importorskip("anthropic")

from searchmaster import ai  # noqa: E402
from searchmaster.models import Result  # noqa: E402


def _sse(text: str, stop_reason: str = "end_turn") -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return b"".join(f"event: {e}\ndata: {json.dumps(d)}\n\n".encode() for e, d in events)


@pytest.fixture
def fake_api(monkeypatch):
    captured: dict = {"replies": []}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured["body"] = body
            captured["beta"] = self.headers.get("anthropic-beta", "")
            text, stop = captured["replies"].pop(0)
            payload = _sse(text, stop)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    yield captured
    srv.shutdown()


def test_expand_query_request_shape(fake_api):
    fake_api["replies"].append((json.dumps({"aliases": ["HL2", "Half-Life 2"], "related_terms": ["Valve"], "is_game": True}),
                                "end_turn"))
    out = ai.expand_query("Half-Life 2", model="claude-opus-5-5")
    assert out["aliases"] == ["HL2"]            # the topic itself is removed
    body = fake_api["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in fake_api["beta"]
    assert body["stream"] is True


def test_summarize_and_refusal(fake_api):
    results = [Result(url="https://a.com", title="Half-Life 2", snippet="Valve", source="bing")]
    fake_api["replies"].append(("## Кратко\nТекст [1]", "end_turn"))
    text = ai.summarize("Half-Life 2", results, {"by_kind": {"page": 1}}, model="claude-opus-5-5", effort="medium")
    assert text.startswith("## Кратко")
    assert fake_api["body"]["output_config"] == {"effort": "medium"}
    assert "https://a.com" in fake_api["body"]["messages"][0]["content"]

    fake_api["replies"].append(("", "refusal"))
    with pytest.raises(ai.AIUnavailable):
        ai.summarize("x", results, {}, model="claude-opus-5-5", effort="low")

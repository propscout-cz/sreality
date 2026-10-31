"""A local HTTP endpoint that plays a model's JSON replies, so the real SDK parses each one and serializes the next request."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest


class WireStub:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.bodies: list[bytes] = []

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [json.loads(body) for body in self.bodies]


@contextmanager
def wire_stub(monkeypatch: pytest.MonkeyPatch, base_url_env: str, replies: list[dict[str, Any]]) -> Iterator[WireStub]:
    """Serve `replies` in order to the SDK that reads its base URL from `base_url_env`; a request past the script is a 400."""
    stub = WireStub(replies)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            stub.bodies.append(self.rfile.read(int(self.headers["content-length"])))
            status = 200 if stub.replies else 400
            body = json.dumps(stub.replies.pop(0) if stub.replies else {"error": "script exhausted"}).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(base_url_env, f"http://127.0.0.1:{server.server_port}")
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def anthropic_reply(*content: dict[str, Any], stop: str = "tool_use", model: str = "claude-opus-5-5") -> dict[str, Any]:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}, "content": list(content)}


def gemini_reply(*parts: dict[str, Any]) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

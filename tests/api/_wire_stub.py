"""A local HTTP endpoint that plays a model's JSON replies, so the real SDK parses each one and serializes the next request.
It also plays Anthropic's Files API, the fake file a code turn produces included."""

from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest


class WireStub:
    def __init__(self, replies: list[dict[str, Any]], files: dict[str, dict[str, Any]] | None = None) -> None:
        self.replies = list(replies)
        self.bodies: list[bytes] = []
        self.files = dict(files or {})
        self.uploads: list[bytes] = []
        self.deleted: list[str] = []

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [json.loads(body) for body in self.bodies]


@contextmanager
def wire_stub(monkeypatch: pytest.MonkeyPatch, base_url_env: str, replies: list[dict[str, Any]],
              files: dict[str, dict[str, Any]] | None = None) -> Iterator[WireStub]:
    """Serve `replies` in order to the SDK that reads its base URL from `base_url_env`; a request past the script is a 400,
    and a reply's `__status` or `__delay_s` is what the endpoint does instead of answering 200 at once.
    `files` maps a file id to its metadata plus its `bytes`, for the Files API routes."""
    stub = WireStub(replies, files)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _metadata(self, file_id: str) -> bytes:
            meta = {k: v for k, v in stub.files[file_id].items() if k != "bytes"}
            return json.dumps({"id": file_id, "type": "file", "created_at": "2026-10-31T00:00:00Z",
                               "size_bytes": len(stub.files[file_id]["bytes"]), "downloadable": True,
                               "expires_at": None, **meta}).encode()

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["content-length"]))
            if self.path == "/v1/files":
                stub.uploads.append(body)
                file_id = f"file_up_{len(stub.uploads)}"
                stub.files[file_id] = {"filename": "up", "mime_type": "text/plain", "bytes": b"", "downloadable": False}
                self._send(200, self._metadata(file_id))
                return
            stub.bodies.append(body)
            reply = dict(stub.replies.pop(0)) if stub.replies else {"__status": 400, "error": "script exhausted"}
            time.sleep(reply.pop("__delay_s", 0))
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self._send(reply.pop("__status", 200), json.dumps(reply).encode())

        def do_GET(self) -> None:
            match = re.fullmatch(r"/v1/files/([^/?]+)(/content)?(\?.*)?", self.path)
            if match is None or match[1] not in stub.files:
                self._send(404, json.dumps({"type": "error", "error": {"type": "not_found_error", "message": "no file"}}).encode())
            elif match[2]:
                self._send(200, stub.files[match[1]]["bytes"], "application/octet-stream")
            else:
                self._send(200, self._metadata(match[1]))

        def do_DELETE(self) -> None:
            file_id = self.path.rsplit("/", 1)[-1].split("?")[0]
            stub.deleted.append(file_id)
            self._send(200, json.dumps({"id": file_id, "type": "file_deleted"}).encode())

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

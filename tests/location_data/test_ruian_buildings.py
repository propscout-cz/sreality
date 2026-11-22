"""The building load's download, offline: what is tried again and what is not."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from location_data import ruian_buildings as rb


class _Resp:
    def __init__(self, status: int, body: bytes = b"zip") -> None:
        self.status_code, self._body = status, body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def iter_content(self, _size: int) -> list[bytes]:
        return [self._body]


class _Session:
    def __init__(self, answers: list[Any]) -> None:
        self.answers, self.calls = answers, 0

    def get(self, url: str, **kw: Any) -> _Resp:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rb.time, "sleep", lambda _s: None)


def test_a_busy_server_is_tried_again(tmp_path):
    sess = _Session([_Resp(503), requests.ConnectionError("reset"), _Resp(429), _Resp(200, b"abc")])
    assert len(rb.download(sess, "u", tmp_path / "f")) == 64
    assert (tmp_path / "f").read_bytes() == b"abc" and sess.calls == 4


def test_a_missing_file_is_not_tried_again(tmp_path):
    sess = _Session([_Resp(404)])
    with pytest.raises(rb.Unavailable, match="HTTP 404"):
        rb.download(sess, "u", tmp_path / "f")
    assert sess.calls == 1


def test_the_tries_are_bounded(tmp_path):
    sess = _Session([_Resp(500)] * 10)
    with pytest.raises(rb.Unavailable):
        rb.download(sess, "u", tmp_path / "f")
    assert sess.calls == len(rb._RETRY_WAITS_S) + 1

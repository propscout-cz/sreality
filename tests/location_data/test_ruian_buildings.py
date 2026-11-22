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


def test_a_reset_in_the_middle_of_the_body_is_tried_again(tmp_path):
    sess = _Session([requests.exceptions.ChunkedEncodingError("reset"),
                     requests.exceptions.ContentDecodingError("bad gzip"), _Resp(200, b"abc")])
    rb.download(sess, "u", tmp_path / "f")
    assert sess.calls == 3


def test_only_a_404_is_marked_missing(tmp_path):
    for status, missing in ((404, True), (403, False)):
        with pytest.raises(rb.Unavailable) as gone:
            rb.download(_Session([_Resp(status)]), "u", tmp_path / "f")
        assert gone.value.missing is missing


class _Conn:
    closed = broken = False

    def __init__(self) -> None:
        self.analyzed = 0

    def close(self) -> None:
        self.closed = True


def _run(monkeypatch: pytest.MonkeyPatch, outcomes: dict[int, Any], tmp_path: Any) -> dict[str, Any]:
    """rb.run over scripted obce: each code maps to (buildings, links) or an exception to raise."""
    seen: dict[str, Any] = {"loaded": [], "failed": [], "opened": 0, "conns": []}

    def open_conn() -> _Conn:
        seen["opened"] += 1
        seen["conns"].append(_Conn())
        return seen["conns"][-1]

    def load(conn: Any, sess: Any, version: Any, unit: int, code: int, work: Any, **kw: Any) -> tuple[int, int]:
        seen["loaded"].append(code)
        outcome = outcomes[code]
        if isinstance(outcome, Exception):
            if getattr(outcome, "kills_connection", False):
                conn.broken = True
            raise outcome
        return outcome

    monkeypatch.setattr(rb.loader_db, "open_loader_connection", open_conn)
    monkeypatch.setattr(rb, "current_version", lambda c: rb.Version(4, __import__("datetime").date(2026, 10, 31)))
    monkeypatch.setattr(rb, "pending_obce", lambda c, v: [(code, code) for code in outcomes])
    monkeypatch.setattr(rb, "load_obec", load)
    monkeypatch.setattr(rb, "record_failure", lambda c, v, code, err: seen["failed"].append((code, err, c)))
    assert rb.run(max_seconds=None, only=None, work_dir=tmp_path) == 0
    return seen


def test_no_kind_of_failure_in_one_obec_stops_the_others(monkeypatch, tmp_path):
    seen = _run(monkeypatch, {
        1: ValueError("a date that is no date"), 2: OSError("disk"), 3: rb.ShrinkRefused("x"),
        4: rb.Unavailable("HTTP 404", missing=True), 5: (10, 7),
    }, tmp_path)
    assert seen["loaded"] == [1, 2, 3, 4, 5]
    assert [code for code, _err, _c in seen["failed"]] == [1, 2, 3, 4]
    assert seen["failed"][0][1].startswith("ValueError: ")


def test_a_dropped_connection_is_reopened_before_the_failure_is_written(monkeypatch, tmp_path):
    lost = rb.psycopg.OperationalError("server closed the connection")
    lost.kills_connection = True
    seen = _run(monkeypatch, {1: lost, 2: (1, 1)}, tmp_path)
    assert seen["opened"] == 2 and seen["loaded"] == [1, 2]
    assert seen["failed"][0][2] is seen["conns"][1], "the failure went to the new connection, not the dead one"


def test_a_vintage_that_is_not_published_stops_the_run_with_one_message(monkeypatch, tmp_path, caplog):
    outcomes = {code: rb.Unavailable("HTTP 404", missing=True) for code in range(1, 60)}
    with caplog.at_level("ERROR"):
        seen = _run(monkeypatch, outcomes, tmp_path)
    assert len(seen["loaded"]) == rb._MISSING_STOP
    assert "is not published" in caplog.text

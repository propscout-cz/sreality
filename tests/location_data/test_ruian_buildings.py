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
    monkeypatch.setattr(rb, "registry_load_in_flight", lambda c, v: None)
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


def _at(hour: int, minute: int, second: int = 0) -> Any:
    import datetime

    return datetime.datetime(2026, 11, 23, hour, minute, second, tzinfo=datetime.UTC)


def test_the_window_is_a_time_of_day_not_a_budget() -> None:
    clock = rb.Clock(window=(13 * 60, 17 * 60))
    assert [clock.open(_at(*t)) for t in ((12, 59), (13, 0), (16, 59), (17, 0), (2, 30))] == [
        False, True, True, False, False]
    assert rb.Clock().open(_at(3, 0)), "a hand run has no window"


def test_the_quiet_minutes_are_waited_out_to_their_end() -> None:
    clock = rb.Clock(quiet=(43, 50))
    assert clock.quiet_for(_at(14, 42, 59)) == 0
    assert clock.quiet_for(_at(14, 43)) == 7 * 60
    assert clock.quiet_for(_at(14, 49, 30)) == 30
    assert clock.quiet_for(_at(14, 50)) == 0


def test_a_run_outside_its_hours_opens_no_connection(monkeypatch, tmp_path) -> None:
    def no_connection() -> Any:
        raise AssertionError("a run outside its hours must not reach the database")

    monkeypatch.setattr(rb.loader_db, "open_loader_connection", no_connection)
    assert rb.run(max_seconds=None, only=None, work_dir=tmp_path, clock=rb.Clock(window=(13 * 60, 17 * 60)),
                  now=lambda: _at(17, 5)) == 0


def test_a_run_restarted_late_stops_at_the_end_of_its_hours_and_waits_out_the_quiet_minutes(monkeypatch,
                                                                                           tmp_path) -> None:
    """Three obce, each start taking the next clock reading: 16:40 runs, 16:44 is waited out, then 17:00 ends it."""
    times = iter([_at(16, 40),               # the run's own gate
                  _at(16, 40), _at(16, 40),  # obec 1: not quiet, inside the hours
                  _at(16, 44), _at(16, 50),  # obec 2: quiet for 6 min, then inside the hours
                  _at(16, 58), _at(17, 0),   # obec 3: not quiet, but the hours are over
                  _at(17, 0)])               # the stop's log line
    slept: list[float] = []
    loaded: list[int] = []

    class _C:
        closed = broken = False

        def close(self) -> None:
            pass

    monkeypatch.setattr(rb.loader_db, "open_loader_connection", _C)
    monkeypatch.setattr(rb, "current_version", lambda c: rb.Version(4, _at(0, 0).date()))
    monkeypatch.setattr(rb, "registry_load_in_flight", lambda c, v: None)
    monkeypatch.setattr(rb, "pending_obce", lambda c, v: [(1, 1), (2, 2), (3, 3)])
    monkeypatch.setattr(rb, "load_obec", lambda c, s, v, unit, code, w, **kw: loaded.append(code) or (1, 1))
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    assert rb.run(max_seconds=None, only=None, work_dir=tmp_path, now=lambda: next(times), sleep=slept.append,
                  clock=rb.Clock(window=(13 * 60, 17 * 60), quiet=(43, 50))) == 0
    assert loaded == [1, 2] and slept == [6 * 60]


def test_a_registry_version_still_merging_holds_a_full_run_but_not_a_named_obec(monkeypatch, tmp_path) -> None:
    loaded: list[int] = []

    class _C:
        closed = broken = False

        def close(self) -> None:
            pass

    monkeypatch.setattr(rb.loader_db, "open_loader_connection", _C)
    monkeypatch.setattr(rb, "current_version", lambda c: rb.Version(4, _at(0, 0).date()))
    monkeypatch.setattr(rb, "registry_load_in_flight", lambda c, v: 5)
    monkeypatch.setattr(rb, "pending_obce", lambda c, v: [(1, 1)])
    monkeypatch.setattr(rb, "load_obec", lambda c, s, v, unit, code, w, **kw: loaded.append(code) or (1, 1))
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    assert rb.run(max_seconds=None, only=None, work_dir=tmp_path) == 0
    assert loaded == []
    assert rb.run(max_seconds=None, only=[1], work_dir=tmp_path) == 0
    assert loaded == [1], "a hand run on a named obec is the operator's own call"


@pytest.mark.parametrize("argv", [["--window-utc", "17:00-13:00"], ["--window-utc", "13-17"],
                                  ["--quiet-minutes", "50-43"], ["--quiet-minutes", "43-61"]])
def test_a_malformed_clock_argument_is_refused_at_the_command_line(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        rb.main(argv)

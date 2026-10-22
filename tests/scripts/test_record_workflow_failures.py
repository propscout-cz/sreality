"""Hermetic tests for the canary's poller: the production filter, the workflow facts, the cursor
and the reason pass.

No DB, no network — psycopg import lives inside main(), so importing the
filter is clean.
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.record_workflow_failures import (
    CANCELLED_MIN_DURATION_MINUTES,
    MAX_PAGES,
    MONITOR_WORKFLOW_NAME,
    PER_PAGE,
    RECENT_STARTS_KEPT,
    _advance_cursor,
    _read_cursor,
    _write_cursor,
    is_production_run,
    parse_ts,
    select_failed_runs,
    select_workflow_health,
)

SINCE = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
_FIXTURE_LOG = Path(__file__).resolve().parents[1] / "fixtures" / "workflow_job_116299577551.txt"
_FIXTURE_REASON = (
    "2026-10-15 07:21:27,594 INFO verify_pipeline CHECK llm_errors status=fail value=0.0035")


def _run(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 101,
        "name": "Scraping: Sreality index walk",
        "path": ".github/workflows/index_walk.yml",
        "event": "schedule",
        "head_branch": "main",
        "conclusion": "failure",
        "updated_at": "2026-06-11T12:30:00Z",
        "run_started_at": "2026-06-11T12:05:00Z",
        "html_url": "https://github.com/waiff/sreality/actions/runs/101",
    }
    base.update(overrides)
    return base


def test_keeps_only_alerting_conclusions():
    runs = [
        _run(id=1, conclusion="success"),
        _run(id=2, conclusion="failure"),
        # a QUICK cancel (1 min) — a supersession, not a failure → excluded
        _run(id=3, conclusion="cancelled", run_started_at="2026-06-11T12:29:00Z"),
        _run(id=4, conclusion="timed_out"),
        _run(id=5, conclusion="startup_failure"),
        _run(id=6, conclusion="skipped"),
        _run(id=7, conclusion=None),
    ]
    kept = select_failed_runs(runs, since=SINCE)
    assert [r["run_id"] for r in kept] == [2, 4, 5]


def test_cancelled_long_but_never_started_is_skipped():
    """A superseded run that sat QUEUED >= 8 min looks like a timeout kill by duration
    (run_started_at = queue entry), but its jobs never started — with started_check
    provided, it must be skipped; a genuinely started long cancel is kept. Without
    started_check (None), duration-only behavior is preserved."""
    long_cancel = _run(id=1, conclusion="cancelled",
                       run_started_at="2026-06-11T12:00:00Z",
                       updated_at="2026-06-11T12:20:00Z")
    # never started -> skipped
    assert select_failed_runs([long_cancel], since=SINCE,
                              started_check=lambda rid: False) == []
    # actually ran -> kept
    assert [r["run_id"] for r in select_failed_runs(
        [long_cancel], since=SINCE, started_check=lambda rid: True)] == [1]
    # no checker (pure mode) -> kept, as before
    assert [r["run_id"] for r in select_failed_runs([long_cancel], since=SINCE)] == [1]
    # the checker is NOT consulted for plain failures (would raise if called)
    def _boom(rid):
        raise AssertionError("started_check must not run for conclusion=failure")
    assert [r["run_id"] for r in select_failed_runs(
        [_run(id=2, conclusion="failure")], since=SINCE, started_check=_boom)] == [2]


def test_cancelled_kept_only_when_it_ran_long_enough():
    long_min = CANCELLED_MIN_DURATION_MINUTES + 5
    runs = [
        # timeout-minutes kill: ran to its budget → kept
        _run(id=1, conclusion="cancelled",
             run_started_at="2026-06-11T12:00:00Z",
             updated_at=f"2026-06-11T12:{long_min:02d}:00Z"),
        # cancel-in-progress supersession: killed in 2 min → dropped
        _run(id=2, conclusion="cancelled",
             run_started_at="2026-06-11T12:28:00Z", updated_at="2026-06-11T12:30:00Z"),
        # cancelled with no start time → can't judge → dropped (conservative)
        _run(id=3, conclusion="cancelled", run_started_at=None),
    ]
    assert [r["run_id"] for r in select_failed_runs(runs, since=SINCE)] == [1]


def test_excludes_the_monitor_itself():
    runs = [
        _run(id=1, name=MONITOR_WORKFLOW_NAME),
        _run(id=2, name="Monitoring: LLM pipeline liveness"),
    ]
    kept = select_failed_runs(runs, since=SINCE)
    assert [r["run_id"] for r in kept] == [2]


def test_drops_runs_completed_before_the_window():
    runs = [
        _run(id=1, updated_at="2026-06-11T11:59:59Z"),
        _run(id=2, updated_at="2026-06-11T12:00:00Z"),  # boundary: kept
        _run(id=3, updated_at=None),
        _run(id=4, updated_at="not-a-timestamp"),
    ]
    kept = select_failed_runs(runs, since=SINCE)
    assert [r["run_id"] for r in kept] == [2]


def test_row_shape_and_timestamp_parsing() -> None:
    (row,) = select_failed_runs([_run()], since=SINCE)
    assert row == {
        "run_id": 101,
        "workflow_name": "Scraping: Sreality index walk",
        "workflow_path": ".github/workflows/index_walk.yml",
        "conclusion": "failure",
        "run_started_at": datetime(2026, 6, 11, 12, 5, tzinfo=timezone.utc),
        "html_url": "https://github.com/waiff/sreality/actions/runs/101",
        "event": "schedule",
        "head_branch": "main",
    }


def test_tolerates_missing_optional_fields():
    (row,) = select_failed_runs(
        [_run(name=None, path=None, run_started_at=None, html_url=None)], since=SINCE,
    )
    assert row["workflow_name"] == "(unnamed)"
    assert row["workflow_path"] is None
    assert row["run_started_at"] is None
    assert row["html_url"] is None


# --- the production filter: a PR, a branch push or a dispatch is not a job's record ---------


@pytest.mark.parametrize(("event", "branch", "production"), [
    ("schedule", "main", True),
    ("push", "main", True),
    ("push", "feature/health-platform", False),
    ("pull_request", "feature/health-platform", False),
    ("workflow_dispatch", "main", False),
    (None, "main", False),
])
def test_only_a_scheduled_run_or_a_push_to_main_is_production(
        event: str | None, branch: str, production: bool) -> None:
    assert is_production_run(_run(event=event, head_branch=branch)) is production
    assert is_production_run(_run(name=MONITOR_WORKFLOW_NAME, event=event,
                                  head_branch=branch)) is False


def test_pr_and_branch_runs_are_skipped_for_failures_and_successes() -> None:
    attended = [
        _run(id=1, event="pull_request", head_branch="feature/x", path="a.yml"),
        _run(id=2, event="push", head_branch="feature/x", path="a.yml"),
        _run(id=3, event="workflow_dispatch", head_branch="main", path="a.yml"),
        _run(id=4, event="pull_request", head_branch="feature/x", path="a.yml",
             conclusion="success"),
        _run(id=5, event="workflow_dispatch", head_branch="main", path="a.yml",
             conclusion="success"),
    ]
    assert select_failed_runs(attended, since=SINCE) == []
    assert select_workflow_health(attended) == []
    canary = [_run(id=6, name=MONITOR_WORKFLOW_NAME, conclusion="success", path="v.yml")]
    assert select_workflow_health(canary) == []


# --- select_workflow_health ----------------------------------------------------


def test_latest_success_picks_newest_per_path():
    runs = [
        _run(id=1, conclusion="success", path="a.yml", run_started_at="2026-06-11T10:00:00Z"),
        _run(id=2, conclusion="success", path="a.yml", run_started_at="2026-06-11T12:00:00Z"),
        _run(id=3, conclusion="success", path="b.yml", run_started_at="2026-06-11T09:00:00Z"),
    ]
    by_path = {s["workflow_path"]: s for s in select_workflow_health(runs)}
    assert by_path["a.yml"]["last_success_run_id"] == 2  # newer wins
    assert by_path["a.yml"]["last_success_at"] == datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
    assert by_path["b.yml"]["last_success_run_id"] == 3


def test_a_workflow_seen_only_failing_gets_its_row_and_a_pathless_run_none() -> None:
    runs = [
        _run(id=1, conclusion="failure", path="a.yml"),
        _run(id=2, conclusion="cancelled", path="a.yml"),
        _run(id=3, conclusion="success", path=None),
    ]
    (row,) = select_workflow_health(runs)
    assert row["workflow_path"] == "a.yml"
    assert (row["last_success_run_id"], row["last_success_at"]) == (None, None)
    assert row["recent_starts"] == [datetime(2026, 6, 11, 12, 5, tzinfo=timezone.utc)]


def test_recent_starts_are_every_conclusions_newest_starts_once_each() -> None:
    starts = [SINCE + timedelta(hours=h) for h in range(RECENT_STARTS_KEPT + 5)]
    runs = [_run(id=i, path="a.yml", conclusion=("success", "failure", "cancelled")[i % 3],
                 run_started_at=s.strftime("%Y-%m-%dT%H:%M:%SZ"))
            for i, s in enumerate(starts)]
    runs.append(_run(id=99, path="a.yml", run_started_at=starts[-1].strftime("%Y-%m-%dT%H:%M:%SZ")))
    runs.append(_run(id=100, path="a.yml", run_started_at=None))
    (row,) = select_workflow_health(runs)
    assert row["recent_starts"] == sorted(starts, reverse=True)[:RECENT_STARTS_KEPT]


def test_only_a_scheduled_run_adds_a_start_and_a_push_still_moves_the_last_success() -> None:
    """A push lands when someone merges, so it has no cadence to be overdue against."""
    runs = [
        _run(id=3, event="push", conclusion="success", path="a.yml",
             run_started_at="2026-06-11T14:00:00Z"),
        _run(id=2, event="push", path="a.yml", run_started_at="2026-06-11T13:00:00Z"),
        _run(id=1, path="a.yml", run_started_at="2026-06-11T12:00:00Z"),
    ]
    (row,) = select_workflow_health(runs)
    assert row["recent_starts"] == [datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)]
    assert (row["last_success_run_id"], row["last_success_at"]) == (
        3, datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc))


def test_parse_ts_handles_z_suffix_and_garbage():
    assert parse_ts("2026-06-11T12:00:00Z") == SINCE
    assert parse_ts(None) is None
    assert parse_ts("") is None
    assert parse_ts("garbage") is None


# --- high-water-mark cursor -------------------------------------------------


class _FakeCursor:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store
        self._row: Any = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if "SELECT value" in sql:
            self._row = (self._store.get("val"),)
        elif "INSERT INTO app_settings" in sql:
            self._store["val"] = params[1]  # the ISO timestamp string

    def fetchone(self) -> Any:
        return self._row


class _FakeConn:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self.store)


def test_cursor_read_write_roundtrip():
    conn = _FakeConn()
    assert _read_cursor(conn) is None  # unset on first ever run
    ts = datetime(2026, 7, 9, 21, 50, tzinfo=timezone.utc)
    _write_cursor(conn, ts)
    assert _read_cursor(conn) == ts


# --- W0.1: the high-water mark only advances over a window it actually covered


def test_cursor_jumps_to_newest_when_the_window_was_fully_covered():
    completions = [SINCE, SINCE + timedelta(minutes=30), SINCE + timedelta(minutes=90)]
    assert _advance_cursor(completions, reached_since=True) == SINCE + timedelta(minutes=90)


def test_cursor_is_held_when_the_page_cap_was_hit_first():
    """The permanent-loss bug. Pages come back newest-first, so hitting the cap means
    every completion seen is NEWER than `since` and the runs that were skipped are older
    than all of them. The old code advanced to min(completions) — believing it was
    "crawling to oldest-seen" — and the next poll's `completed_at < since` filter then
    dropped the skipped runs forever. Only 2 of the 6 portals that failed on 2026-08-26
    were ever recorded."""
    newest = SINCE + timedelta(minutes=200)
    oldest_seen = SINCE + timedelta(minutes=120)
    completions = [oldest_seen, newest]
    # The dangerous answer is anything that moves the mark past `since`.
    assert _advance_cursor(completions, reached_since=False) is None


def test_cursor_untouched_when_nothing_completed():
    assert _advance_cursor([], reached_since=True) is None
    assert _advance_cursor([], reached_since=False) is None


def test_page_budget_covers_the_worst_observed_inter_poll_gap():
    """The cron is */30 but the Actions throttle really runs it 80-256 min apart, so the
    budget has to hold a 256-minute window's worth of completed runs."""
    assert MAX_PAGES * PER_PAGE >= 1000


# --- the reason line: reading WHY a run went red ----------------------------

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from scripts import record_workflow_failures as rwf  # noqa: E402


class _Resp:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *_: Any) -> None:
        return None


def _job(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 555, "name": "drain", "conclusion": "failure",
        "steps": [{"name": "Checkout", "conclusion": "success"},
                  {"name": "Run drain", "conclusion": "failure"}],
    }
    base.update(kw)
    return base


def test_select_failed_job_picks_the_first_failed_job() -> None:
    assert rwf.select_failed_job([_job(id=1, conclusion="success"), _job(id=2), _job(id=3)]) == 2


def test_select_failed_job_reads_a_timeout_kill_too() -> None:
    """GitHub reports a `timeout-minutes` kill as `cancelled`; its log holds the reason."""
    assert rwf.select_failed_job([_job(id=4, conclusion="cancelled")]) == 4


def test_select_failed_job_returns_none_when_every_job_passed():
    assert rwf.select_failed_job([_job(conclusion="success")]) is None


def test_log_fetch_never_forwards_the_token_to_the_signed_blob(monkeypatch):
    """CPython's redirect handler copies EVERY header onto the redirect, Authorization
    included. That leaks GITHUB_TOKEN to a third-party host AND does not work — Azure
    answers 401 when the bearer header is present."""
    signed = "https://productionresultssa15.blob.core.windows.net/x?sig=abc"

    class _Opener:
        def open(self, req: Any, timeout: int = 0) -> Any:
            assert req.get_header("Authorization") is not None   # the API call is authed
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": signed}, None)

    seen: list[tuple[str, dict[str, str]]] = []

    def _bare(req: Any, timeout: int = 0) -> Any:
        seen.append((req.full_url, dict(req.header_items())))
        rng = req.get_header("Range")
        if rng == "bytes=0-0":
            return _Resp(b"x", {"Content-Range": "bytes 0-0/27247"})
        return _Resp(b"##[error]boom")

    monkeypatch.setattr(rwf, "_NO_REDIRECT_OPENER", _Opener())
    monkeypatch.setattr(urllib.request, "urlopen", _bare)

    out = rwf.fetch_job_log("waiff/sreality", "ghs_secret", 555, cap_bytes=1024)
    assert out == "##[error]boom"
    assert [u for u, _h in seen] == [signed, signed]
    for _u, headers in seen:
        assert not any(k.lower() == "authorization" for k in headers)
        assert "ghs_secret" not in str(headers)


def test_log_fetch_asks_for_a_closed_range_not_a_suffix_range(monkeypatch):
    """Azure IGNORES suffix ranges: `Range: bytes=-500` came back 200 with the whole
    27KB body. The length has to be learned first."""
    ranges: list[str] = []

    class _Opener:
        def open(self, req: Any, timeout: int = 0) -> Any:
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": "https://blob/x"}, None)

    def _bare(req: Any, timeout: int = 0) -> Any:
        ranges.append(req.get_header("Range"))
        if len(ranges) == 1:
            return _Resp(b"x", {"Content-Range": "bytes 0-0/27247"})
        return _Resp(b"tail")

    monkeypatch.setattr(rwf, "_NO_REDIRECT_OPENER", _Opener())
    monkeypatch.setattr(urllib.request, "urlopen", _bare)
    rwf.fetch_job_log("r", "t", 1, cap_bytes=1000)
    assert ranges == ["bytes=0-0", "bytes=26247-27246"]


def test_log_fetch_tolerates_an_evicted_blob(monkeypatch):
    """The signed URL 404s (BlobNotFound) on very fresh or very old runs; that is a
    NULL reason, never a poller failure."""
    class _Opener:
        def open(self, req: Any, timeout: int = 0) -> Any:
            raise urllib.error.HTTPError(
                req.full_url, 302, "Found", {"Location": "https://blob/gone"}, None)

    def _bare(req: Any, timeout: int = 0) -> Any:
        raise urllib.error.HTTPError("https://blob/gone", 404, "BlobNotFound", {}, None)

    monkeypatch.setattr(rwf, "_NO_REDIRECT_OPENER", _Opener())
    monkeypatch.setattr(urllib.request, "urlopen", _bare)
    assert rwf.fetch_job_log("r", "t", 1) is None


def test_workflow_states_are_one_listing_and_an_unread_one_keeps_the_stored_states(
        monkeypatch) -> None:
    calls: list[str] = []
    body = (b'{"total_count": 2, "workflows": ['
            b'{"path": ".github/workflows/a.yml", "state": "active"},'
            b'{"path": ".github/workflows/b.yml", "state": "disabled_manually"}]}')

    def _listing(req: Any, timeout: int = 0) -> Any:
        calls.append(req.full_url)
        assert req.get_header("Authorization") == "Bearer t"
        return _Resp(body)

    monkeypatch.setattr(urllib.request, "urlopen", _listing)
    assert rwf._fetch_workflow_states("waiff/sreality", "t") == {
        ".github/workflows/a.yml": "active", ".github/workflows/b.yml": "disabled_manually"}
    assert calls == ["https://api.github.com/repos/waiff/sreality/actions/workflows"
                     "?per_page=100&page=1"]

    def _down(req: Any, timeout: int = 0) -> Any:
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _down)
    assert rwf._fetch_workflow_states("waiff/sreality", "t") is None


class _PollCursor:
    """Just enough of a psycopg cursor for one main() poll; every statement is kept."""

    def __init__(self, conn: "_PollConn") -> None:
        self._conn = conn
        self.rowcount = 0

    def __enter__(self) -> "_PollCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((sql, params))
        self.rowcount = 1 if "INSERT INTO workflow_failures" in sql else 0

    def fetchone(self) -> Any:
        return None  # no cursor stored yet: the poll bootstraps its window

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [(p,) for p in self._conn.claimed]


class _PollConn:
    def __init__(self, claimed: tuple[str, ...] = ()) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.claimed = claimed

    def __enter__(self) -> "_PollConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def cursor(self) -> _PollCursor:
        return _PollCursor(self)

    def params(self, fragment: str) -> list[Any]:
        return [p for s, p in self.executed if fragment in s]


def _failure(run_id: int) -> dict[str, Any]:
    return {"run_id": run_id, "workflow_path": f".github/workflows/w{run_id}.yml"}


def _must_not_fetch(*_a: Any, **_k: Any) -> None:
    raise AssertionError("must not fetch a log")


def test_record_reasons_stores_the_reason_line_of_the_failed_jobs_log(monkeypatch) -> None:
    monkeypatch.setattr(rwf, "_fetch_failed_job", lambda _r, _t, run_id: run_id * 10)
    logs = {9000: _FIXTURE_LOG.read_text(encoding="utf-8")}
    monkeypatch.setattr(rwf, "fetch_job_log", lambda _r, _t, job_id, **_k: logs[job_id])
    conn = _PollConn()
    stats = rwf.record_reasons(conn, "r", "t", [_failure(900)])
    assert (stats["fetched"], stats["stored"]) == (1, 1)
    assert conn.params("SET reason") == [(_FIXTURE_REASON, 900)]


def test_an_unreadable_log_leaves_the_reason_null(monkeypatch) -> None:
    monkeypatch.setattr(rwf, "_fetch_failed_job", lambda *_a: None)
    monkeypatch.setattr(rwf, "fetch_job_log", _must_not_fetch)
    conn = _PollConn()
    stats = rwf.record_reasons(conn, "r", "t", [_failure(900)])
    assert (stats["unreadable"], stats["fetched"]) == (1, 0)
    assert conn.executed == []


def test_record_reasons_stops_at_its_wall_clock_budget(monkeypatch) -> None:
    """MAX_LOG_FETCHES is not a time budget: 25 fetches x up to 4 round trips x
    API_TIMEOUT_S=30 is ~50 minutes inside a job with `timeout-minutes: 5`, and a job
    kill is not an exception anything can catch. Past it a reason stays NULL; nothing
    revisits it."""
    monkeypatch.setattr(rwf, "_fetch_failed_job", _must_not_fetch)
    conn = _PollConn()
    stats = rwf.record_reasons(conn, "r", "t", [_failure(i) for i in range(5)],
                               budget_seconds=0.0)
    assert stats["expired"] == 5 and conn.executed == []


def test_record_reasons_respects_the_api_budget(monkeypatch) -> None:
    fetched: list[int] = []
    monkeypatch.setattr(rwf, "MAX_LOG_FETCHES", 1)
    monkeypatch.setattr(rwf, "_fetch_failed_job", lambda _r, _t, run_id: fetched.append(run_id))
    stats = rwf.record_reasons(_PollConn(), "r", "t", [_failure(i) for i in range(4)])
    assert stats["skipped"] == 3 and fetched == [0]


def test_the_cursor_is_written_before_the_reason_pass_runs() -> None:
    """Ordering, pinned. record_reasons does network I/O inside a job with
    `timeout-minutes: 5`; a job kill is not an exception, so running it before
    `_write_cursor` would put the poller's input-coverage guarantee behind a best-effort
    annotation. Reasons annotate rows already committed — they can wait."""
    src = inspect.getsource(rwf.main)
    assert src.index("_write_cursor(conn, new_cursor)") < src.index("record_reasons(conn")


# --- one poll, end to end over fakes ------------------------------------------------


def _poll_env(monkeypatch, *, ref: str | None) -> None:
    for name, value in (("GITHUB_REPOSITORY", "waiff/sreality"), ("GITHUB_TOKEN", "t"),
                        ("SUPABASE_DB_URL", "postgresql://poller")):
        monkeypatch.setenv(name, value)
    # CI's own GITHUB_REF_NAME names the PR branch, so every test pins it.
    if ref is None:
        monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    else:
        monkeypatch.setenv("GITHUB_REF_NAME", ref)


def _at(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def test_a_poll_stores_event_branch_reason_state_on_main_and_starts(
        monkeypatch, tmp_path: Path) -> None:
    import psycopg

    walk, images, ci = (".github/workflows/index_walk.yml", ".github/workflows/images.yml",
                        ".github/workflows/test.yml")
    runs = [
        _run(id=4242, path=walk, updated_at=_at(10), run_started_at=_at(15)),
        _run(id=4243, path=images, name="Images", event="push", conclusion="success",
             updated_at=_at(20), run_started_at=_at(30)),
        _run(id=4244, path=ci, name="CI", event="pull_request", head_branch="feature/x",
             updated_at=_at(25), run_started_at=_at(28)),
        _run(id=4245, path=".github/workflows/verify_pipeline.yml", name=MONITOR_WORKFLOW_NAME,
             conclusion="success", updated_at=_at(30), run_started_at=_at(35)),
    ]
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / walk).write_text("on: schedule\n")
    monkeypatch.setattr(rwf, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(rwf, "_fetch_runs_page", lambda _r, _t, page: runs if page == 1 else [])
    monkeypatch.setattr(rwf, "_fetch_workflow_states",
                        lambda _r, _t: {walk: "active", images: "disabled_manually"})
    monkeypatch.setattr(rwf, "_fetch_failed_job", lambda _r, _t, run_id: run_id)
    monkeypatch.setattr(rwf, "fetch_job_log",
                        lambda _r, _t, job_id, **_k: _FIXTURE_LOG.read_text(encoding="utf-8"))
    gone = ".github/workflows/recompute_mf_yields.yml"
    conn = _PollConn(claimed=(gone,))
    monkeypatch.setattr(psycopg, "connect", lambda *_a, **_k: conn)
    _poll_env(monkeypatch, ref="main")

    assert rwf.main() == 0

    (failure,) = conn.params("INSERT INTO workflow_failures")
    assert (failure[0], failure[3], failure[6], failure[7]) == (4242, "failure", "schedule", "main")
    assert conn.params("SET reason") == [(_FIXTURE_REASON, 4242)]
    health = {p["path"]: p for p in conn.params("INSERT INTO workflow_run_health")}
    assert set(health) == {walk, images}
    assert (health[images]["success_run_id"], health[walk]["success_run_id"]) == (4243, None)
    assert health[walk]["starts"] == [parse_ts(runs[0]["run_started_at"])]
    assert health[images]["starts"] == [], "a push has no cadence to be overdue against"
    assert health[walk]["kept"] == RECENT_STARTS_KEPT
    (facts,) = conn.params("UPDATE workflow_run_health")
    assert facts == ([images, walk, gone], [False, True, False],
                     ["disabled_manually", "active", None])
    assert conn.params("INSERT INTO app_settings")


def test_a_push_to_main_red_is_inserted_and_bears_its_row_with_no_start(
        monkeypatch, tmp_path: Path) -> None:
    import psycopg

    path = ".github/workflows/apply_migration.yml"
    runs = [_run(id=5151, path=path, name="Apply migration", event="push",
                 updated_at=_at(10), run_started_at=_at(12))]
    monkeypatch.setattr(rwf, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(rwf, "_fetch_runs_page", lambda _r, _t, page: runs if page == 1 else [])
    monkeypatch.setattr(rwf, "_fetch_workflow_states", lambda _r, _t: {path: "active"})
    monkeypatch.setattr(rwf, "_fetch_failed_job", lambda *_a: None)
    monkeypatch.setattr(rwf, "fetch_job_log", _must_not_fetch)
    conn = _PollConn()
    monkeypatch.setattr(psycopg, "connect", lambda *_a, **_k: conn)
    _poll_env(monkeypatch, ref="main")

    assert rwf.main() == 0

    (failure,) = conn.params("INSERT INTO workflow_failures")
    assert (failure[0], failure[6], failure[7]) == (5151, "push", "main")
    (health,) = conn.params("INSERT INTO workflow_run_health")
    assert (health["path"], health["success_run_id"], health["starts"]) == (path, None, [])


def test_a_branch_dispatch_writes_nothing(monkeypatch, caplog) -> None:
    import psycopg

    monkeypatch.setattr(psycopg, "connect", _must_not_fetch)
    monkeypatch.setattr(rwf, "_fetch_runs_page", _must_not_fetch)
    _poll_env(monkeypatch, ref="feature/health-platform")
    caplog.set_level(logging.INFO, logger="record_workflow_failures")
    assert rwf.main() == 0
    assert "not recorded: this run is from feature/health-platform, not main" in caplog.text

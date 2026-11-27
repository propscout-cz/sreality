"""Handshake-retry tests for scraper.db.connect / connect_session.

Hermetic: monkeypatches psycopg.connect and time.sleep so no real socket is
opened and no real delay elapses. Covers the Failure-A fix — a bounded retry on
a pooler handshake drop (OperationalError) — while a non-transient error (a
missing SUPABASE_DB_URL, a bug) still fails fast.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import time
from typing import Any

import psycopg
import pytest

from scraper import db, jobs


def _flaky_connect(fail_times: int, result: Any):
    """Return a fake psycopg.connect that raises OperationalError `fail_times`
    times, then returns `result`. Records the call count on `.calls`."""
    state = {"calls": 0}

    def _connect(url, **kwargs):
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        _connect.last_kwargs = kwargs
        _connect.last_url = url
        return result

    _connect.state = state
    return _connect


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def test_connect_retries_transient_then_succeeds(monkeypatch):
    sentinel = object()
    fake = _flaky_connect(fail_times=2, result=sentinel)
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")

    assert db.connect() is sentinel
    assert fake.state["calls"] == 3  # 2 failures + 1 success (default attempts=3)
    # kwargs preserved on the winning attempt.
    assert fake.last_kwargs["autocommit"] is True
    assert fake.last_kwargs["prepare_threshold"] is None
    assert fake.last_kwargs["keepalives"] == 1


def test_connect_reraises_after_exhausting_attempts(monkeypatch):
    fake = _flaky_connect(fail_times=99, result=object())
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")

    with pytest.raises(psycopg.OperationalError):
        db.connect()
    assert fake.state["calls"] == db._CONNECT_ATTEMPTS  # exactly the budget


def test_connect_custom_budget_is_honored(monkeypatch):
    """The API fail-fast path passes a smaller budget."""
    fake = _flaky_connect(fail_times=99, result=object())
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")

    with pytest.raises(psycopg.OperationalError):
        db.connect(attempts=2, retry_delay=0.01)
    assert fake.state["calls"] == 2


def test_connect_does_not_retry_non_transient(monkeypatch):
    """A bug (ProgrammingError, not OperationalError) fails fast — no retry."""
    calls = {"n": 0}

    def _connect(url, **kwargs):
        calls["n"] += 1
        raise psycopg.ProgrammingError("boom")

    monkeypatch.setattr(psycopg, "connect", _connect)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")

    with pytest.raises(psycopg.ProgrammingError):
        db.connect()
    assert calls["n"] == 1  # tried once, not retried


def test_connect_missing_url_fails_fast_without_touching_psycopg(monkeypatch):
    """A missing SUPABASE_DB_URL raises RuntimeError before psycopg.connect and
    is never retried (RuntimeError is not an OperationalError)."""
    calls = {"n": 0}
    monkeypatch.setattr(
        psycopg, "connect",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)

    with pytest.raises(RuntimeError):
        db.connect()
    assert calls["n"] == 0  # database_url() raised before psycopg.connect


def test_connect_session_retries_transient_then_succeeds(monkeypatch):
    sentinel = object()
    fake = _flaky_connect(fail_times=1, result=sentinel)
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setenv("SUPABASE_DB_SESSION_URL", "postgres://session:5432/db")

    assert db.connect_session() is sentinel
    assert fake.state["calls"] == 2
    # Session pooler: auto-prepare stays ON (no prepare_threshold override).
    assert "prepare_threshold" not in fake.last_kwargs


# --- #2660 (ruling C): a runner job outwaits a database restart ------------------------------------------------------


class _Conn:
    """A connection the pooler handed out; `answers` False is a pooler that cannot reach the database yet."""

    def __init__(self, answers: bool) -> None:
        self.answers, self.closed, self.probes = answers, False, 0

    def execute(self, sql: str) -> "_Conn":
        self.probes += 1
        if not self.answers:
            raise psycopg.errors.ConnectionFailure("Failed to connect to database: {:error, :timeout}")
        return self

    def close(self) -> None:
        self.closed = True


def _pooler(*answers: bool):
    conns = [_Conn(a) for a in answers]
    handed = iter(conns)
    return conns, lambda url, **kwargs: next(handed)


def test_a_job_connect_counts_only_once_the_database_answers(monkeypatch):
    """2026-11-11: broker_sweep's reconnect reached a pooler that could not reach the restarting database, and its next
    statement failed. A job's connect probes; a probe that fails is a failed attempt, retried a minute later."""
    conns, fake = _pooler(False, True)
    sleeps: list[float] = []
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")

    assert db.connect(**db.JOB_PATIENCE) is conns[1]
    assert conns[0].closed and not conns[1].closed
    assert sleeps == [db.JOB_PATIENCE["retry_delay"]]


def test_a_job_waits_three_minutes_for_the_database_then_fails(monkeypatch):
    """Ruling C: 3 × 60 s inside the job, the scheduler unchanged; a database down longer is a real incident."""
    conns, fake = _pooler(*[False] * db.JOB_PATIENCE["attempts"])
    sleeps: list[float] = []
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setenv("SUPABASE_DB_SESSION_URL", "postgres://session:5432/db")

    with pytest.raises(psycopg.errors.ConnectionFailure):
        db.connect_session(**db.JOB_PATIENCE)
    assert sleeps == [60.0, 60.0, 60.0]
    assert all(c.closed for c in conns)


def test_a_plain_connect_does_not_probe(monkeypatch):
    conns, fake = _pooler(False)
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")

    assert db.connect() is conns[0]
    assert conns[0].probes == 0


def test_a_resilient_step_rides_out_a_restart_through_its_patient_reconnect(monkeypatch):
    """The 11-11 path: a two-attempt step's connection dies in the restart; its reconnect reaches a pooler that cannot
    reach the database yet, waits a minute, and the replayed step completes on the connection that answers."""
    conns, fake = _pooler(False, True)
    sleeps: list[float] = []
    monkeypatch.setattr(psycopg, "connect", fake)
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(db.random, "random", lambda: 0.0)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://pooler:6543/db")
    dying = _Conn(answers=True)
    dying.broken = False

    def attribute(conn: Any) -> str:
        if conn is dying:
            dying.broken = True
            raise psycopg.errors.AdminShutdown("terminating connection due to administrator command")
        return "attributed"

    result, live = db.run_resilient(dying, attribute, reconnect=lambda: db.connect(**db.JOB_PATIENCE), attempts=2)

    assert (result, live) == ("attributed", conns[1])
    assert sleeps == [db._RESILIENT_BASE_DELAY, db.JOB_PATIENCE["retry_delay"]]


# Modules a DB-heavy job connects through besides its own entrypoint.
_JOB_CONNECT_MODULES = ("location_data.loader_db", "location_data.ruian_boundaries")


def connect_problems(source: str) -> list[str]:
    """Every connect/connect_session call in `source` (db.*, psycopg.*, bare) not given **JOB_PATIENCE."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in ("connect", "connect_session") and not any(
                kw.arg is None and "JOB_PATIENCE" in ast.unparse(kw.value) for kw in node.keywords):
            problems.append(f"line {node.lineno}: {ast.unparse(node)}")
    return problems


def test_every_db_heavy_runner_job_connects_with_the_jobs_patience():
    """#2660: a DB-heavy job's failure retries a day later (its own hour), so each one's first connect and every
    reconnect outwait a restart: db.JOB_PATIENCE, directly or through loader_db.open_loader_connection."""
    modules = sorted({j.argv[0] for j in jobs.runner_jobs() if j.db_heavy}) + list(_JOB_CONNECT_MODULES)
    assert len(modules) > len(_JOB_CONNECT_MODULES), "no DB-heavy runner job: the census reads nothing"
    found = {m: connect_problems(pathlib.Path(importlib.util.find_spec(m).origin).read_text()) for m in modules}
    assert found == {m: [] for m in modules}


def test_the_connect_census_fires():
    assert connect_problems("db.connect(url)") == ["line 1: db.connect(url)"]
    assert connect_problems("conn = connect(database_url())")
    assert connect_problems("psycopg.connect(url)")
    assert connect_problems("db.connect_session(url, attempts=4)")
    assert connect_problems("db.connect(url, **db.JOB_PATIENCE)\nconnect(**JOB_PATIENCE)  # db.connect(x)") == []

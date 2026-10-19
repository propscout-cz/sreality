"""Hermetic tests for scripts.verify_pipeline — the registry, the harness, the checks.

No DB: the check functions' SQL is exercised in production (and PREPAREd in CI's schema
sweep); here we test the registry metadata, due selection, the registry projection, the
unmeasured paths, persistence, and the pure status-derivation helpers.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import re
import pytest
from typing import Any

from pathlib import Path

from scripts.checks import platform as platform_checks
from scripts.checks.platform import check_migration_drift
from scripts.migration_objects import MIGRATION_OBJECT_PROBE_SQL, SAFE_IDENT, load_migrations
from scripts.verify_pipeline import (
    CHECKS,
    THRESHOLDS,
    Check,
    run_checks,
)

T = THRESHOLDS
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _registered(*keys: str) -> list[Check]:
    """The registered checks with these keys, in registry order."""
    return [c for c in CHECKS if c.key in keys]


def _by_key(key: str) -> Check:
    (check,) = _registered(key)
    return check


def _keys() -> list[str]:
    return [c.key for c in CHECKS]


def _hosted(host: str) -> list[str]:
    """The keys this host measures, in registry order."""
    return [c.key for c in CHECKS if c.host == host]


# The keys the worker's health lane measures (waves 3 and 4); every other key runs on GitHub.
_WORKER_KEYS = ["jobs", "capacity", "llm", "scraping", "publishing", "delisting", "coverage"]


def _stub(key: str, fn: Any, *, budget_s: float = 45.0, cls: str = "alarm",
          host: str = "github-actions", cadence_s: int = 3600) -> Check:
    """An unregistered check for exercising the harness itself."""
    return Check(key, fn, "platform", cls, host, cadence_s, key, budget_s=budget_s)


# --- broker_merge_suppression ------------------------------------------------


class _OneRow:
    """Minimal single-row connection, for the checks that are one O(1) scalar query."""

    def __init__(self, row: tuple[Any, ...]) -> None:
        self.row, self.executed = row, []

    def cursor(self) -> "_OneRow":
        return self

    def transaction(self) -> "_OneRow":
        return self

    def __enter__(self) -> "_OneRow":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)

    def fetchone(self) -> Any:
        return self.row


def test_broker_merge_suppression_is_ok_on_an_empty_rail() -> None:
    """The table starts empty and only grows by operator action, so "no rows" is the
    healthy steady state, not a missing signal."""
    from scripts.verify_pipeline import check_broker_merge_suppression

    out = check_broker_merge_suppression(_OneRow((0, 0, 0)), THRESHOLDS)
    assert out["status"] == "ok"
    assert out["details"] == {"active_suppressions": 0, "lifted": 0, "violations": 0}


def test_a_single_bypassed_suppression_fails_the_check() -> None:
    """THE invariant of the rail: an active suppression whose two identities sit
    under one broker means an operator NO was bypassed. There is no warn tier — the
    pair is either separated or it is not."""
    from scripts.verify_pipeline import check_broker_merge_suppression

    out = check_broker_merge_suppression(_OneRow((4, 2, 1)), THRESHOLDS)
    assert out["status"] == "fail" and out["value"] == 1
    assert out["details"] == {"active_suppressions": 4, "lifted": 2, "violations": 1}
    assert "bypassed" in out["message"]


def test_a_lifted_suppression_is_not_a_violation() -> None:
    """An explicit operator merge lifts the suppression and the two identities then
    legitimately share a broker — the query has to exclude lifted rows or every
    override would red the check."""
    from scripts.verify_pipeline import _BROKER_SUPPRESSION_SQL as sql
    from scripts.verify_pipeline import check_broker_merge_suppression

    assert "s.lifted_at is null" in sql
    assert sql.count("join broker_identities") == 2
    assert "lo.broker_id = hi.broker_id" in sql
    out = check_broker_merge_suppression(_OneRow((3, 9, 0)), THRESHOLDS)
    assert out["status"] == "ok" and out["details"]["lifted"] == 9


def test_broker_suppression_check_is_registered() -> None:
    """An unregistered check is dead code that never writes a row."""
    from scripts.verify_pipeline import check_broker_merge_suppression

    assert _by_key("broker_merge_suppression").fn is check_broker_merge_suppression


# --- run_checks isolation --------------------------------------------------


def test_one_erroring_check_is_unknown_and_does_not_abort_the_run() -> None:
    """A check that raised measured nothing: unknown with the error kept, never a fail
    that reads as a measured Problem, and the lane goes on to the next check."""

    def ok_check(conn: Any, thresholds: Any) -> dict[str, Any]:
        return {"check_key": "ok_one", "status": "ok", "value": 1, "details": {}}

    def boom_check(conn: Any, thresholds: Any) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    results = run_checks(conn=None, thresholds={},
                         checks=[_stub("ok_one", ok_check), _stub("boom", boom_check)])
    by_key = {r["check_key"]: r for r in results}
    assert by_key["ok_one"]["status"] == "ok"
    assert by_key["boom"]["status"] == "unknown"
    assert by_key["boom"]["details"]["errored"] is True
    assert "kaboom" in by_key["boom"]["details"]["error"]
    assert "boom" in by_key["boom"]["message"] and "kaboom" in by_key["boom"]["message"]


def test_run_checks_runs_exactly_the_checks_it_is_handed_in_order() -> None:
    def _one(key: str) -> Check:
        return _stub(key, lambda c, t: {"check_key": key, "status": "ok", "value": 0,
                                        "details": {}})

    assert run_checks(None, {}, checks=[]) == []
    out = run_checks(None, {}, checks=[_one("b"), _one("a")])
    assert [r["check_key"] for r in out] == ["b", "a"]


def test_every_result_carries_the_time_its_check_took() -> None:
    import time

    def _slow(conn: Any, thresholds: Any) -> dict[str, Any]:
        time.sleep(0.02)
        return {"check_key": "slow", "status": "ok", "value": 0}

    def _boom(conn: Any, thresholds: Any) -> dict[str, Any]:
        raise RuntimeError("x")

    slow, boom = run_checks(None, {}, checks=[_stub("slow", _slow), _stub("boom", _boom)])
    assert isinstance(slow["details"]["elapsed_ms"], int)
    assert slow["details"]["elapsed_ms"] >= 20
    assert isinstance(boom["details"]["elapsed_ms"], int)


# --- W0.4: a lane degrades gracefully -------------------------------------


def test_results_are_persisted_as_each_check_completes() -> None:
    """The W0.4 fix: a job killed mid-lane must keep the results it already computed.
    Before this, run_checks computed ALL results before persisting ANY, so a 5-min
    timeout wrote zero rows."""
    persisted: list[str] = []

    def slow_then_boom(conn: Any, thresholds: Any) -> dict[str, Any]:
        raise RuntimeError("the lane died here")

    checks = [
        _stub("first", lambda c, t: {"check_key": "first", "status": "ok", "value": 1,
                                     "details": {}}),
        _stub("second", lambda c, t: {"check_key": "second", "status": "ok", "value": 2,
                                      "details": {}}),
        _stub("third", slow_then_boom),
    ]
    results = run_checks(None, {}, checks=checks,
                         on_result=lambda r: persisted.append(r["check_key"]))
    # Every result reached the sink at the moment it completed, in order.
    assert persisted == ["first", "second", "third"]
    assert [r["status"] for r in results] == ["ok", "ok", "unknown"]


def test_a_failing_sink_does_not_end_the_lane() -> None:
    checks = [
        _stub("a", lambda c, t: {"check_key": "a", "status": "ok", "value": 1, "details": {}}),
        _stub("b", lambda c, t: {"check_key": "b", "status": "ok", "value": 1, "details": {}}),
    ]

    def _explode(_r: dict[str, Any]) -> None:
        raise RuntimeError("insert failed")

    results = run_checks(None, {}, checks=checks, on_result=_explode)
    assert [r["check_key"] for r in results] == ["a", "b"]


def test_a_timed_out_check_is_unknown_rather_than_failing() -> None:
    """"I could not measure this" is a different claim from "this is broken". Under DB
    pressure a fail-on-timeout rule manufactures a wall of false reds at exactly the
    moment the operator needs to read the real one."""

    def _cancelled(conn: Any, thresholds: Any) -> dict[str, Any]:
        raise RuntimeError("canceling statement due to statement timeout")

    (result,) = run_checks(None, {}, checks=[_stub("slow", _cancelled)])
    assert result["status"] == "unknown"
    assert result["details"]["timed_out"] is True
    assert result["details"]["budget_seconds"] == 45.0
    assert result["details"]["elapsed_seconds"] >= 0
    assert "Not measured" in result["message"]


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


def _on_the_clock(monkeypatch: Any) -> _Clock:
    """Point the harness's wall clock at a fake one."""
    import types

    import scripts.verify_pipeline as vp

    clock = _Clock()
    from scripts.checks import clock as lane_clock

    fake = types.SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(vp, "_time", fake)
    monkeypatch.setattr(lane_clock, "_time", fake)  # the lane's deadlines live in the clock module
    return clock


class _TimedConn:
    """Each statement takes its scripted seconds on the fake clock and, like Postgres, is
    cancelled once they pass the SET LOCAL statement_timeout armed before it."""

    def __init__(self, clock: _Clock, seconds: list[float]) -> None:
        self.clock = clock
        self.seconds = list(seconds)
        self.timeouts_ms: list[int] = []
        self.ran: list[str] = []

    def transaction(self) -> "_TimedConn":
        return self

    def cursor(self) -> "_TimedConn":
        return self

    def __enter__(self) -> "_TimedConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        import psycopg

        armed = re.fullmatch(r"SET LOCAL statement_timeout = '(\d+)ms'", sql)
        if armed:
            self.timeouts_ms.append(int(armed.group(1)))
            return
        took, limit = self.seconds.pop(0), self.timeouts_ms[-1] / 1000.0
        if took > limit:
            self.clock.now += limit
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        self.clock.now += took
        self.ran.append(sql)

    def fetchall(self) -> list[Any]:
        return []


def _statements(key: str, n: int, *, budget_s: float = 45.0) -> Check:
    """A check that sends `n` statements through the harness's bounded helper."""
    import scripts.verify_pipeline as vp

    def _fn(conn: Any, thresholds: Any) -> dict[str, Any]:
        for i in range(n):
            vp._fetchall(conn, f"select {i} -- {key}")
        return {"check_key": key, "status": "ok", "value": n, "details": {}}

    return _stub(key, _fn, budget_s=budget_s)


def test_a_check_spends_its_budget_once_across_all_its_statements(monkeypatch: Any) -> None:
    """One fixed timeout per statement let a check of N statements take N budgets
    (dual_write_parity ran p50 57 s on 45 s). Each statement now gets what the check has
    LEFT, never under 1 s (0 would switch the timeout off): the first of three statements
    takes 1.5 of a 2 s budget, so the second gets the 1 s floor, is cancelled there, and the
    check stops 0.5 s past its budget instead of running on to the third."""
    import scripts.verify_pipeline as vp

    conn = _TimedConn(_on_the_clock(monkeypatch), [1.5, 1.5, 0.1])
    checks = [_statements("three", 3, budget_s=2.0)]
    stored: list[dict[str, Any]] = []
    (result,) = run_checks(conn, {}, checks=checks, on_result=stored.append,
                           lane_budget_s=vp.lane_budget_for(checks))
    assert conn.timeouts_ms == [2000, 1000]
    assert conn.ran == ["select 0 -- three"]
    assert result["status"] == "unknown" and result["details"]["timed_out"] is True
    assert result["details"]["budget_seconds"] == 2.0
    assert result["details"]["elapsed_seconds"] == 2.5  # >= the 1.5 s it had spent
    assert stored == [result], "a timed-out check is stored: it ran and measured nothing"


def test_the_lane_remainder_still_caps_a_checks_deadline(monkeypatch: Any) -> None:
    """A check is owed its own budget only while the lane still has it: the second check
    below is declared 45 s but starts with 2 s of the lane left."""
    conn = _TimedConn(_on_the_clock(monkeypatch), [2.0, 0.5])
    out = run_checks(conn, {}, checks=[_statements("first", 1), _statements("second", 1)],
                     lane_budget_s=4.0)
    assert conn.timeouts_ms == [4000, 2000]
    assert [r["status"] for r in out] == ["ok", "ok"]


def test_a_budget_exhausted_lane_writes_no_row_for_the_checks_it_did_not_reach(
    monkeypatch: Any, caplog: Any,
) -> None:
    """A check the lane never reached measured nothing and stores nothing, so it stays due
    and the next run (on the worker, the next minute) measures it. Its old `not_run` row
    made it look measured and pushed its next measurement a whole cadence out."""
    import logging

    clock = _on_the_clock(monkeypatch)

    def _spends(key: str, seconds: float) -> Check:
        def _fn(conn: Any, thresholds: Any) -> dict[str, Any]:
            clock.now += seconds  # Python-side time no statement_timeout can cut short
            return {"check_key": key, "status": "ok", "value": 1, "details": {}}
        return _stub(key, _fn, budget_s=2.0)

    caplog.set_level(logging.WARNING, logger="verify_pipeline")
    stored: list[str] = []
    checks = [_spends("first", 6.5), _spends("second", 0.1), _spends("third", 0.1)]
    results = run_checks(None, {}, checks=checks, lane_budget_s=6.0,
                         on_result=lambda r: stored.append(r["check_key"]))
    assert [r["check_key"] for r in results] == stored == ["first"]
    assert "not reached, so still due: second, third" in caplog.text
    # A budget already spent by the time the loop starts reaches nothing at all.
    assert run_checks(None, {}, checks=checks, lane_budget_s=-1.0,
                      on_result=lambda r: stored.append(r["check_key"])) == []
    assert stored == ["first"]


def test_lane_budget_is_reset_after_a_run() -> None:
    """State leaking between runs would silently shrink a later lane's statement
    timeout to whatever the previous run had left."""
    import scripts.verify_pipeline as vp

    run_checks(None, {}, checks=[_stub("a", lambda c, t: {"check_key": "a", "status": "ok",
                                                          "value": 0, "details": {}})],
               lane_budget_s=5.0)
    assert vp._LANE.deadline is None and vp._LANE.check_deadline is None
    assert vp._LANE.statement_timeout_ms() == vp._DEFAULT_STATEMENT_TIMEOUT_MS


def test_each_check_runs_under_its_own_budget(monkeypatch: Any) -> None:
    """The outbound probe gets 120 s, the database checks 45 s: the statement timeout a
    check runs under is ITS budget, not one lane-wide number."""
    import scripts.verify_pipeline as vp

    _on_the_clock(monkeypatch)
    seen: dict[str, str] = {}

    def _peek(key: str) -> Any:
        def _fn(conn: Any, thresholds: Any) -> dict[str, Any]:
            seen[key] = vp._statement_timeout_clause()
            return {"check_key": key, "status": "ok", "value": 0}
        return _fn

    checks = [_stub("db", _peek("db")), _stub("probe", _peek("probe"), budget_s=120.0)]
    run_checks(None, {}, checks=checks, lane_budget_s=vp.lane_budget_for(checks))
    assert seen == {"db": "45000ms", "probe": "120000ms"}
    assert _by_key("outbound_url_conformance").budget_s == 120.0
    assert {c.budget_s for c in CHECKS[:-1]} == {vp._CHECK_BUDGET_S}


# --- the registry ------------------------------------------------------------


def test_every_registered_check_is_declared_once_with_its_metadata() -> None:
    from scripts import verify_pipeline as vp

    keys = _keys()
    assert len(keys) == len(set(keys)) == 20
    # Waves 3-4: the worker hosts the platform and portal checks; GitHub keeps what needs a
    # checkout (migration_drift) or an address that is not the scrapers' (the outbound probe).
    assert _hosted("realtime-worker") == _WORKER_KEYS
    assert len(_hosted("github-actions")) == 13 and not _hosted("pg_cron")
    for c in CHECKS:
        assert c.label.strip(), c.key
        assert c.cls in vp.CLASSES and c.cls in {"alarm", "gauge"}, c.key
        assert c.domain in vp.DOMAINS, c.key
        assert c.host in vp.HOSTS, c.key
        assert isinstance(c.cadence_s, int) and c.cadence_s > 0, c.key
        assert c.budget_s > 0, c.key
        assert c.owner_path == "" or c.owner_path.startswith("/"), c.key
        # a body wired under the wrong key would store its rows under another check's name
        assert c.fn.__name__ == f"check_{c.key}", c.key


def test_the_worker_heartbeat_is_the_watchers_to_judge_not_a_checks() -> None:
    """Wave 2: the pg_cron watcher's `worker` cell (migration 620) reads the heartbeat every
    5 min; the GitHub-hosted check sampled it every ~4.9 h, so it caught an outage of length
    D with a probability of about D / 4.9 h. The three hosts stay as they are."""
    from scripts import verify_pipeline as vp

    assert "worker_liveness" not in _keys()
    assert "worker_stale_fail_minutes" not in THRESHOLDS
    assert not hasattr(vp, "check_worker_liveness")
    assert vp.HOSTS == ("realtime-worker", "github-actions", "pg_cron")


_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_every_threshold_a_check_reads_is_declared_and_every_declared_one_is_read() -> None:
    """THRESHOLDS is the one home, with no override (wave 3): a key a check reads that it
    lacks is a KeyError, so that check errors into Not measured on every run, and a key no
    check names is a basis nobody is judged by (some are named in a tuple, then read)."""
    head, _, rest = (_SCRIPTS / "verify_pipeline.py").read_text().partition(
        "THRESHOLDS: dict[str, Any] = {")
    _declarations, _, tail = rest.partition("\n}\n")
    text = "\n".join([head, tail, (_SCRIPTS / "verify_outbound_urls.py").read_text(),
                      *(path.read_text() for path in sorted((_SCRIPTS / "checks").glob("*.py")))])
    read = set(re.findall(
        r"""(?:thresholds|THRESHOLDS)(?:\[|\.get\()\s*["']([A-Za-z0-9_]+)["']""", text))
    assert sorted(read - set(THRESHOLDS)) == [], "read but not declared"
    named = {key for key in THRESHOLDS if re.search(rf"""["']{key}["']""", text)}
    assert sorted(set(THRESHOLDS) - named) == [], "declared but named by no check"


def test_the_capacity_reference_is_blessed_at_most_90_days_back() -> None:
    """Design § 4: the sticky reference is a blessed date at most 90 d back, so this rail goes red
    the day it expires and forces the dated re-blessing PR (the lowest weekly p50 since an old
    date judges today's jobs against a workload and an instance they no longer have)."""
    blessed = THRESHOLDS["CAPACITY_REFERENCE_FROM"]
    today = _dt.datetime.now(_dt.timezone.utc).date()
    assert isinstance(blessed, _dt.date) and not isinstance(blessed, _dt.datetime)
    assert blessed <= today, f"CAPACITY_REFERENCE_FROM {blessed} is in the future"
    assert (today - blessed).days <= 90, (
        f"CAPACITY_REFERENCE_FROM {blessed} is {(today - blessed).days} days back, past 90: "
        "re-bless it to a measured quiet week's first day in a dated PR")


def test_the_gauges_are_the_ruled_standing_conditions_and_the_rest_are_alarms() -> None:
    """Q2 (2026-10-18): these are standing conditions, read as gauges (wave 4's coverage in
    walk_coverage's place, delist_flip_refused's deferrals now an arm of the delisting alarm);
    every other key — the quiet regression detectors included — stays an alarm."""
    assert {c.key for c in CHECKS if c.cls == "gauge"} == {
        "location_town_coverage", "coverage", "ppm2_basis_floor_share",
        "area_vs_usable_divergence", "outbound_url_coverage", "text_extraction_lag",
        "floor_convention",
    }


def test_each_check_keeps_the_cadence_of_the_lane_it_ran_in() -> None:
    """The worker's checks run every 10 minutes, capacity every 30 and the coverage gauge
    hourly; on GitHub broker_merge_suppression and migration_drift run hourly, the URL probe
    weekly, everything else 6-hourly."""
    assert {k: _by_key(k).cadence_s for k in _WORKER_KEYS} == {
        "jobs": 600, "capacity": 1_800, "llm": 600, "scraping": 600, "publishing": 600,
        "delisting": 600, "coverage": 3_600}
    github = set(_hosted("github-actions"))
    hourly = {k for k in github if _by_key(k).cadence_s == 3_600}
    assert hourly == {"broker_merge_suppression", "migration_drift"}
    assert _by_key("outbound_url_conformance").cadence_s == 604_800
    rest = github - hourly - {"outbound_url_conformance"}
    assert {_by_key(k).cadence_s for k in rest} == {21_600}


def test_staleness_budget_is_three_cadences_or_the_cadence_plus_githubs_delay() -> None:
    """GitHub delivers a cron up to ~13 h late, so its keys get cadence + 18 h; the worker
    and pg_cron keep time and get three cadences."""
    assert _stub("a", None, host="github-actions", cadence_s=3_600).staleness_budget \
        == _dt.timedelta(hours=19)
    assert _stub("b", None, host="realtime-worker", cadence_s=600).staleness_budget \
        == _dt.timedelta(minutes=30)
    assert _stub("c", None, host="pg_cron", cadence_s=300).staleness_budget \
        == _dt.timedelta(minutes=15)


# --- due selection -------------------------------------------------------------


class _DueConn:
    """Answers the newest-run_at-per-key read from a {key: run_at} map; a newest row older than
    its key's `declared` stamp is dropped where the statement guards on declared_at."""

    def __init__(self, newest: dict[str, Any],
                 declared: dict[str, _dt.datetime] | None = None) -> None:
        self._newest = newest
        self._declared = declared or {}
        self.executed: list[tuple[str, Any]] = []
        self._rows: list[tuple[Any, ...]] = []

    def cursor(self) -> "_DueConn":
        return self

    def __enter__(self) -> "_DueConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        guarded = "r.run_at >= coalesce(h.declared_at" in " ".join(sql.split())

        def newest(key: str) -> Any:
            at, floor = self._newest.get(key), self._declared.get(key)
            if guarded and at is not None and floor is not None and at < floor:
                return None  # the newest row predates the registration, so every row does
            return at

        self._rows = [(k, newest(k)) for k in params["keys"]]

    def fetchall(self) -> Any:
        return self._rows


_NOW = _dt.datetime(2026, 10, 18, 12, 0, tzinfo=_dt.timezone.utc)


def test_a_check_with_no_result_is_due() -> None:
    from scripts.verify_pipeline import due_checks

    for host in ("github-actions", "realtime-worker"):
        assert [c.key for c in due_checks(_DueConn({}), host, _NOW)] == _hosted(host)


def test_a_check_is_due_once_its_newest_result_is_a_cadence_old() -> None:
    from scripts.verify_pipeline import due_checks

    def ago(**kw: float) -> _dt.datetime:
        return _NOW - _dt.timedelta(**kw)

    conn = _DueConn({
        "migration_drift": ago(minutes=30),           # hourly, half a cadence old: not due
        "broker_merge_suppression": ago(minutes=61),  # hourly, over a cadence old: due
        "field_fill_matrix": ago(hours=5),    # 6-hourly: not due
        "floor_convention": ago(hours=7),     # 6-hourly: due
        "outbound_url_conformance": ago(days=6),  # weekly: not due
    })
    due = [c.key for c in due_checks(conn, "github-actions", _NOW)]
    assert "migration_drift" not in due and "field_fill_matrix" not in due
    assert "outbound_url_conformance" not in due
    assert "broker_merge_suppression" in due and "floor_convention" in due
    assert due == [k for k in _keys() if k in due], "registry order is the run order"
    assert len(due) == len(_hosted("github-actions")) - 3
    (sql, params), = conn.executed  # one round trip for the whole host
    assert "max(r.run_at)" in sql and "unnest(%(keys)s::text[])" in sql
    assert params["keys"] == _hosted("github-actions")
    worker = _DueConn({
        "capacity": ago(minutes=20),  # 30-minute: not due
        "scraping": ago(minutes=11),  # 10-minute: due
        "coverage": ago(minutes=30),  # hourly: not due
    })
    due = [c.key for c in due_checks(worker, "realtime-worker", _NOW)]
    assert "capacity" not in due and "coverage" not in due and "scraping" in due
    assert len(due) == len(_hosted("realtime-worker")) - 2


def test_a_check_is_due_a_slack_before_its_cadence_so_an_early_run_still_measures_it() -> None:
    """Every row carries its run's start, so the next hourly run, started a minute earlier in
    its slot, found a 59-minute-old row not due: an hourly check was measured every second
    run. The slack is a tenth of the cadence, at most 15 min."""
    from scripts.verify_pipeline import due_checks

    def due(key: str, **age: float) -> bool:
        conn = _DueConn({key: _NOW - _dt.timedelta(**age)})
        return key in [c.key for c in due_checks(conn, "github-actions", _NOW)]

    assert due("migration_drift", minutes=59)
    assert due("migration_drift", minutes=54)   # 60 min less the 6-min slack, inclusive
    assert not due("migration_drift", minutes=50)
    assert due("field_fill_matrix", hours=5, minutes=45)
    assert not due("field_fill_matrix", hours=5, minutes=44)
    assert due("outbound_url_conformance", days=7, minutes=-10)
    assert not due("outbound_url_conformance", days=7, minutes=-16)
    assert {c.cadence_s - c.due_after_s for c in CHECKS if c.host == "github-actions"} \
        == {360, 900}
    # The worker's 60 s tick: a 10-minute check is due after 9 minutes, a 30-minute one 27,
    # the hourly gauge 54.
    assert {c.cadence_s - c.due_after_s for c in CHECKS if c.host == "realtime-worker"} \
        == {60, 180, 360}


def test_rows_from_before_a_keys_registration_never_make_it_not_due() -> None:
    """The page and the ladder ignore rows older than declared_at, so a key whose newest row
    predates its registration read Not measured for a whole cadence before the lanes measured
    it: the weekly URL probe, last measured before wave 1, among them."""
    from scripts.verify_pipeline import due_checks

    registered = _NOW - _dt.timedelta(minutes=1)  # project_registry runs first in main()
    conn = _DueConn({"outbound_url_conformance": _NOW - _dt.timedelta(days=2),
                     "field_fill_matrix": _NOW - _dt.timedelta(hours=3),
                     "broker_merge_suppression": _NOW - _dt.timedelta(seconds=30)},
                    declared=dict.fromkeys(
                        ("outbound_url_conformance", "field_fill_matrix",
                         "broker_merge_suppression"),
                        registered))
    due = [c.key for c in due_checks(conn, "github-actions", _NOW)]
    assert "outbound_url_conformance" in due and "field_fill_matrix" in due
    assert "broker_merge_suppression" not in due, \
        "a row written since the registration still counts"
    (sql, _params), = conn.executed
    assert "left join health_checks h on h.key = k.key" in " ".join(sql.split())


def test_due_selection_never_reaches_another_hosts_checks() -> None:
    """The canary never measures the worker's checks and the health lane never GitHub's: each
    host's one read names only its own keys."""
    from scripts.verify_pipeline import due_checks

    hosts = ("github-actions", "realtime-worker")
    for host, other in (hosts, hosts[::-1]):
        conn = _DueConn({})
        due = [c.key for c in due_checks(conn, host, _NOW)]
        assert not set(due) & set(_hosted(other)), host
        assert conn.executed[0][1]["keys"] == _hosted(host), host
    assert due_checks(_DueConn({}), "pg_cron", _NOW) == []


# --- the registry projection -----------------------------------------------------


class _HealthChecksConn:
    """An in-memory health_checks table behind project_registry's three statements."""

    def __init__(self, table: dict[str, dict[str, Any]] | None = None) -> None:
        self.table = {k: dict(v) for k, v in (table or {}).items()}
        self.in_transaction = False
        self.rowcount = -1
        self._rows: list[tuple[Any, ...]] = []

    def transaction(self) -> Any:
        conn = self

        class _Txn:
            def __enter__(self) -> None:
                conn.in_transaction = True

            def __exit__(self, *exc: Any) -> bool:
                conn.in_transaction = False
                return False

        return _Txn()

    def cursor(self) -> "_HealthChecksConn":
        return self

    def __enter__(self) -> "_HealthChecksConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        assert self.in_transaction, "the projection is one transaction"
        head = " ".join(sql.split())
        if head.startswith("select key, host, cadence_s, declared_at from health_checks"):
            self._rows = [(k, r["host"], r["cadence_s"], r["declared_at"])
                          for k, r in self.table.items() if k in params["keys"]]
        elif head.startswith("delete from health_checks where host = %(host)s"):
            gone = [k for k, r in self.table.items()
                    if r["host"] == params["host"] and k not in params["keys"]]
            for k in gone:
                del self.table[k]
            self.rowcount = len(gone)
        else:  # pragma: no cover - a statement the projection never sends
            raise AssertionError(sql)

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        assert self.in_transaction and "on conflict (key) do update" in sql
        for row in rows:
            self.table[row["key"]] = dict(row)

    def fetchall(self) -> Any:
        return self._rows


@pytest.mark.parametrize("host", ["github-actions", "realtime-worker"])
def test_projection_inserts_every_check_of_its_host_with_its_metadata(host: str) -> None:
    """Each host projects only its own rows, sorted by their place in the whole registry; a
    GitHub key may age its cadence plus 18 h before it reads stale, a worker key 3 cadences."""
    from scripts.verify_pipeline import project_registry

    conn = _HealthChecksConn()
    assert project_registry(conn, host, _NOW) == {"upserted": len(_hosted(host)), "deleted": 0}
    assert list(conn.table) == _hosted(host)
    for sort, c in enumerate(CHECKS):
        if c.host != host:
            continue
        row = conn.table[c.key]
        assert (row["domain"], row["class"], row["host"], row["cadence_s"]) == (
            c.domain, c.cls, host, c.cadence_s)
        assert (row["label"], row["unit"], row["owner_path"], row["sort"]) == (
            c.label, c.unit, c.owner_path, sort)
        stale_s = c.cadence_s + 18 * 3_600 if host == "github-actions" else 3 * c.cadence_s
        assert row["staleness_budget"] == _dt.timedelta(seconds=stale_s)
        assert row["declared_at"] == _NOW


def test_projection_keeps_declared_at_unless_the_host_or_cadence_changed() -> None:
    from scripts.verify_pipeline import project_registry

    then = _NOW - _dt.timedelta(days=3)
    conn = _HealthChecksConn({
        "broker_merge_suppression": {"host": "github-actions", "cadence_s": 3_600,
                                     "declared_at": then},
        # wave 3 moved migration_drift from 6-hourly to hourly
        "migration_drift": {"host": "github-actions", "cadence_s": 21_600, "declared_at": then},
        "floor_convention": {"host": "realtime-worker", "cadence_s": 21_600,
                             "declared_at": then},
    })
    project_registry(conn, "github-actions", _NOW)
    assert conn.table["broker_merge_suppression"]["declared_at"] == then  # unchanged: kept
    assert conn.table["migration_drift"]["declared_at"] == _NOW           # cadence moved
    assert conn.table["floor_convention"]["declared_at"] == _NOW          # host moved
    assert conn.table["floor_convention"]["host"] == "github-actions"
    project_registry(conn, "github-actions", _NOW + _dt.timedelta(hours=1))
    assert conn.table["migration_drift"]["declared_at"] == _NOW, "a re-projection is a no-op"


def test_projection_retires_only_its_own_hosts_undeclared_keys() -> None:
    from scripts.verify_pipeline import project_registry

    conn = _HealthChecksConn({
        "outbound_url_parity": {"host": "github-actions", "cadence_s": 604_800,
                                "declared_at": _NOW},
        "health_lane": {"host": "realtime-worker", "cadence_s": 600, "declared_at": _NOW},
        "liveness": {"host": "pg_cron", "cadence_s": 300, "declared_at": _NOW},
    })
    assert project_registry(conn, "github-actions", _NOW)["deleted"] == 1
    assert "outbound_url_parity" not in conn.table
    assert {"health_lane", "liveness"} <= set(conn.table)
    assert project_registry(conn, "realtime-worker", _NOW)["deleted"] == 1
    assert "health_lane" not in conn.table and "liveness" in conn.table
    assert set(_hosted("github-actions")) <= set(conn.table), "the lane leaves GitHub's rows be"


def test_the_first_projection_from_main_retires_the_eight_legacy_keys() -> None:
    """Wave 3's jobs, capacity and llm replace eight GitHub keys. Their rows go at the canary's
    first projection from main, and the ladder then closes their open incidents as retired,
    with no notice (an open llm_errors incident included)."""
    from scripts.verify_pipeline import project_registry

    legacy = ("worker_lane_stall", "broker_resolution_freshness", "workflow_poller_liveness",
              "db_saturation", "long_open_transaction", "llm_errors", "llm_burn_rate",
              "dual_write_parity")
    conn = _HealthChecksConn({key: {"host": "github-actions", "cadence_s": 3_600,
                                    "declared_at": _NOW} for key in legacy})
    assert project_registry(conn, "github-actions", _NOW)["deleted"] == 8
    assert not set(legacy) & set(conn.table)


def test_the_first_projection_from_main_retires_the_seven_portal_keys() -> None:
    """Wave 4's scraping, publishing, delisting and coverage replace seven GitHub keys. Their rows
    go at the canary's first projection from main, the ladder closes any incident of theirs as
    retired, with no notice, and the worker projects the four on its first health pass."""
    from scripts.verify_pipeline import project_registry

    legacy = ("acquisition_lag", "property_maintenance", "walk_coverage", "walk_end_streak",
              "delist_flip_refused", "false_delist_share", "sreality_image_template")
    assert not set(legacy) & set(_keys())
    conn = _HealthChecksConn({key: {"host": "github-actions", "cadence_s": 21_600,
                                    "declared_at": _NOW} for key in legacy})
    assert project_registry(conn, "github-actions", _NOW)["deleted"] == 7
    assert not set(legacy) & set(conn.table)
    assert project_registry(conn, "realtime-worker", _NOW) == {
        "upserted": len(_WORKER_KEYS), "deleted": 0}
    assert {"scraping", "publishing", "delisting", "coverage"} <= set(conn.table)


# --- persistence --------------------------------------------------------------------


class _InsertConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> "_InsertConn":
        return self

    def __enter__(self) -> "_InsertConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.calls.append((sql, params))


def test_a_stored_result_keeps_its_message_and_cells_inside_details() -> None:
    """The message used to be dropped on the floor: the page and the ladder read it here."""
    from scripts.verify_pipeline import insert_result

    cells = [{"subject": "bazos", "portal": "bazos", "status": "fail", "value": 3,
              "unit": "h", "message": "bazos 3 h behind", "facts": {}}]
    details = {"cells": cells, "elapsed_ms": 12}
    conn = _InsertConn()
    insert_result(conn, {"check_key": "k", "status": "fail", "value": 3.0,
                         "details": details, "message": "bazos is 3 h behind"}, _NOW)
    (sql, params), = conn.calls
    assert "INSERT INTO pipeline_check_results" in sql
    assert params[:4] == (_NOW, "k", "fail", 3.0)
    stored = json.loads(params[4])
    assert stored == {"cells": cells, "elapsed_ms": 12, "message": "bazos is 3 h behind"}
    assert "message" not in details, "the check's own dict is not mutated"


def test_a_stored_result_without_a_message_adds_none() -> None:
    from scripts.verify_pipeline import insert_result

    conn = _InsertConn()
    insert_result(conn, {"check_key": "k", "status": "ok", "value": None, "details": None},
                  _NOW)
    assert json.loads(conn.calls[0][1][4]) == {}


# --- run_due: what is due, alarms first, stored as it lands ---------------------------------


class _ResultsConn:
    """An in-memory pipeline_check_results behind due_checks' read and insert_result's write."""

    def __init__(self, newest: dict[str, _dt.datetime] | None = None) -> None:
        self.rows: list[tuple[str, _dt.datetime]] = list((newest or {}).items())
        self._out: list[tuple[Any, ...]] = []

    def cursor(self) -> "_ResultsConn":
        return self

    def __enter__(self) -> "_ResultsConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("INSERT INTO pipeline_check_results"):
            self.rows.append((params[1], params[0]))
            return
        assert "max(r.run_at)" in sql, sql
        self._out = [(k, max((at for key, at in self.rows if key == k), default=None))
                     for k in params["keys"]]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._out


def _logging_check(key: str, log: list[str], *, cls: str = "alarm",
                   host: str = "github-actions") -> Check:
    """A stub check that notes when it runs."""
    def _fn(conn: Any, thresholds: Any) -> dict[str, Any]:
        log.append(f"run:{key}")
        return {"check_key": key, "status": "ok", "value": 0, "details": {}}

    return _stub(key, _fn, cls=cls, host=host)


def test_the_harness_projects_only_hosts_that_declare_a_check(monkeypatch: Any) -> None:
    """pg_cron's registry row is the watcher's own (migration 620): projecting that host would
    retire it, and the ladder would silently close every open liveness incident as retired."""
    from scripts import verify_pipeline as vp

    assert vp._HARNESS_HOSTS == ("github-actions", "realtime-worker")
    with pytest.raises(ValueError, match="pg_cron"):
        vp.project_registry(object(), "pg_cron", _NOW)
    # main() is GitHub's runner only: the worker's health lane calls run_due itself.
    for host in ("pg_cron", "realtime-worker"):
        with pytest.raises(SystemExit):
            _run_main(monkeypatch, ["--host", host], [])


def test_run_due_measures_only_the_due_checks_alarms_before_gauges(monkeypatch: Any) -> None:
    """Alarms are what a red means, so a pass that runs short spends its budget on them;
    inside each class the registry order is the priority."""
    import scripts.verify_pipeline as vp

    log: list[str] = []
    monkeypatch.setattr(vp, "CHECKS", (
        _logging_check("gauge_a", log, cls="gauge"), _logging_check("alarm_a", log),
        _logging_check("fresh", log), _logging_check("gauge_b", log, cls="gauge"),
        _logging_check("alarm_b", log), _logging_check("worker", log, host="realtime-worker"),
    ))
    conn = _ResultsConn({"fresh": _NOW - _dt.timedelta(minutes=10)})
    out = vp.run_due(conn, {}, "github-actions", now=_NOW)
    assert log == ["run:alarm_a", "run:alarm_b", "run:gauge_a", "run:gauge_b"]
    assert [r["check_key"] for r in out] == ["alarm_a", "alarm_b", "gauge_a", "gauge_b"]


def test_run_due_hands_each_result_on_the_moment_it_completes(monkeypatch: Any) -> None:
    import scripts.verify_pipeline as vp

    log: list[str] = []
    monkeypatch.setattr(vp, "CHECKS", (_logging_check("a", log), _logging_check("b", log)))

    def _sink(conn: Any, result: dict[str, Any], run_at: _dt.datetime) -> None:
        assert run_at == _NOW, "every row carries its run's start"
        log.append(f"stored:{result['check_key']}")

    vp.run_due(_ResultsConn(), {}, "github-actions", now=_NOW, on_result=_sink)
    assert log == ["run:a", "stored:a", "run:b", "stored:b"]


def test_run_due_caps_the_pass_at_the_lane_budget_it_is_given(monkeypatch: Any) -> None:
    """The worker's health lane runs it under a 360 s pass (300 s in wave 3); by default the due budgets sum."""
    import scripts.verify_pipeline as vp

    log: list[str] = []
    monkeypatch.setattr(vp, "CHECKS", (_logging_check("a", log), _logging_check("b", log)))
    seen: list[float] = []
    real = vp.run_checks

    def _spy(conn: Any, thresholds: Any, **kw: Any) -> Any:
        seen.append(kw["lane_budget_s"])
        return real(conn, thresholds, **kw)

    monkeypatch.setattr(vp, "run_checks", _spy)
    vp.run_due(_ResultsConn(), {}, "github-actions", now=_NOW)
    vp.run_due(_ResultsConn(), {}, "github-actions", now=_NOW, lane_budget_s=10.0)
    assert seen == [2 * vp._CHECK_BUDGET_S, 10.0]


def test_a_restarted_lane_measures_nothing_twice(monkeypatch: Any) -> None:
    """Due-ness is read from the stored results, never held in the process, so a lane that
    restarts (a deploy, a crash, the next 60 s tick) re-measures nothing it already measured
    this cadence, and measures it again once the cadence has passed."""
    import scripts.verify_pipeline as vp

    log: list[str] = []
    monkeypatch.setattr(vp, "CHECKS", (_logging_check("a", log), _logging_check("b", log)))
    conn = _ResultsConn()
    assert [r["check_key"] for r in vp.run_due(conn, {}, "github-actions", now=_NOW)] == ["a", "b"]
    assert vp.run_due(conn, {}, "github-actions", now=_NOW + _dt.timedelta(minutes=1)) == []
    assert log == ["run:a", "run:b"]
    vp.run_due(conn, {}, "github-actions", now=_NOW + _dt.timedelta(hours=1))
    assert log == ["run:a", "run:b", "run:a", "run:b"]


def test_a_check_the_budget_did_not_reach_is_due_on_the_next_run(monkeypatch: Any) -> None:
    import scripts.verify_pipeline as vp

    clock = _on_the_clock(monkeypatch)
    log: list[str] = []

    def _overruns(conn: Any, thresholds: Any) -> dict[str, Any]:
        clock.now += 100.0  # past the whole pass budget, outside any statement
        return {"check_key": "slow", "status": "ok", "value": 0, "details": {}}

    monkeypatch.setattr(vp, "CHECKS", (_stub("slow", _overruns), _logging_check("late", log)))
    conn = _ResultsConn()
    assert [r["check_key"] for r in vp.run_due(conn, {}, "github-actions", now=_NOW)] == ["slow"]
    assert [key for key, _ in conn.rows] == ["slow"] and log == []
    later = _NOW + _dt.timedelta(minutes=1)
    assert [c.key for c in vp.due_checks(conn, "github-actions", later)] == ["late"]
    assert [r["check_key"] for r in vp.run_due(conn, {}, "github-actions", now=later)] == ["late"]


# --- main(): one flow, and its exit code never reflects a check ----------------------------


class _MainConn:
    def __enter__(self) -> "_MainConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _run_main(monkeypatch: Any, argv: list[str], due: list[Check], *,
              ref: str | None = None) -> tuple[int, dict[str, Any]]:
    """main() over fakes; `ref` is GITHUB_REF_NAME (None: unset, as on a local run)."""
    import sys

    import scripts.verify_pipeline as vp

    seen: dict[str, Any] = {"order": [], "inserted": []}

    def _project(conn: Any, host: str, now: Any) -> dict[str, int]:
        seen["order"].append(f"project:{host}")
        return {"upserted": len(due), "deleted": 0}

    def _due(conn: Any, host: str, now: Any) -> list[Check]:
        seen["order"].append(f"due:{host}")
        return due

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://unused")
    # CI's own GITHUB_REF_NAME names the PR branch, so every test pins it.
    if ref is None:
        monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    else:
        monkeypatch.setenv("GITHUB_REF_NAME", ref)
    monkeypatch.setattr(vp, "connect", lambda: _MainConn())
    monkeypatch.setattr(vp, "project_registry", _project)
    monkeypatch.setattr(vp, "due_checks", _due)
    monkeypatch.setattr(vp, "insert_result",
                        lambda conn, r, run_at: seen["inserted"].append(r["check_key"]))
    monkeypatch.setattr(sys, "argv", ["verify_pipeline", *argv])
    return vp.main(), seen


def _returning(key: str, status: str, *, cls: str = "alarm") -> Check:
    return _stub(key, lambda c, t: {"check_key": key, "status": status, "value": 1,
                                    "details": {}, "message": f"{key} {status}"}, cls=cls)


def test_main_projects_the_registry_then_measures_and_stores_the_due_checks(
    monkeypatch: Any, caplog: Any,
) -> None:
    import logging

    import scripts.verify_pipeline as vp

    caplog.set_level(logging.INFO, logger="verify_pipeline")
    handed: list[Any] = []

    def _reads(conn: Any, thresholds: Any) -> dict[str, Any]:
        handed.append(thresholds)
        return {"check_key": "t", "status": "ok", "value": 1, "details": {}}

    code, seen = _run_main(monkeypatch, [], [_returning("a", "ok"), _returning("b", "warn"),
                                             _stub("t", _reads)])
    assert code == 0
    assert seen["order"] == ["project:github-actions", "due:github-actions"]
    assert seen["inserted"] == ["a", "b", "t"]
    assert "CHECK a status=ok value=1" in caplog.text  # failure_signature reads this line
    assert len(handed) == 1 and handed[0] is vp.THRESHOLDS, "no run-time override"


def test_only_a_run_from_main_projects_the_registry(monkeypatch: Any, caplog: Any) -> None:
    """A dispatch from a branch runs that branch's CHECKS: projecting them would delete
    main's keys from health_checks (the page serves registry rows only) until main's next
    run re-declared them. It still measures and stores."""
    import logging

    caplog.set_level(logging.INFO, logger="verify_pipeline")
    code, seen = _run_main(monkeypatch, [], [_returning("a", "ok")], ref="feature/health-w2")
    assert code == 0
    assert seen["order"] == ["due:github-actions"] and seen["inserted"] == ["a"]
    assert "not projected: this run is from feature/health-w2, not main" in caplog.text
    _, seen = _run_main(monkeypatch, [], [_returning("a", "ok")], ref="main")
    assert seen["order"] == ["project:github-actions", "due:github-actions"]


def test_main_dry_run_projects_nothing_and_stores_nothing(monkeypatch: Any) -> None:
    code, seen = _run_main(monkeypatch, ["--dry-run"], [_returning("a", "fail")])
    assert code == 0
    assert seen["order"] == ["due:github-actions"] and seen["inserted"] == []


def test_no_measured_status_reds_the_run(monkeypatch: Any) -> None:
    """The canary's assertions (scripts/health_canary.py) are the one way a monitoring run
    goes red; a measured Problem is the ladder's to tell. The exit flag is gone, so passing
    it is refused rather than silently ignored."""
    gauge = _returning("g", "fail", cls="gauge")
    assert _run_main(monkeypatch, [], [gauge, _returning("a", "fail")])[0] == 0
    assert _run_main(monkeypatch, [], [_returning("a", "warn"),
                                       _returning("b", "unknown")])[0] == 0
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, ["--exit-nonzero-on-fail"], [_returning("a", "fail")])


# --- per-m2 measure plausibility (W9) --------------------------------------
#
# Every cell below is a LIVE measurement of production taken 2026-08-25, before the
# W2 backfill healed either defect. The point of the wave is that the existing health
# surfaces are structurally blind to both — data_quality_by_source tests 29 fields for
# IS NOT NULL and both defects produce 100% non-NULL values — so these tests pin that
# the new checks see what the null-checks cannot, and equally that they stay quiet on
# the eight portals that were fine all along.


_MEASURE_CHECK_KEYS = {
    "ppm2_median_shift", "ppm2_basis_floor_share", "area_vs_usable_divergence",
    "ppm2_measure_coverage",
}


def _cell(
    source: str, cat: str | None, typ: str | None, **kw: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source": source, "category_main": cat, "category_type": typ,
        "price_per_m2_basis": None, "n_active": 1000.0,
        "median_area_m2": None, "median_usable_area": None, "median_price_per_m2": None,
        "n_floor_eligible": None, "floor_null_share": None,
        "n_floor_eligible_7d": None, "floor_null_share_7d": None,
        "n_area_pairs": None, "area_divergence_share": None,
        "n_area_pairs_7d": None, "area_divergence_share_7d": None,
        # Coverage + median SUPPORT. The defaults describe a cell whose measure
        # resolves everywhere, so a test that says nothing about coverage keeps
        # scoring the axis it is actually about.
        "n_area_valued": 1000.0, "n_ppm2_valued": 1000.0, "n_active_7d": 200.0,
        "measure_input_gap_share": 0.0, "measure_input_gap_share_7d": 0.0,
        # W21's derived-sum arm. The default is a cell with no three-area rows, so a
        # test that says nothing about it leaves that arm unscored rather than clean.
        "n_estate_triples": 0.0, "estate_sum_share": None,
    }
    base.update(kw)
    return base


class _PlausibilityConn:
    """Serves measure_plausibility_by_source rows + the week-old baseline row."""

    def __init__(
        self, cells: list[dict[str, Any]],
        baseline: dict[str, Any] | None = None,
        history_days: float | None = None,
    ) -> None:
        self._cells, self._baseline, self._history = cells, baseline, history_days
        self.executed: list[str] = []
        self._rows: list[tuple[Any, ...]] = []
        self._one: tuple[Any, ...] | None = None

    def cursor(self) -> "_PlausibilityConn":
        return self

    def transaction(self) -> "_PlausibilityConn":
        return self

    def __enter__(self) -> "_PlausibilityConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("SET LOCAL"):
            return
        self.executed.append(sql)
        if "measure_plausibility_by_source" in sql:
            from scripts.verify_pipeline import _PLAUSIBILITY_COLS

            self._rows = [tuple(c[k] for k in _PLAUSIBILITY_COLS) for c in self._cells]
        elif "baseline_cells" in sql:
            self._one = (self._baseline, self._history)

    def fetchall(self) -> Any:
        return self._rows

    def fetchone(self) -> Any:
        return self._one


def _fresh(conn: "_PlausibilityConn") -> "_PlausibilityConn":
    """The per-run cache is cleared by run_checks; a direct check call must clear it too."""
    import scripts.verify_pipeline as vp

    vp._PLAUSIBILITY_CACHE.clear()
    return conn


# Live 2026-08-25, dum/prodej, every portal that populates both area fields. mmreality
# wrote the PLOT into the floor-area column: median area_m2 905.0 against median
# usable_area 130.0 on the same 3 588 rows.
_LIVE_DUM_CELLS = [
    _cell("mmreality", "dum", "prodej", n_active=3601.0, median_area_m2=905.0,
          median_usable_area=130.0, median_price_per_m2=5701.0,
          n_area_pairs=3588.0, area_divergence_share=0.9967,
          n_area_pairs_7d=113.0, area_divergence_share_7d=1.0),
    _cell("sreality", "dum", "prodej", n_active=20152.0, median_area_m2=150.0,
          median_usable_area=150.0, median_price_per_m2=45990.0,
          n_area_pairs=19082.0, area_divergence_share=0.0,
          n_area_pairs_7d=420.0, area_divergence_share_7d=0.0),
    _cell("idnes", "dum", "prodej", n_active=28757.0, median_area_m2=163.0,
          median_usable_area=163.0, n_area_pairs=28757.0, area_divergence_share=0.0,
          n_area_pairs_7d=610.0, area_divergence_share_7d=0.0),
    # bazos populates usable_area on 0 of 10 409 rows — nothing to compare, so it must
    # be SILENT, not 100% divergent. `area_m2 IS DISTINCT FROM usable_area` taken
    # literally (the charter's wording) would have scored it 100% and fired on it.
    _cell("bazos", "dum", "prodej", n_active=10409.0, median_area_m2=165.0,
          median_usable_area=None, n_area_pairs=0.0, area_divergence_share=None,
          n_area_pairs_7d=0.0, area_divergence_share_7d=None),
]


# A cell that IS scored and IS clean, on every axis. Several tests below assert that
# some other cell is skipped; without a scored companion in the corpus the check would
# (correctly) report that it verified nothing, and the assertion would pass for the
# wrong reason.
def _clean_scored(source: str = "sreality") -> dict[str, Any]:
    return _cell(source, "byt", "prodej", n_active=40000.0, median_area_m2=68.0,
                 median_usable_area=68.0, median_price_per_m2=95000.0,
                 n_floor_eligible=38000.0, floor_null_share=0.0,
                 n_floor_eligible_7d=900.0, floor_null_share_7d=0.0,
                 n_area_pairs=38000.0, area_divergence_share=0.0,
                 n_area_pairs_7d=900.0, area_divergence_share_7d=0.0,
                 n_area_valued=39000.0, n_ppm2_valued=38000.0, n_active_7d=900.0)


def _divergence(cells: list[dict[str, Any]]) -> dict[str, Any]:
    from scripts.verify_pipeline import check_area_vs_usable_divergence

    return check_area_vs_usable_divergence(_fresh(_PlausibilityConn(cells)), T)


def test_area_divergence_catches_the_mmreality_plot_area_defect() -> None:
    res = _divergence(_LIVE_DUM_CELLS)
    assert res["status"] == "fail"
    assert res["value"] == 100.0  # the 7d arm: 113 of 113 pairs
    # Arm 1 decided the status, so arm 1's number is the headline.
    assert res["details"]["value_arm"] == "area_vs_usable"
    named = " ".join(res["details"]["offenders"])
    assert "mmreality/dum/prodej" in named
    for clean in ("sreality", "idnes", "bazos"):
        assert clean not in named


def test_the_derived_sum_arm_catches_a_side_column_that_is_not_a_measurement() -> None:
    """W21. mmreality's parser read the page's `totalArea` as the parcel for years, and
    `totalArea` is `parcelArea + usableArea` — a SUM the page computes. The headline was
    already the interior, so arm 1 saw nothing; the column was populated, so the
    null-check saw nothing; and the plot came out 44-50% too large. This arm states the
    invariant instead of the portal."""
    cells = [
        _cell("mmreality", "dum", "prodej", n_active=2693.0,
              n_area_pairs=2638.0, area_divergence_share=0.0,
              n_area_pairs_7d=150.0, area_divergence_share_7d=0.0,
              n_estate_triples=1123.0, estate_sum_share=0.62),
        _clean_scored(),
    ]
    res = _divergence(cells)
    assert res["status"] == "fail"
    named = " ".join(res["details"]["estate_sum_offenders"])
    assert "mmreality/dum/prodej" in named and "sreality" not in named
    # The tile reports THE FAILING ARM. Arm 1 is clean here, so a `value` of 0.0
    # beside a `fail` status would be a check contradicting itself — and the two
    # numbers are not the same scale of evidence, so they are never merged either.
    assert res["message"].startswith("1 portal/category cell(s) carry a DERIVED SUM")
    assert res["value"] == 62.0
    assert res["details"]["value_arm"] == "estate_sum"
    assert res["details"]["estate_sum_worst_share"] == 0.62
    assert res["details"]["worst_share"] == 0.0        # arm 1, still published


def test_the_derived_sum_arm_tolerates_the_live_coincidence_rate() -> None:
    """Live 2026-09-17 the relation is a WEAK fingerprint: mmreality dum/prodej is the
    worst SCORED cell at 2.6% of 1 123 triples and no other cell exceeds 1.4%, because
    the sum only coincides where a parcel happens to equal its interior. Amber must not
    fire on that, or the operator learns to dismiss the tile."""
    res = _divergence([
        _cell("mmreality", "dum", "prodej", n_estate_triples=1123.0,
              estate_sum_share=0.0258,
              n_area_pairs=2638.0, area_divergence_share=0.0,
              n_area_pairs_7d=150.0, area_divergence_share_7d=0.0),
        _clean_scored(),
    ])
    assert res["status"] == "ok" and res["details"]["estate_sum_offenders"] == []


def test_the_derived_sum_arm_skips_a_cell_below_the_row_floor() -> None:
    """mmreality dum/pronajem reads 5.5% live — over the warn tier, on 55 rows. A
    55-row share is noise; scoring it would amber a healthy portal for ever."""
    res = _divergence([
        _cell("mmreality", "dum", "pronajem", n_estate_triples=55.0,
              estate_sum_share=0.0545),
        _clean_scored(),
    ])
    assert res["status"] == "ok"
    assert res["details"]["estate_sum_arms_scored"] == 0


def test_area_divergence_is_silent_on_a_portal_publishing_only_one_area() -> None:
    """bazos: no pairs at all must never read 'divergent on 100%'. It is skipped, and
    the check still certifies the cells it CAN score."""
    res = _divergence(
        [c for c in _LIVE_DUM_CELLS if c["source"] == "bazos"] + [_clean_scored()])
    assert res["status"] == "ok" and res["details"]["offenders"] == []
    assert res["details"]["arms_scored"] == 2  # the companion's two arms, not bazos


def test_area_divergence_tolerates_the_realitymix_field_convention() -> None:
    """Live: realitymix byt diverges on 10.5% of pairs (16.5% over the trailing week)
    at a MEDIAN relative difference of 9.6% — a balcony/rounding convention, not a
    different physical quantity, and decaying legacy stock post-W1. Amber it and the
    operator learns to dismiss the axis that has to catch the next mmreality."""
    res = _divergence([
        _cell("realitymix", "byt", "prodej", n_active=11000.0,
              n_area_pairs=8708.0, area_divergence_share=0.105,
              n_area_pairs_7d=237.0, area_divergence_share_7d=0.165),
    ])
    assert res["status"] == "ok"


def test_area_divergence_skips_land_where_the_areas_are_meant_to_differ() -> None:
    """Option A: area_m2 IS the plot for pozemek, so divergence from usable_area is
    the correct answer there. Scoring it would red the check on healthy data forever."""
    res = _divergence([
        _cell("sreality", "pozemek", "prodej", n_active=9000.0,
              n_area_pairs=4000.0, area_divergence_share=1.0,
              n_area_pairs_7d=300.0, area_divergence_share_7d=1.0),
        _clean_scored("idnes"),
    ])
    assert res["status"] == "ok" and res["details"]["offenders"] == []


def test_area_divergence_ignores_cells_below_the_row_floor() -> None:
    res = _divergence([
        _cell("maxima", "dum", "prodej", n_active=83.0,
              n_area_pairs=40.0, area_divergence_share=1.0),
        _clean_scored(),
    ])
    assert res["status"] == "ok" and res["details"]["offenders"] == []


# Live 2026-08-25: the unit-price masquerade. A per-m2 UNIT price written into
# price_czk (136 Kc for a commercial rental) is 100% non-NULL and correctly typed —
# only the per-basis floor inside measure_price_per_m2 rejects it.
_LIVE_FLOOR_CELLS = [
    _cell("ceskereality", "komercni", "pronajem", n_active=8852.0,
          n_floor_eligible=5656.0, floor_null_share=0.2003,
          n_floor_eligible_7d=169.0, floor_null_share_7d=0.112),
    _cell("realitymix", "komercni", "pronajem", n_active=6838.0,
          n_floor_eligible=4500.0, floor_null_share=0.1896,
          n_floor_eligible_7d=85.0, floor_null_share_7d=0.094),
    _cell("realitymix", "pozemek", "pronajem", n_active=246.0,
          n_floor_eligible=115.0, floor_null_share=0.1565),
    _cell("remax", "komercni", "pronajem", n_active=593.0,
          n_floor_eligible=275.0, floor_null_share=0.1127),
    _cell("bazos", "komercni", "pronajem", n_active=3912.0,
          n_floor_eligible=2689.0, floor_null_share=0.0666,
          n_floor_eligible_7d=436.0, floor_null_share_7d=0.067),
    # The two portals that already carry a per-area price guard, same cell.
    _cell("idnes", "komercni", "pronajem", n_active=11041.0,
          n_floor_eligible=8444.0, floor_null_share=0.0054),
    _cell("sreality", "komercni", "pronajem", n_active=13984.0,
          n_floor_eligible=9529.0, floor_null_share=0.0049),
]


def _floor(cells: list[dict[str, Any]]) -> dict[str, Any]:
    from scripts.verify_pipeline import check_ppm2_basis_floor_share

    return check_ppm2_basis_floor_share(_fresh(_PlausibilityConn(cells)), T)


def test_basis_floor_share_catches_the_unit_price_masquerade() -> None:
    res = _floor(_LIVE_FLOOR_CELLS)
    assert res["status"] == "fail"
    assert res["value"] == 20.03
    named = " ".join(res["details"]["offenders"])
    for guilty in ("ceskereality/komercni/pronajem", "realitymix/komercni/pronajem",
                   "realitymix/pozemek/pronajem", "remax/komercni/pronajem",
                   "bazos/komercni/pronajem"):
        assert guilty in named
    # ...and the two portals with a working guard are not accused, in the same cell.
    assert "idnes" not in named and "sreality" not in named


def test_basis_floor_share_pozemek_is_scored_unlike_the_divergence_check() -> None:
    """Land is exempt from the AREA check (area_m2 is the plot by design) but not from
    the PRICE floor — realitymix pozemek/pronajem loses 15.7% of its prices and that is
    a defect on any basis."""
    res = _floor([c for c in _LIVE_FLOOR_CELLS if c["category_main"] == "pozemek"])
    assert res["status"] == "fail"


def test_basis_floor_share_fresh_arm_fires_before_the_stock_arm_can() -> None:
    """A regression shipped this week is ~100% of what arrived since, but only churn-
    fraction of the stock. Without the 7d arm, detection latency is weeks."""
    res = _floor([
        _cell("maxima", "komercni", "pronajem", n_active=5000.0,
              n_floor_eligible=4000.0, floor_null_share=0.004,
              n_floor_eligible_7d=300.0, floor_null_share_7d=0.98),
    ])
    assert res["status"] == "fail"
    assert "first seen in 7d" in " ".join(res["details"]["offenders"])


def _shift(
    cells: list[dict[str, Any]], baseline: dict[str, Any] | None,
    history_days: float | None = 30.0,
) -> dict[str, Any]:
    from scripts.verify_pipeline import check_ppm2_median_shift

    return check_ppm2_median_shift(
        _fresh(_PlausibilityConn(cells, baseline, history_days)), T)


def test_median_shift_fires_when_the_w2_backfill_heals_mmreality() -> None:
    """The mmreality defect predates any baseline, so this axis cannot indict it —
    area_vs_usable_divergence does that. What it MUST do is notice the correction:
    905.0 -> 130.0 is 6.96x and 5 701 -> 42 500 is 7.45x, both far above the 3.0x fail
    ratio. A heal this size passing silently would prove the axis inert."""
    res = _shift(
        [_cell("mmreality", "dum", "prodej", n_active=3601.0,
               median_area_m2=130.0, median_price_per_m2=42500.0)],
        {"mmreality/dum/prodej": {"n": 3588, "area": 905.0, "ppm2": 5701.0,
                                  "n_area": 3588, "n_ppm2": 3400}},
    )
    assert res["status"] == "fail"
    assert res["value"] == 7.455
    named = " ".join(res["details"]["offenders"])
    assert "median area_m2" in named and "median Kc/m2" in named


def test_median_shift_tolerates_the_noisiest_real_weekly_move() -> None:
    """Measured live across 21 cells with >= 200 new listings in both of two
    consecutive weeks — the new-arrival cohort, which swings far harder than the stock
    medians this check actually compares: worst 1.47x on area, 1.90x on Kc/m2."""
    res = _shift(
        [_cell("sreality", "byt", "prodej", n_active=40000.0,
               median_area_m2=147.0, median_price_per_m2=95000.0)],
        {"sreality/byt/prodej": {"n": 39000, "area": 100.0, "ppm2": 50000.0,
                                 "n_area": 39000, "n_ppm2": 38000}},
    )
    assert res["status"] == "ok"


def test_median_shift_no_baseline_is_ok_while_young_and_warn_once_stale() -> None:
    """A missing baseline in the first week after deploy is the expected state and the
    operator can do nothing about it. Two weeks of history with still no baseline means
    the check has been erroring or not running — that must not read as green."""
    cells = [_cell("sreality", "byt", "prodej", n_active=40000.0,
                   median_area_m2=100.0, median_price_per_m2=50000.0)]
    assert _shift(cells, None, history_days=3.0)["status"] == "ok"
    assert _shift(cells, None, history_days=None)["status"] == "ok"
    assert _shift(cells, None, history_days=20.0)["status"] == "warn"


def test_median_shift_skips_a_median_without_enough_support_in_either_week() -> None:
    """The gate is the number of rows CARRYING the median, in both weeks."""
    small_now = _shift(
        [_cell("maxima", "dum", "prodej", n_active=4000.0, n_area_valued=83.0,
               n_ppm2_valued=83.0, median_area_m2=900.0, median_price_per_m2=5000.0)],
        {"maxima/dum/prodej": {"n": 4000, "area": 150.0, "ppm2": 45000.0,
                               "n_area": 4000, "n_ppm2": 4000}},
    )
    small_then = _shift(
        [_cell("maxima", "dum", "prodej", n_active=4000.0, n_area_valued=4000.0,
               n_ppm2_valued=4000.0, median_area_m2=900.0, median_price_per_m2=5000.0)],
        {"maxima/dum/prodej": {"n": 4000, "area": 150.0, "ppm2": 45000.0,
                               "n_area": 83, "n_ppm2": 83}},
    )
    for res in (small_now, small_then):
        assert res["details"]["medians_compared"] == 0
        assert res["details"]["offenders"] == []


def test_median_shift_gates_on_median_support_not_on_cell_size() -> None:
    """Live: bezrealitky pozemek/prodej is 1 643 active rows of which NINE carry an
    area_m2, so both medians rest on 9 values whose Kc/m2 spread is 17.6x. Gating on
    n_active passes it and two ordinary delistings then move the median past the 3.0x
    fail — a red tile plus a bell ring, on no defect at all, over and over as it
    oscillates. The comparison must not happen."""
    cell = _cell("bezrealitky", "pozemek", "prodej", n_active=1643.0,
                 n_area_valued=9.0, n_ppm2_valued=9.0,
                 median_area_m2=6471.0, median_price_per_m2=17573.27)
    res = _shift(
        [cell],
        {"bezrealitky/pozemek/prodej": {"n": 1648, "area": 370.0, "ppm2": 3224.52,
                                        "n_area": 9, "n_ppm2": 9}},
    )
    assert res["details"]["medians_compared"] == 0
    assert res["status"] == "warn" and res["value"] is None
    assert "compared NOTHING" in res["message"]
    # ...and the same cell WITH real support is compared and indicted.
    supported = dict(cell, n_area_valued=900.0, n_ppm2_valued=900.0)
    res2 = _shift(
        [supported],
        {"bezrealitky/pozemek/prodej": {"n": 1648, "area": 370.0, "ppm2": 3224.52,
                                        "n_area": 900, "n_ppm2": 900}},
    )
    assert res2["status"] == "fail" and res2["details"]["medians_compared"] == 2


def test_median_shift_baseline_round_trips_through_its_own_details() -> None:
    """The baseline is this check's own result row from a week ago — no new table. If
    the snapshot's keys and the reader's cell keys ever disagree the axis silently
    never compares anything, so pin the round trip."""
    cells = [_cell("mmreality", "dum", "prodej", n_active=3601.0,
                   median_area_m2=905.0, median_price_per_m2=5701.0)]
    written = _shift(cells, None, history_days=1.0)["details"]["cells"]
    assert written == {"mmreality/dum/prodej": {
        "n": 3601, "area": 905.0, "ppm2": 5701.0, "n_area": 1000, "n_ppm2": 1000}}
    healed = [_cell("mmreality", "dum", "prodej", n_active=3601.0,
                    median_area_m2=130.0, median_price_per_m2=42500.0)]
    assert _shift(healed, written)["status"] == "fail"


def test_measure_checks_share_one_read_of_the_plausibility_view() -> None:
    """The view is a ~12 s sequential scan of the active corpus. Three checks, one read."""
    import scripts.verify_pipeline as vp

    conn = _PlausibilityConn(_LIVE_DUM_CELLS + _LIVE_FLOOR_CELLS, None, 1.0)
    results = run_checks(conn, T, checks=_registered(*_MEASURE_CHECK_KEYS))
    assert len(results) == 4
    assert sum("measure_plausibility_by_source" in s for s in conn.executed) == 1
    assert not vp._PLAUSIBILITY_CACHE or "cells" in vp._PLAUSIBILITY_CACHE


def test_run_checks_clears_the_plausibility_cache_between_runs() -> None:
    """A stale corpus would make every measure check certify last run's data."""
    first = _PlausibilityConn(_LIVE_DUM_CELLS, None, 1.0)
    run_checks(first, T, checks=_registered("area_vs_usable_divergence"))
    healed = [dict(c, area_divergence_share=0.0, area_divergence_share_7d=0.0)
              for c in _LIVE_DUM_CELLS]
    second = _PlausibilityConn(healed, None, 1.0)
    out = run_checks(second, T, checks=_registered("area_vs_usable_divergence"))
    assert out[0]["status"] == "ok"


def test_empty_plausibility_view_is_unknown_never_ok() -> None:
    """The view is gated on is_platform_admin(); read it from a role that fails the
    gate and it returns zero rows. That is an INERT check, not a clean bill of health."""
    from scripts.verify_pipeline import (
        check_area_vs_usable_divergence, check_ppm2_basis_floor_share,
        check_ppm2_measure_coverage, check_ppm2_median_shift,
    )

    for fn in (check_area_vs_usable_divergence, check_ppm2_basis_floor_share,
               check_ppm2_median_shift, check_ppm2_measure_coverage):
        res = fn(_fresh(_PlausibilityConn([])), T)
        assert res["status"] == "unknown" and "INERT" in res["message"]
        assert res["details"]["skipped"]


class _BrokenViewConn(_PlausibilityConn):
    """migration 427 not applied yet — the relation simply is not there."""

    def execute(self, sql: str, params: Any = None) -> None:
        if "measure_plausibility_by_source" in sql:
            raise RuntimeError('relation "measure_plausibility_by_source" does not exist')
        super().execute(sql, params)


def test_missing_view_is_unknown_and_names_the_migration_not_three_red_tiles() -> None:
    """Between merging this PR and applying migration 427 the view does not exist. Three
    checks red with `relation does not exist` are indistinguishable from three real
    defects, so the read is reported, never raised."""
    results = run_checks(_BrokenViewConn([]), T, checks=_registered(*_MEASURE_CHECK_KEYS))
    assert [r["status"] for r in results] == ["unknown"] * 4
    for r in results:
        assert "INERT" in r["message"] and "427" in r["message"]
        assert "does not exist" in r["details"]["skipped"]


# Live 2026-08-25: the coverage hole every OTHER axis is blind to. sreality publishes
# 27 174 active `pozemek` rows with area_m2 NULL on 27 174 of them — the plot size sits
# in estate_area — so these four cells carry no floor-eligible rows, no area pairs and
# no medians. Before the coverage arm all three axes called them healthy while the per-m2
# measure did not exist for ~7% of the active corpus.
_LIVE_DARK_LAND_CELLS = [
    _cell("sreality", "pozemek", "prodej", n_active=20484.0, n_area_valued=0.0,
          n_ppm2_valued=0.0, n_active_7d=10.0, measure_input_gap_share=1.0,
          measure_input_gap_share_7d=1.0, n_floor_eligible=0.0, floor_null_share=None,
          n_area_pairs=0.0, area_divergence_share=None),
    _cell("sreality", "pozemek", "podil", n_active=5825.0, n_area_valued=0.0,
          n_ppm2_valued=0.0, n_active_7d=16.0, measure_input_gap_share=1.0,
          measure_input_gap_share_7d=1.0, n_floor_eligible=0.0, floor_null_share=None,
          n_area_pairs=0.0, area_divergence_share=None),
    _cell("bezrealitky", "pozemek", "prodej", n_active=1643.0, n_area_valued=9.0,
          n_ppm2_valued=9.0, n_active_7d=64.0, measure_input_gap_share=0.9945,
          measure_input_gap_share_7d=1.0, n_floor_eligible=9.0, floor_null_share=0.0,
          n_area_pairs=9.0, area_divergence_share=0.0),
]


def _coverage(cells: list[dict[str, Any]]) -> dict[str, Any]:
    from scripts.verify_pipeline import check_ppm2_measure_coverage

    return check_ppm2_measure_coverage(_fresh(_PlausibilityConn(cells)), T)


def test_coverage_sees_the_land_cells_every_other_axis_skips() -> None:
    """The whole point of the arm: these cells are invisible to the other three because
    every one of those is a ratio over rows that HAVE the inputs."""
    res = _coverage(_LIVE_DARK_LAND_CELLS + [_clean_scored()])
    assert res["status"] == "warn"
    named = " ".join(res["details"]["offenders"])
    for dark in ("sreality/pozemek/prodej", "sreality/pozemek/podil",
                 "bezrealitky/pozemek/prodej"):
        assert dark in named
    assert "sreality/byt/prodej" not in named
    # ...and the axes that cannot see them still report clean, which is why the arm
    # had to exist rather than the other three being "fixed".
    assert _divergence(_LIVE_DARK_LAND_CELLS + [_clean_scored()])["status"] == "ok"
    assert _floor(_LIVE_DARK_LAND_CELLS + [_clean_scored()])["status"] == "ok"


def test_coverage_stock_gap_ambers_but_a_fresh_gap_fails() -> None:
    """Severity is a property of the ARM. A standing gap nobody can clear today is
    amber; the same share among the rows that arrived this week is a parser regression
    in flight and must be red — that is the case where the divergence and floor axes go
    QUIET (a portal writing NULL area produces no pairs and no eligible rows)."""
    standing = _coverage([
        _cell("sreality", "pozemek", "prodej", n_active=20484.0, n_active_7d=10.0,
              measure_input_gap_share=1.0, measure_input_gap_share_7d=1.0),
        _clean_scored(),
    ])
    assert standing["status"] == "warn"  # the 7d arm has 10 rows: below the gate
    shipping = _coverage([
        _cell("mmreality", "pozemek", "prodej", n_active=4190.0, n_active_7d=400.0,
              measure_input_gap_share=0.30, measure_input_gap_share_7d=1.0),
        _clean_scored(),
    ])
    assert shipping["status"] == "fail"
    assert "first seen in 7d" in " ".join(shipping["details"]["offenders"])


def test_coverage_tolerates_ordinary_portal_incompleteness() -> None:
    """Live worst non-dark cells: realitymix ostatni/pronajem 89.4% of stock and 35.8%
    on the worst scored 7d arm (bazos komercni/prodej). Amber those and the operator
    learns to dismiss the one axis that sees a portal going dark."""
    res = _coverage([
        _cell("realitymix", "ostatni", "pronajem", n_active=292.0, n_active_7d=24.0,
              measure_input_gap_share=0.894),
        _cell("bazos", "komercni", "prodej", n_active=1472.0, n_active_7d=229.0,
              measure_input_gap_share=0.358, measure_input_gap_share_7d=0.358),
    ])
    assert res["status"] == "ok" and res["details"]["offenders"] == []


def test_coverage_is_silent_where_the_basis_is_undecidable() -> None:
    """No basis means no measure BY SPECIFICATION (a visible gap, never a guess) — the
    view publishes NULL there, and a permanent amber on it would be noise."""
    res = _coverage([
        _cell("bazos", "ostatni", None, n_active=274.0, n_active_7d=40.0,
              measure_input_gap_share=None, measure_input_gap_share_7d=None),
        _clean_scored(),
    ])
    assert res["status"] == "ok" and res["details"]["arms_scored"] == 2


def test_every_measure_axis_refuses_to_certify_a_corpus_it_cannot_measure() -> None:
    """THE fail-open regression. Take the real production cells and blank their
    measurable content — the per-m2 measure 100% dead platform-wide, exactly what a
    later vocabulary or category migration would cause. Before this, _status_for_share
    started `worst` at 0.0 and _status_for_median_shift at 1.0 and every arm was
    silently skipped, so all three reported `ok` with a reassuring message ('Per-m2
    medians stable week-over-week (worst move 1.00x across 64 cells)') while nothing
    whatsoever had been verified. `_inert_measure_check` did not catch it: it only fires
    on ZERO ROWS, and there are 100 rows here."""
    dead = [
        dict(c, n_floor_eligible=0.0, floor_null_share=None, n_floor_eligible_7d=0.0,
             floor_null_share_7d=None, n_area_pairs=0.0, area_divergence_share=None,
             n_area_pairs_7d=0.0, area_divergence_share_7d=None,
             n_area_valued=0.0, n_ppm2_valued=0.0, median_area_m2=None,
             median_usable_area=None, median_price_per_m2=None,
             price_per_m2_basis=None, measure_input_gap_share=1.0,
             measure_input_gap_share_7d=1.0)
        for c in _LIVE_DUM_CELLS + _LIVE_FLOOR_CELLS
    ]
    baseline = {
        f"{c['source']}/{c['category_main']}/{c['category_type']}":
            {"n": 5000, "area": 100.0, "ppm2": 50000.0, "n_area": 5000, "n_ppm2": 5000}
        for c in dead
    }
    results = run_checks(
        _PlausibilityConn(dead, baseline, 30.0), T, checks=_registered(*_MEASURE_CHECK_KEYS))
    assert {r["status"] for r in results} == {"unknown", "warn", "fail"}
    by_key = {r["check_key"]: r for r in results}
    for key in ("ppm2_basis_floor_share", "area_vs_usable_divergence",
                "ppm2_median_shift"):
        assert by_key[key]["value"] is None, key
        assert "NOTHING" in by_key[key]["message"], key
    # scored nothing = not measured; the median shift's own "compared nothing" stays a
    # warn until wave 6 rewrites it
    assert by_key["ppm2_basis_floor_share"]["status"] == "unknown"
    assert by_key["area_vs_usable_divergence"]["status"] == "unknown"
    assert by_key["ppm2_median_shift"]["status"] == "warn"
    # the corpus went dark THIS WEEK: coverage is the axis that says so out loud
    assert by_key["ppm2_measure_coverage"]["status"] == "fail"


def test_a_measured_and_clean_corpus_still_reports_ok() -> None:
    """The other side of the same rule — refusing to certify an unmeasurable corpus
    must not make a measurable one amber."""
    results = run_checks(
        _PlausibilityConn(_LIVE_DUM_CELLS + [_clean_scored("remax")], None, 1.0), T,
        checks=_registered("ppm2_basis_floor_share", "ppm2_measure_coverage"))
    assert [r["status"] for r in results] == ["ok", "ok"]


# --- migration_drift (scripts/checks/platform.py): merged is not applied ---------


class _DriftConn:
    """Returns a canned presence verdict per (kind, ident) probe."""

    def __init__(self, present: dict[tuple[str, str], bool], default: bool = True) -> None:
        self._present = present
        self._default = default
        self.probed: list[tuple[str, str]] = []

    def transaction(self) -> "_DriftConn":
        return self

    def cursor(self) -> "_DriftConn":
        return self

    def __enter__(self) -> "_DriftConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("SET LOCAL statement_timeout"):
            return
        self.probed = list(zip(params["kinds"], params["idents"]))

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [(k, i, self._present.get((k, i), self._default)) for k, i in self.probed]


def test_migration_drift_ok_when_everything_is_present() -> None:
    out = check_migration_drift(_DriftConn({}, default=True), T)
    assert out["status"] == "ok"
    assert out["value"] == 0
    assert out["details"]["objects_probed"] > 0


def test_migration_drift_fails_when_a_migration_is_wholly_absent() -> None:
    """The 2026-08-25 signature: merged, never applied, so NONE of its objects
    exist. Nothing else in the platform noticed for 29 hours.

    The subject migration is DERIVED from the current window, like the
    half-present test below, not hardcoded: this test used to pin migration 444's
    `listings.discovered_at`, which silently stopped exercising the failure path
    the moment 25 newer migrations pushed 444 out of `migration_drift_window` —
    it then passed `default=True` for everything and asserted "fail" against an
    all-present database, so it went red for the next PR to cross that boundary
    rather than for any real drift.
    """
    migs = load_migrations(_MIGRATIONS_DIR, newest=T["migration_drift_window"])
    # The target's objects must be DISJOINT from every other in-window migration's:
    # two migrations may legitimately restate one object (468 and 471 both restate
    # llm_calls' called_for CHECK), and marking a shared object absent fails BOTH,
    # breaking the exactly-one assertion for a reason that is not drift.
    target = next(
        m for m in migs
        if m.objects and all(SAFE_IDENT.match(o.ident) for o in m.objects)
        and not any(
            {(o.kind, o.ident) for o in m.objects} & {(o.kind, o.ident) for o in other.objects}
            for other in migs if other is not m and other.objects))
    conn = _DriftConn({(o.kind, o.ident): False for o in target.objects}, default=True)
    out = check_migration_drift(conn, T)
    assert out["status"] == "fail"
    assert out["value"] == 1
    assert any(target.filename in m for m in out["details"]["missing_entirely"])


def test_migration_drift_only_warns_when_a_migration_is_half_present() -> None:
    """Half-applied is ambiguous — a partial apply, or the parser mis-read the
    file. Ambiguity warns; it must not page anyone at 3am."""
    migs = load_migrations(_MIGRATIONS_DIR, newest=T["migration_drift_window"])
    multi = next(m for m in migs if len(m.objects) >= 2)
    conn = _DriftConn({(multi.objects[0].kind, multi.objects[0].ident): False}, default=True)
    out = check_migration_drift(conn, T)
    assert out["status"] == "warn"
    assert any(multi.filename in p for p in out["details"]["partially_missing"])
    assert out["details"]["missing_entirely"] == []


def test_migration_drift_reports_its_own_blind_spot() -> None:
    """Migrations that only drop, grant, or update data declare nothing to
    probe. They must be COUNTED and named, not silently treated as passing —
    the failure mode this sprint already hit with a test that never ran."""
    out = check_migration_drift(_DriftConn({}, default=True), T)
    assert isinstance(out["details"]["unverifiable"], list)
    assert all(f.endswith(".sql") for f in out["details"]["unverifiable"])


@pytest.mark.parametrize("folder", ["missing", "empty"])
def test_migration_drift_with_no_migration_files_is_not_measured(
    folder: str, tmp_path: Path, monkeypatch: Any,
) -> None:
    """The worker image ships no migrations/ folder. Zero files probed is zero files
    verified: it read `ok` ("All 0 checkable of the newest 0 migrations are present")."""
    target = tmp_path / "migrations"
    if folder == "empty":
        target.mkdir()
    monkeypatch.setattr(platform_checks, "_MIGRATIONS_DIR", target)
    conn = _DriftConn({}, default=True)
    out = check_migration_drift(conn, T)
    assert out["status"] == "unknown" and out["value"] is None
    assert out["details"]["skipped"] == "no migration files on this host"
    assert "Not measured" in out["message"]
    assert conn.probed == [], "nothing to probe, so no statement is sent"


def test_migration_drift_probes_only_safe_identifiers() -> None:
    """to_regclass raises on a malformed identifier instead of returning NULL,
    which would turn one odd migration into a crashed check."""
    conn = _DriftConn({}, default=True)
    check_migration_drift(conn, T)
    for _kind, ident in conn.probed:
        assert SAFE_IDENT.match(ident), ident


# --- location_town_coverage (rule 25's invariant; every listing since W15) --------


def test_location_town_coverage_is_registered_and_runs_first_of_the_gauges() -> None:
    """Registration is not enough, and 2026-09-11 21:02 is why: the lane ran `CHECKS` in
    order under a 120 s budget and left the last seven unmeasured, this one among them.
    The invariant the location programme is measured by cannot be the measurement that gets
    starved — it is one indexed join, so it goes first of the gauges (`run_due` runs the
    due alarms before them) and first on the page."""
    from scripts.verify_pipeline import check_location_town_coverage

    assert CHECKS[0].key == "location_town_coverage"
    assert CHECKS[0].fn is check_location_town_coverage
    assert [c.key for c in CHECKS if c.cls == "gauge"][0] == "location_town_coverage"
    assert _keys().count("location_town_coverage") == 1


def test_location_town_coverage_is_red_while_any_czech_listing_lacks_a_town() -> None:
    """Absolute counts, no threshold: the invariant is zero. The message names the portal,
    because a non-zero number is a contract that has to change, not a trend to watch."""
    from scripts.verify_pipeline import check_location_town_coverage

    conn = _TownCoverageConn([
        ("bazos", 40_000, 0, 120, 39_500, 120, 0),
        ("idnes", 30_000, 900, 11_000, 18_000, 11_900, 0),
        ("sreality", 200_000, 0, 0, 199_000, 0, 0),
    ])
    out = check_location_town_coverage(conn, T)
    assert out["status"] == "fail"
    assert out["value"] == 12_020
    assert out["details"]["no_row"] == 900 and out["details"]["cz_no_town"] == 11_120
    assert "idnes: 900 without a row, 11,000 Czech without a town" in out["message"]
    assert "sreality" not in out["message"]
    assert any("statement_timeout" in s for s in conn.executed)
    # W5: the consumer rule's workload number rides the same read, never the status.
    assert out["details"]["hidden"] == 12_020
    assert "Consumers currently hide 12,020 unresolved listings (idnes 11,900; bazos 120)" \
        in out["message"]


def test_location_town_coverage_measures_every_listing_not_only_the_live_ones() -> None:
    """W15 (operator ruling 2026-09-14). The read was scoped to the served set and its four
    counts additionally filtered on `l.is_active`; both are gone. A DELISTED listing with no
    answer row is now red exactly like a live one, and the denominator is the portal's whole
    corpus — which is why the per-portal series is not comparable across that date."""
    from scripts.verify_pipeline import check_location_town_coverage

    out = check_location_town_coverage(_TownCoverageConn([
        ("bazos", 40_000, 0, 0, 40_000, 0, 0),
        ("remax", 30_000, 4_200, 0, 25_800, 4_200, 0),
    ]), T)
    assert out["status"] == "fail"
    assert out["value"] == 4_200
    assert out["details"]["cz_no_town"] == 0
    assert out["details"]["listings"] == 70_000
    assert out["details"]["per_source"][1]["listings"] == 30_000
    assert out["details"]["per_source"][1]["town_share"] == 25_800 / 30_000
    assert "cells" not in out["details"]  # the ladder's key: a legacy check emits none
    assert "remax: 4,200 without a row, 0 Czech without a town (of 30,000)" in out["message"]
    assert "bazos" not in out["message"]


def test_location_town_coverage_is_ok_only_at_zero() -> None:
    from scripts.verify_pipeline import check_location_town_coverage

    out = check_location_town_coverage(_TownCoverageConn([
        ("bazos", 40_000, 0, 0, 39_000, 0, 0), ("idnes", 30_000, 0, 0, 10_000, 0, 0)]), T)
    assert out["status"] == "ok" and out["value"] == 0
    assert out["details"]["hidden"] == 0
    assert "Consumers currently hide" not in out["message"]
    assert out["details"]["per_source"][1]["town_share"] == 10_000 / 30_000
    assert "Every one of 70,000 listings" in out["message"]


def test_location_town_coverage_ku_arm_is_red_while_an_address_row_lacks_a_ku() -> None:
    """MF PR-B. An address point lies in exactly one KÚ, so a Czech address-grain row the
    CURRENT resolver wrote without one means KÚ geometry is missing at the registry version —
    the 2026-09-13 failure class, caught on the row instead of on a dead MF job."""
    from location_data.resolver.version import RESOLVER_VERSION
    from scripts.verify_pipeline import check_location_town_coverage

    conn = _TownCoverageConn([
        ("bezrealitky", 20_000, 0, 0, 20_000, 0, 37),
        ("sreality", 200_000, 0, 0, 200_000, 0, 0),
    ])
    out = check_location_town_coverage(conn, T)
    assert out["status"] == "fail"
    assert out["value"] == 0, "the Health series stays the town count; the arm is a detail"
    assert out["details"]["address_no_ku"] == 37
    assert out["details"]["registry"]["current"] == "ruian:2026-08-31"
    assert out["details"]["cz_no_town"] == 0
    assert f"37 Czech address-grain rows at {RESOLVER_VERSION} have no KÚ: bezrealitky 37." \
        in out["message"]


def test_location_town_coverage_ku_arm_reads_the_rank_table_and_the_current_version() -> None:
    """Address grain is `is_address_grain` (never the enum's order — 380 and 535 disagree),
    the version is the constant (older rows are the re-resolve's backlog), and the one excuse
    is a degenerate KÚ in the row's obec at the CURRENT registry version."""
    from scripts.verify_pipeline import _LOCATION_TOWN_COVERAGE_SQL

    flat = " ".join(_LOCATION_TOWN_COVERAGE_SQL.split()).lower()
    assert "gr.is_address_grain and ll.resolver_version = %s and ll.katastr_kod is null" in flat
    assert "d.discrepancy = 'degenerate_boundary_geometry'" in flat
    assert "(select id from registry_versions where is_current)" in flat
    assert "boundary_load_failed" not in flat
    assert _LOCATION_TOWN_COVERAGE_SQL.count("%s") == 1


def test_location_town_coverage_counts_undetermined_as_czech() -> None:
    """Foreign is a determination the resolver makes; a listing with no Czech town and no
    foreign signal is missing, never quietly foreign. The SQL states that directly."""
    from scripts.verify_pipeline import _LOCATION_TOWN_COVERAGE_SQL

    flat = " ".join(_LOCATION_TOWN_COVERAGE_SQL.split()).lower()
    assert "ll.country_status <> 'foreign' and ll.obec_kod is null" in flat
    assert "from listings l left join listing_location ll on ll.listing_id = l.id" in flat
    # W2-a repointed it: the frozen projection is not the coverage denominator any more.
    assert "listing_location_current" not in flat


def test_location_town_coverage_covers_every_listing() -> None:
    """W15. The guard and the producer must not disagree about who is in scope, and since
    the operator's 2026-09-14 ruling that is EVERY listing: the served set is retired, so
    the read carries no WHERE and no per-count `l.is_active` filter, exactly like
    `drain._SWEEP_SQL`. RED by: a cohort creeping back into either one."""
    from location_data.resolver import drain
    from scripts.verify_pipeline import _LOCATION_TOWN_COVERAGE_SQL

    flat = " ".join(_LOCATION_TOWN_COVERAGE_SQL.split()).lower()
    for text in ("repr_listing_ref_id", "properties", "l.is_active"):
        assert text not in flat, text
    # No cohort WHERE at all: the join goes straight into the GROUP BY. (The only
    # `where` left in the statement is the consumer rule's own EXISTS.)
    # W16: the listing_location alias is `ll` and the granularity-rank lookup rides
    # along, because `town_n` is now the SHARED town flag (the obec RANK floor included)
    # rendered from location_data/location_steps.py instead of a fourth local spelling.
    assert "listing_location ll on ll.listing_id = l.id" in flat
    assert "location_granularity_rank gr on gr.granularity = ll.granularity group by l.source" in flat
    assert "l.is_active" not in " ".join(drain._SWEEP_SQL.split()).lower()
    # The cells the operator reads, and nothing the old scope needed.
    for kept in ("as listings_n", "as no_row_n", "as cz_no_town_n", "as town_n",
                 "as hidden_n", "as address_no_ku_n"):
        assert kept in flat, kept
    assert "display_no_row" not in flat and "active_n" not in flat
    assert "filter (where l.is_active" not in flat


def test_registry_liveness_is_red_for_a_stale_current_version() -> None:
    """The towns are codes of the CURRENT registry version; a registry that stopped moving
    ages every one of them without a single listing going red."""
    from scripts.verify_pipeline import check_location_town_coverage

    out = check_location_town_coverage(_TownCoverageConn(
        [("bazos", 40_000, 0, 0, 40_000, 0, 0)], registry=("ruian:2026-08-31", 41, None, None),
    ), T)
    assert out["status"] == "fail" and out["value"] == 0
    assert out["details"]["registry"]["age_days"] == 41
    assert "Registry stalled: current ruian:2026-08-31 is 41 days old (> 40)" in out["message"]


def test_registry_liveness_is_red_for_a_newer_version_staged_over_a_day() -> None:
    """A monthly run a pending `location-batch` run superseded is CANCELLED, not failed, and
    a load killed mid-pack leaves an inert staged version — Actions reports neither."""
    from scripts.verify_pipeline import check_location_town_coverage

    out = check_location_town_coverage(_TownCoverageConn(
        [("bazos", 40_000, 0, 0, 40_000, 0, 0)],
        registry=("ruian:2026-08-31", 12, "ruian:2026-09-30", 30.4),
    ), T)
    assert out["status"] == "fail"
    assert "ruian:2026-09-30 has sat staged 30 h (> 24 h)" in out["message"]

    fresh = check_location_town_coverage(_TownCoverageConn(
        [("bazos", 40_000, 0, 0, 40_000, 0, 0)],
        registry=("ruian:2026-08-31", 12, "ruian:2026-09-30", 5.0),
    ), T)
    assert fresh["status"] == "ok" and "Registry stalled" not in fresh["message"]


def test_registry_liveness_is_red_when_nothing_is_current() -> None:
    from scripts.verify_pipeline import check_location_town_coverage

    out = check_location_town_coverage(_TownCoverageConn(
        [("bazos", 40_000, 0, 0, 40_000, 0, 0)], registry=None,
    ), T)
    assert out["status"] == "fail"
    assert "no current registry version" in out["message"]


def test_registry_liveness_reads_only_newer_staged_versions() -> None:
    """A staged version a later vintage overtook is history, not a stall; the age is the
    vintage's, not the load's."""
    from scripts.verify_pipeline import _REGISTRY_LIVENESS_SQL

    flat = " ".join(_REGISTRY_LIVENESS_SQL.split()).lower()
    assert "current_date - c.source_date as age_days" in flat
    assert "where not n.is_current and n.source_date > c.source_date" in flat
    assert "where c.is_current" in flat


# --- location_payload_shape_drift (W4's standing P6 check) ---------------------

class _ShapeDriftConn:
    def __init__(self, rows: list[tuple[str, int, int]]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def cursor(self) -> "_ShapeDriftConn":
        return self

    def transaction(self) -> "_ShapeDriftConn":
        return self

    def __enter__(self) -> "_ShapeDriftConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)

    def fetchall(self) -> Any:
        return self._rows


class _TownCoverageConn(_ShapeDriftConn):
    """The coverage rows plus the registry-liveness row, green unless a test says not."""

    def __init__(self, rows: list[tuple], *,
                 registry: tuple | None = ("ruian:2026-08-31", 10, None, None)) -> None:
        super().__init__(rows)
        self._registry = registry

    def fetchone(self) -> Any:
        return self._registry


def test_location_payload_shape_drift_is_registered() -> None:
    """An unregistered check is dead code that never writes a row."""
    from scripts.verify_pipeline import check_location_payload_shape_drift

    assert _by_key("location_payload_shape_drift").fn is check_location_payload_shape_drift


def test_location_payload_shape_drift_reds_on_a_source_side_shape_change() -> None:
    """The W4 gate's fourth arm: the next cutover must force re-extraction instead of
    silently degrading. A shape change is ~100% of the rows first seen since it landed."""
    from scripts.verify_pipeline import check_location_payload_shape_drift

    conn = _ShapeDriftConn([("sreality", 1200, 1150), ("bezrealitky", 80, 0)])
    out = check_location_payload_shape_drift(conn, T)
    assert out["status"] == "fail"
    assert out["value"] == 95.83
    assert "sreality" in out["message"] and "sreality_payload_shape" in out["message"]
    assert "bezrealitky" not in out["details"]["offenders"][0]
    assert out["details"]["window_hours"] == 48
    assert [c["source"] for c in out["details"]["per_source"]] == ["sreality", "bezrealitky"]
    assert "cells" not in out["details"]  # the ladder's key: a legacy check emits none
    assert any("statement_timeout" in s for s in conn.executed)


def test_location_payload_shape_drift_rings_on_the_first_tick_after_a_cutover() -> None:
    """The window is the detection latency: after a total cutover the share is
    elapsed/window, so one 6-hourly tick into a 48 h window reads 12.5% — above fail.
    A 7-day window would sit below the same line for ~1.4 days."""
    from scripts.verify_pipeline import THRESHOLDS, check_location_payload_shape_drift

    window = THRESHOLDS["location_payload_shape_drift_window_hours"]
    fail = THRESHOLDS["location_payload_shape_drift_fail"]
    assert 6 / window >= fail, "one lane tick after a cutover must already read fail"
    out = check_location_payload_shape_drift(_ShapeDriftConn([("sreality", 800, 100)]), T)
    assert out["status"] == "fail"


def test_location_payload_shape_drift_names_bezrealitkys_own_remedy() -> None:
    from scripts.verify_pipeline import check_location_payload_shape_drift

    out = check_location_payload_shape_drift(
        _ShapeDriftConn([("sreality", 1200, 0), ("bezrealitky", 90, 60)]), T)
    assert out["status"] == "fail"
    assert "_DETAIL_QUERY" in out["message"]


def test_location_payload_shape_drift_is_ok_when_fresh_rows_are_clean() -> None:
    from scripts.verify_pipeline import check_location_payload_shape_drift

    out = check_location_payload_shape_drift(
        _ShapeDriftConn([("sreality", 1200, 3), ("bezrealitky", 80, 1)]), T)
    assert out["status"] == "ok"
    assert out["value"] == 1.25
    assert out["details"]["arms_scored"] == 2


def test_location_payload_shape_drift_refuses_ok_when_nothing_reached_min_rows() -> None:
    """A check that scored nothing has certified nothing."""
    from scripts.verify_pipeline import check_location_payload_shape_drift

    out = check_location_payload_shape_drift(_ShapeDriftConn([("sreality", 10, 10)]), T)
    assert out["status"] == "unknown"
    assert out["value"] is None
    assert out["details"]["arms_scored"] == 0
    assert "nothing was verified" in out["details"]["skipped"]


def test_location_payload_shape_drift_sql_shares_the_gate_classifier_and_reads_the_fresh_arm() -> None:
    from scripts.verify_pipeline import SREALITY_SHAPE_CASE_SQL
    from scripts.verify_pipeline import _LOCATION_PAYLOAD_SHAPE_DRIFT_SQL as sql

    assert SREALITY_SHAPE_CASE_SQL.strip() in sql
    assert "first_seen_at > now() - make_interval(hours => %(window_hours)s)" in sql
    assert "NOT (raw_json ? 'ruianId')" in sql
    assert "IS DISTINCT FROM 'object'" in sql


# --- outbound_url_coverage (portal-URL contract) ----------------------------


class _UrlCoverageConn:
    """A cursor that answers one SELECT with scripted rows."""

    def __init__(self, rows):
        self._rows = rows

    def transaction(self):
        return contextlib.nullcontext()

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def execute(self_inner, sql, params=None):
                if sql.startswith("SET LOCAL statement_timeout"):
                    return
                assert "source_url IS NULL" in sql

            def fetchall(self_inner):
                return conn._rows

        return _Cur()


def test_outbound_url_coverage_is_an_absolute_count_per_source() -> None:
    from scripts.verify_pipeline import check_outbound_url_coverage
    r = check_outbound_url_coverage(_UrlCoverageConn([
        ("bazos", 60000, 0, 0), ("sreality", 110000, 120, 120),
    ]), T)
    assert r["check_key"] == "outbound_url_coverage"
    assert r["status"] == "warn" and r["value"] == 120       # 50 <= 120 < 500
    assert "sreality 120 active rows without a URL (120 first seen in 7d)" in r["message"]
    assert "scraper/sreality_url.py" in r["message"]


def test_outbound_url_coverage_fails_past_the_absolute_fail_count() -> None:
    from scripts.verify_pipeline import check_outbound_url_coverage
    r = check_outbound_url_coverage(_UrlCoverageConn([("sreality", 110000, 5000, 4)]), T)
    assert r["status"] == "fail" and r["value"] == 5000


def test_outbound_url_coverage_clean_is_ok() -> None:
    from scripts.verify_pipeline import check_outbound_url_coverage
    r = check_outbound_url_coverage(_UrlCoverageConn([("bazos", 1, 0, 0), ("sreality", 1, 3, 0)]), T)
    assert r["status"] == "ok" and r["value"] == 3
    assert r["message"].startswith("Every active listing")


# --- field_fill_matrix (field capture W1) ----------------------------------


class _MatrixConn(_ShapeDriftConn):
    """`_fetchall` wants transaction() + cursor(); _ShapeDriftConn already has both."""


def _live_matrix_rows(**moved: int) -> list[tuple[Any, ...]]:
    """One row per BLESSED cell, mirroring the baseline unless `moved` says otherwise.

    Every test here builds the whole matrix, because a blessed cell missing from the live
    read is now an offender in its own right — a partial matrix is a finding, not a
    fixture shortcut."""
    from scraper import field_census

    rows: list[tuple[Any, ...]] = []
    for key, cell in field_census.load_baseline()["cells"].items():
        source, field = key.split("/", 1)
        rows.append((source, cell["n"], field, moved.get(key.replace("/", "__"),
                                                         cell["filled"]),
                     None, None, 0, 0, None, None))
    return rows


def test_field_fill_matrix_is_registered() -> None:
    from scripts.verify_pipeline import check_field_fill_matrix

    assert _by_key("field_fill_matrix").fn is check_field_fill_matrix


def test_field_fill_matrix_reproduces_the_known_zero_cells() -> None:
    """The gate this wave is measured by: a cell whose parser reads a key its portal
    has never emitted is named in the report.

    The baseline was re-blessed after the W4 heal (2026-09-22), so replaying it as the
    live matrix is today's production: the cells W4 wired are filled, the cells left
    declared are the ones the portal genuinely never states, and a zero on a `text` cell
    whose gate is closed is INERT (nothing can write it yet), not a producer failing."""
    from scripts.verify_pipeline import check_field_fill_matrix

    out = check_field_fill_matrix(_MatrixConn(_live_matrix_rows()), T)
    details = out["details"]
    assert {
        "remax/has_balcony", "ceskereality/parking_lots", "ceskereality/total_floors",
        "ceskereality/has_lift", "ceskereality/cellar", "remax/terrace",
    } <= set(details["zero_fill_known_gaps"])
    # The only cells allowed to read "should be filling" are the ones a gate just opened:
    # a zero there is the lane's to-do list between its deploy and its first passes.
    from scraper import attribute_contract
    open_cells = {f"{p}/{f}" for p, fs in attribute_contract.extracted_cells().items() for f in fs}
    assert set(details["zero_fill_undeclared"]) <= open_cells
    assert not {"ceskereality/has_parking", "mmreality/has_balcony", "realitymix/has_lift"} & set(details["zero_fill_known_gaps"])
    assert out["status"] in ("ok", "warn")
    assert out["details"]["cells_measured"] == out["details"]["cells_blessed"]


def test_field_fill_matrix_fails_when_a_whole_source_stops_being_measured() -> None:
    """The denominator arm: a portal disabled in `portals`, or one whose stock has gone
    inactive, drops out of the matrix spine — and a check that walked only live cells
    would report `ok` over a shrinking denominator."""
    from scripts.verify_pipeline import check_field_fill_matrix

    rows = [r for r in _live_matrix_rows() if r[0] != "remax"]
    out = check_field_fill_matrix(_MatrixConn(rows), T)
    assert out["status"] == "fail"
    assert "remax: 26 blessed cell(s) absent" in out["details"]["offenders"][0]
    assert out["details"]["cells_measured"] < out["details"]["cells_blessed"]


def test_field_fill_matrix_runs_under_the_per_check_statement_timeout() -> None:
    from scripts.verify_pipeline import check_field_fill_matrix

    conn = _MatrixConn(_live_matrix_rows())
    check_field_fill_matrix(conn, T)
    assert any("statement_timeout" in s for s in conn.executed)
    # The cohort is the whole active stock, not a newest-N slice of it.
    assert any("where l.is_active" in s and "first_seen_at" not in s
               for s in conn.executed)


def test_field_fill_matrix_fails_when_a_blessed_cell_collapses() -> None:
    from scripts.verify_pipeline import check_field_fill_matrix

    # idnes `condition` is blessed at 65.4% fill; a parser that stopped writing it.
    out = check_field_fill_matrix(
        _MatrixConn(_live_matrix_rows(idnes__condition=0)), T)
    assert out["status"] == "fail"
    assert out["value"] == 1
    assert "idnes/condition" in out["details"]["offenders"][0]


def test_field_fill_matrix_warns_on_a_stale_census(monkeypatch: Any) -> None:
    """Stale census = a portal could have renamed a key and every gate built on the
    census would still agree with itself. A warn here, never a calendar-keyed CI red."""
    import scripts.verify_pipeline as vp
    from scripts.verify_pipeline import check_field_fill_matrix

    monkeypatch.setattr(
        vp.field_census, "load_censuses",
        lambda: [{"portal": "remax", "generated_at": "2020-01-01T00:00:00+00:00"}])
    out = check_field_fill_matrix(_MatrixConn(_live_matrix_rows()), T)
    assert out["status"] == "warn"
    assert out["details"]["stale_census"] and "remax" in out["message"]


def test_a_lane_owes_every_check_it_runs_its_own_budget() -> None:
    from scripts import verify_pipeline as vp

    assert vp.lane_budget_for([]) == 0.0
    assert vp.lane_budget_for(CHECKS) == sum(c.budget_s for c in CHECKS)
    assert vp.lane_budget_for(_registered("jobs", "outbound_url_conformance")) == 165.0


_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


# Checkout, setup-python and `pip install -e .` take about two minutes; the assertions, seconds.
_SETUP_AND_ASSERTIONS_S = 480


def test_the_canary_job_fits_every_github_check_due_at_once() -> None:
    """The canary may find every GitHub-hosted check due at once (after a long gap or a registry
    change), so a registry that outgrows its step must fail HERE, not go quiet in production:
    the shared 120 s budget left 7-17 of 24 checks unrun for weeks. The harness step's own
    limit makes an overrun a red step, not a silent job kill; the poller runs first under its own."""
    import yaml
    from scripts import verify_pipeline as vp

    workflow = yaml.safe_load((_WORKFLOWS / "verify_pipeline.yml").read_text())
    (job,) = workflow["jobs"].values()
    steps = {s["name"]: s for s in job["steps"] if "name" in s}
    poller, harness = steps["Record failed workflow runs"], steps["Measure the GitHub-hosted checks"]
    hosted = [c for c in CHECKS if c.host == "github-actions"]
    assert vp.lane_budget_for(hosted) + vp._JOB_HEADROOM_S <= harness["timeout-minutes"] * 60
    assert ((poller["timeout-minutes"] + harness["timeout-minutes"]) * 60 + _SETUP_AND_ASSERTIONS_S
            <= job["timeout-minutes"] * 60)


def test_no_workflow_passes_a_flag_the_harness_deleted() -> None:
    """--arm-dual-write-parity went in wave 3 with its check, --exit-nonzero-on-fail in wave 2
    (the canary's assertions red a run), --only and --weekly in wave 1. argparse refuses them,
    so a workflow still passing one would crash its harness step on every run instead of
    measuring."""
    import yaml

    runs = [step["run"] for path in sorted(_WORKFLOWS.glob("*.yml"))
            for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values()
            for step in job.get("steps") or []
            if "scripts.verify_pipeline" in step.get("run", "")]
    assert runs, "a workflow runs the harness"
    for run in runs:
        for flag in ("--arm-dual-write-parity", "--exit-nonzero-on-fail", "--only",
                     "--weekly"):
            assert not re.search(rf"{flag}(?![\w-])", run), (flag, run)


def test_the_canary_keeps_the_monitoring_concurrency_group() -> None:
    """One group, so a dispatched run waits for the scheduled one instead of measuring the
    same due checks twice beside it."""
    import yaml

    workflow = yaml.safe_load((_WORKFLOWS / "verify_pipeline.yml").read_text())
    assert workflow["concurrency"]["group"] == "pipeline-verification"


# --- floor_convention (field capture W8) -----------------------------------


class _FloorConn(_ShapeDriftConn):
    """`_fetchall` wants transaction() + cursor(); _ShapeDriftConn already has both."""


def _live_floor_rows() -> list[tuple[Any, ...]]:
    """(source, pairs, mean_delta) as the live cluster read them 2026-09-22, before
    the heal: the six ground=1 portals a storey high, the two canonical ones at their
    own noise floor."""
    return [
        ("remax", 1248, 1.050), ("mmreality", 1574, 0.988), ("sreality", 13575, 0.973),
        ("realitymix", 7416, 0.962), ("bezrealitky", 900, 0.869), ("maxima", 49, 0.816),
        ("ceskereality", 7453, 0.201), ("bazos", 2586, 0.098),
    ]


def test_floor_convention_is_registered() -> None:
    from scripts.verify_pipeline import check_floor_convention

    assert _by_key("floor_convention").fn is check_floor_convention


def test_floor_convention_fails_on_todays_pre_heal_corpus() -> None:
    """The gate W8 is measured by: six portals a whole storey off idnes is a fail, and
    the two portals that were already canonical are NOT named as offenders."""
    from scripts.verify_pipeline import check_floor_convention

    out = check_floor_convention(_FloorConn(_live_floor_rows()), T)
    assert out["status"] == "fail"
    assert out["value"] == 1.05
    assert out["details"]["scored"] == 8
    assert sorted(s.split()[0] for s in out["details"]["offenders"]) == [
        "bezrealitky", "maxima", "mmreality", "realitymix", "remax", "sreality"]
    assert "--write --allow-snapshot-deferral" in out["message"]


def test_floor_convention_never_tells_the_operator_to_heal_a_canonical_portal() -> None:
    """ceskereality's cell declares `ground0`: re-deriving it would shift 34,350 CORRECT
    rows down one storey, so the reparse runbook must never name it."""
    from scripts.verify_pipeline import check_floor_convention

    out = check_floor_convention(_FloorConn([("ceskereality", 7453, 0.62)]), T)
    assert out["status"] == "fail"
    assert "scripts.reparse" not in out["message"]
    assert "re-deriving is NOT the remedy" in out["message"]


def test_floor_convention_is_ok_once_every_portal_reads_the_same_scale() -> None:
    """What the heal buys: the residual is each portal's own sibling noise, not 0."""
    from scripts.verify_pipeline import check_floor_convention

    out = check_floor_convention(_FloorConn(
        [(s, n, d - 1.0) for s, n, d in _live_floor_rows() if d > 0.5]
        + [("ceskereality", 7453, 0.201), ("bazos", 2586, 0.098)]), T)
    assert out["status"] == "ok"
    assert out["details"]["offenders"] == []


def test_floor_convention_is_unknown_rather_than_passing_when_nothing_scored() -> None:
    """A thin sample certifies nothing; `value` stays None so the tile reads em-dash,
    never a 0 that looks like agreement."""
    from scripts.verify_pipeline import check_floor_convention

    out = check_floor_convention(_FloorConn([("maxima", 12, 0.9)]), T)
    assert out["status"] == "unknown"
    assert "sibling pairs" in out["details"]["skipped"]
    assert out["value"] is None
    assert "verified NOTHING" in out["message"]


def test_floor_convention_runs_under_the_per_check_statement_timeout() -> None:
    """The lane's most expensive query (27-35 s measured) is exactly the one that must
    be cancellable server-side: a raw cursor would run it under the 10-minute default."""
    from scripts.verify_pipeline import check_floor_convention

    conn = _FloorConn(_live_floor_rows())
    check_floor_convention(conn, T)
    assert any("statement_timeout" in s for s in conn.executed)


# --- text_extraction_lag (field-capture W7) --------------------------------
#
# The R8 instrument. Its wedge arm is the one that rings when nothing else can —
# a lane that SELECTS nothing looks healthy to every other check — so it has to
# be right about what "claiming none" means.


class _ScriptedConn:
    """A conn that answers each statement from a script keyed on a SQL fragment."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self._answers = answers
        self._last: Any = None

    def transaction(self) -> "_ScriptedConn":
        return self

    def cursor(self) -> "_ScriptedConn":
        return self

    def __enter__(self) -> "_ScriptedConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self._last = next(
            (v for k, v in self._answers.items() if k in sql), [])

    def fetchall(self) -> list[Any]:
        return list(self._last)

    def fetchone(self) -> Any:
        rows = list(self._last)
        return rows[0] if rows else None


def _text_lane_conn(*, waiting: int, oldest_hours: float, claimed: int | None,
                    **last: Any) -> Any:
    lane = None if claimed is None else {"last": {"claimed": claimed, **last}}
    return _ScriptedConn({
        "FROM app_settings": [("gpt-5-mini",)],
        "GROUP BY l.source": ([("bazos", waiting, oldest_hours, oldest_hours / 2)]
                              if waiting else []),
        "percentile_cont(0.99)": [(10, 6.0)],
        "worker_heartbeats": [(lane, 2.0)],
    })


def _open_one_gate(monkeypatch: Any) -> None:
    from scraper import attribute_contract

    monkeypatch.setattr(attribute_contract, "extracted_cells",
                        lambda: {"bazos": ("has_lift",)})


def test_text_extraction_lag_does_not_ring_on_one_quiet_pass(
        monkeypatch: Any) -> None:
    """bazos arrives in bursts — 1,940 rows a day over ~147 distinct minutes — so a great
    many healthy 5-minute passes legitimately claim nothing. Reading that as a wedge is
    the false-red generator this harness exists to replace."""
    from scripts.verify_pipeline import check_text_extraction_lag

    _open_one_gate(monkeypatch)
    out = check_text_extraction_lag(
        _text_lane_conn(waiting=40, oldest_hours=0.05, claimed=0), T)
    assert out["status"] == "ok"


def test_text_extraction_lag_rings_when_the_oldest_row_outlives_the_lane(
        monkeypatch: Any) -> None:
    from scripts.verify_pipeline import check_text_extraction_lag

    _open_one_gate(monkeypatch)
    out = check_text_extraction_lag(
        _text_lane_conn(waiting=50205, oldest_hours=9.0, claimed=0), T)
    assert out["status"] == "fail"
    assert "claimed" in " ".join(out["details"]["offenders"])


def test_text_extraction_lag_rings_when_the_lane_is_not_in_the_heartbeat(
        monkeypatch: Any) -> None:
    """The estimation lane's failure: never registered, therefore invisible to every
    other monitor. That one needs no staleness at all."""
    from scripts.verify_pipeline import check_text_extraction_lag

    _open_one_gate(monkeypatch)
    out = check_text_extraction_lag(
        _text_lane_conn(waiting=3, oldest_hours=0.01, claimed=None), T)
    assert out["status"] == "fail"
    assert "not in the heartbeat" in " ".join(out["details"]["offenders"])


def test_text_extraction_lag_names_the_open_scope() -> None:
    """The check's scope is the contract's open gates — the same declaration the lane
    reads — so a report and the selector can never disagree about who is waiting."""
    from scripts.verify_pipeline import check_text_extraction_lag

    out = check_text_extraction_lag(
        _text_lane_conn(waiting=0, oldest_hours=0.0, claimed=0), T)
    assert out["status"] == "ok"
    assert out["details"]["scope"] == {"bazos": ["floor", "has_lift"]}


def test_text_extraction_lag_fails_on_a_pass_the_provider_stopped(
        monkeypatch: Any) -> None:
    """2026-10-02..10: OpenAI out of credit, and the lane claimed 500 a pass and wrote
    nothing. The wedge arm needs `claimed == 0`, so it never fired, and the check blamed
    backlog age, never the provider. The provider's own words are the alarm, at any age."""
    from scripts.verify_pipeline import check_text_extraction_lag

    _open_one_gate(monkeypatch)
    # The heartbeat's `fatal`, as it read on 2026-10-10.
    fatal = ('openai call failed: HTTP 429 {\n    "error": {\n        "message": "You have '
             'no credits remaining. Add credits to continue using the API at '
             'https://platform.openai.com/settings/organization/billing/.",\n')
    out = check_text_extraction_lag(
        _text_lane_conn(waiting=500, oldest_hours=0.05, claimed=500, written=0,
                        aborted=True, fatal=fatal, model="gpt-5.6-luna"), T)
    assert out["status"] == "fail"
    assert '"message": "You have no credits remaining.' in out["message"]
    assert "\n" not in out["message"]
    # The model the provider refused (not today's setting), and both remedies: four of
    # vision_batch.FATAL_MARKERS are key errors, fixed on the worker's Railway service.
    assert "refused gpt-5.6-luna" in out["message"]
    assert "credit or quota" in out["message"] and "OPENAI_API_KEY" in out["message"]


def test_text_extraction_lag_reads_a_pass_at_its_ceiling_as_bounded(
        monkeypatch: Any) -> None:
    """`aborted` with no `fatal` is the pass's own $/time ceiling: it wrote, and a backlog
    it leaves behind is what the age arms are for."""
    from scripts.verify_pipeline import check_text_extraction_lag

    _open_one_gate(monkeypatch)
    out = check_text_extraction_lag(
        _text_lane_conn(waiting=500, oldest_hours=0.05, claimed=500, written=410,
                        aborted=True, fatal=None), T)
    assert out["status"] == "ok"


# --- the unmeasured rail (health wave 1) ----------------------------------------
#
# A measurement that did not happen is Not measured: never ok, and never a warn that the
# ladder would read as a measured Watch holding an incident open.

_UNMEASURED_MARKERS = {"timed_out", "errored", "skipped"}


def _unmeasured_results() -> list[dict[str, Any]]:
    """One result from every path that measures nothing (a check the lane budget does not
    reach writes no result at all, so it has none here)."""
    from unittest import mock

    import scripts.verify_pipeline as vp
    from scripts.verify_outbound_urls import check_outbound_url_conformance

    def _boom(conn: Any, thresholds: Any) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    def _cancelled(conn: Any, thresholds: Any) -> dict[str, Any]:
        raise RuntimeError("canceling statement due to statement timeout")

    sampled = _ShapeDriftConn([(1, "remax", "byt", None, "https://www.remax-czech.cz/x/1")])
    with mock.patch.object(platform_checks, "_MIGRATIONS_DIR",
                           Path("/no-such-checkout/migrations")):
        no_migration_files = check_migration_drift(_DriftConn({}), T)
    return [
        *run_checks(None, {}, checks=[_stub("boom", _boom), _stub("slow", _cancelled)]),
        no_migration_files,
        vp._inert_measure_check("ppm2_basis_floor_share", None),
        vp._inert_measure_check("ppm2_basis_floor_share", "relation does not exist"),
        vp._unmeasured_check("area_vs_usable_divergence", 3, "100+ rows with both areas"),
        vp.check_floor_convention(_FloorConn([("maxima", 12, 0.9)]), T),
        vp.check_location_payload_shape_drift(_ShapeDriftConn([("sreality", 10, 10)]), T),
        check_outbound_url_conformance(_ShapeDriftConn([]), T),
        check_outbound_url_conformance(sampled, T, budget_s=0.0),
        vp.check_location_town_coverage(_TownCoverageConn([]), T),
    ]


def test_every_unmeasured_path_is_unknown_and_says_why() -> None:
    results = _unmeasured_results()
    assert len(results) == 11
    errored, timed_out = results[:2]
    assert errored["details"]["errored"] is True and "kaboom" in errored["details"]["error"]
    assert timed_out["details"]["timed_out"] is True
    for r in results:
        markers = _UNMEASURED_MARKERS & set(r["details"])
        assert markers, r["check_key"]
        assert r["status"] == "unknown", (r["check_key"], r["status"])
        assert r["value"] is None and r["message"], r["check_key"]
        assert "UNKNOWN" not in r["message"], r["check_key"]  # the page's words: Not measured
        if "skipped" in markers:  # the reason as text, never a bare flag
            assert isinstance(r["details"]["skipped"], str), r["check_key"]
            assert r["details"]["skipped"].strip(), r["check_key"]


def test_no_unmeasured_result_reads_as_a_measured_watch() -> None:
    """The rail. Before wave 1 every one of these was a `warn`, which the ladder read as a
    measured Watch: in 30 days 34 of 88 'Recovered' notices followed an unmeasured run."""
    for r in _unmeasured_results():
        assert not (r["status"] == "warn" and _UNMEASURED_MARKERS & set(r["details"])), r


def test_a_stalled_registry_is_a_problem_even_when_no_listing_was_read() -> None:
    from scripts.verify_pipeline import check_location_town_coverage

    out = check_location_town_coverage(
        _TownCoverageConn([], registry=("ruian:2026-07-01", 80, None, None)), T)
    assert out["status"] == "fail" and "Registry stalled" in out["message"]


# --- the checks that read on a raw cursor now spend their own wall clock (health wave 2) ---


class _BoundConn:
    """Records each statement and whether a SET LOCAL statement_timeout armed it inside the
    same transaction; answers by SQL fragment, else with no rows."""

    def __init__(self, answers: dict[str, list[Any]] | None = None) -> None:
        self._answers = answers or {}
        self.sent: list[tuple[str, bool]] = []
        self._in_transaction = False
        self._armed = False
        self._rows: list[Any] = []

    @contextlib.contextmanager
    def transaction(self) -> Any:
        self._in_transaction = True
        try:
            yield
        finally:
            self._in_transaction = self._armed = False

    def cursor(self) -> "_BoundConn":
        return self

    def __enter__(self) -> "_BoundConn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("SET LOCAL statement_timeout"):
            self._armed = self._in_transaction
            return
        self.sent.append((sql, self._armed))
        self._armed = False
        self._rows = next((v for k, v in self._answers.items() if k in sql), [])

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


def _own_sql(key: str) -> list[str]:
    """The statements each formerly raw-cursor check sends, as the code spells them."""
    import scripts.verify_pipeline as vp
    from toolkit import description_extraction as text_lane

    return {
        "text_extraction_lag": ["SELECT value FROM app_settings WHERE key = %s",  # resolve_model
                                text_lane.LLM_TEXT_SOURCES_SQL,  # lane_scope
                                text_lane.ELIGIBLE_LAG_SQL, text_lane.EXTRACTION_LATENCY_SQL,
                                vp._TEXT_EXTRACT_LANE_SQL],
        "migration_drift": [MIGRATION_OBJECT_PROBE_SQL],
        "outbound_url_coverage": [vp._OUTBOUND_URL_COVERAGE_SQL],
    }[key]


@pytest.mark.parametrize("key", ["text_extraction_lag", "migration_drift", "outbound_url_coverage"])
def test_each_formerly_raw_cursor_check_sends_its_sql_on_its_own_wall_clock(
    key: str, monkeypatch: Any,
) -> None:
    """These three read on a raw cursor, under the connection's default timeout and past any
    budget. Each now sends the same SQL through _fetchone/_fetchall, every statement armed
    with what its check has left."""
    answers: dict[str, list[Any]] = {}
    if key == "text_extraction_lag":
        _open_one_gate(monkeypatch)
        answers = {"FROM app_settings": [("gpt-5-mini",)]}
    conn = _BoundConn(answers)
    _by_key(key).fn(conn, T)
    assert [sql for sql, armed in conn.sent if armed] == _own_sql(key)
    assert all(armed for _, armed in conn.sent), key

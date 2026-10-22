"""Hermetic tests for scripts.health_canary: its exit codes through main() over a fake database.

The canary's SQL is PREPAREd against the replayed schema by tests/test_sql_schema_prepare.py in
CI's database job; here the fake answers each read with the rows that SQL would return.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml

import scripts.health_canary as hc
from scripts.record_workflow_failures import MONITOR_WORKFLOW_NAME

_WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _ago(minutes: float) -> _dt.datetime:
    """A stamp `minutes` before the clock main() reads; every threshold sits minutes away."""
    return _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=minutes)


def _row(minutes: float, *failing: str, incidents: tuple[str, ...] = ()) -> tuple[Any, ...]:
    """One liveness result as _CELLS_SQL returns it: these cells failing, else one OK worker."""
    cells = ([{"subject": s, "status": "fail"} for s in failing]
             or [{"subject": "worker", "status": "ok"}])
    return (_ago(minutes), "fail" if failing else "ok", cells, list(incidents))


def _rung(minutes: float, *, key: str = "llm_errors", subject: str = "openai",
          at: _dt.datetime | None = None) -> tuple[Any, ...]:
    """One open, unacknowledged alarm incident told `minutes` ago, as _RUNGS_SQL returns it."""
    return (key, f"cell:{key}:{subject}", at or _ago(minutes))


class _Conn:
    """Answers the canary's reads from canned rows and records every statement it is sent."""

    def __init__(self, *, newest: Any, declared: Any, rows: list[Any], rungs: list[Any],
                 broken: Exception | None = None) -> None:
        self._answers = {"select 1": [(1,)], hc._LIVENESS_SQL: [(newest, declared)],
                         hc._CELLS_SQL: rows, hc._RUNGS_SQL: rungs}
        self._broken = broken
        self.executed: list[str] = []

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def transaction(self) -> "_Conn":
        return self

    def cursor(self) -> "_Conn":
        return self

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)
        if self._broken is not None and not sql.startswith("SET LOCAL"):
            raise self._broken

    def fetchall(self) -> list[Any]:
        return self._answers[self.executed[-1]]


def _conn(**answers: Any) -> _Conn:
    """A database where everything is alive, unless a test says otherwise."""
    state: dict[str, Any] = {"newest": _ago(3), "declared": _ago(2 * 24 * 60),
                             "rows": [_row(3)], "rungs": []}
    return _Conn(**{**state, **answers})


def _main(monkeypatch: Any, conn: _Conn) -> int:
    monkeypatch.setattr(hc, "connect", lambda **_kw: conn)
    return hc.main()


# --- db_answers ----------------------------------------------------------------


def test_a_database_that_does_not_answer_exits_2(monkeypatch: Any, capsys: Any) -> None:
    def _refuse(**_kw: Any) -> None:
        raise psycopg.OperationalError("connection to server failed:\n\tConnection refused")

    monkeypatch.setattr(hc, "connect", _refuse)
    assert hc.main() == 2
    assert "::error::CANARY FAIL db_answers: connection to server failed: Connection refused" in (
        capsys.readouterr().out)


def test_a_read_cancelled_by_its_statement_timeout_exits_2(monkeypatch: Any, capsys: Any) -> None:
    broken = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    assert _main(monkeypatch, _conn(broken=broken)) == 2
    assert "CANARY FAIL db_answers: canceling statement" in capsys.readouterr().out


def test_no_database_url_exits_2_without_connecting(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert hc.main() == 2
    assert "SUPABASE_DB_URL" in capsys.readouterr().out


def test_everything_alive_exits_0_and_says_so(monkeypatch: Any, capsys: Any) -> None:
    assert _main(monkeypatch, _conn()) == 0
    out = capsys.readouterr().out
    assert "CANARY OK" in out and "CANARY FAIL" not in out


def test_every_read_runs_under_its_own_statement_timeout(monkeypatch: Any) -> None:
    conn = _conn()
    assert _main(monkeypatch, conn) == 0
    timeouts, reads = conn.executed[0::2], conn.executed[1::2]
    assert timeouts == ["SET LOCAL statement_timeout = '10s'"] * 4
    assert reads == ["select 1", hc._LIVENESS_SQL, hc._CELLS_SQL, hc._RUNGS_SQL]


# --- liveness_fresh ------------------------------------------------------------


def test_a_stale_liveness_row_exits_1(monkeypatch: Any, capsys: Any) -> None:
    assert _main(monkeypatch, _conn(newest=_ago(47), rows=[])) == 1
    out = capsys.readouterr().out
    assert "::error::CANARY FAIL liveness_fresh" in out  # one GitHub error annotation per failure
    assert "CANARY FAIL liveness_fresh: the newest liveness result is 47 min old" in out


def test_no_liveness_row_and_no_registry_key_is_skipped(monkeypatch: Any, capsys: Any) -> None:
    """Migration 620 not applied yet: the canary merges before the apply without going red."""
    assert _main(monkeypatch, _conn(newest=None, declared=None, rows=[])) == 0
    assert "CANARY SKIP liveness_fresh" in capsys.readouterr().out


def test_a_registered_watcher_that_never_wrote_fails_past_the_limit(monkeypatch: Any,
                                                                   capsys: Any) -> None:
    assert _main(monkeypatch, _conn(newest=None, declared=_ago(120), rows=[])) == 1
    assert "pg_cron has never written a liveness result" in capsys.readouterr().out
    # Before pg_cron's first tick after the apply, a missing row is not yet a problem.
    assert _main(monkeypatch, _conn(newest=None, declared=_ago(4), rows=[])) == 0


# --- failing_cells_have_incidents ---------------------------------------------------


def test_a_cell_failing_for_20_min_without_an_incident_exits_1(monkeypatch: Any,
                                                              capsys: Any) -> None:
    rows = [_row(m, "worker") for m in (0, 5, 10, 15, 20)]
    assert _main(monkeypatch, _conn(rows=rows)) == 1
    out = capsys.readouterr().out
    assert "CANARY FAIL failing_cells_have_incidents: liveness cell worker" in out
    assert "at least 20 min" in out and "teller is not running" in out


def test_the_same_cell_with_an_open_incident_exits_0(monkeypatch: Any) -> None:
    rows = [_row(m, "worker", incidents=("cell:liveness:worker",)) for m in (0, 5, 10, 15, 20)]
    assert _main(monkeypatch, _conn(rows=rows)) == 0


def test_only_a_cell_failing_since_the_grace_is_asserted(monkeypatch: Any) -> None:
    """The streak walks back from the newest row: an OK row ends it, and so does a cell
    that failed long ago but passes now."""
    young = [_row(0, "api"), _row(5, "api"), _row(10, "api"), _row(15), _row(20)]
    assert _main(monkeypatch, _conn(rows=young)) == 0
    healed = [_row(0), _row(5), _row(20, "api"), _row(25, "api")]
    assert _main(monkeypatch, _conn(rows=healed)) == 0


def test_each_failing_cell_is_judged_by_its_own_incident(monkeypatch: Any, capsys: Any) -> None:
    rows = [_row(m, "worker", "stale:llm_errors", incidents=("cell:liveness:worker",))
            for m in (0, 5, 10, 15)]
    assert _main(monkeypatch, _conn(rows=rows)) == 1
    out = capsys.readouterr().out
    assert "liveness cell stale:llm_errors" in out and "liveness cell worker" not in out


# --- alarms_acknowledged ---------------------------------------------------------


def test_an_unacknowledged_alarm_rung_exits_1(monkeypatch: Any, capsys: Any) -> None:
    assert _main(monkeypatch, _conn(rungs=[_rung(20)])) == 1
    assert ("CANARY FAIL alarms_acknowledged: the llm_errors alarm told 20 min ago (openai) is "
            "open and unacknowledged") in capsys.readouterr().out


def test_a_rung_inside_its_grace_is_not_asserted_yet(monkeypatch: Any) -> None:
    assert _main(monkeypatch, _conn(rungs=[_rung(10)])) == 0


def test_a_rung_older_than_the_window_is_not_asserted(monkeypatch: Any) -> None:
    """The next reminder (6 h, 24 h, 72 h, then weekly) stamps a new rung and re-arms it."""
    assert _main(monkeypatch, _conn(rungs=[_rung(13 * 60)])) == 0


def test_one_reason_per_check_and_rung(monkeypatch: Any, capsys: Any) -> None:
    """A rung stamps every cell it tells at once, so its cells share one annotation."""
    told = _ago(30)
    rungs = [_rung(0, subject="openai", at=told), _rung(0, subject="gemini", at=told),
             _rung(90, key="capacity", subject="connections")]
    assert _main(monkeypatch, _conn(rungs=rungs)) == 1
    out = capsys.readouterr().out
    assert out.count("CANARY FAIL alarms_acknowledged") == 2
    assert "the llm_errors alarm told 30 min ago (gemini, openai)" in out
    assert "the capacity alarm told 90 min ago (connections)" in out


def test_the_rule_reads_only_open_unacknowledged_alarm_incidents() -> None:
    """The fake cannot run the SQL, so pin what it selects: acknowledged, recovered, gauge and
    unregistered incidents never reach the rule, and no bell state can answer for an alarm."""
    sql = " ".join(hc._RUNGS_SQL.split())
    assert ("from ops_incidents i join health_checks hc "
            "on starts_with(i.signature, 'cell:' || hc.key || ':')") in sql
    for clause in ("i.resolved_at is null", "i.acknowledged_at is null", "hc.class = 'alarm'",
                   "i.alerted_at > %(since)s"):
        assert clause in sql
    for bell in ("notification_dispatches", "channel_sends", "seen_at", "dedupe_key"):
        assert bell not in sql


# --- the workflow -----------------------------------------------------------------


def test_the_canary_is_the_one_monitoring_workflow_and_asserts_last() -> None:
    """A failed poll or harness run must not skip the assertions: they are the run's verdict."""
    assert not (_WORKFLOWS / "llm_health.yml").exists()
    assert not (_WORKFLOWS / "monitor_workflow_failures.yml").exists()
    doc = yaml.safe_load((_WORKFLOWS / "verify_pipeline.yml").read_text())
    assert doc["name"] == MONITOR_WORKFLOW_NAME  # the poller skips its own runs by this name
    assert doc[True]["schedule"] == [{"cron": "15 * * * *"}]  # YAML 1.1 reads `on` as True
    (job,) = doc["jobs"].values()
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    steps = [s for s in job["steps"] if "scripts." in s.get("run", "")]
    assert [s["name"] for s in steps] == ["Record failed workflow runs",
                                          "Measure the GitHub-hosted checks",
                                          "Assert the watchers and the teller are alive"]
    assert job["steps"][-1] is steps[-1]
    assert steps[-1]["run"].strip() == "python -m scripts.health_canary"
    # the harness runs after a failed poll but not after a cancel; the assertions always
    assert [s.get("if") for s in steps[1:]] == ["${{ !cancelled() }}", "always()"]
    assert "--host github-actions" in steps[1]["run"]
    assert "--exit-nonzero-on-fail" not in steps[1]["run"]

"""Hermetic tests for scripts.health_canary: its exit codes through main() over a fake database.

The canary's SQL is PREPAREd against the replayed schema by tests/test_sql_schema_prepare.py in
CI's database job; here the fake answers each read with the rows that SQL would return.
"""

from __future__ import annotations

import datetime as _dt
import re
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


def _notice(minutes: float, *, kind: str = "onset", key: str = "llm_errors",
            cls: str | None = "alarm", seen: bool = False, delivered: bool = False,
            open_unacked: bool = True) -> tuple[Any, ...]:
    """One system alert as _NOTICES_SQL returns it."""
    return (f"sys:{key}:{kind}:2026-10-18T10:00:00Z", _ago(minutes), seen, delivered, cls,
            open_unacked)


def _counted(status: str) -> bool:
    """Whether _NOTICES_SQL counts a channel_sends row in this status as the notice delivered."""
    statuses = re.search(r"s\.status in \(([^)]*)\)", hc._NOTICES_SQL)
    assert statuses, "_NOTICES_SQL no longer reads `s.status in (...)`: re-pin the delivery tests"
    return status in {s.strip(" '") for s in statuses[1].split(",")}


class _Conn:
    """Answers the canary's reads from canned rows and records every statement it is sent."""

    def __init__(self, *, newest: Any, declared: Any, rows: list[Any], notices: list[Any],
                 broken: Exception | None = None) -> None:
        self._answers = {"select 1": [(1,)], hc._LIVENESS_SQL: [(newest, declared)],
                         hc._CELLS_SQL: rows, hc._NOTICES_SQL: notices}
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
                             "rows": [_row(3)], "notices": []}
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
    assert _main(monkeypatch, _conn(notices=[_notice(30, seen=True)])) == 0
    out = capsys.readouterr().out
    assert "CANARY OK" in out and "CANARY FAIL" not in out


def test_every_read_runs_under_its_own_statement_timeout(monkeypatch: Any) -> None:
    conn = _conn()
    assert _main(monkeypatch, conn) == 0
    timeouts, reads = conn.executed[0::2], conn.executed[1::2]
    assert timeouts == ["SET LOCAL statement_timeout = '10s'"] * 4
    assert reads == ["select 1", hc._LIVENESS_SQL, hc._CELLS_SQL, hc._NOTICES_SQL]


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


# --- alarm_notices_handled -----------------------------------------------------


def test_an_unseen_undelivered_alarm_onset_with_an_open_incident_exits_1(
    monkeypatch: Any, capsys: Any,
) -> None:
    assert _main(monkeypatch, _conn(notices=[_notice(20)])) == 1
    out = capsys.readouterr().out
    assert ("CANARY FAIL alarm_notices_handled: the llm_errors onset system alert sent 20 min "
            "ago") in out


def test_an_unseen_reminder_is_asserted_like_an_onset(monkeypatch: Any) -> None:
    assert _main(monkeypatch, _conn(notices=[_notice(20, kind="reminder")])) == 1


@pytest.mark.parametrize("handled", [{"seen": True}, {"open_unacked": False}],
                         ids=["seen", "acknowledged"])
def test_a_handled_alarm_notice_exits_0(monkeypatch: Any, handled: dict[str, bool]) -> None:
    assert _main(monkeypatch, _conn(notices=[_notice(20, **handled)])) == 0


@pytest.mark.parametrize("status, code", [("delivered", 0), ("sent", 0), ("queued", 1),
                                          ("failed", 1), ("bounced", 1), ("suppressed", 1)])
def test_a_channel_send_handles_the_notice_once_sent(monkeypatch: Any, status: str,
                                                     code: int) -> None:
    """Resend's webhook turns `sent` into `delivered` later; Telegram has no webhook, so a
    sent-only row must count. A send queued, failed, bounced or suppressed reached no one."""
    assert _main(monkeypatch, _conn(notices=[_notice(20, delivered=_counted(status))])) == code


def test_a_notice_inside_its_grace_is_not_asserted_yet(monkeypatch: Any) -> None:
    assert _main(monkeypatch, _conn(notices=[_notice(10)])) == 0


def test_gauge_notices_are_never_asserted(monkeypatch: Any) -> None:
    """The operator's ruling: gauges ring daily in the bell; the email path is the alarms'."""
    assert _main(monkeypatch, _conn(notices=[_notice(20, cls="gauge")])) == 0


def test_a_notice_older_than_the_window_is_not_asserted(monkeypatch: Any) -> None:
    assert _main(monkeypatch, _conn(notices=[_notice(13 * 60)])) == 0


def test_recovery_and_unregistered_notices_are_ignored(monkeypatch: Any) -> None:
    notices = [_notice(20, kind="recovery"), _notice(20, key="ops_incident", cls=None)]
    assert _main(monkeypatch, _conn(notices=notices)) == 0


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

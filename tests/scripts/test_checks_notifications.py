"""Hermetic tests for scripts.checks.notifications: the push ledger's four cells (stall, recall, failures, the brake
shown) over the wave-3 fake connection, its registry row and thresholds, and its base-dependency import. The SQL
itself runs on the real schema in tests/test_notifications_push_live.py."""

from __future__ import annotations

import ast
import datetime as _dt
import inspect
from typing import Any

import pytest

from api import push_delivery as pd
from scripts.checks import notifications as nc
from scripts.verify_pipeline import CHECKS, THRESHOLDS
from tests.scripts.test_checks_platform import _NOW, _Conn

T = {k: THRESHOLDS[k] for k in ("notifications_stall_min", "notifications_recall_grace_min",
                                "notifications_failures_fail")}


def _answers(*, beat_age_s: float = 30.0, last: Any = None, devices: int = 1, stall: tuple[Any, Any] = (0, None),
             recall: tuple[Any, Any] = (0, None), failures: tuple[Any, Any] = (0, [])) -> dict[str, Any]:
    beat = _NOW - _dt.timedelta(seconds=beat_age_s)
    return {
        nc._PUSH_LANE_SQL: [(beat, {"live_devices": devices, "pushes": 0} if last is None else last)],
        pd.RECALL_SQL: [recall], pd.STALL_SQL: [stall], pd.FAILURES_SQL: [failures],
        pd.LIVE_DEVICES_SQL: [(devices,)],
    }


def _cells(out: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {c["subject"]: (c["status"], c["message"]) for c in out["details"]["cells"]}


def test_a_draining_ledger_is_four_ok_cells_and_every_read_runs_on_the_checks_clock() -> None:
    conn = _Conn(_answers())
    out = nc.check_notifications(conn, T)
    assert out["status"] == "ok" and out["check_key"] == "notifications"
    assert _cells(out) == {
        "push/stall": ("ok", "no delivery due for over 10 min"),
        "push/recall": ("ok", "every visible event reached its devices' ledger within 5 min"),
        "push/failures": ("ok", "0 pushes failed for good in 24 h"),
        "push/unconfigured": ("ok", "configured"),
    }
    assert all(armed for _sql, _params, armed in conn.sent), "every statement under SET LOCAL statement_timeout"
    sent = {sql: params for sql, params, _ in conn.sent}
    assert sent[pd.STALL_SQL] == {"stall_min": 10} and sent[pd.RECALL_SQL] == {"grace_min": 5}


def test_a_stalled_lane_and_a_lost_push_are_problems() -> None:
    out = nc.check_notifications(_Conn(_answers(stall=(4, 1_500.0), recall=(2, 900.0))), T)
    cells = _cells(out)
    assert out["status"] == "fail" and out["value"] == 2
    assert cells["push/stall"] == ("fail", "4 deliveries due for over 10 min (the oldest 25 min)")
    assert cells["push/recall"] == (
        "fail", "2 (event, device) pairs have no delivery 5 min after the event (the oldest 15 min)")


@pytest.mark.parametrize(("failed", "status"), [(2, "ok"), (3, "fail"), (9, "fail")])
def test_three_terminal_failures_a_day_are_a_problem(failed: int, status: str) -> None:
    out = nc.check_notifications(_Conn(_answers(failures=(failed, ["BadDeviceToken", "400"]))), T)
    assert _cells(out)["push/failures"] == (status, f"{failed} pushes failed for good in 24 h (400, BadDeviceToken)")


def test_the_brake_with_a_device_waiting_is_watch_and_recall_is_not_judged() -> None:
    conn = _Conn(_answers(last={"unconfigured": True}, devices=2, recall=(7, 600.0)))
    out = nc.check_notifications(conn, T)
    cells = _cells(out)
    assert out["status"] == "warn"
    assert cells["push/unconfigured"][0] == "warn" and "2 devices are registered" in cells["push/unconfigured"][1]
    assert cells["push/recall"] == ("ok", "not judged while the lane is braked (push/unconfigured)")
    assert pd.RECALL_SQL not in [sql for sql, _p, _a in conn.sent]


def test_the_brake_with_no_device_is_ok() -> None:
    out = nc.check_notifications(_Conn(_answers(last={"unconfigured": True}, devices=0)), T)
    assert out["status"] == "ok"
    assert _cells(out)["push/unconfigured"] == ("ok", "braked, and no device is registered")


@pytest.mark.parametrize(("answers", "why"), [
    (_answers(beat_age_s=900.0), "the worker's heartbeat is stale; the watcher's worker cell judges that"),
    ({**_answers(), nc._PUSH_LANE_SQL: []}, "no realtime-worker heartbeat on record"),
    (_answers(last="not an object"), "the push lane has not completed a pass in this worker"),
])
def test_a_heartbeat_that_cannot_say_leaves_the_brake_not_measured(answers: dict[str, Any], why: str) -> None:
    out = nc.check_notifications(_Conn(answers), T)
    assert out["details"]["not_measured"] == {"unconfigured": why}
    assert set(_cells(out)) == {"push/stall", "push/recall", "push/failures"}


def test_the_registry_row_is_a_ten_minute_worker_alarm_owned_by_the_feed() -> None:
    (c,) = [c for c in CHECKS if c.key == "notifications"]
    assert (c.fn, c.domain, c.cls, c.host, c.cadence_s, c.label, c.unit, c.owner_path, c.budget_s) == (
        nc.check_notifications, "platform", "alarm", "realtime-worker", 600, "Push notifications", "cells",
        "/notifications", 5.0)
    keys = [c.key for c in CHECKS]
    assert keys.index("notifications") == keys.index("dedup") + 1 == keys.index("parsing") - 1
    assert T == {"notifications_stall_min": 10, "notifications_recall_grace_min": 5,
                 "notifications_failures_fail": 3}


def test_the_module_imports_with_the_base_dependencies() -> None:
    """GitHub's canary imports the registry after `pip install -e .` alone (test.yml): the api package (pydantic,
    cryptography) is reached only inside the check, which runs on the worker."""
    tree = ast.parse(inspect.getsource(nc))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not [n for n in top if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("api")]
    assert "from api import push_delivery as pd" in inspect.getsource(nc.check_notifications)

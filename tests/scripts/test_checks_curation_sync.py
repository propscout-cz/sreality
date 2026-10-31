"""Hermetic tests for the `curation_sync` alarm (scripts.checks.platform, docs/design/curation-sync.md § 3.9): its
`triggers` and `board_bumps` cells over the wave-3 fake connection, and its registry row. The SQL runs on the replayed
schema in tests/test_curation_clock_live.py."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from psycopg import errors as pg_errors

from scripts.checks import platform as plat
from scripts.verify_pipeline import CHECKS, THRESHOLDS
from tests.scripts.test_checks_platform import _NOW, _Conn

T = {"CURATION_CLOCK_TRIGGERS": 12}


def _answers(*, armed: int = 12, found: int = 12, off: list[str] | None = None,
             bumps: Any = (0, 0, None, 9)) -> dict[str, Any]:
    return {plat._CURATION_TRIGGERS_SQL: [(armed, found, off or [])],
            plat._CURATION_BOARD_BUMPS_SQL: bumps if isinstance(bumps, BaseException) else [bumps]}


def _cells(out: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {c["subject"]: (c["status"], c["message"]) for c in out["details"]["cells"]}


def test_twelve_armed_triggers_and_every_event_bumped_are_two_ok_cells_on_the_checks_clock() -> None:
    conn = _Conn(_answers())
    out = plat.check_curation_sync(conn, T)
    assert out["status"] == "ok" and out["check_key"] == "curation_sync"
    assert _cells(out) == {
        "triggers": ("ok", "all 12 curation-clock triggers are enabled and deferred"),
        "board_bumps": ("ok", "every one of the 9 card events of the last 24 h bumped its account's board clock"),
    }
    assert all(armed for _sql, _params, armed in conn.sent), "every statement under SET LOCAL statement_timeout"


def test_a_disabled_or_missing_trigger_is_a_problem_naming_the_table() -> None:
    out = plat.check_curation_sync(_Conn(_answers(armed=11, found=12, off=["tags"])), T)
    status, message = _cells(out)["triggers"]
    assert out["status"] == "fail" and status == "fail"
    assert message.startswith("11 of 12 curation-clock triggers are enabled and deferred (12 found; off on tags)")
    [facts] = [c["facts"] for c in out["details"]["cells"] if c["subject"] == "triggers"]
    assert facts == {"expected": 12, "armed": 11, "found": 12, "off": ["tags"]}
    missing = plat.check_curation_sync(_Conn(_answers(armed=0, found=0)), T)
    assert _cells(missing)["triggers"][0] == "fail", "no trigger at all (696 not applied) is a Problem"


def test_a_thirteenth_armed_trigger_is_a_problem_too() -> None:
    """Exactly the threshold: a stray extra trigger means CLOCKED_TABLES, the census and the threshold disagree."""
    assert _cells(plat.check_curation_sync(_Conn(_answers(armed=13, found=13)), T))["triggers"][0] == "fail"


def test_a_card_event_with_no_board_bump_after_it_is_a_problem() -> None:
    first = _NOW - _dt.timedelta(hours=3)
    out = plat.check_curation_sync(_Conn(_answers(bumps=(2, 1, first, 9))), T)
    status, message = _cells(out)["board_bumps"]
    assert out["status"] == "fail" and out["value"] == 1 and status == "fail"
    assert message.startswith("2 of 9 card events of the last 24 h (1 accounts, the oldest 3.0 h ago) have no "
                              "board clock bump at or after them")


def test_an_absent_clock_leaves_board_bumps_not_measured_and_the_triggers_judged() -> None:
    out = plat.check_curation_sync(_Conn(_answers(armed=0, found=0, bumps=pg_errors.UndefinedTable(
        'relation "curation_clock" does not exist'))), T)
    assert set(_cells(out)) == {"triggers"} and out["status"] == "fail"
    assert out["details"]["not_measured"]["board_bumps"].startswith("the curation clock is absent")


def test_the_registry_row_is_an_hourly_worker_alarm_after_notifications() -> None:
    (c,) = [c for c in CHECKS if c.key == "curation_sync"]
    assert (c.fn, c.domain, c.cls, c.host, c.cadence_s, c.label, c.unit, c.owner_path, c.budget_s) == (
        plat.check_curation_sync, "platform", "alarm", "realtime-worker", 3600, "Curation sync", "cells", "", 5.0)
    keys = [c.key for c in CHECKS]
    assert keys.index("curation_sync") == keys.index("notifications") + 1 == keys.index("parsing") - 1
    assert {k: THRESHOLDS[k] for k in T} == T

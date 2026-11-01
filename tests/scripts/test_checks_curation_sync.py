"""Hermetic tests for the `curation_sync` alarm (scripts.checks.platform, docs/design/curation-sync.md § 3.9): its
`triggers` and `board_bumps` cells (W3), its `reads` and `load` cells (W4) over the wave-3 fake connection, and its
registry row. The clock's SQL runs on the replayed schema in tests/test_curation_clock_live.py; pg_stat_statements is
the platform's, absent there (allowlisted in the PREPARE sweep), so `reads` and `load` read Not measured in CI."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from psycopg import errors as pg_errors

import pytest

from scripts.checks import platform as plat
from scripts.checks.common import PREVIOUS_CELLS_SQL
from scripts.verify_pipeline import CHECKS, THRESHOLDS
from tests.scripts.test_checks_platform import _NOW, _Conn

T = {"CURATION_CLOCK_TRIGGERS": 12, "CURATION_CLOCK_QUIET_H": 24, "CURATION_CLOCK_LOAD_PCT": 5.0}
_RESET = _dt.datetime(2026, 8, 11, 5, 34, tzinfo=_dt.timezone.utc)
_NO_PGSS = pg_errors.UndefinedTable('relation "pg_stat_statements" does not exist')


def _answers(*, armed: int = 12, found: int = 12, off: list[str] | None = None,
             bumps: Any = (0, 0, None, 9), statements: Any = _NO_PGSS, masked: int = 0,
             previous: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The clock's two reads; pg_stat_statements absent unless `statements` = (calls, clock_ms, all_ms, reset), with
    `masked` entries of authenticated whose text the worker may not read."""
    def one(answer: Any) -> Any:
        return answer if isinstance(answer, BaseException) else [answer]
    stats = statements if isinstance(statements, BaseException) else (*statements, masked)
    return {plat._CURATION_TRIGGERS_SQL: [(armed, found, off or [])],
            plat._CURATION_BOARD_BUMPS_SQL: one(bumps),
            plat._CURATION_STATEMENTS_SQL: one(stats),
            PREVIOUS_CELLS_SQL: [] if previous is None else [(_NOW - _dt.timedelta(hours=1), previous)]}


def _cell(out: dict[str, Any], subject: str) -> dict[str, Any]:
    (c,) = [c for c in out["details"]["cells"] if c["subject"] == subject]
    return c


def _stored(*, calls: int = 150, quiet_since: _dt.datetime | None = None, clock_ms: float = 100.0,
            all_ms: float = 10_000.0, reset: _dt.datetime = _RESET) -> list[dict[str, Any]]:
    """The previous result's two cells, as pipeline_check_results stores them."""
    return [{"subject": "reads", "facts": {"calls": calls, "quiet_since": (quiet_since or _NOW).isoformat(),
                                           "stats_reset": reset.isoformat()}},
            {"subject": "load", "facts": {"clock_ms": clock_ms, "all_ms": all_ms, "stats_reset": reset.isoformat()}}]


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
    assert out["details"]["not_measured"] == {
        arm: 'pg_stat_statements cannot be read here (relation "pg_stat_statements" does not exist)'
        for arm in ("reads", "load")}


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


def test_a_day_with_no_card_event_says_so_and_stays_ok() -> None:
    out = plat.check_curation_sync(_Conn(_answers(bumps=(0, 0, None, 0))), T)
    board = _cell(out, "board_bumps")
    assert (board["status"], board["value"]) == ("ok", 0) and out["status"] == "ok"
    assert board["message"] == "no card events in the last 24 h: nothing to check against the board clock"


def test_the_armed_predicate_is_one_clause_counted_and_negated() -> None:
    assert plat._CURATION_TRIGGERS_SQL.count(plat._CLOCK_TRIGGER_ARMED) == 2
    assert f"filter (where not ({plat._CLOCK_TRIGGER_ARMED}))" in plat._CURATION_TRIGGERS_SQL
    assert "'{}'" in plat._CURATION_TRIGGERS_SQL


def test_an_absent_clock_leaves_board_bumps_not_measured_and_the_triggers_judged() -> None:
    out = plat.check_curation_sync(_Conn(_answers(armed=0, found=0, bumps=pg_errors.UndefinedTable(
        'relation "curation_clock" does not exist'))), T)
    assert set(_cells(out)) == {"triggers"} and out["status"] == "fail"
    assert out["details"]["not_measured"]["board_bumps"].startswith("the curation clock is absent")
    assert set(out["details"]["not_measured"]) == {"board_bumps", "reads", "load"}


# --- reads and load (W4): the browsers' clock read, from pg_stat_statements ----------------------------------------


def test_the_statements_read_binds_its_pattern_and_names_the_browser_role() -> None:
    """A literal pattern would be its own match in pg_stat_statements' text; bound, it is stored as $1."""
    conn = _Conn(_answers(statements=(0, 0.0, 0.0, _RESET)))
    plat.check_curation_sync(conn, T)
    [params] = [params for sql, params, _armed in conn.sent if sql == plat._CURATION_STATEMENTS_SQL]
    assert params == {"relation": "%curation_clock_public%"}
    assert "curation_clock_public" not in plat._CURATION_STATEMENTS_SQL
    assert "to_regrole('authenticated')" in plat._CURATION_STATEMENTS_SQL


def test_a_first_measurement_watches_the_reads_from_now_and_takes_the_load_baseline() -> None:
    out = plat.check_curation_sync(_Conn(_answers(statements=(42, 90.0, 9_000.0, _RESET))), T)
    reads, load = _cell(out, "reads"), _cell(out, "load")
    assert (reads["status"], reads["value"], reads["unit"]) == ("ok", 0.0, "h")
    assert reads["message"] == "watching the clock read from now (42 calls so far)"
    assert reads["facts"] == {"calls": 42, "quiet_since": _NOW.isoformat(), "stats_reset": _RESET.isoformat()}
    assert load["status"] == "unknown" and load["message"].startswith("Not measured: a baseline taken now")
    assert load["facts"] == {"clock_ms": 90.0, "all_ms": 9_000.0, "stats_reset": _RESET.isoformat()}
    assert out["status"] == "ok" and "1 Not measured" in out["message"]


def test_reads_that_advanced_are_ok_and_restart_the_quiet_clock() -> None:
    out = plat.check_curation_sync(_Conn(_answers(
        statements=(151, 0.0, 0.0, _RESET),
        previous=_stored(calls=150, quiet_since=_NOW - _dt.timedelta(hours=30)))), T)
    reads = _cell(out, "reads")
    assert (reads["status"], reads["value"], reads["facts"]["quiet_since"]) == ("ok", 0.0, _NOW.isoformat())
    assert reads["message"] == "the browsers read the curation clock (151 calls)"


@pytest.mark.parametrize("hours, status", [(23.9, "ok"), (24, "warn"), (30, "warn")])
def test_reads_that_stood_still_for_24_h_are_a_watch_never_a_problem(hours: float, status: str) -> None:
    since = _NOW - _dt.timedelta(hours=hours)
    out = plat.check_curation_sync(_Conn(_answers(statements=(150, 0.0, 0.0, _RESET),
                                                  previous=_stored(calls=150, quiet_since=since))), T)
    reads = _cell(out, "reads")
    assert (reads["status"], reads["value"], reads["facts"]["quiet_since"]) == (status, hours, since.isoformat())
    assert out["status"] == status, "Watch at worst: the clock's reader is never paged"
    if status == "warn":
        assert reads["message"].startswith(f"no browser has read the curation clock for {hours:.1f} h (its count "
                                           "stood at 150 since ")


@pytest.mark.parametrize("calls", [0, 50, 149])
def test_fewer_calls_under_the_same_reset_an_evicted_entry_are_no_advance(calls: int) -> None:
    """One of several entries evicted (and re-created lower) leaves the sum below the last: no read is proven."""
    since = _NOW - _dt.timedelta(hours=30)
    evicted = plat.check_curation_sync(_Conn(_answers(statements=(calls, 0.0, 0.0, _RESET),
                                                      previous=_stored(calls=150, quiet_since=since))), T)
    reads = _cell(evicted, "reads")
    assert (reads["status"], reads["facts"]["quiet_since"]) == ("warn", since.isoformat())
    assert reads["facts"]["calls"] == calls, "the next hour counts from the re-created entry's lower base"


def test_a_reset_with_reads_since_is_an_advance() -> None:
    since = _NOW - _dt.timedelta(hours=30)
    later = _RESET + _dt.timedelta(days=60)
    reset = plat.check_curation_sync(_Conn(_answers(statements=(150, 0.0, 0.0, later),
                                                    previous=_stored(calls=150, quiet_since=since))), T)
    assert (_cell(reset, "reads")["status"], _cell(reset, "reads")["value"]) == ("ok", 0.0)


@pytest.mark.parametrize("took, status", [(99.9, "ok"), (100.0, "warn"), (300.0, "warn")])
def test_load_is_a_watch_at_5_percent_of_statement_time_between_two_measurements(took: float, status: str) -> None:
    out = plat.check_curation_sync(_Conn(_answers(statements=(200, 100.0 + took, 12_000.0, _RESET),
                                                  previous=_stored(clock_ms=100.0, all_ms=10_000.0))), T)
    load = _cell(out, "load")
    share = took / 2_000.0 * 100
    assert (load["status"], load["value"], load["unit"]) == (status, round(share, 2), "%")
    assert load["facts"]["share_pct"] == round(share, 3)
    assert out["status"] == status
    if status == "warn":
        assert load["message"].startswith(f"the clock read took {share:.1f} % of all statement time since the "
                                          "previous measurement (Watch at 5 %)")


@pytest.mark.parametrize("statements", [
    (200, 400.0, 12_000.0, _RESET + _dt.timedelta(days=1)),  # the statistics were reset since
    (200, 400.0, 9_000.0, _RESET),                            # total time went backwards
    (200, 50.0, 12_000.0, _RESET),                            # the clock read's entry was evicted
])
def test_load_takes_a_new_baseline_and_judges_nothing_when_the_two_measurements_do_not_compare(
        statements: tuple[Any, ...]) -> None:
    out = plat.check_curation_sync(_Conn(_answers(statements=statements, previous=_stored())), T)
    assert _cell(out, "load")["status"] == "unknown"


def test_statement_text_the_worker_may_not_read_leaves_reads_and_load_not_measured() -> None:
    """Without pg_read_all_stats, authenticated's entries read <insufficient privilege>: zero calls would be a lie."""
    out = plat.check_curation_sync(_Conn(_answers(statements=(0, 0.0, 9_000.0, _RESET), masked=3,
                                                  previous=_stored(quiet_since=_NOW - _dt.timedelta(hours=30)))), T)
    assert set(_cells(out)) == {"triggers", "board_bumps"} and out["status"] == "ok"
    assert out["details"]["not_measured"] == {arm: (
        "the worker cannot read other roles' statement text (3 entries of authenticated show <insufficient "
        "privilege>; grant it pg_read_all_stats)") for arm in ("reads", "load")}
    assert "'<insufficient privilege>'" in plat._CURATION_STATEMENTS_SQL


@pytest.mark.parametrize("error", [
    pg_errors.UndefinedTable('relation "pg_stat_statements" does not exist'),
    pg_errors.InsufficientPrivilege("permission denied for view pg_stat_statements"),
    pg_errors.ObjectNotInPrerequisiteState('pg_stat_statements must be loaded via "shared_preload_libraries"'),
    pg_errors.InvalidSchemaName('schema "extensions" does not exist'),
])
def test_unreadable_statement_statistics_leave_reads_and_load_not_measured(error: BaseException) -> None:
    out = plat.check_curation_sync(_Conn(_answers(statements=error)), T)
    assert set(_cells(out)) == {"triggers", "board_bumps"} and out["status"] == "ok"
    assert set(out["details"]["not_measured"]) == {"reads", "load"}


def test_the_registry_row_is_an_hourly_worker_alarm_after_notifications() -> None:
    (c,) = [c for c in CHECKS if c.key == "curation_sync"]
    assert (c.fn, c.domain, c.cls, c.host, c.cadence_s, c.label, c.unit, c.owner_path, c.budget_s) == (
        plat.check_curation_sync, "platform", "alarm", "realtime-worker", 3600, "Curation sync", "cells", "", 5.0)
    keys = [c.key for c in CHECKS]
    assert keys.index("curation_sync") == keys.index("notifications") + 1 == keys.index("parsing") - 1
    assert {k: THRESHOLDS[k] for k in T} == T

"""Hermetic tests for scripts.checks.platform: jobs, capacity, llm and the moved migration_drift."""

# No DB: a fake connection answers each statement by the SQL constant it carries, through the
# per-check clock. The SQL itself ran read-only against production at build time; cron's is
# invisible to CI's replayed schema (allowlisted in the PREPARE sweep), the rest PREPAREs there.

from __future__ import annotations

import ast
import contextlib
import datetime as _dt
import inspect
import re
from typing import Any

import pytest
from psycopg import errors as pg_errors

from scripts.checks import platform as plat

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 18, 12, 0, tzinfo=_UTC)

# The platform keys exactly as the wave-3 contract spells them (§ 3), plus the moved check's and
# the registry's GitHub delivery slack, which a workflow's artifact is given too.
T: dict[str, Any] = {
    "jobs_gh_long_cycle_h": 24, "jobs_gh_streak_fail": 3, "jobs_gh_gap_p90_multiple": 3,
    "jobs_gh_gap_min_starts": 10, "jobs_poller_stale_h": 18, "jobs_cron_fail_of_3": 2,
    "jobs_cron_gap_multiple": 3, "jobs_cron_gap_min_starts": 5, "jobs_lane_floor_s": 900,
    "jobs_lane_boot_grace_s": 900, "jobs_lane_inflight_warn_s": 1200,
    "jobs_lane_inflight_fail_s": 1800, "jobs_lane_daily_interval_s": 3600,
    "jobs_lane_flap_share": 0.5, "jobs_lane_flap_min_passes": 3,
    "capacity_p50_warn_multiple": 3, "capacity_p50_warn_floor_s": 30,
    "capacity_p50_fail_multiple": 4, "capacity_p50_fail_floor_s": 60,
    "capacity_p95_interval_warn": 0.5, "capacity_p95_interval_fail": 0.8,
    "capacity_reference_min_days": 7, "capacity_timeout_share_warn": 0.05,
    "capacity_timeout_share_fail": 0.20, "capacity_open_xact_warn_min": 60,
    "capacity_open_xact_fail_min": 360, "capacity_connections_warn": 0.7,
    "capacity_connections_fail": 0.9, "capacity_memory_warn": 0.8, "capacity_memory_fail": 0.9,
    "CAPACITY_REFERENCE_FROM": _dt.date(2026, 10, 17), "llm_error_share_warn": 0.2,
    "llm_error_share_min_calls": 20, "llm_starved_min_attempts": 5,
    "llm_spend_24h_warn_usd": 130, "llm_spend_24h_fail_usd": 150, "llm_lookback_h": 72,
    "migration_drift_window": 25, "GITHUB_DELIVERY_SLACK_S": 18 * 3600,
}


def _ago(**delta: float) -> _dt.datetime:
    return _NOW - _dt.timedelta(**delta)


def _iso(**delta: float) -> str:
    return _ago(**delta).isoformat()


class _Conn:
    """Answers by the SQL constant a statement carries; records each and whether it was armed."""

    def __init__(self, answers: dict[str, Any], now: _dt.datetime = _NOW) -> None:
        self.answers = {plat._CLOCK_SQL: [(now,)], **answers}
        self.sent: list[tuple[str, Any, bool]] = []
        self._in_transaction = self._armed = False
        self._rows: list[Any] = []

    @contextlib.contextmanager
    def transaction(self) -> Any:
        self._in_transaction = True
        try:
            yield
        finally:
            self._in_transaction = self._armed = False

    def cursor(self) -> "_Conn":
        return self

    def __enter__(self) -> "_Conn":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        if sql.startswith("SET LOCAL statement_timeout"):
            self._armed = self._in_transaction
            return
        self.sent.append((sql, params, self._armed))
        self._armed = False
        answer = next((v for k, v in self.answers.items() if k in sql), [])
        if isinstance(answer, BaseException):
            raise answer
        self._rows = list(answer(params) if callable(answer) else answer)

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


def _cells(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in result["details"]["cells"]}


# --- jobs ------------------------------------------------------------------------------------


def _workflow(stem: str = "property_maintenance", *, name: str | None = None,
              state: str | None = "active", last_ok: _dt.datetime | None = None,
              starts: list[_dt.datetime] | None = None, reds: int = 0,
              red_at: _dt.datetime | None = None, conclusion: str = "failure",
              reason: str | None = "CHECK walk_end_streak status=fail value=3",
              url: str | None = "https://github.com/o/r/actions/runs/7") -> tuple[Any, ...]:
    """A _GH_WORKFLOWS_SQL row: a workflow on main with its production record."""
    return (f".github/workflows/{stem}.yml", name or f"Jobs: {stem}", state,
            last_ok if last_ok is not None else _ago(hours=1), starts or [], reds,
            red_at if reds else None, conclusion if reds else None,
            reason if reds else None, url if reds else None)


def _hourly(n: int, *, every_h: float = 1.0) -> list[_dt.datetime]:
    return [_ago(hours=every_h * i) for i in range(n)]


def _cron(jobname: str = "browse-list-rebuild", *, active: bool = True,
          newest: _dt.datetime | None = None, starts: int = 672, gap: float | None = 900.0,
          last3: list[str] | None = None, failed_24h: int = 0,
          failed_at: _dt.datetime | None = None,
          failure: str | None = None) -> tuple[Any, ...]:
    """A _CRON_JOBS_SQL row: one pg_cron job's newest week."""
    return (jobname, active, newest if newest is not None else _ago(minutes=5), starts, gap,
            last3 if last3 is not None else ["succeeded"] * 3, failed_24h, failed_at, failure)


def _lane(**over: Any) -> dict[str, Any]:
    """One lane's heartbeat entry, healthy: a 180 s lane that passed a minute ago."""
    info = {"interval_s": 180.0, "passes": 40, "failed_passes": 0,
            "last_pass_at": _iso(minutes=1), "last_duration_s": 30.0, "started_at": None,
            "in_flight_s": None, "last": {"portals": 9}, "failing_since": None,
            "last_error": None}
    info.update(over)
    return info


def _artifact(name: str = "browse_list", *, last_ok: _dt.datetime | None = None,
              stale: bool = False, never: bool = False) -> tuple[Any, ...]:
    """A _ARTIFACTS_SQL row: a pg_cron artifact, so no GitHub delivery slack."""
    return (name, "rebuild_browse_list", "pg_cron", "*/15 * * * *",
            None if never else (last_ok or _ago(minutes=10)), 41_000, 41_700, 2700.0, 0.0, True,
            True if never else stale)


def _jobs(*, workflows: list[Any] | None = None, cursor: Any = "fresh", cron: Any = None,
          lanes: dict[str, Any] | None = None, booted: _dt.datetime | None = None,
          beat_at: _dt.datetime | None = None, previous: list[dict[str, Any]] | None = None,
          artifacts: list[Any] | None = None) -> tuple[dict[str, Any], _Conn]:
    """check_jobs over a healthy fleet, with the parts a test names replaced."""
    cursor_rows = [(_ago(hours=1),)] if cursor == "fresh" else ([(cursor,)] if cursor else [])
    conn = _Conn({
        plat._GH_CURSOR_SQL: cursor_rows,
        plat._GH_WORKFLOWS_SQL: workflows or [],
        plat._CRON_JOBS_SQL: cron if cron is not None else [_cron()],
        plat._HEARTBEAT_SQL: [(beat_at or _ago(seconds=20), booted or _ago(days=2),
                               lanes if lanes is not None else {"probe": _lane()})],
        plat._PREVIOUS_CELLS_SQL: [(previous,)] if previous is not None else [],
        plat._ARTIFACTS_SQL: artifacts or [],
    })
    return plat.check_jobs(conn, T), conn


def test_jobs_returns_the_wave_one_result_shape_with_one_cell_per_job() -> None:
    out, _ = _jobs(workflows=[_workflow()], artifacts=[_artifact()])
    assert out["check_key"] == "jobs" and out["status"] == "ok" and out["value"] == 0
    assert set(_cells(out)) == {"gh/poller", "gh/property_maintenance", "cron/browse-list-rebuild",
                                "lane/probe", "artifact/browse_list"}
    for cell in out["details"]["cells"]:
        assert set(cell) == {"subject", "portal", "status", "value", "unit", "message", "facts"}
    assert out["message"].endswith("5 OK.")


def test_every_jobs_statement_runs_on_the_checks_own_clock() -> None:
    _, conn = _jobs(workflows=[_workflow()], artifacts=[_artifact()])
    assert conn.sent and all(armed for _, _, armed in conn.sent)


# GitHub workflows


def test_an_mf_shaped_history_of_three_production_reds_fails_with_its_reason_line() -> None:
    """recompute_mf_yields 09-13: three reds after its last success, the third one the onset."""
    wf = _workflow("recompute_mf_yields", last_ok=_ago(hours=16), reds=3, red_at=_ago(hours=2),
                   starts=_hourly(4, every_h=4),
                   reason="RuntimeError: MF rent map refused the cell")
    cell = _cells(_jobs(workflows=[wf])[0])["gh/recompute_mf_yields"]
    assert cell["status"] == "fail" and cell["value"] == 3
    assert "3 production reds in a row" in cell["message"]
    assert "RuntimeError: MF rent map refused the cell" in cell["message"]
    assert "https://github.com/o/r/actions/runs/7" in cell["message"]


def test_a_monthly_jobs_single_red_fails_but_a_frequent_jobs_lone_red_is_watch() -> None:
    """A daily-or-rarer job's one red loses a cycle (09-01); frequent ones red ~10 % of runs."""
    monthly = _workflow("location_registry_load", reds=1, red_at=_ago(hours=3),
                        last_ok=_ago(days=31), starts=_hourly(3, every_h=24 * 30))
    hourly = _workflow("property_maintenance", reds=1, red_at=_ago(minutes=30),
                       starts=_hourly(4))
    cells = _cells(_jobs(workflows=[monthly, hourly])[0])
    assert cells["gh/location_registry_load"]["status"] == "fail"
    assert "loses a cycle" in cells["gh/location_registry_load"]["message"]
    assert cells["gh/property_maintenance"]["status"] == "warn"
    assert "newest production run failed" in cells["gh/property_maintenance"]["message"]


def test_a_workflow_quiet_past_three_p90_gaps_fails_once_it_has_ten_starts() -> None:
    """The overdue arm, judged as of the poller's cursor so the canary's gap never reads as one."""
    starts = _hourly(10, every_h=2)
    overdue = _workflow("clip_tag", last_ok=_ago(hours=9), starts=starts)
    cell = _cells(_jobs(workflows=[overdue], cursor=_ago(minutes=5))[0])["gh/clip_tag"]
    assert cell["status"] == "fail" and "over 3× its p90 start gap" in cell["message"]
    lagging = _cells(_jobs(workflows=[overdue], cursor=_ago(hours=4))[0])["gh/clip_tag"]
    assert lagging["status"] == "ok", "5 h as of the cursor is under 3 × the 2 h p90"


def test_the_overdue_arm_is_not_measured_under_ten_starts_never_a_verdict() -> None:
    wf = _workflow("clip_tag", last_ok=_ago(days=5), starts=_hourly(9, every_h=2))
    cell = _cells(_jobs(workflows=[wf])[0])["gh/clip_tag"]
    assert cell["status"] == "ok"
    assert cell["facts"]["overdue"] == "Not measured: 9 of 10 scheduled starts recorded"


def test_a_push_only_workflow_is_judged_by_its_reds_alone() -> None:
    """The poller stores scheduled starts only (a push has no cadence to be overdue against), so
    both start-gap arms are Not measured: a lone red is Watch and three are a Problem."""
    lone = _workflow("deploy_docs", reds=1, red_at=_ago(hours=2), last_ok=_ago(days=40))
    cell = _cells(_jobs(workflows=[lone])[0])["gh/deploy_docs"]
    assert cell["status"] == "warn"
    assert cell["facts"]["long_cycle"] == "Not measured: fewer than 2 scheduled starts recorded"
    assert cell["facts"]["overdue"] == "Not measured: 0 of 10 scheduled starts recorded"
    streak = _workflow("deploy_docs", reds=3, red_at=_ago(hours=2), last_ok=_ago(days=40))
    assert _cells(_jobs(workflows=[streak])[0])["gh/deploy_docs"]["status"] == "fail"


def test_a_disabled_workflow_reads_off_not_red() -> None:
    wf = _workflow("autodedup", state="disabled_manually", reds=4, red_at=_ago(hours=1))
    cell = _cells(_jobs(workflows=[wf])[0])["gh/autodedup"]
    assert cell["status"] == "ok" and cell["message"] == "off: the workflow is disabled_manually"
    assert cell["facts"]["state"] == "disabled_manually"


def test_a_reason_past_the_pollers_budget_reads_as_not_read() -> None:
    wf = _workflow("idnes_index_walk", reds=1, red_at=_ago(hours=1), reason=None, url=None)
    cell = _cells(_jobs(workflows=[wf])[0])["gh/idnes_index_walk"]
    assert cell["status"] == "warn" and cell["message"].endswith(": reason not read")


def test_the_canary_has_no_cell() -> None:
    from scripts.record_workflow_failures import MONITOR_WORKFLOW_NAME

    canary = _workflow("verify_pipeline", name=MONITOR_WORKFLOW_NAME, reds=5, red_at=_ago(hours=1))
    assert "gh/verify_pipeline" not in _cells(_jobs(workflows=[canary])[0])


def test_only_production_runs_on_main_are_a_jobs_record() -> None:
    """Dispatch, PR, branch and pre-wave-3 rows never count; a file gone from main has no cell."""
    sql = " ".join(plat._GH_WORKFLOWS_SQL.split())
    assert "(f.event = 'schedule' or (f.event = 'push' and f.head_branch = 'main'))" in sql
    assert "where h.on_main" in sql


def test_a_stale_poller_cursor_fails_its_cell_and_turns_every_github_cell_unknown() -> None:
    wf = _workflow("recompute_mf_yields", reds=3, red_at=_ago(days=1))
    out, _ = _jobs(workflows=[wf], cursor=_ago(hours=19))
    cells = _cells(out)
    assert cells["gh/poller"]["status"] == "fail" and cells["gh/poller"]["value"] == 19.0
    assert cells["gh/recompute_mf_yields"]["status"] == "unknown"
    assert cells["gh/recompute_mf_yields"]["message"].startswith("Not measured")


def test_the_poller_cell_is_ok_inside_the_canarys_measured_gap() -> None:
    """The canary's longest measured gap was 13.3 h; 18 h leaves margin before it is a Problem."""
    out, _ = _jobs(cursor=_ago(hours=13.3))
    assert _cells(out)["gh/poller"]["status"] == "ok"


def test_no_cursor_on_record_is_a_problem_for_the_poller() -> None:
    cell = _cells(_jobs(cursor=None)[0])["gh/poller"]
    assert cell["status"] == "fail" and "never completed a poll" in cell["message"]


# pg_cron


def test_two_of_a_jobs_last_three_runs_failed_is_a_problem() -> None:
    row = _cron("refresh-health-dashboard", last3=["failed", "succeeded", "failed"], failed_24h=2,
                failed_at=_ago(minutes=10),
                failure="ERROR:  canceling statement due to statement timeout\nCONTEXT: x")
    cell = _cells(_jobs(cron=[row])[0])["cron/refresh-health-dashboard"]
    assert cell["status"] == "fail"
    assert cell["message"] == ("2 of its last 3 runs failed: ERROR: canceling statement due to "
                               "statement timeout CONTEXT: x")


def test_any_failure_in_24_hours_is_watch() -> None:
    row = _cron("capture-data-quality", last3=["succeeded", "failed", "succeeded"], failed_24h=1,
                failed_at=_ago(hours=7), failure="job startup timeout", starts=28, gap=21600.0)
    cell = _cells(_jobs(cron=[row])[0])["cron/capture-data-quality"]
    assert cell["status"] == "warn" and "1 failed run in 24 h" in cell["message"]


def test_a_job_that_stopped_starting_is_late_against_its_own_median_gap() -> None:
    """pg_cron never back-fills a missed slot; browse-map-rebuild once stopped firing entirely."""
    late = _cron("browse-map-rebuild", newest=_ago(minutes=91), gap=1800.0, starts=336)
    cell = _cells(_jobs(cron=[late])[0])["cron/browse-map-rebuild"]
    assert cell["status"] == "fail" and "no start for 1.5 h, over 3×" in cell["message"]
    on_time = _cron("browse-map-rebuild", newest=_ago(minutes=89), gap=1800.0, starts=336)
    assert _cells(_jobs(cron=[on_time])[0])["cron/browse-map-rebuild"]["status"] == "ok"


def test_lateness_needs_five_starts_else_that_arm_is_not_measured() -> None:
    young = _cron("refresh-ingest-bulk-days", newest=_ago(days=3), starts=4, gap=60.0)
    cell = _cells(_jobs(cron=[young])[0])["cron/refresh-ingest-bulk-days"]
    assert cell["status"] == "ok"
    assert cell["facts"]["late"] == "Not measured: 4 of 5 starts in its newest 7 days"


def test_an_inactive_job_reads_ok_with_the_fact() -> None:
    cell = _cells(_jobs(cron=[_cron(active=False, last3=["failed"] * 3)])[0])
    assert cell["cron/browse-list-rebuild"]["status"] == "ok"
    assert cell["cron/browse-list-rebuild"]["facts"]["active"] is False


def test_the_watchers_own_job_is_a_cell_like_any_other() -> None:
    row = _cron("health-liveness", gap=300.0, starts=84)
    assert _cells(_jobs(cron=[row])[0])["cron/health-liveness"]["status"] == "ok"


@pytest.mark.parametrize("answer, said", [
    (pg_errors.InsufficientPrivilege("permission denied for schema cron"), "unreadable"),
    ([], "returned no rows"),
])
def test_unreadable_or_empty_cron_tables_are_not_measured_never_ok(answer: Any, said: str) -> None:
    """pg_cron's row security returns zero rows to a wrong role: silence there is not health."""
    out, _ = _jobs(cron=answer)
    assert not any(s.startswith("cron/") for s in _cells(out))
    assert said in out["details"]["not_measured"]["cron"]
    assert "Not read: cron" in out["message"]


def test_every_failure_the_cron_arm_reads_is_a_finished_runs() -> None:
    """pg_cron marks a run a server restart cut off failed and leaves its end_time NULL (10 such
    rows in the 120 days to 10-19, all 'server restarted', the newest 08-17); the failure quoted is
    a finished one, like the last three and the 24 h count. The live rail runs the SQL."""
    sql = " ".join(plat._CRON_JOBS_SQL.split())
    assert ("max(r.start_time) filter (where r.status = 'failed' and r.end_time is not null) "
            "as newest_failure_at") in sql
    assert ("filter (where r.status = 'failed' and r.end_time is not null))[1] "
            "as newest_failure") in sql
    assert "filter (where r.end_time is not null and r.nth <= 3) as last_3" in sql
    assert "count(*) filter (where r.end_time is not null and r.status = 'failed'" in sql


def test_a_cron_timeout_is_the_harness_s_to_book_never_swallowed() -> None:
    with pytest.raises(pg_errors.QueryCanceled):
        _jobs(cron=pg_errors.QueryCanceled("canceling statement due to statement timeout"))


# Worker lanes


def test_off_lanes_read_ok_by_interval_or_by_slice() -> None:
    lanes = {"estimation": _lane(interval_s=0.0, last_pass_at=None),
             "drain": _lane(last={"skipped": "slice<=0"}, last_pass_at=_iso(days=3)),
             "probe": _lane(last={"skipped": 3, "portals": 6})}
    cells = _cells(_jobs(lanes=lanes)[0])
    assert cells["lane/estimation"]["message"] == "off: its interval is 0"
    assert cells["lane/drain"]["message"] == "off: its slice is 0"
    assert cells["lane/probe"]["message"].startswith("last pass"), "a count of skipped portals"
    assert all(c["status"] == "ok" for c in cells.values())


def test_a_lane_that_never_woke_fails_after_the_boot_grace() -> None:
    lanes = {"sold_comps": _lane(interval_s=None, last_pass_at=None, passes=0)}
    cell = _cells(_jobs(lanes=lanes, booted=_ago(minutes=16))[0])["lane/sold_comps"]
    assert cell["status"] == "fail" and cell["message"].startswith("never woke")
    booting = _cells(_jobs(lanes=lanes, booted=_ago(minutes=14))[0])
    assert "lane/sold_comps" not in booting


def test_a_lane_entry_from_before_wave_3_is_not_measured_while_a_seeded_none_never_woke() -> None:
    """A first deploy reads an older worker's beat, whose lanes carry no interval_s key at all:
    that is no verdict, where a wave-3 lane seeded None and still unwoken is a Problem."""
    old = _lane(last_failure_at=_iso(hours=1))
    del old["interval_s"], old["failing_since"], old["last_error"]
    seeded = _lane(interval_s=None, last_pass_at=None, passes=0)
    cells = _cells(_jobs(lanes={"probe": old, "sold_comps": seeded})[0])
    assert cells["lane/probe"]["status"] == "unknown"
    assert cells["lane/probe"]["message"] == ("Not measured: its heartbeat entry has no "
                                              "interval_s, so the beat is from a worker older "
                                              "than wave 3")
    assert cells["lane/sold_comps"]["status"] == "fail"
    assert cells["lane/sold_comps"]["message"].startswith("never woke")


def test_a_late_lane_fails_past_its_bound_and_only_once_the_worker_has_been_up_that_long() -> None:
    """max(3 × interval, interval + 2 × last duration, 15 min): 900 s for a 180 s probe."""
    late = {"probe": _lane(last_pass_at=_iso(minutes=16))}
    cell = _cells(_jobs(lanes=late)[0])["lane/probe"]
    assert cell["status"] == "fail" and cell["message"].startswith("late: no completed pass")
    in_time = {"probe": _lane(last_pass_at=_iso(minutes=14))}
    assert _cells(_jobs(lanes=in_time)[0])["lane/probe"]["status"] == "ok"
    # The previous process's pass, 35 min ago; this one is up 20 min, under the 30 min bound.
    seeded = {"count_probe": _lane(interval_s=600.0, last_duration_s=10.0,
                                   last_pass_at=_iso(minutes=35))}
    young = _cells(_jobs(lanes=seeded, booted=_ago(minutes=20))[0])["lane/count_probe"]
    assert young["status"] == "ok"


def test_a_lane_with_no_pass_is_judged_from_the_workers_start() -> None:
    lanes = {"broker_maintenance": _lane(interval_s=120.0, last_pass_at=None, passes=0)}
    cell = _cells(_jobs(lanes=lanes, booted=_ago(minutes=20))[0])["lane/broker_maintenance"]
    assert cell["status"] == "fail" and "no completed pass for 20 min" in cell["message"]


def test_a_long_slow_pass_widens_its_own_late_bound() -> None:
    """interval + 2 × last duration: a 30 s lane whose pass took 10 min is due 21 min later."""
    lanes = {"drain": _lane(interval_s=30.0, last_duration_s=600.0, last_pass_at=_iso(minutes=20))}
    assert _cells(_jobs(lanes=lanes)[0])["lane/drain"]["status"] == "ok"


def test_failing_since_is_watch_then_a_problem_past_its_bound() -> None:
    fresh = {"text_extract": _lane(interval_s=300.0, failing_since=_iso(minutes=5),
                                   last_error="RuntimeError: no credits remaining")}
    cell = _cells(_jobs(lanes=fresh)[0])["lane/text_extract"]
    assert cell["status"] == "warn"
    assert cell["message"] == "its newest pass failed: RuntimeError: no credits remaining"
    old = {"text_extract": _lane(interval_s=300.0, failing_since=_iso(minutes=16),
                                 last_error="RuntimeError: no credits remaining")}
    cell = _cells(_jobs(lanes=old)[0])["lane/text_extract"]
    assert cell["status"] == "fail" and cell["message"].startswith("failing for 16 min")


def test_a_daily_lanes_single_failure_is_a_problem_at_once() -> None:
    """A daily lane passes about once per boot, so it would never fail twice inside a bound."""
    lanes = {"location_refetch": _lane(interval_s=86400.0, failing_since=_iso(minutes=2),
                                       last_error="pass exceeded 1800 s",
                                       last_pass_at=_iso(hours=20))}
    cell = _cells(_jobs(lanes=lanes)[0])["lane/location_refetch"]
    assert cell["status"] == "fail" and "pass exceeded 1800 s" in cell["message"]


def test_a_pass_in_flight_is_watch_past_20_minutes_and_a_problem_past_30() -> None:
    watch = _cells(_jobs(lanes={"autodedup": _lane(in_flight_s=1300.0)})[0])["lane/autodedup"]
    problem = _cells(_jobs(lanes={"autodedup": _lane(in_flight_s=1900.0)})[0])["lane/autodedup"]
    assert (watch["status"], problem["status"]) == ("warn", "fail")


def test_one_cell_per_lane_carries_the_worst_with_every_reason() -> None:
    lanes = {"probe": _lane(in_flight_s=1300.0, failing_since=_iso(minutes=30),
                            last_error="ConnectionError: boom", last_pass_at=_iso(minutes=40))}
    cell = _cells(_jobs(lanes=lanes)[0])["lane/probe"]
    assert cell["status"] == "fail"
    assert [c.split(":")[0] for c in cell["facts"]["conditions"]] == ["fail", "fail", "warn"]
    assert "one pass in flight" not in cell["message"]


def test_flapping_is_half_of_three_or_more_passes_failed_since_the_previous_result() -> None:
    """autodedup showed failed_passes 6 on 10-02 while worker_lane_stall read ok."""
    before = [{"subject": "lane/autodedup", "facts": {"passes": 10, "failed_passes": 2}}]
    flapping = {"autodedup": _lane(passes=12, failed_passes=4)}
    cell = _cells(_jobs(lanes=flapping, previous=before)[0])["lane/autodedup"]
    assert cell["status"] == "warn"
    assert cell["message"] == "flapping: 2 of 4 passes failed since the previous measurement"
    steady = {"autodedup": _lane(passes=13, failed_passes=4)}
    assert _cells(_jobs(lanes=steady, previous=before)[0])["lane/autodedup"]["status"] == "ok"
    reset = {"autodedup": _lane(passes=1, failed_passes=1)}  # a boot the seed missed
    assert _cells(_jobs(lanes=reset, previous=before)[0])["lane/autodedup"]["status"] == "ok"


def test_a_booting_worker_judges_no_lane_that_has_not_passed_in_this_process() -> None:
    lanes = {"probe": _lane(last_pass_at=_iso(minutes=12), failing_since=_iso(hours=3))}
    out, _ = _jobs(lanes=lanes, booted=_ago(minutes=5))
    assert "lane/probe" not in _cells(out)
    passed = {"probe": _lane(last_pass_at=_iso(minutes=1))}
    assert "lane/probe" in _cells(_jobs(lanes=passed, booted=_ago(minutes=5))[0])


def test_counters_survive_a_restart_through_the_seeded_heartbeat() -> None:
    """Seeded from the previous beat, a flap is measured across the deploy, not reset by it."""
    before = [{"subject": "lane/drain", "facts": {"passes": 500, "failed_passes": 1}}]
    lanes = {"drain": _lane(interval_s=30.0, passes=501, failed_passes=3,
                            last_pass_at=_iso(minutes=1))}
    cell = _cells(_jobs(lanes=lanes, booted=_ago(minutes=20), previous=before)[0])["lane/drain"]
    assert cell["status"] == "warn" and cell["facts"]["passes"] == 501


def test_a_portal_failing_inside_a_lane_is_its_own_cell() -> None:
    lanes = {"probe": _lane(portal_errors={
        "idnes": {"failing_since": _iso(minutes=20), "last_error": "every category failed"},
        "remax": {"failing_since": _iso(minutes=4), "last_error": "HTTPError: 503"}})}
    cells = _cells(_jobs(lanes=lanes)[0])
    assert cells["lane/probe/idnes"]["status"] == "fail"
    assert cells["lane/probe/idnes"]["portal"] == "idnes"
    assert cells["lane/probe/remax"]["status"] == "warn"
    assert cells["lane/probe"]["status"] == "ok", "a portal's failure is not the lane's"


def test_a_recovered_portal_reads_ok_so_its_incident_can_close_on_a_measured_ok() -> None:
    before = [{"subject": "lane/drain/bazos", "portal": "bazos", "status": "fail", "facts": {}}]
    out, _ = _jobs(lanes={"drain": _lane(interval_s=30.0)}, previous=before)
    assert _cells(out)["lane/drain/bazos"]["status"] == "ok"


def test_the_heartbeat_lane_is_the_watchers_and_the_health_lane_is_judged() -> None:
    lanes = {"heartbeat": _lane(interval_s=30.0, last_pass_at=_iso(hours=2)),
             "health": _lane(interval_s=60.0, last_pass_at=_iso(minutes=20))}
    cells = _cells(_jobs(lanes=lanes)[0])
    assert "lane/heartbeat" not in cells
    assert cells["lane/health"]["status"] == "fail"


def test_a_stale_heartbeat_leaves_the_lanes_to_the_watcher() -> None:
    out, _ = _jobs(lanes={"probe": _lane(last_pass_at=_iso(hours=3))}, beat_at=_ago(minutes=11))
    assert "lane/probe" not in _cells(out)
    assert "watcher's worker cell" in out["details"]["not_measured"]["lanes"]


# Derived artifacts


def test_an_artifact_past_its_budget_is_a_problem_with_its_facts() -> None:
    out, _ = _jobs(artifacts=[_artifact(last_ok=_ago(minutes=50), stale=True)])
    cell = _cells(out)["artifact/browse_list"]
    assert cell["status"] == "fail" and cell["value"] == 50
    assert cell["message"] == ("stale: last succeeded 50 min ago, past its 45 min budget "
                               "(rebuild_browse_list, on pg_cron)")
    assert {"producer", "host", "cadence", "last_rows", "last_duration_ms", "budget_s",
            "slack_s", "is_serving"} <= set(cell["facts"])
    assert cell["facts"]["slack_s"] == 0.0, "only a github-actions artifact gets the slack"


def test_a_never_succeeded_artifact_is_not_measured() -> None:
    """health_stale(NULL) reads true; never succeeded is no verdict, so it is tested first."""
    cell = _cells(_jobs(artifacts=[_artifact(never=True)])[0])["artifact/browse_list"]
    assert cell["status"] == "unknown" and "never succeeded" in cell["message"]


def test_a_workflows_artifact_is_stale_only_past_its_budget_plus_githubs_delivery_slack() -> None:
    """GitHub delivers a cron hours late (the canary's scheduled runs came a median 7.2 h apart),
    so a github-actions artifact is given the registry's 18 h; pg_cron's keeps its bare budget.
    The fake applies health_stale to the slack the check sends; the live rail runs the SQL."""
    def rows(params: Any) -> list[tuple[Any, ...]]:
        out = []
        for name, host, budget_h, age_h in (("price_stat_choropleth", "github-actions", 30, 40),
                                            ("image_storage_overview_mv", "github-actions", 6, 25),
                                            ("browse_list", "pg_cron", 0.75, 50 / 60)):
            slack_s = float(params["slack_s"]) if host == "github-actions" else 0.0
            last_ok = _ago(hours=age_h)
            out.append((name, "producer", host, "cadence", last_ok, 1, 1, budget_h * 3600.0,
                        slack_s, True,
                        last_ok < _NOW - _dt.timedelta(seconds=budget_h * 3600 + slack_s)))
        return out

    out, conn = _jobs(artifacts=rows)
    cells = _cells(out)
    held = cells["artifact/price_stat_choropleth"]
    assert held["status"] == "ok", "40 h old on a 30 h budget is inside its 18 h slack"
    assert held["message"] == ("last succeeded 40.0 h ago (budget 30.0 h + 18.0 h GitHub "
                               "delivery slack)")
    assert (held["facts"]["budget_s"], held["facts"]["slack_s"]) == (108_000.0, 64_800.0)
    stale = cells["artifact/image_storage_overview_mv"]
    assert stale["status"] == "fail" and stale["message"] == (
        "stale: last succeeded 25.0 h ago, past its 6.0 h budget + 18.0 h GitHub delivery "
        "slack (producer, on github-actions)")
    assert cells["artifact/browse_list"]["status"] == "fail", "pg_cron keeps the bare budget"
    sent = [params for sql, params, _ in conn.sent if sql == plat._ARTIFACTS_SQL]
    assert sent == [{"slack_s": 64_800.0}]


def test_a_check_that_measured_no_cell_is_unknown_and_says_why() -> None:
    unread = plat._result("jobs", [plat._cell("artifact/x", "unknown", "Not measured: never")],
                          {"cron": plat._CRON_EMPTY, "lanes": "no realtime-worker heartbeat"})
    assert unread["status"] == "unknown" and unread["value"] is None
    assert unread["details"]["skipped"] == ("cron: " + plat._CRON_EMPTY
                                            + "; lanes: no realtime-worker heartbeat")
    assert unread["message"].startswith("Not measured: ") and "UNKNOWN" not in unread["message"]


def test_unmeasured_arms_are_named_while_the_measured_cells_still_judge() -> None:
    out, _ = _jobs(cron=[], workflows=[_workflow(reds=3, red_at=_ago(hours=1))])
    assert out["status"] == "fail" and out["value"] == 1
    assert out["details"]["not_measured"] == {"cron": plat._CRON_EMPTY}
    assert out["message"].startswith("1 Problem — gh/property_maintenance: 3 production reds")


# --- capacity ------------------------------------------------------------------------------


def _capacity_row(jobname: str = "browse-list-rebuild", *, p50: float = 80.0, p95: float = 180.0,
                  finished: int = 96, gap: float | None = 900.0, active: bool = True,
                  timeout: str | None = "1800s") -> tuple[Any, ...]:
    command = (f"set statement_timeout='{timeout}'; select public.rebuild_browse_list();"
               if timeout else "select public.rebuild_browse_list();")
    return (jobname, active, command, p50, p95, finished, gap)


def _memory(now: _dt.datetime, peak: str | None = "7400.0", *, kind: str | None = "object",
            booted_ago: _dt.timedelta = _dt.timedelta(days=2),
            passed_ago: _dt.timedelta | None = _dt.timedelta(minutes=10)) -> list[Any]:
    """A _WORKER_MEMORY_SQL row: a fresh beat, the boot, autodedup's newest pass and its reading."""
    passed = None if passed_ago is None else (now - passed_ago).isoformat()
    return [(now - _dt.timedelta(seconds=20), now - booted_ago, passed, kind, peak,
             None if peak is None else "22888.2")]


def _capacity(*, cron: Any = None, reference: list[Any] | None = None,
              now: _dt.datetime = _dt.datetime(2026, 10, 27, 12, 0, tzinfo=_UTC),
              timeouts: list[Any] | None = None, clients: int = 29, hidden: int = 0,
              oldest: list[Any] | None = None, memory: Any = "healthy") -> dict[str, Any]:
    """check_capacity over a healthy database 10 days after the reference date."""
    conn = _Conn({
        plat._CAPACITY_CRON_SQL: cron if cron is not None else [_capacity_row()],
        plat._CAPACITY_REFERENCE_SQL: reference if reference is not None
        else [("browse-list-rebuild", 0, 75.0)],
        plat._CHECK_TIMEOUTS_SQL: timeouts if timeouts is not None
        else [("github-actions", 400, 0, None), ("realtime-worker", 336, 0, None)],
        plat._SERVER_SQL: [(_dt.datetime(2026, 10, 16, 16, 22, tzinfo=_UTC), 380, clients,
                            hidden)],
        plat._OLDEST_TRANSACTIONS_SQL: oldest or [],
        plat._WORKER_MEMORY_SQL: _memory(now) if memory == "healthy" else memory,
    }, now=now)
    return plat.check_capacity(conn, T)


def test_capacity_is_ok_on_the_2xl_baseline_with_every_cell_named() -> None:
    out = _capacity()
    assert out["check_key"] == "capacity" and out["status"] == "ok"
    assert set(_cells(out)) == {"cron/browse-list-rebuild", "check_timeouts/github-actions",
                                "check_timeouts/realtime-worker", "open_transaction",
                                "connections", "worker_memory", "db_restart"}


def test_the_relative_arm_reads_the_lowest_weekly_p50_since_the_reference() -> None:
    """browse-list-rebuild ran p50 151-277 s from 08-17 against a lowest weekly p50 of ~9.9 s."""
    weeks = [("browse-list-rebuild", 0, 41.0), ("browse-list-rebuild", 1, 9.9)]
    problem = _cells(_capacity(cron=[_capacity_row(p50=182.0)], reference=weeks))
    assert problem["cron/browse-list-rebuild"]["status"] == "fail"
    assert "18.4× its reference 9.9 s" in problem["cron/browse-list-rebuild"]["message"]
    watch = _cells(_capacity(cron=[_capacity_row(p50=31.0)], reference=weeks))
    assert watch["cron/browse-list-rebuild"]["status"] == "warn"


@pytest.mark.parametrize("p50, status", [(29.0, "ok"), (59.0, "warn"), (61.0, "fail")])
def test_the_relative_arm_has_floors_so_a_fast_job_never_alarms_on_seconds(p50: float,
                                                                         status: str) -> None:
    weeks = [("llm-cost-rollup-refresh", 0, 0.1)]
    row = _capacity_row("llm-cost-rollup-refresh", p50=p50, p95=p50, gap=900.0, timeout="600s")
    cell = _cells(_capacity(cron=[row], reference=weeks))["cron/llm-cost-rollup-refresh"]
    assert cell["status"] == status


def test_the_relative_arm_is_not_measured_before_seven_days_of_post_reference_history() -> None:
    out = _capacity(cron=[_capacity_row(p50=500.0, p95=100.0)], reference=[],
                    now=_dt.datetime(2026, 10, 19, 6, 0, tzinfo=_UTC))
    cell = _cells(out)["cron/browse-list-rebuild"]
    assert cell["status"] == "ok"
    assert cell["facts"]["relative"] == "Not measured: 2.2 of 7 days of history since 2026-10-17"


def test_p95_against_the_interval_watches_at_half_and_is_a_problem_at_80_percent() -> None:
    def cell(p95: float) -> dict[str, Any]:
        return _cells(_capacity(cron=[_capacity_row(p95=p95)]))["cron/browse-list-rebuild"]

    assert cell(449.0)["status"] == "ok"
    assert cell(450.0)["status"] == "warn" and "50% of its 900 s interval" in cell(450.0)["message"]
    assert cell(720.0)["status"] == "fail"


def test_p95_against_the_statement_timeout_is_a_problem_at_80_percent() -> None:
    row = _capacity_row("refresh-health-dashboard", p50=104.0, p95=245.0, gap=None, timeout="300s")
    cell = _cells(_capacity(cron=[row], reference=[]))["cron/refresh-health-dashboard"]
    assert cell["status"] == "fail" and "82% of its 300 s statement_timeout" in cell["message"]
    unset = _capacity_row("refresh-health-dashboard", p50=104.0, p95=245.0, gap=None, timeout=None)
    cell = _cells(_capacity(cron=[unset], reference=[]))["cron/refresh-health-dashboard"]
    assert cell["status"] == "ok"


@pytest.mark.parametrize("command, seconds", [
    ("set statement_timeout='300s'; select public.refresh_health_matviews();", 300.0),
    ("SET statement_timeout = '15min'; select 1;", 900.0),
    ("set statement_timeout to 60000; select 1;", 60.0),
    ("select public.rebuild_browse_list();", None),
])
def test_the_statement_timeout_is_read_from_the_commands_prefix(command: str,
                                                                seconds: float | None) -> None:
    assert plat._statement_timeout_s(command) == seconds


def test_a_job_with_no_finished_run_in_24_hours_is_not_measured_here() -> None:
    cell = _cells(_capacity(cron=[_capacity_row(p50=None, p95=None, finished=0)]))
    assert cell["cron/browse-list-rebuild"]["status"] == "unknown"


@pytest.mark.parametrize("timed_out, status", [(20, "ok"), (21, "warn"), (81, "fail")])
def test_check_timeouts_watch_above_5_and_fail_above_20_percent(timed_out: int,
                                                                 status: str) -> None:
    rows = [("github-actions", 400, timed_out, ["walk_coverage"]),
            ("realtime-worker", 336, 0, None)]
    cell = _cells(_capacity(timeouts=rows))["check_timeouts/github-actions"]
    assert cell["status"] == status


def test_check_timeouts_are_judged_per_harness_host_never_diluted_or_counting_the_watcher() -> None:
    """Pooled, the worker's 30 of 100 would read 30 of 500 (6 %, Watch); per host it is 30 %. The
    pg_cron watcher's keys are not the harness's and never time out through it."""
    rows = [("github-actions", 400, 0, None),
            ("realtime-worker", 100, 30, ["capacity", "jobs"])]
    cells = _cells(_capacity(timeouts=rows))
    worker = cells["check_timeouts/realtime-worker"]
    assert worker["status"] == "fail" and worker["value"] == 30.0
    assert worker["message"] == ("30 of 100 check results on realtime-worker in 24 h ran out of "
                                 "their budget (30.0%): capacity, jobs")
    assert worker["facts"]["timed_out_keys"] == ["capacity", "jobs"]
    assert cells["check_timeouts/github-actions"]["status"] == "ok"
    assert not any(s == "check_timeouts" or s.endswith("/pg_cron") for s in cells)
    sql = " ".join(plat._CHECK_TIMEOUTS_SQL.split())
    assert "where hc.host = any(%(hosts)s::text[]) group by hc.host" in sql
    assert plat._HARNESS_HOSTS == ("github-actions", "realtime-worker")


def test_a_harness_host_with_no_result_in_24_hours_is_not_measured() -> None:
    cells = _cells(_capacity(timeouts=[("github-actions", 400, 0, None)]))
    cell = cells["check_timeouts/realtime-worker"]
    assert cell["status"] == "unknown"
    assert cell["message"] == "Not measured: no check result from realtime-worker in 24 h"


def test_check_timeouts_reads_the_hosts_the_harness_projects() -> None:
    """The check never imports the harness, so its copy of the host list is pinned to it here."""
    from scripts.verify_pipeline import _HARNESS_HOSTS

    assert sorted(plat._HARNESS_HOSTS) == sorted(_HARNESS_HOSTS)


@pytest.mark.parametrize("minutes, status", [(60, "ok"), (61, "warn"), (361, "fail")])
def test_the_oldest_open_transaction_watches_past_an_hour_and_fails_past_six(minutes: float,
                                                                           status: str) -> None:
    oldest = [("worker", "idle in transaction", minutes * 60.0)]
    cell = _cells(_capacity(oldest=oldest))["open_transaction"]
    assert cell["status"] == status and "worker [idle in transaction]" in cell["message"]


def test_the_open_transaction_read_excludes_autovacuum_and_itself() -> None:
    sql = " ".join(plat._OLDEST_TRANSACTIONS_SQL.split())
    assert "pid <> pg_backend_pid()" in sql and "not starts_with(backend_type, 'autovacuum')" in sql


@pytest.mark.parametrize("clients, status", [(266, "ok"), (267, "warn"), (343, "fail")])
def test_connections_watch_above_70_and_fail_above_90_percent(clients: int, status: str) -> None:
    assert _cells(_capacity(clients=clients))["connections"]["status"] == status


def test_sessions_hidden_from_this_role_leave_both_activity_cells_not_measured() -> None:
    """pg_stat_activity shows a session this role may not see with backend_type, state and
    xact_start NULL, so a count or an oldest transaction read past it would be a partial one."""
    oldest = [("worker", "idle in transaction", 400 * 60.0)]
    cells = _cells(_capacity(hidden=3, clients=350, oldest=oldest))
    for subject in ("open_transaction", "connections"):
        assert cells[subject]["status"] == "unknown", subject
        assert cells[subject]["message"] == ("Not measured: pg_stat_activity hides other sessions "
                                             "from this role (3 read without a backend_type)")
        assert cells[subject]["facts"] == {"hidden_sessions": 3}
    assert cells["db_restart"]["status"] == "ok"
    sql = " ".join(plat._SERVER_SQL.split())
    assert "(select count(*) from pg_stat_activity where backend_type is null) as hidden" in sql


@pytest.mark.parametrize("peak, status", [
    ("18310.0", "ok"), ("18320.0", "warn"), ("20610.0", "fail")])
def test_worker_memory_watches_above_80_and_fails_above_90_percent(peak: str, status: str) -> None:
    now = _dt.datetime(2026, 10, 27, 12, 0, tzinfo=_UTC)
    assert _cells(_capacity(memory=_memory(now, peak)))["worker_memory"]["status"] == status


def test_worker_memory_is_not_measured_while_autodedup_has_no_reading() -> None:
    now = _dt.datetime(2026, 10, 27, 12, 0, tzinfo=_UTC)
    cell = _cells(_capacity(memory=_memory(now, None, kind=None)))
    assert cell["worker_memory"]["status"] == "unknown"
    assert "autodedup lane" in cell["worker_memory"]["message"]


def test_worker_memory_judges_only_a_reading_taken_since_the_worker_booted() -> None:
    """The boot seed carries the previous process's autodedup `last`, so its peak RSS is that
    process's: no pass since boot, or none at all, is Not measured, never a verdict."""
    now = _dt.datetime(2026, 10, 27, 12, 0, tzinfo=_UTC)
    seeded = _memory(now, "21000.0", booted_ago=_dt.timedelta(minutes=30),
                     passed_ago=_dt.timedelta(hours=3))
    cell = _cells(_capacity(memory=seeded))["worker_memory"]
    assert cell["status"] == "unknown"
    assert cell["message"] == "Not measured: no autodedup pass since the worker booted"
    assert cell["facts"] == {"last_pass_at": (now - _dt.timedelta(hours=3)).isoformat(),
                             "boot": (now - _dt.timedelta(minutes=30)).isoformat()}
    never = _memory(now, "21000.0", passed_ago=None)
    assert _cells(_capacity(memory=never))["worker_memory"]["status"] == "unknown"
    fresh = _memory(now, "21000.0", booted_ago=_dt.timedelta(minutes=30),
                    passed_ago=_dt.timedelta(minutes=5))
    assert _cells(_capacity(memory=fresh))["worker_memory"]["status"] == "fail"


def test_the_database_restart_is_a_fact_never_a_verdict() -> None:
    cell = _cells(_capacity())["db_restart"]
    assert cell["status"] == "ok" and cell["message"].startswith(
        "the database last started 2026-10-16 16:22 UTC")


def test_unreadable_cron_tables_leave_the_rest_of_capacity_measured() -> None:
    out = _capacity(cron=pg_errors.UndefinedTable('relation "cron.job" does not exist'))
    assert out["status"] == "ok" and "cron" in out["details"]["not_measured"]
    assert not any(s.startswith("cron/") for s in _cells(out))


def test_capacity_sends_the_reference_date_as_a_utc_midnight() -> None:
    conn = _Conn({plat._CAPACITY_CRON_SQL: [_capacity_row()],
                  plat._CHECK_TIMEOUTS_SQL: [("github-actions", 1, 0, None)],
                  plat._SERVER_SQL: [(_ago(days=2), 380, 20, 0)],
                  plat._WORKER_MEMORY_SQL: []})
    plat.check_capacity(conn, T)
    sent = {sql: params for sql, params, _ in conn.sent}
    assert sent[plat._CAPACITY_REFERENCE_SQL] == {"ref": _dt.datetime(2026, 10, 17, tzinfo=_UTC)}


# --- llm ----------------------------------------------------------------------------------


def _calls(provider: str = "openai", called_for: str = "enrich_listing_description", *,
           last_ok: _dt.datetime | None = None, last_err: _dt.datetime | None = None,
           last_credit: _dt.datetime | None = None, credit_errors: int = 0,
           calls: int = 3000, errors: int = 0, attempts: int = 120, successes: int = 120,
           spend: float = 25.0) -> tuple[Any, ...]:
    """A _LLM_CALLS_SQL row: one job's calls to one provider over the lookback."""
    return (provider, called_for, last_ok if last_ok is not None else _ago(minutes=2), last_err,
            last_credit, credit_errors, ["gpt-5.6-luna"] if credit_errors else None,
            calls, errors, attempts, successes, spend)


_OPENAI_429 = ('openai call failed: HTTP 429 {"error": {"message": "You have no credits '
               'remaining. Top up your account."}}')


def _llm(calls: list[Any], *, credit: list[Any] | None = None,
         providers: tuple[str, ...] = ("anthropic", "openai")) -> tuple[dict[str, Any], _Conn]:
    conn = _Conn({plat._LLM_PROVIDERS_SQL: [(p,) for p in providers],
                  plat._LLM_CALLS_SQL: calls,
                  plat._LLM_CREDIT_ERROR_SQL: credit or []})
    return plat.check_llm(conn, T), conn


def test_a_credit_outage_is_a_problem_carrying_the_providers_own_error() -> None:
    row = _calls(last_ok=_ago(hours=5), last_err=_ago(minutes=3), last_credit=_ago(minutes=3),
                 credit_errors=480, attempts=12, successes=0)
    credit = [("openai", _ago(minutes=3), "enrich_listing_description", "gpt-5.6-luna",
               _OPENAI_429)]
    cells = _cells(_llm([row], credit=credit)[0])
    cell = cells["openai/credit"]
    assert cell["status"] == "fail" and cell["facts"]["error"] == _OPENAI_429
    assert "enrich_listing_description on gpt-5.6-luna" in cell["message"]
    assert "newer than its newest success (5.0 h ago)" in cell["message"]
    assert "You have no credits remaining" in cell["message"]


def test_one_providers_success_never_masks_anothers_credit_outage() -> None:
    """D30: the text lane's OpenAI successes once cleared an empty Anthropic account."""
    anthropic = _calls("anthropic", "summarize_listing", last_ok=_ago(days=1),
                       last_err=_ago(minutes=30), last_credit=_ago(minutes=30), credit_errors=4,
                       calls=4, errors=4, attempts=2, successes=0)
    openai = _calls(last_ok=_ago(minutes=1))
    cells = _cells(_llm([anthropic, openai])[0])
    assert cells["anthropic/credit"]["status"] == "fail"
    assert cells["openai/credit"]["status"] == "ok"


def test_the_credit_arm_stays_red_after_callers_back_off_inside_the_lookback() -> None:
    row = _calls(last_ok=_ago(hours=70), last_err=_ago(hours=48), last_credit=_ago(hours=48),
                 credit_errors=40, calls=0, attempts=0, successes=0, spend=0.0)
    out, conn = _llm([row])
    assert _cells(out)["openai/credit"]["status"] == "fail"
    assert {p["lookback_h"] for _, p, _ in conn.sent if p} == {72}


def test_a_success_after_the_newest_credit_error_ends_the_outage() -> None:
    row = _calls(last_ok=_ago(minutes=1), last_err=_ago(minutes=40), last_credit=_ago(minutes=40),
                 credit_errors=32)
    assert _cells(_llm([row])[0])["openai/credit"]["status"] == "ok"


def test_only_the_credit_patterns_count_as_an_outage() -> None:
    """Auth failures (401, invalid key) are not a credit outage; their errors still count below."""
    assert plat._CREDIT_ERROR_PATTERNS == ("%credit balance%", "%no credits remaining%")
    row = _calls("qwen", "autodedup_judge_vision", last_ok=_ago(hours=2), last_err=_ago(minutes=1),
                 calls=40, errors=30, attempts=3, successes=0)
    cells = _cells(_llm([row], providers=("qwen",))[0])
    assert cells["qwen/credit"]["status"] == "ok"
    assert cells["qwen/autodedup_judge_vision"]["status"] == "warn"


def test_a_jobs_error_share_watches_only_while_its_providers_newest_call_errors() -> None:
    erroring = _calls(last_ok=_ago(minutes=9), last_err=_ago(minutes=1), calls=100, errors=20)
    assert _cells(_llm([erroring])[0])["openai/enrich_listing_description"]["status"] == "warn"
    recovered = _calls(last_ok=_ago(minutes=1), last_err=_ago(minutes=9), calls=100, errors=20)
    assert _cells(_llm([recovered])[0])["openai/enrich_listing_description"]["status"] == "ok"
    few = _calls(last_ok=_ago(minutes=9), last_err=_ago(minutes=1), calls=19, errors=19)
    assert _cells(_llm([few])[0])["openai/enrich_listing_description"]["status"] == "ok"


def test_starvation_is_five_attempts_and_no_success_in_the_last_hour() -> None:
    starved = _calls(last_ok=_ago(hours=2), last_err=_ago(minutes=1), attempts=5, successes=0)
    cell = _cells(_llm([starved])[0])["openai/enrich_listing_description"]
    assert cell["status"] == "fail" and cell["message"].startswith("starved: 5 calls")
    four = _calls(last_ok=_ago(hours=2), last_err=_ago(minutes=1), attempts=4, successes=0)
    assert _cells(_llm([four])[0])["openai/enrich_listing_description"]["status"] == "ok"


@pytest.mark.parametrize("spend, status", [(130.0, "ok"), (130.01, "warn"), (150.01, "fail")])
def test_fleet_spend_watches_above_130_and_fails_above_150(spend: float, status: str) -> None:
    cell = _cells(_llm([_calls(spend=spend)])[0])["fleet/spend"]
    assert cell["status"] == status and cell["unit"] == "USD"


def test_a_provider_with_no_calls_in_the_lookback_reads_ok_idle() -> None:
    cells = _cells(_llm([_calls()], providers=("anthropic", "gemini", "openai"))[0])
    assert cells["gemini"]["status"] == "ok" and cells["gemini"]["facts"] == {"state": "idle"}
    assert "gemini/credit" not in cells, "an idle provider's credit is not measured, so not OK"


# --- migration_drift and the module's own rails ----------------------------------------


def test_migration_drift_reads_the_repository_checkout() -> None:
    assert plat._MIGRATIONS_DIR.name == "migrations" and plat._MIGRATIONS_DIR.is_dir()
    assert (plat._MIGRATIONS_DIR / "620_health_liveness.sql").is_file()


def test_migration_drift_probes_through_the_shared_probe_on_its_own_clock() -> None:
    conn = _Conn({plat.MIGRATION_OBJECT_PROBE_SQL: lambda p: [
        (k, i, True) for k, i in zip(p["kinds"], p["idents"])]})
    out = plat.check_migration_drift(conn, {"migration_drift_window": 3})
    assert out["check_key"] == "migration_drift" and out["status"] == "ok"
    assert conn.sent and all(armed for _, _, armed in conn.sent)


def test_the_module_never_imports_the_harness_or_the_worker() -> None:
    """The harness imports these checks, so importing it back is a cycle; the worker is a lane."""
    tree = ast.parse(inspect.getsource(plat))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts", "scraper.db"}


def test_the_checks_read_exactly_the_thresholds_the_contract_names() -> None:
    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", inspect.getsource(plat)))
    assert read == set(T)


def test_the_harness_declares_every_threshold_these_checks_read_with_the_contracts_value() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in T} == T

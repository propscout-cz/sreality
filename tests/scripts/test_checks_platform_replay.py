"""Input replays: production history fed through jobs, capacity and llm, then through the ladder."""

# Each fixture is an aggregate exported read-only from production on 2026-10-18 (UTC to the
# second; no ids, no URLs, no error text). Refresh them read-only, never by hand, from the SQL
# below. A replay rebuilds, at each simulated tick, the rows a check's SQL would return then,
# runs the check on them and feeds its result to the ladder (toolkit.system_alerts.reconcile).
#
# tests/fixtures/health_jobs_14d.csv (arm,subject,status,from_at,to_at,runs), three arms:
#  cron: every pg_cron job's finished runs of the 14 days, run-length encoded by status and
#  broken at a gap over twice the job's median gap; a row's runs are spread evenly from from_at
#  to to_at.
#   with r as (select j.jobname, d.start_time, d.status, extract(epoch from d.start_time
#                     - lag(d.start_time) over (partition by d.jobid order by d.start_time)) gap
#                from cron.job_run_details d join cron.job j using (jobid)
#               where d.start_time > now() - interval '14 days' and d.end_time is not null),
#        m as (select jobname, percentile_cont(0.5) within group (order by gap) med
#                from r group by jobname),
#        b as (select r.*, case when lag(r.status) over (partition by r.jobname
#                                    order by r.start_time) is distinct from r.status
#                                 or r.gap > 2 * m.med then 1 else 0 end brk
#                from r join m using (jobname)),
#        g as (select *, sum(brk) over (partition by jobname order by start_time) grp from b)
#   select 'cron', jobname, status, min(start_time), max(start_time), count(*)
#     from g group by jobname, status, grp order by 2, 4;
#  poller: every cursor the canary's poller wrote since 2026-09-10 (from_at = the write, to_at =
#  the cursor it wrote). app_settings_history keeps each replaced value at the time it was
#  replaced, so a value was written when its predecessor was replaced:
#   with h as (select replaced_at, (value #>> '{}')::timestamptz v from app_settings_history
#               where key = 'workflow_failures_cursor'),
#        w as (select lag(replaced_at) over (order by replaced_at) written_at, v from h
#              union all select (select max(replaced_at) from h), (value #>> '{}')::timestamptz
#                          from app_settings where key = 'workflow_failures_cursor')
#   select 'poller', 'workflow_failures_cursor', 'poll', written_at, v, 1
#     from w where written_at >= '2026-09-10' order by 4;
#  gh: recompute_mf_yields' recorded runs of 09-10 to 09-14 (from_at = start, to_at = when the
#  poller recorded it) and its newest success, recorded at the first poll after it. Rows the
#  poller stored before it recorded the event are all schedule runs of this workflow.
#   select 'gh', 'recompute_mf_yields', conclusion, run_started_at, recorded_at, 1
#     from workflow_failures where workflow_path = '.github/workflows/recompute_mf_yields.yml'
#      and run_started_at >= '2026-09-10' and run_started_at < '2026-09-14'
#   union all
#   select 'gh', 'recompute_mf_yields', 'success', last_success_at,
#          (select min(recorded_at) from workflow_failures where recorded_at > h.last_success_at), 1
#     from workflow_run_health h where workflow_path = '.github/workflows/recompute_mf_yields.yml';
#
# tests/fixtures/health_capacity_cron_8w.csv (jobname,week,finished,failed,p50_s,p95_s,gap_s,
# timeout_s): per job and Monday week from 2026-08-10, one week before the 08-17 saturation (the
# sticky reference must predate the incident): finished-run duration p50/p95, the median start
# gap, and the statement_timeout of today's command.
#   with r as (select d.*, extract(epoch from d.start_time - lag(d.start_time)
#                     over (partition by d.jobid order by d.start_time)) gap_s
#                from cron.job_run_details d where d.start_time >= '2026-08-09')
#   select j.jobname, date_trunc('week', r.start_time at time zone 'UTC')::date week,
#          count(*) filter (where r.end_time is not null),
#          count(*) filter (where r.status = 'failed'),
#          percentile_cont(0.5) within group (order by extract(epoch from r.end_time
#                                                               - r.start_time)),
#          percentile_cont(0.95) within group (order by extract(epoch from r.end_time
#                                                                - r.start_time)),
#          percentile_cont(0.5) within group (order by r.gap_s),
#          substring(j.command from 'statement_timeout\s*=\s*''(\d+)s''')
#     from r join cron.job j using (jobid) where r.start_time >= '2026-08-10'
#    group by j.jobname, week, j.command order by 1, 2;
#
# tests/fixtures/health_llm_14d.csv (hour,provider,called_for,calls,errors,credit_errors,cost_usd,
# last_ok_s,last_err_s,last_credit_s): llm_calls per hour, provider and job from 2026-09-11 (a
# quiet fortnight with its 72 h lead-in, then the 10-02 and 10-18 OpenAI outages); last_*_s are
# the seconds into the hour of its newest success, error and credit error.
#   with c as (select date_trunc('hour', called_at) h, called_at, provider, called_for, error,
#                     cost_usd, coalesce(error ilike '%credit balance%'
#                                        or error ilike '%no credits remaining%', false) credit
#                from llm_calls where called_at >= '2026-09-11')
#   select h, provider, called_for, count(*), count(*) filter (where error is not null),
#          count(*) filter (where credit), round(coalesce(sum(cost_usd), 0), 4),
#          floor(extract(epoch from max(called_at) filter (where error is null) - h)),
#          floor(extract(epoch from max(called_at) filter (where error is not null) - h)),
#          floor(extract(epoch from max(called_at) filter (where credit) - h))
#     from c group by 1, 2, 3 order by 1, 2, 3;
#
# Stated limits: the worker lanes and the derived artifacts keep no history, so only their
# hermetic tests judge them. GitHub stored no successful run before wave 3 (only each
# workflow's newest), so a quiet fortnight of workflow cells cannot be rebuilt; the MF replay
# starts at its last success and assumes a live poller, though on 09-13 the real cursor was
# held at the page cap, so the gh/poller cell, not the workflow's, would have rung that day.

from __future__ import annotations

import bisect
import csv
import datetime as _dt
import functools
import statistics
from pathlib import Path
from typing import Any

from scripts.checks import platform as plat
from scripts.verify_pipeline import THRESHOLDS
from tests.scripts.test_checks_platform import _Conn
from tests.toolkit.test_system_alerts import _FakeConn as _Ladder
from toolkit.system_alerts import reconcile

_UTC = _dt.timezone.utc
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_MINUTE, _HOUR, _DAY = (_dt.timedelta(minutes=1), _dt.timedelta(hours=1), _dt.timedelta(days=1))


def _utc(stamp: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(stamp).astimezone(_UTC)


def _fixture(name: str) -> list[dict[str, str]]:
    with (_FIXTURES / name).open(newline="") as fh:
        return list(csv.DictReader(fh))


def _ticks(start: _dt.datetime, end: _dt.datetime, step: _dt.timedelta) -> list[_dt.datetime]:
    return [start + step * i for i in range(int((end - start) / step) + 1)]


def _told(key: str, cadence_s: int, results: list[tuple[_dt.datetime, dict[str, Any]]]) -> _Ladder:
    """The ladder after each result, in time order: a worker key's budget is three cadences."""
    ladder = _Ladder()
    ladder.register(key, cls="alarm", cadence_s=cadence_s, budget_s=3 * cadence_s)
    for at, result in results:
        ladder.result(key, at, result["status"], message=result["message"],
                      cells=result["details"]["cells"], value=result["value"])
        reconcile(ladder, now=at)
    return ladder


def _incidents(ladder: _Ladder, key: str, prefix: str = "") -> list[dict[str, Any]]:
    """Every cell incident the replay opened for `key` whose subject starts with `prefix`."""
    head = f"cell:{key}:"
    return [{**inc, "subject": inc["signature"][len(head):]} for inc in ladder.incidents
            if inc["signature"].startswith(head + prefix)]


def _jobs_at(at: _dt.datetime, *, cursor: _dt.datetime | None, workflows: list[Any],
             cron: list[Any]) -> dict[str, Any]:
    """check_jobs at `at` on these rows; no heartbeat or artifact history exists to replay."""
    conn = _Conn({plat._GH_CURSOR_SQL: [(cursor,)] if cursor else [],
                  plat._GH_WORKFLOWS_SQL: workflows, plat._CRON_JOBS_SQL: cron,
                  plat._HEARTBEAT_SQL: [], plat._ARTIFACTS_SQL: []}, now=at)
    return plat.check_jobs(conn, THRESHOLDS)


# --- jobs: pg_cron ---------------------------------------------------------------------------


@functools.cache
def _cron_runs() -> dict[str, tuple[list[_dt.datetime], list[str], list[float]]]:
    """Each job's runs expanded from the run-length rows: starts, statuses, gaps to the previous."""
    runs: dict[str, list[tuple[_dt.datetime, str]]] = {}
    for row in _fixture("health_jobs_14d.csv"):
        if row["arm"] != "cron":
            continue
        first, last, n = _utc(row["from_at"]), _utc(row["to_at"]), int(row["runs"])
        step = (last - first) / (n - 1) if n > 1 else _dt.timedelta(0)
        runs.setdefault(row["subject"], []).extend(
            (first + step * i, row["status"]) for i in range(n))
    out = {}
    for job, rows in runs.items():
        rows.sort()
        starts = [at for at, _ in rows]
        gaps = [0.0] + [(b - a).total_seconds() for a, b in zip(starts, starts[1:])]
        out[job] = (starts, [status for _, status in rows], gaps)
    return out


def _cron_rows(at: _dt.datetime) -> list[tuple[Any, ...]]:
    """_CRON_JOBS_SQL's rows at `at`: each job's 7 days counted back from its newest start."""
    rows = []
    for job, (starts, statuses, gaps) in sorted(_cron_runs().items()):
        hi = bisect.bisect_right(starts, at)
        if not hi:
            rows.append((job, True, None, 0, None, None, 0, None, None))
            continue
        newest = starts[hi - 1]
        lo = bisect.bisect_right(starts, newest - 7 * _DAY)
        window = gaps[lo + 1:hi]
        day = max(lo, bisect.bisect_right(starts, at - _DAY))
        failed = [starts[i] for i in range(lo, hi) if statuses[i] == "failed"]
        rows.append((job, True, newest, hi - lo, statistics.median(window) if window else None,
                     statuses[max(lo, hi - 3):hi][::-1], statuses[day:hi].count("failed"),
                     failed[-1] if failed else None,
                     "failed (the fixture keeps no message)" if failed else None))
    return rows


@functools.cache
def _cron_replay() -> _Ladder:
    starts = [s for runs in _cron_runs().values() for s in runs[0]]
    results = [(at, _jobs_at(at, cursor=at, workflows=[], cron=_cron_rows(at)))
               for at in _ticks(min(starts), max(starts), 10 * _MINUTE)]
    return _told("jobs", 600, results)


def test_the_cron_arm_opens_nothing_once_the_2xl_instance_settled() -> None:
    """From 10-11 refresh-health-dashboard failed four times, each alone: Watch, never told."""
    quiet = _utc("2026-10-11T00:00:00Z")
    opened = _incidents(_cron_replay(), "jobs", "cron/")
    assert [i for i in opened if i["first_seen_at"] >= quiet] == []


def test_the_saturation_tail_rings_the_health_refresh_that_kept_timing_out() -> None:
    opened = _incidents(_cron_replay(), "jobs", "cron/refresh-health-dashboard")
    assert opened and all(i["first_seen_at"] < _utc("2026-10-11T00:00:00Z") for i in opened)


def test_every_cron_onset_stands_on_real_failures_or_a_real_silence() -> None:
    """Only real incidents: 2 of the newest 3 runs failed, or pg_cron skipped 3 gaps' worth."""
    opened = _incidents(_cron_replay(), "jobs", "cron/")
    assert opened
    for inc in opened:
        at, job = inc["first_seen_at"], inc["subject"].removeprefix("cron/")
        _, _, newest, _, gap, last3, *_ = next(r for r in _cron_rows(at) if r[0] == job)
        assert last3.count("failed") >= 2 or (at - newest).total_seconds() > 3 * gap, inc


# --- jobs: the GitHub poller ----------------------------------------------------------------


@functools.cache
def _poller_replay() -> _Ladder:
    polls = sorted((_utc(r["from_at"]), _utc(r["to_at"])) for r in _fixture("health_jobs_14d.csv")
                   if r["arm"] == "poller")
    written = [at for at, _ in polls]
    first = polls[0][0].replace(minute=0, second=0) + _HOUR
    results = []
    for at in _ticks(first, polls[-1][0], _HOUR):
        cursor = polls[bisect.bisect_right(written, at) - 1][1]
        results.append((at, _jobs_at(at, cursor=cursor, workflows=[], cron=[])))
    return _told("jobs", 600, results)


def test_the_poller_cell_rings_both_real_poller_outages_and_closes_each_on_a_measured_ok() -> None:
    """Held at its page cap 09-11..09-25, reset to an old run 09-29..10-05: both real outages."""
    opened = _incidents(_poller_replay(), "jobs", "gh/poller")
    assert [i["first_seen_at"].date().isoformat() for i in opened] == ["2026-09-11", "2026-09-29"]
    assert [i["resolved_at"].date().isoformat() for i in opened] == ["2026-09-25", "2026-10-05"]
    assert {i["resolve_reason"] for i in opened} == {"measured ok"}


def test_the_canarys_ordinary_gaps_never_ring_the_poller_cell() -> None:
    """Since 10-05 the canary's longest gap stayed under 13 h, inside the 18 h limit."""
    after = _utc("2026-10-05T18:00:00Z")
    assert [i for i in _incidents(_poller_replay(), "jobs") if i["first_seen_at"] > after] == []


# --- jobs: recompute_mf_yields, 09-13 ---------------------------------------------------------


_MF = ".github/workflows/recompute_mf_yields.yml"


@functools.cache
def _mf_replay() -> tuple[_Ladder, dict[_dt.datetime, dict[str, Any]]]:
    runs = sorted((_utc(r["to_at"]), _utc(r["from_at"]), r["status"])
                  for r in _fixture("health_jobs_14d.csv") if r["arm"] == "gh")
    success = max(start for _, start, status in runs if status == "success")
    first = _utc("2026-09-13T09:50:00Z")
    assert min(seen for seen, start, _ in runs if start == success) <= first
    results, cells = [], {}
    for at in _ticks(first, _utc("2026-09-14T02:00:00Z"), 10 * _MINUTE):
        seen = [(start, status) for recorded, start, status in runs if recorded <= at]
        reds = [(start, status) for start, status in seen
                if status != "success" and start > success]
        newest = max(reds, default=(None, None))
        row = (_MF, "Jobs: recompute MF gross yields (hourly)", "active", success,
               sorted((start for start, _ in seen), reverse=True)[:20], len(reds), newest[0],
               newest[1], None, None)
        cursor = max(recorded for recorded, _, _ in runs if recorded <= at)
        result = _jobs_at(at, cursor=cursor, workflows=[row], cron=[])
        results.append((at, result))
        cells[at] = {c["subject"]: c for c in result["details"]["cells"]}
    return _told("jobs", 600, results), cells


def test_the_mf_streak_rings_once_at_its_third_production_red() -> None:
    """Last success 04:58; the 10:22 and 14:28 reds are Watch, the 18:14 one (seen 20:34) rings."""
    ladder, cells = _mf_replay()
    opened = _incidents(ladder, "jobs", "gh/recompute_mf_yields")
    assert [i["first_seen_at"] for i in opened] == [_utc("2026-09-13T20:40:00Z")]
    onset = cells[_utc("2026-09-13T20:40:00Z")]["gh/recompute_mf_yields"]
    assert onset["status"] == "fail" and onset["message"].startswith("3 production reds in a row")
    lone = cells[_utc("2026-09-13T17:40:00Z")]["gh/recompute_mf_yields"]
    assert lone["status"] == "warn" and lone["value"] == 2


# --- capacity -----------------------------------------------------------------------------------


def _capacity_rows(at: _dt.datetime, weeks: list[dict[str, str]]) -> list[tuple[Any, ...]]:
    """_CAPACITY_CRON_SQL's rows at `at`, the 24 h figures read off the week that holds it."""
    monday = (at - _dt.timedelta(days=at.weekday())).date().isoformat()
    rows = []
    for job in sorted({w["jobname"] for w in weeks}):
        week = next((w for w in weeks if w["jobname"] == job and w["week"] == monday), None)
        command = f"set statement_timeout='{week['timeout_s']}s'; select 1;" if week else ""
        if week is None or not int(week["finished"]):
            rows.append((job, True, command, None, None, 0, None))
            continue
        rows.append((job, True, command, float(week["p50_s"]), float(week["p95_s"]),
                     int(week["finished"]), float(week["gap_s"]) if week["gap_s"] else None))
    return rows


def _reference_rows(at: _dt.datetime, weeks: list[dict[str, str]],
                    ref: _dt.datetime) -> list[tuple[Any, ...]]:
    """_CAPACITY_REFERENCE_SQL's rows: the whole weeks since the reference, as week indexes."""
    rows = []
    for w in weeks:
        start = _dt.datetime.fromisoformat(w["week"]).replace(tzinfo=_UTC)
        if start >= ref and start + 7 * _DAY <= at:
            rows.append((w["jobname"], (start - ref).days // 7, float(w["p50_s"])))
    return rows


def _capacity_replay(reference_from: _dt.date, start: str, end: str) -> tuple[_Ladder, list[Any]]:
    weeks = _fixture("health_capacity_cron_8w.csv")
    ref = _dt.datetime.combine(reference_from, _dt.time(), tzinfo=_UTC)
    thresholds = {**THRESHOLDS, "CAPACITY_REFERENCE_FROM": reference_from}
    results = []
    for at in _ticks(_utc(start), _utc(end), 6 * _HOUR):
        conn = _Conn({
            plat._CAPACITY_CRON_SQL: _capacity_rows(at, weeks),
            plat._CAPACITY_REFERENCE_SQL: _reference_rows(at, weeks, ref),
            plat._CHECK_TIMEOUTS_SQL: [("github-actions", 300, 0, None),
                                       ("realtime-worker", 336, 0, None)],
            plat._SERVER_SQL: [(at - 2 * _DAY, 380, 29, 0)],
            plat._WORKER_MEMORY_SQL: [(at - _MINUTE, at - 2 * _DAY, (at - 10 * _MINUTE).isoformat(),
                                       "object", "7000", "22888")],
        }, now=at)
        results.append((at, plat.check_capacity(conn, thresholds)))
    return _told("capacity", 1800, results), results


def test_the_browse_list_saturation_never_recovers_against_the_sticky_reference() -> None:
    """Its p50 ran 161-254 s from 08-17 against ~10 s the week before; it stays red to the end."""
    ladder, results = _capacity_replay(_dt.date(2026, 8, 10), "2026-08-10T06:00:00Z",
                                       "2026-10-18T18:00:00Z")
    opened = _incidents(ladder, "capacity", "cron/browse-list-rebuild")
    assert len(opened) == 1 and opened[0]["resolved_at"] is None
    assert opened[0]["first_seen_at"] < _utc("2026-08-17T00:00:00Z")
    relative = [r for at, r in results if at >= _utc("2026-08-17T00:00:00Z")
                and at < _utc("2026-10-11T00:00:00Z")]
    for result in relative:
        cell = {c["subject"]: c for c in result["details"]["cells"]}["cron/browse-list-rebuild"]
        assert cell["status"] == "fail" and "its reference 10.0 s" in cell["message"], cell


def test_the_settled_week_on_the_blessed_reference_opens_nothing() -> None:
    """The 2XL week of 10-12 on the 10-17 reference: no whole week yet, so no relative verdict."""
    ladder, results = _capacity_replay(THRESHOLDS["CAPACITY_REFERENCE_FROM"],
                                       "2026-10-12T00:00:00Z", "2026-10-18T18:00:00Z")
    assert ladder.incidents == []
    for _, result in results:
        for cell in result["details"]["cells"]:
            if cell["subject"].startswith("cron/") and cell["status"] != "unknown":
                assert cell["facts"]["relative"].startswith("Not measured"), cell


# --- llm --------------------------------------------------------------------------------------


@functools.cache
def _llm_hours() -> dict[tuple[str, str], list[tuple[_dt.datetime, dict[str, str]]]]:
    pairs: dict[tuple[str, str], list[tuple[_dt.datetime, dict[str, str]]]] = {}
    for row in _fixture("health_llm_14d.csv"):
        pairs.setdefault((row["provider"], row["called_for"]), []).append((_utc(row["hour"]), row))
    return pairs


def _newest(hours: list[tuple[_dt.datetime, dict[str, str]]], column: str) -> _dt.datetime | None:
    stamps = [hour + _dt.timedelta(seconds=int(row[column])) for hour, row in hours if row[column]]
    return max(stamps, default=None)


def _llm_rows(at: _dt.datetime) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """_LLM_CALLS_SQL's and _LLM_CREDIT_ERROR_SQL's rows at an hour boundary `at`."""
    calls, credit = [], {}
    for (provider, called_for), hours in sorted(_llm_hours().items()):
        stamps = [hour for hour, _ in hours]
        window = hours[bisect.bisect_left(stamps, at - 72 * _HOUR):bisect.bisect_left(stamps, at)]
        if not window:
            continue
        day = [row for hour, row in window if hour >= at - 24 * _HOUR]
        last = [row for hour, row in window if hour == at - _HOUR]
        credit_errors = sum(int(row["credit_errors"]) for _, row in window)
        last_credit = _newest(window, "last_credit_s")
        calls.append((provider, called_for, _newest(window, "last_ok_s"),
                      _newest(window, "last_err_s"), last_credit, credit_errors,
                      ["(not exported)"] if credit_errors else None,
                      sum(int(r["calls"]) for r in day), sum(int(r["errors"]) for r in day),
                      sum(int(r["calls"]) for r in last),
                      sum(int(r["calls"]) - int(r["errors"]) for r in last),
                      sum(float(r["cost_usd"]) for r in day)))
        if last_credit and (provider not in credit or last_credit > credit[provider][1]):
            credit[provider] = (provider, last_credit, called_for, "(not exported)",
                                "(the fixture keeps no error text)")
    return calls, list(credit.values())


@functools.cache
def _llm_replay() -> _Ladder:
    hours = sorted({hour for pair in _llm_hours().values() for hour, _ in pair})
    # The providers production's llm_cost_hour_rollup knew at export: gemini never called.
    providers = [("anthropic",), ("gemini",), ("openai",), ("oss",), ("qwen",)]
    results = []
    for at in _ticks(hours[0] + _HOUR, hours[-1] + _HOUR, _HOUR):
        calls, credit = _llm_rows(at)
        conn = _Conn({plat._LLM_PROVIDERS_SQL: providers, plat._LLM_CALLS_SQL: calls,
                      plat._LLM_CREDIT_ERROR_SQL: credit}, now=at)
        results.append((at, plat.check_llm(conn, THRESHOLDS)))
    return _told("llm", 600, results)


def test_a_quiet_fortnight_of_llm_calls_opens_nothing() -> None:
    """09-14..09-28: the judges and the text lane at full traffic, qwen's quota errors a Watch."""
    quiet = [i for i in _incidents(_llm_replay(), "llm")
             if _utc("2026-09-14T00:00:00Z") <= i["first_seen_at"] < _utc("2026-09-28T00:00:00Z")]
    assert quiet == []


def test_both_openai_outages_ring_their_credit_cell() -> None:
    opened = [i["first_seen_at"] for i in _incidents(_llm_replay(), "llm", "openai/credit")]
    assert any(at.date() == _dt.date(2026, 10, 2) for at in opened)
    assert any(_utc("2026-10-18T17:00:00Z") <= at <= _utc("2026-10-18T22:00:00Z") for at in opened)


def test_every_llm_onset_stands_on_a_real_outage_hour() -> None:
    """Only real incidents: credit errors, or five calls and no answer, in the hour before."""
    pairs = _llm_hours()
    opened = _incidents(_llm_replay(), "llm")
    assert opened
    for inc in opened:
        provider, _, job = inc["subject"].partition("/")
        hour = [row for (p, j), rows in pairs.items() for at, row in rows
                if p == provider and (job == "credit" or j == job)
                and at == inc["first_seen_at"] - _HOUR]
        assert any(int(r["credit_errors"]) for r in hour) or any(
            int(r["calls"]) >= 5 and int(r["calls"]) == int(r["errors"]) for r in hour), inc

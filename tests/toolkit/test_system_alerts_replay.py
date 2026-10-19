"""Replay rail: 14 days of real check history fed through the ladder."""

# The fixture is pipeline_check_results run-length encoded (check_key,status,from_run_at,to_run_at,
# runs; UTC to the second), the unmeasured warns of the time read as unknown, with no ids and no
# URLs, for the keys CHECKS declares (`:keys`; exported 2026-10-19 05:09Z, before wave 4's keys
# had rows; wave 5 retired broker_merge_suppression and dropped its one row, 111 runs all OK).
# Refresh it read-only, never by hand, from:
#   with r as (select check_key, run_at,
#                     case when status = 'warn' and (details ? 'timed_out' or details ? 'not_run')
#                          then 'unknown' else status end as s
#                from pipeline_check_results where run_at > now() - interval '14 days'
#                 and check_key = any(:keys)),
#        g as (select *, row_number() over (partition by check_key order by run_at)
#                      - row_number() over (partition by check_key, s order by run_at) as grp
#                from r)
#   select check_key, s, min(run_at), max(run_at), count(*) from g
#    group by check_key, s, grp order by 1, 3;

from __future__ import annotations

import csv
import datetime as _dt
import functools
from dataclasses import dataclass
from pathlib import Path

from tests.toolkit.test_system_alerts import _FakeConn
from toolkit.system_alerts import RECOVERY_HOLD_MIN_S, due_after_s, reconcile

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "health_replay_14d.csv"
# Production reconciles every 60 s; every row's arrival plus a 10-minute tick resolves holds of
# 108 minutes and more, and keeps the replay well under a second.
_TICK = _dt.timedelta(minutes=10)
# Check.staleness_budget: cadence plus this slack on github-actions; three cadences on the worker,
# whose wave-3 keys have rows from 2026-10-19 00:39Z and whose wave-4 and wave-5 keys have none.
_GITHUB_SLACK = _dt.timedelta(hours=18)
_GH, _RW = "github-actions", "realtime-worker"

# The registry (scripts/verify_pipeline.py CHECKS) as (class, cadence, host). The loader drops the
# rows of a key retired since the export, as the ladder reads none of them.
_REGISTRY: dict[str, tuple[str, int, str]] = {
    "location_town_coverage": ("gauge", 21600, _GH), "jobs": ("alarm", 600, _RW),
    "capacity": ("alarm", 1800, _RW), "llm": ("alarm", 600, _RW),
    "scraping": ("alarm", 600, _RW), "publishing": ("alarm", 600, _RW),
    "delisting": ("alarm", 600, _RW), "coverage": ("gauge", 3600, _RW),
    "dedup": ("alarm", 600, _RW),
    "ppm2_median_shift": ("alarm", 21600, _GH), "ppm2_basis_floor_share": ("gauge", 21600, _GH),
    "area_vs_usable_divergence": ("gauge", 21600, _GH),
    "ppm2_measure_coverage": ("alarm", 21600, _GH), "migration_drift": ("alarm", 3600, _GH),
    "location_payload_shape_drift": ("alarm", 21600, _GH),
    "outbound_url_coverage": ("gauge", 21600, _GH), "field_fill_matrix": ("alarm", 21600, _GH),
    "text_extraction_lag": ("gauge", 21600, _GH), "floor_convention": ("gauge", 21600, _GH),
    "outbound_url_conformance": ("alarm", 604800, _GH),
}

_Rows = list[tuple[_dt.datetime, str]]


@dataclass(frozen=True)
class _Told:
    """One notice the replay emitted, with the key's newest status when it went out."""

    at: _dt.datetime
    key: str
    kind: str
    rung: str
    newest: str | None


def _budget(key: str) -> _dt.timedelta:
    _cls, cadence_s, host = _REGISTRY[key]
    if host == _GH:
        return _dt.timedelta(seconds=cadence_s) + _GITHUB_SLACK
    return _dt.timedelta(seconds=3 * cadence_s)


def _hold(key: str) -> _dt.timedelta:
    """Two due intervals (a cadence less its due slack), never under the floor."""
    return _dt.timedelta(seconds=max(2 * due_after_s(_REGISTRY[key][1]), RECOVERY_HOLD_MIN_S))


def _history() -> dict[str, _Rows]:
    """Every run of the export expanded into evenly spaced rows, per key, oldest first."""
    history: dict[str, _Rows] = {}
    with _FIXTURE.open(newline="") as fh:
        for run in csv.DictReader(fh):
            start = _dt.datetime.fromisoformat(run["from_run_at"])
            end = _dt.datetime.fromisoformat(run["to_run_at"])
            n = int(run["runs"])
            step = (end - start) / (n - 1) if n > 1 else _dt.timedelta(0)
            history.setdefault(run["check_key"], []).extend(
                (start + step * i, run["status"]) for i in range(n))
    return {key: sorted(rows) for key, rows in history.items() if key in _REGISTRY}


def _passes(history: dict[str, _Rows]) -> list[_dt.datetime]:
    """A pass at every row's arrival and on a regular tick in between."""
    arrivals = {at for rows in history.values() for at, _ in rows}
    start, end = min(arrivals), max(arrivals)
    ticks = {start + _TICK * i for i in range(int((end - start) / _TICK) + 1)}
    return sorted(arrivals | ticks)


@functools.cache
def _replay() -> tuple[dict[str, _Rows], list[_dt.datetime], _FakeConn, list[_Told]]:
    """Feed the history to reconcile in time order through the fake, once per test session."""
    history = _history()
    passes = _passes(history)
    conn = _FakeConn()
    for key in history:
        cls, cadence_s, _host = _REGISTRY[key]
        conn.register(key, cls=cls, cadence_s=cadence_s,
                      budget_s=_budget(key).total_seconds())
    fed = dict.fromkeys(history, 0)
    told: list[_Told] = []
    for at in passes:
        for key, rows in history.items():
            while fed[key] < len(rows) and rows[fed[key]][0] <= at:
                conn.result(key, *rows[fed[key]])
                fed[key] += 1
        before = len(conn.dispatches)
        reconcile(conn, now=at)
        for _message, dedupe_key, _channels in conn.dispatches[before:]:
            _sys, key, kind, rest = dedupe_key.split(":", 3)
            rung = rest.split(":", 1)[0] if kind == "reminder" else ""
            told.append(_Told(at, key, kind, rung, conn.newest_status(key)))
    return history, passes, conn, told


def _streaks(rows: _Rows, hold: _dt.timedelta) -> int:
    """The rule restated: a fail opens a streak; an OK measured `hold` after the first OK ends it."""
    count, is_open, ok_since = 0, False, None
    for at, status in rows:
        if status == "fail":
            count += not is_open
            is_open, ok_since = True, None
        elif status == "warn":
            ok_since = None
        elif status == "ok" and is_open:
            ok_since = ok_since or at
            if at - ok_since >= hold:
                is_open, ok_since = False, None
    return count


def _rung(cls: str, hours: float) -> str | None:
    """The rung table restated: alarm 6 h / 24 h / 72 h then weekly (w2 = week two), gauge daily."""
    if cls == "gauge":
        return f"d{int(hours // 24)}" if hours >= 24 else None
    if hours >= 168:
        return f"w{int(hours // 168) + 1}"
    return next((f"{h}h" for h in (72, 24, 6) if hours >= h), None)


def test_the_replay_registry_is_the_harness_registry() -> None:
    """A class, cadence, host or budget change in CHECKS must reach this rail, not drift past it."""
    from scripts.verify_pipeline import CHECKS

    assert _REGISTRY == {c.key: (c.cls, c.cadence_s, c.host) for c in CHECKS}
    assert {c.key: c.staleness_budget for c in CHECKS} == {key: _budget(key) for key in _REGISTRY}


def test_no_recovery_is_told_unless_the_newest_row_is_a_measured_ok() -> None:
    *_, told = _replay()
    recoveries = [t for t in told if t.kind == "recovery"]
    assert recoveries, "the window holds real recoveries"
    assert {t.newest for t in recoveries} == {"ok"}


def test_every_incident_write_is_anchored_on_a_measured_row_that_says_so() -> None:
    """Not measured never opens, keeps or starts a hold, even where the hold would absorb it."""
    history, _passes, conn, _told = _replay()
    status_at = {(key, at): status for key, rows in history.items() for at, status in rows}
    key_of = {i["id"]: i["signature"].split(":")[1] for i in conn.incidents}
    anchored = 0
    for sql, params in conn.executed:
        norm = " ".join(sql.split())
        if norm.startswith("INSERT INTO ops_incidents"):
            assert status_at[(params["sig"].split(":")[1], params["at"])] == "fail"
        elif "SET last_seen_at" in norm:
            assert status_at[(key_of[params["id"]], params["at"])] in ("fail", "warn")
        elif "SET ok_since" in norm:
            assert status_at[(key_of[params["id"]], params["at"])] == "ok"
        else:
            continue
        anchored += 1
    assert anchored > 0


def test_each_maximal_fail_streak_opens_exactly_one_incident_and_one_onset() -> None:
    history, _passes, conn, told = _replay()
    total = 0
    for key, rows in history.items():
        expected = _streaks(rows, _hold(key))
        opened = [i for i in conn.incidents if i["signature"] == f"cell:{key}:{key}"]
        assert len(opened) == expected, key
        assert sum(1 for t in told if t.key == key and t.kind == "onset") == expected, key
        total += expected
    assert total > 0
    # Nothing in this window went a week unmeasured, so every close was a measured recovery.
    assert {i["resolve_reason"] for i in conn.incidents} <= {None, "measured ok"}


def test_a_key_failing_all_window_tells_one_onset_then_only_its_rungs() -> None:
    history, passes, _conn, told = _replay()
    always = [key for key, rows in history.items()
              if {status for _, status in rows} - {"unknown"} == {"fail"}]
    assert always, "the window holds a key that never measured anything but Problem"
    for key in always:
        rows, cls = history[key], _REGISTRY[key][0]
        first = next(at for at, status in rows if status == "fail")
        rungs: list[str] = []
        i = -1
        for at in passes:
            while i + 1 < len(rows) and rows[i + 1][0] <= at:
                i += 1
            # Reminders come only while the newest row is measured: not unknown, not stale.
            if i < 0 or rows[i][1] == "unknown" or rows[i][0] < at - _budget(key):
                continue
            rung = _rung(cls, (at - first).total_seconds() / 3600)
            if rung and rung not in rungs:
                rungs.append(rung)
        mine = [t for t in told if t.key == key]
        assert [t.kind for t in mine] == ["onset"] + ["reminder"] * len(rungs), key
        assert [t.rung for t in mine[1:]] == rungs, key

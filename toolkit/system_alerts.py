"""System alerts: the one health ladder, from check results to incidents and their rungs.

`reconcile` reads each registered check's newest result, keeps one `ops_incidents` row per
failing cell (`cell:<key>:<subject>`) and tells each one by stamping it (`alert_count`,
`alerted_at`): an onset, then reminders by class until it is acknowledged on Health; one
recovery closes it once an OK is measured again across the hold. Health shows the incidents and
the GitHub canary (scripts/health_canary.py) turns an alarm's unacknowledged rung into the
operator's e-mail; no system alert reaches the bell (ruling R2, notifications W1). A newest
result that is `unknown`, past its key's staleness budget or older than the key's registration
is Not measured, and Not measured is inert: it neither opens, keeps, recovers nor reminds. A
subject the newest measured result no longer lists has left its check and closes as retired.
Stdlib plus a caller-passed psycopg connection, so the API and the scripts import it with base
dependencies only.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

# Six hours reaches a missed onset the same day; 24 h and 72 h repeat it before it is forgotten.
ALARM_RUNGS_H = (6.0, 24.0, 72.0)
# Past the fixed rungs an unfixed alarm keeps saying so weekly, too rarely to become wallpaper.
ALARM_WEEKLY_H = 168.0
# The operator's ruling of 2026-10-18: a gauge rings when it first reads Problem, then once a day.
GAUGE_RUNG_H = 24.0
# A lane measures a check again once its newest row is a cadence old less a tenth of the cadence,
# at most this: every row carries its run's START, so the next run, started a minute earlier in
# its slot, would find the row not due and measure the check only every second run.
DUE_SLACK_MAX_S = 900
# Recovery needs a second OK measured at least two due intervals (`due_after_s`), and never less
# than this floor, after the first, so neither a lone green run nor two quick greens of a fast
# check close an incident.
RECOVERY_HOLD_MIN_S = 1800
# A listed cell nobody measured for a week can never close on a measured OK, so it closes silently.
UNMEASURED_CLOSE_DAYS = 7
# The signature namespace of cell incidents, the only kind since the signature engine retired (621).
CELL_PREFIX = "cell:"

_RANK = {"ok": 0, "warn": 1, "fail": 2}

_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(hashtext('health_reconcile'))"

# Only rows since the key's registration count: the harness before it stored a check's own
# error as fail and no message, so a key is Not measured until the new harness measures it.
# The budget is read as seconds: psycopg parses interval text only under the default IntervalStyle.
_NEWEST_SQL = """
SELECT hc.key, hc.class, hc.cadence_s,
       extract(epoch FROM hc.staleness_budget)::float8,
       latest.status, latest.run_at, latest.details -> 'cells'
  FROM health_checks hc
  LEFT JOIN LATERAL (
        SELECT c.status, c.run_at, c.details
          FROM pipeline_check_results c
         WHERE c.check_key = hc.key AND c.run_at >= hc.declared_at
         ORDER BY c.run_at DESC
         LIMIT 1) latest ON true
 ORDER BY hc.sort, hc.key
"""

_OPEN_INCIDENTS_SQL = """
SELECT id, signature, first_seen_at, last_seen_at, alert_count, alerted_at, ok_since,
       acknowledged_at
  FROM ops_incidents
 WHERE resolved_at IS NULL AND signature LIKE %(cells)s
"""

_OPEN_SQL = """
INSERT INTO ops_incidents (signature, first_seen_at, last_seen_at)
VALUES (%(sig)s, %(at)s, %(at)s)
ON CONFLICT (signature) WHERE resolved_at IS NULL DO NOTHING
RETURNING id
"""

_SEEN_SQL = """
UPDATE ops_incidents SET last_seen_at = greatest(last_seen_at, %(at)s), ok_since = NULL
 WHERE id = %(id)s
"""

_OK_SINCE_SQL = "UPDATE ops_incidents SET ok_since = %(at)s WHERE id = %(id)s AND ok_since IS NULL"

_RESOLVE_SQL = """
UPDATE ops_incidents SET resolved_at = %(now)s, resolve_reason = %(reason)s
 WHERE id = ANY(%(ids)s) AND resolved_at IS NULL
"""

_STAMP_SQL = """
UPDATE ops_incidents SET alert_count = alert_count + 1, alerted_at = %(now)s
 WHERE id = ANY(%(ids)s)
"""

def _as_utc(value: _dt.datetime) -> _dt.datetime:
    """Tolerate a naive timestamp from a caller or a fixture; the column is timestamptz."""
    if value.tzinfo is None:
        return value.replace(tzinfo=_dt.timezone.utc)
    return value.astimezone(_dt.timezone.utc)


def _opt_utc(value: _dt.datetime | None) -> _dt.datetime | None:
    """`_as_utc` for a nullable column."""
    return None if value is None else _as_utc(value)


@dataclass
class _Check:
    """A registered check and its newest result, split into the cells this pass judges."""

    key: str
    cls: str
    cadence_s: int
    run_at: _dt.datetime | None
    cells: dict[str, dict[str, Any]]
    listed: set[str] | None  # every subject of a measured result, Not measured ones too; else None


@dataclass
class _Incident:
    """An open cell incident as this pass holds it."""

    id: int
    key: str
    subject: str
    first_seen_at: _dt.datetime
    last_seen_at: _dt.datetime
    alerted_at: _dt.datetime | None
    ok_since: _dt.datetime | None
    acknowledged: bool


def due_after_s(cadence_s: int) -> int:
    """How old a check's newest result must be before a lane measures it again."""
    return cadence_s - min(cadence_s // 10, DUE_SLACK_MAX_S)


def _hold(cadence_s: int) -> _dt.timedelta:
    """How long after a cell's first OK a later measured OK must come to recover its incident."""
    # Two due intervals, not two cadences: a lane measures a check up to a slack early, so two
    # measured intervals can sum to under two cadences and a two-cadence hold waits for a 4th OK.
    return _dt.timedelta(seconds=max(2 * due_after_s(cadence_s), RECOVERY_HOLD_MIN_S))


def _unmeasured_window(cadence_s: int) -> _dt.timedelta:
    """How long a cell may go unmeasured before its incident closes; weekly checks get two weeks."""
    return _dt.timedelta(seconds=max(UNMEASURED_CLOSE_DAYS * 86400, 2 * cadence_s))


def _stale(stamp: _dt.datetime, budget_s: float, at: _dt.datetime) -> bool:
    """`health_stale` (migration 619) on this pass's clock: older than the key's budget."""
    return stamp < at - _dt.timedelta(seconds=float(budget_s))


def _cells(raw: Any) -> dict[str, dict[str, Any]]:
    """A result's verdict cells by subject (`details -> 'cells'`), the worst one per subject."""
    # No cell is ever invented (wave 6 deleted the one named by the key): a result without a
    # verdict cell opens, keeps and recovers nothing, so every check writes its own, OK included.
    cells: dict[str, dict[str, Any]] = {}
    for cell in raw if isinstance(raw, list) else []:
        if not isinstance(cell, dict) or cell.get("status") not in _RANK:
            continue
        subject = str(cell.get("subject") or "")
        held = cells.get(subject)
        if subject and (held is None or _RANK[cell["status"]] > _RANK[held["status"]]):
            cells[subject] = cell
    return cells


def _check(row: tuple[Any, ...], at: _dt.datetime) -> _Check:
    """A registry row with its newest result; only a measured result inside its budget has cells."""
    key, cls, cadence_s, budget_s, status, run_at, raw_cells = row
    run_at, cadence = _opt_utc(run_at), int(cadence_s)
    # pipeline_checks_public's effective_status: missing, unknown or stale is Not measured.
    measured = (status in _RANK and run_at is not None
                and not _stale(run_at, budget_s, at))
    if not measured:
        return _Check(key, cls, cadence, run_at, {}, None)
    raw = raw_cells if isinstance(raw_cells, list) else []
    listed = {str(c["subject"]) for c in raw if isinstance(c, dict) and c.get("subject")}
    return _Check(key, cls, cadence, run_at, _cells(raw_cells), listed)


def _incident(row: tuple[Any, ...]) -> _Incident:
    """An open cell incident row; its key is the signature's first field (keys hold no colon)."""
    inc_id, signature, first_seen, last_seen, _count, alerted, ok_since, acked = row
    key, _, subject = signature[len(CELL_PREFIX):].partition(":")
    return _Incident(int(inc_id), key, subject, _as_utc(first_seen), _as_utc(last_seen),
                     _opt_utc(alerted), _opt_utc(ok_since), acked is not None)


def _rung(cls: str, elapsed_h: float) -> float | None:
    """The hours of the highest reminder rung an incident open `elapsed_h` hours has reached."""
    if cls == "gauge":
        days = int(elapsed_h // GAUGE_RUNG_H)
        return days * GAUGE_RUNG_H if days >= 1 else None
    weeks = int(elapsed_h // ALARM_WEEKLY_H)
    if weeks >= 1:
        return weeks * ALARM_WEEKLY_H  # the 168 h rung opens week two
    reached = [h for h in ALARM_RUNGS_H if h <= elapsed_h]
    return max(reached) if reached else None


def _reminded(
    check: _Check, incidents: list[_Incident], current: dict[int, str], at: _dt.datetime,
) -> list[_Incident]:
    """The incidents of `check` a reminder rung is due for this pass: measured failing or warning
    now, unacknowledged, and not stamped since their highest rung opened."""
    due = []
    for inc in incidents:
        if inc.id not in current or inc.acknowledged:
            continue
        rung_h = _rung(check.cls, (at - inc.first_seen_at).total_seconds() / 3600)
        if rung_h is not None and (inc.alerted_at is None
                                   or inc.alerted_at < inc.first_seen_at + _dt.timedelta(hours=rung_h)):
            due.append(inc)
    return due


def _dark(inc: _Incident, check: _Check, at: _dt.datetime) -> bool:
    """Unjudged by the newest result and last measured a window ago (a listed cell's OK counts)."""
    if inc.subject in check.cells:
        return False
    ok = inc.ok_since if inc.ok_since and inc.subject in (check.listed or ()) else None
    return (ok or inc.last_seen_at) < at - _unmeasured_window(check.cadence_s)


def reconcile(conn: Any, *, now: _dt.datetime | None = None) -> dict[str, int]:
    """One ladder pass in one transaction: open, keep, recover and close incidents, then tell."""
    at = _as_utc(now) if now is not None else _dt.datetime.now(_dt.timezone.utc)
    counts = dict.fromkeys(("opened", "reminded", "recovered", "closed_silently", "notices"), 0)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_LOCK_SQL)
        locked = cur.fetchone()
        if not locked or not locked[0]:
            return counts  # another process is reconciling
        cur.execute(_NEWEST_SQL)
        checks = {c.key: c for c in (_check(row, at) for row in cur.fetchall())}
        cur.execute(_OPEN_INCIDENTS_SQL, {"cells": CELL_PREFIX + "%"})
        incidents = [_incident(row) for row in cur.fetchall()]
        by_sig = {f"{CELL_PREFIX}{i.key}:{i.subject}": i for i in incidents}

        opened: dict[str, list[_Incident]] = {}
        current: dict[int, str] = {}  # incident id -> fail | warn, as measured this pass
        recovered: dict[str, list[_Incident]] = {}
        for check in checks.values():
            for subject, cell in check.cells.items():
                status, run_at = cell["status"], check.run_at
                inc = by_sig.get(f"{CELL_PREFIX}{check.key}:{subject}")
                if inc is None:
                    if status == "fail":
                        cur.execute(_OPEN_SQL, {"sig": f"{CELL_PREFIX}{check.key}:{subject}",
                                                "at": run_at})
                        row = cur.fetchone()
                        if row:
                            opened.setdefault(check.key, []).append(_Incident(
                                int(row[0]), check.key, subject, run_at, run_at, None, None, False))
                    continue
                if status != "ok":
                    if inc.last_seen_at < run_at or inc.ok_since is not None:
                        cur.execute(_SEEN_SQL, {"id": inc.id, "at": run_at})
                        inc.last_seen_at, inc.ok_since = max(inc.last_seen_at, run_at), None
                    current[inc.id] = status
                    continue
                if inc.ok_since is None:
                    cur.execute(_OK_SINCE_SQL, {"id": inc.id, "at": run_at})
                    inc.ok_since = run_at
                # Measured time, not wall-clock: silence after one OK never recovers anything.
                if run_at - inc.ok_since >= _hold(check.cadence_s):
                    recovered.setdefault(check.key, []).append(inc)
        counts["opened"] = sum(len(v) for v in opened.values())

        closed = {i.id for v in recovered.values() for i in v}
        if closed:
            cur.execute(_RESOLVE_SQL, {"now": at, "reason": "measured ok", "ids": sorted(closed)})
        counts["recovered"] = len(closed)

        still_open = [i for i in incidents if i.id not in closed]
        retired = [i for i in still_open if i.key not in checks or (
            checks[i.key].listed is not None and i.subject not in checks[i.key].listed)]
        gone = {i.id for i in retired}
        dark = [i for i in still_open if i.id not in gone and _dark(i, checks[i.key], at)]
        unmeasured = f"unmeasured {UNMEASURED_CLOSE_DAYS}d"
        for reason, group in (("retired", retired), (unmeasured, dark)):
            if group:
                cur.execute(_RESOLVE_SQL, {"now": at, "reason": reason,
                                           "ids": sorted(i.id for i in group)})
        counts["closed_silently"] = len(retired) + len(dark)

        # Telling is stamping: one onset and at most one reminder per check per pass, each
        # stamping the incidents it names; a recovery is its RESOLVE above. `notices` counts the
        # rungs told, one per check and kind.
        by_key: dict[str, list[_Incident]] = {}
        for inc in incidents:
            by_key.setdefault(inc.key, []).append(inc)
        for key, check in checks.items():
            if key in opened:
                cur.execute(_STAMP_SQL, {"now": at, "ids": [i.id for i in opened[key]]})
                counts["notices"] += 1
            reminded = _reminded(check, by_key.get(key, []), current, at)
            if reminded:
                cur.execute(_STAMP_SQL, {"now": at, "ids": [i.id for i in reminded]})
                counts["reminded"] += len(reminded)
                counts["notices"] += 1
        counts["notices"] += len(recovered)
    return counts

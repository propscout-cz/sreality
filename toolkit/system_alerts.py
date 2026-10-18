"""System alerts: the one health ladder, from check results to incidents to notices.

`reconcile` reads each registered check's newest result, keeps one `ops_incidents` row per
failing cell (`cell:<key>:<subject>`) and tells each one through `system_health`
`notification_dispatches` rows: an onset, reminders by class until it is acknowledged on
Health, and one recovery once an OK is measured again across the hold. A newest result that
is `unknown`, past its key's staleness budget or older than the key's registration is Not
measured, and Not measured is inert: it neither opens, keeps, recovers nor reminds. Stdlib
plus a caller-passed psycopg connection, so the API and the scripts import it with base
dependencies only.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Six hours reaches a missed onset the same day; 24 h and 72 h repeat it before it is forgotten.
ALARM_RUNGS_H = (6.0, 24.0, 72.0)
# Past the fixed rungs an unfixed alarm keeps saying so weekly, too rarely to become wallpaper.
ALARM_WEEKLY_H = 168.0
# The operator's ruling of 2026-10-18: a gauge rings when it first reads Problem, then once a day.
GAUGE_RUNG_H = 24.0
# Recovery needs a second OK measured at least max(2 cadences, this floor) after the first, so
# neither a lone green run nor two quick greens of a fast check close an incident.
RECOVERY_HOLD_MIN_S = 1800
# A cell nobody has measured for a week can never close on a measured OK, so it closes silently.
UNMEASURED_CLOSE_DAYS = 7
# The signature namespace of cell incidents; the retiring signature engine never produces it.
CELL_PREFIX = "cell:"
# Room for every failing cell's reason, yet one readable bell entry and one Telegram message.
MESSAGE_MAX_CHARS = 1500

_RANK = {"ok": 0, "warn": 1, "fail": 2}

_LOCK_SQL = "SELECT pg_try_advisory_xact_lock(hashtext('health_reconcile'))"

# Only rows since the key's registration count: the harness before it stored a check's own
# error as fail and no message, so a key is Not measured until the new harness measures it.
# The budget is read as seconds: psycopg parses interval text only under the default IntervalStyle.
_NEWEST_SQL = """
SELECT hc.key, hc.class, hc.cadence_s, hc.label, hc.unit,
       extract(epoch FROM hc.staleness_budget)::float8,
       latest.status, latest.run_at, latest.value,
       latest.details -> 'cells', latest.details ->> 'message'
  FROM health_checks hc
  LEFT JOIN LATERAL (
        SELECT c.status, c.run_at, c.value, c.details
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

_MEASURED_SINCE_SQL = """
SELECT check_key, run_at, status, details -> 'cells'
  FROM pipeline_check_results
 WHERE check_key = ANY(%(keys)s) AND status <> 'unknown' AND run_at >= %(since)s
"""


def _system_health_channels(conn: Any) -> list[str]:
    """Operator-chosen external channels for system alerts (default [] = in-app only)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT value FROM app_settings WHERE key = 'system_health_channels'"
        )
        row = cur.fetchone()
    raw = row[0] if row and row[0] is not None else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, str) and c]


def emit_system_alert(conn: Any, check_key: str, message: str, *, dedupe_key: str) -> bool:
    """Insert one system_health dispatch for `check_key`; True when it landed (repeats no-op)."""
    if not dedupe_key.startswith(f"sys:{check_key}:"):
        raise ValueError(f"dedupe key {dedupe_key!r} is outside sys:{check_key}:")
    channels = _system_health_channels(conn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_dispatches "
            "  (source_kind, change_kind, channel, status, message, dedupe_key, target_channels) "
            "VALUES ('system_health', 'system_alert', 'in_app', 'sent', %s, %s, %s::text[]) "
            "ON CONFLICT (dedupe_key) DO NOTHING",
            (message, dedupe_key, channels),
        )
        return (cur.rowcount or 0) > 0


def _iso(run_at: _dt.datetime) -> str:
    """Stable second-resolution UTC stamp for incident-anchored dedupe keys."""
    return run_at.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    label: str
    run_at: _dt.datetime | None
    cells: dict[str, dict[str, Any]]


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


def _hold(cadence_s: int) -> _dt.timedelta:
    """How long after a cell's first OK a later measured OK must come to recover its incident."""
    return _dt.timedelta(seconds=max(2 * cadence_s, RECOVERY_HOLD_MIN_S))


def _unmeasured_window(cadence_s: int) -> _dt.timedelta:
    """How long a cell may go unmeasured before its incident closes; weekly checks get two weeks."""
    return _dt.timedelta(seconds=max(UNMEASURED_CLOSE_DAYS * 86400, 2 * cadence_s))


def _stale(stamp: _dt.datetime, budget_s: float, at: _dt.datetime) -> bool:
    """`health_stale` (migration 619) on this pass's clock: older than the key's budget."""
    return stamp < at - _dt.timedelta(seconds=float(budget_s))


def _cells(key: str, status: str, value: Any, details: Any, unit: str) -> dict[str, dict[str, Any]]:
    """A measured result's cells by subject: its own, else one implicit cell named by the key."""
    details = details if isinstance(details, dict) else {}
    raw = details.get("cells")
    if not isinstance(raw, list):
        raw = []
    implicit = {"subject": key, "portal": None, "status": status, "value": value, "unit": unit,
                "message": details.get("message") or "", "facts": {}}
    cells: dict[str, dict[str, Any]] = {}
    for cell in raw:
        if not isinstance(cell, dict) or cell.get("status") not in _RANK:
            continue
        subject = str(cell.get("subject") or "")
        held = cells.get(subject)
        if subject and (held is None or _RANK[cell["status"]] > _RANK[held["status"]]):
            cells[subject] = cell
    # A list under `cells` that holds no verdict (a legacy per-portal table, a payload shape of
    # its own) is not a cell list: the check still measured, so its own status is the cell.
    return cells or {key: implicit}


def _check(row: tuple[Any, ...], at: _dt.datetime) -> _Check:
    """A registry row with its newest result; only a measured result inside its budget has cells."""
    key, cls, cadence_s, label, unit, budget_s, status, run_at, value, raw_cells, message = row
    run_at, cadence = _opt_utc(run_at), int(cadence_s)
    # pipeline_checks_public's effective_status: missing, unknown or stale is Not measured.
    measured = (status in _RANK and run_at is not None
                and not _stale(run_at, budget_s, at))
    details = {"cells": raw_cells, "message": message}
    cells = _cells(key, status, value, details, unit or "") if measured else {}
    return _Check(key, cls, cadence, label or key, run_at, cells)


def _incident(row: tuple[Any, ...]) -> _Incident:
    """An open cell incident row; its key is the signature's first field (keys hold no colon)."""
    inc_id, signature, first_seen, last_seen, _count, alerted, ok_since, acked = row
    key, _, subject = signature[len(CELL_PREFIX):].partition(":")
    return _Incident(int(inc_id), key, subject, _as_utc(first_seen), _as_utc(last_seen),
                     _opt_utc(alerted), _opt_utc(ok_since), acked is not None)


def _rung(cls: str, elapsed_h: float) -> tuple[str, float] | None:
    """The highest reminder rung an incident open `elapsed_h` hours has reached: (label, hours)."""
    if cls == "gauge":
        days = int(elapsed_h // GAUGE_RUNG_H)
        return (f"d{days}", days * GAUGE_RUNG_H) if days >= 1 else None
    weeks = int(elapsed_h // ALARM_WEEKLY_H)
    if weeks >= 1:
        return f"w{weeks + 1}", weeks * ALARM_WEEKLY_H  # the 168 h rung opens week two
    reached = [h for h in ALARM_RUNGS_H if h <= elapsed_h]
    return (f"{max(reached):g}h", max(reached)) if reached else None


def _clip(text: str) -> str:
    """Cap a notice at MESSAGE_MAX_CHARS."""
    return text if len(text) <= MESSAGE_MAX_CHARS else text[: MESSAGE_MAX_CHARS - 1] + "…"


def _named(key: str, subjects: Iterable[str]) -> str:
    """The subjects a notice names; the implicit cell is the check itself and needs no name."""
    return ", ".join(sorted({s for s in subjects if s != key}))


def _anchor(incidents: list[_Incident]) -> str:
    """A grouped notice's dedupe tail: `{oldest onset}:{lowest id}x{count}`."""
    # An incident joins one notice per rung and one recovery, so its lowest id keeps two groups
    # of one check apart when their cells are told in different passes.
    oldest = min(i.first_seen_at for i in incidents)
    return f"{_iso(oldest)}:{min(i.id for i in incidents)}x{len(incidents)}"


def _onset(check: _Check, opened: list[tuple[_Incident, dict[str, Any]]]) -> str:
    """`{label}: Problem — {subject}: {cell message}; …` for the cells this pass opened."""
    parts = []
    for inc, cell in opened:
        text = str(cell.get("message") or "").strip()
        if inc.subject != check.key:
            text = f"{inc.subject}: {text}" if text else inc.subject
        if text:
            parts.append(text)
    return f"{check.label}: Problem" + (f" — {'; '.join(parts)}" if parts else "")


def _reminder(
    check: _Check, incidents: list[_Incident], current: dict[int, str], at: _dt.datetime,
) -> tuple[str, str, list[_Incident]] | None:
    """The one reminder this check owes this pass (message, dedupe key, incidents), or None."""
    due: list[tuple[_Incident, str]] = []
    for inc in incidents:
        if inc.id not in current or inc.acknowledged:
            continue
        rung = _rung(check.cls, (at - inc.first_seen_at).total_seconds() / 3600)
        if rung and (inc.alerted_at is None
                     or inc.alerted_at < inc.first_seen_at + _dt.timedelta(hours=rung[1])):
            due.append((inc, rung[0]))
    if not due:
        return None
    oldest, rung_label = min(due, key=lambda d: d[0].first_seen_at)
    told = [inc for inc, _ in due]
    hours = (at - oldest.first_seen_at).total_seconds() / 3600
    state = "Problem" if any(current[inc.id] == "fail" for inc in told) else "Watch"
    names = _named(check.key, (inc.subject for inc in told))
    message = f"{check.label}: still {state} for {hours:.0f} h" + (f" — {names}" if names else "")
    return message, f"sys:{check.key}:reminder:{rung_label}:{_anchor(told)}", told


def _recovery(check: _Check, incidents: list[_Incident]) -> tuple[str, str]:
    """The notice for the incidents a measured OK closed this pass: (message, dedupe key)."""
    oldest = min(incidents, key=lambda i: i.first_seen_at)
    ok_since = oldest.ok_since or oldest.first_seen_at
    hours = (ok_since - oldest.first_seen_at).total_seconds() / 3600
    names = _named(check.key, (i.subject for i in incidents))
    message = f"✓ {check.label}: {names + ' ' if names else ''}recovered after {hours:.0f} h"
    return message, f"sys:{check.key}:recovery:{_anchor(incidents)}"


def _measured_subjects(
    cur: Any, checks: dict[str, _Check], keys: set[str], at: _dt.datetime,
) -> dict[str, set[str]]:
    """The subjects each key measured inside its unmeasured window."""
    since = {k: at - _unmeasured_window(checks[k].cadence_s) for k in keys}
    cur.execute(_MEASURED_SINCE_SQL, {"keys": sorted(keys), "since": min(since.values())})
    seen: dict[str, set[str]] = {}
    for key, run_at, status, cells in cur.fetchall():
        if _as_utc(run_at) >= since[key]:
            seen.setdefault(key, set()).update(_cells(key, status, None, {"cells": cells}, ""))
    return seen


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

        opened: dict[str, list[tuple[_Incident, dict[str, Any]]]] = {}
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
                            new = _Incident(int(row[0]), check.key, subject, run_at, run_at,
                                            None, None, False)
                            opened.setdefault(check.key, []).append((new, cell))
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
        retired = [i for i in still_open if i.key not in checks]
        dark = [i for i in still_open if i.key in checks
                and i.last_seen_at < at - _unmeasured_window(checks[i.key].cadence_s)]
        if dark:
            seen = _measured_subjects(cur, checks, {i.key for i in dark}, at)
            dark = [i for i in dark if i.subject not in seen.get(i.key, set())]
        unmeasured = f"unmeasured {UNMEASURED_CLOSE_DAYS}d"
        for reason, group in (("retired", retired), (unmeasured, dark)):
            if group:
                cur.execute(_RESOLVE_SQL, {"now": at, "reason": reason,
                                           "ids": sorted(i.id for i in group)})
        counts["closed_silently"] = len(retired) + len(dark)

        by_key: dict[str, list[_Incident]] = {}
        for inc in incidents:
            by_key.setdefault(inc.key, []).append(inc)
        for key, check in checks.items():
            if key in opened and check.run_at is not None:
                counts["notices"] += int(emit_system_alert(
                    conn, key, _clip(_onset(check, opened[key])),
                    dedupe_key=f"sys:{key}:onset:{_iso(check.run_at)}"))
                cur.execute(_STAMP_SQL, {"now": at, "ids": [i.id for i, _ in opened[key]]})
            owed = _reminder(check, by_key.get(key, []), current, at)
            if owed:
                message, dedupe, reminded = owed
                counts["notices"] += int(emit_system_alert(conn, key, _clip(message),
                                                           dedupe_key=dedupe))
                cur.execute(_STAMP_SQL, {"now": at, "ids": [i.id for i in reminded]})
                counts["reminded"] += len(reminded)
            if key in recovered:
                message, dedupe = _recovery(check, recovered[key])
                counts["notices"] += int(emit_system_alert(conn, key, _clip(message),
                                                           dedupe_key=dedupe))
    return counts

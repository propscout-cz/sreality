"""The result shape every check module shares: cells, a judged cell, the verdict, the clock.

A check returns `result(key, cells, not_measured)`: one cell per portal, job, lane or unit,
each `{subject, portal, status, value, unit, message, facts}`; the verdict is the worst
measured cell, and a check that could measure nothing is `unknown` with `details.skipped`.
Moved verbatim out of platform.py (wave 4), so no module re-spells the shape. A gauge's cells
also carry their trend (`trend`, wave 6): a rolling series rebuilt from the check's own results.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any
from zoneinfo import ZoneInfo

from scripts.checks.clock import PASS, fetchall, fetchone

_UTC = _dt.timezone.utc
_RANK = {"ok": 0, "warn": 1, "fail": 2}
# The failing cells a check's own message names before it counts the rest.
_NAMED = 5

CLOCK_SQL = "select now()"
# scraper/realtime_worker.WORKER_NAME, the heartbeat's key (a check never imports the worker).
WORKER = "realtime-worker"
# Migration 620's c_worker_dead: past it the watcher's `worker` cell judges a dead worker, and
# lanes read from an older beat would ring the same outage a second time.
BEAT_FRESH_S = 600.0
# An ad is new for its first NEW_AD_H hours: publishing judges the new ads, the location gauge the
# ads past them, so the edge between the two checks is this one number.
NEW_AD_H = 24

# A check's own newest stored cells: what a rule that compares with its previous measurement
# reads (the jobs lanes' counters and failing portals, the Engine's open decision lag).
PREVIOUS_CELLS_SQL = """
select run_at, details -> 'cells' from pipeline_check_results
 where check_key = %(key)s and jsonb_typeof(details -> 'cells') = 'array'
 order by run_at desc
 limit 1
"""

# A gauge cell's trend: one [day, value, status] point per Europe/Prague day over the last
# TREND_DAYS days, and whether its status is worse than the point dated WORSE_DAYS earlier.
TREND_DAYS = 30
WORSE_DAYS = 7
_PRAGUE = ZoneInfo("Europe/Prague")

# The gauge's newest MEASURED result: each cell carries its series on from it, so a run that
# measured nothing (status unknown) never resets one.
TREND_SQL = """
select details -> 'cells' from pipeline_check_results
 where check_key = %(key)s and status <> 'unknown'
   and jsonb_typeof(details -> 'cells') = 'array'
 order by run_at desc
 limit 1
"""

# The subjects that result lacks (an arm unread then, a unit back after a gap): for each, the
# newest series a measured result of the window holds. Sent at most once per check, on a miss.
TREND_LOOKBACK_SQL = """
select distinct on (c ->> 'subject') c ->> 'subject' as subject, c -> 'facts' -> 'trend' as trend
  from pipeline_check_results r
 cross join lateral jsonb_array_elements(
       case when jsonb_typeof(r.details -> 'cells') = 'array'
            then r.details -> 'cells' else '[]'::jsonb end) c
 where r.check_key = %(key)s and r.status <> 'unknown'
   and r.run_at >= %(since)s
   and jsonb_typeof(c -> 'facts' -> 'trend') = 'array'
   and not (c ->> 'subject' = any(%(known)s::text[]))
 order by c ->> 'subject', r.run_at desc
"""


def now(conn: Any) -> _dt.datetime:
    """The database's clock, the one every window in these checks' SQL is measured on."""
    row = fetchone(conn, CLOCK_SQL)
    return as_utc(row[0])


def as_utc(value: Any) -> _dt.datetime | None:
    """A stored timestamp, or the worker's ISO text, as an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = _dt.datetime.fromisoformat(value)
    return value.astimezone(_UTC) if value.tzinfo else value.replace(tzinfo=_UTC)


def iso(at: _dt.datetime | None) -> str | None:
    return None if at is None else at.astimezone(_UTC).isoformat()


def num(value: Any) -> float | None:
    return None if value is None else float(value)


def ago(seconds: float) -> str:
    """A duration the way the page says it."""
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def previous_result(conn: Any,
                    check_key: str) -> tuple[_dt.datetime | None, dict[str, dict[str, Any]]]:
    """When this check last stored a result, and its cells by subject; (None, {}) if none."""
    row = fetchone(conn, PREVIOUS_CELLS_SQL, {"key": check_key})
    if row is None:
        return None, {}
    cells = row[1] if isinstance(row[1], list) else []
    return as_utc(row[0]), {c.get("subject"): c for c in cells if isinstance(c, dict)}


def previous_cells(conn: Any, check_key: str) -> dict[str, dict[str, Any]]:
    """This check's previous measurement, cell by subject."""
    return previous_result(conn, check_key)[1]


def oneline(text: Any, limit: int = 200) -> str:
    """An error as one bounded line (pg_cron's messages carry a CONTEXT block)."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def graded(value: float, warn: float, fail: float) -> str:
    """Problem above `fail`, Watch above `warn`, else OK."""
    return "fail" if value > fail else "warn" if value > warn else "ok"


def cell(subject: str, status: str, message: str, *, value: Any = None, unit: str = "",
         portal: str | None = None, facts: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"subject": subject, "portal": portal, "status": status, "value": value,
            "unit": unit, "message": message, "facts": facts or {}}


def judged(subject: str, conditions: list[tuple[str, str]], ok_message: str,
           **cell_fields: Any) -> dict[str, Any]:
    """One cell carrying the worst of its conditions, every condition's reason in its facts."""
    if not conditions:
        return cell(subject, "ok", ok_message, **cell_fields)
    status = max((level for level, _ in conditions), key=_RANK.__getitem__)
    cell_fields["facts"] = {**cell_fields.get("facts", {}),
                            "conditions": [f"{level}: {why}" for level, why in conditions]}
    return cell(subject, status, "; ".join(why for level, why in conditions if level == status),
                **cell_fields)


def result(key: str, cells: list[dict[str, Any]], not_measured: dict[str, str]) -> dict[str, Any]:
    """A check's verdict: the worst measured cell; every cell and every unread arm kept."""
    details: dict[str, Any] = {"cells": cells}
    if not_measured:
        details["not_measured"] = not_measured
    unread = "; ".join(f"{arm}: {why}" for arm, why in not_measured.items())
    measured = [c for c in cells if c["status"] in _RANK]
    if not measured:
        details["skipped"] = unread or "nothing to judge"
        return {"check_key": key, "status": "unknown", "value": None, "details": details,
                "message": f"Not measured: {details['skipped']}."}
    status = max((c["status"] for c in measured), key=_RANK.__getitem__)
    parts = []
    for level, word in (("fail", "Problem"), ("warn", "Watch")):
        named = [c for c in measured if c["status"] == level]
        if named:
            shown = "; ".join(f"{c['subject']}: {oneline(c['message'], 160)}"
                              for c in named[:_NAMED])
            more = f"; {len(named) - _NAMED} more" if len(named) > _NAMED else ""
            parts.append(f"{len(named)} {word} — {shown}{more}")
    parts.append(f"{sum(c['status'] == 'ok' for c in measured)} OK")
    if len(cells) > len(measured):
        parts.append(f"{len(cells) - len(measured)} Not measured")
    if unread:
        parts.append(f"Not read: {unread}")
    value = sum(c["status"] == status for c in measured) if status != "ok" else 0
    return {"check_key": key, "status": status, "value": value, "details": details,
            "message": ". ".join(parts) + "."}


class _Series:
    """A gauge's stored series by subject, read once per check of a pass (clock.PASS)."""

    def __init__(self, conn: Any, check_key: str, at: _dt.datetime) -> None:
        self._conn, self._key, self._since = conn, check_key, at - _dt.timedelta(days=TREND_DAYS)
        row = fetchone(conn, TREND_SQL, {"key": check_key})
        cells = row[0] if row is not None and isinstance(row[0], list) else []
        self._held = {c["subject"]: _stored_series(c.get("facts")) for c in cells
                      if isinstance(c, dict) and isinstance(c.get("subject"), str)}
        # With no measured result at all there is nothing older to look back through.
        self._older: dict[str, list[Any]] | None = None if row is not None else {}

    def get(self, subject: str) -> list[Any]:
        if subject in self._held:
            return self._held[subject]
        if self._older is None:
            rows = fetchall(self._conn, TREND_LOOKBACK_SQL, {
                "key": self._key, "since": self._since, "known": sorted(self._held)})
            self._older = {s: t for s, t in rows if isinstance(s, str) and isinstance(t, list)}
        return self._older.get(subject, [])


def _stored_series(facts: Any) -> list[Any]:
    series = facts.get("trend") if isinstance(facts, dict) else None
    return series if isinstance(series, list) else []


def _point_day(point: Any) -> _dt.date | None:
    """A stored point's day, or None for anything that is not [day, value, measured status]."""
    if not (isinstance(point, list) and len(point) == 3 and isinstance(point[0], str)
            and isinstance(point[2], str) and point[2] in _RANK):
        return None
    try:
        return _dt.date.fromisoformat(point[0])
    except ValueError:
        return None


def trend(conn: Any, check_key: str, subject: str, value: Any, status: str,
          at: _dt.datetime) -> dict[str, Any]:
    """A gauge cell's `trend` ([[Prague day, value, status], …] over TREND_DAYS days, today's
    point replacing the day's earlier one; an unmeasured cell carries its series unchanged) and
    `worse_7d` (today's status worse than the point dated WORSE_DAYS back, None without one)."""
    held = PASS.memo(f"trend:{check_key}", lambda: _Series(conn, check_key, at)).get(subject)
    today = at.astimezone(_PRAGUE).date()
    first = today - _dt.timedelta(days=TREND_DAYS - 1)
    points: dict[_dt.date, list[Any]] = {}
    for point in held:
        day = _point_day(point)
        if day is not None and first <= day <= today:
            points[day] = list(point)
    if status in _RANK:
        points[today] = [today.isoformat(), value, status]
    week = points.get(today - _dt.timedelta(days=WORSE_DAYS))
    worse = None if status not in _RANK or week is None else _RANK[status] > _RANK[week[2]]
    return {"trend": [points[day] for day in sorted(points)], "worse_7d": worse}


def with_trends(conn: Any, check_key: str, cells: list[dict[str, Any]],
                at: _dt.datetime) -> None:
    """Every cell of a gauge carries its trend; alarms carry none."""
    for c in cells:
        c["facts"].update(trend(conn, check_key, c["subject"], c["value"], c["status"], at))

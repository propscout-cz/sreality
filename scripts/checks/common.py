"""The result shape every check module shares: cells, a judged cell, the verdict, the clock.

A check returns `result(key, cells, not_measured)`: one cell per portal, job, lane or unit,
each `{subject, portal, status, value, unit, message, facts}`; the verdict is the worst
measured cell, and a check that could measure nothing is `unknown` with `details.skipped`.
Moved verbatim out of platform.py (wave 4), so no module re-spells the shape.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any

from scripts.checks.clock import fetchone

_UTC = _dt.timezone.utc
_RANK = {"ok": 0, "warn": 1, "fail": 2}
# The failing cells a check's own message names before it counts the rest.
_NAMED = 5

CLOCK_SQL = "select now()"


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


def oneline(text: Any, limit: int = 200) -> str:
    """An error as one bounded line (pg_cron's messages carry a CONTEXT block)."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def graded(value: float, warn: float, fail: float) -> str:
    """Problem above `fail`, Watch above `warn`, else OK."""
    return "fail" if value > fail else "warn" if value > warn else "ok"


def wilson_lower(hits: int, n: int, z: float = 1.96) -> float | None:
    """The Wilson score interval's lower bound of hits / n; None when nothing was counted."""
    if n == 0:
        return None
    p, z2 = hits / n, z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z2 / n))


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

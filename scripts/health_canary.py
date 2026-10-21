"""health_canary.py — the GitHub canary: are the watchers and the teller alive?

`python -m scripts.health_canary`, the last step of the hourly verify_pipeline.yml and the one
alarm path that needs neither pg_cron nor Railway (only SUPABASE_DB_URL). Four assertions, one
bounded read each: `db_answers`; `liveness_fresh` (pg_cron's watcher wrote a result in the last
30 min); `failing_cells_have_incidents` (a liveness cell failing for 15 min has an open incident,
else the API's teller is not running); `alarms_acknowledged` (no open, unacknowledged incident of
an alarm check had its latest rung, the onset or a reminder the teller stamps as `alerted_at`,
15 min to 12 h ago; a gauge's rungs show on Health only). A failure prints `CANARY FAIL <name>:
<reason>` as a GitHub error annotation and reds the run, so GitHub emails the operator: exit 2
when the database does not answer, 1 when an assertion fails. It reads no notification: system
alerts left the bell (ruling R2, notifications W1), so acknowledging on Health is the one answer.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from scraper.db import connect
from toolkit.system_alerts import CELL_PREFIX, _cells

LIVENESS_STALE_MIN = 30  # the watcher writes every 5 min: six missed ticks, never one late tick
TELLER_GRACE_MIN = 15  # the teller passes every 60 s; 15 min of failing outlasts an API deploy
NOTICE_WINDOW_H = 12  # a rung is asserted for 12 h; the alarm's next reminder re-arms it
NOTICE_GRACE_MIN = 15  # a rung this young is not asserted yet: room to acknowledge it first
STATEMENT_TIMEOUT = "10s"  # Postgres cancels a read past this: a wedged DB does not answer

_LIVENESS_SQL = """
select (select max(run_at) from pipeline_check_results where check_key = 'liveness'),
       (select declared_at from health_checks where key = 'liveness')
"""

# The open liveness incidents ride along as one uncorrelated subquery, evaluated once.
_CELLS_SQL = """
select r.run_at, r.status, r.details -> 'cells',
       array(select i.signature from ops_incidents i
              where i.resolved_at is null and starts_with(i.signature, 'cell:liveness:'))
  from pipeline_check_results r
 where r.check_key = 'liveness' and r.run_at > %(since)s
 order by r.run_at desc
"""

# The latest rung of every open, unacknowledged alarm incident told inside the window
# (toolkit.system_alerts stamps alerted_at at the onset and at each reminder). starts_with, not
# LIKE: every check key holds `_`.
_RUNGS_SQL = """
select hc.key, i.signature, i.alerted_at
  from ops_incidents i
  join health_checks hc on starts_with(i.signature, 'cell:' || hc.key || ':')
 where i.resolved_at is null and i.acknowledged_at is null
   and hc.class = 'alarm' and i.alerted_at > %(since)s
 order by i.alerted_at, i.signature
"""


def _age(delta: timedelta) -> str:
    """A duration as the operator reads it: minutes up to two hours, then hours."""
    minutes = delta.total_seconds() / 60
    return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} h"


def liveness_fresh(newest: datetime | None, declared_at: datetime | None,
                   now: datetime) -> list[str]:
    """The watcher's newest row, or its later registration, is under LIVENESS_STALE_MIN old."""
    stamp = max((s for s in (newest, declared_at) if s is not None), default=None)
    if stamp is None or now - stamp < timedelta(minutes=LIVENESS_STALE_MIN):
        return []
    if newest is None:
        return [f"pg_cron has never written a liveness result (registered {_age(now - stamp)} ago)"]
    return [f"the newest liveness result is {_age(now - newest)} old (limit {LIVENESS_STALE_MIN} "
            "min): pg_cron's watcher has stopped"]


def failing_cells_have_incidents(rows: Sequence[tuple[datetime, str, Any]],
                                 incidents: Collection[str], now: datetime) -> list[str]:
    """Every cell failing in each liveness row since TELLER_GRACE_MIN ago has an open incident."""
    # Each row's failing subjects, cut by the teller's own cell rule (the worst cell per subject).
    failing = [(run_at, {s for s, c in _cells(cells).items() if c["status"] == "fail"})
               for run_at, _status, cells in rows]
    reasons = []
    for subject in sorted(failing[0][1] if failing else ()):
        since = failing[0][0]
        for run_at, subjects in failing:  # newest first: walk back while the cell keeps failing
            if subject not in subjects:
                break
            since = run_at
        if (now - since >= timedelta(minutes=TELLER_GRACE_MIN)
                and f"{CELL_PREFIX}liveness:{subject}" not in incidents):
            reasons.append(f"liveness cell {subject} has failed for at least {_age(now - since)} "
                           "with no open incident: the API's teller is not running")
    return reasons


def alarms_acknowledged(rungs: Iterable[Sequence[Any]], now: datetime) -> list[str]:
    """No alarm rung of the window, past its grace, sits on an open, unacknowledged incident;
    one reason per check and rung (a rung stamps all the cells it tells at once)."""
    told: dict[tuple[str, datetime], list[str]] = {}
    for key, signature, alerted_at in rungs:
        if timedelta(minutes=NOTICE_GRACE_MIN) <= now - alerted_at <= timedelta(hours=NOTICE_WINDOW_H):
            told.setdefault((key, alerted_at), []).append(
                signature.removeprefix(f"{CELL_PREFIX}{key}:"))
    return [f"the {key} alarm told {_age(now - at)} ago ({', '.join(sorted(subjects))}) is open "
            "and unacknowledged: acknowledge it on Health or fix it"
            for (key, at), subjects in told.items()]


def _read(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    """One bounded read in its own transaction; Postgres cancels it past STATEMENT_TIMEOUT."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> int:
    """Run the four assertions: exit 2 when the database does not answer, 1 on any failure."""
    now = datetime.now(timezone.utc)
    try:
        with connect(connect_timeout=10) as conn:
            _read(conn, "select 1")
            ((newest, declared_at),) = _read(conn, _LIVENESS_SQL)
            rows = _read(conn, _CELLS_SQL, {"since": now - timedelta(minutes=LIVENESS_STALE_MIN)})
            rungs = _read(conn, _RUNGS_SQL, {"since": now - timedelta(hours=NOTICE_WINDOW_H)})
    except (psycopg.Error, RuntimeError) as exc:  # RuntimeError: SUPABASE_DB_URL is not set
        print(f"::error::CANARY FAIL db_answers: {' '.join(str(exc).split()) or repr(exc)}")
        return 2
    if newest is None and declared_at is None:
        print("CANARY SKIP liveness_fresh: no liveness key yet (migration 620 is not applied)")
    failures = [("liveness_fresh", r) for r in liveness_fresh(newest, declared_at, now)]
    failures += [("failing_cells_have_incidents", r) for r in failing_cells_have_incidents(
        [row[:3] for row in rows], set(rows[0][3] or ()) if rows else set(), now)]
    failures += [("alarms_acknowledged", r) for r in alarms_acknowledged(rungs, now)]
    for name, reason in failures:  # an annotation each, listed on the run's summary page
        print(f"::error::CANARY FAIL {name}: {reason}")
    if not failures:
        print("CANARY OK: the database answers, the watchers are alive, every alarm acknowledged")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

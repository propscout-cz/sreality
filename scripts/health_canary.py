"""health_canary.py — the GitHub canary: are the watchers and the teller alive?

`python -m scripts.health_canary`, the last step of the hourly verify_pipeline.yml and the one
alarm path that needs neither pg_cron nor Railway (only SUPABASE_DB_URL). Four assertions, one
bounded read each: `db_answers`; `liveness_fresh` (pg_cron's watcher wrote a result in the last
30 min); `failing_cells_have_incidents` (a liveness cell failing for 15 min has an open incident,
else the API's teller is not running); `alarm_notices_handled` (an alarm's onset or reminder
system alert of the last 12 h was, 15 min on, read in the bell, delivered, or its key has no open
unacknowledged incident left; a gauge's system alerts ring in the bell only). A failure prints `CANARY
FAIL <name>: <reason>` as a GitHub error annotation and reds the run, so GitHub emails the
operator: exit 2 when the database does not answer, 1 when an assertion fails.
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
NOTICE_WINDOW_H = 12  # a notice is asserted for 12 h; the alarm's next reminder re-arms it
NOTICE_GRACE_MIN = 15  # the outbox sends every 120 s and retries once inside this
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

# starts_with, not LIKE: every check key holds `_`. notification_id is the indexed twin of the
# outbox's `notif:<dispatch>:<channel>` key; `delivered` is the Resend webhook's later `sent`.
_NOTICES_SQL = """
select d.dedupe_key, d.dispatched_at, d.seen_at is not null,
       exists (select 1 from channel_sends s
                where s.notification_id = d.id and s.status in ('sent', 'delivered')),
       hc.class,
       exists (select 1 from ops_incidents i
                where i.resolved_at is null and i.acknowledged_at is null
                  and starts_with(i.signature, 'cell:' || hc.key || ':'))
  from notification_dispatches d
  left join health_checks hc on hc.key = split_part(d.dedupe_key, ':', 2)
 where d.source_kind = 'system_health' and d.dispatched_at > %(since)s
 order by d.dispatched_at
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
    failing = [(run_at, {s for s, c in _cells("liveness", status, None, {"cells": cells}, "")
                         .items() if c["status"] == "fail"}) for run_at, status, cells in rows]
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


def alarm_notices_handled(notices: Iterable[Sequence[Any]], now: datetime) -> list[str]:
    """Every alarm onset or reminder of the window, past its grace, was seen, sent or answered."""
    reasons = []
    for dedupe_key, dispatched_at, seen, delivered, cls, open_unacked in notices:
        _, key, kind, *_ = (dedupe_key or "").split(":") + ["", "", ""]
        age = now - dispatched_at
        if (cls == "alarm" and kind in ("onset", "reminder")
                and timedelta(minutes=NOTICE_GRACE_MIN) <= age <= timedelta(hours=NOTICE_WINDOW_H)
                and not seen and not delivered and open_unacked):
            reasons.append(f"the {key} {kind} system alert sent {_age(age)} ago was neither read "
                           "in the bell nor delivered, and its incident is open and unacknowledged")
    return reasons


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
            notices = _read(conn, _NOTICES_SQL, {"since": now - timedelta(hours=NOTICE_WINDOW_H)})
    except (psycopg.Error, RuntimeError) as exc:  # RuntimeError: SUPABASE_DB_URL is not set
        print(f"::error::CANARY FAIL db_answers: {' '.join(str(exc).split()) or repr(exc)}")
        return 2
    if newest is None and declared_at is None:
        print("CANARY SKIP liveness_fresh: no liveness key yet (migration 620 is not applied)")
    failures = [("liveness_fresh", r) for r in liveness_fresh(newest, declared_at, now)]
    failures += [("failing_cells_have_incidents", r) for r in failing_cells_have_incidents(
        [row[:3] for row in rows], set(rows[0][3] or ()) if rows else set(), now)]
    failures += [("alarm_notices_handled", r) for r in alarm_notices_handled(notices, now)]
    for name, reason in failures:  # an annotation each, listed on the run's summary page
        print(f"::error::CANARY FAIL {name}: {reason}")
    if not failures:
        print("CANARY OK: the database answers, the watchers are alive, every alarm's system alert handled")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

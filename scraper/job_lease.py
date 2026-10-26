"""Lease-row CAS on `scheduled_jobs` (migration 675; `location_jobs` until then).

**Never a session advisory lock.** Every service-role path uses the transaction-mode pooler,
where a lock taken on one backend and released on another silently strands. The row is the
lock: a claim is a conditional UPDATE, a crashed holder's lease simply expires.

Two shapes over one row:

* `claim` / `renew` / `release` — a scheduled job, driven by the job runner (`scraper.job_runner`).
  A claim takes a DUE row (its `due_at` passed and it is not paused, or a request is waiting: a
  request is a person asking, a pause only stops the schedule), never a retired one, never while a
  sibling sharing its `resource` holds a live lease, and opens one `job_runs` row; the database's
  unique indexes allow one open run per job and per resource, so a lost race is a refused claim.
  The holder renews and fences itself (`Lease.fenced`, on the shared `clock`) before the lease can
  expire under it. The release closes the run and moves `due_at` by a due kind (`DUE_KINDS`): the
  next slot of the epoch-anchored `cadence` grid, now (an interrupted run), a minute (exit 75), a
  backoff doubling from a minute and capped at the cadence and the next slot, a retry floored at the
  job's own `timeout_s` (a timeout), or the job's own hour (a DB-heavy job's retries); a pure request
  run never moves the schedule. A cancelled, interrupted or skipped run is not a failure.
  The runner's boot `register`s its registry (adopting a lane-era row, skipping one the database
  refuses) and `retire`s only rows it registered; `due` lists free rows with no open run, oldest
  first; `reap_lost` closes a run whose lease lapsed as `lost` (a failure), re-run at once, then
  backed off on a second loss in a row. Every runner-facing call is one transaction under
  `STATEMENT_TIMEOUT_S` (`_bounded`).
* `held` — a leased lane with no schedule (the location resolver drain, run every few
  seconds by the realtime worker and by `location_resolve.yml`): no due time and no run
  history, just mutual exclusion and the last outcome.
"""
from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from scraper.jobs import Job

LOG = logging.getLogger("scraper.job_lease")

# The job runner's spelling of `scheduled_jobs.runner` (== jobs.RUNNER, pinned by a test).
RUNNER = "job-runner"
# The holder's fence and the runner's guard read this at call time, so a test patches one clock.
clock: Callable[[], float] = time.monotonic

TTL_S = 180
# The holder stops its work this long before the database's expiry, so nothing runs on a lapsed lease.
FENCE_MARGIN_S = 30

RELEASE_OUTCOMES = frozenset({"ok", "failed", "cancelled", "skipped", "timeout", "interrupted"})
NOT_FAILURES = frozenset({"cancelled", "skipped", "interrupted"})
# Where a released run's next due time goes (`_RELEASE_RUN_SQL`).
DUE_KINDS = frozenset({"slot", "now", "more", "backoff", "retry", "own_hour"})
MORE_REMAINS_S = 60
BACKOFF_BASE_S = 60
# Every runner-facing statement runs under it; it also bounds lock waits, so there is no lock timeout.
STATEMENT_TIMEOUT_S = 5

_FREE = "(j.lease_holder IS NULL OR j.lease_expires_at IS NULL OR j.lease_expires_at < now())"
_UNPAUSED = "(j.paused_until IS NULL OR j.paused_until <= now())"

# The next slot strictly after now() on the grid anchored at the Unix epoch: missed slots are skipped.
_NEXT_SLOT_SQL = (
    "to_timestamp((floor((extract(epoch from now()) - coalesce(j.offset_s, 0))"
    " / extract(epoch from j.cadence)) + 1) * extract(epoch from j.cadence) + coalesce(j.offset_s, 0))"
)

_CLAIM_SQL = f"""
UPDATE scheduled_jobs j
   SET lease_holder = %(holder)s,
       lease_expires_at = now() + make_interval(secs => %(ttl)s),
       last_started_at = now(),
       last_outcome = 'running'
 WHERE j.job_name = %(job)s
   AND j.retired_at IS NULL
   AND ((j.due_at <= now() AND {_UNPAUSED}) OR j.requested_at <= now())
   AND {_FREE}
   AND (j.resource IS NULL OR NOT EXISTS (
         SELECT 1 FROM scheduled_jobs s
          WHERE s.resource = j.resource AND s.job_name <> j.job_name
            AND s.lease_holder IS NOT NULL AND s.lease_expires_at >= now()))
RETURNING j.resource, coalesce(j.requested_at <= now(), false), j.requested_args, j.requested_by,
          coalesce(j.due_at <= now() AND {_UNPAUSED}, false)
"""

# The open run on this job lost its holder: the claim above took the row from a lapsed lease. A sibling's
# open run on the resource is lost only if ITS lease lapsed, read now: the claim's snapshot is older, and a
# sibling may have claimed and committed since (closing its live run would put two jobs on one resource).
# Each lost run counts as its job's failure; a sibling's row also gives up the lease it no longer holds.
_CLOSE_LOST_SQL = """
WITH lost AS (
  UPDATE job_runs r
     SET ended_at = now(), outcome = 'lost'
    FROM scheduled_jobs s
   WHERE s.job_name = r.job_name
     AND r.ended_at IS NULL
     AND (r.job_name = %(job)s
          OR (r.resource = %(resource)s
              AND NOT (s.lease_holder IS NOT DISTINCT FROM r.holder
                       AND coalesce(s.lease_expires_at >= now(), false))))
  RETURNING r.job_name
)
UPDATE scheduled_jobs j
   SET consecutive_failures = j.consecutive_failures + 1,
       last_outcome = CASE WHEN j.job_name = %(job)s THEN j.last_outcome ELSE 'lost' END,
       lease_holder = CASE WHEN j.job_name = %(job)s THEN j.lease_holder END,
       lease_expires_at = CASE WHEN j.job_name = %(job)s THEN j.lease_expires_at END
  FROM lost
 WHERE j.job_name = lost.job_name
"""

_OPEN_RUN_SQL = """
INSERT INTO job_runs (job_name, holder, version, resource, args, requested_by)
VALUES (%(job)s, %(holder)s, %(version)s, %(resource)s, %(args)s, %(requested_by)s)
RETURNING id
"""

_TAKE_REQUEST_SQL = """
UPDATE scheduled_jobs
   SET requested_at = NULL, requested_args = NULL, requested_by = NULL
 WHERE job_name = %(job)s
"""

_RENEW_SQL = """
UPDATE scheduled_jobs
   SET lease_expires_at = now() + make_interval(secs => %(ttl)s)
 WHERE job_name = %(job)s AND lease_holder = %(holder)s AND lease_expires_at >= now()
RETURNING 1
"""

_RELEASE_JOB_SQL = f"""
UPDATE scheduled_jobs j
   SET lease_holder = NULL,
       lease_expires_at = NULL,
       last_outcome = %(outcome)s,
       last_error = %(error)s,
       last_success_at = CASE WHEN %(outcome)s = 'ok' THEN now() ELSE j.last_success_at END,
       consecutive_failures = CASE WHEN %(outcome)s = 'ok' THEN 0
                                   WHEN %(not_failure)s THEN j.consecutive_failures
                                   ELSE j.consecutive_failures + 1 END,
       due_at = CASE WHEN %(due_at)s::timestamptz IS NOT NULL THEN %(due_at)s::timestamptz
                     WHEN j.due_at IS NULL THEN NULL
                     WHEN %(outcome)s = 'interrupted' THEN now()
                     ELSE {_NEXT_SLOT_SQL} END
 WHERE j.job_name = %(job)s AND j.lease_holder = %(holder)s
RETURNING 1
"""

_CLOSE_RUN_SQL = """
UPDATE job_runs
   SET ended_at = now(), outcome = %(outcome)s, exit_code = %(exit_code)s,
       peak_rss_mb = %(peak_rss_mb)s, log_tail = %(log_tail)s
 WHERE id = %(run_id)s AND ended_at IS NULL
"""

_UPSERT_LANE_SQL = """
INSERT INTO scheduled_jobs (job_name, cadence, runner)
VALUES (%s, %s::interval, %s)
ON CONFLICT (job_name) DO NOTHING
"""

_ACQUIRE_LANE_SQL = f"""
UPDATE scheduled_jobs j
   SET lease_holder = %s,
       lease_expires_at = now() + make_interval(secs => %s),
       last_started_at = now(),
       last_outcome = 'running'
 WHERE j.job_name = %s
   AND j.retired_at IS NULL
   AND {_UNPAUSED}
   AND {_FREE}
RETURNING j.job_name
"""


# Each runner-facing call is one transaction under this bound. One statement, so the PREPARE sweep checks it.
_BOUND_SQL = f"SELECT set_config('statement_timeout', '{STATEMENT_TIMEOUT_S * 1000}', true)"

# The next slot on a whole-day grid at the job's own time of day: a DB-heavy job's retries stay in its hour.
_DAY_SLOT_SQL = (
    "to_timestamp((floor((extract(epoch from now()) - mod(coalesce(j.offset_s, 0), 86400)) / 86400) + 1)"
    " * 86400 + mod(coalesce(j.offset_s, 0), 86400))"
)
_BACKOFF_S = f"{BACKOFF_BASE_S} * 2 ^ least(j.consecutive_failures, 20)"
_BACKOFF_SQL = (
    f"least(now() + make_interval(secs => least({_BACKOFF_S}, extract(epoch from j.cadence))), {_NEXT_SLOT_SQL})"
)

# A pure request run (it took no due slot) never moves the schedule; an exit 75 only pulls it nearer. The SET
# expressions read the old row, so the first failure waits BACKOFF_BASE_S, then twice that, never past the cadence
# or the next slot; a timeout waits at least its own timeout_s.
_RELEASE_RUN_SQL = f"""
UPDATE scheduled_jobs j
   SET lease_holder = NULL,
       lease_expires_at = NULL,
       last_outcome = %(outcome)s,
       last_error = %(error)s,
       last_success_at = CASE WHEN %(outcome)s = 'ok' THEN now() ELSE j.last_success_at END,
       consecutive_failures = CASE WHEN %(outcome)s = 'ok' THEN 0
                                   WHEN %(not_failure)s THEN j.consecutive_failures
                                   ELSE j.consecutive_failures + 1 END,
       due_at = CASE WHEN j.due_at IS NULL THEN NULL
                     WHEN %(requested)s AND NOT %(due_slot)s THEN
                          CASE WHEN %(due)s = 'more'
                               THEN least(j.due_at, now() + make_interval(secs => {MORE_REMAINS_S}))
                               ELSE j.due_at END
                     WHEN %(due)s = 'now' THEN now()
                     WHEN %(due)s = 'more' THEN now() + make_interval(secs => {MORE_REMAINS_S})
                     WHEN %(due)s = 'backoff' THEN {_BACKOFF_SQL}
                     WHEN %(due)s = 'retry' THEN least(now() + make_interval(secs => least(
                          greatest(coalesce(j.timeout_s, {BACKOFF_BASE_S}), {_BACKOFF_S}),
                          extract(epoch from j.cadence))), {_NEXT_SLOT_SQL})
                     WHEN %(due)s = 'own_hour' THEN least({_DAY_SLOT_SQL}, {_NEXT_SLOT_SQL})
                     ELSE {_NEXT_SLOT_SQL} END
 WHERE j.job_name = %(job)s AND j.lease_holder = %(holder)s
RETURNING 1
"""

_REGISTERED_STATE_SQL = """
SELECT registry_at IS NULL, retired_at IS NOT NULL FROM scheduled_jobs WHERE job_name = %(job)s FOR UPDATE
"""

# The registry's columns only: never the lease, the history or `enabled` (dead since 675). `due_at` is set only for a
# new, adopted, un-retired or never-scheduled row, at the first slot no earlier than now() + timeout_s, so a move's
# first run comes after any GitHub run still in flight; a changed grid is never re-slotted.
_REGISTER_SQL = """
INSERT INTO scheduled_jobs AS s (job_name, argv, cadence, offset_s, timeout_s, memory_mb, cpus, resource, runner,
                                 registry_at, retired_at, due_at)
VALUES (%(job)s, %(argv)s::text[], make_interval(secs => %(every_s)s::integer), %(offset_s)s::integer,
        %(timeout_s)s::integer, %(memory_mb)s::integer, %(cpus)s::integer, %(resource)s::text, %(runner)s::text,
        now(), NULL,
        to_timestamp(ceil((extract(epoch from now()) + %(timeout_s)s::integer - %(offset_s)s::integer)
                          / %(every_s)s::integer) * %(every_s)s::integer + %(offset_s)s::integer))
ON CONFLICT (job_name) DO UPDATE
   SET argv = EXCLUDED.argv, cadence = EXCLUDED.cadence, offset_s = EXCLUDED.offset_s,
       timeout_s = EXCLUDED.timeout_s, memory_mb = EXCLUDED.memory_mb, cpus = EXCLUDED.cpus,
       resource = EXCLUDED.resource, runner = EXCLUDED.runner, registry_at = now(), retired_at = NULL,
       due_at = CASE WHEN s.retired_at IS NOT NULL OR s.registry_at IS NULL OR s.due_at IS NULL
                     THEN EXCLUDED.due_at ELSE s.due_at END
"""

# Only rows a runner registered: a lane `held()` created has registry_at NULL and is never retired.
_RETIRE_SQL = """
UPDATE scheduled_jobs
   SET retired_at = now()
 WHERE registry_at IS NOT NULL AND runner = %(runner)s AND retired_at IS NULL
   AND job_name <> ALL(%(names)s::text[])
RETURNING job_name
"""

# Open runs whose holder no longer holds a live lease, on every row this runner owns (paused, not due and retired
# included). SKIP LOCKED: a claim locks the row, then the runs, then its siblings, so a reaper that waited could
# deadlock with it. `again`: the job's latest closed run (by id; a claim closes and opens at one now()) was lost
# too. `due_at` moves only for this deployment's names, so an old replica never reschedules a newer one's row.
_REAP_LOST_SQL = f"""
WITH lapsed AS MATERIALIZED (
  SELECT r.id, r.job_name, r.holder
    FROM job_runs r
    JOIN scheduled_jobs j ON j.job_name = r.job_name
   WHERE r.ended_at IS NULL
     AND j.runner = %(runner)s AND j.registry_at IS NOT NULL
     AND NOT (j.lease_holder IS NOT DISTINCT FROM r.holder AND coalesce(j.lease_expires_at >= now(), false))
     FOR UPDATE OF r, j SKIP LOCKED
), judged AS (
  SELECT l.id, l.job_name, l.holder,
         coalesce((SELECT p.outcome = 'lost' FROM job_runs p
                    WHERE p.job_name = l.job_name AND p.ended_at IS NOT NULL
                    ORDER BY p.id DESC LIMIT 1), false) AS again
    FROM lapsed l
), closed AS (
  UPDATE job_runs r
     SET ended_at = now(), outcome = 'lost'
    FROM judged x
   WHERE r.id = x.id
  RETURNING r.id
)
UPDATE scheduled_jobs j
   SET consecutive_failures = j.consecutive_failures + 1,
       last_outcome = 'lost',
       lease_holder = CASE WHEN j.lease_holder IS NOT DISTINCT FROM x.holder THEN NULL ELSE j.lease_holder END,
       lease_expires_at = CASE WHEN j.lease_holder IS NOT DISTINCT FROM x.holder THEN NULL
                               ELSE j.lease_expires_at END,
       due_at = CASE WHEN j.due_at IS NULL OR j.job_name <> ALL(%(names)s::text[]) THEN j.due_at
                     WHEN j.job_name = ANY(%(on_hour)s::text[]) THEN least({_DAY_SLOT_SQL}, {_NEXT_SLOT_SQL})
                     WHEN x.again THEN {_BACKOFF_SQL}
                     ELSE now() END
  FROM judged x
 WHERE j.job_name = x.job_name
RETURNING j.job_name
"""

# Due or requested, free, and with no open run on the job or its resource: a lapsed run is the reaper's, so a
# second loss never becomes an immediate re-run through the claim's own lost-run close. Oldest first.
_DUE_SQL = f"""
SELECT j.job_name
  FROM scheduled_jobs j
 WHERE j.job_name = ANY(%(names)s::text[])
   AND j.runner = %(runner)s AND j.registry_at IS NOT NULL AND j.retired_at IS NULL
   AND {_FREE}
   AND ((j.due_at <= now() AND {_UNPAUSED}) OR j.requested_at <= now())
   AND NOT EXISTS (SELECT 1 FROM job_runs r
                    WHERE r.ended_at IS NULL
                      AND (r.job_name = j.job_name OR (j.resource IS NOT NULL AND r.resource = j.resource)))
 ORDER BY least(CASE WHEN {_UNPAUSED} THEN j.due_at END, j.requested_at), j.job_name
"""

_REQUEST_SQL = """
UPDATE scheduled_jobs
   SET requested_at = now(), requested_args = %(args)s::text[], requested_by = %(by)s
 WHERE job_name = %(job)s AND runner = %(runner)s AND registry_at IS NOT NULL AND retired_at IS NULL
   AND requested_at IS NULL
RETURNING 1
"""

_ROW_STATE_SQL = """
SELECT runner, registry_at IS NOT NULL, retired_at IS NOT NULL, requested_at, requested_by
  FROM scheduled_jobs WHERE job_name = %(job)s
"""

# Any live row, a lane's included. 675's CHECK demands the note and the author; `until` NULL lifts the pause now.
_PAUSE_SQL = """
UPDATE scheduled_jobs
   SET paused_until = coalesce(%(until)s::timestamptz, now()), pause_note = %(note)s, paused_by = %(by)s
 WHERE job_name = %(job)s AND retired_at IS NULL
RETURNING 1
"""

_ROWS_SQL = """
SELECT j.job_name, j.runner, j.registry_at IS NOT NULL AS registered, j.retired_at, j.due_at, j.paused_until,
       j.pause_note, j.requested_at, j.requested_by, j.lease_holder, j.lease_expires_at, j.last_outcome,
       j.consecutive_failures, j.last_success_at, j.last_error, r.started_at, r.ended_at, r.outcome,
       r.exit_code, r.peak_rss_mb
  FROM scheduled_jobs j
  LEFT JOIN LATERAL (SELECT started_at, ended_at, outcome, exit_code, peak_rss_mb FROM job_runs
                      WHERE job_name = j.job_name ORDER BY id DESC LIMIT 1) r ON true
 ORDER BY j.job_name
"""

_BEAT_SQL = """
INSERT INTO worker_heartbeats (worker, beat_at, started_at, details)
VALUES (%(worker)s, now(), %(started_at)s, %(details)s)
ON CONFLICT (worker) DO UPDATE SET beat_at = now(), started_at = EXCLUDED.started_at, details = EXCLUDED.details
"""

_FORGET_SQL = "DELETE FROM worker_heartbeats WHERE worker = %(worker)s"

# A replica of another deployment was replaced, not killed: forgotten at once. One of this deployment that went
# silent stays red (675's c_worker_dead) until it is an hour old.
_FORGET_REPLACED_SQL = """
DELETE FROM worker_heartbeats
 WHERE starts_with(worker, %(prefix)s) AND worker <> %(keep)s
   AND ((details ->> 'deployment') IS DISTINCT FROM %(deployment)s::text
        OR beat_at < now() - make_interval(secs => %(older_than_s)s::integer))
"""

_PRUNE_RUNS_SQL = "DELETE FROM job_runs WHERE ended_at < now() - make_interval(days => %(days)s::integer)"


def holder_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


@dataclass
class Lease:
    job_name: str
    holder: str
    run_id: int
    ttl_s: int
    args: list[str] | None
    requested_by: str | None
    fence_at: float
    due_slot: bool = False  # the row was due and unpaused when claimed: the run also took its scheduled slot

    def fenced(self, at: float | None = None) -> bool:
        """True once the holder must stop: its lease may lapse before another renewal lands."""
        return (clock() if at is None else at) >= self.fence_at


@dataclass(frozen=True)
class Registered:
    """What a boot's `register` did: rows born, adopted from a lane-era NULL registry_at, un-retired, and skipped."""

    inserted: tuple[str, ...]
    adopted: tuple[str, ...]
    unretired: tuple[str, ...]
    skipped: dict[str, str]


def _fence_at(sent_at: float, ttl_s: int) -> float:
    # Counted from before the statement was sent, so the fence never trails the database's expiry.
    return sent_at + ttl_s - FENCE_MARGIN_S


@contextmanager
def _bounded(conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    """One transaction under STATEMENT_TIMEOUT_S: a stalled database bounds each runner call, lock waits included."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_BOUND_SQL)
        yield cur


def claim(conn: psycopg.Connection, job_name: str, *, holder: str | None = None,
          ttl_s: int = TTL_S, version: str | None = None) -> Lease | None:
    """Claims a due job and opens its run, or returns None (not due, paused, retired, held, sibling busy)."""
    holder = holder or holder_id()
    params = {"job": job_name, "holder": holder, "ttl": ttl_s, "version": version}
    sent_at = clock()
    try:
        with _bounded(conn) as cur:
            cur.execute(_CLAIM_SQL, params)
            row = cur.fetchone()
            if row is None:
                return None
            resource, requested, args, requested_by, due_slot = row
            if not requested:
                args = requested_by = None
            params.update(resource=resource, args=args, requested_by=requested_by)
            cur.execute(_CLOSE_LOST_SQL, params)
            cur.execute(_OPEN_RUN_SQL, params)
            run_id = cur.fetchone()[0]
            if requested:
                cur.execute(_TAKE_REQUEST_SQL, params)
    except psycopg.errors.UniqueViolation:
        LOG.info("LEASE race lost job=%s: another holder opened a run on it or its resource", job_name)
        return None
    return Lease(job_name, holder, run_id, ttl_s, args, requested_by, _fence_at(sent_at, ttl_s), bool(due_slot))


def renew(conn: psycopg.Connection, lease: Lease) -> bool:
    """Extends a lease that has not lapsed; on False the holder keeps its old fence and must stop by it."""
    sent_at = clock()
    with _bounded(conn) as cur:
        cur.execute(_RENEW_SQL, {"job": lease.job_name, "holder": lease.holder, "ttl": lease.ttl_s})
        renewed = cur.fetchone() is not None
    if renewed:
        lease.fence_at = _fence_at(sent_at, lease.ttl_s)
    else:
        LOG.warning("LEASE lost job=%s holder=%s: renewal refused", lease.job_name, lease.holder)
    return renewed


def release(conn: psycopg.Connection, lease: Lease, outcome: str, *, due: str | None = None,
            error: str | None = None, exit_code: int | None = None, peak_rss_mb: int | None = None,
            log_tail: str | None = None) -> bool:
    """Closes the run and frees the row. Call it only once the work has stopped. False: the lease was already lost.

    `due` is where the next due time goes (DUE_KINDS); by default an interrupted run is due at once and any other
    run at its next slot."""
    if outcome not in RELEASE_OUTCOMES:
        raise ValueError(f"{outcome!r} is not a release outcome")
    due = due or ("now" if outcome == "interrupted" else "slot")
    if due not in DUE_KINDS:
        raise ValueError(f"{due!r} is not a due kind")
    params = {
        "job": lease.job_name, "holder": lease.holder, "outcome": outcome, "not_failure": outcome in NOT_FAILURES,
        "error": error[:500] if error else None, "due": due, "requested": lease.requested_by is not None,
        "due_slot": lease.due_slot, "run_id": lease.run_id, "exit_code": exit_code, "peak_rss_mb": peak_rss_mb,
        "log_tail": log_tail,
    }
    with _bounded(conn) as cur:
        cur.execute(_RELEASE_RUN_SQL, params)
        if cur.fetchone() is None:
            return False
        cur.execute(_CLOSE_RUN_SQL, params)
    return True


def register(conn: psycopg.Connection, entries: Sequence[Job], *, runner: str = RUNNER) -> Registered:
    """Upserts each entry in its own savepoint. A row the database refuses (a CHECK, a bad value) is skipped and
    named; any other error (a timeout, a lock, a dropped connection) fails the whole attempt, to be retried."""
    inserted: list[str] = []
    adopted: list[str] = []
    unretired: list[str] = []
    skipped: dict[str, str] = {}
    with _bounded(conn) as cur:
        for job in entries:
            params = {"job": job.name, "argv": list(job.argv), "every_s": job.every_s, "offset_s": job.offset_s,
                      "timeout_s": job.timeout_s, "memory_mb": job.memory_mb, "cpus": job.cpus,
                      "resource": job.resource, "runner": runner}
            try:
                with conn.transaction():
                    cur.execute(_REGISTERED_STATE_SQL, params)
                    state = cur.fetchone()
                    cur.execute(_REGISTER_SQL, params)
            except (psycopg.IntegrityError, psycopg.DataError) as exc:
                skipped[job.name] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
                continue
            if state is None:
                inserted.append(job.name)
            elif state[0]:
                adopted.append(job.name)
            elif state[1]:
                unretired.append(job.name)
    return Registered(tuple(inserted), tuple(adopted), tuple(unretired), skipped)


def retire(conn: psycopg.Connection, names: Sequence[str], *, runner: str = RUNNER) -> list[str]:
    """Retires the rows this runner registered that its registry no longer names; a lane's row is never touched."""
    with _bounded(conn) as cur:
        cur.execute(_RETIRE_SQL, {"names": list(names), "runner": runner})
        return [row[0] for row in cur.fetchall()]


def reap_lost(conn: psycopg.Connection, *, names: Sequence[str], on_hour: Sequence[str],
              runner: str = RUNNER) -> list[str]:
    """Closes every open run whose lease lapsed as `lost` (a failure): re-run at once, then back off on a second
    loss in a row; an `on_hour` (DB-heavy) name waits for its own hour either way. Returns the jobs it closed."""
    with _bounded(conn) as cur:
        cur.execute(_REAP_LOST_SQL, {"names": list(names), "on_hour": list(on_hour), "runner": runner})
        return [row[0] for row in cur.fetchall()]


def due(conn: psycopg.Connection, names: Sequence[str], *, runner: str = RUNNER) -> list[str]:
    """The claimable names among `names`, oldest due (or requested) first."""
    with _bounded(conn) as cur:
        cur.execute(_DUE_SQL, {"names": list(names), "runner": runner})
        return [row[0] for row in cur.fetchall()]


def request(conn: psycopg.Connection, name: str, args: Sequence[str], *, by: str,
            runner: str = RUNNER) -> str | None:
    """Files a request run (args appended to the registry argv). None when filed, else why it was refused."""
    with _bounded(conn) as cur:
        cur.execute(_REQUEST_SQL, {"job": name, "args": list(args) or None, "by": by, "runner": runner})
        if cur.fetchone() is not None:
            return None
        cur.execute(_ROW_STATE_SQL, {"job": name})
        row = cur.fetchone()
    if row is None:
        return f"{name} has no row yet: the runner registers it at boot"
    row_runner, registered, retired, requested_at, requested_by = row
    if row_runner != runner or not registered:
        return f"{name} is not a {runner} job (runner {row_runner})"
    if retired:
        return f"{name} is retired"
    return f"a request by {requested_by} has waited since {requested_at:%Y-%m-%d %H:%M:%S %Z}"


def pause(conn: psycopg.Connection, name: str, *, until: datetime | None, note: str, by: str) -> str | None:
    """Pauses the schedule until `until` (a request still runs), or lifts the pause now (`until` None)."""
    with _bounded(conn) as cur:
        cur.execute(_PAUSE_SQL, {"job": name, "until": until, "note": note, "by": by})
        if cur.fetchone() is not None:
            return None
        cur.execute(_ROW_STATE_SQL, {"job": name})
        row = cur.fetchone()
    return f"{name} has no row" if row is None else f"{name} is retired"


def rows(conn: psycopg.Connection) -> list[dict[str, object]]:
    """Every row with its latest run, for `list`."""
    with _bounded(conn) as cur:
        cur.execute(_ROWS_SQL)
        names = [d.name for d in cur.description or ()]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def beat(conn: psycopg.Connection, worker: str, started_at: datetime, details: Mapping[str, object]) -> None:
    with _bounded(conn) as cur:
        cur.execute(_BEAT_SQL, {"worker": worker, "started_at": started_at, "details": Jsonb(dict(details))})


def forget(conn: psycopg.Connection, worker: str) -> None:
    with _bounded(conn) as cur:
        cur.execute(_FORGET_SQL, {"worker": worker})


def forget_replaced(conn: psycopg.Connection, *, prefix: str, keep: str, deployment: str | None,
                    older_than_s: int) -> int:
    """Deletes the heartbeats of replaced replicas (another deployment), and this deployment's long-silent ones."""
    with _bounded(conn) as cur:
        cur.execute(_FORGET_REPLACED_SQL, {"prefix": prefix, "keep": keep, "deployment": deployment,
                                           "older_than_s": older_than_s})
        return cur.rowcount


def prune_runs(conn: psycopg.Connection, *, days: int = 90) -> int:
    """Deletes closed runs older than `days` (migration 675: the runner prunes job_runs); open runs stay."""
    with _bounded(conn) as cur:
        cur.execute(_PRUNE_RUNS_SQL, {"days": days})
        return cur.rowcount


def _release_params(job_name: str, holder: str, outcome: str, error: str | None,
                    due_at: datetime | None) -> dict[str, object]:
    if outcome not in RELEASE_OUTCOMES:
        raise ValueError(f"{outcome!r} is not a release outcome")
    return {"job": job_name, "holder": holder, "outcome": outcome, "not_failure": outcome in NOT_FAILURES,
            "error": error[:500] if error else None, "due_at": due_at}


@contextmanager
def held(conn: psycopg.Connection, job_name: str, *, cadence: str, runner: str = "github_actions",
         ttl_s: int = 3600) -> Iterator[bool]:
    """Yields True when this run owns the lane, False when another run holds it or it is paused or retired."""
    holder = holder_id()
    with conn.cursor() as cur:
        cur.execute(_UPSERT_LANE_SQL, (job_name, cadence, runner))
        cur.execute(_ACQUIRE_LANE_SQL, (holder, ttl_s, job_name))
        acquired = cur.fetchone() is not None
    if not acquired:
        LOG.info("LEASE busy job=%s", job_name)
        yield False
        return
    outcome, error = "ok", None
    try:
        yield True
    except Exception as exc:  # noqa: BLE001 - the lease must be released either way
        outcome, error = "failed", str(exc)[:500]
        raise
    except BaseException:
        outcome = "interrupted"
        raise
    finally:
        with conn.cursor() as cur:
            cur.execute(_RELEASE_JOB_SQL, _release_params(job_name, holder, outcome, error, None))

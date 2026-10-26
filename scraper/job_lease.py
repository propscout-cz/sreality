"""Lease-row CAS on `scheduled_jobs` (migration 675; `location_jobs` until then).

**Never a session advisory lock.** Every service-role path uses the transaction-mode pooler,
where a lock taken on one backend and released on another silently strands. The row is the
lock: a claim is a conditional UPDATE, a crashed holder's lease simply expires.

Two shapes over one row:

* `claim` / `renew` / `release` — a scheduled job. A claim takes a DUE row (its `due_at`
  passed and it is not paused, or a request is waiting: a request is a person asking, a
  pause only stops the schedule), never a retired one, never while a sibling sharing its
  `resource` holds a live lease, and opens one `job_runs` row; the database's unique
  indexes allow one open run per job and per resource, so a lost race is a refused claim.
  A run whose lease lapsed is closed as `lost` (a failure) by the next claim on its job or
  resource. The holder renews and fences itself (`Lease.fenced`) before the lease can
  expire under it; the release closes the run and moves `due_at` to the next slot of the
  epoch-anchored `cadence` grid. A cancelled, interrupted or skipped run is not a failure,
  and an interrupted one is due again at once.
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import psycopg

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
RETURNING j.resource, coalesce(j.requested_at <= now(), false), j.requested_args, j.requested_by
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


def claim(conn: psycopg.Connection, job_name: str, *, holder: str | None = None,
          ttl_s: int = TTL_S, version: str | None = None) -> Lease | None:
    """Claims a due job and opens its run, or returns None (not due, paused, retired, held, sibling busy)."""
    holder = holder or holder_id()
    params = {"job": job_name, "holder": holder, "ttl": ttl_s, "version": version}
    sent_at = time.monotonic()
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_CLAIM_SQL, params)
            row = cur.fetchone()
            if row is None:
                return None
            resource, requested, args, requested_by = row
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
    return Lease(job_name, holder, run_id, ttl_s, args, requested_by, _fence_at(sent_at, ttl_s))


def renew(conn: psycopg.Connection, lease: Lease) -> bool:
    """Extends a lease that has not lapsed; on False the holder keeps its old fence and must stop by it."""
    sent_at = time.monotonic()
    with conn.cursor() as cur:
        cur.execute(_RENEW_SQL, {"job": lease.job_name, "holder": lease.holder, "ttl": lease.ttl_s})
        renewed = cur.fetchone() is not None
    if renewed:
        lease.fence_at = _fence_at(sent_at, lease.ttl_s)
    else:
        LOG.warning("LEASE lost job=%s holder=%s: renewal refused", lease.job_name, lease.holder)
    return renewed


def release(conn: psycopg.Connection, lease: Lease, outcome: str, *, error: str | None = None,
            exit_code: int | None = None, peak_rss_mb: int | None = None, log_tail: str | None = None,
            due_at: datetime | None = None) -> bool:
    """Closes the run and frees the row. Call it only once the work has stopped. False: the lease was already lost."""
    params = {
        **_release_params(lease.job_name, lease.holder, outcome, error, due_at), "run_id": lease.run_id,
        "exit_code": exit_code, "peak_rss_mb": peak_rss_mb, "log_tail": log_tail,
    }
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_RELEASE_JOB_SQL, params)
        if cur.fetchone() is None:
            return False
        cur.execute(_CLOSE_RUN_SQL, params)
    return True


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

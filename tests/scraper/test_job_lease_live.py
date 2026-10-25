"""Migration 675 + `scraper.job_lease`, EXECUTED on the replayed schema: claim-if-due, renewal, self-fencing before
expiry, one open run per job and per resource, the shared-resource exclusion, the release that is not a failure, the
lease-only lane, and the alias view the deployment predating the rename still writes through.

Runs in CI's migrations job (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`). Every test but the race rolls back; the
race needs two committed sessions, so it runs on job names of its own and deletes them after.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from scraper import job_lease
from tests._live_property import REQUIRED_DB, cur, db_url  # noqa: F401 -- `cur` is the fixture

pytestmark = REQUIRED_DB

# The deployment that predates 675, verbatim (location_data/resolver/lease.py at d4f564e19).
_OLD_UPSERT = """
INSERT INTO location_jobs (job_name, cadence, concurrency_group, runner, enabled)
VALUES (%s, %s::interval, %s, %s, true)
ON CONFLICT (job_name) DO NOTHING
"""
_OLD_ACQUIRE = """
UPDATE location_jobs
   SET lease_holder = %s,
       lease_expires_at = now() + make_interval(secs => %s),
       last_started_at = now(),
       last_outcome = 'running'
 WHERE job_name = %s
   AND enabled
   AND (lease_holder IS NULL OR lease_expires_at IS NULL OR lease_expires_at < now())
RETURNING job_name
"""
_OLD_RELEASE = """
UPDATE location_jobs
   SET lease_holder = NULL,
       lease_expires_at = NULL,
       last_outcome = %s,
       last_error = %s,
       last_success_at = CASE WHEN %s = 'ok' THEN now() ELSE last_success_at END,
       consecutive_failures = CASE WHEN %s = 'ok' THEN 0 ELSE consecutive_failures + 1 END
 WHERE job_name = %s AND lease_holder = %s
"""


def _job(cur: Any, *, due: str | None = "-1 second", cadence: str = "1 hour", offset_s: int | None = None,
         resource: str | None = None) -> str:
    """A scheduled job due `due` from now (None: never due, a lease-only lane)."""
    name = f"test-job-{uuid.uuid4().hex[:12]}"
    cur.execute("INSERT INTO scheduled_jobs (job_name, cadence, runner, due_at, offset_s, timeout_s, resource) "
                "VALUES (%s, %s::interval, 'job-runner', now() + %s::interval, %s, 600, %s)",
                (name, cadence, due, offset_s, resource))
    return name


def _row(cur: Any, name: str) -> dict[str, Any]:
    cur.execute("SELECT lease_holder, lease_expires_at - now() AS ttl, last_outcome, last_success_at = now() AS ok_now,"
                " consecutive_failures, due_at, due_at = now() AS due_now, requested_at, requested_args, requested_by"
                " FROM scheduled_jobs WHERE job_name = %s", (name,))
    cols = [d.name for d in cur.description]
    return dict(zip(cols, cur.fetchone(), strict=True))


def _runs(cur: Any, name: str) -> list[tuple[Any, ...]]:
    cur.execute("SELECT holder, outcome, ended_at IS NULL, args, requested_by, exit_code, peak_rss_mb, log_tail"
                " FROM job_runs WHERE job_name = %s ORDER BY id", (name,))
    return cur.fetchall()


def _expire(cur: Any, name: str) -> None:
    cur.execute("UPDATE scheduled_jobs SET lease_expires_at = now() - interval '1 second' WHERE job_name = %s",
                (name,))


def test_a_due_job_is_claimed_once_and_opens_one_run(cur: Any) -> None:
    conn = cur.connection
    name = _job(cur)
    lease = job_lease.claim(conn, name, holder="A", version="abc123")
    assert lease is not None and lease.holder == "A" and lease.args is None
    row = _row(cur, name)
    assert (row["lease_holder"], row["ttl"], row["last_outcome"]) == ("A", timedelta(seconds=180), "running")
    assert _runs(cur, name) == [("A", None, True, None, None, None, None, None)]
    cur.execute("SELECT version, resource FROM job_runs WHERE id = %s", (lease.run_id,))
    assert cur.fetchone() == ("abc123", None)
    assert job_lease.claim(conn, name, holder="B") is None, "a held row is never claimed twice"


@pytest.mark.parametrize("state", ["not due", "never due", "paused", "retired"])
def test_a_row_that_is_not_due_or_not_live_is_never_claimed(cur: Any, state: str) -> None:
    name = _job(cur, due={"not due": "1 minute", "never due": None}.get(state, "-1 second"))
    if state == "paused":
        cur.execute("UPDATE scheduled_jobs SET paused_until = now() + interval '1 day', pause_note = 'drill',"
                    " paused_by = 'operator' WHERE job_name = %s", (name,))
    if state == "retired":
        cur.execute("UPDATE scheduled_jobs SET retired_at = now() WHERE job_name = %s", (name,))
    assert job_lease.claim(cur.connection, name, holder="A") is None
    assert _runs(cur, name) == []


def test_a_pause_ends_and_must_be_signed(cur: Any) -> None:
    name = _job(cur)
    cur.execute("UPDATE scheduled_jobs SET paused_until = now() - interval '1 second', pause_note = 'over',"
                " paused_by = 'operator' WHERE job_name = %s", (name,))
    assert job_lease.claim(cur.connection, name, holder="A") is not None
    for pause in ("paused_until = 'infinity', pause_note = 'n', paused_by = 'o'",
                  "paused_until = now() + interval '1 day', pause_note = NULL"):
        with pytest.raises(psycopg.errors.CheckViolation), cur.connection.transaction():
            cur.execute(f"UPDATE scheduled_jobs SET {pause} WHERE job_name = %s", (name,))  # noqa: S608


def test_a_request_is_a_row_the_claim_takes_even_while_the_schedule_is_paused(cur: Any) -> None:
    name = _job(cur, due="1 day")
    cur.execute("UPDATE scheduled_jobs SET requested_at = now(), requested_args = '{--limit,5}',"
                " requested_by = 'operator', paused_until = now() + interval '1 day', pause_note = 'drill',"
                " paused_by = 'operator' WHERE job_name = %s", (name,))
    lease = job_lease.claim(cur.connection, name, holder="A")
    assert lease is not None and (lease.args, lease.requested_by) == (["--limit", "5"], "operator")
    row = _row(cur, name)
    assert (row["requested_at"], row["requested_args"], row["requested_by"]) == (None, None, None)
    assert _runs(cur, name)[0][3:5] == (["--limit", "5"], "operator")


def test_a_due_claim_leaves_a_request_for_later_untouched(cur: Any) -> None:
    name = _job(cur)
    cur.execute("UPDATE scheduled_jobs SET requested_at = now() + interval '1 hour', requested_args = '{--x}',"
                " requested_by = 'operator' WHERE job_name = %s", (name,))
    lease = job_lease.claim(cur.connection, name, holder="A")
    assert lease is not None and (lease.args, lease.requested_by) == (None, None)
    assert _row(cur, name)["requested_args"] == ["--x"]
    assert _runs(cur, name)[0][3:5] == (None, None)


def test_renewal_extends_a_live_lease_and_moves_the_fence(cur: Any) -> None:
    conn = cur.connection
    name = _job(cur)
    lease = job_lease.claim(conn, name, holder="A", ttl_s=60)
    first_fence = lease.fence_at
    cur.execute("UPDATE scheduled_jobs SET lease_expires_at = now() + interval '5 seconds' WHERE job_name = %s",
                (name,))
    assert job_lease.renew(conn, lease)
    assert _row(cur, name)["ttl"] == timedelta(seconds=60)
    assert lease.fence_at >= first_fence


def test_a_lapsed_or_taken_lease_is_never_renewed_and_the_holder_fences_itself(cur: Any) -> None:
    conn = cur.connection
    name = _job(cur)
    sent_before = time.monotonic()
    lease = job_lease.claim(conn, name, holder="A")
    # The fence falls a margin BEFORE the database's expiry, counted from before the claim was sent.
    assert lease.fence_at <= sent_before + job_lease.TTL_S - job_lease.FENCE_MARGIN_S + 1
    assert not lease.fenced(at=lease.fence_at - 0.001)
    assert lease.fenced(at=lease.fence_at)
    fence = lease.fence_at

    _expire(cur, name)
    assert not job_lease.renew(conn, lease), "an expired lease is lost, never resurrected"
    assert lease.fence_at == fence

    cur.execute("UPDATE scheduled_jobs SET lease_holder = 'B', lease_expires_at = now() + interval '1 minute'"
                " WHERE job_name = %s", (name,))
    assert not job_lease.renew(conn, lease)
    assert _row(cur, name)["lease_holder"] == "B"


def test_the_database_allows_one_open_run_per_job(cur: Any) -> None:
    name = _job(cur)
    cur.execute("INSERT INTO job_runs (job_name, holder) VALUES (%s, 'A')", (name,))
    with pytest.raises(psycopg.errors.UniqueViolation), cur.connection.transaction():
        cur.execute("INSERT INTO job_runs (job_name, holder) VALUES (%s, 'B')", (name,))
    with pytest.raises(psycopg.errors.CheckViolation), cur.connection.transaction():
        cur.execute("INSERT INTO job_runs (job_name, holder, ended_at) VALUES (%s, 'B', now())", (name,))


def test_a_run_whose_lease_expired_is_closed_as_lost_by_the_next_claim(cur: Any) -> None:
    conn = cur.connection
    name = _job(cur)
    lost = job_lease.claim(conn, name, holder="A")
    _expire(cur, name)
    assert job_lease.claim(conn, name, holder="B") is not None
    assert [r[:3] for r in _runs(cur, name)] == [("A", "lost", False), ("B", None, True)]
    row = _row(cur, name)
    assert (row["lease_holder"], row["last_outcome"], row["consecutive_failures"]) == ("B", "running", 1)
    assert not job_lease.release(conn, lost, "ok"), "the lost holder's release changes nothing"
    assert [r[:3] for r in _runs(cur, name)] == [("A", "lost", False), ("B", None, True)]
    assert _row(cur, name)["lease_holder"] == "B"


def test_jobs_sharing_a_resource_never_run_together(cur: Any) -> None:
    conn = cur.connection
    first, sibling = _job(cur, resource="test-heavy"), _job(cur, resource="test-heavy")
    held = job_lease.claim(conn, first, holder="A")
    assert held is not None
    assert job_lease.claim(conn, sibling, holder="B") is None, "a held sibling makes the claim fail"
    assert job_lease.release(conn, held, "ok")
    assert job_lease.claim(conn, sibling, holder="B") is not None
    with pytest.raises(psycopg.errors.UniqueViolation), cur.connection.transaction():
        cur.execute("INSERT INTO job_runs (job_name, holder, resource) VALUES (%s, 'C', 'test-heavy')", (first,))


def test_a_sibling_whose_lease_expired_is_lost_and_frees_the_resource(cur: Any) -> None:
    conn = cur.connection
    first, sibling = _job(cur, resource="test-heavy"), _job(cur, resource="test-heavy")
    lost = job_lease.claim(conn, first, holder="A")
    _expire(cur, first)
    assert job_lease.claim(conn, sibling, holder="B") is not None
    assert [r[:3] for r in _runs(cur, first)] == [("A", "lost", False)]
    row = _row(cur, first)
    assert (row["lease_holder"], row["last_outcome"], row["consecutive_failures"]) == (None, "lost", 1)
    assert not job_lease.release(conn, lost, "ok")


@pytest.mark.parametrize("outcome,failures,success,due", [
    ("ok", 0, True, "next slot"),
    ("failed", 3, False, "next slot"),
    ("timeout", 3, False, "next slot"),
    ("cancelled", 2, False, "next slot"),
    ("skipped", 2, False, "next slot"),
    ("interrupted", 2, False, "now"),
])
def test_the_release_closes_the_run_and_only_a_failure_counts(
        cur: Any, outcome: str, failures: int, success: bool, due: str) -> None:
    conn = cur.connection
    name = _job(cur, cadence="1 hour", offset_s=600)
    cur.execute("UPDATE scheduled_jobs SET consecutive_failures = 2 WHERE job_name = %s", (name,))
    lease = job_lease.claim(conn, name, holder="A")
    assert job_lease.release(conn, lease, outcome, error="boom" if outcome != "ok" else None, exit_code=1,
                             peak_rss_mb=512, log_tail="last line")
    row = _row(cur, name)
    assert (row["lease_holder"], row["last_outcome"], row["consecutive_failures"]) == (None, outcome, failures)
    assert bool(row["ok_now"]) is success
    cur.execute("SELECT now()")
    now = cur.fetchone()[0]
    if due == "now":
        assert row["due_now"]
    else:
        # The grid is anchored at the epoch: an hourly job at offset 600 s is due at the next hh:10 after now.
        assert row["due_at"] > now and row["due_at"] - now <= timedelta(hours=1)
        assert (row["due_at"].minute, row["due_at"].second) == (10, 0)
    assert _runs(cur, name) == [("A", outcome, False, None, None, 1, 512, "last line")]


def test_the_runner_may_set_the_next_due_time_and_never_a_live_outcome(cur: Any) -> None:
    conn = cur.connection
    name = _job(cur)
    lease = job_lease.claim(conn, name, holder="A")
    later = datetime(2030, 1, 1, tzinfo=UTC)
    assert job_lease.release(conn, lease, "failed", due_at=later)
    assert _row(cur, name)["due_at"] == later
    with pytest.raises(ValueError, match="not a release outcome"):
        job_lease.release(conn, lease, "lost")


def test_the_lease_only_lane_takes_no_run_and_honours_a_pause(cur: Any) -> None:
    conn = cur.connection
    name = f"test-lane-{uuid.uuid4().hex[:12]}"
    with job_lease.held(conn, name, cadence="15 minutes") as acquired:
        assert acquired
        with job_lease.held(conn, name, cadence="15 minutes") as again:
            assert not again
    assert _runs(cur, name) == []
    assert _row(cur, name)["last_outcome"] == "ok"

    with pytest.raises(KeyboardInterrupt), job_lease.held(conn, name, cadence="15 minutes"):
        raise KeyboardInterrupt
    row = _row(cur, name)
    assert (row["last_outcome"], row["consecutive_failures"], row["lease_holder"]) == ("interrupted", 0, None)
    assert row["due_at"] is None, "a lane has no schedule, so an interruption never makes it due"

    with pytest.raises(RuntimeError), job_lease.held(conn, name, cadence="15 minutes"):
        raise RuntimeError("drain broke")
    assert (_row(cur, name)["last_outcome"], _row(cur, name)["consecutive_failures"]) == ("failed", 1)

    cur.execute("UPDATE scheduled_jobs SET paused_until = now() + interval '1 hour', pause_note = 'drill',"
                " paused_by = 'operator' WHERE job_name = %s", (name,))
    with job_lease.held(conn, name, cadence="15 minutes") as acquired:
        assert not acquired


def test_the_deployment_before_the_rename_keeps_its_lease_through_the_alias(cur: Any) -> None:
    """Between the apply and the merge the worker and location_resolve.yml run 384's text against `location_jobs`."""
    name = f"test-old-{uuid.uuid4().hex[:12]}"
    cur.execute(_OLD_UPSERT, (name, "15 minutes", "location-resolve", "railway-realtime-worker"))
    cur.execute(_OLD_UPSERT, (name, "15 minutes", "location-resolve", "railway-realtime-worker"))
    cur.execute(_OLD_ACQUIRE, ("old-holder", 600, name))
    assert cur.fetchone() == (name,)
    cur.execute(_OLD_ACQUIRE, ("other", 600, name))
    assert cur.fetchone() is None
    assert _row(cur, name)["lease_holder"] == "old-holder"
    with job_lease.held(cur.connection, name, cadence="15 minutes") as acquired:
        assert not acquired, "the new code and the old share one row"
    cur.execute(_OLD_RELEASE, ("ok", None, "ok", "ok", name, "old-holder"))
    row = _row(cur, name)
    assert (row["lease_holder"], row["last_outcome"], bool(row["ok_now"])) == (None, "ok", True)
    cur.execute("SELECT relkind FROM pg_class WHERE oid = 'public.location_jobs'::regclass")
    assert cur.fetchone() == ("v",)


def test_no_browser_role_reaches_the_lease_tables(cur: Any) -> None:
    """The alias view is ungated on purpose (tests/test_tenant_isolation_live.py::_ADMIN_GATE_ALLOWLIST): its ACL is
    the guard, so the ACL is asserted here."""
    for relation in ("scheduled_jobs", "job_runs", "location_jobs"):
        for role in ("anon", "authenticated"):
            cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, f"public.{relation}"))
            assert cur.fetchone() == (False,), (role, relation)


def _sessions(n: int) -> list[Any]:
    return [psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000 -c lock_timeout=10000")
            for _ in range(n)]


def _siblings(conn: Any, resource: str) -> list[str]:
    names = [f"test-race-{uuid.uuid4().hex[:12]}" for _ in range(2)]
    with conn.cursor() as cur:
        for name in names:
            cur.execute("INSERT INTO scheduled_jobs (job_name, cadence, runner, due_at, resource)"
                        " VALUES (%s, '1 hour', 'job-runner', now() - interval '1 second', %s)", (name, resource))
    return names


def _cleanup(conn: Any, names: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM job_runs WHERE job_name = ANY(%s)", (names,))
        cur.execute("DELETE FROM scheduled_jobs WHERE job_name = ANY(%s)", (names,))


def _open_runs(conn: Any, resource: str) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute("SELECT holder FROM job_runs WHERE resource = %s AND ended_at IS NULL", (resource,))
        return cur.fetchall()


def test_two_holders_racing_for_one_resource_get_one_run() -> None:
    """Two replicas claim two sibling jobs at once: B's sibling check reads before A commits, so only the unique
    open-run index stands between them, and B's claim is refused, not raised, and leaves no lease behind."""
    resource = f"test-race-{uuid.uuid4().hex[:8]}"
    a, b = _sessions(2)
    names = _siblings(a, resource)
    try:
        result: dict[str, Any] = {}
        with a.transaction():
            assert job_lease.claim(a, names[0], holder="A") is not None  # open until this block commits
            racer = threading.Thread(target=lambda: result.update(lease=job_lease.claim(b, names[1], holder="B")))
            racer.start()
            racer.join(timeout=1)
            assert racer.is_alive(), "B's open run must wait on A's uncommitted one"
        racer.join(timeout=15)
        assert result == {"lease": None}
        assert _open_runs(a, resource) == [("A",)]
        with a.cursor() as cur:
            cur.execute("SELECT lease_holder FROM scheduled_jobs WHERE job_name = %s", (names[1],))
            assert cur.fetchone() == (None,), "the refused claim rolled its lease back too"
    finally:
        _cleanup(a, names)
        a.close()
        b.close()


def test_a_sibling_that_commits_mid_claim_is_never_closed_as_lost() -> None:
    """The other order: B's compare-and-set reads the resource free, then A claims and commits before B closes lost
    runs. B must see A's live lease at that step (a fresh snapshot), leave A's run open and be refused by the index;
    closing it as lost would put two jobs on one resource. C holds B's row so A lands in between."""
    resource = f"test-race-{uuid.uuid4().hex[:8]}"
    a, b, c = _sessions(3)
    names = _siblings(a, resource)
    try:
        result: dict[str, Any] = {}
        with c.transaction(), c.cursor() as cur:
            cur.execute("SELECT 1 FROM scheduled_jobs WHERE job_name = %s FOR UPDATE", (names[1],))
            racer = threading.Thread(target=lambda: result.update(lease=job_lease.claim(b, names[1], holder="B")))
            racer.start()
            racer.join(timeout=1)
            assert racer.is_alive(), "B's compare-and-set must wait on C's row lock"
            assert job_lease.claim(a, names[0], holder="A") is not None
        racer.join(timeout=15)
        assert result == {"lease": None}
        assert _open_runs(a, resource) == [("A",)]
        with a.cursor() as cur:
            cur.execute("SELECT job_name, lease_holder, last_outcome FROM scheduled_jobs WHERE job_name = ANY(%s)"
                        " ORDER BY job_name = %s DESC", (names, names[0]))
            assert cur.fetchall() == [(names[0], "A", "running"), (names[1], None, None)]
    finally:
        _cleanup(a, names)
        for conn in (a, b, c):
            conn.close()

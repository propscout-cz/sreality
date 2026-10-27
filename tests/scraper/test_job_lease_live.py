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
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest

from scraper import job_lease, jobs
from scraper.jobs import Job
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


def _now(cur: Any) -> datetime:
    """The test transaction's frozen now(); also opens it, so no job_lease call commits (psycopg's outermost
    `transaction()` on an idle non-autocommit connection would BEGIN and COMMIT)."""
    cur.execute("SELECT now()")
    return cur.fetchone()[0]


def _offset_away(now: datetime, every_s: int = 3600) -> int:
    """An offset whose next slot is half a cadence after `now`: no wait a test asserts is then capped by the next
    slot, whatever the time of day."""
    return (int(now.timestamp()) + every_s // 2) % every_s


def _release_due(cur: Any, *, cadence: str = "1 hour", offset_s: int = 600, timeout_s: int = 600,
                 failures: int = 0, outcome: str = "failed", due: str | None = None,
                 request: bool = False, due_now: bool = True) -> tuple[datetime, datetime | None]:
    """(now, the released row's due_at) for one claim and release; a request may ride a due or a not-due row."""
    now = _now(cur)
    name = _job(cur, due="-1 second" if due_now else "1 day", cadence=cadence, offset_s=offset_s)
    cur.execute("UPDATE scheduled_jobs SET consecutive_failures = %s, timeout_s = %s WHERE job_name = %s",
                (failures, timeout_s, name))
    if request:
        cur.execute("UPDATE scheduled_jobs SET requested_at = now(), requested_by = 'operator' WHERE job_name = %s",
                    (name,))
    lease = job_lease.claim(cur.connection, name, holder="A")
    assert lease is not None
    assert job_lease.release(cur.connection, lease, outcome, due=due)
    return now, _row(cur, name)["due_at"]


def test_a_more_remains_exit_re_dues_in_a_minute_and_a_backoff_doubles(cur: Any) -> None:
    now, due_at = _release_due(cur, outcome="ok", due="more")
    assert due_at == now + timedelta(seconds=job_lease.MORE_REMAINS_S)
    for failures, wait_s in ((0, 60), (1, 120), (2, 240)):
        now, due_at = _release_due(cur, failures=failures, due="backoff", offset_s=_offset_away(_now(cur)))
        assert due_at == now + timedelta(seconds=wait_s), failures


def test_a_backoff_never_passes_the_cadence_or_the_next_slot(cur: Any) -> None:
    now, due_at = _release_due(cur, cadence="15 minutes", offset_s=0, failures=12, due="backoff")
    assert now < due_at <= now + timedelta(minutes=15)
    assert (due_at.minute % 15, due_at.second) == (0, 0), "capped at the next slot"


def test_a_timeout_retries_no_sooner_than_its_own_timeout(cur: Any) -> None:
    """A walk cut by its own timeout never restarts at once; a 30-day job does not wait a month (R-c)."""
    away = _offset_away(_now(cur), 30 * 86400)
    now, due_at = _release_due(cur, cadence="30 days", offset_s=away, timeout_s=900, outcome="timeout",
                               due="retry")
    assert due_at == now + timedelta(minutes=15)
    now, due_at = _release_due(cur, cadence="30 days", offset_s=away, timeout_s=900, failures=3,
                               outcome="timeout", due="retry")
    assert due_at == now + timedelta(minutes=15), "60 * 2^3 = 480 s is under the floor"
    now, due_at = _release_due(cur, cadence="30 days", offset_s=away, timeout_s=900, failures=4,
                               outcome="timeout", due="retry")
    assert due_at == now + timedelta(seconds=960), "past the floor, the backoff grows"


def test_a_db_heavy_retry_waits_for_its_own_hour_of_the_next_day_at_most(cur: Any) -> None:
    now, due_at = _release_due(cur, cadence="91 days", offset_s=6300000, timeout_s=7200, outcome="timeout",
                               due="own_hour")
    assert (due_at.hour, due_at.minute, due_at.second) == (22, 0, 0), "map_overlays' hour"
    assert now < due_at <= now + timedelta(days=1)
    now, due_at = _release_due(cur, cadence="1 day", offset_s=3 * 3600, outcome="failed", due="own_hour")
    assert (due_at.hour, due_at.minute) == (3, 0) and now < due_at <= now + timedelta(days=1)


def test_a_pure_request_run_never_moves_the_schedule(cur: Any) -> None:
    cur.execute("SELECT now() + interval '1 day'")
    tomorrow = cur.fetchone()[0]
    now, due_at = _release_due(cur, outcome="ok", request=True, due_now=False)
    assert due_at == tomorrow
    _, due_at = _release_due(cur, outcome="failed", due="backoff", request=True, due_now=False)
    assert due_at == tomorrow, "not even a failure's backoff"
    _, due_at = _release_due(cur, outcome="ok", due="more", request=True, due_now=False)
    assert due_at == now + timedelta(seconds=job_lease.MORE_REMAINS_S), "an exit 75 only pulls it nearer"


def test_a_request_that_took_a_due_slot_advances_it_like_a_scheduled_run(cur: Any) -> None:
    now, due_at = _release_due(cur, outcome="ok", request=True, due_now=True)
    assert now < due_at <= now + timedelta(hours=1) and due_at.minute == 10, "the past slot is not left behind"


def test_a_claim_says_whether_it_took_a_due_slot(cur: Any) -> None:
    _now(cur)
    due, later, paused = _job(cur), _job(cur, due="1 day"), _job(cur)
    cur.execute("UPDATE scheduled_jobs SET requested_at = now(), requested_by = 'operator'"
                " WHERE job_name = ANY(%s)", ([later, paused],))
    cur.execute("UPDATE scheduled_jobs SET paused_until = now() + interval '1 day', pause_note = 'drill',"
                " paused_by = 'operator' WHERE job_name = %s", (paused,))
    assert [job_lease.claim(cur.connection, n, holder="A").due_slot for n in (due, later, paused)] == [
        True, False, False]


def test_a_release_refuses_an_unknown_outcome_or_due_kind(cur: Any) -> None:
    _now(cur)
    lease = job_lease.claim(cur.connection, _job(cur), holder="A")
    with pytest.raises(ValueError, match="not a release outcome"):
        job_lease.release(cur.connection, lease, "lost")
    with pytest.raises(ValueError, match="not a due kind"):
        job_lease.release(cur.connection, lease, "failed", due="tomorrow")


def _entry(name: str | None = None, *, every_s: int = 3600, offset_s: int = 600, timeout_s: int = 900,
           cpus: int = 1, resource: str | None = None, host: jobs.Host = jobs.RUNNER) -> Job:
    return Job(name or f"test-job-{uuid.uuid4().hex[:12]}", ("scripts.x", "--limit", "5"), every_s, offset_s,
               timeout_s, 512, cpus, host, resource)


def _registry_row(cur: Any, name: str) -> dict[str, Any]:
    cur.execute("SELECT argv, cadence, offset_s, timeout_s, memory_mb, cpus, resource, runner, registry_at = now()"
                " AS registered_now, retired_at, due_at, enabled, lease_holder FROM scheduled_jobs"
                " WHERE job_name = %s", (name,))
    cols = [d.name for d in cur.description]
    return dict(zip(cols, cur.fetchone(), strict=True))


def test_register_writes_the_contract_columns_and_a_first_slot_after_the_timeout(cur: Any) -> None:
    now = _now(cur)
    entry = _entry(resource="test-heavy")
    assert job_lease.register(cur.connection, [entry]) == job_lease.Registered((entry.name,), (), (), {})
    row = _registry_row(cur, entry.name)
    assert {k: row[k] for k in ("argv", "cadence", "offset_s", "timeout_s", "memory_mb", "cpus", "resource",
                                "runner", "registered_now", "retired_at", "enabled", "lease_holder")} == {
        "argv": ["scripts.x", "--limit", "5"], "cadence": timedelta(hours=1), "offset_s": 600, "timeout_s": 900,
        "memory_mb": 512, "cpus": 1, "resource": "test-heavy", "runner": "job-runner", "registered_now": True,
        "retired_at": None, "enabled": True, "lease_holder": None}
    assert row["due_at"] >= now + timedelta(seconds=900)
    assert row["due_at"] < now + timedelta(seconds=900 + 3600)
    assert (row["due_at"].minute, row["due_at"].second, row["due_at"].microsecond) == (10, 0, 0), "on the grid"


def test_a_re_register_keeps_the_schedule_and_never_writes_enabled(cur: Any) -> None:
    _now(cur)
    entry = _entry()
    job_lease.register(cur.connection, [entry])
    cur.execute("UPDATE scheduled_jobs SET due_at = '2030-01-01', enabled = false WHERE job_name = %s",
                (entry.name,))
    moved = Job(entry.name, ("scripts.x",), 7200, 0, 1200, 1024, 2, jobs.RUNNER)
    assert job_lease.register(cur.connection, [moved]) == job_lease.Registered((), (), (), {})
    row = _registry_row(cur, entry.name)
    assert (row["argv"], row["cadence"], row["timeout_s"], row["cpus"]) == (
        ["scripts.x"], timedelta(hours=2), 1200, 2)
    assert row["due_at"].year == 2030, "a changed grid is never re-slotted"
    assert row["enabled"] is False, "enabled is dead since 675: never written"


def test_register_un_retires_and_adopts_a_lane_era_row_keeping_its_lease_and_history(cur: Any) -> None:
    now = _now(cur)
    retired = _entry()
    job_lease.register(cur.connection, [retired])
    cur.execute("UPDATE scheduled_jobs SET retired_at = now(), due_at = '2000-01-01' WHERE job_name = %s",
                (retired.name,))
    lane_era = _job(cur, due=None)  # registry_at NULL: a row a registry entry now names
    cur.execute("UPDATE scheduled_jobs SET lease_holder = 'old', lease_expires_at = now() + interval '1 minute'"
                " WHERE job_name = %s", (lane_era,))
    cur.execute("INSERT INTO job_runs (job_name, holder, ended_at, outcome) VALUES (%s, 'old', now(), 'ok')",
                (lane_era,))
    assert job_lease.register(cur.connection, [retired, _entry(lane_era)]) == job_lease.Registered(
        (), (lane_era,), (retired.name,), {})
    for name in (retired.name, lane_era):
        row = _registry_row(cur, name)
        assert row["retired_at"] is None and row["due_at"] >= now + timedelta(seconds=900), name
    assert _registry_row(cur, lane_era)["lease_holder"] == "old"
    assert [r[:2] for r in _runs(cur, lane_era)] == [("old", "ok")]


def test_registry_at_is_when_the_row_joined_the_runner_and_a_later_boot_keeps_it(cur: Any) -> None:
    """Health (migration 682) judges a row that joined after a gap on its runs since: a boot re-registering a live row
    must not move that moment, and an un-retire or an adoption must."""
    _now(cur)
    live, retired = _entry(), _entry()
    job_lease.register(cur.connection, [live, retired])
    lane_era = _job(cur, due=None)
    cur.execute("UPDATE scheduled_jobs SET registry_at = now() - interval '3 days' WHERE job_name = ANY(%s)",
                ([live.name, retired.name],))
    cur.execute("UPDATE scheduled_jobs SET retired_at = now() - interval '1 day' WHERE job_name = %s",
                (retired.name,))
    job_lease.register(cur.connection, [live, retired, _entry(lane_era)])
    cur.execute("SELECT job_name, now() - registry_at FROM scheduled_jobs WHERE job_name = ANY(%s)",
                ([live.name, retired.name, lane_era],))
    assert dict(cur.fetchall()) == {live.name: timedelta(days=3), retired.name: timedelta(0),
                                    lane_era: timedelta(0)}


def test_a_row_the_database_refuses_is_skipped_and_its_siblings_still_land(cur: Any) -> None:
    _now(cur)
    bad, good = _entry(cpus=0), _entry()  # cpus 0 breaks 675's declarations_positive
    result = job_lease.register(cur.connection, [bad, good])
    assert result.inserted == (good.name,) and list(result.skipped) == [bad.name]
    assert "CheckViolation" in result.skipped[bad.name]
    cur.execute("SELECT count(*) FROM scheduled_jobs WHERE job_name = %s", (bad.name,))
    assert cur.fetchone() == (0,)


def test_retire_touches_only_rows_this_runner_registered(cur: Any) -> None:
    _now(cur)
    kept, gone = _entry(), _entry()
    github = _entry(host=jobs.GITHUB)
    job_lease.register(cur.connection, [kept, gone])
    job_lease.register(cur.connection, [github], runner=jobs.GITHUB)
    lane = f"test-lane-{uuid.uuid4().hex[:12]}"
    with job_lease.held(cur.connection, lane, cadence="15 minutes", runner="job-runner") as acquired:
        assert acquired
    assert job_lease.retire(cur.connection, [kept.name]) == [gone.name]
    cur.execute("SELECT job_name, retired_at IS NOT NULL FROM scheduled_jobs WHERE job_name = ANY(%s)"
                " ORDER BY job_name", ([kept.name, gone.name, github.name, lane],))
    assert dict(cur.fetchall()) == {kept.name: False, gone.name: True, github.name: False, lane: False}
    with job_lease.held(cur.connection, lane, cadence="15 minutes") as acquired:
        assert acquired, "a lane's row is never retired"
    assert kept.name in job_lease.retire(cur.connection, []), "an empty registry retires every owned row"


def _open_lapsed(cur: Any, name: str, holder: str = "A") -> None:
    cur.execute("INSERT INTO job_runs (job_name, holder) VALUES (%s, %s)", (name, holder))
    cur.execute("UPDATE scheduled_jobs SET lease_holder = %s, lease_expires_at = now() - interval '1 second'"
                " WHERE job_name = %s", (holder, name))


def _registered_job(cur: Any, **entry: Any) -> str:
    job = _entry(**entry)
    job_lease.register(cur.connection, [job])
    cur.execute("UPDATE scheduled_jobs SET due_at = now() + interval '1 day' WHERE job_name = %s", (job.name,))
    return job.name


def test_a_lost_run_is_re_run_once_then_backs_off(cur: Any) -> None:
    now = _now(cur)
    name = _registered_job(cur, offset_s=_offset_away(now))
    _open_lapsed(cur, name)
    assert job_lease.reap_lost(cur.connection, names=[name], on_hour=[]) == [name]
    row = _row(cur, name)
    assert (row["due_at"], row["last_outcome"], row["consecutive_failures"], row["lease_holder"]) == (
        now, "lost", 1, None)
    assert [r[1] for r in _runs(cur, name)] == ["lost"]
    _open_lapsed(cur, name, holder="B")
    assert job_lease.reap_lost(cur.connection, names=[name], on_hour=[]) == [name]
    row = _row(cur, name)
    assert (row["due_at"], row["consecutive_failures"]) == (now + timedelta(seconds=120), 2), "a second loss in a row"
    cur.execute("INSERT INTO job_runs (job_name, holder, ended_at, outcome) VALUES (%s, 'C', now(), 'ok')", (name,))
    _open_lapsed(cur, name, holder="D")
    job_lease.reap_lost(cur.connection, names=[name], on_hour=[])
    assert _row(cur, name)["due_at"] == now, "an ok run in between (same now(), a later id) makes it a first loss"


def test_a_db_heavy_lost_run_waits_for_its_own_hour(cur: Any) -> None:
    now = _now(cur)
    name = _registered_job(cur, every_s=86400, offset_s=3 * 3600)
    _open_lapsed(cur, name)
    job_lease.reap_lost(cur.connection, names=[name], on_hour=[name])
    due_at = _row(cur, name)["due_at"]
    assert (due_at.hour, due_at.minute) == (3, 0) and now < due_at <= now + timedelta(days=1)


def test_the_reaper_closes_lost_runs_it_does_not_reschedule(cur: Any) -> None:
    """A paused row's run closes too; a retired row and one this deployment does not name keep their schedule."""
    now = _now(cur)
    paused, retired, foreign = (_registered_job(cur) for _ in range(3))
    cur.execute("UPDATE scheduled_jobs SET paused_until = now() + interval '1 day', pause_note = 'drill',"
                " paused_by = 'operator' WHERE job_name = %s", (paused,))
    cur.execute("UPDATE scheduled_jobs SET retired_at = now() WHERE job_name = %s", (retired,))
    before = {n: _row(cur, n)["due_at"] for n in (paused, retired, foreign)}
    for name in before:
        _open_lapsed(cur, name)
    lane = _job(cur, due=None)
    _open_lapsed(cur, lane)
    assert sorted(job_lease.reap_lost(cur.connection, names=[paused], on_hour=[])) == sorted(before)
    assert [r[1] for r in _runs(cur, lane)] == [None], "a lane-era row (registry_at NULL) is never reaped"
    for name in before:
        assert [r[1] for r in _runs(cur, name)] == ["lost"], name
    assert _row(cur, paused)["due_at"] == now, "due again, and claimed once the pause ends"
    assert {n: _row(cur, n)["due_at"] for n in (retired, foreign)} == {n: before[n] for n in (retired, foreign)}


def test_a_live_run_is_never_reaped(cur: Any) -> None:
    _now(cur)
    name = _registered_job(cur)
    cur.execute("UPDATE scheduled_jobs SET due_at = now() - interval '1 second' WHERE job_name = %s", (name,))
    lease = job_lease.claim(cur.connection, name, holder="A")
    assert lease is not None
    assert job_lease.reap_lost(cur.connection, names=[name], on_hour=[]) == []
    assert _runs(cur, name)[0][1:3] == (None, True)


def test_due_lists_free_reconciled_rows_oldest_first(cur: Any) -> None:
    _now(cur)
    older, newer, requested, paused, open_run, sibling, unregistered = (
        _registered_job(cur, resource="test-heavy" if i == 5 else None) for i in range(7))
    for name, ago in ((older, "2 hours"), (newer, "1 hour"), (paused, "3 hours"), (open_run, "4 hours"),
                      (sibling, "5 hours"), (unregistered, "6 hours")):
        cur.execute("UPDATE scheduled_jobs SET due_at = now() - %s::interval WHERE job_name = %s", (ago, name))
    cur.execute("UPDATE scheduled_jobs SET requested_at = now() - interval '90 minutes', requested_by = 'op',"
                " paused_until = now() + interval '1 day', pause_note = 'drill', paused_by = 'op'"
                " WHERE job_name = %s", (requested,))
    cur.execute("UPDATE scheduled_jobs SET paused_until = now() + interval '1 day', pause_note = 'drill',"
                " paused_by = 'op' WHERE job_name = %s", (paused,))
    _open_lapsed(cur, open_run)  # a lapsed run belongs to the reaper, not to the next claim
    holder = _registered_job(cur, resource="test-heavy")
    cur.execute("INSERT INTO job_runs (job_name, holder, resource) VALUES (%s, 'A', 'test-heavy')", (holder,))
    cur.execute("UPDATE scheduled_jobs SET registry_at = NULL WHERE job_name = %s", (unregistered,))
    names = [older, newer, requested, paused, open_run, sibling, unregistered, holder]
    assert job_lease.due(cur.connection, names) == [older, requested, newer]


def test_a_request_is_filed_once_and_only_for_a_runner_row(cur: Any) -> None:
    _now(cur)
    name = _registered_job(cur)
    assert job_lease.request(cur.connection, name, ["--limit", "7"], by="github:op") is None
    row = _row(cur, name)
    assert (row["requested_args"], row["requested_by"], row["requested_at"] is not None) == (
        ["--limit", "7"], "github:op", True)
    refusal = job_lease.request(cur.connection, name, [], by="cli:other")
    assert refusal and refusal.startswith("a request by github:op has waited since ")
    lane = _job(cur, due=None)
    assert "not a job-runner job" in (job_lease.request(cur.connection, lane, [], by="op") or "")
    assert "no row" in (job_lease.request(cur.connection, "test-job-missing", [], by="op") or "")
    assert job_lease.request(cur.connection, _registered_job(cur), [], by="op") is None
    assert _row(cur, name)["requested_args"] == ["--limit", "7"]


def test_a_pause_is_set_and_lifted_on_any_live_row_a_lane_s_included(cur: Any) -> None:
    now = _now(cur)
    name = _registered_job(cur)
    until = now + timedelta(days=2)
    assert job_lease.pause(cur.connection, name, until=until, note="drill", by="cli:op") is None
    cur.execute("SELECT paused_until, pause_note, paused_by FROM scheduled_jobs WHERE job_name = %s", (name,))
    assert cur.fetchone() == (until, "drill", "cli:op")
    assert job_lease.pause(cur.connection, name, until=None, note="lifted", by="cli:other") is None
    cur.execute("SELECT paused_until, paused_by FROM scheduled_jobs WHERE job_name = %s", (name,))
    assert cur.fetchone() == (now, "cli:other"), "lifted now, and who lifted it is recorded"
    lane = f"test-lane-{uuid.uuid4().hex[:12]}"
    with job_lease.held(cur.connection, lane, cadence="15 minutes"):
        pass
    assert job_lease.pause(cur.connection, lane, until=until, note="drill", by="op") is None
    assert job_lease.pause(cur.connection, "test-job-missing", until=until, note="x", by="op") == (
        "test-job-missing has no row")


def test_the_heartbeat_is_one_row_per_replica_and_replaced_peers_are_forgotten(cur: Any) -> None:
    now = _now(cur)
    prefix = f"test-runner-{uuid.uuid4().hex[:6]}/"
    me, peer, old, stale = (prefix + s for s in ("me", "peer", "old", "stale"))
    job_lease.beat(cur.connection, me, now, {"deployment": "d2", "children": []})
    job_lease.beat(cur.connection, peer, now, {"deployment": "d2"})
    job_lease.beat(cur.connection, old, now, {"deployment": "d1"})
    job_lease.beat(cur.connection, stale, now, {"deployment": "d2"})
    cur.execute("UPDATE worker_heartbeats SET beat_at = now() - interval '61 minutes' WHERE worker = %s", (stale,))
    cur.execute("SELECT details FROM worker_heartbeats WHERE worker = %s", (me,))
    assert cur.fetchone() == ({"deployment": "d2", "children": []},)
    assert job_lease.forget_replaced(cur.connection, prefix=prefix, keep=me, deployment="d2",
                                     older_than_s=3600) == 2
    cur.execute("SELECT worker FROM worker_heartbeats WHERE starts_with(worker, %s) ORDER BY 1", (prefix,))
    assert cur.fetchall() == [(me,), (peer,)]
    job_lease.forget(cur.connection, me)
    cur.execute("SELECT worker FROM worker_heartbeats WHERE starts_with(worker, %s)", (prefix,))
    assert cur.fetchall() == [(peer,)]


def test_the_prune_drops_closed_runs_past_ninety_days_and_never_an_open_one(cur: Any) -> None:
    _now(cur)
    name = _registered_job(cur)
    cur.execute("INSERT INTO job_runs (job_name, holder, started_at, ended_at, outcome) VALUES"
                " (%s, 'A', now() - interval '100 days', now() - interval '91 days', 'ok'),"
                " (%s, 'A', now() - interval '89 days', now() - interval '89 days', 'ok')", (name, name))
    cur.execute("INSERT INTO job_runs (job_name, holder, started_at) VALUES (%s, 'A', now() - interval '200 days')",
                (name,))
    assert job_lease.prune_runs(cur.connection) >= 1
    assert sorted((r[1] or "", r[2]) for r in _runs(cur, name)) == [("", True), ("ok", False)]


def test_rows_lists_each_row_with_its_latest_run(cur: Any) -> None:
    _now(cur)
    name = _registered_job(cur)
    cur.execute("INSERT INTO job_runs (job_name, holder, ended_at, outcome, exit_code) VALUES"
                " (%s, 'A', now(), 'failed', 1), (%s, 'B', now(), 'ok', 0)", (name, name))
    row = next(r for r in job_lease.rows(cur.connection) if r["job_name"] == name)
    assert (row["runner"], row["registered"], row["outcome"], row["exit_code"]) == ("job-runner", True, "ok", 0)


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
    """The alias view is ungated on purpose (tests/_admin_gate_registry.py UNGATED, guard 'acl'): its ACL is the
    guard, so the ACL is asserted here."""
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


def _registered_committed(conn: Any, n: int, *, resource: str | None = None) -> list[str]:
    names = [f"test-race-{uuid.uuid4().hex[:12]}" for _ in range(n)]
    job_lease.register(conn, [_entry(name, resource=resource) for name in names])
    with conn.cursor() as cur:
        cur.execute("UPDATE scheduled_jobs SET due_at = now() - interval '1 second' WHERE job_name = ANY(%s)",
                    (names,))
    return names


def test_two_holders_racing_for_one_due_job_get_one_run() -> None:
    a, b = _sessions(2)
    [name] = _registered_committed(a, 1)
    try:
        result: dict[str, Any] = {}
        with a.transaction():
            assert job_lease.claim(a, name, holder="A") is not None
            racer = threading.Thread(target=lambda: result.update(lease=job_lease.claim(b, name, holder="B")))
            racer.start()
            racer.join(timeout=1)
            assert racer.is_alive(), "B's compare-and-set waits on A's row lock"
        racer.join(timeout=15)
        assert result == {"lease": None}
        with a.cursor() as cur:
            cur.execute("SELECT holder FROM job_runs WHERE job_name = %s", (name,))
            assert cur.fetchall() == [("A",)]
    finally:
        _cleanup(a, [name])
        a.close()
        b.close()


def test_the_reaper_never_waits_on_a_claim_s_locks_and_due_skips_what_it_skipped() -> None:
    """B's claim on a sibling closes X's lapsed run and then waits on X's row, which C holds: the reaper must skip
    both locks at once (a reaper that waited would form a cycle with the claim), and due() must not list X."""
    resource = f"test-race-{uuid.uuid4().hex[:8]}"
    a, b, c = _sessions(3)
    x, y = _registered_committed(a, 2, resource=resource)
    with a.cursor() as cur:
        cur.execute("INSERT INTO job_runs (job_name, holder, resource) VALUES (%s, 'dead', %s)", (x, resource))
        cur.execute("UPDATE scheduled_jobs SET lease_holder = 'dead', lease_expires_at = now() - interval '1 second',"
                    " due_at = now() + interval '1 day' WHERE job_name = %s", (x,))
    try:
        result: dict[str, Any] = {}
        with c.transaction(), c.cursor() as cur:
            cur.execute("SELECT 1 FROM scheduled_jobs WHERE job_name = %s FOR UPDATE", (x,))
            racer = threading.Thread(target=lambda: result.update(lease=job_lease.claim(b, y, holder="B")))
            racer.start()
            racer.join(timeout=1)
            assert racer.is_alive(), "B's claim waits on C's lock on X's row"
            started = time.monotonic()
            assert job_lease.reap_lost(a, names=[x, y], on_hour=[]) == []
            assert job_lease.due(a, [x, y]) == []
            assert time.monotonic() - started < job_lease.STATEMENT_TIMEOUT_S
        racer.join(timeout=15)
        assert result["lease"] is not None, "no deadlock: B's claim lands once C lets go"
        with a.cursor() as cur:
            cur.execute("SELECT job_name, holder, outcome FROM job_runs WHERE resource = %s ORDER BY id", (resource,))
            assert cur.fetchall() == [(x, "dead", "lost"), (y, "B", None)]
    finally:
        _cleanup(a, [x, y])
        for conn in (a, b, c):
            conn.close()


def test_a_register_attempt_that_cannot_lock_a_row_fails_whole_and_skips_nothing() -> None:
    """A lock held past the statement timeout is transient: the attempt raises (the runner retries next tick)
    instead of skipping the entry until the next deploy."""
    a, c = _sessions(2)
    [locked] = _registered_committed(a, 1)
    fresh = _entry()
    try:
        with c.transaction(), c.cursor() as cur:
            cur.execute("SELECT 1 FROM scheduled_jobs WHERE job_name = %s FOR UPDATE", (locked,))
            with pytest.raises(psycopg.errors.QueryCanceled):
                job_lease.register(a, [fresh, _entry(locked)])
        with a.cursor() as cur:
            cur.execute("SELECT count(*) FROM scheduled_jobs WHERE job_name = %s", (fresh.name,))
            assert cur.fetchone() == (0,), "the whole attempt rolled back"
    finally:
        _cleanup(a, [locked, fresh.name])
        a.close()
        c.close()

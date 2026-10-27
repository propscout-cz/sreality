"""The job runner's tick on the replayed schema (Actions exit W2, #2024): the SQL decides each case.

`Runner` with a stand-in spawner (tests/scraper/test_job_runner.py) over the real `PgLedger` and `scraper.job_lease`:
claim, pause, request, more-remains, failure backoff, the lost-run retry, the registry's upsert and retirement at
boot (AC1), one real child's complete run record (AC2), and a deploy stop's interrupted release (AC3).

Runs in CI's migrations job (`TEST_DATABASE_URL`, `DB_RAILS_REQUIRED=1`). Every test but the committed one rolls back,
with now() frozen at the transaction's start; the fake clock is patched into `job_lease.clock`, so the lease's fence
and the guard read one clock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
import pytest

from scraper import job_lease, job_process, jobs
from scraper.job_process import Child, Guard
from scraper.job_runner import Finished, PgLedger, Runner
from scraper.jobs import Job
from tests._live_property import REQUIRED_DB, cur, db_url  # noqa: F401 -- `cur` is the fixture
from tests.scraper.test_job_runner import CHILD, Clock, Script, StandIn, _kill_quietly, _wait_for

pytestmark = REQUIRED_DB


def _entry(*args: str, every_s: int = 3600, offset_s: int = 600, timeout_s: int = 900, db_heavy: bool = False,
           resource: str | None = None) -> Job:
    return Job(f"test-job-{uuid.uuid4().hex[:12]}", (CHILD, *args), every_s, offset_s, timeout_s, 256, 1,
               jobs.RUNNER, resource, db_heavy)


def _offset_away(cur: Any, every_s: int = 3600) -> int:
    """An offset whose next slot is half a cadence after the frozen now() (it opens the test transaction): no wait a
    test asserts is then capped by the next slot, whatever the time of day."""
    cur.execute("SELECT extract(epoch from now())::bigint")
    return (cur.fetchone()[0] + every_s // 2) % every_s


@dataclass
class Live:
    cur: Any
    clock: Clock
    standin: StandIn
    guard: Guard
    runner: Runner
    stop: list[bool]

    def tick(self, seconds: float = 6.0) -> None:
        """Past the next claim pass, with a guard pass at the new time."""
        self.clock.advance(seconds)
        self.guard.run_once(self.clock())
        self.runner.tick()

    def drive(self, seconds: float, *, step: float = 30.0) -> None:
        """Through a long stretch in steps short of the lease's renewal, so the fence never passes."""
        until = self.clock() + seconds
        while self.clock() < until:
            self.tick(min(step, until - self.clock()))

    def row(self, name: str) -> dict[str, Any]:
        self.cur.execute("SELECT due_at, due_at - now() AS wait, last_outcome, consecutive_failures, lease_holder,"
                         " last_error, requested_at FROM scheduled_jobs WHERE job_name = %s", (name,))
        cols = [d.name for d in self.cur.description]
        return dict(zip(cols, self.cur.fetchone(), strict=True))

    def runs(self, name: str) -> list[tuple[Any, ...]]:
        self.cur.execute("SELECT outcome, ended_at IS NULL, args, requested_by, exit_code FROM job_runs"
                         " WHERE job_name = %s ORDER BY id", (name,))
        return self.cur.fetchall()

    def due_now(self, name: str, ago: str = "1 second") -> None:
        self.cur.execute("UPDATE scheduled_jobs SET due_at = now() - %s::interval WHERE job_name = %s", (ago, name))


def _live(cur: Any, monkeypatch: pytest.MonkeyPatch, registry: Sequence[Job],
          scripts: Mapping[str, Script] | None = None, *, spawner: Callable[..., Child] | None = None,
          max_children: int = 3, conn: psycopg.Connection | None = None, registered: bool = False,
          grace_s: float = job_process.GRACE_S) -> Live:
    cur.execute("SELECT now()")  # opens the test transaction, so no job_lease call commits
    clock = Clock()
    monkeypatch.setattr(job_lease, "clock", clock)
    standin = StandIn(clock, scripts)
    stop = [False]
    guard = Guard(clock=clock, grace_s=grace_s, stopping=lambda: stop[0], sample=standin.sample,
                  term=standin.term, kill=standin.kill)
    if spawner is not None:
        guard = Guard(clock=clock, grace_s=grace_s, stopping=lambda: stop[0])
    ledger = PgLedger(lambda: conn or cur.connection, holder=f"test-holder-{uuid.uuid4().hex[:6]}")
    runner = Runner(registry, ledger=ledger, spawner=spawner or standin, guard=guard, clock=clock, env={},
                    max_children=max_children, registered=registered)
    return Live(cur, clock, standin, guard, runner, stop)


def _ran(live: Live) -> list[str]:
    return [label.split("#")[0] for _, _, label in live.standin.spawned]


def test_a_due_row_spawns_its_exact_argv_and_opens_one_run(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L1, and L8: the first tick registers every entry; a registered row is not due before its first slot."""
    entry = _entry("--exit", "0")
    live = _live(cur, monkeypatch, [entry])
    live.runner.tick()
    assert live.runner.registered and _ran(live) == []
    cur.execute("SELECT argv, runner, registry_at IS NOT NULL, due_at >= now() + interval '900 seconds'"
                " FROM scheduled_jobs WHERE job_name = %s", (entry.name,))
    assert cur.fetchone() == ([CHILD, "--exit", "0"], "job-runner", True, True)
    live.due_now(entry.name)
    live.tick()
    [(command, env, _)] = live.standin.spawned
    assert command[-3:] == [CHILD, "--exit", "0"] and env["JOB_CPUS"] == "1"
    assert live.runs(entry.name) == [(None, True, None, None, None)]


def test_a_paused_row_does_not_spawn(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L2."""
    entry = _entry()
    live = _live(cur, monkeypatch, [entry])
    live.runner.tick()
    live.due_now(entry.name)
    assert job_lease.pause(cur.connection, entry.name, until=datetime(2099, 1, 1).astimezone(), note="drill",
                           by="test") is None
    live.tick()
    assert _ran(live) == [] and live.runs(entry.name) == []


def test_a_request_runs_on_a_paused_row_with_its_args_and_leaves_the_schedule(
        cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L3."""
    entry = _entry("--exit", "0")
    live = _live(cur, monkeypatch, [entry], {entry.name: Script(code=0, at=1010.0)})
    live.runner.tick()
    before = live.row(entry.name)["due_at"]
    job_lease.pause(cur.connection, entry.name, until=datetime(2099, 1, 1).astimezone(), note="drill", by="test")
    assert job_lease.request(cur.connection, entry.name, ["--lines", "3"], by="github:op") is None
    live.tick()
    [(command, _, _)] = live.standin.spawned
    assert command[-5:] == [CHILD, "--exit", "0", "--lines", "3"]
    live.tick()
    assert live.runs(entry.name) == [("ok", False, ["--lines", "3"], "github:op", 0)]
    assert live.row(entry.name)["due_at"] == before, "a pure request run never moves the schedule"


def test_a_request_on_a_due_row_is_one_run_that_takes_the_slot(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L3: claimed once, the slot advances, and the next claim pass does not run the schedule again."""
    entry = _entry("--exit", "0")
    live = _live(cur, monkeypatch, [entry], {entry.name: Script(code=0, at=1010.0)})
    live.runner.tick()
    live.due_now(entry.name)
    job_lease.request(cur.connection, entry.name, [], by="github:op")
    live.tick()
    live.tick()
    live.tick()
    assert len(live.standin.spawned) == 1 and [r[0] for r in live.runs(entry.name)] == ["ok"]
    row = live.row(entry.name)
    assert timedelta(0) < row["wait"] <= timedelta(hours=1) and row["due_at"].minute == 10


def test_a_more_remains_exit_is_ok_and_due_again_in_a_minute(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L4."""
    entry = _entry()
    live = _live(cur, monkeypatch, [entry], {entry.name: Script(code=75, at=1010.0)})
    live.runner.tick()
    live.due_now(entry.name)
    live.tick()
    live.tick()
    row = live.row(entry.name)
    assert (row["last_outcome"], row["wait"], row["consecutive_failures"]) == ("ok", timedelta(seconds=60), 0)
    assert live.runs(entry.name)[-1][0::4] == ("ok", 75)


def test_failures_back_off_and_a_db_heavy_job_waits_for_its_own_hour(
        cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L5."""
    plain = _entry(offset_s=_offset_away(cur))
    heavy = _entry(every_s=86400, offset_s=3 * 3600, db_heavy=True)
    live = _live(cur, monkeypatch, [plain, heavy], {plain.name: Script(code=1, at=0.0),
                                                     heavy.name: Script(code=1, at=0.0)})
    live.runner.tick()
    live.due_now(plain.name)
    live.due_now(heavy.name)
    live.tick()
    live.tick()
    row = live.row(plain.name)
    assert (row["last_outcome"], row["wait"], row["consecutive_failures"], row["last_error"]) == (
        "failed", timedelta(seconds=60), 1, "exit 1")
    due_at = live.row(heavy.name)["due_at"]
    assert (due_at.hour, due_at.minute) == (3, 0) and timedelta(0) < live.row(heavy.name)["wait"] <= timedelta(days=1)
    live.due_now(plain.name)
    live.tick()
    live.tick()
    assert live.row(plain.name)["wait"] == timedelta(seconds=120), "the second failure in a row waits twice as long"


@pytest.mark.parametrize("every_s,offset_s,timeout_s,db_heavy,expected", [
    (3600, None, 900, False, "15 min"),  # None: the next slot half a cadence away
    (30 * 86400, None, 900, False, "15 min"),  # rent_map's cadence: retries, never waits a month
    (91 * 86400, 6300000, 7200, True, "22:00"),  # map_overlays: the next day at its hour
])
def test_a_timeout_is_a_failure_that_retries_no_sooner_than_its_timeout(
        cur: Any, monkeypatch: pytest.MonkeyPatch, every_s: int, offset_s: int | None, timeout_s: int,
        db_heavy: bool, expected: str) -> None:
    """L5: the runner stopped it at timeout_s, so it is recorded `timeout` whatever it exited."""
    offset_s = _offset_away(cur, every_s) if offset_s is None else offset_s
    entry = _entry(every_s=every_s, offset_s=offset_s, timeout_s=timeout_s, db_heavy=db_heavy)
    live = _live(cur, monkeypatch, [entry], {entry.name: Script(code_on_term=0)}, grace_s=5.0)
    live.runner.tick()
    live.due_now(entry.name)
    live.tick()
    live.drive(timeout_s + 1)
    row = live.row(entry.name)
    assert (row["last_outcome"], row["consecutive_failures"]) == ("timeout", 1)
    assert row["last_error"] == f"stopped at timeout_s={timeout_s}; exit 0"
    if expected == "15 min":
        assert row["wait"] == timedelta(seconds=timeout_s)
    else:
        assert (row["due_at"].hour, row["due_at"].minute) == (22, 0) and row["wait"] <= timedelta(days=1)


def test_a_lost_run_is_re_run_once_in_the_same_tick_then_backs_off(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L6: a dead holder's lapsed run is closed and re-claimed at once; a second loss in a row waits."""
    entry = _entry(offset_s=_offset_away(cur))
    live = _live(cur, monkeypatch, [entry], {entry.name: Script()})
    live.runner.tick()
    cur.execute("INSERT INTO job_runs (job_name, holder) VALUES (%s, 'dead')", (entry.name,))
    cur.execute("UPDATE scheduled_jobs SET lease_holder = 'dead', lease_expires_at = now() - interval '1 second'"
                " WHERE job_name = %s", (entry.name,))
    live.tick()
    assert [r[:2] for r in live.runs(entry.name)] == [("lost", False), (None, True)]
    assert len(live.standin.spawned) == 1, "re-run in the same tick"
    cur.execute("UPDATE scheduled_jobs SET lease_expires_at = now() - interval '1 second' WHERE job_name = %s",
                (entry.name,))
    restarted = _live(cur, monkeypatch, [entry], {entry.name: Script()})  # the replica died and came back
    restarted.runner.tick()
    assert [r[:2] for r in restarted.runs(entry.name)] == [("lost", False), ("lost", False)]
    assert restarted.standin.spawned == [], "a second loss in a row is not re-run at once"
    assert restarted.row(entry.name)["wait"] == timedelta(seconds=120)


def test_a_stale_release_or_renewal_never_touches_a_later_run_of_the_same_job(
        cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """One replica re-claims a job whose release it could not land: run 1's late renewal and release are refused,
    and run 2's lease stands (each claim is its own holder)."""
    entry = _entry()
    live = _live(cur, monkeypatch, [entry])
    live.runner.tick()
    ledger = live.runner.ledger
    live.due_now(entry.name)
    first = ledger.claim(entry.name)
    assert first is not None
    cur.execute("UPDATE scheduled_jobs SET lease_expires_at = now() - interval '1 second' WHERE job_name = %s",
                (entry.name,))
    assert ledger.reap_lost([entry.name], []) == [entry.name]
    second = ledger.claim(entry.name)
    assert second is not None
    assert ledger.renew(first) is False
    assert ledger.release(Finished(first, "ok", "slot", None, 0, None, "")) is False
    assert live.row(entry.name)["lease_holder"] == second.holder
    assert ledger.renew(second) is True
    assert [r[:2] for r in live.runs(entry.name)] == [("lost", False), (None, True)]


def test_the_oldest_due_row_is_claimed_first(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L7."""
    newer, older = _entry(), _entry()
    live = _live(cur, monkeypatch, [newer, older], max_children=1)
    live.runner.tick()
    live.due_now(newer.name, "1 hour")
    live.due_now(older.name, "2 hours")
    live.tick()
    assert _ran(live) == [older.name]


def test_boot_retires_only_what_this_registry_registered_and_dropped(
        cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L9: a held() lane survives the runner's boot and still acquires."""
    kept, dropped = _entry(), _entry()
    lane = f"test-lane-{uuid.uuid4().hex[:12]}"
    cur.execute("SELECT now()")
    with job_lease.held(cur.connection, lane, cadence="15 minutes", runner="job-runner") as acquired:
        assert acquired
    _live(cur, monkeypatch, [kept, dropped]).runner.tick()
    _live(cur, monkeypatch, [kept]).runner.tick()
    cur.execute("SELECT job_name, retired_at IS NOT NULL FROM scheduled_jobs WHERE job_name = ANY(%s)",
                ([kept.name, dropped.name, lane],))
    assert dict(cur.fetchall()) == {kept.name: False, dropped.name: True, lane: False}
    with job_lease.held(cur.connection, lane, cadence="15 minutes") as acquired:
        assert acquired


def test_a_real_child_s_run_record_is_complete(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L10 (AC2): exit, outcome, peak and tail in job_runs; the lease free; last_error carries no log text."""
    entry = _entry("--ignore-term", "--hold-mb", "64", "--lines", "100", "--grandchild", timeout_s=1)
    live = _live(cur, monkeypatch, [entry], spawner=job_process.spawn_process, grace_s=1.0)
    live.runner.tick()
    live.due_now(entry.name)
    live.clock.advance(6.0)
    live.runner.tick()
    [run] = live.runner.running.values()
    pgid = run.child.pid
    try:
        _wait_for(lambda: "ready" in run.child.tail())
        live.clock.advance(1.5)
        live.guard.run_once(live.clock())
        live.clock.advance(1.5)
        live.guard.run_once(live.clock())
        _wait_for(lambda: not live.runner.running and not live.runner.finished, tick=live.runner.tick)
        cur.execute("SELECT ended_at IS NOT NULL, outcome, exit_code, peak_rss_mb, log_tail FROM job_runs"
                    " WHERE job_name = %s", (entry.name,))
        ended, outcome, exit_code, peak, tail = cur.fetchone()
        assert (ended, outcome, exit_code) == (True, "timeout", -9)
        assert 64 <= peak < 200
        assert tail.splitlines()[-1] == "ready" and len(tail.splitlines()) == 64
        row = live.row(entry.name)
        assert (row["lease_holder"], row["last_outcome"], row["last_error"]) == (
            None, "timeout", "stopped at timeout_s=1; exit -9")
    finally:
        _kill_quietly(pgid)


def test_a_deploy_stop_releases_interrupted_and_due_at_once(cur: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """L11 (AC3), with its DB-heavy twin: an interrupted run is due again at once for every job (R-a (b))."""
    plain, heavy = _entry(), _entry(every_s=86400, offset_s=3 * 3600, db_heavy=True)
    live = _live(cur, monkeypatch, [plain, heavy])
    live.runner.tick()
    live.due_now(plain.name)
    live.due_now(heavy.name)
    live.tick()
    assert len(live.standin.spawned) == 2
    live.stop[0] = True
    live.tick(1.0)
    live.tick(1.0)
    assert live.runner.tick() is True
    cur.execute("SELECT now()")
    now = cur.fetchone()[0]
    for name in (plain.name, heavy.name):
        row = live.row(name)
        assert (row["last_outcome"], row["due_at"], row["consecutive_failures"]) == ("interrupted", now, 0), name
        assert live.runs(name)[-1][:2] == ("interrupted", False)


def test_a_second_holder_claims_an_interrupted_run_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """L11, committed: the new deployment's claim takes the row the old one released on its stop."""
    a = psycopg.connect(db_url(), autocommit=True)
    b = psycopg.connect(db_url(), autocommit=True)
    entry = _entry()
    try:
        job_lease.register(a, [entry])
        with a.cursor() as c:
            c.execute("UPDATE scheduled_jobs SET due_at = now() - interval '1 second' WHERE job_name = %s",
                      (entry.name,))
        with a.cursor() as c:
            live = _live(c, monkeypatch, [entry], conn=a, registered=True)
            live.tick()
            assert len(live.standin.spawned) == 1
            live.stop[0] = True
            live.tick(1.0)
            live.tick(1.0)
        lease = job_lease.claim(b, entry.name, holder="new-deployment")
        assert lease is not None and lease.due_slot, "due at once, and claimed by the new holder"
    finally:
        with a.cursor() as c:
            c.execute("DELETE FROM job_runs WHERE job_name = %s", (entry.name,))
            c.execute("DELETE FROM scheduled_jobs WHERE job_name = %s", (entry.name,))
        a.close()
        b.close()

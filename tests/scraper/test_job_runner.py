"""The job runner's tick, guard and CLI with no database (Actions exit W2, #2024).

Every test but the real-child ones runs on a fake clock: a `FakeLedger` scripts the database and records each call
with the fake time and whether the child's leader was still alive, and a `StandIn` spawner scripts each child's
exit and records every signal the guard sends. The test calls `guard.run_once(t)` itself, standing in for the guard
thread. The real-child tests spawn `tests/scraper/_job_runner_child.py` and always kill its group in `finally`.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scraper import job_lease, job_process, job_runner, jobs
from scraper.job_process import Child, Exit, Guard
from scraper.job_runner import Finished, Runner
from scraper.jobs import Job

CHILD = "tests.scraper._job_runner_child"
ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


@dataclass
class Script:
    """One stand-in child: when it exits on its own, how it meets SIGTERM, what it reads as memory."""

    code: int = 0
    at: float | None = None  # fake time it exits on its own; None: never
    ignores_term: bool = False
    code_on_term: int = 143
    rss_mb: int | None = 100
    group_for_s: float = 0.0  # a straggler in D state keeps the group alive this long after the leader exits


class StandInChild:
    def __init__(self, pid: int, script: Script, clock: Clock) -> None:
        self.pid = pid
        self.script = script
        self.clock = clock
        self.code: int | None = None
        self.group_until: float | None = None
        self.reaped = False

    def end(self, code: int) -> None:
        if self.code is None:
            self.code = code
            self.group_until = self.clock() + self.script.group_for_s if self.script.group_for_s else None

    def alive(self) -> bool:
        if self.code is None and self.script.at is not None and self.clock() >= self.script.at:
            self.end(self.script.code)
        return self.code is None

    def exited(self) -> bool:
        return not self.alive()

    def reap(self) -> Exit:
        assert self.code is not None and not self.reaped, "reaped twice, or before it exited"
        self.reaped = True
        return Exit(self.code)

    def stragglers(self) -> int:
        return int(self.group_until is not None and self.clock() < self.group_until)

    def reader_done(self) -> bool:
        return self.code is not None

    def tail(self) -> str:
        return f"pid {self.pid} said goodbye"


class StandIn:
    """The spawner, the guard's term/kill/sample, and the record of every signal: (fake t, signal, target, pid)."""

    def __init__(self, clock: Clock, scripts: Mapping[str, Script] | None = None) -> None:
        self.clock = clock
        self.scripts = dict(scripts or {})
        self.children: dict[int, StandInChild] = {}
        self.by_label: dict[str, StandInChild] = {}
        self.spawned: list[tuple[list[str], dict[str, str], str]] = []
        self.signals: list[tuple[float, str, str, int]] = []
        self.fail: BaseException | None = None
        self._next_pid = 5000

    def __call__(self, command: Sequence[str], env: Mapping[str, str], label: str,
                 adopt: Callable[[Child], None]) -> Child:
        if self.fail is not None:
            raise self.fail
        self._next_pid += 1
        child = StandInChild(self._next_pid, self.scripts.get(label.split("#")[0], Script()), self.clock)
        self.children[child.pid] = child
        self.by_label[label] = child
        self.spawned.append((list(command), dict(env), label))
        adopt(child)
        return child

    def term(self, pid: int) -> None:
        self.signals.append((self.clock(), "TERM", "leader", pid))
        child = self.children[pid]
        if child.alive() and not child.script.ignores_term:
            child.end(child.script.code_on_term)

    def kill(self, pid: int) -> None:
        self.signals.append((self.clock(), "KILL", "group", pid))
        child = self.children[pid]
        if child.alive():
            child.end(-9)
            child.group_until = None  # a SIGKILL'd leader's group dies with it; a D-state straggler outlives it

    def sample(self, pid: int) -> int | None:
        child = self.children[pid]
        return child.script.rss_mb if child.alive() else None

    def pid_of(self, job: str) -> int:
        return next(c.pid for label, c in self.by_label.items() if label.startswith(f"{job}#"))


@dataclass
class FakeLedger:
    """Scripted answers; every call recorded as (event, args, fake t, leader alive)."""

    clock: Clock
    alive: Callable[[str], bool | None]  # by label "<job>#<run>": is the run's leader still alive?
    due_names: list[str] = field(default_factory=list)
    requests: dict[str, tuple[list[str], str]] = field(default_factory=dict)
    renew_result: bool | None = True
    release_result: bool | None = True
    up: bool = True
    down: bool = False  # the database is unreachable: every tick's latch drops at begin()
    events: list[tuple[str, object, float, bool | None]] = field(default_factory=list)
    on: dict[str, Callable[[], None]] = field(default_factory=dict)  # event -> run inside the call (a blocking DB)
    _run_id: int = 0

    def _record(self, event: str, args: object, label: str | None = None) -> None:
        if event in self.on:
            self.on[event]()
        self.events.append((event, args, self.clock(), self.alive(label) if label else None))

    def named(self, event: str) -> list[tuple[str, object, float, bool | None]]:
        return [e for e in self.events if e[0] == event]

    def begin(self, *, stopping: bool) -> None:
        self.up = not self.down
        self._record("begin", stopping)

    skipped: dict[str, str] = field(default_factory=dict)
    register_fails: bool = False  # a statement timeout, say: this call fails, the tick goes on
    due_fails: bool = False

    def register(self, entries: Sequence[Job]) -> job_lease.Registered | None:
        self._record("register", [j.name for j in entries])
        return None if self.register_fails else job_lease.Registered((), (), (), dict(self.skipped))

    def retire(self, names: Sequence[str]) -> list[str] | None:
        self._record("retire", list(names))
        return []

    def reap_lost(self, names: Sequence[str], on_hour: Sequence[str]) -> list[str] | None:
        self._record("reap_lost", (list(names), list(on_hour)))
        return []

    def due(self, names: Sequence[str]) -> list[str] | None:
        self._record("due", list(names))
        return None if self.due_fails else [n for n in self.due_names if n in names]

    def claim(self, name: str) -> job_lease.Lease | None:
        self._record("claim", name)
        self.due_names = [n for n in self.due_names if n != name]
        self._run_id += 1
        args, by = self.requests.pop(name, (None, None))
        return job_lease.Lease(name, "H", job_lease.TTL_S, self.clock() + job_lease.TTL_S - job_lease.FENCE_MARGIN_S,
                               run_id=self._run_id, args=args, requested_by=by, due_slot=by is None)

    def renew(self, lease: job_lease.Lease) -> bool | None:
        self._record("renew", lease.job_name)
        if self.renew_result:
            lease.fence_at = self.clock() + job_lease.TTL_S - job_lease.FENCE_MARGIN_S
        return self.renew_result

    def release(self, done: Finished) -> bool | None:
        self._record("release", done, f"{done.lease.job_name}#{done.lease.run_id}")
        return self.release_result

    def beat(self, details: Mapping[str, object]) -> None:
        self._record("beat", dict(details))

    def forget_replaced(self) -> None:
        self._record("forget_replaced", None)

    def forget(self) -> None:
        self._record("forget", None)

    def prune(self) -> None:
        self._record("prune", None)


def _job(name: str = "test_job", *, argv: tuple[str, ...] = (CHILD, "--exit", "0"), timeout_s: int = 600,
         cpus: int = 2, db_heavy: bool = False, resource: str | None = None) -> Job:
    return Job(name, argv, 3600, 0, timeout_s, 256, cpus, jobs.RUNNER, resource, db_heavy)


@dataclass
class Rig:
    clock: Clock
    standin: StandIn
    ledger: FakeLedger
    guard: Guard
    runner: Runner
    stop: list[bool]

    def releases(self) -> list[Finished]:
        return [e[1] for e in self.ledger.named("release") if isinstance(e[1], Finished)]


def _rig(registry: Sequence[Job], scripts: Mapping[str, Script] | None = None, *, due: Sequence[str] = (),
         **runner: object) -> Rig:
    clock = Clock()
    standin = StandIn(clock, scripts)
    ledger = FakeLedger(clock, lambda label: standin.by_label[label].alive() if label in standin.by_label else None,
                        due_names=list(due))
    stop = [False]
    guard = Guard(clock=clock, stopping=lambda: stop[0], sample=standin.sample, term=standin.term,
                  kill=standin.kill)
    return Rig(clock, standin, ledger, guard,
               Runner(registry, ledger=ledger, spawner=standin, guard=guard, clock=clock, env={"BASE": "1"},
                      **runner),  # type: ignore[arg-type]
               stop)


def test_a_due_entry_runs_its_exact_argv_and_is_released_only_after_the_reap() -> None:
    """N1, the tracer bullet: claim, spawn the registry's argv through the shim with JOB_CPUS, release ok."""
    rig = _rig([_job()], {"test_job": Script(code=0, at=1001.0)}, due=["test_job"])
    rig.runner.tick()
    [(command, env, label)] = rig.standin.spawned
    assert command == [sys.executable, "-c", job_process.OOM_SHIM, "-m", CHILD, "--exit", "0"]
    assert (env["JOB_CPUS"], env["JOB_NAME"], env["JOB_RUN_ID"], env["BASE"]) == ("2", "test_job", "1", "1")
    assert label == "test_job#1"
    rig.clock.advance(0.5)
    rig.runner.tick()
    assert rig.releases() == [], "nothing is released while the leader runs"

    rig.clock.advance(2)
    rig.runner.tick()
    [release] = rig.ledger.named("release")
    done = release[1]
    assert isinstance(done, Finished)
    assert (done.outcome, done.due, done.exit_code, done.error) == ("ok", "slot", 0, None)
    assert release[3] is False, "the release fires only after the leader exited and was reaped"
    assert rig.runner.running == {} and rig.runner.finished == []


def _c_worker_dead_s() -> float:
    text = (ROOT / "migrations" / "675_scheduled_jobs_expand.sql").read_text()
    match = re.search(r"c_worker_dead constant interval := interval '(\d+) minutes'", text)
    assert match, "675's c_worker_dead moved"
    return int(match.group(1)) * 60.0


def test_the_timing_constants_hold_together() -> None:
    """N2: raising a grace, a timeout or a budget, or shortening the drain, without the others fails here."""
    assert job_lease.RUNNER == jobs.RUNNER
    assert job_runner.HEARTBEAT_PREFIX == job_lease.RUNNER + "/"
    assert job_process.GRACE_S < jobs.STUCK_RUN_GRACE_S
    assert job_runner.CALL_MAX_S == job_runner.CONNECT_BUDGET_S + 3 * job_lease.STATEMENT_TIMEOUT_S
    assert job_runner.CONNECT_BUDGET_S >= 2 * job_runner.CONNECT_TIMEOUT_S
    drain = (job_process.GUARD_POLL_S + job_process.GRACE_S
             + 2 * (job_runner.STOP_PHASE_BUDGET_S + job_runner.CALL_MAX_S) + 2 * job_runner.TICK_S)
    assert drain < job_runner.DRAINING_S, f"a deploy's drain needs {drain} s of Railway's {job_runner.DRAINING_S}"
    assert job_runner.RENEW_EVERY_S + job_runner.CALL_MAX_S < job_lease.TTL_S - job_lease.FENCE_MARGIN_S
    assert job_process.LIVENESS_BOUND_S < _c_worker_dead_s()
    assert job_runner.MAX_CHILDREN == jobs.RUNNER_MAX_CONCURRENT
    assert job_runner.MORE_REMAINS_EXIT == 75


@pytest.mark.parametrize("reason,code,stopping,expected", [
    (None, None, False, ("failed", "backoff")),  # the spawn raised
    ("timeout", 0, False, ("timeout", "retry")),
    ("timeout", 75, False, ("timeout", "retry")),
    ("timeout", 143, False, ("timeout", "retry")),
    ("timeout", -9, True, ("timeout", "retry")),  # a deploy during the timeout's grace: the first cause wins
    (None, 0, False, ("ok", "slot")),
    ("deploy", 0, True, ("ok", "slot")),  # finished its work inside the drain
    (None, 75, False, ("ok", "more")),
    ("deploy", 143, True, ("interrupted", "now")),
    ("deploy", -9, True, ("interrupted", "now")),
    ("fence", -9, False, ("interrupted", "now")),
    (None, 1, True, ("interrupted", "now")),
    (None, 143, False, ("failed", "backoff")),  # a 143 nobody asked for is the job's own failure
    (None, -9, False, ("failed", "backoff")),  # a SIGKILL the runner did not send: likely the OOM killer (R-b)
    (None, -11, False, ("failed", "backoff")),
    (None, 1, False, ("failed", "backoff")),
])
def test_the_runner_s_own_stop_decides_before_the_exit_code(
        reason: job_process.Stop | None, code: int | None, stopping: bool, expected: tuple[str, str]) -> None:
    """N3: the settle table, with its DB-heavy twin (retries keep the job's own hour; an interruption does not)."""
    assert job_runner.settle(reason, code, stopping=stopping, db_heavy=False) == expected
    heavy = (expected[0], "own_hour" if expected[1] in ("backoff", "retry") else expected[1])
    assert job_runner.settle(reason, code, stopping=stopping, db_heavy=True) == heavy


def test_the_error_text_names_the_cause_and_never_the_log() -> None:
    job = _job(timeout_s=900)
    assert job_runner.cause(job, "timeout", -9, peak_mb=80, stragglers=0) == "stopped at timeout_s=900; exit -9"
    assert job_runner.cause(job, None, -9, peak_mb=80, stragglers=0) == (
        "SIGKILL not sent by the runner (likely the OOM killer); peak 80 MB of memory_mb 256")
    assert job_runner.cause(job, None, -11, peak_mb=None, stragglers=0) == "killed by SIGSEGV"
    assert job_runner.cause(job, None, -40, peak_mb=None, stragglers=0) == "killed by signal 40", (
        "a real-time signal has no enum name: a raise here would strand the run and trip the watchdog")
    assert job_runner.cause(job, None, 3, peak_mb=None, stragglers=0) == "exit 3"
    assert job_runner.cause(job, "deploy", 143, peak_mb=None, stragglers=0) == "deploy stop; exit 143"
    assert job_runner.cause(job, "fence", -9, peak_mb=None, stragglers=0) == "lease fenced; exit -9"
    assert job_runner.cause(job, None, 0, peak_mb=None, stragglers=0) is None
    assert job_runner.cause(job, None, 75, peak_mb=None, stragglers=0) is None
    assert job_runner.cause(job, "deploy", 0, peak_mb=None, stragglers=0) is None, "ok: the text follows the outcome"
    assert job_runner.cause(job, "fence", 75, peak_mb=None, stragglers=0) is None
    assert job_runner.cause(job, None, 1, peak_mb=None, stragglers=2) == (
        "exit 1; 2 processes outlived the leader by 10 s")


def test_a_run_killed_by_a_signal_with_no_name_still_settles_and_frees_its_slot() -> None:
    rig = _rig([_job()], {"test_job": Script(code=-40, at=1001.0)}, due=["test_job"])
    rig.runner.tick()
    rig.clock.advance(2)
    assert rig.runner.tick() is False
    [done] = rig.releases()
    assert (done.outcome, done.exit_code, done.error) == ("failed", -40, "killed by signal 40")
    assert rig.runner.running == {}


def test_a_request_s_args_follow_the_registry_argv_and_the_child_learns_its_run() -> None:
    """N4: argparse keeps a repeated flag's last value, so the request's own flags win."""
    job = _job(argv=("scripts.x", "--limit", "5"), cpus=3)
    assert job_runner.child_command(job, ["--limit", "7"])[-6:] == ["-m", "scripts.x", "--limit", "5", "--limit", "7"]
    assert job_runner.child_command(job, None)[-4:] == ["-m", "scripts.x", "--limit", "5"]
    env = job_runner.child_env(job, 42, {"PATH": "/bin", "JOB_CPUS": "99"})
    assert env == {"PATH": "/bin", "JOB_CPUS": "3", "JOB_NAME": "test_job", "JOB_RUN_ID": "42",
                   "PYTHONUNBUFFERED": "1", "MALLOC_ARENA_MAX": "2"}


def test_a_child_caps_glibcs_arenas_as_the_worker_does_unless_the_runner_sets_its_own() -> None:
    """glibc gives each thread its own arena and keeps what it freed (E949: the worker runs with 2); clip decodes and
    downloads on 20 threads beside torch's, inside its 2048 MB declaration."""
    job = _job(argv=("scripts.x",), cpus=1)
    assert job_runner.child_env(job, 1, {})["MALLOC_ARENA_MAX"] == "2"
    assert job_runner.child_env(job, 1, {"MALLOC_ARENA_MAX": "4"})["MALLOC_ARENA_MAX"] == "4"


def _drive(rig: Rig, until: float, *, step: float = 1.0, tick: bool = True) -> None:
    """Advance the fake clock to `until`, the guard passing every `step` and the runner ticking with it."""
    while rig.clock() < until:
        rig.clock.advance(min(step, until - rig.clock()))
        rig.guard.run_once(rig.clock())
        if tick:
            rig.runner.tick()


def _signals(rig: Rig) -> list[tuple[float, str, str]]:
    return [(t - 1000.0, sig, target) for t, sig, target, _ in rig.standin.signals]


def test_a_job_past_its_timeout_gets_sigterm_then_its_group_sigkill_after_the_grace() -> None:
    """N5: the leader's SIGTERM at timeout_s, the group's SIGKILL GRACE_S later, renewals all the while."""
    rig = _rig([_job(timeout_s=600)], {"test_job": Script(ignores_term=True)}, due=["test_job"])
    rig.runner.tick()
    _drive(rig, 1599.0)
    assert _signals(rig) == []
    _drive(rig, 1600.0)
    assert _signals(rig) == [(600.0, "TERM", "leader")], "SIGTERM goes to the leader only, at timeout_s"
    _drive(rig, 1689.0)
    assert _signals(rig) == [(600.0, "TERM", "leader")], "no SIGKILL before the grace ends"
    _drive(rig, 1690.0)
    assert _signals(rig)[1] == (690.0, "KILL", "group")
    renewals = [t - 1000.0 for _, _, t, _ in rig.ledger.named("renew")]
    assert [t for t in renewals if t > 600] == [660.0], "renewal goes on through the grace"
    _drive(rig, 1692.0)
    [done] = rig.releases()
    assert (done.outcome, done.due, done.exit_code, done.error) == (
        "timeout", "retry", -9, "stopped at timeout_s=600; exit -9")


def test_a_refused_renewal_kills_the_group_in_the_same_tick() -> None:
    """N6: another holder may claim the job the moment a renewal is refused, so no grace."""
    rig = _rig([_job()], {"test_job": Script()}, due=["test_job"])
    rig.runner.tick()
    rig.ledger.renew_result = False
    _drive(rig, 1059.0)
    assert _signals(rig) == []
    rig.clock.advance(1.0)
    rig.runner.tick()
    assert _signals(rig) == [(60.0, "KILL", "group")]
    rig.runner.tick()
    [done] = rig.releases()
    assert (done.outcome, done.due, done.error) == ("interrupted", "now", "lease fenced; exit -9")


def test_the_guard_fences_a_child_by_itself_while_the_database_is_down() -> None:
    """N7: renewals cannot land for 150 s; the guard kills the group at the fence with no tick."""
    rig = _rig([_job()], {"test_job": Script()}, due=["test_job"])
    rig.runner.tick()
    rig.ledger.renew_result = None
    fence_at = rig.ledger.named("claim")[0][2] + job_lease.TTL_S - job_lease.FENCE_MARGIN_S
    _drive(rig, fence_at - 1)
    assert _signals(rig) == []
    rig.clock.t = fence_at
    rig.guard.run_once(fence_at)
    assert _signals(rig) == [(fence_at - 1000.0, "KILL", "group")]
    rig.runner.tick()
    [done] = rig.releases()
    assert (done.outcome, done.due) == ("interrupted", "now")


def test_the_cap_counts_every_child_until_its_group_is_empty() -> None:
    """N8: three of five due names, in due() order; a reaped leader whose straggler lives still holds its place."""
    names = ["d1", "d2", "d3", "d4", "d5"]
    scripts = {"d1": Script(code=0, at=1001.0, group_for_s=8.0)}
    rig = _rig([_job(n) for n in names], scripts, due=names, max_children=3)
    rig.runner.tick()
    assert [e[1] for e in rig.ledger.named("claim")] == ["d1", "d2", "d3"]
    _drive(rig, 1007.0)
    assert len(rig.ledger.named("claim")) == 3, "the straggler's group still holds d1's place"
    assert rig.releases() == []
    _drive(rig, 1014.0)
    assert [e[1] for e in rig.ledger.named("claim")] == ["d1", "d2", "d3", "d4"]
    [done] = rig.releases()
    assert done.lease.job_name == "d1" and done.outcome == "ok"


def test_a_finished_run_waits_in_memory_through_a_database_outage() -> None:
    """N10: a release that could not be asked keeps its run for the next tick; a down tick claims nothing."""
    rig = _rig([_job("a"), _job("b")], {"a": Script(code=0, at=1001.0)}, due=["a"])
    rig.runner.tick()
    rig.ledger.release_result = None
    rig.clock.advance(2)
    rig.runner.tick()
    assert len(rig.releases()) == 1 and len(rig.runner.finished) == 1, "asked, not answered: kept"
    rig.ledger.down = True
    rig.ledger.due_names = ["b"]
    rig.clock.advance(10)
    rig.runner.tick()
    assert len(rig.releases()) == 1 and rig.ledger.named("claim")[-1][1] == "a", "a down tick asks nothing"
    rig.ledger.down, rig.ledger.release_result = False, True
    rig.clock.advance(10)
    rig.runner.tick()
    assert [d.lease.job_name for d in rig.releases()] == ["a", "a"] and rig.runner.finished == []
    assert [e[1] for e in rig.ledger.named("claim")] == ["a", "b"]


class FakeConn:
    def __init__(self) -> None:
        self.closed = False
        self.broken = False

    def close(self) -> None:
        self.closed = True


def test_the_pg_ledger_latches_on_a_lost_connection_and_retries_next_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    opened: list[FakeConn] = []

    def connect() -> FakeConn:
        if len(opened) == 1:
            opened.append(FakeConn())
            raise psycopg.OperationalError("connection refused")
        opened.append(FakeConn())
        return opened[-1]

    calls: list[str] = []

    def due(conn: FakeConn, names: Sequence[str]) -> list[str]:
        calls.append("due")
        if len(calls) == 1:
            conn.broken = True
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return list(names)

    monkeypatch.setattr(job_lease, "due", due)
    ledger = job_runner.PgLedger(connect, holder="H")  # type: ignore[arg-type]
    ledger.begin(stopping=False)
    assert ledger.due(["x"]) is None and not ledger.up and opened[0].closed, "dropped, and the tick latched"
    assert ledger.due(["x"]) is None and calls == ["due"], "nothing more is asked this tick"
    ledger.begin(stopping=False)
    assert ledger.due(["x"]) is None and len(opened) == 2, "the reconnect failed: still down"
    ledger.begin(stopping=False)
    assert ledger.due(["x"]) == ["x"] and len(opened) == 3


def test_the_pg_ledger_keeps_a_statement_error_to_its_call_unless_stopping(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    def due(conn: FakeConn, names: Sequence[str]) -> list[str]:
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")

    monkeypatch.setattr(job_lease, "due", due)
    ledger = job_runner.PgLedger(FakeConn, holder="H")  # type: ignore[arg-type]
    ledger.begin(stopping=False)
    assert ledger.due(["x"]) is None and ledger.up, "one slow statement does not end the tick"
    ledger.begin(stopping=True)
    assert ledger.due(["x"]) is None and not ledger.up, "while stopping, any error latches"


def test_a_release_the_database_refuses_on_content_is_sent_again_bare(monkeypatch: pytest.MonkeyPatch) -> None:
    """N10: a DataError never drops the run; its outcome and exit code land without the error and the tail."""
    import psycopg

    sent: list[dict[str, object]] = []

    def release(conn: FakeConn, lease: job_lease.Lease, outcome: str, **kwargs: object) -> bool:
        sent.append({"outcome": outcome, **kwargs})
        if len(sent) == 1:
            raise psycopg.DataError("invalid byte sequence")
        return True

    monkeypatch.setattr(job_lease, "release", release)
    ledger = job_runner.PgLedger(FakeConn, holder="H")  # type: ignore[arg-type]
    lease = job_lease.Lease("a", "H", 180, 0.0, run_id=7, args=None, requested_by=None)
    done = Finished(lease, "failed", "backoff", "exit 3", 3, 80, "tail \udcff text")
    ledger.begin(stopping=False)
    assert ledger.release(done) is None and done.bare and ledger.up
    assert ledger.release(done) is True
    assert sent[1] == {"outcome": "failed", "due": "backoff", "error": None, "exit_code": 3, "peak_rss_mb": 80,
                       "log_tail": None}


def test_each_claim_is_its_own_holder_so_a_stale_release_never_reaches_a_later_run(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """One holder per replica would let run 1's late release or renewal match run 2's lease on the same job."""
    holders: list[str] = []

    def claim(conn: FakeConn, name: str, *, holder: str, version: str | None) -> None:
        holders.append(holder)

    monkeypatch.setattr(job_lease, "claim", claim)
    ledger = job_runner.PgLedger(FakeConn, holder="H")  # type: ignore[arg-type]
    ledger.begin(stopping=False)
    ledger.claim("x")
    ledger.claim("x")
    assert len(set(holders)) == 2 and all(h.startswith("H/") for h in holders), holders


def test_a_job_that_cannot_start_is_a_failed_run_with_no_exit_code() -> None:
    """N9: a spawn OSError is the run's failure, never the runner's."""
    rig = _rig([_job()], due=["test_job"])
    rig.standin.fail = FileNotFoundError(2, "No such file or directory")
    rig.runner.tick()
    rig.runner.tick()
    [done] = rig.releases()
    assert (done.outcome, done.due, done.exit_code, done.error) == (
        "failed", "backoff", None, "spawn: FileNotFoundError 2")
    heavy = _rig([_job(db_heavy=True)], due=["test_job"])
    heavy.standin.fail = OSError(12, "Cannot allocate memory")
    heavy.runner.tick()
    heavy.runner.tick()
    assert [d.due for d in heavy.releases()] == ["own_hour"]


def test_a_spawn_that_raises_before_the_watch_leaves_no_process_behind() -> None:
    """N9: atomic spawn. Without it a dropped Popen would leave the job running untracked, or poll its status away."""
    import os

    seen: list[job_process.ProcessChild] = []

    def adopt(child: Child) -> None:
        assert isinstance(child, job_process.ProcessChild)
        seen.append(child)
        raise RuntimeError("the watch failed")

    try:
        with pytest.raises(RuntimeError, match="the watch failed"):
            job_process.spawn_process([sys.executable, "-c", "import time; time.sleep(30)"], dict(os.environ),
                                      "sleeper#1", adopt)
        [child] = seen
        assert child._popen.returncode == -9
        assert job_process.stragglers(child.pid) == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        for child in seen:
            _kill_quietly(child.pid)


def _kill_quietly(pgid: int) -> None:
    import contextlib
    import os
    import signal

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _state(pid: int) -> str | None:
    """The /proc state letter of `pid`, None once it is gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            stat = handle.read().decode("ascii", "replace")
    except OSError:
        return None
    return stat[stat.rfind(")") + 2:].split()[0]


def _wait_for(condition: Callable[[], bool], *, within_s: float = 5.0, tick: Callable[[], object] | None = None) -> None:
    import time

    deadline = time.monotonic() + within_s
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        if tick is not None:
            tick()
        time.sleep(0.02)


def _real_rig(registry: Sequence[Job], *, grace_s: float = 1.0, stopping: Callable[[], bool] | None = None) -> Rig:
    import os

    clock = Clock()
    pids: dict[str, int] = {}

    def spawn(command: Sequence[str], env: Mapping[str, str], label: str,
              adopt: Callable[[Child], None]) -> Child:
        def remember(child: Child) -> None:
            pids[label] = child.pid
            adopt(child)
        return job_process.spawn_process(command, env, label, remember)

    def alive(label: str) -> bool | None:
        return _state(pids[label]) not in (None, "Z") if label in pids else None

    stop = [False]
    guard = Guard(clock=clock, grace_s=grace_s, stopping=stopping or (lambda: stop[0]))
    ledger = FakeLedger(clock, alive, due_names=[j.name for j in registry])
    runner = Runner(registry, ledger=ledger, spawner=spawn, guard=guard, clock=clock, env=dict(os.environ),
                    straggler_wait_s=5.0)
    return Rig(clock, StandIn(clock), ledger, guard, runner, stop)


def _children(rig: Rig) -> dict[str, job_process.ProcessChild]:
    found = {run.job.name: run.child for run in rig.runner.running.values()}
    assert all(isinstance(c, job_process.ProcessChild) for c in found.values())
    return found  # type: ignore[return-value]


def test_a_real_child_that_ignores_sigterm_is_killed_after_the_grace_and_recorded_whole() -> None:
    """N14 (AC2): the parent holds 256 MB, so a peak read from ru_maxrss (which inherits it at exec) would show."""
    import time

    ballast = b"\x01" * (256 << 20)
    argv = (CHILD, "--ignore-term", "--hold-mb", "64", "--lines", "100", "--grandchild")
    rig = _real_rig([_job("stubborn", argv=argv, timeout_s=1, cpus=2)])
    pgid = None
    try:
        rig.runner.tick()
        child = _children(rig)["stubborn"]
        pgid = child.pid
        _wait_for(lambda: "ready" in child.tail())
        with open("/proc/self/oom_score_adj") as mine, open(f"/proc/{pgid}/oom_score_adj") as its:
            assert int(its.read()) == min(1000, int(mine.read()) + 500), "the shim raised the job's OOM score"
        with open(f"/proc/{pgid}/environ", "rb") as environ:
            assert b"JOB_CPUS=2" in environ.read().split(b"\0")
        [grandchild] = [pid for pid, _, _ in job_process._group(pgid) if pid != pgid]

        rig.clock.advance(1.5)
        rig.guard.run_once(rig.clock())
        time.sleep(0.2)
        assert _state(pgid) not in (None, "Z"), "it ignores SIGTERM"
        assert _state(grandchild) not in (None, "Z"), "SIGTERM went to the leader only, not the group"

        rig.clock.advance(1.5)
        rig.guard.run_once(rig.clock())
        _wait_for(lambda: bool(rig.releases()), tick=rig.runner.tick)
        [done] = rig.releases()
        assert (done.outcome, done.due, done.exit_code) == ("timeout", "retry", -9)
        assert done.peak_rss_mb is not None and 64 <= done.peak_rss_mb < 200, done.peak_rss_mb
        assert done.log_tail.splitlines() == [f"line {i}" for i in range(37, 100)] + ["ready"]
        assert job_process.stragglers(pgid) == 0 and _state(grandchild) in (None, "Z")
        [event] = rig.ledger.named("release")
        assert event[3] is False, "released only after the leader exited"
        assert len(ballast) == 256 << 20
    finally:
        if pgid is not None:
            _kill_quietly(pgid)


def test_a_real_deploy_stop_forwards_sigterm_to_each_leader_and_waits_for_the_pool(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """N15 (AC3): a real SIGTERM to this process. The cooperative child's pool finishes its item (SIGTERM reached the
    leader only, so the pool worker lived), the stubborn one dies at stop_at + the grace, and both are interrupted."""
    import os
    import signal

    from scraper import stop

    registry = [_job("coop", argv=(CHILD, "--cooperative", "--pool")), _job("stubborn", argv=(CHILD, "--ignore-term")),
                _job("late", argv=(CHILD, "--exit", "0"))]
    rig = _real_rig(registry, stopping=stop.requested)
    rig.ledger.due_names = ["coop", "stubborn"]
    pgids: list[int] = []
    try:
        rig.runner.tick()
        children = _children(rig)
        pgids = [c.pid for c in children.values()]
        for child in children.values():
            _wait_for(lambda child=child: "ready" in child.tail())
        stop.install()
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.requested()
        rig.guard.run_once(rig.clock())
        assert rig.guard.stop_at == rig.clock()
        rig.ledger.due_names = ["late"]
        _wait_for(lambda: any(d.lease.job_name == "coop" for d in rig.releases()), within_s=10, tick=rig.runner.tick)
        rig.clock.advance(1.0)
        rig.guard.run_once(rig.clock())
        _wait_for(lambda: len(rig.releases()) == 2, tick=rig.runner.tick)
        done = {d.lease.job_name: d for d in rig.releases()}
        assert (done["coop"].outcome, done["coop"].exit_code) == ("interrupted", 143)
        assert "pool ok" in done["coop"].log_tail and "BrokenProcessPool" not in done["coop"].log_tail
        assert (done["stubborn"].outcome, done["stubborn"].due, done["stubborn"].exit_code) == (
            "interrupted", "now", -9)
        rig.clock.advance(job_runner.CLAIM_EVERY_S)
        assert rig.runner.tick() is True, "nothing left running or settling: the loop ends"
        assert [e[1] for e in rig.ledger.named("claim")] == ["coop", "stubborn"], "no claim after the stop"
    finally:
        for pgid in pgids:
            _kill_quietly(pgid)


def test_the_orphan_reaper_never_takes_a_tracked_child_s_status() -> None:
    """N16: run in its own interpreter, since reaping pytest's own children would be a test bug of its own."""
    import subprocess

    script = """
import os, subprocess, sys, time
from scraper import job_process
tracked = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
untracked = subprocess.Popen([sys.executable, "-c", "pass"])
def zombie(pid):
    with open(f"/proc/{pid}/stat") as f:
        s = f.read()
    return s[s.rfind(")") + 2:].split()[0] == "Z"
deadline = time.monotonic() + 5
while not (zombie(tracked.pid) and zombie(untracked.pid)) and time.monotonic() < deadline:
    time.sleep(0.02)
reaped = job_process.reap_orphans({tracked.pid})
code = os.waitstatus_to_exitcode(os.waitpid(tracked.pid, 0)[1])
try:
    os.waitpid(untracked.pid, os.WNOHANG)
    gone = False
except ChildProcessError:
    gone = True
tracked.returncode = untracked.returncode = 0
print(reaped, code, gone)
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30,
                            check=False)
    assert result.stdout.split() == ["1", "3", "True"], result.stderr


def test_a_deploy_stop_forwards_sigterm_at_once_keeps_renewing_and_releases_interrupted() -> None:
    """N11 (AC3) on the stand-in: the guard, not a tick, forwards the stop; the loop stops claiming, renews the
    child still in its grace, and ends only when both are released."""
    rig = _rig([_job("polite"), _job("stubborn"), _job("late")], {"stubborn": Script(ignores_term=True)},
               due=["polite", "stubborn"])
    rig.runner.tick()
    rig.clock.advance(10)
    rig.stop[0] = True
    rig.ledger.due_names = ["late"]
    rig.guard.run_once(rig.clock())
    assert _signals(rig) == [(10.0, "TERM", "leader"), (10.0, "TERM", "leader")], "both, with no tick"
    rig.runner.tick()
    assert [d.lease.job_name for d in rig.releases()] == ["polite"]
    assert rig.releases()[0].outcome == "interrupted" and rig.releases()[0].due == "now"
    _drive(rig, 1000 + 10 + job_process.GRACE_S - 1)
    assert [s for s in _signals(rig) if s[1] == "KILL" and s[0] > 10] == [], "the grace is waited out"
    assert len(rig.ledger.named("renew")) >= 1, "the child in its grace keeps its lease"
    _drive(rig, 1000 + 10 + job_process.GRACE_S)
    assert (10 + job_process.GRACE_S, "KILL", "group") in _signals(rig)
    assert rig.runner.tick() is True
    assert [(d.lease.job_name, d.outcome, d.exit_code) for d in rig.releases()] == [
        ("polite", "interrupted", 143), ("stubborn", "interrupted", -9)]
    assert [e[1] for e in rig.ledger.named("claim")] == ["polite", "stubborn"], "no claim after the stop"
    assert all(stopping for _, stopping, _, _ in rig.ledger.named("begin")[-3:])


def test_a_deploy_during_a_timeout_grace_stays_a_timeout_and_a_clean_exit_in_the_drain_is_ok() -> None:
    """N12: the first cause wins, and the kill comes at the earlier of the two deadlines."""
    rig = _rig([_job("slow", timeout_s=100), _job("quick")],
               {"slow": Script(ignores_term=True), "quick": Script(ignores_term=True, code=0, at=1150.0)},
               due=["slow", "quick"])
    rig.runner.tick()
    _drive(rig, 1100)
    assert _signals(rig)[0] == (100.0, "TERM", "leader")
    _drive(rig, 1140)
    rig.stop[0] = True
    _drive(rig, 1160)
    assert (140.0, "KILL", "group") not in _signals(rig)
    _drive(rig, 1100 + job_process.GRACE_S)
    slow = rig.standin.pid_of("slow")
    kills = [t - 1000.0 for t, sig, _, pid in rig.standin.signals if sig == "KILL" and pid == slow]
    assert kills[0] == 100 + job_process.GRACE_S, "the timeout's own kill: one grace, so it always comes first"
    _drive(rig, 1100 + job_process.GRACE_S + 2)
    done = {d.lease.job_name: d for d in rig.releases()}
    assert (done["slow"].outcome, done["slow"].due) == ("timeout", "retry")
    assert (done["quick"].outcome, done["quick"].exit_code) == ("ok", 0)


def test_a_stop_that_lands_inside_a_ledger_phase_stops_its_claims_at_once() -> None:
    """The stop is read live: one seen during the due list claims nothing, and a lease claimed just before it is
    released interrupted with nothing spawned (a child started now would die mid-startup)."""
    def stop_now(rig: Rig) -> Callable[[], None]:
        def go() -> None:
            rig.stop[0] = True
            rig.guard.run_once(rig.clock())
        return go

    listed = _rig([_job("a"), _job("b")], due=["a", "b"])
    listed.ledger.on["due"] = stop_now(listed)
    listed.runner.tick()
    assert listed.ledger.named("claim") == [] and listed.standin.spawned == []
    assert listed.runner.tick() is True

    claimed = _rig([_job("a"), _job("b")], due=["a", "b"])
    claimed.ledger.on["claim"] = stop_now(claimed)
    claimed.runner.tick()
    assert [e[1] for e in claimed.ledger.named("claim")] == ["a"] and claimed.standin.spawned == []
    claimed.runner.tick()
    [done] = claimed.releases()
    assert (done.lease.job_name, done.outcome, done.due, done.exit_code, done.error) == (
        "a", "interrupted", "now", None, "deploy stop; not started")
    assert claimed.runner.tick() is True


def test_the_drain_s_signals_never_wait_on_a_blocked_database() -> None:
    """N20: a release blocks for 100 s of fake time; the guard still SIGKILLs at stop_at + GRACE_S inside it."""
    rig = _rig([_job("polite"), _job("stubborn")], {"stubborn": Script(ignores_term=True)},
               due=["polite", "stubborn"])
    rig.runner.tick()
    rig.stop[0] = True
    rig.guard.run_once(rig.clock())
    stop_at = rig.clock()
    blocked = [True]

    def block() -> None:
        if blocked[0]:
            blocked[0] = False
            for _ in range(100):
                rig.clock.advance(1.0)
                rig.guard.run_once(rig.clock())

    rig.ledger.on["release"] = block
    rig.runner.tick()
    assert (stop_at - 1000.0 + job_process.GRACE_S, "KILL", "group") in _signals(rig), "killed inside the call"
    [done] = rig.releases()
    assert (done.lease.job_name, done.outcome) == ("polite", "interrupted")
    rig.runner.tick()
    assert [d.outcome for d in rig.releases()] == ["interrupted", "interrupted"]


def test_a_stopping_ledger_phase_asks_nothing_new_past_its_budget() -> None:
    """N20: three runs to release at 8 s a call: two are asked in the 10 s phase, the third next tick."""
    names = ["r1", "r2", "r3"]
    rig = _rig([_job(n) for n in names], due=names)
    rig.runner.tick()
    rig.stop[0] = True
    rig.guard.run_once(rig.clock())
    rig.ledger.on["release"] = lambda: rig.clock.advance(8.0)
    rig.runner.tick()
    assert len(rig.ledger.named("release")) == 2
    rig.runner.tick()
    assert len(rig.ledger.named("release")) == 3


class _Exited(Exception):
    pass


def _serve_guard(rig: Rig, *, steps: int, between: Callable[[], object] | None = None) -> list[int]:
    """Runs Guard.serve on this thread with a fake wait: each step advances the clock a second."""
    exits: list[int] = []
    count = [0]

    def wait(seconds: float) -> None:
        count[0] += 1
        if count[0] > steps:
            raise _Exited
        rig.clock.advance(seconds)
        if between is not None:
            between()

    def exit_(code: int, why: str) -> None:
        exits.append(code)
        raise _Exited(why)

    with pytest.raises(_Exited):
        rig.guard.serve(wait=wait, exit_=exit_)
    return exits


def test_the_watchdog_kills_every_child_then_exits_when_no_tick_finishes_cleanly() -> None:
    """N17: a tick that raises every time never feeds the watchdog; at LIVENESS_BOUND_S the guard kills and exits."""
    rig = _rig([_job()], {"test_job": Script()}, due=["test_job"])
    rig.runner.tick()
    rig.guard.move(rig.standin.pid_of("test_job"), 1e9)  # no fence in this window: only the watchdog may kill
    rig.ledger.on["begin"] = lambda: (_ for _ in ()).throw(RuntimeError("bug"))
    exits = _serve_guard(rig, steps=400, between=rig.runner.tick)
    assert exits == [1]
    assert rig.clock() - 1000.0 == pytest.approx(job_process.LIVENESS_BOUND_S + 1, abs=1)
    assert _signals(rig) == [(pytest.approx(job_process.LIVENESS_BOUND_S + 1, abs=1), "KILL", "group")], (
        "its children die before it exits, and only the watchdog killed them")


def test_the_watchdog_stays_quiet_while_ticks_finish_and_forgives_one_bad_tick() -> None:
    rig = _rig([_job()], due=[])
    raised = [False]

    def flaky() -> None:
        if not raised[0] and rig.clock() > 1100:
            raised[0] = True
            raise RuntimeError("one bad tick")

    rig.ledger.on["begin"] = flaky
    assert _serve_guard(rig, steps=700, between=rig.runner.tick) == []
    assert raised[0] and rig.runner.last_error == "RuntimeError: one bad tick"


def test_a_log_write_that_never_returns_holds_none_of_the_guard_s_signals_nor_its_watchdog() -> None:
    """A stalled log reader (a full stderr pipe): the guard thread only queues its lines, so the timeout's SIGTERM,
    the grace's SIGKILL and the watchdog's exit all still happen."""
    import logging
    import threading

    rig = _rig([_job(timeout_s=5)], {"test_job": Script(ignores_term=True)}, due=["test_job"])
    rig.runner.tick()
    rig.guard.move(rig.standin.pid_of("test_job"), 1e9)
    gate = threading.Event()
    written: list[str] = []

    class Stalled(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            gate.wait(30)
            written.append(record.getMessage())

    stalled = Stalled()
    logger = logging.getLogger("scraper.job_process")
    logger.addHandler(stalled)
    result: list[list[int]] = []
    served = threading.Thread(target=lambda: result.append(_serve_guard(rig, steps=400)), daemon=True)
    try:
        served.start()
        served.join(timeout=10)
        assert not served.is_alive(), "the guard thread waited on a log write"
        assert result == [[1]], "the watchdog exited"
        sent = _signals(rig)
        assert (5.0, "TERM", "leader") in sent and (5.0 + job_process.GRACE_S, "KILL", "group") in sent
        gate.set()
        _wait_for(lambda: any(line.startswith("RUN kill") for line in written))
        assert written[0].startswith("RUN term job=test_job#1"), "the lines are written once the reader drains"
    finally:
        gate.set()
        logger.removeHandler(stalled)
        served.join(timeout=10)


def test_the_watchdog_s_exit_never_waits_on_a_full_stderr() -> None:
    """`_die` arms a backstop that writes nowhere before it writes anything, so a stderr nobody reads cannot hold the
    exit (faulthandler's own timer writes its dump before it exits)."""
    import subprocess

    script = """
import os
from scraper import job_process
os.set_blocking(2, False)
try:
    while True:
        os.write(2, b"x" * 65536)
except BlockingIOError:
    pass
os.set_blocking(2, True)
job_process._die(1, "WATCHDOG drill", backstop_s=0.5)
"""
    proc = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert proc.wait(timeout=10) == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        assert proc.stderr is not None
        proc.stderr.close()


def test_only_a_railway_replica_beats_and_a_clean_stop_forgets_it() -> None:
    """N18: one liveness subject per replica, named from one constant; none off Railway."""
    assert job_runner.heartbeat_name({}) is None
    assert job_runner.heartbeat_name({"RAILWAY_REPLICA_ID": "0f3c9a1e-77aa-4d1e"}) == "job-runner/0f3c9a1e"
    rig = _rig([_job()], {"test_job": Script(ignores_term=True)}, due=["test_job"], beats=True,
               identity={"version": "abc123"})
    rig.runner.tick()
    [beat] = rig.ledger.named("beat")
    details = beat[1]
    assert isinstance(details, dict) and details["version"] == "abc123" and details["stopping"] is False
    assert [c["job"] for c in details["children"]] == ["test_job"]
    assert len(rig.ledger.named("forget_replaced")) == 1
    rig.stop[0] = True
    rig.guard.run_once(rig.clock())
    _drive(rig, rig.clock() + job_process.GRACE_S + 2)
    assert len(rig.ledger.named("beat")) == 1, "no beat while stopping"
    job_runner.loop(rig.runner, sleep=lambda _: None)
    assert len(rig.ledger.named("forget")) == 1
    quiet = _rig([_job()], due=[])
    job_runner.loop(quiet.runner, sleep=lambda _: quiet.stop.__setitem__(0, True) or quiet.guard.run_once(0))
    assert quiet.ledger.named("beat") == [] and quiet.ledger.named("forget") == []


def test_a_replica_beats_only_while_its_claim_passes_complete() -> None:
    """A registration or due list that fails on every tick (a schema drift, say) leaves the database up and the
    ticks clean; withholding the beat is what turns its liveness subject red (675's c_worker_dead)."""
    rig = _rig([_job()], due=[], beats=True)
    rig.ledger.register_fails = True
    for _ in range(3):
        rig.clock.advance(job_runner.BEAT_EVERY_S + 1)
        rig.runner.tick()
    assert rig.ledger.named("beat") == [], "never registered: no beat"
    rig.ledger.register_fails = False
    rig.clock.advance(job_runner.BEAT_EVERY_S + 1)
    rig.runner.tick()
    assert len(rig.ledger.named("beat")) == 1
    rig.ledger.due_fails = True
    for _ in range(3):
        rig.clock.advance(job_runner.BEAT_EVERY_S + 1)
        rig.runner.tick()
    assert len(rig.ledger.named("beat")) == 1, "no claim pass completed since: no beat"
    rig.ledger.due_fails = False
    rig.clock.advance(job_runner.BEAT_EVERY_S + 1)
    rig.runner.tick()
    assert len(rig.ledger.named("beat")) == 2


def test_an_empty_registry_registers_nothing_and_still_beats() -> None:
    """An empty registry (the dark runner before #2025 PR-2): an empty claim pass still completes, so each replica
    beats and its liveness subject is green with no job to run."""
    rig = _rig([], beats=True)
    rig.runner.tick()
    assert [e[1] for e in rig.ledger.named("register")] == [[]] and rig.runner.registered
    assert [e[1] for e in rig.ledger.named("due")] == [[]] and rig.ledger.named("claim") == []
    assert rig.runner.claim_passes == 1 and len(rig.ledger.named("beat")) == 1
    rig.clock.advance(job_runner.BEAT_EVERY_S + 1)
    rig.runner.tick()
    assert len(rig.ledger.named("beat")) == 2


def test_the_boot_line_and_identity_carry_the_replica_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    """The replica's own cgroup limits on the BOOT line and in every beat's details, so Railway's 8 GB (8e9 B, 7,629
    MiB) and 4 vCPU are one SELECT away, as the worker reports its 22,888 MiB."""
    from scraper import cgroup, cpus, stop

    (tmp_path / "memory.max").write_text("8000000000\n")
    (tmp_path / "cpu.max").write_text("400000 100000\n")
    (tmp_path / "self.cgroup").write_text("0::/\n")
    read = cgroup.memory_limit_mb
    monkeypatch.setattr(cgroup, "memory_limit_mb", lambda: read(str(tmp_path)))
    monkeypatch.setattr(cpus, "CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(cpus, "PROC_CGROUP", tmp_path / "self.cgroup")
    monkeypatch.setattr(cpus.os, "sched_getaffinity", lambda _pid: set(range(32)), raising=False)
    monkeypatch.delenv(cpus.JOB_CPUS_ENV, raising=False)
    monkeypatch.setenv("RAILWAY_REPLICA_ID", "0f3c9a1e-77aa-4d1e")
    monkeypatch.setattr(stop, "install", lambda **_: None)
    monkeypatch.setattr(job_runner.db, "database_url", lambda: "postgresql://u@pooler.example:6543/postgres")
    monkeypatch.setattr(job_runner, "_pooler_addresses", lambda url: 2)
    monkeypatch.setattr(job_runner, "_guard", lambda *, watchdog: Guard(clock=Clock()))
    served: list[Runner] = []
    monkeypatch.setattr(job_runner, "loop", served.append)
    with caplog.at_level("INFO", logger="scraper.job_runner"):
        assert job_runner.serve(registry=()) == 0
    [boot] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("BOOT ")]
    assert "heartbeat=job-runner/0f3c9a1e" in boot and boot.endswith("jobs=0 memory_limit_mb=7629.4 cpus=4"), boot
    [runner] = served
    assert {k: runner.identity[k] for k in ("memory_limit_mb", "cpus")} == {"memory_limit_mb": 7629.4, "cpus": 4}
    assert {"version", "holder"} <= set(runner.identity), "the beat's details carry the identity whole"


def test_the_pg_ledger_stamps_its_deployment_on_every_beat(monkeypatch: pytest.MonkeyPatch) -> None:
    beats: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(job_lease, "beat", lambda conn, worker, started_at, details: beats.append((worker, details)))
    ledger = job_runner.PgLedger(FakeConn, holder="H", heartbeat="job-runner/0f3c9a1e",  # type: ignore[arg-type]
                                 deployment="dep-7")
    ledger.begin(stopping=False)
    ledger.beat({"children": []})
    assert beats == [("job-runner/0f3c9a1e", {"children": [], "deployment": "dep-7"})]


RUNNER_FILES = ("scraper/job_runner.py", "scraper/job_process.py", "scraper/jobs.py", "scraper/job_lease.py")


def _code_only(source: str) -> str:
    """The source with its comments and docstrings gone (ast.unparse drops comments)."""
    import ast

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def portal_name_problems(sources: Mapping[str, str]) -> list[str]:
    """A portal's name on letter boundaries (`walk_idnes` and `_web` match, `webhook` does not), code only."""
    from scraper.portal_factory import PORTAL_CLASSES

    problems = []
    for path, text in sources.items():
        code = _code_only(text)
        for source in sorted(PORTAL_CLASSES):
            if re.search(rf"(?<![a-z]){re.escape(source)}(?![a-z])", code, re.IGNORECASE):
                problems.append(f"{path} names the portal {source!r} (rule 21)")
    return problems


def test_no_portal_name_in_the_runner_or_registry_code() -> None:
    """N19 (AC4)."""
    sources = {path: (ROOT / path).read_text() for path in RUNNER_FILES}
    assert portal_name_problems(sources) == []


def test_the_portal_name_census_fires() -> None:
    from scraper.portal_factory import PORTAL_CLASSES

    assert {"web", "idnes", "bazos"} <= set(PORTAL_CLASSES)
    assert portal_name_problems({"x.py": "NAME = 'walk_idnes'\n"})
    assert portal_name_problems({"x.py": "def f():\n    return 'bazos'\n"})
    assert portal_name_problems({"x.py": "SUFFIX = '_web'\n"})
    assert portal_name_problems({"x.py": 'MOD = f"scraper.{source}_main"\nHOOK = "webhook"\n'}) == []
    assert portal_name_problems({"x.py": '"""About bazos."""\n# idnes\nX = 1\n'}) == []


def test_the_cli_prints_help_with_no_verb_and_refuses_what_is_not_a_runner_job(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """N13."""
    assert job_runner.main([], registry=[]) == 2
    assert "{run,once,request,pause,list}" in capsys.readouterr().out
    elsewhere = Job("canary_like", ("scripts.x",), 3600, 0, 60, 256, 1, jobs.GITHUB)
    monkeypatch.setattr(jobs, "host_of", lambda name: elsewhere.host if name == elsewhere.name else None)
    for verb in ("once", "request"):
        assert job_runner.main([verb, "canary_like"], registry=[_job()]) == 2
        assert "canary_like runs on github-actions" in capsys.readouterr().err
    assert job_runner.main(["request", "no_such_job"], registry=[_job()]) == 2
    assert "no job named no_such_job" in capsys.readouterr().err
    assert job_runner.main(["pause", "test_job", "--until", "2001-01-01", "--note", "x"], registry=[_job()]) == 2
    assert "in the past" in capsys.readouterr().err


def test_request_and_pause_reach_job_lease_with_their_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object, object, object]] = []
    monkeypatch.setattr(job_lease, "request",
                        lambda conn, name, args, *, by: calls.append(("request", name, list(args), by)))
    monkeypatch.setattr(job_lease, "pause",
                        lambda conn, name, *, until, note, by: calls.append(("pause", name, until, (note, by))))
    connect = FakeConn
    assert job_runner.main(["request", "test_job", "--by", "github:op", "--", "--limit", "7"], registry=[_job()],
                           connect=connect) == 0  # type: ignore[arg-type]
    assert job_runner.main(["pause", "location_resolve_incremental", "--until", "now", "--note", "lifted",
                            "--by", "op"], registry=[_job()], connect=connect) == 0  # type: ignore[arg-type]
    assert calls == [("request", "test_job", ["--limit", "7"], "github:op"),
                     ("pause", "location_resolve_incremental", None, ("lifted", "op"))]


@pytest.mark.parametrize("script,code", [
    (Script(code=0, at=1001.0), 0),
    (Script(code=75, at=1001.0), 0),
    (Script(code=3, at=1001.0), 1),
    (Script(ignores_term=True), 1),  # a timeout is not ok
])
def test_once_exits_by_the_run_s_outcome(script: Script, code: int) -> None:
    rig = _rig([_job(timeout_s=10)], {"test_job": script}, due=["test_job"])

    def sleep(_: float) -> None:
        assert rig.clock() < 1000.0 + 300, "once never finished"
        rig.clock.advance(1.0)
        rig.guard.run_once(rig.clock())

    assert job_runner.once(_job(timeout_s=10), ledger=rig.ledger, spawner=rig.standin, guard=rig.guard,
                           clock=rig.clock, sleep=sleep) == code
    assert rig.ledger.named("register") == [], "once never upserts"


def test_once_says_not_due_and_an_interrupted_once_exits_143(capsys: pytest.CaptureFixture[str]) -> None:
    idle = _rig([_job()], due=[])
    assert job_runner.once(_job(), ledger=idle.ledger, spawner=idle.standin, guard=idle.guard, clock=idle.clock,
                           sleep=lambda _: None) == 0
    assert "not due" in capsys.readouterr().out
    rig = _rig([_job()], {"test_job": Script()}, due=["test_job"])

    def sleep(_: float) -> None:
        rig.clock.advance(1.0)
        rig.stop[0] = True
        rig.guard.run_once(rig.clock())

    assert job_runner.once(_job(), ledger=rig.ledger, spawner=rig.standin, guard=rig.guard, clock=rig.clock,
                           sleep=sleep) == 143


def test_once_gives_up_on_a_database_that_never_answers_and_never_calls_it_not_due(
        capsys: pytest.CaptureFixture[str]) -> None:
    down = _rig([_job()], due=["test_job"])
    down.ledger.down = True

    def sleep(_: float) -> None:
        assert down.clock() < 1000.0 + 10 * job_runner.ONCE_ANSWER_S, "once never gave up"
        down.clock.advance(job_runner.TICK_S)

    assert job_runner.once(_job(), ledger=down.ledger, spawner=down.standin, guard=down.guard, clock=down.clock,
                           sleep=sleep) == 1
    out = capsys.readouterr()
    assert "the database did not answer" in out.err and "not due" not in out.out
    assert down.clock() <= 1000.0 + job_runner.ONCE_ANSWER_S + job_runner.TICK_S
    stopped = _rig([_job()], due=["test_job"])
    stopped.ledger.down = True

    def interrupt(_: float) -> None:
        stopped.clock.advance(1.0)
        stopped.stop[0] = True
        stopped.guard.run_once(stopped.clock())

    assert job_runner.once(_job(), ledger=stopped.ledger, spawner=stopped.standin, guard=stopped.guard,
                           clock=stopped.clock, sleep=interrupt) == 143
    assert "not due" not in capsys.readouterr().out


def test_a_closed_terminal_stops_once_s_child_instead_of_orphaning_it() -> None:
    """The child runs in its own session, so a hangup reaches only the runner: it must become the stop."""
    import os
    import signal

    from scraper import stop

    rig = _rig([_job()], {"test_job": Script()}, due=["test_job"])
    rig.guard = Guard(clock=rig.clock, stopping=stop.requested, sample=rig.standin.sample, term=rig.standin.term,
                      kill=rig.standin.kill)
    sent: list[bool] = []

    def sleep(_: float) -> None:
        assert rig.clock() < 1000.0 + 300, "the hangup never became the stop"
        rig.clock.advance(1.0)
        if not sent:
            sent.append(True)
            assert signal.getsignal(signal.SIGHUP) is not signal.SIG_DFL, "a hangup would kill the runner"
            os.kill(os.getpid(), signal.SIGHUP)
        rig.guard.run_once(rig.clock())

    assert job_runner.once(_job(), ledger=rig.ledger, spawner=rig.standin, guard=rig.guard, clock=rig.clock,
                           sleep=sleep) == 143
    assert [s[1] for s in _signals(rig)][:1] == ["TERM"], "the guard forwarded the stop to the leader"


def test_serve_routes_the_stop_before_it_reads_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """As PID 1, a SIGTERM that arrives before a handler exists is discarded by the kernel: install comes first."""
    import signal

    from scraper import stop

    seen: list[object] = []

    class Booted(Exception):
        pass

    def runner_jobs() -> tuple[Job, ...]:
        seen.append(signal.getsignal(signal.SIGTERM))
        raise Booted

    monkeypatch.setattr(jobs, "runner_jobs", runner_jobs)
    with pytest.raises(Booted):
        job_runner.serve()
    assert seen == [stop._on_signal]


def test_registration_is_retried_every_tick_and_nothing_is_claimed_before_it_lands() -> None:
    """A database outage at boot never crash-loops the runner, and never lets it claim on an unreconciled row."""
    rig = _rig([_job("a"), _job("b")], due=["a", "b"])
    rig.ledger.register_fails = True
    for _ in range(3):
        rig.clock.advance(10)
        assert rig.runner.tick() is False
    assert len(rig.ledger.named("register")) == 3, "retried every tick"
    assert not rig.runner.registered and rig.ledger.named("claim") == [] and rig.ledger.named("due") == []
    rig.ledger.register_fails, rig.ledger.skipped = False, {"b": "CheckViolation: declarations_positive"}
    rig.clock.advance(10)
    rig.runner.tick()
    assert rig.runner.registered and rig.runner.skipped == {"b": "CheckViolation: declarations_positive"}
    assert rig.ledger.named("due")[0][1] == ["a"], "a row the boot skipped is never claimed"
    assert [e[1] for e in rig.ledger.named("claim")] == ["a"]
    assert rig.ledger.named("retire")[0][1] == ["a", "b"], "a skipped name is still the registry's: never retired"


def test_only_pid_1_reaps_orphans_and_never_a_tracked_child(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[int]] = []
    monkeypatch.setattr(job_runner, "reap_orphans", lambda tracked: seen.append(sorted(tracked)) or 0)
    rig = _rig([_job()], {"test_job": Script()}, due=["test_job"])
    rig.runner.tick()
    rig.runner.tick()
    assert seen == []
    pid1 = _rig([_job()], {"test_job": Script()}, due=["test_job"], pid1=True)
    pid1.runner.tick()
    pid1.runner.tick()
    assert seen == [[], [pid1.standin.pid_of("test_job")]]


def test_list_prints_every_row_with_its_state_and_latest_run(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    rows: list[dict[str, object]] = [
        {"job_name": "rent_map", "runner": "job-runner", "registered": True, "retired_at": None,
         "due_at": now + timedelta(days=3), "paused_until": now + timedelta(hours=1), "pause_note": "drill",
         "requested_at": None, "requested_by": None, "lease_holder": None, "lease_expires_at": None,
         "last_outcome": "ok", "consecutive_failures": 0, "last_success_at": now, "last_error": None,
         "started_at": now, "ended_at": now, "outcome": "ok", "exit_code": 0, "peak_rss_mb": 80},
        {"job_name": "location_resolve_incremental", "runner": "github_actions", "registered": False,
         "retired_at": None, "due_at": None, "paused_until": None, "pause_note": None, "requested_at": None,
         "requested_by": None, "lease_holder": "w:1", "lease_expires_at": now, "last_outcome": "running",
         "consecutive_failures": 0, "last_success_at": now, "last_error": None, "started_at": None,
         "ended_at": None, "outcome": None, "exit_code": None, "peak_rss_mb": None},
    ]
    monkeypatch.setattr(job_lease, "rows", lambda conn: rows)
    assert job_runner.main(["list"], registry=[], connect=FakeConn) == 0  # type: ignore[arg-type]
    first, second = capsys.readouterr().out.splitlines()
    assert first.split()[:3] == ["rent_map", "job-runner", "paused"] and "exit=0 peak=80MB" in first
    assert second.split()[:4] == ["location_resolve_incremental", "github_actions", "held", "lane"]

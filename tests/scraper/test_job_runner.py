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
    standin: StandIn
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
        child = self.standin.by_label.get(label) if label else None
        self.events.append((event, args, self.clock(), child.alive() if child else None))

    def named(self, event: str) -> list[tuple[str, object, float, bool | None]]:
        return [e for e in self.events if e[0] == event]

    def begin(self, *, stopping: bool) -> None:
        self.up = not self.down
        self._record("begin", stopping)

    def register(self, entries: Sequence[Job]) -> job_lease.Registered | None:
        self._record("register", [j.name for j in entries])
        return job_lease.Registered((), (), (), {})

    def retire(self, names: Sequence[str]) -> list[str] | None:
        self._record("retire", list(names))
        return []

    def reap_lost(self, names: Sequence[str], on_hour: Sequence[str]) -> list[str] | None:
        self._record("reap_lost", (list(names), list(on_hour)))
        return []

    def due(self, names: Sequence[str]) -> list[str] | None:
        self._record("due", list(names))
        return [n for n in self.due_names if n in names]

    def claim(self, name: str) -> job_lease.Lease | None:
        self._record("claim", name)
        self.due_names = [n for n in self.due_names if n != name]
        self._run_id += 1
        args, by = self.requests.pop(name, (None, None))
        return job_lease.Lease(name, "H", self._run_id, job_lease.TTL_S, args, by,
                               self.clock() + job_lease.TTL_S - job_lease.FENCE_MARGIN_S, due_slot=by is None)

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
    ledger = FakeLedger(clock, standin, due_names=list(due))
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
    assert job_runner.cause(job, None, 3, peak_mb=None, stragglers=0) == "exit 3"
    assert job_runner.cause(job, "deploy", 143, peak_mb=None, stragglers=0) == "deploy stop; exit 143"
    assert job_runner.cause(job, "fence", -9, peak_mb=None, stragglers=0) == "lease fenced; exit -9"
    assert job_runner.cause(job, None, 0, peak_mb=None, stragglers=0) is None
    assert job_runner.cause(job, None, 75, peak_mb=None, stragglers=0) is None
    assert job_runner.cause(job, None, 1, peak_mb=None, stragglers=2) == (
        "exit 1; 2 processes outlived the leader by 10 s")


def test_a_request_s_args_follow_the_registry_argv_and_the_child_learns_its_run() -> None:
    """N4: argparse keeps a repeated flag's last value, so the request's own flags win."""
    job = _job(argv=("scripts.x", "--limit", "5"), cpus=3)
    assert job_runner.child_command(job, ["--limit", "7"])[-6:] == ["-m", "scripts.x", "--limit", "5", "--limit", "7"]
    assert job_runner.child_command(job, None)[-4:] == ["-m", "scripts.x", "--limit", "5"]
    env = job_runner.child_env(job, 42, {"PATH": "/bin", "JOB_CPUS": "99"})
    assert env == {"PATH": "/bin", "JOB_CPUS": "3", "JOB_NAME": "test_job", "JOB_RUN_ID": "42",
                   "PYTHONUNBUFFERED": "1"}


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
    lease = job_lease.Lease("a", "H", 7, 180, None, None, 0.0)
    done = Finished(lease, "failed", "backoff", "exit 3", 3, 80, "tail \udcff text")
    ledger.begin(stopping=False)
    assert ledger.release(done) is None and done.bare and ledger.up
    assert ledger.release(done) is True
    assert sent[1] == {"outcome": "failed", "due": "backoff", "error": None, "exit_code": 3, "peak_rss_mb": 80,
                       "log_tail": None}


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

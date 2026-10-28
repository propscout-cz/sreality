"""The job runner (Actions exit W2, #2024; PROGRAM §4.2): one always-on program that runs the registry's due jobs.

`python -m scraper.job_runner run` is the service. Every `TICK_S` it ticks in two phases:

* the **process phase** does no database I/O and sends no signal: it reaps exited leaders, waits for each group to
  empty, and settles each run into an outcome and a due kind (`settle`);
* the **ledger phase** releases settled runs, renews leases, then (not stopping) registers the registry at boot,
  reaps lost runs, lists due jobs oldest first and claims them up to `MAX_CHILDREN`, beats and prunes.

The guard thread (`scraper.job_process.Guard`) sends every signal. A stop stops the claims; the loop keeps renewing
and releasing until nothing runs. All SQL is `scraper.job_lease`'s; a scheduler reads `jobs.runner_jobs()` only.
"""

from __future__ import annotations

import argparse
import getpass
import itertools
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import psycopg

from scraper import cgroup, cpus, db, job_lease, jobs, stop
from scraper.job_process import (
    OOM_SHIM,
    STRAGGLER_WAIT_S,
    Child,
    Exit,
    Guard,
    Spawner,
    Stop,
    Watch,
    reap_orphans,
    spawn_process,
)
from scraper.jobs import Job

LOG = logging.getLogger("scraper.job_runner")

TICK_S = 2.0
CLAIM_EVERY_S = 5.0
RENEW_EVERY_S = 60.0
BEAT_EVERY_S = 30.0
FORGET_AFTER_S = 3600  # a silent heartbeat of this deployment is forgotten after this; another deployment's at once
PRUNE_EVERY_S = 86400.0
# The runner's own drain; Railway's drainingSeconds (RUNNER_DRAINING_SECONDS) outlasts it by the last tick and forget.
DRAINING_S = 180.0
CONNECT_TIMEOUT_S = 5
CONNECT_BUDGET_S = 10  # two pooler addresses at CONNECT_TIMEOUT_S each (libpq counts it per address)
STOP_PHASE_BUDGET_S = 10.0  # a stopping ledger phase asks nothing new after this
ONCE_ANSWER_S = 60.0  # `once` gives up when no claim pass has completed by then (a few connect budgets)
CALL_MAX_S = CONNECT_BUDGET_S + 3 * job_lease.STATEMENT_TIMEOUT_S  # a release: the bound, the row, the run
MORE_REMAINS_EXIT = os.EX_TEMPFAIL
MAX_CHILDREN = jobs.RUNNER_MAX_CONCURRENT
HEARTBEAT_PREFIX = f"{job_lease.RUNNER}/"


def settle(reason: Stop | None, code: int | None, *, stopping: bool, db_heavy: bool) -> tuple[str, str]:
    """(outcome, due kind) of a finished run. The runner's own stop decides before the exit code: a timeout is a
    timeout whatever the child exited (0, 75 and 143 included)."""
    if code is None:
        outcome, due = "failed", "backoff"
    elif reason == "timeout":
        outcome, due = "timeout", "retry"
    elif code == 0:
        outcome, due = "ok", "slot"
    elif code == MORE_REMAINS_EXIT:
        outcome, due = "ok", "more"
    elif reason in ("deploy", "fence") or stopping:
        outcome, due = "interrupted", "now"
    else:
        outcome, due = "failed", "backoff"
    # A DB-heavy job's retries stay in its own hour (the census); an interrupted one is due at once (R-a (b)).
    if db_heavy and due in ("backoff", "retry"):
        due = "own_hour"
    return outcome, due


def cause(job: Job, reason: Stop | None, code: int | None, *, peak_mb: int | None, stragglers: int) -> str | None:
    """`last_error`'s text, in `settle`'s order (an ok run has none). Never log text: the tail stays in
    job_runs.log_tail, which can hold a connection string."""
    if code is None:
        text: str | None = "spawn failed"
    elif reason == "timeout":
        text = f"stopped at timeout_s={job.timeout_s}; exit {code}"
    elif code in (0, MORE_REMAINS_EXIT):
        text = None
    elif reason == "deploy":
        text = f"deploy stop; exit {code}"
    elif reason == "fence":
        text = f"lease fenced; exit {code}"
    elif code == -signal.SIGKILL:
        text = (f"SIGKILL not sent by the runner (likely the OOM killer); peak {peak_mb} MB of memory_mb "
                f"{job.memory_mb}")
    elif code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:  # a real-time signal has no name, and a raise here would strand the run
            name = f"signal {-code}"
        text = f"killed by {name}"
    else:
        text = f"exit {code}"
    if stragglers:
        tail = f"{stragglers} processes outlived the leader by {STRAGGLER_WAIT_S:.0f} s"
        text = f"{text}; {tail}" if text else tail
    return text


def child_command(job: Job, args: Sequence[str] | None) -> list[str]:
    """`python -m <argv> <requested args>` through the oom_score_adj shim (same pid); argparse keeps a repeated
    flag's last value, so a request overrides the registry's."""
    return [sys.executable, "-c", OOM_SHIM, "-m", *job.argv, *(args or ())]


def child_env(job: Job, run_id: int, base: Mapping[str, str]) -> dict[str, str]:
    # glibc's per-thread arenas keep what they freed (E949, the worker's `env MALLOC_ARENA_MAX=2`): clip downloads and
    # decodes on 20 threads beside torch's inside its 2048 MB declaration. The runner's own value wins.
    return {"MALLOC_ARENA_MAX": "2", **base, cpus.JOB_CPUS_ENV: str(job.cpus), "JOB_NAME": job.name,
            "JOB_RUN_ID": str(run_id), "PYTHONUNBUFFERED": "1"}


def heartbeat_name(env: Mapping[str, str]) -> str | None:
    """One liveness subject per Railway replica; off Railway (a local run, a test) none, so nothing turns red."""
    replica = env.get("RAILWAY_REPLICA_ID")
    return f"{HEARTBEAT_PREFIX}{replica[:8]}" if replica else None


@dataclass
class Running:
    job: Job  # the in-memory entry: argv, timeout, cpus and db_heavy come from code, never the row
    lease: job_lease.Lease
    child: Child
    started: float
    renew_at: float
    exit: Exit | None = None
    watch: Watch | None = None  # what the guard sent, handed back at the reap
    reaped_at: float | None = None


@dataclass
class Finished:
    """Reaped, group empty, not yet released: kept in memory through a database outage."""

    lease: job_lease.Lease
    outcome: str
    due: str
    error: str | None
    exit_code: int | None
    peak_rss_mb: int | None
    log_tail: str
    bare: bool = False  # a release raised on the content: sent again with no error and no tail


class Ledger(Protocol):
    """The database as the tick sees it. Each call is bounded; None means it could not ask. `up` drops for the rest
    of the tick after a connection failure, or after any failure while stopping."""

    up: bool

    def begin(self, *, stopping: bool) -> None: ...
    def register(self, entries: Sequence[Job]) -> job_lease.Registered | None: ...
    def retire(self, names: Sequence[str]) -> list[str] | None: ...
    def reap_lost(self, names: Sequence[str], on_hour: Sequence[str]) -> list[str] | None: ...
    def due(self, names: Sequence[str]) -> list[str] | None: ...
    def claim(self, name: str) -> job_lease.Lease | None: ...
    def renew(self, lease: job_lease.Lease) -> bool | None: ...
    def release(self, done: Finished) -> bool | None: ...
    def beat(self, details: Mapping[str, object]) -> None: ...
    def forget_replaced(self) -> None: ...
    def forget(self) -> None: ...
    def prune(self) -> None: ...


@dataclass
class Runner:
    registry: Sequence[Job]
    ledger: Ledger
    spawner: Spawner
    guard: Guard
    clock: Callable[[], float]
    service: bool = True
    claim_limit: int | None = None
    max_children: int = MAX_CHILDREN
    pid1: bool = False
    beats: bool = False
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    identity: Mapping[str, object] = field(default_factory=dict)
    renew_every_s: float = RENEW_EVERY_S
    straggler_wait_s: float = STRAGGLER_WAIT_S
    stop_phase_budget_s: float = STOP_PHASE_BUDGET_S
    running: dict[int, Running] = field(default_factory=dict)
    finished: list[Finished] = field(default_factory=list)
    last_released: tuple[Finished, bool] | None = None  # for `once`'s exit code; kept alone, so nothing grows
    registered: bool = False
    skipped: dict[str, str] = field(default_factory=dict)
    claims: int = 0
    claim_passes: int = 0
    answer_by: float | None = None  # `once` gives up when no claim pass has completed by then
    last_error: str | None = None
    _passes_at_beat: int = 0
    _next_claim_at: float = float("-inf")
    _next_beat_at: float = float("-inf")
    _next_prune_at: float = float("-inf")
    _budget_until: float | None = None

    def __post_init__(self) -> None:
        self._by_name = {j.name: j for j in self.registry}

    @property
    def stopping(self) -> bool:
        return self.guard.stop_at is not None

    def tick(self) -> bool:
        """One tick. True once there is nothing left to do: stopping (or, for `once`, done) with nothing running
        or settling."""
        try:
            clean = self._process_phase(self.clock())
            self._ledger_phase(self.clock())
        except Exception as exc:  # noqa: BLE001 - logged; only a clean tick feeds the watchdog
            LOG.exception("TICK failed")
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            return False
        if clean:
            self.guard.tick_done(self.clock())
        return self._done()

    def _done(self) -> bool:
        if self.running or self.finished:
            return False
        if self.stopping:
            return True
        if self.service:
            return False
        return self.claim_passes > 0 or (self.answer_by is not None and self.clock() >= self.answer_by)

    # The process phase: no database, no signal.

    def _process_phase(self, now: float) -> bool:
        clean = True
        for pid, run in list(self.running.items()):
            try:
                if run.exit is None and run.child.exited():
                    run.watch = self.guard.forget(pid)
                    run.exit = run.child.reap()
                    run.reaped_at = now
                if run.exit is not None and run.reaped_at is not None:
                    left = run.child.stragglers()
                    if (left == 0 and run.child.reader_done()) or now >= run.reaped_at + self.straggler_wait_s:
                        self._settle(pid, run, left)
            except Exception:  # noqa: BLE001 - one child's trouble must not stop the others
                LOG.exception("RUN settle failed job=%s pid=%d", run.job.name, pid)
                clean = False
        if self.pid1:
            try:
                reap_orphans(self.running.keys())
            except Exception:  # noqa: BLE001
                LOG.exception("REAP orphans failed")
                clean = False
        return clean

    def _settle(self, pid: int, run: Running, left: int) -> None:
        watch = run.watch or Watch(0.0, 0.0)
        assert run.exit is not None and run.reaped_at is not None
        stop_at = self.guard.stop_at
        outcome, due = settle(watch.reason, run.exit.code, db_heavy=run.job.db_heavy,
                              stopping=stop_at is not None and stop_at <= run.reaped_at)
        error = cause(run.job, watch.reason, run.exit.code, peak_mb=watch.peak_mb, stragglers=left)
        self.finished.append(Finished(run.lease, outcome, due, error, run.exit.code, watch.peak_mb,
                                      run.child.tail()))
        del self.running[pid]

    # The ledger phase.

    def _ask(self, call: Callable[..., object], *args: object) -> object:
        if not self.ledger.up:
            return None
        if self._budget_until is not None and self.clock() >= self._budget_until:
            return None
        return call(*args)

    def _ledger_phase(self, now: float) -> None:
        stopping = self.stopping
        self._budget_until = now + self.stop_phase_budget_s if stopping else None
        self.ledger.begin(stopping=stopping)
        if self.service and not stopping and not self.registered:
            self._register()
        self._release()
        self._renew(now)
        if stopping or self.stopping:  # read live: the guard may have seen the stop during the calls above
            return
        if (self.registered or not self.service) and now >= self._next_claim_at and self.ledger.up:
            self._claim_pass(now)
        # A beat says the schedule works end to end: none until a claim pass completes since the last one, so a
        # registration or due list that fails every tick turns the liveness subject red.
        if self.service and self.beats and now >= self._next_beat_at and self.ledger.up \
                and self.claim_passes > self._passes_at_beat and not self.stopping:
            self._next_beat_at, self._passes_at_beat = now + BEAT_EVERY_S, self.claim_passes
            self._ask(self.ledger.beat, self._details(now))
            self._ask(self.ledger.forget_replaced)
        if self.service and now >= self._next_prune_at and self.ledger.up:
            self._next_prune_at = now + PRUNE_EVERY_S
            self._ask(self.ledger.prune)

    def _register(self) -> None:
        result = self._ask(self.ledger.register, self.registry)
        if not isinstance(result, job_lease.Registered):
            return
        retired = self._ask(self.ledger.retire, [j.name for j in self.registry])
        if retired is None:
            return
        self.registered, self.skipped = True, dict(result.skipped)
        LOG.info("JOBS register entries=%d inserted=%s adopted=%s unretired=%s retired=%s skipped=%s",
                 len(self.registry), list(result.inserted), list(result.adopted), list(result.unretired),
                 retired, sorted(result.skipped))
        for name, why in sorted(result.skipped.items()):
            LOG.error("JOBS skip job=%s why=%s", name, why)

    def _release(self) -> None:
        for done in list(self.finished):
            result = self._ask(self.ledger.release, done)
            if result is None:
                continue
            self.finished.remove(done)
            self.last_released = (done, bool(result))
            lease = done.lease
            if result:
                LOG.info("RUN end job=%s run=%d outcome=%s due=%s exit=%s peak_mb=%s", lease.job_name,
                         lease.run_id, done.outcome, done.due, done.exit_code, done.peak_rss_mb)
            else:
                LOG.warning("LEASE lost job=%s run=%d: release refused (re-claimed, or closed as lost)",
                            lease.job_name, lease.run_id)

    def _renew(self, now: float) -> None:
        for pid, run in list(self.running.items()):
            if run.renew_at > now:
                continue
            result = self._ask(self.ledger.renew, run.lease)
            if result is None:
                continue
            if result:
                self.guard.move(pid, run.lease.fence_at)
                run.renew_at = now + self.renew_every_s
            else:
                self.guard.fence_now(pid)
                run.renew_at = float("inf")

    def _claim_pass(self, now: float) -> None:
        self._next_claim_at = now + CLAIM_EVERY_S
        names = [j.name for j in self.registry if j.name not in self.skipped]
        on_hour = [j.name for j in self.registry if j.db_heavy and j.name in names]
        lost = self._ask(self.ledger.reap_lost, names, on_hour)
        if lost is None:
            return
        for name in lost if isinstance(lost, list) else ():
            LOG.warning("LEASE lost job=%s: its run's lease lapsed; closed as lost", name)
        due = self._ask(self.ledger.due, names)
        if due is None:
            return
        self.claim_passes += 1
        for name in due if isinstance(due, list) else ():
            if len(self.running) >= self.max_children or self.stopping:
                break
            if self.claim_limit is not None and self.claims >= self.claim_limit:
                break
            lease = self._ask(self.ledger.claim, name)
            if not isinstance(lease, job_lease.Lease):
                continue
            self.claims += 1
            if self.stopping:  # it came during the claim: a child started now would die mid-startup
                self.finished.append(Finished(lease, "interrupted", "now", "deploy stop; not started", None, None, ""))
                break
            self._spawn(self._by_name[name], lease, now)

    def _spawn(self, job: Job, lease: job_lease.Lease, now: float) -> None:
        label = f"{job.name}#{lease.run_id}"

        def adopt(child: Child) -> None:
            LOG.info("RUN start job=%s run=%d pid=%d requested_by=%s args=%s", job.name, lease.run_id, child.pid,
                     lease.requested_by, lease.args)
            self.guard.watch(child.pid, term_at=now + job.timeout_s, fence_at=lease.fence_at, label=label)
            self.running[child.pid] = Running(job, lease, child, now, now + self.renew_every_s)

        try:
            self.spawner(child_command(job, lease.args), child_env(job, lease.run_id, self.env), label, adopt)
        except Exception as exc:  # noqa: BLE001 - a job that cannot start is a failed run, not a dead runner
            LOG.exception("RUN spawn failed job=%s run=%d", job.name, lease.run_id)
            _, due = settle(None, None, stopping=False, db_heavy=job.db_heavy)
            errno = getattr(exc, "errno", None)
            self.finished.append(Finished(lease, "failed", due, f"spawn: {type(exc).__name__} {errno or ''}".strip(),
                                          None, None, ""))

    def _details(self, now: float) -> dict[str, object]:
        children = []
        for pid, run in self.running.items():
            watch = self.guard.peek(pid) or run.watch
            children.append({"job": run.job.name, "run": run.lease.run_id, "pid": pid,
                             "age_s": round(now - run.started), "peak_mb": watch.peak_mb if watch else None,
                             "reason": watch.reason if watch else None})
        return {**self.identity, "children": children, "stopping": self.stopping,
                "skipped": sorted(self.skipped), "last_error": self.last_error}


class PgLedger:
    """The `Ledger` over `scraper.job_lease`, on one kept connection that is reopened only after a drop. No SQL."""

    def __init__(self, connect: Callable[[], psycopg.Connection], *, holder: str, version: str | None = None,
                 heartbeat: str | None = None, deployment: str | None = None,
                 started_at: datetime | None = None) -> None:
        self._connect = connect
        self._conn: psycopg.Connection | None = None
        self.holder = holder
        self.version = version
        self.heartbeat = heartbeat
        self.deployment = deployment
        self.started_at = started_at or datetime.now(UTC)
        self.up = True
        self._stopping = False
        self._claims = itertools.count(1)

    def begin(self, *, stopping: bool) -> None:
        self.up, self._stopping = True, stopping

    def _call(self, what: str, call: Callable[[psycopg.Connection], object]) -> object:
        if not self.up:
            return None
        try:
            if self._conn is None or self._conn.closed:
                self._conn = self._connect()
            return call(self._conn)
        except psycopg.Error as exc:
            broken = self._conn is None or self._conn.broken or self._conn.closed
            LOG.warning("LEDGER %s failed%s: %s: %s", what, " (connection lost)" if broken else "",
                        type(exc).__name__, str(exc).splitlines()[0][:200] if str(exc) else "")
            if broken:
                self._drop()
            if broken or self._stopping:
                self.up = False
            raise _Failed(exc) from exc

    def _ask(self, what: str, call: Callable[[psycopg.Connection], object]) -> object:
        try:
            return self._call(what, call)
        except _Failed:
            return None

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with suppress(Exception):
                conn.close()

    def close(self) -> None:
        self._drop()

    def register(self, entries: Sequence[Job]) -> job_lease.Registered | None:
        result = self._ask("register", lambda c: job_lease.register(c, entries))
        return result if isinstance(result, job_lease.Registered) else None

    def retire(self, names: Sequence[str]) -> list[str] | None:
        return self._list(self._ask("retire", lambda c: job_lease.retire(c, names)))

    def reap_lost(self, names: Sequence[str], on_hour: Sequence[str]) -> list[str] | None:
        return self._list(self._ask("reap_lost", lambda c: job_lease.reap_lost(c, names=names, on_hour=on_hour)))

    def due(self, names: Sequence[str]) -> list[str] | None:
        return self._list(self._ask("due", lambda c: job_lease.due(c, names)))

    def claim(self, name: str) -> job_lease.Lease | None:
        # Each claim is its own holder: renewals and releases match on it, so a late release of an earlier run of
        # the same job (lapsed, reaped and re-claimed by this replica) can never free or extend the later run's lease.
        holder = f"{self.holder}/{next(self._claims)}"
        lease = self._ask("claim", lambda c: job_lease.claim(c, name, holder=holder, version=self.version))
        return lease if isinstance(lease, job_lease.Lease) else None

    def renew(self, lease: job_lease.Lease) -> bool | None:
        result = self._ask("renew", lambda c: job_lease.renew(c, lease))
        return None if result is None else bool(result)

    def release(self, done: Finished) -> bool | None:
        def call(conn: psycopg.Connection) -> bool:
            return job_lease.release(conn, done.lease, done.outcome, due=done.due,
                                     error=None if done.bare else done.error, exit_code=done.exit_code,
                                     peak_rss_mb=done.peak_rss_mb, log_tail=None if done.bare else done.log_tail)

        try:
            return bool(self._call("release", call))
        except _Failed as failed:
            if isinstance(failed.exc, (psycopg.DataError, psycopg.IntegrityError)) and not done.bare:
                LOG.error("RUN release refused its content job=%s run=%d: sending outcome and exit only",
                          done.lease.job_name, done.lease.run_id)
                done.bare = True
            return None

    def beat(self, details: Mapping[str, object]) -> None:
        heartbeat = self.heartbeat
        if heartbeat is not None:
            self._ask("beat", lambda c: job_lease.beat(c, heartbeat, self.started_at,
                                                       {**details, "deployment": self.deployment}))

    def forget_replaced(self) -> None:
        heartbeat = self.heartbeat
        if heartbeat is not None:
            self._ask("forget_replaced", lambda c: job_lease.forget_replaced(
                c, prefix=HEARTBEAT_PREFIX, keep=heartbeat, deployment=self.deployment,
                older_than_s=FORGET_AFTER_S))

    def forget(self) -> None:
        heartbeat = self.heartbeat
        if heartbeat is not None:
            self.up = True
            self._ask("forget", lambda c: job_lease.forget(c, heartbeat))

    def prune(self) -> None:
        pruned = self._ask("prune", lambda c: job_lease.prune_runs(c))
        if pruned:
            LOG.info("RUNS pruned %s closed runs older than 90 days", pruned)

    @staticmethod
    def _list(result: object) -> list[str] | None:
        return list(result) if isinstance(result, list) else None


class _Failed(Exception):
    def __init__(self, exc: psycopg.Error) -> None:
        super().__init__(str(exc))
        self.exc = exc


def loop(runner: Runner, *, sleep: Callable[[float], object] = time.sleep, draining_s: float = DRAINING_S) -> None:
    """Ticks until nothing is left (or, stopping, until Railway's drain is spent), then forgets the heartbeat."""
    while not runner.tick():
        stop_at = runner.guard.stop_at
        if stop_at is not None and runner.clock() >= stop_at + draining_s:
            LOG.error("STOP drain spent with %d running, %d unreleased: their leases lapse and the reaper closes "
                      "them as lost", len(runner.running), len(runner.finished))
            break
        sleep(TICK_S)
    if runner.beats:
        runner.ledger.forget()


def _pooler_addresses(url: str) -> int | None:
    """How many addresses the database host resolves to (CONNECT_BUDGET_S assumes two); the host is never logged."""
    try:
        parts = urlsplit(url)
        return len({info[4][0] for info in socket.getaddrinfo(parts.hostname or "", parts.port or 5432)})
    except (OSError, ValueError):
        return None


def _ledger(heartbeat: str | None = None) -> PgLedger:
    return PgLedger(lambda: db.connect(attempts=1, connect_timeout=CONNECT_TIMEOUT_S), holder=job_lease.holder_id(),
                    version=os.environ.get("RAILWAY_GIT_COMMIT_SHA"), heartbeat=heartbeat,
                    deployment=os.environ.get("RAILWAY_DEPLOYMENT_ID"))


def _guard(*, watchdog: bool) -> Guard:
    guard = Guard(clock=job_lease.clock)
    threading.Thread(target=guard.serve, kwargs={"watchdog": watchdog}, name="job-guard", daemon=True).start()
    return guard


def serve(registry: Sequence[Job] | None = None) -> int:
    """`run`: the service. It alone registers, retires, beats, prunes and arms the watchdog's exit."""
    stop.install()  # first: PID 1 discards a SIGTERM that comes before its handler (the imports, the DNS lookup)
    entries = tuple(jobs.runner_jobs() if registry is None else registry)
    heartbeat = heartbeat_name(os.environ)
    ledger = _ledger(heartbeat)
    limits = {"memory_limit_mb": cgroup.memory_limit_mb(), "cpus": cpus.job_cpus()}  # the replica's, by its cgroup
    LOG.info("BOOT pid1=%s version=%s holder=%s deployment=%s heartbeat=%s pooler_addresses=%s jobs=%d "
             "memory_limit_mb=%s cpus=%s", os.getpid() == 1, ledger.version, ledger.holder, ledger.deployment,
             heartbeat, _pooler_addresses(db.database_url()), len(entries), limits["memory_limit_mb"], limits["cpus"])
    guard = _guard(watchdog=True)
    runner = Runner(entries, ledger=ledger, spawner=spawn_process, guard=guard, clock=job_lease.clock,
                    pid1=os.getpid() == 1, beats=heartbeat is not None,
                    identity={"version": ledger.version, "holder": ledger.holder, **limits})
    loop(runner)
    ledger.close()
    return 0


def once(job: Job, *, ledger: Ledger | None = None, spawner: Spawner = spawn_process, guard: Guard | None = None,
         clock: Callable[[], float] = job_lease.clock, sleep: Callable[[float], object] = time.sleep) -> int:
    """One foreground claim, run and release of a runner entry: no upsert, no heartbeat. 0 ok or not due, 143
    interrupted, 1 anything else (a timeout, or a database that never answered, included)."""
    stop.install(hangup=True)  # the child has its own session: a closed terminal must stop it, not orphan it
    pg = None if ledger is not None else _ledger()
    runner = Runner([job], ledger=ledger or pg, spawner=spawner, guard=guard or _guard(watchdog=False),  # type: ignore[arg-type]
                    clock=clock, service=False, claim_limit=1, answer_by=clock() + ONCE_ANSWER_S)
    loop(runner, sleep=sleep)
    if pg is not None:
        pg.close()
    if runner.claims == 0:
        if runner.stopping:
            print(f"{job.name}: stopped before it was claimed", file=sys.stderr)
            return stop.EXIT_CODE
        if runner.claim_passes == 0:
            print(f"{job.name}: the database did not answer within {ONCE_ANSWER_S:.0f} s; nothing ran",
                  file=sys.stderr)
            return 1
        print(f"{job.name}: not due (and no request waiting), or held elsewhere")
        return 0
    done, released = runner.last_released or (None, False)
    outcome = done.outcome if done is not None and released else None
    if outcome == "ok":
        return 0
    return stop.EXIT_CODE if outcome == "interrupted" else 1


def _by() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except (KeyError, OSError):
        return "cli:unknown"


def _until(text: str) -> datetime | None:
    if text == "now":
        return None
    at = datetime.fromisoformat(text)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _state(row: Mapping[str, object], now: datetime) -> str:
    paused_until = row["paused_until"]
    if row["retired_at"]:
        return "retired"
    if isinstance(paused_until, datetime) and paused_until > now:
        return "paused"
    if row["requested_at"]:
        return "requested"
    return "held" if row["lease_holder"] else "-"


def _list(conn: psycopg.Connection) -> None:
    now = datetime.now(UTC)
    for row in job_lease.rows(conn):
        due_at = row["due_at"]
        due = f"due={due_at:%Y-%m-%d %H:%M}Z" if isinstance(due_at, datetime) else "lane"
        last = (f"run={row['outcome'] or 'open'} exit={row['exit_code']} peak={row['peak_rss_mb']}MB"
                if row["started_at"] else "no run")
        print(f"{row['job_name']:32} {row['runner']:16} {_state(row, now):9} {due:22} last={row['last_outcome']} "
              f"failures={row['consecutive_failures']} {last}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scraper.job_runner",
                                     description="The job runner: run it as a service, or act on one job's row.")
    verbs = parser.add_subparsers(dest="verb", metavar="{run,once,request,pause,list}")
    verbs.add_parser("run", help="the service (Railway's start command)")
    once_ = verbs.add_parser("once", help="claim, run and release one runner job here, if it is due or requested")
    once_.add_argument("job")
    request_ = verbs.add_parser("request", help="ask the runner to run a job now: request <job> [--by WHO] [-- ARGS]")
    request_.add_argument("job")
    request_.add_argument("--by", default=None)
    pause_ = verbs.add_parser("pause", help="pause a job's schedule (a request still runs); --until now lifts it")
    pause_.add_argument("job")
    pause_.add_argument("--until", required=True, help="an ISO time (UTC unless given), or now to lift the pause")
    pause_.add_argument("--note", required=True)
    pause_.add_argument("--by", default=None)
    verbs.add_parser("list", help="every scheduled_jobs row with its latest run")
    return parser


def main(argv: Sequence[str] | None = None, *, registry: Sequence[Job] | None = None,
         connect: Callable[[], psycopg.Connection] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    extra: list[str] = []
    if "--" in args:
        at = args.index("--")
        args, extra = args[:at], args[at + 1:]
    parser = _parser()
    ns = parser.parse_args(args)
    if ns.verb is None:
        parser.print_help()
        return 2
    if extra and ns.verb != "request":
        parser.error("only request takes -- ARGS")
    if ns.verb == "run":
        return serve(registry)
    entries = {j.name: j for j in (jobs.runner_jobs() if registry is None else registry)}
    if ns.verb in ("once", "request") and ns.job not in entries:
        host = jobs.host_of(ns.job)
        print(f"{ns.job} runs on {host}" if host else f"no job named {ns.job} in the registry", file=sys.stderr)
        return 2
    if ns.verb == "once":
        return once(entries[ns.job])
    until = None
    if ns.verb == "pause":
        try:
            until = _until(ns.until)
        except ValueError:
            parser.error(f"--until {ns.until!r} is not an ISO time or now")
        if until is not None and until <= datetime.now(UTC):
            print(f"--until {ns.until} is in the past: use --until now to lift a pause", file=sys.stderr)
            return 2
    conn = (connect or (lambda: db.connect(attempts=1, connect_timeout=CONNECT_TIMEOUT_S)))()
    try:
        if ns.verb == "list":
            _list(conn)
            return 0
        if ns.verb == "request":
            refusal = job_lease.request(conn, ns.job, extra, by=ns.by or _by())
        else:
            refusal = job_lease.pause(conn, ns.job, until=until, note=ns.note, by=ns.by or _by())
    finally:
        conn.close()
    if refusal:
        print(refusal, file=sys.stderr)
        return 1
    print(f"{ns.verb} filed for {ns.job}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

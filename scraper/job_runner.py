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
import logging
import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import psycopg

from scraper import cpus, db, job_lease, jobs, stop
from scraper.job_process import (
    GRACE_S,
    GUARD_POLL_S,
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
DRAINING_S = 180.0  # Railway's drainingSeconds (#2025 pins it to .railway/railway.ts)
CONNECT_TIMEOUT_S = 5
CONNECT_BUDGET_S = 10  # two pooler addresses at CONNECT_TIMEOUT_S each (libpq counts it per address)
STOP_PHASE_BUDGET_S = 10.0  # a stopping ledger phase asks nothing new after this
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
    """`last_error`'s text. Never log text: the tail stays in job_runs.log_tail, which can hold a connection string."""
    if code is None:
        text: str | None = "spawn failed"
    elif reason == "timeout":
        text = f"stopped at timeout_s={job.timeout_s}; exit {code}"
    elif reason == "deploy":
        text = f"deploy stop; exit {code}"
    elif reason == "fence":
        text = f"lease fenced; exit {code}"
    elif code in (0, MORE_REMAINS_EXIT):
        text = None
    elif code == -signal.SIGKILL:
        text = (f"SIGKILL not sent by the runner (likely the OOM killer); peak {peak_mb} MB of memory_mb "
                f"{job.memory_mb}")
    elif code < 0:
        text = f"killed by {signal.Signals(-code).name}"
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
    return {**base, cpus.JOB_CPUS_ENV: str(job.cpus), "JOB_NAME": job.name, "JOB_RUN_ID": str(run_id),
            "PYTHONUNBUFFERED": "1"}


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
    released: list[tuple[Finished, bool]] = field(default_factory=list)
    registered: bool = False
    skipped: dict[str, str] = field(default_factory=dict)
    claims: int = 0
    claim_passes: int = 0
    last_error: str | None = None
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
        return not self.service and self.claim_passes > 0

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
        if stopping:
            return
        if (self.registered or not self.service) and now >= self._next_claim_at and self.ledger.up:
            self._claim_pass(now)
        if self.service and self.beats and now >= self._next_beat_at and self.ledger.up:
            self._next_beat_at = now + BEAT_EVERY_S
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
            self.released.append((done, bool(result)))
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
            if len(self.running) >= self.max_children:
                break
            if self.claim_limit is not None and self.claims >= self.claim_limit:
                break
            lease = self._ask(self.ledger.claim, name)
            if not isinstance(lease, job_lease.Lease):
                continue
            self.claims += 1
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

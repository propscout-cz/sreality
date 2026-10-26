"""The job runner's children (Actions exit W2, #2024): spawn, peek, reap, read `/proc`, and the guard thread.

Each job runs in its own session, so its pid is its process group. The **guard** sends every signal, under one
lock and only while the leader is unreaped, and takes every memory sample; it never touches the database, so a
stalled database or a wedged main loop delays no stop:

* SIGTERM goes to the leader only, at the job's `timeout_s` or as soon as a stop arrives, so the process that
  installed `scraper.stop` finalizes while its pool workers finish the item in hand;
* SIGKILL goes to the whole group `GRACE_S` later, at once at the lease's fence, and to stragglers once the leader
  has exited;
* with no clean tick for `LIVENESS_BOUND_S` it kills every group and exits the process (the watchdog).

Exit statuses are read with a pid-targeted `waitid(WNOWAIT)` peek and a `waitpid` reap, never through `Popen`'s own
methods (they reap with no status left to read, and `send_signal` polls first). Peak memory is the leader's `VmHWM`
or the group's summed `VmRSS`, whichever is higher, over the guard's samples; never `ru_maxrss`, which inherits the
runner's own peak at exec.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Literal, Protocol

from scraper import stop

LOG = logging.getLogger("scraper.job_process")

Stop = Literal["timeout", "deploy", "fence"]  # why the runner stopped a job; the first cause wins

# Run before the job's own interpreter, in the same pid: an OOM picks the job, never the runner. Raising the
# score needs no privilege; preexec_fn would be unsafe in a threaded parent.
OOM_SHIM = (
    "import os, sys\n"
    "p = '/proc/self/oom_score_adj'\n"
    "try:\n"
    "    with open(p) as f:\n"
    "        v = min(1000, int(f.read()) + 500)\n"
    "    with open(p, 'w') as f:\n"
    "        f.write(str(v))\n"
    "except (OSError, ValueError):\n"
    "    pass\n"
    "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
)

GRACE_S = 90.0  # SIGTERM to SIGKILL, at a timeout and in a deploy's drain; under jobs.STUCK_RUN_GRACE_S
GUARD_POLL_S = 1.0
LIVENESS_BOUND_S = 300.0  # no clean tick for this long: the watchdog exits (under 675's c_worker_dead)
STRAGGLER_WAIT_S = 10.0  # after the leader's exit, how long the release waits for its group to empty
TAIL_LINES, TAIL_LINE_BYTES, TAIL_BYTES = 64, 8192, 32768
REPO_ROOT = Path(__file__).resolve().parents[1]
PROC = "/proc"

_OUTPUT_LOCK = threading.Lock()


@dataclass(frozen=True)
class Exit:
    code: int  # os.waitstatus_to_exitcode: negative = killed by that signal


@dataclass
class Watch:
    """The guard's record of one child; `forget` hands a copy back at the reap."""

    term_at: float
    fence_at: float
    label: str = ""
    reason: Stop | None = None
    kill_at: float | None = None
    killed: bool = False  # the runner sent the group its SIGKILL
    peak_mb: int | None = None


class Child(Protocol):
    pid: int  # = its process group

    def exited(self) -> bool: ...  # the leader exited; it stays waitable

    def reap(self) -> Exit: ...

    def stragglers(self) -> int: ...  # live (non-zombie) processes left in the group

    def reader_done(self) -> bool: ...  # every writer closed the output pipe

    def tail(self) -> str: ...


# (command, env, label "<job>#<run>", adopt): `adopt` runs inside the spawn, so a child it raises on is killed.
Spawner = Callable[[Sequence[str], Mapping[str, str], str, Callable[[Child], None]], Child]


def _stats(proc: str = PROC) -> Iterator[tuple[int, list[str]]]:
    """(pid, the /proc/<pid>/stat fields after the command name) of every process: state first, then ppid, pgrp."""
    try:
        entries = os.listdir(proc)
    except OSError:
        return
    for entry in entries:
        if not entry.isdecimal():
            continue
        try:
            with open(os.path.join(proc, entry, "stat"), "rb") as handle:
                stat = handle.read().decode("ascii", "replace")
        except OSError:
            continue
        yield int(entry), stat[stat.rfind(")") + 2:].split()


def _group(pgid: int, proc: str = PROC) -> list[tuple[int, str, int]]:
    """(pid, state, rss pages) of every process whose process group is `pgid`."""
    return [(pid, f[0], int(f[21])) for pid, f in _stats(proc) if len(f) > 21 and f[2] == str(pgid)]


def _kb(path: str, key: str) -> int | None:
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line.startswith(key):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def process_memory_mb(pgid: int, proc: str = PROC) -> int | None:
    """The higher of the leader's peak resident memory and its group's summed resident memory, in MiB."""
    hwm_kb = _kb(os.path.join(proc, str(pgid), "status"), "VmHWM:")
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    rss_kb = sum(pages * page_kb for _, state, pages in _group(pgid, proc) if state != "Z")
    best = max(hwm_kb or 0, rss_kb)
    return -(-best // 1024) if best else None


def stragglers(pgid: int, proc: str = PROC) -> int:
    return sum(1 for _, state, _ in _group(pgid, proc) if state != "Z")


class ProcessChild:
    """A spawned job: its output is echoed as `[<label>] <line>` and the last lines kept for the run record."""

    def __init__(self, popen: subprocess.Popen[bytes], label: str) -> None:
        self.pid = popen.pid
        self.label = label
        self._popen = popen
        self._lines: deque[str] = deque(maxlen=TAIL_LINES)
        self._lock = threading.Lock()
        assert popen.stdout is not None
        self._reader = threading.Thread(target=self._read, args=(popen.stdout,), name=f"tail {label}", daemon=True)
        self._reader.start()

    def _read(self, pipe: IO[bytes]) -> None:
        with suppress(OSError, ValueError):
            for raw in iter(lambda: pipe.readline(TAIL_LINE_BYTES), b""):
                line = raw.decode("utf-8", "replace").replace("\x00", "").rstrip("\r\n")
                with self._lock:
                    self._lines.append(line)
                with _OUTPUT_LOCK, suppress(OSError, ValueError):
                    sys.stdout.write(f"[{self.label}] {line}\n")
                    sys.stdout.flush()
        with suppress(OSError):
            pipe.close()

    def exited(self) -> bool:
        if self._popen.returncode is not None:
            return True
        try:
            return os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        except ChildProcessError:
            return True

    def reap(self) -> Exit:
        if self._popen.returncode is None:
            _, status = os.waitpid(self.pid, 0)
            self._popen.returncode = os.waitstatus_to_exitcode(status)
        return Exit(self._popen.returncode)

    def stragglers(self) -> int:
        return stragglers(self.pid)

    def reader_done(self) -> bool:
        return not self._reader.is_alive()

    def tail(self) -> str:
        with self._lock:
            text = "\n".join(self._lines)
        data = text.encode("utf-8")
        return data[-TAIL_BYTES:].decode("utf-8", "ignore") if len(data) > TAIL_BYTES else text


def _term_leader(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)


def _kill_group(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def spawn_process(command: Sequence[str], env: Mapping[str, str], label: str,
                  adopt: Callable[[Child], None]) -> ProcessChild:
    """Starts the job in its own session. Atomic: if anything raises before `adopt` returns, the group is killed and
    the leader reaped, so no dropped `Popen` steals a status later or leaves a job running untracked."""
    popen = subprocess.Popen(  # noqa: S603 - argv from the registry, never a shell
        list(command), env=dict(env), cwd=REPO_ROOT, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        child = ProcessChild(popen, label)
        adopt(child)
    except BaseException:
        _kill_group(popen.pid)
        _, status = os.waitpid(popen.pid, 0)
        popen.returncode = os.waitstatus_to_exitcode(status)
        raise
    return child


def reap_orphans(tracked: Collection[int], proc: str = PROC) -> int:
    """PID 1 only: reaps every zombie child that is not a tracked job, by pid, so a job's status is never taken."""
    me = str(os.getpid())
    reaped = 0
    for pid, fields in _stats(proc):
        if pid in tracked or len(fields) < 2 or fields[0] != "Z" or fields[1] != me:
            continue
        with suppress(ChildProcessError):
            if os.waitpid(pid, os.WNOHANG)[0]:
                reaped += 1
    return reaped


def _die(code: int) -> None:
    # A blocked stderr must not hold the exit: the faulthandler thread exits on its own 10 s later.
    faulthandler.dump_traceback_later(10, exit=True)
    faulthandler.dump_traceback(all_threads=True)
    os._exit(code)


class Guard:
    """Every signal and every memory sample, on its own thread; never a database call."""

    def __init__(self, *, clock: Callable[[], float], grace_s: float = GRACE_S,
                 stopping: Callable[[], bool] = stop.requested,
                 sample: Callable[[int], int | None] = process_memory_mb,
                 term: Callable[[int], None] = _term_leader, kill: Callable[[int], None] = _kill_group,
                 liveness_s: float = LIVENESS_BOUND_S) -> None:
        self._clock = clock
        self._grace_s = grace_s
        self._stopping = stopping
        self._sample = sample
        self._term = term
        self._kill = kill
        self._liveness_s = liveness_s
        self._lock = threading.Lock()
        self._watches: dict[int, Watch] = {}
        self._clean_at = clock()
        self.stop_at: float | None = None

    def watch(self, pid: int, *, term_at: float, fence_at: float, label: str = "") -> None:
        watch = Watch(term_at, fence_at, label)
        with self._lock:
            self._watches[pid] = watch

    def move(self, pid: int, fence_at: float) -> None:
        """A renewal landed: the fence moves out."""
        with self._lock:
            if pid in self._watches:
                self._watches[pid].fence_at = fence_at

    def fence_now(self, pid: int) -> None:
        """A renewal was refused: another holder may claim the job now, so its group dies now."""
        with self._lock:
            watch = self._watches.get(pid)
            if watch is not None and not watch.killed:
                watch.reason = watch.reason or "fence"
                self._kill_locked(pid, watch, "renewal refused")

    def forget(self, pid: int) -> Watch:
        """The leader exited and is about to be reaped: kill its group's stragglers and never signal the pid again."""
        with self._lock:
            watch = self._watches.pop(pid, None)
            self._kill(pid)
        return watch if watch is not None else Watch(0.0, 0.0)

    def peek(self, pid: int) -> Watch | None:
        with self._lock:
            watch = self._watches.get(pid)
            return replace(watch) if watch is not None else None

    def tick_done(self, at: float) -> None:
        """Only a tick that raised nothing calls this; the watchdog counts from the last one."""
        self._clean_at = at

    def _kill_locked(self, pid: int, watch: Watch, why: str) -> None:
        self._kill(pid)
        watch.killed = True
        LOG.warning("RUN kill job=%s pid=%d why=%s (%s)", watch.label, pid, watch.reason, why)

    def run_once(self, now: float) -> None:
        with self._lock:
            if self.stop_at is None and self._stopping():
                self.stop_at = now
                LOG.warning("STOP seen: forwarding SIGTERM to %d children, SIGKILL in %.0f s",
                            len(self._watches), self._grace_s)
            for pid, watch in self._watches.items():
                if not watch.killed:
                    mb = self._sample(pid)
                    if mb is not None:
                        watch.peak_mb = max(watch.peak_mb or 0, mb)
                if not watch.killed and now >= watch.fence_at:
                    watch.reason = watch.reason or "fence"
                    self._kill_locked(pid, watch, "the lease's fence passed")
                    continue
                if watch.reason is None and self.stop_at is not None:
                    watch.reason, watch.kill_at = "deploy", self.stop_at + self._grace_s
                    self._term(pid)
                    LOG.warning("RUN term job=%s pid=%d why=deploy", watch.label, pid)
                elif watch.reason is None and now >= watch.term_at:
                    watch.reason, watch.kill_at = "timeout", now + self._grace_s
                    self._term(pid)
                    LOG.warning("RUN term job=%s pid=%d why=timeout", watch.label, pid)
                if watch.reason == "timeout" and self.stop_at is not None and watch.kill_at is not None:
                    watch.kill_at = min(watch.kill_at, self.stop_at + self._grace_s)
                if not watch.killed and watch.kill_at is not None and now >= watch.kill_at:
                    self._kill_locked(pid, watch, f"{self._grace_s:.0f} s after the SIGTERM")

    def _trip(self, now: float, exit_: Callable[[int], None]) -> None:
        with self._lock:
            for pid in self._watches:
                self._kill(pid)
            LOG.critical("WATCHDOG no clean tick for %.0f s: killed %d children, exiting",
                         now - self._clean_at, len(self._watches))
        exit_(1)

    def serve(self, *, wait: Callable[[float], object] | None = None, exit_: Callable[[int], None] = _die,
              watchdog: bool = True) -> None:
        """The guard thread's loop. It waits on the stop event until it has seen the stop, then sleeps."""
        while True:
            now = self._clock()
            try:
                self.run_once(now)
            except Exception:  # noqa: BLE001 - the guard outlives any one bad pass
                LOG.exception("GUARD pass failed")
            if watchdog and now - self._clean_at > self._liveness_s:
                self._trip(now, exit_)
                return
            if wait is not None:
                wait(GUARD_POLL_S)
            elif self.stop_at is None:
                stop.EVENT.wait(GUARD_POLL_S)
            else:
                time.sleep(GUARD_POLL_S)

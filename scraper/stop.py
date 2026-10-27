"""One stop signal per process (Actions exit W1, #2019).

A deploy, a cancel or the job runner's timeout sends SIGTERM (a terminal or GitHub's cancel sends SIGINT first).
`install` turns either into one shared event: `portal.deadline_reached` reads it, the rate limiters and the HTTP
client's backoff wait on it, and a request in hand on the main thread is cut short, so a walk or a drain finalizes its
run record within the drain window and exits `EXIT_CODE`.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

LOG = logging.getLogger(__name__)

EVENT = threading.Event()
# 128 + SIGTERM: what a shell reports for a process the stop signal ended. The job runner reads it as interrupted, or as
# timeout when the stop was its own at the job's timeout_s (an interrupted run is due again at once).
EXIT_CODE = 143
_installed = False
_main_thread_in_request = False


class StopRequested(Exception):
    """A stop signal arrived: the work in hand is abandoned, not failed."""


def requested() -> bool:
    return EVENT.is_set()


def wait(seconds: float) -> bool:
    """Sleeps up to `seconds`; True, at once, when a stop arrives first (a plain sleep before `install`: nothing can
    deliver a stop then, and the realtime worker keeps its own asyncio stop event)."""
    if not _installed:
        if seconds > 0:
            time.sleep(seconds)
        return EVENT.is_set()
    return EVENT.wait(seconds) if seconds > 0 else EVENT.is_set()


@contextmanager
def interruptible() -> Iterator[None]:
    """A blocking request the stop signal may cut short: on the main thread, where the handler runs, it raises
    `StopRequested` inside the call. Other threads' requests end on their own timeout."""
    global _main_thread_in_request
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    _main_thread_in_request = True
    try:
        yield
    finally:
        _main_thread_in_request = False


def install(*, hangup: bool = False) -> None:
    """SIGTERM and SIGINT set the event; with `hangup`, SIGHUP and SIGQUIT too (a foreground `job_runner once`, whose
    child in its own session would outlive a closed terminal). Main thread only: no other thread can install them."""
    global _installed
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT, *((signal.SIGHUP, signal.SIGQUIT) if hangup else ())):
            signal.signal(sig, _on_signal)
        _installed = True


def reset() -> None:
    """Forget a stop and the install (a process that runs phases again, or a test); the handlers stay as they are."""
    global _installed
    EVENT.clear()
    _installed = False


def _on_signal(signum: int, _frame: FrameType | None) -> None:
    first = not EVENT.is_set()
    EVENT.set()  # first: nothing below may keep the stop from being seen
    if first:
        LOG.warning("STOP %s received: finalizing after the request in hand", signal.Signals(signum).name)
    # A repeat (GitHub's cancel sends SIGINT, then SIGTERM) changes nothing more: the finalize under way must finish.
    if _main_thread_in_request:
        raise StopRequested("a stop signal cut the request in hand")

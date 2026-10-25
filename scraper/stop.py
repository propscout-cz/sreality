"""One stop signal per process (Actions exit W1, #2019).

A deploy, a cancel or the job runner's timeout sends SIGTERM (a terminal sends SIGINT). `install` turns either into
one shared event: `portal.deadline_reached` reads it, the rate limiters and the HTTP client's backoff wait on it, so a
walk or a drain stops after at most one more request, finalizes its run record and exits `EXIT_CODE`.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from types import FrameType

LOG = logging.getLogger("scraper.stop")

EVENT = threading.Event()
_installed = False
# 128 + SIGTERM: what a shell reports for a process the stop signal ended, and what the job runner reads as interrupted.
EXIT_CODE = 143


class StopRequested(Exception):
    """A stop signal arrived: the work in hand is abandoned, not failed."""


def requested() -> bool:
    return EVENT.is_set()


def wait(seconds: float) -> bool:
    """Sleeps up to `seconds`; True, at once, when a stop arrives first.

    Until `install`, nothing can deliver a stop, so this is a plain `time.sleep` (the realtime worker, which keeps
    its own asyncio stop event, and every caller that never runs a phase, sleep exactly as before)."""
    if not _installed:
        if seconds > 0:
            time.sleep(seconds)
        return EVENT.is_set()
    return EVENT.wait(seconds) if seconds > 0 else EVENT.is_set()


def install() -> None:
    """SIGTERM and SIGINT set the event. Main thread only: Python installs signal handlers nowhere else."""
    global _installed
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, _on_signal)
        _installed = True


def _on_signal(signum: int, _frame: FrameType | None) -> None:
    # A repeat (GitHub's cancel sends SIGINT, then SIGTERM) changes nothing: the finalize already under way must finish.
    if not EVENT.is_set():
        LOG.warning("STOP %s received: finishing the request in hand, then finalizing",
                    signal.Signals(signum).name)
    EVENT.set()

"""`scraper.stop` (#2019): a signal sets the one stop event, a repeat changes nothing, and a wait ends on it."""

from __future__ import annotations

import os
import signal
import threading
import time

import pytest

from scraper import stop


@pytest.mark.parametrize("first,second", [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)])
def test_a_signal_sets_the_event_and_a_repeat_changes_nothing(first: signal.Signals, second: signal.Signals) -> None:
    """GitHub's cancel sends SIGINT, then SIGTERM: the second must not cut the finalize the first started."""
    stop.install()
    os.kill(os.getpid(), first)
    assert stop.requested()
    os.kill(os.getpid(), second)  # no KeyboardInterrupt, no exit
    assert stop.requested()


def test_an_installed_wait_ends_at_once_on_a_stop() -> None:
    stop.install()
    timer = threading.Timer(0.05, stop.EVENT.set)
    timer.start()
    try:
        started = time.monotonic()
        assert stop.wait(5.0)
        assert time.monotonic() - started < 1.0
    finally:
        timer.cancel()


def test_the_signal_cuts_only_a_request_in_hand() -> None:
    """Inside `interruptible` the handler raises; outside it (a finalize under way) it only sets the event."""
    stop.install()
    with pytest.raises(stop.StopRequested), stop.interruptible():
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(1)
    stop.reset()
    stop.install()
    os.kill(os.getpid(), signal.SIGTERM)
    assert stop.requested()


@pytest.mark.parametrize("sig", [signal.SIGHUP, signal.SIGQUIT])
def test_a_foreground_install_also_takes_a_hangup_and_a_quit_as_the_stop(sig: signal.Signals) -> None:
    """`job_runner once` runs its child in its own session: a closed terminal must stop it, not orphan it."""
    stop.install()
    assert signal.getsignal(sig) is not stop._on_signal, "only a foreground run asks for these"
    stop.install(hangup=True)
    assert signal.getsignal(sig) is stop._on_signal  # before the kill: an unrouted one would end this process
    os.kill(os.getpid(), sig)
    assert stop.requested()


def test_reset_forgets_the_stop_and_the_install() -> None:
    stop.install()
    stop.EVENT.set()
    stop.reset()
    assert not stop.requested() and not stop._installed


def test_before_install_a_wait_is_the_plain_sleep_it_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing can deliver a stop to a process that never installed the handler (the realtime worker keeps its own)."""
    slept: list[float] = []
    monkeypatch.setattr(stop.time, "sleep", slept.append)
    assert stop.wait(2.5) is False
    assert stop.wait(0) is False
    assert slept == [2.5]


def test_off_the_main_thread_install_changes_nothing() -> None:
    before = signal.getsignal(signal.SIGTERM)
    worker = threading.Thread(target=stop.install)
    worker.start()
    worker.join()
    assert signal.getsignal(signal.SIGTERM) is before
    assert not stop._installed

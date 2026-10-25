"""Tests for scraper.rate_limit.RateLimiter.

Hermetic: monkeypatches time.monotonic / time.sleep on the module so no
real wall-clock time passes and the scheduling math is deterministic.
"""

from __future__ import annotations

import threading
import time

import pytest

import scraper.rate_limit as rl
from scraper import stop
from scraper.rate_limit import RateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _patch_time(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(rl.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(rl.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def test_rate_must_be_positive():
    with pytest.raises(ValueError):
        RateLimiter(0)


def test_acquire_spaces_by_interval_when_clock_frozen(monkeypatch):
    clock = _Clock()
    sleeps = _patch_time(monkeypatch, clock)
    lim = RateLimiter(rate_per_s=2.0)  # interval 0.5s
    for _ in range(4):
        lim.acquire()
    # Frozen clock: each acquire schedules one interval further out, so the
    # waits grow by the interval and never overlap. The first acquire has no
    # backlog (wait 0) and so records no sleep.
    assert sleeps == [0.5, 1.0, 1.5]


def test_acquire_no_wait_once_enough_time_elapsed(monkeypatch):
    clock = _Clock()
    sleeps = _patch_time(monkeypatch, clock)
    lim = RateLimiter(rate_per_s=4.0)  # interval 0.25s
    lim.acquire()
    clock.now += 10.0  # plenty of idle time
    lim.acquire()
    # Neither acquire had to wait, so no sleeps were issued.
    assert sleeps == []


def test_penalize_widens_then_decays_back_to_base(monkeypatch):
    clock = _Clock()
    _patch_time(monkeypatch, clock)
    lim = RateLimiter(rate_per_s=10.0)  # base interval 0.1s
    base = lim.interval
    assert base == pytest.approx(0.1)

    lim.penalize()
    widened = lim.interval
    assert widened > base

    # Healthy acquires (clock advances so there's no backlog) decay the
    # interval monotonically back to base.
    prev = widened
    for _ in range(60):
        clock.now += 1.0
        lim.acquire()
        cur = lim.interval
        assert cur <= prev
        prev = cur
    assert lim.interval == pytest.approx(base)


def test_penalize_is_capped(monkeypatch):
    clock = _Clock()
    _patch_time(monkeypatch, clock)
    lim = RateLimiter(rate_per_s=1.0, penalty_factor=2.0, max_interval_mult=8.0)
    for _ in range(20):
        lim.penalize()
    assert lim.interval == pytest.approx(8.0)  # base 1.0s * 8


def test_reschedule_adopts_leased_window(monkeypatch):
    clock = _Clock()
    sleeps = _patch_time(monkeypatch, clock)
    lim = RateLimiter(rate_per_s=2.0)  # base interval 0.5s
    lim.reschedule(2.0, clock.now + 3.0)
    lim.acquire()
    # First slot no earlier than not_before; then leased spacing, and the
    # per-acquire decay can't undercut it (reschedule pins the base too).
    assert sleeps == [3.0]
    lim.acquire()
    assert sleeps == [3.0, 5.0]
    assert lim.interval == pytest.approx(2.0)


def test_reschedule_never_moves_next_slot_earlier(monkeypatch):
    clock = _Clock()
    sleeps = _patch_time(monkeypatch, clock)
    lim = RateLimiter(rate_per_s=1.0)
    lim.acquire()  # schedules the next slot at now + 1.0
    lim.reschedule(1.0, clock.now + 0.2)  # earlier not_before must not win
    lim.acquire()
    assert sleeps == [1.0]


def test_a_local_limiter_has_no_deadline_and_a_client_can_refuse_the_run() -> None:
    """`refuse` is how a client that knows its next fetch cannot start in time (the `web`
    client's host pacing) hands the rest of the run back; the runner reads `refused`."""
    from scraper.rate_ledger import LedgerRateLimiter

    local = RateLimiter(1.0)
    assert local.deadline is None and not local.refused
    local.refuse("deadline")
    assert (local.refused, local.refused_reason) == (True, "deadline")
    ledger = LedgerRateLimiter("web", 1.0, deadline=123.0, connect=lambda: None)
    assert ledger.deadline == 123.0


def test_a_stop_ends_the_wait_for_a_slot_and_refuses_the_run() -> None:
    """#2019: the limiter waits on the stop event, not a sleep; the run is refused so the drain hands claims back."""
    stop.install()
    lim = RateLimiter(0.2)  # five seconds between slots
    lim.acquire()
    threading.Timer(0.05, stop.EVENT.set).start()
    started = time.monotonic()
    with pytest.raises(stop.StopRequested):
        lim.acquire()
    assert time.monotonic() - started < 1.0
    assert (lim.refused, lim.refused_reason) == (True, "stop")


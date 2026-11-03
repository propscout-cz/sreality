"""The realtime worker's `email` lane (docs/design/email/PROGRAM.md PR 4), hermetic: a constant 60 s tick between the
push lane and the heartbeat; the brake (no RESEND_API_KEY: `unconfigured`, no connection); a configured pass handing
api/notification_email.deliver_once a bounded connect, its steps `lane/email/<step>` cells; and an abandoned pass
never overlapped."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from api import notification_email as ne
from scraper import realtime_worker as rw


@pytest.fixture()
def lane(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fresh pass lock and a connect that records whether the lane opened anything."""
    opened: dict[str, Any] = {"conns": 0}

    def connect(**kwargs: Any) -> object:
        opened["kwargs"] = kwargs
        opened["conns"] += 1
        return object()

    monkeypatch.setattr(rw.db, "connect", connect)
    monkeypatch.setattr(rw, "_EMAIL_PASS_LOCK", rw._PassLock("email"))
    return opened


def _run(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or rw._new_state()
    asyncio.run(rw._email_pass(asyncio.Event(), state))
    return state


def test_the_email_lane_ticks_on_a_constant_between_push_and_heartbeat() -> None:
    lane = inspect.getsource(rw._amain).split('("email"', 1)[1].split(")),", 1)[0]
    assert "lambda: EMAIL_TICK_SECONDS" in lane and "default_interval=EMAIL_TICK_SECONDS" in lane
    assert rw.EMAIL_TICK_SECONDS == 60.0
    at = rw.LANES.index("email")
    assert rw.LANES[at - 1:at + 2] == ("push", "email", "heartbeat")
    assert "app_settings" not in inspect.getsource(rw._email_sync)
    assert "- email:" in rw.__doc__
    assert ne.STEPS == ("settings", "select", "send")


def test_the_brake_is_no_resend_key_and_opens_nothing(lane: dict[str, Any]) -> None:
    state = rw._new_state()
    state["lanes"]["email"] = {"portal_errors": {"send": {"failing_since": "2026-11-03T00:00:00+00:00",
                                                         "last_error": "1 of 1 accounts failed: status_401"}}}
    _run(state)
    entry = state["lanes"]["email"]
    assert entry["last"] == {"unconfigured": True} and entry["passes"] == 1
    assert entry["portal_errors"] == {}, "braked, no step can be failing"
    assert lane["conns"] == 0


def test_a_configured_pass_runs_once_with_a_bounded_connect(lane: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    seen: list[Any] = []

    def deliver_once(connect: Any, *, stop: Any) -> dict[str, Any]:
        seen.append((connect(), stop()))
        return {"candidates": 0, "sent": 0, "attempted": list(ne.STEPS), "raised": {"select": "OperationalError"}}

    monkeypatch.setattr(ne, "deliver_once", deliver_once)
    state = _run()
    assert len(seen) == 1 and seen[0][1] is False and lane["conns"] == 1
    assert lane["kwargs"] == {"connect_timeout": rw.EMAIL_CONNECT_TIMEOUT_SECONDS}
    entry = state["lanes"]["email"]
    assert entry["last"] == {"candidates": 0, "sent": 0}
    assert set(entry["portal_errors"]) == {"select"}
    assert entry["portal_errors"]["select"]["last_error"] == "OperationalError"
    Jsonb(rw._lane_snapshot(state["lanes"]))


def test_an_abandoned_email_pass_is_never_overlapped(lane: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    assert rw._EMAIL_PASS_LOCK.try_enter()
    try:
        assert rw._email_sync(asyncio.Event()) == {"previous_pass_running": True}
    finally:
        rw._EMAIL_PASS_LOCK.release()
    assert lane["conns"] == 0

"""The realtime worker's `push` lane (notifications push W4, PROGRAM.md § 3.3), hermetic: a constant 15 s tick between
the notifications lane and the heartbeat; the brake (no VAPID_PRIVATE_KEY: `unconfigured`, no connection); an
unreadable key as its own cell; a pass on one connection whose steps are `lane/push/<step>` cells; and a
ConnectionError driven through the whole lane leaving no push.apple.com anywhere in the heartbeat or the log."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from typing import Any

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg.types.json import Jsonb

from api import push
from api import push_delivery as pd
from scraper import realtime_worker as rw
from tests.api._script_conn import ScriptConn

ENDPOINT = "https://web.push.apple.com/QGuHfVhTMmGc4fZDuWs8fIY4ELbdpXFgx8ibAmN3UDPqx0QvGhIA9xlu7Ok"


class _Conn(ScriptConn):
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def lane(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A fresh pass lock and a connection that records whether the lane opened one."""
    opened: dict[str, Any] = {"conns": []}

    def connect(**kwargs: Any) -> _Conn:
        opened["kwargs"] = kwargs
        opened["conns"].append(_Conn(opened.get("script", [])))
        return opened["conns"][-1]

    monkeypatch.setattr(rw.db, "connect", connect)
    monkeypatch.setattr(rw, "_PUSH_PASS_LOCK", rw._PassLock("push"))
    return opened


def _run(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or rw._new_state()
    asyncio.run(rw._push_pass(asyncio.Event(), state))
    return state


def test_the_push_lane_ticks_on_a_constant_between_notifications_and_heartbeat() -> None:
    lane = inspect.getsource(rw._amain).split('("push"', 1)[1].split(")),", 1)[0]
    assert "lambda: PUSH_TICK_SECONDS" in lane and "default_interval=PUSH_TICK_SECONDS" in lane
    assert rw.PUSH_TICK_SECONDS == 15.0
    at = rw.LANES.index("push")
    assert rw.LANES[at - 1:at + 2] == ("notifications", "push", "heartbeat")
    assert "app_settings" not in inspect.getsource(rw._push_sync)
    assert "- push:" in rw.__doc__
    assert rw.PUSH_STEPS == ("key", "fan_out", "claim", "send")


def test_the_brake_is_no_vapid_key_and_opens_nothing(lane: dict[str, Any]) -> None:
    state = rw._new_state()
    state["lanes"]["push"] = {"portal_errors": {"send": {"failing_since": "2026-10-21T00:00:00+00:00",
                                                        "last_error": "1 of 1 devices failed: ValueError"}}}
    _run(state)
    entry = state["lanes"]["push"]
    assert entry["last"] == {"unconfigured": True} and entry["passes"] == 1
    assert entry["portal_errors"] == {}, "braked, no step can be failing"
    assert lane["conns"] == []


def test_an_unreadable_key_is_the_key_cell_and_claims_nothing(lane: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setenv(push.VAPID_ENV, "-----BEGIN PRIVATE KEY----- s3cr3t")
    state = _run()
    errors = state["lanes"]["push"]["portal_errors"]
    assert set(errors) == {"key"}
    assert errors["key"]["last_error"] == "VAPID_PRIVATE_KEY is not a base64url 32-byte P-256 scalar"
    assert lane["conns"] == [] and "s3cr3t" not in json.dumps(state, default=str)


def test_a_configured_pass_runs_once_on_one_bounded_connection(lane: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setenv(push.VAPID_ENV, push.b64url(os.urandom(32)))
    seen: list[Any] = []

    def deliver_once(conn: Any, *, stop: Any) -> dict[str, Any]:
        seen.append((conn, stop()))
        return {"live_devices": 0, "pushes": 0, "attempted": list(rw.PUSH_STEPS),
                "raised": {"claim": "OperationalError"}}

    monkeypatch.setattr(pd, "deliver_once", deliver_once)
    state = _run()
    [conn] = lane["conns"]
    assert seen == [(conn, False)] and conn.closed
    assert lane["kwargs"] == {"connect_timeout": rw.PUSH_CONNECT_TIMEOUT_SECONDS}
    entry = state["lanes"]["push"]
    assert entry["last"] == {"live_devices": 0, "pushes": 0}, "'no devices' reads as live_devices 0"
    assert set(entry["portal_errors"]) == {"claim"}
    assert entry["portal_errors"]["claim"]["last_error"] == "OperationalError"
    Jsonb(rw._lane_snapshot(state["lanes"]))


def test_an_abandoned_push_pass_is_never_overlapped(lane: dict[str, Any], monkeypatch) -> None:
    monkeypatch.setenv(push.VAPID_ENV, push.b64url(os.urandom(32)))
    assert rw._PUSH_PASS_LOCK.try_enter()
    try:
        assert rw._push_sync(asyncio.Event()) == {"previous_pass_running": True}
    finally:
        rw._PUSH_PASS_LOCK.release()
    assert lane["conns"] == []


def test_a_connection_error_through_the_lane_reaches_no_heartbeat_and_no_log(
        lane: dict[str, Any], monkeypatch, caplog) -> None:
    """PROGRAM.md § 3.4 "Endpoint secrecy": the whole lane, the real transport, a refused connection."""
    monkeypatch.setenv(push.VAPID_ENV, push.b64url(os.urandom(32)))
    p256dh = push.b64url(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(*push._POINT))
    row = (5, 9, 1, "00000000-0000-4000-8000-000000000005", "acc", "new", False, True, False, 80_000, False,
           ENDPOINT, p256dh, push.b64url(os.urandom(16)), "https://app.example.cz", 5_000_000, None, "Praha",
           "byt", None, "2+kk", None)
    norm = lambda sql: (lambda q: q == " ".join(sql.split()))  # noqa: E731
    lane["script"] = [(norm(pd.LIVE_DEVICES_SQL), [(1,)]), (norm(pd.FANOUT_SQL), [("pending",)]),
                      (norm(pd.CLAIM_SQL), [row]), (norm(pd.UNREAD_SQL), [(1,)])]

    def refused(url: str, **kwargs: Any) -> Any:
        raise requests.ConnectionError(f"Max retries exceeded with url: {url} (Caused by NewConnectionError)")

    monkeypatch.setattr(push.requests, "post", refused)
    with caplog.at_level(logging.DEBUG):
        state = _run()
    entry = state["lanes"]["push"]
    assert entry["last"]["retry"] == 1 and entry["portal_errors"] == {}
    heartbeat = json.dumps(rw._lane_snapshot(state["lanes"]), default=str)
    assert "push.apple.com" not in heartbeat and "push.apple.com" not in caplog.text
    retry = [p for q, p in lane["conns"][0].executed if q == " ".join(pd.RETRY_SQL.split())]
    assert retry and retry[0]["reason"] == "ConnectionError"

"""api/push_delivery.py, hermetic: the pass over a scripted connection — fan-out and claim as written, the re-check's
four skips, ONE push per device (coalesced past one row), each outcome's statement, the stop signal between devices,
and no endpoint in anything a failure leaves behind. The statements run on the real schema (concurrent claims, the
lease, a 429's hold, fan-out's registration time, VISIBLE and the share cap) in tests/test_notifications_push_live.py."""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec

from api import push
from api import push_delivery as pd
from api.notifications import VISIBLE_SQL
from tests.api._script_conn import ScriptConn

ACCOUNT = uuid.UUID("22222222-2222-2222-2222-222222222222")
ORIGIN = "https://app.example.cz"
P256DH = push.b64url(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(*push._POINT))
AUTH = push.b64url(os.urandom(16))


def _endpoint(device: int) -> str:
    return f"https://web.push.apple.com/device-{device}-QGuHfVhTMmGc4fZDuWs8fIY4ELbdpXFgx8ib"


def _is(sql: str) -> Any:
    norm = " ".join(sql.split())
    return lambda q: q == norm


def _row(delivery: int, device: int, *, kind: str = "new", seen: bool = False, visible: bool = True,
         stale: bool = False, ttl: int = 80_000, ended: bool = False, label: str = "Praha 5",
         dispatch: str | None = None) -> tuple[Any, ...]:
    """One CLAIM_SQL row, in its select's column order."""
    return (delivery, device, 1, dispatch or f"00000000-0000-4000-8000-{delivery:012d}", ACCOUNT, kind, seen,
            visible, stale, ttl, ended, None if ended else _endpoint(device), None if ended else P256DH,
            None if ended else AUTH, ORIGIN, 6_490_000, 6_990_000, label, "byt", None, "2+kk", "za nemovitost")


def _conn(claimed: list[tuple[Any, ...]], *, fanout: Any = None, unread: int = 4) -> ScriptConn:
    return ScriptConn([
        (_is(pd.LIVE_DEVICES_SQL), [(2,)]),
        (_is(pd.FANOUT_SQL), fanout if fanout is not None else [("pending",), ("pending",), ("skipped",)]),
        (_is(pd.CLAIM_SQL), claimed),
        (_is(pd.UNREAD_SQL), [(unread,)]),
    ])


class _Send:
    """api.push.send, faked: records each push and answers `outcomes` in turn (an exception is raised)."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes) or [push.Outcome("sent", provider_message_id="M-1")]
        self.calls: list[dict[str, Any]] = []

    def __call__(self, endpoint: str, origin: str, p256dh: str, auth: str, payload: dict[str, Any],
                 **kwargs: Any) -> push.Outcome:
        self.calls.append({"endpoint": endpoint, "origin": origin, "payload": payload, **kwargs})
        answer = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _after(conn: ScriptConn, sql: str) -> list[Any]:
    norm = " ".join(sql.split())
    return [p for q, p in conn.executed if q == norm]


# --- the statements -------------------------------------------------------------------------


def test_fan_out_is_every_visible_unseen_event_of_a_day_times_its_accounts_older_live_devices() -> None:
    sql = " ".join(pd.FANOUT_SQL.split())
    assert "JOIN push_subscriptions ps ON ps.account_id = d.account_id AND ps.ended_at IS NULL" in sql
    assert "AND ps.created_at < d.dispatched_at" in sql
    assert "WHERE d.dispatched_at > now() - interval '24 hours' AND d.seen_at IS NULL" in sql
    assert " ".join(VISIBLE_SQL.split()) in sql
    assert sql.endswith("ON CONFLICT (notification_id, push_subscription_id) DO NOTHING RETURNING status")
    # R11: another push of the same sharer to the same account within a day (sent, or on its way) caps it.
    assert "xd.actor_account_id = d.actor_account_id AND xd.id <> d.id" in sql
    assert "(x.status = 'sent' AND x.sent_at > now() - interval '24 hours')" in sql
    assert "CASE WHEN c.capped THEN 'skipped' ELSE 'pending' END" in sql


def test_the_claim_is_a_leased_skip_locked_compare_and_set_of_at_most_ten_devices() -> None:
    sql = " ".join(pd.CLAIM_SQL.split())
    assert "WHERE status IN ('pending', 'failed') AND next_attempt_at <= now() AND attempts < %(max_attempts)s" in sql
    assert "ORDER BY next_attempt_at, id LIMIT %(max_rows)s FOR UPDATE SKIP LOCKED" in sql
    assert "SET next_attempt_at = now() + interval '2 minutes', attempts = x.attempts + 1" in sql
    assert "LIMIT %(max_devices)s" in sql and " ".join(VISIBLE_SQL.split()) in sql
    # A device Apple throttled is skipped in the window itself: its rows neither fill it nor get locked.
    due = sql.split("), devices AS", 1)[0]
    assert ("NOT EXISTS (SELECT 1 FROM push_subscriptions h WHERE h.id = notification_deliveries.push_subscription_id "
            "AND h.hold_until > now())") in due
    assert (pd.MAX_ATTEMPTS, pd.MAX_DEVICES, pd.MAX_ROWS) == (5, 10, 200)
    assert pd.MAX_DEVICES * 10 < 120, "ten 10 s POSTs fit one 2-minute lease"


def test_every_finalize_touches_only_rows_still_claimed() -> None:
    for sql in (pd.SENT_SQL, pd.GONE_SQL, pd.RETRY_SQL, pd.FAIL_SQL, pd.SKIP_SQL):
        assert "WHERE id = ANY(%(ids)s) AND status IN ('pending', 'failed')" in " ".join(sql.split())
    assert "THEN 'infinity'::timestamptz ELSE now() + make_interval(mins => power(2, attempts)::int)" in " ".join(
        pd.RETRY_SQL.split())


# --- the pass -------------------------------------------------------------------------------


def test_one_push_per_device_and_several_rows_coalesce() -> None:
    conn = _conn([_row(11, 1, kind="price_drop"), _row(12, 1, kind="new", label="Brno"), _row(13, 1, label="Kolín"),
                  _row(14, 1, kind="inactive"), _row(20, 2, kind="shared_pipeline")], unread=7)
    send = _Send()
    last = pd.deliver_once(conn, stop=lambda: False, send=send)
    assert [c["endpoint"] for c in send.calls] == [_endpoint(1), _endpoint(2)]
    many, one = send.calls[0]["payload"], send.calls[1]["payload"]
    assert many["notification"]["title"] == "4 nová upozornění"
    assert many["notification"]["body"].split("\n") == [
        "Zlevněno · Praha 5", "Nový inzerát · Brno", "Nový inzerát · Kolín"]
    assert many["notification"]["navigate"] == f"{ORIGIN}/notifications?d=11"
    assert one["notification"] == {"title": "Sdíleno s vámi", "body": "Pipeline",
                                   "navigate": f"{ORIGIN}/n/00000000-0000-4000-8000-000000000020?d=20",
                                   "lang": "cs", "app_badge": 7}
    assert one["web_push"] == 8030 and one["app_badge"] == 7 == many["app_badge"]
    assert _after(conn, pd.SENT_SQL) == [
        {"ids": [11, 12, 13, 14], "device": 1, "reason": None, "max_attempts": 5, "message_id": "M-1", "wait": 60},
        {"ids": [20], "device": 2, "reason": None, "max_attempts": 5, "message_id": "M-1", "wait": 60}]
    assert (last["pushes"], last["sent"], last["claimed"], last["fanned_out"], last["capped"]) == (2, 2, 5, 2, 1)
    assert last["raised"] == {} and last["live_devices"] == 2


def test_ttl_is_the_newest_events_time_left_and_never_under_a_minute_and_topic_names_the_batch() -> None:
    conn = _conn([_row(31, 3, ttl=40), _row(32, 3, ttl=10), _row(40, 4, ttl=0)])
    send = _Send()
    pd.deliver_once(conn, stop=lambda: False, send=send)
    assert [(c["ttl_s"], c["urgency"], c["topic"]) for c in send.calls] == [
        (60, "high", pd.topic(3, 31)), (60, "high", pd.topic(4, 40))]
    conn = _conn([_row(31, 3, ttl=3_000), _row(32, 3, ttl=86_000)])
    send = _Send()
    pd.deliver_once(conn, stop=lambda: False, send=send)
    assert send.calls[0]["ttl_s"] == 86_000
    assert len(pd.topic(3, 31)) == 22 and pd.topic(3, 31) != pd.topic(3, 32) != pd.topic(4, 31)
    assert set(pd.topic(3, 31)) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@pytest.mark.parametrize(("row", "reason"), [
    (_row(50, 5, seen=True), "seen"),
    (_row(51, 5, visible=False), "hidden"),
    (_row(52, 5, stale=True), "stale"),
    (_row(53, 5, ended=True), "device_ended"),
    (_row(54, 5, seen=True, visible=False, stale=True, ended=True), "seen"),
])
def test_the_recheck_skips_with_its_reason_and_sends_nothing_for_it(row: tuple[Any, ...], reason: str) -> None:
    conn = _conn([row, _row(60, 6)])
    send = _Send()
    last = pd.deliver_once(conn, stop=lambda: False, send=send)
    assert _after(conn, pd.SKIP_SQL) == [{"reason": reason, "ids": [row[0]]}]
    assert [c["endpoint"] for c in send.calls] == [_endpoint(6)]
    assert last["skipped"] == {reason: 1}


@pytest.mark.parametrize(("outcome", "sql", "extra"), [
    (push.Outcome("sent", provider_message_id="C2E1"), pd.SENT_SQL, {"message_id": "C2E1"}),
    (push.Outcome("retry", "ConnectionError"), pd.RETRY_SQL, {"reason": "ConnectionError"}),
    (push.Outcome("failed", "BadJwtToken"), pd.FAIL_SQL, {"reason": "BadJwtToken"}),
], ids=lambda v: getattr(v, "kind", None))
def test_each_outcome_is_one_statement(outcome: push.Outcome, sql: str, extra: dict[str, Any]) -> None:
    conn = _conn([_row(70, 7), _row(71, 7)])
    last = pd.deliver_once(conn, stop=lambda: False, send=_Send(outcome))
    [params] = _after(conn, sql)
    assert params["ids"] == [70, 71] and params["device"] == 7
    assert {k: params[k] for k in extra} == extra
    assert last[outcome.kind] == 1 and last["pushes"] == 1
    others = {pd.SENT_SQL, pd.THROTTLED_SQL, pd.HOLD_DEVICE_SQL, pd.RETRY_SQL, pd.FAIL_SQL, pd.GONE_SQL,
              pd.END_DEVICE_SQL} - {sql}
    assert not any(_after(conn, s) for s in others)


def test_a_429_holds_the_device_and_gives_the_rows_their_attempt_back_five_times_running() -> None:
    """Five passes, five 429s: each holds the device for Retry-After and hands the claimed rows their attempt back
    (due again, still pending), in one transaction; never the retry clock, never the terminal failure."""
    throttled = push.Outcome("throttled", "TooManyRequests", retry_after_s=120)
    for _ in range(5):
        conn = _conn([_row(70, 7), _row(71, 7)])
        last = pd.deliver_once(conn, stop=lambda: False, send=_Send(throttled))
        assert last["throttled"] == 1 and last["raised"] == {}
        [rows] = _after(conn, pd.THROTTLED_SQL)
        [device] = _after(conn, pd.HOLD_DEVICE_SQL)
        assert (rows["ids"], rows["reason"], device["device"], device["wait"]) == (
            [70, 71], "TooManyRequests", 7, 120)
        assert not any(_after(conn, s) for s in (pd.RETRY_SQL, pd.FAIL_SQL, pd.SENT_SQL))
        assert conn.tx == ["commit"]
    sql = " ".join(pd.THROTTLED_SQL.split())
    assert "attempts = greatest(attempts - 1, 0), next_attempt_at = now()" in sql and "status =" not in sql
    assert "hold_until = greatest(hold_until, now() + make_interval(secs => %(wait)s))" in pd.HOLD_DEVICE_SQL
    assert "reason = coalesce(reason, 'lease_expired')" in pd.EXHAUSTED_SQL


def test_gone_ends_the_device_with_its_rows_in_one_transaction() -> None:
    conn = _conn([_row(80, 8)])
    pd.deliver_once(conn, stop=lambda: False, send=_Send(push.Outcome("gone", "Unregistered")))
    assert _after(conn, pd.GONE_SQL)[0]["reason"] == "Unregistered"
    assert _after(conn, pd.END_DEVICE_SQL)[0]["device"] == 8
    assert conn.tx == ["commit"]


def test_the_stop_signal_is_read_between_devices() -> None:
    conn = _conn([_row(90, 9), _row(91, 10), _row(92, 11)])
    send = _Send()
    asked: list[int] = []

    def stop() -> bool:
        asked.append(len(send.calls))
        return len(send.calls) >= 1

    last = pd.deliver_once(conn, stop=stop, send=send)
    assert len(send.calls) == 1 and last["stopped"] is True and asked == [0, 1]
    assert [p["ids"] for p in _after(conn, pd.SENT_SQL)] == [[90]], "the rest come back after their lease"


def test_a_device_that_raises_is_counted_by_class_and_the_next_still_sends(caplog) -> None:
    conn = _conn([_row(100, 12), _row(101, 13)])
    send = _Send(ValueError(f"bad key for {_endpoint(12)}"), push.Outcome("sent"))
    with caplog.at_level(logging.INFO, logger="api.push_delivery"):
        last = pd.deliver_once(conn, stop=lambda: False, send=send)
    assert last["raised"] == {"send": "1 of 2 devices failed: ValueError"}
    assert last["sent"] == 1 and len(send.calls) == 2
    assert "push.apple.com" not in caplog.text and "push.apple.com" not in json.dumps(last)


def test_a_step_that_raises_is_its_class_name_and_the_claim_still_runs(caplog) -> None:
    import psycopg

    failing = psycopg.errors.CheckViolation(f"Failing row contains ({_endpoint(1)}, {P256DH})")
    conn = _conn([_row(110, 14)], fanout=failing)
    with caplog.at_level(logging.INFO, logger="api.push_delivery"):
        last = pd.deliver_once(conn, stop=lambda: False, send=_Send())
    assert last["raised"] == {"fan_out": "CheckViolation"} and last["sent"] == 1
    assert "push.apple.com" not in caplog.text and P256DH not in caplog.text


def test_a_connection_error_through_the_real_transport_leaves_no_endpoint(monkeypatch, caplog) -> None:
    monkeypatch.setenv(push.VAPID_ENV, push.b64url(os.urandom(32)))

    def refused(url: str, **kwargs: Any) -> Any:
        raise requests.ConnectionError(f"HTTPSConnectionPool(host='web.push.apple.com'): {url}")

    monkeypatch.setattr(push.requests, "post", refused)
    conn = _conn([_row(120, 15)])
    with caplog.at_level(logging.DEBUG):
        last = pd.deliver_once(conn, stop=lambda: False)
    assert last["retry"] == 1 and last["raised"] == {}
    assert _after(conn, pd.RETRY_SQL)[0]["reason"] == "ConnectionError"
    assert "push.apple.com" not in caplog.text and "push.apple.com" not in json.dumps(last, default=str)


def test_a_quiet_pass_logs_nothing(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="api.push_delivery"):
        last = pd.deliver_once(_conn([], fanout=[]), stop=lambda: False, send=_Send())
    assert caplog.text == "" and last["pushes"] == 0 and last["skipped"] == {}


def test_czech_counts_agree_with_their_number() -> None:
    assert [pd.coalesced_title(n) for n in (2, 3, 4, 5, 12)] == [
        "2 nová upozornění", "3 nová upozornění", "4 nová upozornění", "5 nových upozornění",
        "12 nových upozornění"]

"""Migration 654 and the push lane's statements, executed (notifications push W4, PROGRAM.md § 5 W4's gate): fan-out
respects registration time, VISIBLE, the day and the share cap; a claim is a lease, and a claim whose process died
is due again after it; two concurrent claims never share a row; a 429 holds the whole device; every outcome lands in
a row the CHECKs accept; one full pass over the real schema; and the device registry's PUT, DELETE and opened
semantics. Runs in CI's migrations job (`TEST_DATABASE_URL`, DB_RAILS_REQUIRED=1); every test rolls back but the
concurrency one, which commits its own rows and deletes their accounts after."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from psycopg.types.json import Jsonb

from api import push, push_devices
from api import push_delivery as pd
from tests._live_property import REQUIRED_DB, _account, _advert, _property, cur, db_url  # noqa: F401

pytestmark = REQUIRED_DB

ORIGIN = "https://app.example.cz"
P256DH = push.b64url(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(*push._POINT))
AUTH = push.b64url(os.urandom(16))


def _endpoint() -> str:
    return f"https://web.push.apple.com/{uuid.uuid4().hex}{uuid.uuid4().hex}"


def _device(cur: Any, account: uuid.UUID, *, registered: str = "1 hour") -> int:
    cur.execute(
        "INSERT INTO push_subscriptions (account_id, endpoint, p256dh, auth, origin, created_at, last_seen_at) "
        "VALUES (%s, %s, %s, %s, %s, now() - %s::interval, now() - %s::interval) RETURNING id",
        (account, _endpoint(), P256DH, AUTH, ORIGIN, registered, registered))
    return int(cur.fetchone()[0])


def _share(cur: Any, recipient: uuid.UUID, actor: uuid.UUID, *, ago: str = "10 minutes",
           seen: bool = False) -> uuid.UUID:
    cur.execute(
        "INSERT INTO notification_dispatches (source_kind, change_kind, account_id, actor_account_id, event_ref, "
        "  dispatched_at, seen_at) "
        "VALUES ('share', 'shared_pipeline', %s, %s, %s, now() - %s::interval, "
        "        CASE WHEN %s THEN now() END) RETURNING id",
        (recipient, actor, uuid.uuid4().hex[:12], ago, seen))
    return cur.fetchone()[0]


def _watchdog_new(cur: Any, account: uuid.UUID, *, ago: str = "10 minutes") -> tuple[uuid.UUID, int, uuid.UUID]:
    """A watchdog's 'new' on a fresh property: (watchdog, property, dispatch)."""
    cur.execute("INSERT INTO notification_subscriptions (account_id, name, filter_spec) "
                "VALUES (%s, 'w', '{}'::jsonb) RETURNING id", (account,))
    sub = cur.fetchone()[0]
    pid = _property(cur)
    lid = _advert(cur, pid, source="idnes")
    cur.execute(
        "INSERT INTO notification_dispatches (subscription_id, source_kind, property_id, listing_id, change_kind, "
        "  trigger_price_czk, dispatched_at) "
        "VALUES (%s, 'watchdog', %s, %s, 'new', 5000000, now() - %s::interval) RETURNING id",
        (sub, pid, lid, ago))
    return sub, pid, cur.fetchone()[0]


def _ledger(cur: Any, device: int) -> dict[uuid.UUID, tuple[str, str | None]]:
    cur.execute("SELECT notification_id, status, reason FROM notification_deliveries "
                "WHERE push_subscription_id = %s", (device,))
    return {nid: (status, reason) for nid, status, reason in cur.fetchall()}


def _fan_out(cur: Any) -> list[str]:
    cur.execute(pd.FANOUT_SQL)
    return [s for (s,) in cur.fetchall()]


def _delivery(cur: Any, dispatch: uuid.UUID, device: int) -> int:
    cur.execute("INSERT INTO notification_deliveries (notification_id, push_subscription_id) VALUES (%s, %s) "
                "RETURNING id", (dispatch, device))
    return int(cur.fetchone()[0])


# --- fan-out ----------------------------------------------------------------------------------


def test_fan_out_is_visible_unseen_events_of_a_day_to_devices_registered_before_them(cur: Any) -> None:
    me, other, sharer = _account(cur), _account(cur), _account(cur)
    device = _device(cur, me, registered="1 hour")
    fresh = _share(cur, me, sharer)
    _share(cur, me, _account(cur), ago="25 hours")                    # past the day
    _share(cur, me, _account(cur), seen=True)                         # read in the app first
    _share(cur, me, _account(cur), ago="2 hours")                     # before the device registered
    _share(cur, other, sharer)                                        # another account's
    retired_sub, _p, retired = _watchdog_new(cur, me)
    cur.execute("UPDATE notification_subscriptions SET retired_at = now() WHERE id = %s", (retired_sub,))
    _s, dismissed_pid, dismissed = _watchdog_new(cur, me)
    cur.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (me, dismissed_pid))
    _s, _p, live_new = _watchdog_new(cur, me)
    cur.execute("INSERT INTO notification_dispatches (source_kind, change_kind, account_id, actor_account_id, "
                "event_ref, dispatched_at, duplicate_of) VALUES ('share', 'shared_pipeline', %s, %s, 'dup', "
                "now() - interval '5 minutes', %s)", (me, sharer, fresh))  # a marked repeat
    ended = _device(cur, me)
    cur.execute("UPDATE push_subscriptions SET ended_at = now(), end_reason = 'switched_off', endpoint = NULL, "
                "p256dh = NULL, auth = NULL WHERE id = %s", (ended,))

    assert _fan_out(cur).count("pending") == 2
    assert _ledger(cur, device) == {fresh: ("pending", None), live_new: ("pending", None)}
    assert _ledger(cur, ended) == {}
    assert retired not in _ledger(cur, device) and dismissed not in _ledger(cur, device)
    assert _fan_out(cur) == [], "one row per (event, device), ever"


def test_a_sharer_pushes_once_a_day_and_the_rest_reach_the_bell(cur: Any) -> None:
    me, sharer, someone = _account(cur), _account(cur), _account(cur)
    device = _device(cur, me)
    first = _share(cur, me, sharer, ago="20 minutes")
    _fan_out(cur)
    second = _share(cur, me, sharer, ago="5 minutes")                 # the first is still on its way
    other = _share(cur, me, someone, ago="5 minutes")
    _fan_out(cur)
    ledger = _ledger(cur, device)
    assert ledger[first] == ("pending", None) and ledger[other] == ("pending", None)
    assert ledger[second] == ("skipped", "capped")
    cur.execute("UPDATE notification_deliveries SET status = 'sent', sent_at = now() "
                "WHERE notification_id = %s", (first,))
    third = _share(cur, me, sharer, ago="1 minute")
    _fan_out(cur)
    assert _ledger(cur, device)[third] == ("skipped", "capped"), "one pushed in the last day caps the next"
    cur.execute("UPDATE notification_deliveries SET sent_at = now() - interval '25 hours' "
                "WHERE notification_id = %s", (first,))
    fourth = _share(cur, me, sharer, ago="30 seconds")
    _fan_out(cur)
    assert _ledger(cur, device)[fourth] == ("pending", None), "a day later the sharer may push again"


# --- the claim and its lease -------------------------------------------------------------------


def test_a_claim_leases_and_a_claim_whose_process_died_is_due_after_its_lease(cur: Any) -> None:
    me = _account(cur)
    device = _device(cur, me)
    row = _delivery(cur, _share(cur, me, _account(cur)), device)
    [claimed] = pd.claim(cur.connection)
    assert (claimed.delivery_id, claimed.attempts, claimed.endpoint is not None) == (row, 1, True)
    assert claimed.visible and not claimed.seen and not claimed.stale and not claimed.device_ended
    cur.execute("SELECT next_attempt_at - now() FROM notification_deliveries WHERE id = %s", (row,))
    assert cur.fetchone()[0].total_seconds() == 120
    assert pd.claim(cur.connection) == [], "leased"
    # The process died before finalizing: the lease runs out and the row is due again.
    cur.execute("UPDATE notification_deliveries SET next_attempt_at = now() - interval '1 second' WHERE id = %s",
                (row,))
    [again] = pd.claim(cur.connection)
    assert again.delivery_id == row and again.attempts == 2
    cur.execute("UPDATE notification_deliveries SET attempts = 5, next_attempt_at = now() WHERE id = %s", (row,))
    assert pd.claim(cur.connection) == []
    cur.execute(pd.EXHAUSTED_SQL, {"max_attempts": pd.MAX_ATTEMPTS})
    cur.execute("SELECT status, next_attempt_at = 'infinity', reason FROM notification_deliveries WHERE id = %s",
                (row,))
    assert cur.fetchone() == ("failed", True, "lease_expired"), "a crash-exhausted row is named"


@pytest.fixture()
def committed() -> Iterator[tuple[Any, Any, dict[str, Any]]]:
    """Two autocommit sessions over rows committed for this test alone; their accounts are deleted after."""
    import psycopg

    def session() -> Any:
        return psycopg.connect(db_url(), autocommit=True, options="-c statement_timeout=20000 -c lock_timeout=5000")

    seed, a, b = session(), session(), session()
    accounts: list[uuid.UUID] = []
    try:
        with seed.cursor() as c:
            me, sharer = _account(c), _account(c)
            accounts += [me, sharer]
            devices = [_device(c, me), _device(c, me)]
            rows = {d: [_delivery(c, _share(c, me, sharer), d) for _ in range(3)] for d in devices}
        yield a, b, {"devices": devices, "rows": rows}
    finally:
        a.close()
        b.close()
        with seed.cursor() as c:
            c.execute("DELETE FROM accounts WHERE id = ANY(%s)", (accounts,))
        seed.close()


def test_two_concurrent_claims_never_share_a_row(committed: tuple[Any, Any, dict[str, Any]]) -> None:
    a, b, seeded = committed
    every = {r for rows in seeded["rows"].values() for r in rows}
    with a.transaction():
        first = {c.delivery_id for c in pd.claim(a, max_devices=1)}
        assert len(first) == 3 and first < every
        # A still holds its rows: B skips every row A's claim locked and takes none of them.
        assert {c.delivery_id for c in pd.claim(b)} & first == set()
    second = {c.delivery_id for c in pd.claim(b)}
    assert second == every - first, "after A commits, B takes exactly the rest"
    assert pd.claim(a) == [] and pd.claim(b) == [], "both claims leased"


# --- outcomes ----------------------------------------------------------------------------------


def _hold_left_s(cur: Any, device: int) -> Any:
    cur.execute("SELECT round(extract(epoch FROM hold_until - now())) FROM push_subscriptions WHERE id = %s",
                (device,))
    return cur.fetchone()[0]


def test_a_429_holds_the_device_and_no_other_and_spends_no_attempt(cur: Any) -> None:
    me = _account(cur)
    held, free = _device(cur, me), _device(cur, me)
    sharer = _account(cur)
    first = _delivery(cur, _share(cur, me, sharer), held)
    waiting = _delivery(cur, _share(cur, me, sharer), held)
    elsewhere = _delivery(cur, _share(cur, me, sharer), free)
    claims = {c.delivery_id: c for c in pd.claim(cur.connection, max_devices=1)}
    assert set(claims) == {first, waiting}
    pd.finalize(cur.connection, held, [first, waiting],
                push.Outcome("throttled", "TooManyRequests", retry_after_s=120))
    cur.execute("SELECT id, status, attempts, reason, next_attempt_at <= now() FROM notification_deliveries "
                "WHERE id = ANY(%s) ORDER BY id", ([first, waiting, elsewhere],))
    assert cur.fetchall() == [(first, "pending", 0, "TooManyRequests", True),
                              (waiting, "pending", 0, "TooManyRequests", True),
                              (elsewhere, "pending", 0, None, True)]
    assert _hold_left_s(cur, held) == 120 and _hold_left_s(cur, free) is None
    assert [c.delivery_id for c in pd.claim(cur.connection)] == [elsewhere], "the held device is not claimed"


def test_five_429s_running_leave_the_rows_pending_with_every_attempt_and_the_device_held(cur: Any) -> None:
    me = _account(cur)
    device = _device(cur, me)
    row = _delivery(cur, _share(cur, me, _account(cur)), device)
    throttled = push.Outcome("throttled", "TooManyRequests", retry_after_s=60)
    for attempt in range(5):
        if attempt:  # the hold of the previous 429 has run out
            cur.execute("UPDATE push_subscriptions SET hold_until = now() - interval '1 second' WHERE id = %s",
                        (device,))
        last = pd.deliver_once(cur.connection, stop=lambda: False, send=lambda *a, **k: throttled)
        assert (last["claimed"], last["throttled"], last["exhausted"]) == (1, 1, 0), attempt
    cur.execute("SELECT status, attempts, reason FROM notification_deliveries WHERE id = %s", (row,))
    assert cur.fetchone() == ("pending", 0, "TooManyRequests")
    assert _hold_left_s(cur, device) == 60


def test_a_new_event_for_a_held_device_waits_out_the_hold(cur: Any) -> None:
    me = _account(cur)
    device = _device(cur, me)
    cur.execute("UPDATE push_subscriptions SET hold_until = now() + interval '5 minutes' WHERE id = %s", (device,))
    event = _share(cur, me, _account(cur), ago="1 minute")
    posted: list[Any] = []

    def send(*args: Any, **kwargs: Any) -> push.Outcome:
        posted.append(args)
        return push.Outcome("sent", provider_message_id="H-1")

    last = pd.deliver_once(cur.connection, stop=lambda: False, send=send)
    assert last["fanned_out"] == 1 and last["claimed"] == 0 and posted == []
    assert _ledger(cur, device) == {event: ("pending", None)}, "fan-out keeps writing; the row simply waits"
    cur.execute("UPDATE push_subscriptions SET hold_until = now() - interval '1 second' WHERE id = %s", (device,))
    assert pd.deliver_once(cur.connection, stop=lambda: False, send=send)["sent"] == 1 and len(posted) == 1


@pytest.mark.parametrize(("outcome", "attempts", "expected"), [
    (push.Outcome("sent", provider_message_id="C2E1-77"), 1, ("sent", None, "C2E1-77", True, False)),
    (push.Outcome("retry", "ConnectionError"), 1, ("failed", "ConnectionError", None, False, False)),
    (push.Outcome("retry", "ServiceUnavailable"), 5, ("failed", "ServiceUnavailable", None, False, True)),
    (push.Outcome("failed", "BadJwtToken"), 1, ("failed", "BadJwtToken", None, False, True)),
], ids=["sent", "retry", "retry-exhausted", "failed"])
def test_each_outcome_lands_in_a_row_the_checks_accept(cur: Any, outcome: push.Outcome, attempts: int,
                                                      expected: tuple[Any, ...]) -> None:
    me = _account(cur)
    device = _device(cur, me)
    row = _delivery(cur, _share(cur, me, _account(cur)), device)
    cur.execute("UPDATE notification_deliveries SET attempts = %s WHERE id = %s", (attempts, row))
    pd.finalize(cur.connection, device, [row], outcome)
    cur.execute("SELECT status, reason, provider_message_id, sent_at IS NOT NULL, next_attempt_at = 'infinity' "
                "FROM notification_deliveries WHERE id = %s", (row,))
    assert cur.fetchone() == expected
    if outcome.kind == "retry" and attempts == 1:
        cur.execute("SELECT next_attempt_at - now() FROM notification_deliveries WHERE id = %s", (row,))
        assert cur.fetchone()[0].total_seconds() == 120, "2^1 minutes"


def test_gone_ends_the_device_and_blanks_its_secrets(cur: Any) -> None:
    me = _account(cur)
    device = _device(cur, me)
    row = _delivery(cur, _share(cur, me, _account(cur)), device)
    pd.finalize(cur.connection, device, [row], push.Outcome("gone", "Unregistered"))
    cur.execute("SELECT status, reason FROM notification_deliveries WHERE id = %s", (row,))
    assert cur.fetchone() == ("gone", "Unregistered")
    cur.execute("SELECT endpoint, p256dh, auth, end_reason, ended_at IS NOT NULL FROM push_subscriptions "
                "WHERE id = %s", (device,))
    assert cur.fetchone() == (None, None, None, "gone", True)


def test_one_full_pass_over_the_real_schema(cur: Any) -> None:
    me, sharer = _account(cur), _account(cur)
    device = _device(cur, me)
    share = _share(cur, me, sharer)
    _sub, _pid, new = _watchdog_new(cur, me, ago="3 minutes")
    sent: list[dict[str, Any]] = []

    def send(endpoint: str, origin: str, p256dh: str, auth: str, payload: dict[str, Any],
             **kwargs: Any) -> push.Outcome:
        sent.append({"origin": origin, "payload": payload, **kwargs})
        return push.Outcome("sent", provider_message_id="A-1")

    last = pd.deliver_once(cur.connection, stop=lambda: False, send=send)
    assert last["raised"] == {} and (last["fanned_out"], last["claimed"], last["sent"]) == (2, 2, 1)
    [push_] = sent
    note = push_["payload"]["notification"]
    assert note["title"] == "2 nová upozornění" and note["app_badge"] == 2 == push_["payload"]["app_badge"]
    assert note["body"].split("\n")[0].startswith("Nový inzerát"), "newest first"
    assert note["body"].split("\n")[1] == "Sdíleno s vámi"
    cur.execute("SELECT min(id) FROM notification_deliveries WHERE push_subscription_id = %s", (device,))
    assert note["navigate"] == f"{ORIGIN}/notifications?d={cur.fetchone()[0]}"
    assert push_["ttl_s"] > 86_000 and push_["urgency"] == "high" and len(push_["topic"]) == 22
    assert _ledger(cur, device) == {share: ("sent", None), new: ("sent", None)}
    cur.execute("UPDATE notification_dispatches SET seen_at = now() WHERE id = %s", (new,))
    assert pd.deliver_once(cur.connection, stop=lambda: False, send=send)["claimed"] == 0


# --- the device registry -----------------------------------------------------------------------


def _live(cur: Any, endpoint: str) -> list[tuple[Any, ...]]:
    cur.execute("SELECT id, account_id, p256dh, created_at FROM push_subscriptions "
                "WHERE endpoint = %s AND ended_at IS NULL", (endpoint,))
    return cur.fetchall()


def _registration(endpoint: str, *, p256dh: str = P256DH) -> push_devices.Device:
    return push_devices.Device(endpoint, p256dh, AUTH, ORIGIN, "UA")


def test_a_registration_refreshes_moves_or_inserts_and_keeps_ten(cur: Any) -> None:
    me, other = _account(cur), _account(cur)
    endpoint = _endpoint()
    push_devices.register(cur.connection, me, _registration(endpoint))
    [(first, owner, _key, _born)] = _live(cur, endpoint)
    assert owner == me
    cur.execute("UPDATE push_subscriptions SET created_at = now() - interval '3 days' WHERE id = %s", (first,))
    rotated = push.b64url(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(*push._POINT))
    push_devices.register(cur.connection, me, _registration(endpoint, p256dh=rotated))
    [(again, _o, key, born)] = _live(cur, endpoint)
    assert again == first and key == rotated, "the same live row, its keys refreshed"
    cur.execute("SELECT now() - %s", (born,))
    assert cur.fetchone()[0].days == 3, "created_at never moves"

    push_devices.register(cur.connection, other, _registration(endpoint))
    [(moved, owner, _k, _b)] = _live(cur, endpoint)
    assert owner == other and moved != first
    cur.execute("SELECT end_reason, endpoint, p256dh, auth FROM push_subscriptions WHERE id = %s", (first,))
    assert cur.fetchone() == ("replaced", None, None, None)

    for _ in range(11):
        push_devices.register(cur.connection, me, _registration(_endpoint()))
    cur.execute("SELECT count(*) FILTER (WHERE ended_at IS NULL), count(*) FILTER (WHERE end_reason = 'replaced') "
                "FROM push_subscriptions WHERE account_id = %s", (me,))
    assert cur.fetchone() == (10, 2), "ten live; the eleventh's oldest and the moved one ended 'replaced'"


def test_an_end_is_idempotent_and_only_the_callers(cur: Any) -> None:
    me, other = _account(cur), _account(cur)
    endpoint = _endpoint()
    push_devices.register(cur.connection, me, _registration(endpoint))
    push_devices.end(cur.connection, other, endpoint, "signed_out")
    assert len(_live(cur, endpoint)) == 1, "another account cannot end my device"
    push_devices.end(cur.connection, me, endpoint, "switched_off")
    push_devices.end(cur.connection, me, endpoint, "signed_out")
    cur.execute("SELECT end_reason FROM push_subscriptions WHERE account_id = %s", (me,))
    assert cur.fetchall() == [("switched_off",)]


def test_an_open_is_stamped_once_on_the_callers_own_delivery(cur: Any) -> None:
    me, other = _account(cur), _account(cur)
    device = _device(cur, me)
    fresh = _delivery(cur, _share(cur, me, _account(cur)), device)
    mine = _delivery(cur, _share(cur, me, _account(cur)), device)
    theirs = _delivery(cur, _share(cur, other, _account(cur)), _device(cur, other))
    cur.execute("UPDATE notification_deliveries SET opened_at = now() - interval '1 hour' WHERE id = %s", (mine,))
    for delivery in (fresh, mine, theirs, 9_000_000_000):
        push_devices.mark_opened(cur.connection, me, delivery)
    cur.execute("SELECT id, now() - opened_at FROM notification_deliveries WHERE id = ANY(%s)",
                ([fresh, mine, theirs],))
    rows = dict(cur.fetchall())
    assert rows[fresh].total_seconds() == 0, "stamped"
    assert rows[mine].total_seconds() == 3600, "once: the first open stands"
    assert rows[theirs] is None, "another account's delivery is not mine to open"


# --- the notifications Health check ------------------------------------------------------------


def test_the_health_checks_reads_judge_the_real_ledger(cur: Any) -> None:
    """A stalled row, a lost push and three terminal failures, each read by the check's own statements."""
    from scripts.checks import notifications as nc
    from scripts.verify_pipeline import THRESHOLDS

    cur.execute("INSERT INTO worker_heartbeats (worker, beat_at, started_at, details) "
                "VALUES ('realtime-worker', now(), now(), %s) "
                "ON CONFLICT (worker) DO UPDATE SET beat_at = now(), details = EXCLUDED.details",
                (Jsonb({"push": {"last": {"live_devices": 1}}}),))
    me = _account(cur)
    device = _device(cur, me)
    _share(cur, me, _account(cur), ago="10 minutes")                  # no ledger row: lost
    _share(cur, me, _account(cur), ago="2 minutes")                   # inside the grace: not yet
    stuck = _delivery(cur, _share(cur, me, _account(cur)), device)
    cur.execute("UPDATE notification_deliveries SET next_attempt_at = now() - interval '11 minutes' "
                "WHERE id = %s", (stuck,))
    throttled_account = _account(cur)                                  # a 429's rows wait by design: no stall
    held = _device(cur, throttled_account)
    cur.execute("UPDATE push_subscriptions SET hold_until = now() + interval '30 minutes' WHERE id = %s", (held,))
    waiting = _delivery(cur, _share(cur, throttled_account, _account(cur), ago="20 minutes"), held)
    cur.execute("UPDATE notification_deliveries SET next_attempt_at = now() - interval '15 minutes' "
                "WHERE id = %s", (waiting,))
    for reason in ("BadDeviceToken", "BadDeviceToken", "400"):
        row = _delivery(cur, _share(cur, me, _account(cur)), device)
        cur.execute("UPDATE notification_deliveries SET status = 'failed', reason = %s, "
                    "next_attempt_at = 'infinity' WHERE id = %s", (reason, row))
    out = nc.check_notifications(cur.connection, THRESHOLDS)
    cells = {c["subject"]: (c["status"], c["value"]) for c in out["details"]["cells"]}
    assert cells == {"push/stall": ("fail", 1), "push/recall": ("fail", 1), "push/failures": ("fail", 3),
                     "push/unconfigured": ("ok", 2)}
    failures = next(c for c in out["details"]["cells"] if c["subject"] == "push/failures")
    assert failures["facts"]["reasons"] == ["400", "BadDeviceToken"]

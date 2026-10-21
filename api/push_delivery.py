"""The push lane's pass (notifications push W4, PROGRAM.md § 3.3–3.4): fan out, claim, re-check, coalesce, send,
finalize. scraper/realtime_worker.py's `push` lane runs `deliver_once` every 15 s on one autocommit connection.

No lock but the claim: fan-out is idempotent on the (event, device) pair, a claim moves a row's `next_attempt_at`
two minutes ahead (`FOR UPDATE SKIP LOCKED`, so two claims never share a row) and a process that dies holding one
leaves it due again when that lease runs out. One push per device per pass, finalized right after its POST, the
stop signal read between devices. An endpoint is never logged and never in an error: a step or a device that
raises is recorded by its exception's class name alone.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from api import push
from api.notification_render import render
from api.notifications import VISIBLE_SQL

LOG = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Ten devices a pass: ten 10 s POSTs stay inside one 2-minute lease.
MAX_DEVICES = 10
MAX_ROWS = 200
COALESCED_TITLES = 3
# The two SPA routes a push opens (tests/test_spa_route_contract.py pins both to frontend/src/lib/routes.ts).
LANDING_PATH = "/n/{dispatch_id}?d={delivery_id}"
FEED_PATH = "/notifications?d={delivery_id}"

LIVE_DEVICES_SQL = "SELECT count(*) FROM push_subscriptions WHERE ended_at IS NULL"

# (a) Every VISIBLE unseen event of the last 24 h x every live device of its account registered before it. A share
# past the cap (R11: one push per sharer and recipient a day; another of that sharer's shares already pushed in 24 h,
# or on its way) is written skipped/capped: it reaches the bell only.
FANOUT_SQL = (
    "INSERT INTO notification_deliveries (notification_id, push_subscription_id, status, reason) "
    "SELECT d.id, ps.id, CASE WHEN c.capped THEN 'skipped' ELSE 'pending' END, "
    "       CASE WHEN c.capped THEN 'capped' END "
    "FROM notification_dispatches d "
    "JOIN push_subscriptions ps ON ps.account_id = d.account_id AND ps.ended_at IS NULL "
    "  AND ps.created_at < d.dispatched_at "
    "CROSS JOIN LATERAL (SELECT d.source_kind = 'share' AND EXISTS ("
    "  SELECT 1 FROM notification_deliveries x JOIN notification_dispatches xd ON xd.id = x.notification_id "
    "   WHERE xd.account_id = d.account_id AND xd.source_kind = 'share' "
    "     AND xd.actor_account_id = d.actor_account_id AND xd.id <> d.id "
    "     AND ((x.status = 'sent' AND x.sent_at > now() - interval '24 hours') "
    "          OR (x.status IN ('pending', 'failed') AND x.next_attempt_at < 'infinity'))) AS capped) c "
    "WHERE d.dispatched_at > now() - interval '24 hours' AND d.seen_at IS NULL "
    f"  AND {VISIBLE_SQL} "
    "ON CONFLICT (notification_id, push_subscription_id) DO NOTHING "
    "RETURNING status"
)

# A row claimed MAX_ATTEMPTS times whose process died each time (never finalized) is failed for good.
EXHAUSTED_SQL = (
    "UPDATE notification_deliveries SET status = 'failed', next_attempt_at = 'infinity' "
    "WHERE status IN ('pending', 'failed') AND attempts >= %(max_attempts)s AND next_attempt_at <= now()"
)

# (b) The claim: the due rows of at most MAX_DEVICES devices, leased for 2 minutes, each with what it says and where
# it goes. The select re-checks (c) in the same statement: seen, VISIBLE, 24 h, the device still live.
CLAIM_SQL = (
    "WITH due AS ("
    "  SELECT id, push_subscription_id, next_attempt_at FROM notification_deliveries "
    "   WHERE status IN ('pending', 'failed') AND next_attempt_at <= now() AND attempts < %(max_attempts)s "
    "   ORDER BY next_attempt_at, id LIMIT %(max_rows)s FOR UPDATE SKIP LOCKED"
    "), devices AS ("
    "  SELECT push_subscription_id FROM due GROUP BY push_subscription_id "
    "   ORDER BY min(next_attempt_at), min(id) LIMIT %(max_devices)s"
    "), claimed AS ("
    "  UPDATE notification_deliveries x "
    "     SET next_attempt_at = now() + interval '2 minutes', attempts = x.attempts + 1 "
    "    FROM due JOIN devices USING (push_subscription_id) WHERE x.id = due.id "
    "  RETURNING x.id, x.notification_id, x.push_subscription_id, x.attempts"
    ") "
    "SELECT c.id, c.push_subscription_id, c.attempts, d.id::text, d.account_id, d.change_kind, "
    "       d.seen_at IS NOT NULL, "
    f"      ({VISIBLE_SQL}), "
    "       d.dispatched_at <= now() - interval '24 hours', "
    "       greatest(0, extract(epoch FROM d.dispatched_at + interval '24 hours' - now()))::int, "
    "       ps.ended_at IS NOT NULL, ps.endpoint, ps.p256dh, ps.auth, ps.origin, "
    "       d.trigger_price_czk, d.prev_price_czk, pp.display_label, "
    "       l.category_main, l.subtype, l.disposition, l.price_unit "
    "FROM claimed c "
    "JOIN notification_dispatches d ON d.id = c.notification_id "
    "JOIN push_subscriptions ps ON ps.id = c.push_subscription_id "
    "LEFT JOIN properties_public pp ON pp.property_id = d.property_id "
    "LEFT JOIN listings l ON l.id = d.listing_id "
    "ORDER BY c.push_subscription_id, d.dispatched_at DESC, c.id"
)

SKIP_SQL = (
    "UPDATE notification_deliveries SET status = 'skipped', reason = %(reason)s "
    "WHERE id = ANY(%(ids)s) AND status IN ('pending', 'failed')"
)

# The badge: the account's VISIBLE unseen count at send time, the bell's number.
UNREAD_SQL = (
    "SELECT count(*) FROM notification_dispatches d "
    f"WHERE d.account_id = %(account_id)s AND d.seen_at IS NULL AND {VISIBLE_SQL}"
)

# (f) Finalize, each guarded on the claimed state so a late writer never overwrites a finished row.
SENT_SQL = (
    "UPDATE notification_deliveries SET status = 'sent', sent_at = now(), provider_message_id = %(message_id)s, "
    "reason = NULL WHERE id = ANY(%(ids)s) AND status IN ('pending', 'failed')"
)
GONE_SQL = (
    "UPDATE notification_deliveries SET status = 'gone', reason = %(reason)s "
    "WHERE id = ANY(%(ids)s) AND status IN ('pending', 'failed')"
)
END_DEVICE_SQL = (
    "UPDATE push_subscriptions SET ended_at = now(), end_reason = 'gone', endpoint = NULL, p256dh = NULL, "
    "auth = NULL WHERE id = %(device)s AND ended_at IS NULL"
)
# A 429 holds the DEVICE: every due row of it waits Retry-After, still pending (or failed and retrying).
HOLD_SQL = (
    "UPDATE notification_deliveries "
    "SET next_attempt_at = now() + make_interval(secs => %(wait)s), "
    "    reason = CASE WHEN id = ANY(%(ids)s) THEN %(reason)s ELSE reason END "
    "WHERE push_subscription_id = %(device)s AND status IN ('pending', 'failed') "
    "  AND next_attempt_at < 'infinity' "
    "  AND (id = ANY(%(ids)s) OR next_attempt_at < now() + make_interval(secs => %(wait)s))"
)
RETRY_SQL = (
    "UPDATE notification_deliveries SET status = 'failed', reason = %(reason)s, "
    "next_attempt_at = CASE WHEN attempts >= %(max_attempts)s THEN 'infinity'::timestamptz "
    "  ELSE now() + make_interval(mins => power(2, attempts)::int) END "
    "WHERE id = ANY(%(ids)s) AND status IN ('pending', 'failed')"
)
FAIL_SQL = (
    "UPDATE notification_deliveries SET status = 'failed', reason = %(reason)s, next_attempt_at = 'infinity' "
    "WHERE id = ANY(%(ids)s) AND status IN ('pending', 'failed')"
)

# The `notifications` Health check's reads (scripts/checks/notifications.py), beside the statements they judge.
STALL_SQL = (
    "SELECT count(*), extract(epoch FROM now() - min(next_attempt_at)) FROM notification_deliveries "
    "WHERE status IN ('pending', 'failed') AND next_attempt_at < now() - make_interval(mins => %(stall_min)s)"
)
RECALL_SQL = (
    "SELECT count(*), extract(epoch FROM now() - min(d.dispatched_at)) "
    "FROM notification_dispatches d "
    "JOIN push_subscriptions ps ON ps.account_id = d.account_id AND ps.ended_at IS NULL "
    "  AND ps.created_at < d.dispatched_at "
    "WHERE d.dispatched_at > now() - interval '24 hours' "
    "  AND d.dispatched_at < now() - make_interval(mins => %(grace_min)s) AND d.seen_at IS NULL "
    f"  AND {VISIBLE_SQL} "
    "  AND NOT EXISTS (SELECT 1 FROM notification_deliveries x "
    "                   WHERE x.notification_id = d.id AND x.push_subscription_id = ps.id)"
)
FAILURES_SQL = (
    "SELECT count(*), coalesce(array_remove(array_agg(DISTINCT reason), NULL), '{}') "
    "FROM notification_deliveries "
    "WHERE status = 'failed' AND next_attempt_at = 'infinity' AND created_at > now() - interval '24 hours'"
)


@dataclass(frozen=True)
class Claimed:
    """One claimed delivery: its device's secrets, the re-check's four answers and the renderer's row."""

    delivery_id: int
    device_id: int
    attempts: int
    dispatch_id: str
    account_id: Any
    seen: bool
    visible: bool
    stale: bool
    ttl_left_s: int
    device_ended: bool
    endpoint: str | None
    p256dh: str | None
    auth: str | None
    origin: str
    text: Mapping[str, Any]


def _claimed(row: tuple[Any, ...]) -> Claimed:
    (delivery_id, device_id, attempts, dispatch_id, account_id, change_kind, seen, visible, stale, ttl_left,
     device_ended, endpoint, p256dh, auth, origin, trigger, prev, label, category, subtype, disposition,
     unit) = row
    text = {"change_kind": change_kind, "trigger_price_czk": trigger, "prev_price_czk": prev,
            "display_label": label, "category_main": category, "subtype": subtype, "disposition": disposition,
            "price_unit": unit}
    return Claimed(delivery_id, device_id, attempts, dispatch_id, account_id, bool(seen), bool(visible),
                   bool(stale), int(ttl_left or 0), bool(device_ended), endpoint, p256dh, auth, origin, text)


def skip_reason(row: Claimed) -> str | None:
    """Why a claimed row is not pushed (c): read in the app first, hidden, past 24 h, or its device ended."""
    if row.seen:
        return "seen"
    if not row.visible:
        return "hidden"
    if row.stale:
        return "stale"
    if row.device_ended:
        return "device_ended"
    return None


def coalesced_title(n: int) -> str:
    return f"{n} nová upozornění" if 2 <= n <= 4 else f"{n} nových upozornění"


def topic(device_id: int, first_delivery_id: int) -> str:
    """22 base64url characters: a crash's re-send of this batch replaces the undelivered first, never another event."""
    return push.b64url(hashlib.sha256(f"{device_id}:{first_delivery_id}".encode()).digest()[:16])


def build_payload(rows: list[Claimed], origin: str, badge: int) -> dict[str, Any]:
    """Declarative Web Push (iOS 18.4+ shows it unaided; sw.js reads the same fields below that). One row: its own
    words and landing; several: their count, the newest three titles, and the feed."""
    if len(rows) == 1:
        text = render(rows[0].text)
        title, body = text.title, text.body
        navigate = origin + LANDING_PATH.format(dispatch_id=rows[0].dispatch_id, delivery_id=rows[0].delivery_id)
    else:
        title = coalesced_title(len(rows))
        body = "\n".join(render(r.text).title for r in rows[:COALESCED_TITLES])
        navigate = origin + FEED_PATH.format(delivery_id=min(r.delivery_id for r in rows))
    notification = {"title": title, "body": body, "navigate": navigate, "lang": "cs", "app_badge": badge}
    return {"web_push": 8030, "notification": notification, "app_badge": badge}


def _fetch(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(conn: Any, sql: str, params: Mapping[str, Any] | None = None) -> int:
    return int(_fetch(conn, sql, params)[0][0] or 0)


def _run(conn: Any, sql: str, params: Mapping[str, Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount or 0


def claim(conn: Any, *, max_devices: int = MAX_DEVICES, max_rows: int = MAX_ROWS) -> list[Claimed]:
    params = {"max_attempts": MAX_ATTEMPTS, "max_devices": max_devices, "max_rows": max_rows}
    return [_claimed(row) for row in _fetch(conn, CLAIM_SQL, params)]


def finalize(conn: Any, device_id: int, ids: list[int], outcome: push.Outcome) -> None:
    """One push's rows by its outcome: sent | gone (the device ends) | throttled (the device waits) | retry | failed."""
    params = {"ids": ids, "device": device_id, "reason": outcome.reason, "max_attempts": MAX_ATTEMPTS,
              "message_id": outcome.provider_message_id, "wait": outcome.retry_after_s or 60}
    if outcome.kind == "gone":
        with conn.transaction():
            _run(conn, GONE_SQL, params)
            _run(conn, END_DEVICE_SQL, params)
        return
    sql = {"sent": SENT_SQL, "throttled": HOLD_SQL, "retry": RETRY_SQL}.get(outcome.kind, FAIL_SQL)
    _run(conn, sql, params)


def _deliver(conn: Any, rows: list[Claimed], send: Callable[..., push.Outcome]) -> push.Outcome:
    first = rows[0]
    badge = _scalar(conn, UNREAD_SQL, {"account_id": first.account_id})
    ids = [r.delivery_id for r in rows]
    outcome = send(first.endpoint, first.origin, first.p256dh, first.auth,
                   build_payload(rows, first.origin, badge), ttl_s=max(60, max(r.ttl_left_s for r in rows)),
                   urgency="high", topic=topic(first.device_id, min(ids)))
    finalize(conn, first.device_id, ids, outcome)
    return outcome


def deliver_once(conn: Any, *, stop: Callable[[], bool],
                 send: Callable[..., push.Outcome] = push.send) -> dict[str, Any]:
    """One pass. Returns the heartbeat's `last`, plus `attempted` and `raised` (step -> class names, never a text)."""
    last: dict[str, Any] = {"live_devices": 0, "fanned_out": 0, "capped": 0, "claimed": 0, "skipped": {},
                            "pushes": 0, "sent": 0, "gone": 0, "throttled": 0, "retry": 0, "failed": 0}
    raised: dict[str, str] = {}
    try:
        last["live_devices"] = _scalar(conn, LIVE_DEVICES_SQL)
        statuses = [s for (s,) in _fetch(conn, FANOUT_SQL)]
        last["fanned_out"], last["capped"] = statuses.count("pending"), statuses.count("skipped")
    except Exception as exc:  # noqa: BLE001 - a step's error is its class name; its text may hold a row
        raised["fan_out"] = type(exc).__name__
    devices: dict[int, list[Claimed]] = {}
    try:
        last["exhausted"] = _run(conn, EXHAUSTED_SQL, {"max_attempts": MAX_ATTEMPTS})
        rows = claim(conn)
        last["claimed"] = len(rows)
        skips: dict[str, list[int]] = {}
        for row in rows:
            reason = skip_reason(row)
            if reason:
                skips.setdefault(reason, []).append(row.delivery_id)
            else:
                devices.setdefault(row.device_id, []).append(row)
        for reason, ids in sorted(skips.items()):
            _run(conn, SKIP_SQL, {"reason": reason, "ids": ids})
        last["skipped"] = {reason: len(ids) for reason, ids in sorted(skips.items())}
    except Exception as exc:  # noqa: BLE001
        raised["claim"] = type(exc).__name__
        devices = {}
    errors: dict[str, int] = {}
    for device_rows in devices.values():
        if stop():
            # The rest come back when their lease runs out.
            last["stopped"] = True
            break
        try:
            outcome = _deliver(conn, device_rows, send)
        except Exception as exc:  # noqa: BLE001 - one device must not end the pass, nor print its endpoint
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            continue
        last["pushes"] += 1
        last[outcome.kind] += 1
    if errors:
        raised["send"] = (f"{sum(errors.values())} of {len(devices)} devices failed: "
                          + ", ".join(sorted(errors)))
    if raised or any(last[k] for k in ("sent", "gone", "throttled", "retry", "failed")):
        LOG.info("PUSH devices=%d pushes=%d sent=%d gone=%d throttled=%d retry=%d failed=%d skipped=%s "
                 "capped=%d raised=%s", len(devices), last["pushes"], last["sent"], last["gone"],
                 last["throttled"], last["retry"], last["failed"], last["skipped"], last["capped"], raised)
    return {**last, "attempted": ["key", "fan_out", "claim", "send"], "raised": raised}

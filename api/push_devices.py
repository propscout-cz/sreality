"""The push device registry (notifications push W4, PROGRAM.md § 3.4): one live row per installed app, and the open stamp.

Every statement runs on the SERVICE ROLE (browser roles hold nothing on either table, migration 654) for the ONE
account `require_account_id` resolved at the route edge; a push belongs to the signed-in user, never to an account
acted for. The endpoint is validated before any SQL, so no CHECK can print it; validation answers fixed words.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from api import push

# Live devices an account keeps; a registration past it ends the least recently seen ('replaced').
DEVICE_CAP = 10
ENDPOINT_MAX_CHARS = 1024
USER_AGENT_MAX_CHARS = 512

_END = "ended_at = now(), endpoint = NULL, p256dh = NULL, auth = NULL"

# Another account's live row for this endpoint ends 'replaced': an app belongs to whoever signed in last.
_END_OTHER_ACCOUNTS_SQL = (
    f"UPDATE push_subscriptions SET {_END}, end_reason = 'replaced' "
    "WHERE endpoint = %(endpoint)s AND ended_at IS NULL AND account_id <> %(account_id)s"
)
# The caller's own live row is refreshed in place (created_at never moves); none is inserted. A row the
# conflict finds under another account (a registration racing this one) answers nothing and is retried.
_UPSERT_SQL = (
    "INSERT INTO push_subscriptions AS ps (account_id, endpoint, p256dh, auth, origin, user_agent) "
    "VALUES (%(account_id)s, %(endpoint)s, %(p256dh)s, %(auth)s, %(origin)s, %(user_agent)s) "
    "ON CONFLICT (endpoint) WHERE ended_at IS NULL DO UPDATE SET p256dh = EXCLUDED.p256dh, "
    "auth = EXCLUDED.auth, origin = EXCLUDED.origin, user_agent = EXCLUDED.user_agent, last_seen_at = now() "
    "WHERE ps.account_id = EXCLUDED.account_id "
    "RETURNING ps.id"
)
_CAP_SQL = (
    f"UPDATE push_subscriptions SET {_END}, end_reason = 'replaced' "
    "WHERE id IN (SELECT id FROM push_subscriptions WHERE account_id = %(account_id)s AND ended_at IS NULL "
    "ORDER BY last_seen_at DESC, id DESC OFFSET %(cap)s)"
)
_END_OWN_SQL = (
    f"UPDATE push_subscriptions SET {_END}, end_reason = %(reason)s "
    "WHERE endpoint = %(endpoint)s AND account_id = %(account_id)s AND ended_at IS NULL"
)
# The caller's own delivery, through its event's account; stamped once.
_OPENED_SQL = (
    "UPDATE notification_deliveries x SET opened_at = now() FROM notification_dispatches d "
    "WHERE x.id = %(delivery)s AND x.opened_at IS NULL AND d.id = x.notification_id "
    "AND d.account_id = %(account_id)s"
)


@dataclass(frozen=True)
class Device:
    endpoint: str
    p256dh: str
    auth: str
    origin: str
    user_agent: str | None


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _key(text: Any, size: int) -> bytes | None:
    if not isinstance(text, str) or len(text) > 200:
        return None
    try:
        raw = push.unb64url(text)
    except ValueError:
        return None
    return raw if len(raw) == size else None


def validate(endpoint: str, p256dh: str, auth: str, user_agent: str | None, origin: str | None,
             allowed_origins: list[str]) -> Device:
    """The registration as stored, or a 422 that repeats none of it: Apple's push service only, a P-256 point
    and a 16-byte secret, and the request's Origin one of CORS_ALLOW_ORIGINS over https."""
    if len(endpoint) > ENDPOINT_MAX_CHARS or push.apple_host(endpoint) is None:
        raise _refuse("endpoint is not an Apple push endpoint")
    point = _key(p256dh, 65)
    try:
        if point is None or point[0] != 4:
            raise ValueError
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    except ValueError:
        raise _refuse("keys.p256dh is not a P-256 public key") from None
    secret = _key(auth, 16)
    if secret is None:
        raise _refuse("keys.auth is not a 16-byte secret")
    if not origin or not origin.startswith("https://") or origin not in allowed_origins:
        raise _refuse("Origin is not an allowed https origin")
    agent = (user_agent or "")[:USER_AGENT_MAX_CHARS] or None
    return Device(endpoint, push.b64url(point), push.b64url(secret), origin, agent)


def register(conn: "psycopg.Connection", account_id: uuid.UUID, device: Device) -> None:
    """Live rows only: the caller's row is refreshed, another account's is ended and the caller's inserted, or
    the caller's is inserted; then the account keeps DEVICE_CAP live rows. One transaction."""
    params = {"account_id": account_id, "endpoint": device.endpoint, "p256dh": device.p256dh,
              "auth": device.auth, "origin": device.origin, "user_agent": device.user_agent,
              "cap": DEVICE_CAP}
    try:
        with conn.transaction(), conn.cursor() as cur:
            for _ in range(2):
                cur.execute(_END_OTHER_ACCOUNTS_SQL, params)
                cur.execute(_UPSERT_SQL, params)
                if cur.fetchone() is not None:
                    break
            cur.execute(_CAP_SQL, params)
    except psycopg.IntegrityError:
        # Validation mirrors every CHECK, so this is drift; its text would carry the failing row.
        raise _refuse("subscription refused") from None


def end(conn: "psycopg.Connection", account_id: uuid.UUID, endpoint: str, reason: str) -> None:
    """End the caller's live row for `endpoint` (`signed_out` | `switched_off`); no row is no error."""
    if len(endpoint) > ENDPOINT_MAX_CHARS or push.apple_host(endpoint) is None:
        return
    with conn.cursor() as cur:
        cur.execute(_END_OWN_SQL, {"account_id": account_id, "endpoint": endpoint, "reason": reason})


def mark_opened(conn: "psycopg.Connection", account_id: uuid.UUID, delivery: int) -> None:
    """Stamp `opened_at` once on the caller's own delivery; another account's or an unknown id changes nothing."""
    with conn.cursor() as cur:
        cur.execute(_OPENED_SQL, {"account_id": account_id, "delivery": delivery})

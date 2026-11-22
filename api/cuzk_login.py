"""An account's own ČÚZK WSDP login: the store. The password is encrypted here, with a key only the
API service holds, before it reaches the database; nothing in this module returns it to a route's
response. Every statement names the account (service-role connection, tenancy shape 3)."""

from __future__ import annotations

import datetime
import os
import uuid
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CUZK_LOGIN_KEY"


class NotConfigured(RuntimeError):
    """The API has no usable encryption key, so no login can be stored or read."""


class Unreadable(RuntimeError):
    """A stored password does not decrypt with the current key (the key changed): the account enters it again."""


def _box() -> Fernet:
    raw = os.environ.get(KEY_ENV, "").strip()
    if not raw:
        raise NotConfigured(KEY_ENV)
    try:
        return Fernet(raw.encode())
    except ValueError:
        raise NotConfigured(KEY_ENV) from None


def require_key() -> None:
    _box()


def _state(row: tuple[Any, ...] | None) -> dict[str, Any]:
    if row is None:
        return {"set": False, "username": None, "checked_at": None}
    return {"set": True, "username": row[0], "checked_at": row[1].isoformat()}


def state(conn: psycopg.Connection, account_id: uuid.UUID) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("select username, checked_at from account_cuzk_logins where account_id = %s", (account_id,))
        return _state(cur.fetchone())


def save(conn: psycopg.Connection, account_id: uuid.UUID, username: str, password: str,
         checked_at: datetime.datetime) -> dict[str, Any]:
    token = _box().encrypt(password.encode("utf-8")).decode("ascii")
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into account_cuzk_logins (account_id, username, password_enc, checked_at)
            values (%s, %s, %s, %s)
            on conflict (account_id) do update set
                   username = excluded.username, password_enc = excluded.password_enc,
                   checked_at = excluded.checked_at, updated_at = now()
            returning username, checked_at
            """,
            (account_id, username, token, checked_at),
        )
        return _state(cur.fetchone())


def remove(conn: psycopg.Connection, account_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("delete from account_cuzk_logins where account_id = %s", (account_id,))


def login_of(conn: psycopg.Connection, account_id: uuid.UUID) -> tuple[str, str] | None:
    """(username, password) for an outbound call made for this account; never for a response."""
    with conn.cursor() as cur:
        cur.execute("select username, password_enc from account_cuzk_logins where account_id = %s", (account_id,))
        row = cur.fetchone()
    if row is None:
        return None
    try:
        return row[0], _box().decrypt(row[1].encode("ascii")).decode("utf-8")
    except InvalidToken:
        raise Unreadable(str(account_id)) from None

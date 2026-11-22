"""An account's own ČÚZK WSDP login: the store. The password is encrypted here, with a key only the
API service holds, before it reaches the database; nothing in this module returns it to a route's
response. Every statement names the account (service-role connection, tenancy shape 3)."""

from __future__ import annotations

import datetime
import hashlib
import os
import uuid
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CUZK_LOGIN_KEY"

# ČÚZK blocks an account for 15 minutes after three refused logins. A username gets two unaccepted checks per
# window from ALL our accounts together, so we never spend the third; an account gets five an hour over any
# usernames, so one cannot walk a list. A check still in flight counts: two requests cannot both pass the count.
USERNAME_WINDOW = datetime.timedelta(minutes=15)
USERNAME_BUDGET = 2
ACCOUNT_WINDOW = datetime.timedelta(hours=1)
ACCOUNT_BUDGET = 5


class NotConfigured(RuntimeError):
    """The API has no usable encryption key, so no login can be stored or read."""


class Throttled(RuntimeError):
    """The budget of login checks is spent; nothing was sent to ČÚZK. `args[0]` is "username" or "account"."""


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


_SPENT_SQL = """
select count(*) filter (where username_sha = %(sha)s and checked_at > now() - %(uw)s::interval) as on_username,
       count(*) filter (where account_id = %(account)s and checked_at > now() - %(aw)s::interval) as on_account
  from account_cuzk_login_checks
 where outcome in ('pending', 'refused', 'no_answer')
   and checked_at > now() - %(aw)s::interval
   and (username_sha = %(sha)s or account_id = %(account)s)
"""


def admit_check(conn: psycopg.Connection, account_id: uuid.UUID, username: str) -> int:
    """Take one login check from the budget, before the outbound call: the ledger row's id, or `Throttled`.

    One transaction under a lock on the username, so concurrent requests are counted one after another."""
    sha = hashlib.sha256(username.encode("utf-8")).hexdigest()
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, 2600))", (sha,))
        cur.execute(_SPENT_SQL, {"sha": sha, "account": account_id, "uw": USERNAME_WINDOW, "aw": ACCOUNT_WINDOW})
        on_username, on_account = cur.fetchone()
        if on_username >= USERNAME_BUDGET:
            raise Throttled("username")
        if on_account >= ACCOUNT_BUDGET:
            raise Throttled("account")
        cur.execute(
            "insert into account_cuzk_login_checks (account_id, username_sha) values (%s, %s) returning id",
            (account_id, sha),
        )
        return int(cur.fetchone()[0])


def settle_check(conn: psycopg.Connection, check_id: int, outcome: str) -> None:
    with conn.cursor() as cur:
        cur.execute("update account_cuzk_login_checks set outcome = %s where id = %s", (outcome, check_id))

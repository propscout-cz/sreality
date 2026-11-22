"""An account's own ČÚZK WSDP login: the store. The password is encrypted here, with a key only the
API service holds, before it reaches the database; nothing in this module returns it to a route's
response. Every statement names the account (service-role connection, tenancy shape 3)."""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken

from api import wsdp_client

KEY_ENV = "CUZK_LOGIN_KEY"

# ČÚZK locks an account for 15 minutes at THREE CONSECUTIVE refused logins; a correct login resets its counter
# (WSDP 3.1 documentation, "Bezpečnost"). It states no time after which the counter forgets, so "consecutive" is
# what we count: at most USERNAME_BUDGET unaccepted checks on one username SINCE ITS LAST ACCEPTED ONE, from all
# our accounts together, so we never send the third. We cannot see a correct login made elsewhere, so the count
# also forgets after USERNAME_WINDOW; that is the one place this is a judgement and not ČÚZK's rule: an attacker
# who knows a username can make ČÚZK's counter reach three across two windows unless its owner logged in between.
# A check in flight, and one ČÚZK did not answer, count as unaccepted: we cannot know that ČÚZK did not count them.
# A cadastre price purchase (`api/cadastre_prices.py`) takes ONE ROW PER CALL it sends to ČÚZK (about twenty a run:
# two to order, a poll every two seconds for half a minute, one fetch): each is admitted through the budgets just
# before it leaves and settled by its own answer, exactly like a save's check, and a row is never changed after
# it is settled. That is enough only because ONE request per account uses the login at a time (the login lease,
# `lease` below), so a run's calls are one after another.
USERNAME_WINDOW = datetime.timedelta(hours=24)
USERNAME_BUDGET = 2
# One account over any usernames, per hour: unaccepted ones, so it cannot walk a list; and all of them whatever
# the outcome, so a valid login cannot be looped against ČÚZK from our address.
ACCOUNT_WINDOW = datetime.timedelta(hours=1)
ACCOUNT_UNACCEPTED_BUDGET = 5
ACCOUNT_TOTAL_BUDGET = 10  # so also at most ten purchase requests an hour, resumes included
LEDGER_KEEPS = datetime.timedelta(days=2)
# A check still in flight this long after it was admitted is a dead request's (no call lasts a third of it): from
# then on an accepted check resets it like one that got no answer. Without this a killed request's row could never
# be reset, and one more timeout would throttle the username for a day.
STRANDED_AFTER = datetime.timedelta(seconds=120)
_LOCK_ACCOUNT, _LOCK_USERNAME = 2601, 2600  # advisory-lock namespaces, always taken in this order


class NotConfigured(RuntimeError):
    """The API has no usable encryption key, so no login can be stored or read."""


class Throttled(RuntimeError):
    """A budget of login checks is spent; nothing was sent to ČÚZK."""

    def __init__(self, by: str, retry_after_s: int) -> None:
        super().__init__(by)
        self.by, self.retry_after_s = by, retry_after_s


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
        return {"set": False, "username": None, "checked_at": None, "refused": False}
    return {"set": True, "username": row[0], "checked_at": row[1].isoformat(), "refused": row[2] is not None}


def state(conn: psycopg.Connection, account_id: uuid.UUID) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("select username, checked_at, refused_at from account_cuzk_logins where account_id = %s",
                    (account_id,))
        return _state(cur.fetchone())


class LeaseHeld(RuntimeError):
    """Another request of this account is using the stored login right now."""


# The login lease: ONE request per account talks to ČÚZK on the stored login at a time, a purchase run or a save
# or removal of the login itself. Its holder renews it before every call; it may be taken from a holder only when
# not renewed for LEASE_S, which no single call can outlast (`wsdp_client.CALL_MAX_S`, a wall-clock bound).
LEASE_S = 120
assert LEASE_S > 2 * wsdp_client.CALL_MAX_S, "a live holder's lease must outlast its longest call, with room"


@dataclass(frozen=True)
class Leased:
    username: str
    password: str
    stamp: datetime.datetime  # the stored login's `updated_at`: which login this run read
    refused: bool


def take_lease(conn: psycopg.Connection, account_id: uuid.UUID, token: uuid.UUID) -> tuple[Any, ...] | None:
    """Take the account's login lease, in one statement: the stored row (username, password_enc, updated_at,
    refused_at), or None when no login is stored. `LeaseHeld` when another request holds it and renewed it within
    LEASE_S. The taker must `release` it."""
    with conn.cursor() as cur:
        cur.execute(
            """
            update account_cuzk_logins set run_token = %s, run_beat_at = now()
             where account_id = %s
               and (run_token is null or run_beat_at < now() - make_interval(secs => %s))
            returning username, password_enc, updated_at, refused_at
            """,
            (token, account_id, LEASE_S),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("select 1 from account_cuzk_logins where account_id = %s", (account_id,))
            if cur.fetchone() is not None:
                raise LeaseHeld(str(account_id))
    return row


def lease(conn: psycopg.Connection, account_id: uuid.UUID, token: uuid.UUID) -> Leased | None:
    """`take_lease`, with the login it guards decrypted. A login that cannot be read gives the lease back."""
    row = take_lease(conn, account_id, token)
    if row is None:
        return None
    try:
        password = _box().decrypt(row[1].encode("ascii")).decode("utf-8")
    except BaseException as exc:
        release(conn, account_id, token)
        if isinstance(exc, InvalidToken):
            raise Unreadable(str(account_id)) from None
        raise
    return Leased(row[0], password, row[2], row[3] is not None)


def renew(conn: psycopg.Connection, account_id: uuid.UUID, token: uuid.UUID, held: Leased) -> bool:
    """Renew the lease before a call to ČÚZK. False: do not send. The lease is no longer this run's, or the stored
    login was refused, re-saved or removed since this run read it."""
    with conn.cursor() as cur:
        cur.execute(
            "update account_cuzk_logins set run_beat_at = now() where account_id = %s and run_token = %s "
            "returning refused_at is null and updated_at = %s",
            (account_id, token, held.stamp),
        )
        row = cur.fetchone()
    return row is not None and bool(row[0])


def release(conn: psycopg.Connection, account_id: uuid.UUID, token: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("update account_cuzk_logins set run_token = null, run_beat_at = null "
                    "where account_id = %s and run_token = %s", (account_id, token))


def run_live(conn: psycopg.Connection, account_id: uuid.UUID) -> bool:
    """Whether a request of this account holds the lease right now (a read for the card; it decides nothing)."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from account_cuzk_logins where account_id = %s and run_token is not null "
            "and run_beat_at >= now() - make_interval(secs => %s)",
            (account_id, LEASE_S),
        )
        return cur.fetchone() is not None


def mark_refused(conn: psycopg.Connection, account_id: uuid.UUID, held: Leased) -> None:
    """ČÚZK refused the login this run read: nothing more is sent on it until it is saved again. A login saved
    since (another `updated_at`) is not the one that was refused, and is left alone."""
    with conn.cursor() as cur:
        cur.execute(
            "update account_cuzk_logins set refused_at = now() "
            "where account_id = %s and updated_at = %s and refused_at is null",
            (account_id, held.stamp),
        )


def withdraw_check(conn: psycopg.Connection, check_id: int) -> None:
    """A check that was admitted and then not sent after all: it never happened, so it leaves the ledger."""
    with conn.cursor() as cur:
        cur.execute("delete from account_cuzk_login_checks where id = %s and outcome = 'pending'", (check_id,))


def seal(data: bytes) -> bytes:
    """Encrypt a document that names the account's ČÚZK login, with the key the passwords use."""
    return _box().encrypt(data)


def unseal(token: bytes) -> bytes:
    try:
        return _box().decrypt(bytes(token))
    except InvalidToken:
        raise Unreadable("sealed document") from None


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
                   checked_at = excluded.checked_at, updated_at = now(), refused_at = null
            returning username, checked_at, refused_at
            """,
            (account_id, username, token, checked_at),
        )
        return _state(cur.fetchone())


def remove(conn: psycopg.Connection, account_id: uuid.UUID) -> None:
    """Forget the stored login. `LeaseHeld` while a request of the account is using it: the lease lives on this
    row, so removing it under a live run would let a second run start beside the first."""
    with conn.cursor() as cur:
        cur.execute(
            "delete from account_cuzk_logins where account_id = %s "
            "and (run_token is null or run_beat_at < now() - make_interval(secs => %s))",
            (account_id, LEASE_S),
        )
        if cur.rowcount == 0:
            cur.execute("select 1 from account_cuzk_logins where account_id = %s", (account_id,))
            if cur.fetchone() is not None:
                raise LeaseHeld(str(account_id))


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


def _keyed(purpose: bytes, text: str) -> str:
    raw = os.environ.get(KEY_ENV, "").strip()
    if not raw:
        raise NotConfigured(KEY_ENV)
    secret = hashlib.sha256(purpose + raw.encode()).digest()
    return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()


def budget_key(username: str) -> str:
    """What the budget COUNTS by: a keyed hash of the username's folded form (NFKC, casefold, format characters
    dropped), so 'firma01', 'FIRMA01' and a zero-width variant are ONE budget, and the stored value cannot be
    reversed without the server's key. The typed username itself is what is sent to ČÚZK, unchanged."""
    folded = "".join(ch for ch in unicodedata.normalize("NFKC", username).casefold()
                     if unicodedata.category(ch) != "Cf")
    return _keyed(b"cuzk-login-check:", folded)


def exact_key(username: str) -> str:
    """What an accepted check RESETS by: a keyed hash of the username exactly as typed. Counting folds, resetting
    does not: whether ČÚZK holds 'novak' and 'Novak' as one account is unverified, and this is safe either way."""
    return _keyed(b"cuzk-login-check-exact:", username)


# An unaccepted check r stays in its username's run unless an accepted check L on the SAME exact name was
# ADMITTED after r had SETTLED: only then do we know ČÚZK handled r before L's correct login reset its counter.
# A check still in flight is never reset. (Ids are the order we admitted in, not the order ČÚZK handled.)
_SPENT_SQL = """
with username_run as (
  select coalesce(r.answered_at, r.admitted_at) as at
    from account_cuzk_login_checks r
   where r.username_key = %(key)s and r.outcome <> 'accepted'
     and coalesce(r.answered_at, r.admitted_at) > now() - %(uw)s::interval
     and not exists (select 1 from account_cuzk_login_checks l
                      where l.username_exact_key = r.username_exact_key and l.outcome = 'accepted'
                        and coalesce(r.answered_at, r.admitted_at + %(tail)s::interval) < l.admitted_at)
), account_hour as (
  select admitted_at as at, outcome, in_run from account_cuzk_login_checks
   where account_id = %(account)s and admitted_at > now() - %(aw)s::interval
)
select (select count(*) from username_run),
       (select extract(epoch from min(at) + %(uw)s::interval - now()) from username_run),
       (select count(*) from account_hour where outcome <> 'accepted'),
       (select extract(epoch from min(at) + %(aw)s::interval - now()) from account_hour where outcome <> 'accepted'),
       (select count(*) from account_hour where not in_run),
       (select extract(epoch from min(at) + %(aw)s::interval - now()) from account_hour where not in_run)
"""


def _wait(seconds: float | None, *, to_the_hour: bool = False) -> int:
    wait = max(60, int(seconds or 0) + 1)
    return -(-wait // 3600) * 3600 if to_the_hour else wait


def admit_check(conn: psycopg.Connection, account_id: uuid.UUID, username: str, *, in_run: bool = False) -> int:
    """Take one login check from the budgets, before the outbound call: the ledger row's id, or `Throttled`.
    `in_run`: a further call of a purchase run whose first call was already admitted; it counts for the username
    and as unaccepted like any other, but not toward the account's hourly total, which caps runs, not their calls.

    One transaction under two transaction-scoped advisory locks, the account's and then the username's, always in
    that order: concurrent requests of one account, or on one username, are counted one after another, and the
    row is committed before anything is sent."""
    key, exact = budget_key(username), exact_key(username)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, %s))", (str(account_id), _LOCK_ACCOUNT))
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, %s))", (key, _LOCK_USERNAME))
        cur.execute("delete from account_cuzk_login_checks where admitted_at < now() - %s::interval", (LEDGER_KEEPS,))
        cur.execute(_SPENT_SQL, {"key": key, "account": account_id, "uw": USERNAME_WINDOW, "aw": ACCOUNT_WINDOW,
                                 "tail": STRANDED_AFTER})
        on_username, username_wait, unaccepted, unaccepted_wait, total, total_wait = cur.fetchone()
        if on_username >= USERNAME_BUDGET:
            # Rounded up to the hour: the exact second would tell a caller when someone else tried this username.
            raise Throttled("username", _wait(username_wait, to_the_hour=True))
        if unaccepted >= ACCOUNT_UNACCEPTED_BUDGET:
            raise Throttled("account", _wait(unaccepted_wait))
        if total >= ACCOUNT_TOTAL_BUDGET:
            raise Throttled("account", _wait(total_wait))
        cur.execute(
            "insert into account_cuzk_login_checks (account_id, username_key, username_exact_key, in_run) "
            "values (%s, %s, %s, %s) returning id",
            (account_id, key, exact, in_run),
        )
        return int(cur.fetchone()[0])


def settle_check(conn: psycopg.Connection, check_id: int, outcome: str) -> None:
    """The check's outcome and when ČÚZK answered; the time we admitted it stays."""
    with conn.cursor() as cur:
        cur.execute("update account_cuzk_login_checks set outcome = %s, answered_at = now() where id = %s",
                    (outcome, check_id))

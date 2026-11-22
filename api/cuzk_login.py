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
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken

KEY_ENV = "CUZK_LOGIN_KEY"

# ČÚZK locks an account for 15 minutes at THREE CONSECUTIVE refused logins; a correct login resets its counter
# (WSDP 3.1 documentation, "Bezpečnost"). It states no time after which the counter forgets, so "consecutive" is
# what we count: at most USERNAME_BUDGET unaccepted checks on one username SINCE ITS LAST ACCEPTED ONE, from all
# our accounts together, so we never send the third. We cannot see a correct login made elsewhere, so the count
# also forgets after USERNAME_WINDOW; that is the one place this is a judgement and not ČÚZK's rule: an attacker
# who knows a username can make ČÚZK's counter reach three across two windows unless its owner logged in between.
# A check in flight, and one ČÚZK did not answer, count as unaccepted: we cannot know that ČÚZK did not count them.
USERNAME_WINDOW = datetime.timedelta(hours=24)
USERNAME_BUDGET = 2
# One account over any usernames, per hour: unaccepted ones, so it cannot walk a list; and all of them whatever
# the outcome, so a valid login cannot be looped against ČÚZK from our address.
ACCOUNT_WINDOW = datetime.timedelta(hours=1)
ACCOUNT_UNACCEPTED_BUDGET = 5
ACCOUNT_TOTAL_BUDGET = 10
LEDGER_KEEPS = datetime.timedelta(days=2)
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
                        and r.answered_at < l.admitted_at)
), account_hour as (
  select admitted_at as at, outcome from account_cuzk_login_checks
   where account_id = %(account)s and admitted_at > now() - %(aw)s::interval
)
select (select count(*) from username_run),
       (select extract(epoch from min(at) + %(uw)s::interval - now()) from username_run),
       (select count(*) from account_hour where outcome <> 'accepted'),
       (select extract(epoch from min(at) + %(aw)s::interval - now()) from account_hour where outcome <> 'accepted'),
       (select count(*) from account_hour),
       (select extract(epoch from min(at) + %(aw)s::interval - now()) from account_hour)
"""


def _wait(seconds: float | None, *, to_the_hour: bool = False) -> int:
    wait = max(60, int(seconds or 0) + 1)
    return -(-wait // 3600) * 3600 if to_the_hour else wait


def admit_check(conn: psycopg.Connection, account_id: uuid.UUID, username: str) -> int:
    """Take one login check from the budgets, before the outbound call: the ledger row's id, or `Throttled`.

    One transaction under two transaction-scoped advisory locks, the account's and then the username's, always in
    that order: concurrent requests of one account, or on one username, are counted one after another, and the
    row is committed before anything is sent."""
    key, exact = budget_key(username), exact_key(username)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, %s))", (str(account_id), _LOCK_ACCOUNT))
        cur.execute("select pg_advisory_xact_lock(hashtextextended(%s, %s))", (key, _LOCK_USERNAME))
        cur.execute("delete from account_cuzk_login_checks where admitted_at < now() - %s::interval", (LEDGER_KEEPS,))
        cur.execute(_SPENT_SQL, {"key": key, "account": account_id, "uw": USERNAME_WINDOW, "aw": ACCOUNT_WINDOW})
        on_username, username_wait, unaccepted, unaccepted_wait, total, total_wait = cur.fetchone()
        if on_username >= USERNAME_BUDGET:
            # Rounded up to the hour: the exact second would tell a caller when someone else tried this username.
            raise Throttled("username", _wait(username_wait, to_the_hour=True))
        if unaccepted >= ACCOUNT_UNACCEPTED_BUDGET:
            raise Throttled("account", _wait(unaccepted_wait))
        if total >= ACCOUNT_TOTAL_BUDGET:
            raise Throttled("account", _wait(total_wait))
        cur.execute(
            "insert into account_cuzk_login_checks (account_id, username_key, username_exact_key) "
            "values (%s, %s, %s) returning id",
            (account_id, key, exact),
        )
        return int(cur.fetchone()[0])


def settle_check(conn: psycopg.Connection, check_id: int, outcome: str) -> None:
    """The check's outcome and when ČÚZK answered; the time we admitted it stays."""
    with conn.cursor() as cur:
        cur.execute("update account_cuzk_login_checks set outcome = %s, answered_at = now() where id = %s",
                    (outcome, check_id))

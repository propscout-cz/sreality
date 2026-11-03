"""Migration 722 and api/notification_email.py's statements on the real schema (docs/design/email/PROGRAM.md PR 4): the
missing settings rows and the default, the off link, the tables' CHECKs and their darkness to browser roles; the daily
claim (one per Prague day, a failure claimed again after 15 minutes up to three times, nothing to say recorded empty
so the day is spent), the instant claim (only with an event, never within 15 minutes of any claim), what an email
covers (VISIBLE and unseen, after the last claim that did not fail, never more than a day back) and one delivery end
to end. Runs in CI's migrations job (`TEST_DATABASE_URL`, DB_RAILS_REQUIRED=1); each test rolls back. Inside one
transaction now() stands still, so passing time is written into the rows instead."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest

from api import notification_email as ne
from tests._live_property import REQUIRED_DB, _account, _advert, _property, db_url

pytestmark = REQUIRED_DB

# The claims' Prague day is the candidates' (today in Prague), so the two agree in any run.
DAY = ne.prague_now().date()


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    """One connection in one outer transaction (claim's own transaction is a savepoint inside it); rolled back."""
    c = psycopg.connect(db_url(), options="-c statement_timeout=20000 -c lock_timeout=5000")
    try:
        c.execute("SELECT 1")
        yield c
    finally:
        c.rollback()
        c.close()


def _member(conn: Any, account: uuid.UUID, email: str, *, joined: str = "1 day") -> None:
    user = uuid.uuid4()
    conn.execute("ALTER TABLE auth.users DISABLE TRIGGER USER")
    conn.execute("INSERT INTO auth.users (id, email, email_confirmed_at) VALUES (%s, %s, now())", (user, email))
    conn.execute("ALTER TABLE auth.users ENABLE TRIGGER USER")
    conn.execute("INSERT INTO account_members (account_id, user_id, role, created_at) "
                 "VALUES (%s, %s, 'owner', now() - %s::interval)", (account, user, joined))


def _signed_up(conn: Any, mode: str | None = "daily") -> tuple[uuid.UUID, str]:
    """An account with one member and, unless `mode` is None, its settings row."""
    with conn.cursor() as cur:
        acc = _account(cur)
    email = f"ne-{uuid.uuid4().hex[:10]}@example.cz"
    _member(conn, acc, email)
    if mode is not None:
        ne.set_mode(conn, acc, mode)
    return acc, email


def _watchdog_new(conn: Any, account: uuid.UUID, *, ago: str = "10 minutes") -> tuple[uuid.UUID, int, uuid.UUID]:
    """A watchdog's 'new' on a fresh property: (watchdog, property, dispatch)."""
    sub = conn.execute("INSERT INTO notification_subscriptions (account_id, name, filter_spec) "
                       "VALUES (%s, 'w', '{}'::jsonb) RETURNING id", (account,)).fetchone()[0]
    with conn.cursor() as cur:
        pid = _property(cur)
        lid = _advert(cur, pid, source="idnes")
    dispatch = conn.execute(
        "INSERT INTO notification_dispatches (subscription_id, source_kind, property_id, listing_id, change_kind, "
        "  trigger_price_czk, dispatched_at) "
        "VALUES (%s, 'watchdog', %s, %s, 'new', 5150000, now() - %s::interval) RETURNING id",
        (sub, pid, lid, ago)).fetchone()[0]
    return sub, pid, dispatch


def _share(conn: Any, recipient: uuid.UUID, *, ago: str = "10 minutes", seen: bool = False) -> uuid.UUID:
    with conn.cursor() as cur:
        actor = _account(cur)
    return conn.execute(
        "INSERT INTO notification_dispatches (source_kind, change_kind, account_id, actor_account_id, event_ref, "
        "  dispatched_at, seen_at) "
        "VALUES ('share', 'shared_collection', %s, %s, %s, now() - %s::interval, CASE WHEN %s THEN now() END) "
        "RETURNING id", (recipient, actor, uuid.uuid4().hex[:12], ago, seen)).fetchone()[0]


def _sends(conn: Any, account: uuid.UUID) -> list[tuple[Any, ...]]:
    return conn.execute("SELECT mode, local_day, status, attempts, event_count, provider_message_id, reason "
                        "FROM notification_email_sends WHERE account_id = %s ORDER BY id", (account,)).fetchall()


def _age(conn: Any, account: uuid.UUID, by: str = "16 minutes") -> None:
    """Every claim of the account happened `by` earlier."""
    conn.execute("UPDATE notification_email_sends SET claimed_at = claimed_at - %s::interval, "
                 "covered_until = covered_until - %s::interval WHERE account_id = %s", (by, by, account))


def _candidates(conn: Any, *, open_: bool = True) -> dict[uuid.UUID, tuple[str, str]]:
    local = ne.prague_now().replace(hour=8 if open_ else 6)
    return {c.account_id: (c.mode, c.email) for c in ne.candidates(conn, local, limit=100_000)}


# --- settings ---------------------------------------------------------------------------------------------------


def test_every_account_with_a_member_gets_a_daily_row_once(conn: Any) -> None:
    acc, _ = _signed_up(conn, mode=None)
    with conn.cursor() as cur:
        bare = _account(cur)
    assert ne.read_mode(conn, acc) == "daily", "no row reads as the default"
    conn.execute(ne.ENSURE_SETTINGS_SQL)
    rows = conn.execute("SELECT account_id, mode, unsubscribe_token IS NOT NULL FROM account_notification_email "
                        "WHERE account_id = ANY(%s)", ([acc, bare],)).fetchall()
    assert rows == [(acc, "daily", True)], "an account nobody belongs to gets none"
    assert conn.execute(ne.ENSURE_SETTINGS_SQL).rowcount == 0


def test_a_mode_is_set_in_place_and_the_off_link_turns_it_off_once(conn: Any) -> None:
    acc, _ = _signed_up(conn, mode="instant")
    assert ne.read_mode(conn, acc) == "instant"
    token = conn.execute("SELECT unsubscribe_token FROM account_notification_email WHERE account_id = %s",
                         (acc,)).fetchone()[0]
    assert ne.set_mode(conn, acc, "daily") == "daily"
    assert conn.execute("SELECT unsubscribe_token FROM account_notification_email WHERE account_id = %s",
                        (acc,)).fetchone()[0] == token, "the token outlives a change of mode"
    assert ne.unsubscribe(conn, token) is True
    assert ne.read_mode(conn, acc) == "off"
    assert ne.unsubscribe(conn, token) is False and ne.unsubscribe(conn, uuid.uuid4()) is False


@pytest.mark.parametrize(("sql", "params"), [
    ("INSERT INTO account_notification_email (account_id, mode) VALUES (%s, 'weekly')", ()),
    ("INSERT INTO notification_email_sends (account_id, mode, local_day, covered_until, status) "
     "VALUES (%s, 'daily', NULL, now(), 'sending')", ()),
    ("INSERT INTO notification_email_sends (account_id, mode, local_day, covered_until, status) "
     "VALUES (%s, 'instant', current_date, now(), 'sending')", ()),
    ("INSERT INTO notification_email_sends (account_id, mode, local_day, covered_until, status) "
     "VALUES (%s, 'daily', current_date, now(), 'queued')", ()),
    ("INSERT INTO notification_email_sends (account_id, mode, covered_until, status) "
     "VALUES (%s, 'off', now(), 'empty')", ()),
])
def test_the_tables_refuse_what_the_code_never_writes(conn: Any, sql: str, params: tuple[Any, ...]) -> None:
    acc, _ = _signed_up(conn, mode=None)
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(sql, (acc, *params))


@pytest.mark.parametrize("table", ["account_notification_email", "notification_email_sends"])
@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_browser_role_reaches_either_table(conn: Any, table: str, role: str) -> None:
    assert conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT, INSERT, UPDATE, DELETE')",
                        (role, f"public.{table}")).fetchone() == (False,)
    assert conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
                        (f"public.{table}",)).fetchone() == (True,)


# --- the daily claim --------------------------------------------------------------------------------------------


def test_a_day_is_claimed_once_and_a_failure_again_after_a_wait_up_to_three_times(conn: Any) -> None:
    acc, email = _signed_up(conn)
    assert _candidates(conn)[acc] == ("daily", email)
    assert acc not in _candidates(conn, open_=False), "the day opens at 07:00 Prague"
    first = ne.claim(conn, acc, "daily", DAY)
    assert first is not None and first.attempts == 1 and first.mode == "daily"
    assert ne.claim(conn, acc, "daily", DAY) is None, "one row per Prague day"
    assert acc not in _candidates(conn)
    for attempt in (1, 2, 3):
        conn.execute("UPDATE notification_email_sends SET status = 'failed', reason = 'status_503' "
                     "WHERE account_id = %s", (acc,))
        if attempt == 3:
            break
        assert ne.claim(conn, acc, "daily", DAY) is None, "a failure waits 15 minutes"
        _age(conn, acc)
        assert acc in _candidates(conn)
        again = ne.claim(conn, acc, "daily", DAY)
        assert again is not None and again.id == first.id and again.attempts == attempt + 1
    _age(conn, acc)
    assert ne.claim(conn, acc, "daily", DAY) is None and acc not in _candidates(conn), "three attempts, then none"
    assert _sends(conn, acc) == [("daily", DAY, "failed", 3, 0, None, "status_503")]
    assert ne.claim(conn, acc, "daily", DAY + timedelta(days=1)) is not None, "the next day is its own"


def test_a_day_with_nothing_to_say_is_spent_empty(conn: Any) -> None:
    acc, email = _signed_up(conn)
    out = ne.deliver(conn, ne.Candidate(acc, "daily", email), DAY, send=lambda **m: pytest.fail("nothing to say"))
    assert out == ("empty", None)
    assert _sends(conn, acc) == [("daily", DAY, "empty", 1, 0, None, None)]
    _watchdog_new(conn, acc, ago="1 minute")
    assert acc not in _candidates(conn), "a later event that day sends no second daily"
    assert ne.claim(conn, acc, "daily", DAY) is None


def test_a_switched_mode_or_off_is_never_claimed(conn: Any) -> None:
    acc, _ = _signed_up(conn, mode="off")
    _watchdog_new(conn, acc)
    assert acc not in _candidates(conn)
    assert ne.claim(conn, acc, "daily", DAY) is None and ne.claim(conn, acc, "instant", DAY) is None
    ne.set_mode(conn, acc, "instant")
    assert ne.claim(conn, acc, "daily", DAY) is None, "the mode moved since the select"
    assert _sends(conn, acc) == []


# --- the instant claim ------------------------------------------------------------------------------------------


def test_an_instant_claim_needs_an_event_and_waits_fifteen_minutes_after_any_claim(conn: Any) -> None:
    acc, email = _signed_up(conn, mode="instant")
    assert acc not in _candidates(conn) and ne.claim(conn, acc, "instant", DAY) is None, "nothing to tell"
    _watchdog_new(conn, acc)
    assert _candidates(conn)[acc] == ("instant", email)
    first = ne.claim(conn, acc, "instant", DAY)
    assert first is not None and first.mode == "instant"
    conn.execute("UPDATE notification_email_sends SET status = 'failed' WHERE id = %s", (first.id,))
    assert ne.claim(conn, acc, "instant", DAY) is None and acc not in _candidates(conn), "any status holds 15 min"
    _age(conn, acc)
    again = ne.claim(conn, acc, "instant", DAY)
    assert again is not None and again.id != first.id, "a failed instant is a new claim, covering its events again"
    assert [r[0:3] for r in _sends(conn, acc)] == [("instant", None, "failed"), ("instant", None, "sending")]


# --- what an email covers ---------------------------------------------------------------------------------------


def test_an_email_covers_visible_unseen_events_since_the_last_claim_that_did_not_fail(conn: Any) -> None:
    acc, email = _signed_up(conn, mode="instant")
    other, _ = _signed_up(conn, mode="instant")
    _watchdog_new(conn, acc, ago="40 minutes")                       # told by the earlier email
    _, _, fresh = _watchdog_new(conn, acc, ago="10 minutes")
    shared = _share(conn, acc, ago="5 minutes")
    _share(conn, acc, ago="6 minutes", seen=True)                     # read in the app first
    _share(conn, acc, ago="25 hours")                                 # past the day
    _share(conn, other, ago="5 minutes")                              # another account's
    retired, _, _ = _watchdog_new(conn, acc, ago="8 minutes")
    conn.execute("UPDATE notification_subscriptions SET retired_at = now() WHERE id = %s", (retired,))
    _, dismissed_pid, _ = _watchdog_new(conn, acc, ago="7 minutes")
    conn.execute("INSERT INTO property_dismissals (account_id, property_id) VALUES (%s, %s)", (acc, dismissed_pid))
    conn.execute("INSERT INTO notification_email_sends (account_id, mode, claimed_at, covered_until, status, "
                 "event_count, sent_at) VALUES (%s, 'instant', now() - interval '30 minutes', "
                 "now() - interval '30 minutes', 'sent', 1, now() - interval '30 minutes')", (acc,))
    conn.execute("INSERT INTO notification_email_sends (account_id, mode, claimed_at, covered_until, status) "
                 "VALUES (%s, 'instant', now() - interval '20 minutes', now() - interval '20 minutes', 'failed')",
                 (acc,))
    sent: list[dict[str, Any]] = []
    out = ne.deliver(conn, ne.Candidate(acc, "instant", email), DAY,
                     send=lambda **mail: sent.append(mail) or "re_live")
    assert out == ("sent", None)
    [mail] = sent
    assert mail["to"] == email and mail["subject"] == "1 nový inzerát a 1 další změna pro vaše hledání"
    assert f"/n/{fresh}" in mail["text"] and f"/n/{shared}" in mail["text"]
    assert mail["text"].count("https://app.propscout.cz/n/") == 2
    assert mail["text"].index(f"/n/{shared}") < mail["text"].index(f"/n/{fresh}"), "newest first"
    assert "Sdíleno s vámi: Sbírka" in mail["text"]
    assert _sends(conn, acc)[-1] == ("instant", None, "sent", 1, 2, "re_live", None)


def test_a_first_email_reaches_back_one_day_at_most(conn: Any) -> None:
    acc, email = _signed_up(conn)
    _share(conn, acc, ago="23 hours")
    _share(conn, acc, ago="25 hours")
    sent: list[dict[str, Any]] = []
    assert ne.deliver(conn, ne.Candidate(acc, "daily", email), DAY,
                      send=lambda **mail: sent.append(mail) or "re_1") == ("sent", None)
    assert sent[0]["subject"] == "1 další změna pro vaše hledání"
    assert _sends(conn, acc) == [("daily", DAY, "sent", 1, 1, "re_1", None)]


def test_an_account_without_a_member_is_never_a_candidate(conn: Any) -> None:
    with conn.cursor() as cur:
        bare = _account(cur)
    _share(conn, bare)
    for mode in ("instant", "daily"):
        ne.set_mode(conn, bare, mode)
        assert bare not in _candidates(conn), mode

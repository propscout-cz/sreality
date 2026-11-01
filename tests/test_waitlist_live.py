"""Migration 700 and api/waitlist.py's two statements on the real schema: a new email gets a token and a second signup
of it none, the table refuses what the API would never send (mixed case, a junk address, an unknown investor type),
an answer is accepted once, and no browser role can read or write the table. Runs in CI's migrations job
(`TEST_DATABASE_URL`, DB_RAILS_REQUIRED=1); each test rolls back."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from api import waitlist
from tests._live_property import REQUIRED_DB, db_url

pytestmark = REQUIRED_DB


class _Tx:
    """One connection inside one outer transaction; record_* open savepoints inside it; rolled back after."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn

    def transaction(self) -> Any:
        return self.conn.transaction()

    def cursor(self) -> Any:
        return self.conn.cursor()


@pytest.fixture()
def tx() -> Iterator[_Tx]:
    conn = psycopg.connect(db_url(), options="-c statement_timeout=20000")
    try:
        yield _Tx(conn)
    finally:
        conn.rollback()
        conn.close()


def test_a_new_email_gets_a_token_and_a_repeat_gets_none(tx: _Tx) -> None:
    first = waitlist.record_signup(tx, "a@example.cz", waitlist.CONSENT_VERSION, "flip", {"utm_source": "facebook"})
    assert isinstance(first, uuid.UUID)
    assert waitlist.record_signup(tx, "a@example.cz", waitlist.CONSENT_VERSION, None, {}) is None
    with tx.cursor() as cur:
        cur.execute("SELECT consent_version, page_variant, utm, answered_at FROM waitlist_signups "
                    "WHERE email = 'a@example.cz'")
        assert cur.fetchall() == [(waitlist.CONSENT_VERSION, "flip", {"utm_source": "facebook"}, None)]


@pytest.mark.parametrize(("email", "extra"), [
    ("Mixed@Example.cz", {}),
    ("no-at-sign.cz", {}),
    ("a@b", {}),
    ("ok@example.cz", {"investor_type": "landlord"}),
    ("ok@example.cz", {"utm": Jsonb(["not", "an", "object"])}),
])
def test_the_table_refuses_what_the_api_never_sends(tx: _Tx, email: str, extra: dict[str, Any]) -> None:
    cols = ["email", "consent_version", *extra]
    with pytest.raises(psycopg.errors.CheckViolation), tx.transaction(), tx.cursor() as cur:
        cur.execute(f"INSERT INTO waitlist_signups ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    (email, waitlist.CONSENT_VERSION, *extra.values()))


def test_an_answer_is_accepted_once(tx: _Tx) -> None:
    token = waitlist.record_signup(tx, "b@example.cz", waitlist.CONSENT_VERSION, None, {})
    assert token is not None
    assert waitlist.record_answer(tx, token, "rent", "Brno, do 4 M") is True
    assert waitlist.record_answer(tx, token, "flip", "again") is False
    assert waitlist.record_answer(tx, uuid.uuid4(), "flip", None) is False
    with tx.cursor() as cur:
        cur.execute("SELECT investor_type, note, answered_at IS NOT NULL FROM waitlist_signups WHERE followup_token = %s",
                    (token,))
        assert cur.fetchone() == ("rent", "Brno, do 4 M", True)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_browser_role_reaches_the_table(tx: _Tx, role: str) -> None:
    with tx.cursor() as cur:
        cur.execute("SELECT has_table_privilege(%s, 'public.waitlist_signups', 'SELECT, INSERT, UPDATE, DELETE')",
                    (role,))
        assert cur.fetchone() == (False,)
        cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'public.waitlist_signups'::regclass")
        assert cur.fetchone() == (True,)


def test_the_admin_list_reads_every_column_newest_first(tx: _Tx) -> None:
    waitlist.record_signup(tx, "first@example.cz", waitlist.CONSENT_VERSION, None, {})
    token = waitlist.record_signup(tx, "second@example.cz", waitlist.CONSENT_VERSION, "najem", {"utm_source": "fb"})
    assert token is not None
    waitlist.record_answer(tx, token, "both", "Plzeň")
    rows = [r for r in waitlist.list_signups(tx) if r["email"].endswith("@example.cz")]
    assert [r["email"] for r in rows][:2] == ["second@example.cz", "first@example.cz"]
    assert rows[0]["utm"] == {"utm_source": "fb"} and rows[0]["investor_type"] == "both" and rows[0]["note"] == "Plzeň"
    assert set(rows[0]) == {*waitlist.SIGNUP_COLUMNS, "has_account"}


def _entry(tx: _Tx, email: str) -> uuid.UUID:
    token = waitlist.record_signup(tx, email, waitlist.CONSENT_VERSION, None, {})
    assert token is not None
    return token


def test_a_confirmation_is_claimed_once_and_a_release_gives_it_back(tx: _Tx) -> None:
    token = _entry(tx, "claim@example.cz")
    assert waitlist.claim_confirmation(tx, token) == "claim@example.cz"
    assert waitlist.claim_confirmation(tx, token) is None
    waitlist.release_confirmation(tx, token)
    assert waitlist.claim_confirmation(tx, token) == "claim@example.cz"


def test_the_cap_counts_todays_sends_only(tx: _Tx) -> None:
    with tx.cursor() as cur:
        cur.execute("UPDATE waitlist_signups SET confirmation_sent_at = NULL")
    first, second, third = (_entry(tx, f"cap{n}@example.cz") for n in range(3))
    assert waitlist.claim_confirmation(tx, first, cap=1) == "cap0@example.cz"
    assert waitlist.claim_confirmation(tx, second, cap=1) is None
    with tx.cursor() as cur:
        cur.execute("UPDATE waitlist_signups SET confirmation_sent_at = now() - interval '2 days' WHERE followup_token = %s",
                    (first,))
    assert waitlist.claim_confirmation(tx, third, cap=1) == "cap2@example.cz"


def test_unsubscribing_keeps_the_first_time_and_stops_the_confirmation(tx: _Tx) -> None:
    token = _entry(tx, "out@example.cz")
    assert waitlist.unsubscribe(tx, token) is True
    assert waitlist.unsubscribe(tx, token) is False
    assert waitlist.unsubscribe(tx, uuid.uuid4()) is False
    assert waitlist.claim_confirmation(tx, token) is None
    with tx.cursor() as cur:
        cur.execute("SELECT email, unsubscribed_at IS NOT NULL FROM waitlist_signups WHERE followup_token = %s", (token,))
        assert cur.fetchone() == ("out@example.cz", True)


def _hook(tx: _Tx, email: str) -> dict[str, Any]:
    with tx.cursor() as cur:
        cur.execute("SELECT public.hook_before_user_created(%s)", (Jsonb({"user": {"email": email}}),))
        return cur.fetchone()[0]


def test_only_an_invited_address_may_create_an_account(tx: _Tx) -> None:
    _entry(tx, "invited@example.cz")
    _entry(tx, "waiting@example.cz")
    with tx.cursor() as cur:
        cur.execute("SELECT id FROM waitlist_signups WHERE email = 'invited@example.cz'")
        entry_id = cur.fetchone()[0]
    waitlist.stamp_invitation(tx, entry_id)
    assert _hook(tx, "invited@example.cz") == {}
    assert _hook(tx, "Invited@Example.CZ") == {}
    for refused in ("waiting@example.cz", "stranger@example.cz"):
        error = _hook(tx, refused)["error"]
        assert error["http_code"] == 403 and "nemá pozvánku" in error["message"]


def test_sending_again_keeps_the_first_permission_and_counts_each_send(tx: _Tx) -> None:
    _entry(tx, "again@example.cz")
    with tx.cursor() as cur:
        cur.execute("UPDATE waitlist_signups SET invitation_sent_at = NULL")
        cur.execute("UPDATE waitlist_signups SET invited_at = now() - interval '3 days', "
                    "invitation_sent_at = now() - interval '3 days' WHERE email = 'again@example.cz' RETURNING id, invited_at")
        entry_id, first = cur.fetchone()
    assert waitlist.invitations_sent_today(tx) == 0
    waitlist.stamp_invitation(tx, entry_id)
    assert waitlist.invitations_sent_today(tx) == 1
    with tx.cursor() as cur:
        cur.execute("SELECT invited_at FROM waitlist_signups WHERE id = %s", (entry_id,))
        assert cur.fetchone()[0] == first


def test_the_lists_know_whether_an_entry_has_an_account(tx: _Tx) -> None:
    _entry(tx, "member@example.cz")
    _entry(tx, "nobody@example.cz")
    with tx.cursor() as cur:
        cur.execute("INSERT INTO auth.users (id, email) VALUES (gen_random_uuid(), 'Member@Example.cz')")
    rows = {r["email"]: r for r in waitlist.list_signups(tx)}
    assert rows["member@example.cz"]["has_account"] is True and rows["nobody@example.cz"]["has_account"] is False
    ids = [rows["member@example.cz"]["id"], rows["nobody@example.cz"]["id"]]
    assert [(e["email"], e["has_account"]) for e in waitlist.invitation_entries(tx, ids)] == [
        ("member@example.cz", True), ("nobody@example.cz", False),
    ]


def test_no_browser_role_can_call_the_hook(tx: _Tx) -> None:
    with tx.cursor() as cur:
        for role in ("anon", "authenticated"):
            cur.execute("SELECT has_function_privilege(%s, 'public.hook_before_user_created(jsonb)', 'EXECUTE')", (role,))
            assert cur.fetchone()[0] is False


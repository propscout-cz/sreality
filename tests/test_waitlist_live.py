"""Migration 699 and api/waitlist.py's two statements on the real schema: a new email gets a token and a second signup
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

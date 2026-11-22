"""The ČÚZK login-check budget on a real schema (migration 762): what counts, per username and per account,
and that the windows let a check through again. Gated on TEST_DATABASE_URL; everything rolls back."""

from __future__ import annotations

import os
import uuid

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DB_URL, reason="TEST_DATABASE_URL not set")

from api import cuzk_login  # noqa: E402


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(_DB_URL) as c:
        try:
            yield c
        finally:
            c.rollback()


def _account(conn) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute("insert into accounts (name) values ('budget test') returning id")
        return cur.fetchone()[0]


def _refuse(conn, account, username: str) -> None:
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, account, username), "refused")


def test_a_username_gets_two_unaccepted_checks_from_all_accounts_together(conn):
    a, b = _account(conn), _account(conn)
    _refuse(conn, a, "firma01")
    _refuse(conn, b, "firma01")
    for account in (a, b):
        with pytest.raises(cuzk_login.Throttled, match="username"):
            cuzk_login.admit_check(conn, account, "firma01")
    assert cuzk_login.admit_check(conn, a, "firma02") > 0, "another username is not spent"


def test_an_accepted_check_spends_nothing(conn):
    a = _account(conn)
    for _ in range(4):
        cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, a, "firma01"), "accepted")
    assert cuzk_login.admit_check(conn, a, "firma01") > 0


def test_a_check_in_flight_and_one_with_no_answer_both_count(conn):
    a = _account(conn)
    cuzk_login.admit_check(conn, a, "firma01")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, a, "firma01"), "no_answer")
    with pytest.raises(cuzk_login.Throttled, match="username"):
        cuzk_login.admit_check(conn, a, "firma01")


def test_an_account_cannot_walk_a_list_of_usernames(conn):
    a = _account(conn)
    for i in range(cuzk_login.ACCOUNT_BUDGET):
        _refuse(conn, a, f"user{i}")
    with pytest.raises(cuzk_login.Throttled, match="account"):
        cuzk_login.admit_check(conn, a, "one-more")
    assert cuzk_login.admit_check(conn, _account(conn), "one-more") > 0, "another account is not spent"


def test_the_windows_pass(conn):
    a = _account(conn)
    _refuse(conn, a, "firma01")
    _refuse(conn, a, "firma01")
    with conn.cursor() as cur:
        cur.execute("update account_cuzk_login_checks set checked_at = now() - interval '16 minutes'")
    assert cuzk_login.admit_check(conn, a, "firma01") > 0


def test_the_username_is_stored_as_a_hash(conn):
    a = _account(conn)
    cuzk_login.admit_check(conn, a, "firma01")
    with conn.cursor() as cur:
        cur.execute("select username_sha from account_cuzk_login_checks where account_id = %s", (a,))
        sha, = cur.fetchone()
    assert len(sha) == 64 and "firma01" not in sha

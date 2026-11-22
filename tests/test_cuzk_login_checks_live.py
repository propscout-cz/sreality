"""The ČÚZK login-check budgets on a real schema (migration 762), each connection its own session and every
admit committed, so the advisory locks and the counts are the ones production runs. Gated on TEST_DATABASE_URL."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DB_URL, reason="TEST_DATABASE_URL not set")

from cryptography.fernet import Fernet  # noqa: E402

from api import cuzk_login  # noqa: E402


def _connect():
    import psycopg

    return psycopg.connect(_DB_URL, autocommit=True)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cuzk_login.KEY_ENV, Fernet.generate_key().decode())


@pytest.fixture
def conn() -> Iterator:
    with _connect() as c:
        c.execute("delete from account_cuzk_login_checks")
        made: list[uuid.UUID] = []
        c.made = made  # type: ignore[attr-defined]
        try:
            yield c
        finally:
            c.execute("delete from account_cuzk_login_checks")
            for account in made:
                c.execute("delete from accounts where id = %s", (account,))


def _account(conn) -> uuid.UUID:
    account = conn.execute("insert into accounts (name) values ('budget test') returning id").fetchone()[0]
    conn.made.append(account)
    return account


def _age(conn, by: str) -> None:
    conn.execute(f"update account_cuzk_login_checks set admitted_at = admitted_at - interval '{by}', "
                 f"settled_at = settled_at - interval '{by}'")


def _check(conn, account, username: str, outcome: str = "refused") -> None:
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, account, username), outcome)


def _race(calls: list[tuple[uuid.UUID, str]]) -> tuple[int, int]:
    """Every call on its own connection, all released together: (admitted, throttled)."""
    gate, results = threading.Barrier(len(calls)), []

    def one(account: uuid.UUID, username: str) -> None:
        with _connect() as c:
            gate.wait()
            try:
                cuzk_login.admit_check(c, account, username)
                results.append(True)
            except cuzk_login.Throttled:
                results.append(False)

    threads = [threading.Thread(target=one, args=call) for call in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results.count(True), results.count(False)


def test_concurrent_checks_on_one_username_never_pass_the_budget(conn):
    accounts = [_account(conn) for _ in range(8)]
    assert _race([(a, "firma01") for a in accounts]) == (cuzk_login.USERNAME_BUDGET, 6)


def test_concurrent_checks_of_one_account_never_pass_its_budget(conn):
    me = _account(conn)
    assert _race([(me, f"user{i}") for i in range(20)]) == (cuzk_login.ACCOUNT_UNACCEPTED_BUDGET, 15)


def test_a_username_gets_two_unaccepted_checks_since_its_last_accepted_one(conn):
    a, b = _account(conn), _account(conn)
    _check(conn, a, "firma01")
    _check(conn, b, "firma01")
    for account in (a, b):
        with pytest.raises(cuzk_login.Throttled) as spent:
            cuzk_login.admit_check(conn, account, "firma01")
        assert spent.value.by == "username" and spent.value.retry_after_s == 24 * 3600
    assert cuzk_login.admit_check(conn, a, "firma02") > 0, "another username is not spent"


def test_an_accepted_check_resets_the_usernames_run_as_cuzk_does(conn):
    a = _account(conn)
    _check(conn, a, "firma01")
    _check(conn, a, "firma01", "accepted")
    _check(conn, a, "firma01")
    _check(conn, a, "firma01")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, a, "firma01")


def test_fifteen_minutes_do_not_reopen_a_spent_username(conn):
    a = _account(conn)
    _check(conn, a, "firma01")
    _check(conn, a, "firma01")
    _age(conn, "16 minutes")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, _account(conn), "firma01")
    _age(conn, "25 hours")
    assert cuzk_login.admit_check(conn, a, "firma01") > 0


def test_a_check_in_flight_and_one_with_no_answer_both_count(conn):
    a = _account(conn)
    cuzk_login.admit_check(conn, a, "firma01")
    _check(conn, a, "firma01", "no_answer")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, a, "firma01")


@pytest.mark.parametrize("variant", ["FIRMA01", "Firma01", "firma​01", "ｆｉｒｍａ０１"])
def test_variants_of_one_username_share_one_budget(conn, variant):
    a = _account(conn)
    _check(conn, a, "firma01")
    _check(conn, a, variant)
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, a, "firma01")


def test_an_account_cannot_walk_a_list_of_usernames(conn):
    a = _account(conn)
    for i in range(cuzk_login.ACCOUNT_UNACCEPTED_BUDGET):
        _check(conn, a, f"user{i}")
    with pytest.raises(cuzk_login.Throttled) as spent:
        cuzk_login.admit_check(conn, a, "one-more")
    assert spent.value.by == "account" and 3000 < spent.value.retry_after_s <= 3601
    assert cuzk_login.admit_check(conn, _account(conn), "one-more") > 0, "another account is not spent"


def test_accepted_checks_are_capped_too(conn):
    a = _account(conn)
    for _ in range(cuzk_login.ACCOUNT_TOTAL_BUDGET):
        _check(conn, a, "firma01", "accepted")
    with pytest.raises(cuzk_login.Throttled) as spent:
        cuzk_login.admit_check(conn, a, "firma01")
    assert spent.value.by == "account"


def test_settling_keeps_the_admit_time_and_stamps_the_answer(conn):
    a = _account(conn)
    check = cuzk_login.admit_check(conn, a, "firma01")
    conn.execute("update account_cuzk_login_checks set admitted_at = now() - interval '30 seconds' where id = %s",
                 (check,))
    cuzk_login.settle_check(conn, check, "refused")
    admitted, settled = conn.execute(
        "select extract(epoch from now() - admitted_at), extract(epoch from now() - settled_at) "
        "from account_cuzk_login_checks where id = %s", (check,)).fetchone()
    assert admitted > 25 and settled < 5


def test_an_accepted_check_does_not_wipe_one_still_in_flight_when_it_was_admitted(conn):
    """T0 a wrong check is sent; before ČÚZK answers it a correct one is admitted and accepted; then the wrong
    one settles. ČÚZK may have handled the correct one FIRST, so its counter holds 1: we must hold 1 too."""
    a = _account(conn)
    wrong = cuzk_login.admit_check(conn, a, "firma01")
    right = cuzk_login.admit_check(conn, a, "firma01")
    cuzk_login.settle_check(conn, right, "accepted")
    cuzk_login.settle_check(conn, wrong, "refused")
    _check(conn, a, "firma01")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, a, "firma01")


def test_an_accepted_check_by_another_account_resets_the_run(conn):
    a, b = _account(conn), _account(conn)
    _check(conn, a, "firma01")
    _check(conn, a, "firma01")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, b, "firma01")
    # The owner logs in correctly through us from another account once the window lets a check through.
    _age(conn, "25 hours")
    _check(conn, b, "firma01", "accepted")
    _check(conn, a, "firma01")
    _check(conn, a, "firma01")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, a, "firma01")


def test_a_correct_login_on_another_spelling_does_not_reset_the_run(conn):
    """Counting folds, resetting does not: 'Novak' accepted must not clear 'novak' if ČÚZK keeps them apart."""
    a = _account(conn)
    _check(conn, a, "novak")
    _check(conn, a, "Novak", "accepted")
    _check(conn, a, "novak")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, a, "novak")


def test_the_username_wait_is_rounded_up_to_the_hour(conn):
    a = _account(conn)
    _check(conn, a, "firma01")
    _check(conn, a, "firma01")
    with pytest.raises(cuzk_login.Throttled) as spent:
        cuzk_login.admit_check(conn, a, "firma01")
    assert spent.value.retry_after_s == 24 * 3600


def test_the_account_wait_runs_from_the_oldest_check_of_the_kind_over_its_cap(conn):
    a = _account(conn)
    _check(conn, a, "old-accepted", "accepted")
    conn.execute("update account_cuzk_login_checks set admitted_at = now() - interval '50 minutes', "
                 "settled_at = now() - interval '50 minutes'")
    for i in range(cuzk_login.ACCOUNT_UNACCEPTED_BUDGET):
        _check(conn, a, f"user{i}")
    with pytest.raises(cuzk_login.Throttled) as spent:
        cuzk_login.admit_check(conn, a, "one-more")
    assert spent.value.retry_after_s > 55 * 60, "not the ten minutes left on the old accepted check"


def test_a_deleted_account_leaves_its_spend_on_the_username(conn):
    a = _account(conn)
    _check(conn, a, "firma01")
    _check(conn, a, "firma01")
    conn.execute("delete from accounts where id = %s", (a,))
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, _account(conn), "firma01")


def test_the_ledger_is_pruned_as_checks_are_admitted(conn):
    a = _account(conn)
    _check(conn, a, "old")
    _age(conn, "3 days")
    cuzk_login.admit_check(conn, a, "new")
    assert conn.execute("select count(*) from account_cuzk_login_checks").fetchone()[0] == 1


def test_the_stored_name_is_a_keyed_hash(conn, monkeypatch):
    first = cuzk_login.budget_key("firma01")
    assert len(first) == 64 and "firma01" not in first
    import hashlib

    assert first != hashlib.sha256(b"firma01").hexdigest()
    monkeypatch.setenv(cuzk_login.KEY_ENV, Fernet.generate_key().decode())
    assert cuzk_login.budget_key("firma01") != first, "another server key, another name"

"""Cadastre prices on request, on a real schema (migration 763) with the ČÚZK service scripted: a purchase is
ordered once, resumed on the same report, stored for its buyer alone. Gated on TEST_DATABASE_URL."""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not _DB_URL, reason="TEST_DATABASE_URL not set")

from api import cadastre_prices as prices  # noqa: E402
from api import cuzk_login, wsdp_client  # noqa: E402

REPORT = (Path(__file__).resolve().parent / "fixtures" / "wsdp" / "ceny_dle_nemovitosti_trial.xml").read_bytes()
BUILDING = prices.Building(code=22948236, iskn_id=470801805)


@pytest.fixture(autouse=True)
def _login_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv(cuzk_login.KEY_ENV, Fernet.generate_key().decode())


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(_DB_URL, autocommit=True) as c:
        accounts: list[uuid.UUID] = []
        c.accounts = accounts  # type: ignore[attr-defined]
        try:
            yield c
        finally:
            for account in accounts:
                c.execute("delete from accounts where id = %s", (account,))


def _account(conn) -> uuid.UUID:
    account = conn.execute("insert into accounts (name) values ('prices test') returning id").fetchone()[0]
    conn.accounts.append(account)
    return account


class _Cuzk:
    """The service, scripted: counts what was ordered, and can stall or refuse."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, units: int = 3, ready_after: int = 0) -> None:
        self.generated: list[tuple[str, list[int]]] = []
        self.fetched = 0
        self.polls = 0
        self.ready_after = ready_after
        self.units = [wsdp_client.Unit(100 + i, f"565/{i + 1}") for i in range(units)]
        monkeypatch.setattr(wsdp_client, "find_units", lambda b, u, p: self.units)
        monkeypatch.setattr(wsdp_client, "generate_prices", self._generate)
        monkeypatch.setattr(wsdp_client, "report_state", self._state)
        monkeypatch.setattr(wsdp_client, "fetch_report", self._fetch)
        monkeypatch.setattr(prices, "building_of", lambda c, pid: BUILDING)
        monkeypatch.setattr(cuzk_login, "login_of", lambda c, a: ("firma01", "x"))

    def _generate(self, kind: str, ids: list[int], u: str, p: str) -> wsdp_client.Report:
        self.generated.append((kind, ids))
        return wsdp_client.Report(9000 + len(self.generated), "čeká", None)

    def _state(self, report_id: int, u: str, p: str) -> wsdp_client.Report:
        self.polls += 1
        ready = self.polls > self.ready_after
        return wsdp_client.Report(report_id, "zpracován" if ready else "vytváří se", 50 if ready else None)

    def _fetch(self, report_id: int, u: str, p: str) -> tuple[wsdp_client.Report, bytes]:
        self.fetched += 1
        return wsdp_client.Report(report_id, "zaúčtován", 50), REPORT


def _buy(conn, account, **kw: Any) -> None:
    prices.buy(account, 7, opener=lambda: contextlib.nullcontext(conn), sleep=lambda s: None, **kw)


def test_a_purchase_is_ordered_once_and_its_deeds_are_the_buyers(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(conn, me)
    assert cuzk.generated == [("JED_S", [100, 101, 102])] and cuzk.fetched == 1
    mine = prices.state(conn, me, BUILDING.code)
    assert mine["purchase"]["status"] == "done" and mine["purchase"]["cost_czk"] == 50
    assert mine["purchase"]["object_count"] == 3
    assert len(mine["deeds"]) >= 1 and mine["deeds"][0]["proceeding"].startswith("V-")
    assert mine["deeds"][0]["properties"][0]["label"]
    theirs = prices.state(conn, other, BUILDING.code)
    assert theirs["purchase"] is None and theirs["deeds"] == []


def test_a_report_cuzk_has_not_finished_is_resumed_not_ordered_again(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=10_000), _account(conn)
    ticks = iter(range(0, 10_000, 30))
    with pytest.raises(prices.StillPreparing):
        _buy(conn, me, clock=lambda: next(ticks))
    assert prices.state(conn, me, BUILDING.code)["purchase"]["status"] == "ordered"
    cuzk.ready_after = 0
    _buy(conn, me)
    assert len(cuzk.generated) == 1, "the second request picked the same report up"
    assert prices.state(conn, me, BUILDING.code)["purchase"]["status"] == "done"


def test_a_refusal_before_the_order_fails_the_request_and_charges_nothing(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)

    def refuse(*a: Any) -> Any:
        raise wsdp_client.WsdpError("refused", 500)

    monkeypatch.setattr(wsdp_client, "find_units", refuse)
    with pytest.raises(wsdp_client.WsdpError):
        _buy(conn, me)
    assert cuzk.generated == [] and cuzk.fetched == 0
    assert prices.state(conn, me, BUILDING.code)["purchase"] is None, "a failed request is not shown as a purchase"
    status, error = conn.execute(
        "select status, error from cadastre_price_purchases where account_id = %s", (me,)).fetchone()
    assert status == "failed" and "refused" in error


def test_a_house_with_no_units_is_priced_as_a_building(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, units=0), _account(conn)
    _buy(conn, me)
    assert cuzk.generated == [("STA_S", [BUILDING.iskn_id])]


def test_a_building_one_output_cannot_cover_is_refused_before_any_order(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, units=201), _account(conn)
    with pytest.raises(prices.TooManyUnits):
        _buy(conn, me)
    assert cuzk.generated == []


def test_buying_again_later_shows_the_newer_purchase(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    _buy(conn, me)
    _buy(conn, me, repeat=True)
    assert len(cuzk.generated) == 2, "a finished purchase is not in flight: a confirmed repeat is a new, priced order"
    deeds = conn.execute("select count(distinct purchase_id) from cadastre_deeds where account_id = %s",
                         (me,)).fetchone()[0]
    assert deeds == 2 and len(prices.state(conn, me, BUILDING.code)["deeds"]) == len(
        {d["proceeding"] + d["kind"] + str(d["signed_on"]) + str(d["price"])
         for d in prices.state(conn, me, BUILDING.code)["deeds"]})


def test_without_a_stored_login_nothing_is_started(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    monkeypatch.setattr(cuzk_login, "login_of", lambda c, a: None)
    with pytest.raises(prices.NoLogin):
        _buy(conn, me)
    assert cuzk.generated == []
    assert conn.execute("select count(*) from cadastre_price_purchases where account_id = %s", (me,)).fetchone()[0] == 0


def _checks(conn, account) -> list[str | None]:
    return [r[0] for r in conn.execute(
        "select outcome from account_cuzk_login_checks where account_id = %s order by id", (account,)).fetchall()]


def test_a_building_already_bought_is_not_bought_again_without_the_repeat(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    _buy(conn, me)
    with pytest.raises(prices.AlreadyBought) as refused:
        _buy(conn, me)
    assert refused.value.finished_at is not None
    assert len(cuzk.generated) == 1 and cuzk.fetched == 1, "nothing was sent to ČÚZK for the refused click"
    assert _checks(conn, me) == ["accepted"], "and it used nothing of the login budget"
    assert conn.execute("select count(*) from cadastre_price_purchases where account_id = %s", (me,)).fetchone()[0] == 1


def test_another_accounts_purchase_does_not_count_as_mine(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(conn, other)
    _buy(conn, me)
    assert len(cuzk.generated) == 2


def test_a_request_in_flight_is_resumed_without_the_repeat(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    _buy(conn, me)
    cuzk.ready_after, cuzk.polls = 10**6, 0
    with pytest.raises(prices.StillPreparing):
        _buy(conn, me, repeat=True, clock=iter([0.0, 10**6]).__next__)
    cuzk.ready_after = 0
    _buy(conn, me)
    assert len(cuzk.generated) == 2, "the unfinished repeat is resumed on its own report, no third order"


def test_each_use_of_the_login_is_one_row_of_the_login_ledger(conn, monkeypatch):
    _Cuzk(monkeypatch)
    me = _account(conn)
    _buy(conn, me)
    assert _checks(conn, me) == ["accepted"], "four calls to ČÚZK, one use of the login"


def test_a_login_cuzk_refuses_during_a_purchase_counts_and_then_blocks(conn, monkeypatch):
    _Cuzk(monkeypatch)
    me = _account(conn)
    sent: list[int] = []

    def refuse(*a: Any) -> Any:
        sent.append(1)
        raise wsdp_client.WsdpError("refused", 500)

    monkeypatch.setattr(wsdp_client, "find_units", refuse)
    for _ in range(cuzk_login.USERNAME_BUDGET):
        with pytest.raises(wsdp_client.WsdpError):
            _buy(conn, me)
    assert _checks(conn, me) == ["refused"] * cuzk_login.USERNAME_BUDGET
    with pytest.raises(cuzk_login.Throttled):
        _buy(conn, me)
    assert len(sent) == cuzk_login.USERNAME_BUDGET, "past the budget nothing reaches ČÚZK"
    assert conn.execute("select count(*) from cadastre_price_purchases where account_id = %s", (me,)).fetchone()[0] \
        == cuzk_login.USERNAME_BUDGET, "and the throttled click wrote no purchase"


def test_an_answer_that_never_came_is_not_a_refusal(conn, monkeypatch):
    _Cuzk(monkeypatch)
    me = _account(conn)

    def silent(*a: Any) -> Any:
        raise wsdp_client.WsdpError("timeout", None)

    monkeypatch.setattr(wsdp_client, "find_units", silent)
    with pytest.raises(wsdp_client.WsdpError):
        _buy(conn, me)
    assert _checks(conn, me) == ["no_answer"]

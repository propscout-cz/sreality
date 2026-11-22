"""Cadastre prices on request, on a real schema (migration 763) with the ČÚZK service scripted. Every request opens
its own connection, as the API does, so two requests really are two sessions: one purchase is ordered once and
fetched once whatever overlaps or breaks, and it is its buyer's alone. Gated on TEST_DATABASE_URL."""

from __future__ import annotations

import datetime
import os
import threading
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
REFUSED = wsdp_client.WsdpError("refused", 500)
TIMEOUT = wsdp_client.WsdpError("timeout")
SERVICE = wsdp_client.WsdpError("service", 505, authenticated=True)


def _open() -> Any:
    import psycopg

    return psycopg.connect(_DB_URL, autocommit=True)


@pytest.fixture(autouse=True)
def _login_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv(cuzk_login.KEY_ENV, Fernet.generate_key().decode())
    monkeypatch.delenv(wsdp_client.BASE_ENV, raising=False)


@pytest.fixture
def conn():
    with _open() as c:
        accounts: list[uuid.UUID] = []
        c.accounts = accounts  # type: ignore[attr-defined]
        try:
            yield c
        finally:
            for account in accounts:
                c.execute("delete from accounts where id = %s", (account,))


def _account(conn) -> uuid.UUID:
    """An account with a stored, accepted ČÚZK login."""
    account = conn.execute("insert into accounts (name) values ('prices test') returning id").fetchone()[0]
    conn.accounts.append(account)
    cuzk_login.save(conn, account, f"firma-{account.hex[:8]}", "x", datetime.datetime.now(datetime.UTC))
    return account


class _Cuzk:
    """The service, scripted: counts what was ordered and fetched, and each call can be made to raise or to wait."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, units: int = 3, ready_after: int = 0,
                 price: int | None = 50, document: bytes = REPORT) -> None:
        self.generated: list[tuple[str, list[int]]] = []
        self.fetched: list[int] = []
        self.calls = 0
        self.polls = 0
        self.ready_after, self.price, self.document = ready_after, price, document
        self.raises: dict[str, list[Exception]] = {}
        self.gate: dict[str, tuple[threading.Event, threading.Event]] = {}
        self.units = [wsdp_client.Unit(100 + i, f"565/{i + 1}") for i in range(units)]
        monkeypatch.setattr(wsdp_client, "find_units", self._find)
        monkeypatch.setattr(wsdp_client, "generate_prices", self._generate)
        monkeypatch.setattr(wsdp_client, "report_state", self._state)
        monkeypatch.setattr(wsdp_client, "fetch_report", self._fetch)
        monkeypatch.setattr(prices, "building_of", lambda c, pid: BUILDING)

    def hold(self, call: str) -> tuple[threading.Event, threading.Event]:
        """The next `call` waits inside ČÚZK: (it arrived, let it go)."""
        self.gate[call] = (threading.Event(), threading.Event())
        return self.gate[call]

    def _enter(self, call: str) -> None:
        self.calls += 1
        if call in self.gate:
            arrived, go = self.gate.pop(call)
            arrived.set()
            assert go.wait(20)
        if self.raises.get(call):
            raise self.raises[call].pop(0)

    def _find(self, b: int, u: str, p: str) -> list[wsdp_client.Unit]:
        self._enter("find")
        return self.units

    def _generate(self, kind: str, ids: list[int], u: str, p: str) -> wsdp_client.Report:
        self._enter("generate")
        self.generated.append((kind, ids))
        return wsdp_client.Report(9000 + len(self.generated), "čeká", None)

    def _state(self, report_id: int, u: str, p: str) -> wsdp_client.Report:
        self._enter("poll")
        self.polls += 1
        ready = self.polls > self.ready_after
        return wsdp_client.Report(report_id, "zpracován" if ready else "vytváří se", self.price if ready else None)

    def _fetch(self, report_id: int, u: str, p: str) -> tuple[wsdp_client.Report, bytes]:
        self._enter("fetch")
        self.fetched.append(report_id)
        return wsdp_client.Report(report_id, "zaúčtován", self.price), self.document


def _buy(account, **kw: Any) -> None:
    kw.setdefault("clock", iter(range(0, 10**6, 100)).__next__)  # each poll is past the deadline: one poll a request
    prices.buy(account, 7, opener=_open, sleep=lambda s: None, **kw)


def _in_thread(account, **kw: Any) -> tuple[threading.Thread, list[BaseException | None]]:
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            _buy(account, **kw)
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - the thread's outcome is the assertion
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _rows(conn, account) -> list[tuple]:
    return conn.execute(
        "select status, report_id, cost_czk, raw_report is not null, fetch_sent_at is not null "
        "from cadastre_price_purchases where account_id = %s order by id", (account,)).fetchall()


def _deeds(conn, account) -> int:
    return conn.execute("select count(*) from cadastre_deeds where account_id = %s", (account,)).fetchone()[0]


def _checks(conn, account) -> list[str | None]:
    return [r[0] for r in conn.execute(
        "select outcome from account_cuzk_login_checks where account_id = %s order by id", (account,)).fetchall()]


PARSED = len(prices.cadastre_price_report.parse(REPORT))


def test_a_purchase_is_ordered_once_fetched_once_and_its_deeds_are_the_buyers(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(me)
    assert cuzk.generated == [("JED_S", [100, 101, 102])] and cuzk.fetched == [9001]
    assert _rows(conn, me) == [("done", 9001, 50, False, True)], "cost recorded, the kept file cleared"
    assert _deeds(conn, me) == PARSED > 0
    mine = prices.state(conn, me, BUILDING.code)
    assert mine["purchase"]["status"] == "done" and len(mine["deeds"]) == PARSED
    assert prices.state(conn, other, BUILDING.code) == {"price_czk": 50, "purchase": None, "deeds": []}
    assert _checks(conn, me) == ["accepted"], "four calls to ČÚZK, one use of the login"


def test_two_requests_at_once_order_one_report(conn, monkeypatch):
    """B1: the second request arrives while the first is still ordering. It must not order."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("find")
    first, first_outcome = _in_thread(me)
    assert arrived.wait(20)
    with pytest.raises(prices.InProgress):
        _buy(me)
    assert cuzk.generated == [], "the request that did not insert the row sent nothing"
    go.set()
    first.join(20)
    assert first_outcome == [None]
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001]
    assert _rows(conn, me) == [("done", 9001, 50, False, True)] and _deeds(conn, me) == PARSED


def test_a_request_that_lost_its_stale_claim_never_fetches_what_it_ordered(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("generate")
    first, first_outcome = _in_thread(me)
    assert arrived.wait(20)
    conn.execute("update cadastre_price_purchases set claimed_at = now() - interval '10 minutes' "
                 "where account_id = %s", (me,))
    _buy(me)  # takes the stale claim, orders, fetches
    assert cuzk.fetched == [9001]
    go.set()
    first.join(20)
    assert isinstance(first_outcome[0], prices.InProgress)
    assert len(cuzk.generated) == 2 and cuzk.fetched == [9001], "the loser's report stays unfetched: uncharged"
    assert _rows(conn, me) == [("done", 9001, 50, False, True)] and _deeds(conn, me) == PARSED


def test_a_report_cuzk_has_not_finished_is_resumed_not_ordered_again(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(me)
    assert _rows(conn, me) == [("ordered", 9001, None, False, False)] and cuzk.fetched == []
    _buy(me)
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001]
    assert _rows(conn, me)[0][0] == "done"


def test_two_resumes_at_once_fetch_once_and_store_the_deeds_once(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(me)
    arrived, go = cuzk.hold("fetch")
    first, first_outcome = _in_thread(me)
    assert arrived.wait(20)
    with pytest.raises(prices.InProgress):
        _buy(me)
    go.set()
    first.join(20)
    assert first_outcome == [None] and cuzk.fetched == [9001] and _deeds(conn, me) == PARSED


def test_a_lost_answer_to_the_charging_fetch_is_known_and_resumed_on_the_same_report(conn, monkeypatch):
    """M2: after the fetch was sent, 'nothing was charged' cannot be said."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(me)
    assert _rows(conn, me) == [("ordered", 9001, None, False, True)]
    with pytest.raises(prices.InProgress):
        _buy(me)  # inside the fetch claim: nothing is sent twice at once
    conn.execute("update cadastre_price_purchases set fetch_sent_at = now() - interval '10 minutes' "
                 "where account_id = %s", (me,))
    _buy(me)
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001], "the same report, never a second order"
    assert _rows(conn, me)[0][0] == "done" and _deeds(conn, me) == PARSED


def test_a_fetched_report_that_cannot_be_read_is_kept_and_not_bought_again(conn, monkeypatch):
    """M1: the money is spent, so the file stays; the purchase ends and does not loop."""
    cuzk, me = _Cuzk(monkeypatch, document=b"<not a report"), _account(conn)
    with pytest.raises(prices.PaidUnread):
        _buy(me)
    assert _rows(conn, me) == [("paid_unread", 9001, 50, True, True)]
    kept = conn.execute("select raw_report from cadastre_price_purchases where account_id = %s", (me,)).fetchone()[0]
    assert bytes(kept) != b"<not a report" and cuzk_login.unseal(kept) == b"<not a report"
    assert prices.state(conn, me, BUILDING.code)["purchase"]["status"] == "paid_unread"
    sent = cuzk.calls
    with pytest.raises(prices.PaidUnread):
        _buy(me)  # asking again re-reads the kept file, and it still cannot be read
    assert cuzk.calls == sent and _checks(conn, me) == ["accepted"], "free: nothing sent, no budget used"


def test_a_kept_report_is_read_later_without_a_new_purchase(conn, monkeypatch):
    """The parser is fixed after the fact: the next request stores the deeds from the kept file."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    real_parse = prices.cadastre_price_report.parse

    def broken(document: bytes) -> Any:
        raise ValueError("a field this parser does not know")

    monkeypatch.setattr(prices.cadastre_price_report, "parse", broken)
    with pytest.raises(prices.PaidUnread):
        _buy(me)
    monkeypatch.setattr(prices.cadastre_price_report, "parse", real_parse)
    sent = cuzk.calls
    _buy(me)
    assert cuzk.calls == sent and cuzk.fetched == [9001], "nothing sent to ČÚZK for the re-read"
    assert _rows(conn, me) == [("done", 9001, 50, False, True)] and _deeds(conn, me) == PARSED


def test_a_possibly_charged_report_cuzk_no_longer_serves_is_not_bought_again_silently(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(me)
    cuzk.raises["poll"] = [SERVICE]
    with pytest.raises(prices.PaidUnread):
        _buy(me)
    assert _rows(conn, me) == [("paid_unread", 9001, None, False, True)]
    with pytest.raises(prices.AlreadyBought):
        _buy(me)
    assert len(cuzk.generated) == 1


def test_a_report_cuzk_no_longer_serves_ends_the_purchase_and_a_new_one_can_start(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(me)
    cuzk.raises["poll"] = [SERVICE]
    with pytest.raises(prices.ReportGone):
        _buy(me)
    assert _rows(conn, me) == [("failed", 9001, None, False, False)] and cuzk.fetched == []
    cuzk.ready_after = 0
    _buy(me)  # no `repeat`: nothing was ever paid
    assert len(cuzk.generated) == 2 and cuzk.fetched == [9002]


@pytest.mark.parametrize("stated", [60, None])
def test_a_report_priced_otherwise_than_acknowledged_is_not_fetched(conn, monkeypatch, stated):
    cuzk, me = _Cuzk(monkeypatch, price=stated), _account(conn)
    with pytest.raises(prices.PriceChanged) as changed:
        _buy(me)
    assert changed.value.stated_czk == stated and cuzk.fetched == []
    assert _rows(conn, me) == [("failed", 9001, None, False, False)]


def test_a_refused_login_fails_the_request_is_ledgered_and_is_not_used_again(conn, monkeypatch):
    """M5: the second click must not spend the username's last unit."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["find"] = [REFUSED]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(me)
    assert _rows(conn, me) == [("failed", None, None, False, False)] and _checks(conn, me) == ["refused"]
    assert cuzk_login.state(conn, me)["refused"]
    sent = cuzk.calls
    with pytest.raises(prices.LoginRefused):
        _buy(me)
    assert cuzk.calls == sent and _checks(conn, me) == ["refused"], "nothing sent, nothing more spent"
    cuzk_login.save(conn, me, "firma01", "y", datetime.datetime.now(datetime.UTC))
    assert not cuzk_login.state(conn, me)["refused"]
    _buy(me)
    assert _rows(conn, me)[-1][0] == "done"


def test_a_refusal_later_in_the_same_purchase_is_ledgered_too(conn, monkeypatch):
    """M3: the password changed between the order and the poll."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["poll"] = [REFUSED]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(me)
    assert _checks(conn, me) == ["refused"], "the row its first answer settled as accepted now reads refused"
    assert cuzk_login.state(conn, me)["refused"] and cuzk.fetched == []
    assert _rows(conn, me) == [("ordered", 9001, None, False, False)], "the order stands for after the re-save"


def test_an_error_cuzk_gives_after_accepting_the_login_does_not_count_against_it(conn, monkeypatch):
    """M4: an unknown building id is not a wrong password."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    for _ in range(cuzk_login.USERNAME_BUDGET + 1):
        cuzk.raises["find"] = [SERVICE]
        with pytest.raises(wsdp_client.WsdpError):
            _buy(me)
    assert _checks(conn, me) == ["accepted"] * (cuzk_login.USERNAME_BUDGET + 1)
    assert not cuzk_login.state(conn, me)["refused"]


def test_an_answer_that_never_came_is_not_a_refusal(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["find"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(me)
    assert _checks(conn, me) == ["no_answer"] and not cuzk_login.state(conn, me)["refused"]


def test_a_misconfigured_address_sends_nothing_and_spends_nothing(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    monkeypatch.setenv(wsdp_client.BASE_ENV, "http://example.com/")
    with pytest.raises(wsdp_client.WsdpError):
        _buy(me)
    assert cuzk.calls == 0 and _rows(conn, me) == [] and _checks(conn, me) == []


def test_a_login_past_its_budget_sends_nothing_and_writes_no_purchase(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    for _ in range(cuzk_login.USERNAME_BUDGET):
        cuzk.raises["find"] = [TIMEOUT]
        with pytest.raises(wsdp_client.WsdpError):
            _buy(me)
    sent, rows = cuzk.calls, len(_rows(conn, me))
    with pytest.raises(cuzk_login.Throttled):
        _buy(me)
    assert cuzk.calls == sent and len(_rows(conn, me)) == rows


def test_a_building_already_bought_is_not_bought_again_without_the_repeat(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    _buy(me)
    sent = cuzk.calls
    with pytest.raises(prices.AlreadyBought) as refused:
        _buy(me)
    assert refused.value.finished_at is not None
    assert cuzk.calls == sent and _checks(conn, me) == ["accepted"], "nothing sent, no budget used"
    _buy(me, repeat=True)
    assert len(cuzk.generated) == 2 and [r[0] for r in _rows(conn, me)] == ["done", "done"]
    shown = prices.state(conn, me, BUILDING.code)["deeds"]
    assert len(shown) == PARSED and _deeds(conn, me) == 2 * PARSED, "the card shows the newer purchase's deeds once"


def test_another_accounts_purchase_does_not_count_as_mine(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(other)
    _buy(me)
    assert len(cuzk.generated) == 2


def test_a_house_with_no_units_is_priced_as_a_building(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, units=0), _account(conn)
    _buy(me)
    assert cuzk.generated == [("STA_S", [BUILDING.iskn_id])]


def test_a_building_one_output_cannot_cover_is_refused_before_any_order(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, units=201), _account(conn)
    with pytest.raises(prices.TooManyUnits):
        _buy(me)
    assert cuzk.generated == [] and _rows(conn, me) == [("failed", None, None, False, False)]


def test_without_a_stored_login_nothing_is_started(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk_login.remove(conn, me)
    with pytest.raises(prices.NoLogin):
        _buy(me)
    assert cuzk.calls == 0 and _rows(conn, me) == []

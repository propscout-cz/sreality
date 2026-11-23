"""Cadastre prices on request, on a real schema (migration 763) with the ČÚZK service scripted. Every request opens
its own connections, as the API does, so two requests really are two sessions. What is pinned: one run per account
at a time, one order and one charged fetch per purchase whatever overlaps or breaks, no fetched report ever lost,
every state with a way out, and a login ČÚZK may refuse never sent a third time. Gated on TEST_DATABASE_URL."""

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
ELSEWHERE = prices.Building(code=22948237, iskn_id=470801806)
REFUSED = wsdp_client.WsdpError("refused", 500)
TIMEOUT = wsdp_client.WsdpError("timeout")
GONE = wsdp_client.WsdpError("service", wsdp_client.REPORT_GONE, authenticated=True)
OTHER_ERROR = wsdp_client.WsdpError("service", 505, authenticated=True)
PARSED = len(prices.cadastre_price_report.parse(REPORT))


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


def _account(conn, username: str | None = None) -> uuid.UUID:
    """An account with a stored, accepted ČÚZK login."""
    account = conn.execute("insert into accounts (name) values ('prices test') returning id").fetchone()[0]
    conn.accounts.append(account)
    cuzk_login.save(conn, account, username or f"firma-{account.hex[:8]}", "x", datetime.datetime.now(datetime.UTC))
    return account


class _Cuzk:
    """The service, scripted: counts what was ordered and fetched; each call can be made to raise, to wait, or to
    run a hook before it answers. `t` is the clock the purchase reads: a poll that is not ready moves it past the
    poll's own wait, so one request makes one poll."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, units: int = 3, ready_after: int = 0,
                 price: int | None = 50, document: bytes = REPORT, ready_state: str = "zpracován") -> None:
        self.generated: list[tuple[str, list[int]]] = []
        self.fetched: list[int] = []
        self.calls = 0
        self.polls = 0
        self.t = 0.0
        self.ready_after, self.price, self.document, self.ready_state = ready_after, price, document, ready_state
        self.waiting_state = "vytváří se"
        self.raises: dict[str, list[Exception]] = {}
        self.gate: dict[str, tuple[threading.Event, threading.Event]] = {}
        self.hook: dict[str, Any] = {}
        self.units = [wsdp_client.Unit(100 + i, f"565/{i + 1}") for i in range(units)]
        self.building = BUILDING
        monkeypatch.setattr(wsdp_client, "find_units", self._find)
        monkeypatch.setattr(wsdp_client, "generate_prices", self._generate)
        monkeypatch.setattr(wsdp_client, "report_state", self._state)
        monkeypatch.setattr(wsdp_client, "fetch_report", self._fetch)
        monkeypatch.setattr(prices, "building_of", lambda c, pid: BUILDING if pid == 7 else ELSEWHERE)

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
        if call in self.hook:
            self.hook.pop(call)()
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
        if self.polls > self.ready_after:
            return wsdp_client.Report(report_id, self.ready_state, self.price)
        self.t += prices.POLL_FOR_S + 1
        return wsdp_client.Report(report_id, self.waiting_state, None)

    def _fetch(self, report_id: int, u: str, p: str) -> tuple[wsdp_client.Report, bytes]:
        self._enter("fetch")
        self.fetched.append(report_id)
        return wsdp_client.Report(report_id, "zaúčtován", self.price), self.document


def _buy(cuzk: _Cuzk, account, property_id: int = 7, **kw: Any) -> None:
    prices.buy(account, property_id, opener=_open, sleep=lambda s: None, clock=lambda: cuzk.t, **kw)


def _in_thread(cuzk: _Cuzk, account, **kw: Any) -> tuple[threading.Thread, list[BaseException | None]]:
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            _buy(cuzk, account, **kw)
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


def _id(conn, account) -> int:
    return conn.execute("select max(id) from cadastre_price_purchases where account_id = %s", (account,)).fetchone()[0]


def _deeds(conn, account) -> int:
    return conn.execute("select count(*) from cadastre_deeds where account_id = %s", (account,)).fetchone()[0]


def _checks(conn, account) -> list[str | None]:
    return [r[0] for r in conn.execute(
        "select outcome from account_cuzk_login_checks where account_id = %s order by id", (account,)).fetchall()]


def _lease_free(conn, account) -> bool:
    return conn.execute("select run_token is null from account_cuzk_logins where account_id = %s",
                        (account,)).fetchone()[0]


def _later(conn, account) -> None:
    """Minutes pass: a lease nobody renewed is stale, and a fetch that was sent can no longer be being served."""
    conn.execute("update account_cuzk_logins set run_beat_at = now() - interval '1 hour' where account_id = %s",
                 (account,))
    conn.execute("update cadastre_price_purchases set fetch_sent_at = fetch_sent_at - interval '1 hour' "
                 "where account_id = %s", (account,))


DONE = ("done", 9001, 50, False, True)


# --- the plain purchase ---------------------------------------------------------------------------------------

def test_a_purchase_is_ordered_once_fetched_once_and_its_deeds_are_the_buyers(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(cuzk, me)
    assert cuzk.generated == [("JED_S", [100, 101, 102])] and cuzk.fetched == [9001]
    assert _rows(conn, me) == [DONE], "cost recorded, the kept file cleared"
    assert _deeds(conn, me) == PARSED > 0 and _lease_free(conn, me)
    mine = prices.state(conn, me, BUILDING.code)
    assert mine["purchase"]["status"] == "done" and len(mine["deeds"]) == PARSED and mine["running"] is False
    theirs = prices.state(conn, other, BUILDING.code)
    assert theirs["purchase"] is None and theirs["deeds"] == []
    assert _checks(conn, me) == ["accepted"] * 4, "four calls to ČÚZK, each its own row of the login ledger"


def test_a_house_with_no_units_is_priced_as_a_building(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, units=0), _account(conn)
    _buy(cuzk, me)
    assert cuzk.generated == [("STA_S", [BUILDING.iskn_id])]


def test_a_building_one_output_cannot_cover_is_refused_before_any_order(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, units=201), _account(conn)
    with pytest.raises(prices.TooManyUnits):
        _buy(cuzk, me)
    assert cuzk.generated == [] and _rows(conn, me) == [("failed", None, None, False, False)]


def test_without_a_stored_login_nothing_is_started(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk_login.remove(conn, me)
    with pytest.raises(prices.NoLogin):
        _buy(cuzk, me)
    assert cuzk.calls == 0 and _rows(conn, me) == []


def test_a_misconfigured_address_sends_nothing_and_spends_nothing(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    monkeypatch.setenv(wsdp_client.BASE_ENV, "http://example.com/")
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert cuzk.calls == 0 and _rows(conn, me) == [] and _checks(conn, me) == [] and _lease_free(conn, me)


# --- one run per account ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("held_in", ["find", "generate", "poll", "fetch"])
def test_while_a_run_is_live_every_other_request_of_the_account_changes_nothing(conn, monkeypatch, held_in):
    """The second review's B1 and B2: nobody resumes, polls or closes a purchase beside its live run, on this
    building or another."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold(held_in)
    first, first_outcome = _in_thread(cuzk, me)
    assert arrived.wait(20)
    sent, purchase_id, checks = cuzk.calls, _id(conn, me), _checks(conn, me)
    assert prices.state(conn, me, BUILDING.code)["running"] is True
    for kw in ({}, {"repeat": True}, {"resume": purchase_id}, {"property_id": 8}):
        with pytest.raises(prices.InProgress) as busy:
            _buy(cuzk, me, **kw)
        assert busy.value.why == "busy"
    assert cuzk.calls == sent, "nothing was sent to ČÚZK beside the live run"
    assert _checks(conn, me) == checks, "and none of them spent a row of the login ledger"
    go.set()
    first.join(20)
    assert first_outcome == [None]
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001]
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED and _lease_free(conn, me)


def test_a_run_whose_lease_was_taken_sends_nothing_more(conn, monkeypatch):
    """A lease is taken over only when stale, which a live run cannot be; if it ever is, the old run stops at its
    next call instead of fetching beside the new one."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("generate")
    first, first_outcome = _in_thread(cuzk, me)
    assert arrived.wait(20)
    purchase_id = _id(conn, me)
    conn.execute("update account_cuzk_logins set run_beat_at = now() - interval '1 hour' where account_id = %s", (me,))
    _buy(cuzk, me, resume=purchase_id)  # takes the stale lease, orders, fetches
    assert cuzk.fetched == [9001] and _rows(conn, me) == [DONE]
    go.set()
    first.join(20)
    assert isinstance(first_outcome[0], prices.InProgress)
    assert len(cuzk.generated) == 2 and cuzk.fetched == [9001], "the old run's report stays unfetched: uncharged"
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_a_request_out_of_time_starts_no_further_call(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.hook["find"] = lambda: setattr(cuzk, "t", cuzk.t + prices.RUN_S + 1)
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)
    assert cuzk.generated == [] and _rows(conn, me) == [("pending", None, None, False, False)]
    _buy(cuzk, me, resume=_id(conn, me))
    assert len(cuzk.generated) == 1 and _rows(conn, me) == [DONE]


def test_the_lease_is_given_back_whatever_ends_the_run(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["find"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert _lease_free(conn, me)


# --- which purchase a request is about -------------------------------------------------------------------------

def test_a_report_cuzk_has_not_finished_is_continued_by_name_not_ordered_again(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)
    assert _rows(conn, me) == [("ordered", 9001, None, False, False)] and cuzk.fetched == []
    sent = cuzk.calls
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)  # a NEW purchase beside one in flight: nothing happens, the card shows the one in flight
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me, repeat=True)
    assert cuzk.calls == sent
    _buy(cuzk, me, resume=_id(conn, me))
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001] and _rows(conn, me) == [DONE]


def test_a_resume_can_never_start_a_purchase(conn, monkeypatch):
    """The second review's M3: a stale card's 'finish' button, after its purchase ended."""
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    cuzk.raises["find"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    ended = _id(conn, me)
    sent = cuzk.calls
    for who, purchase in ((me, ended), (me, 10**9), (other, ended)):
        with pytest.raises(prices.NotResumable):
            _buy(cuzk, who, resume=purchase)
    with pytest.raises(prices.NotResumable):
        _buy(cuzk, me, property_id=8, resume=ended)
    assert cuzk.calls == sent and len(_rows(conn, me)) == 1 and _rows(conn, other) == []


def test_a_building_already_paid_for_is_not_bought_again_without_the_repeat(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    _buy(cuzk, me)
    sent = cuzk.calls
    with pytest.raises(prices.AlreadyBought) as refused:
        _buy(cuzk, me)
    assert refused.value.finished_at is not None
    assert cuzk.calls == sent and len(_checks(conn, me)) == 4, "nothing sent, no budget used"
    _buy(cuzk, me, repeat=True)
    assert len(cuzk.generated) == 2 and [r[0] for r in _rows(conn, me)] == ["done", "done"]
    shown = prices.state(conn, me, BUILDING.code)["deeds"]
    assert len(shown) == PARSED and _deeds(conn, me) == 2 * PARSED, "the card shows the newer purchase's deeds once"


def test_another_accounts_purchase_does_not_count_as_mine(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(cuzk, other)
    _buy(cuzk, me)
    assert len(cuzk.generated) == 2


# --- the charging fetch and what follows it ---------------------------------------------------------------------

def test_a_lost_answer_to_the_charging_fetch_is_known_and_continued_on_the_same_report(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(cuzk, me)
    assert _rows(conn, me) == [("ordered", 9001, None, False, True)]
    shown = prices.state(conn, me, BUILDING.code)["purchase"]
    assert shown["status"] == "ordered" and shown["fetch_sent"] and shown["fetching"] and not shown["file_kept"]
    _later(conn, me)
    assert prices.state(conn, me, BUILDING.code)["purchase"]["fetching"] is False
    cuzk.ready_state = "zaúčtován"  # ČÚZK did serve it: the report now reads as charged
    _buy(cuzk, me, resume=_id(conn, me))
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001], "the same report, never a second order"
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_no_second_fetch_leaves_while_the_first_may_still_be_being_served(conn, monkeypatch):
    """The third review's M1. The caller of a fetch gives up; the request itself lives on at ČÚZK. An immediate
    'finish' must not send a second FIRST fetch beside it: the lease is not given back, and even once it is stale
    the fetch is not sent again inside its cool-down."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(cuzk, me)
    sent, checks, purchase = cuzk.calls, _checks(conn, me), _id(conn, me)
    assert not _lease_free(conn, me), "the lease is left to go stale, not released"
    with pytest.raises(prices.InProgress) as busy:
        _buy(cuzk, me, resume=purchase)
    assert busy.value.why == "busy"
    conn.execute("update account_cuzk_logins set run_beat_at = now() - interval '1 hour' where account_id = %s", (me,))
    with pytest.raises(prices.InProgress) as cooling:
        _buy(cuzk, me, resume=purchase)  # the lease is free now; the fetch still is not sent again
    assert cooling.value.why == "fetching"
    assert cuzk.calls == sent and _checks(conn, me) == checks, "nothing sent, not even a poll; no budget used"
    assert _lease_free(conn, me)


def test_a_run_taken_over_during_its_fetch_does_not_get_a_second_fetch_beside_it(conn, monkeypatch):
    """Takeover at the fetch: the old holder's fetch is still in flight when another request takes the stale
    lease. Inside the cool-down it sends nothing; the old run's answer is kept and read whoever holds the lease."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("fetch")
    first, first_outcome = _in_thread(cuzk, me)
    assert arrived.wait(20)
    conn.execute("update account_cuzk_logins set run_beat_at = now() - interval '1 hour' where account_id = %s", (me,))
    with pytest.raises(prices.InProgress) as cooling:
        _buy(cuzk, me, resume=_id(conn, me))
    assert cooling.value.why == "fetching" and cuzk.fetched == []
    go.set()
    first.join(20)
    assert first_outcome == [None], "the old run's fetch answered: its file and deeds are stored without the lease"
    assert cuzk.fetched == [9001] and _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_two_fetches_of_one_report_long_apart_store_its_deeds_once(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("fetch")
    first, first_outcome = _in_thread(cuzk, me)
    assert arrived.wait(20)
    _later(conn, me)  # the first fetch has been "in flight" for an hour
    _buy(cuzk, me, resume=_id(conn, me))
    go.set()
    first.join(20)
    assert first_outcome == [None] and cuzk.fetched == [9001, 9001]
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_one_passing_service_error_does_not_end_a_possibly_paid_purchase(conn, monkeypatch):
    """The second review's M1: only error 304 says a report is gone."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(cuzk, me)
    _later(conn, me)
    cuzk.raises["poll"] = [OTHER_ERROR]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me, resume=_id(conn, me))
    assert _rows(conn, me) == [("ordered", 9001, None, False, True)], "nothing about the purchase changed"
    _buy(cuzk, me, resume=_id(conn, me))
    assert _rows(conn, me) == [DONE]


def test_a_possibly_charged_report_cuzk_no_longer_serves_ends_truthfully_and_can_be_bought_anew(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(cuzk, me)
    _later(conn, me)
    cuzk.raises["poll"] = [GONE]
    with pytest.raises(prices.ReportGone) as gone:
        _buy(cuzk, me, resume=_id(conn, me))
    assert gone.value.possibly_charged is True
    assert _rows(conn, me) == [("paid_unread", 9001, None, False, True)]
    shown = prices.state(conn, me, BUILDING.code)["purchase"]
    assert shown["status"] == "paid_unread" and shown["file_kept"] is False and shown["gone"] is True
    sent = cuzk.calls
    with pytest.raises(prices.NotResumable):
        _buy(cuzk, me, resume=_id(conn, me))  # ČÚZK said it is deleted: there is nothing to try again
    with pytest.raises(prices.AlreadyBought):
        _buy(cuzk, me)
    assert cuzk.calls == sent
    _buy(cuzk, me, repeat=True)
    assert [r[0] for r in _rows(conn, me)] == ["paid_unread", "done"]


def test_a_paid_purchase_with_no_file_is_fetched_again_for_free_while_cuzk_serves_it(conn, monkeypatch):
    """The second review's M2: `paid_unread` with nothing kept is not a dead end."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(cuzk, me)
    _later(conn, me)
    first = _id(conn, me)
    prices.cancel(me, 7, first, opener=_open)  # given up: paid, perhaps, and nothing kept
    assert _rows(conn, me) == [("paid_unread", 9001, None, False, True)]
    assert prices.state(conn, me, BUILDING.code)["purchase"]["gone"] is False
    cuzk.ready_state = "zaúčtován"
    _buy(cuzk, me, resume=first)
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001]
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_a_report_never_fetched_that_cuzk_no_longer_serves_ends_uncharged(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)
    cuzk.raises["poll"] = [GONE]
    with pytest.raises(prices.ReportGone) as gone:
        _buy(cuzk, me, resume=_id(conn, me))
    assert gone.value.possibly_charged is False
    assert _rows(conn, me) == [("failed", 9001, None, False, False)] and cuzk.fetched == []
    _buy(cuzk, me)  # no `repeat`: nothing was ever paid
    assert len(cuzk.generated) == 2 and cuzk.fetched == [9002]


def test_a_report_in_an_error_state_does_not_wait_for_ever(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    cuzk.waiting_state = "chyba při vytváření"
    with pytest.raises(prices.ReportGone) as gone:
        _buy(cuzk, me)
    assert gone.value.possibly_charged is False and _rows(conn, me) == [("failed", 9001, None, False, False)]


@pytest.mark.parametrize("stated", [60, None])
def test_a_report_priced_otherwise_than_acknowledged_is_not_fetched(conn, monkeypatch, stated):
    cuzk, me = _Cuzk(monkeypatch, price=stated), _account(conn)
    with pytest.raises(prices.PriceChanged) as changed:
        _buy(cuzk, me)
    assert changed.value.stated_czk == stated and cuzk.fetched == []
    assert _rows(conn, me) == [("failed", 9001, None, False, False)]


def test_a_fetched_report_that_cannot_be_read_is_kept_and_has_two_ways_out(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, document=b"<not a report"), _account(conn)
    with pytest.raises(prices.PaidUnread):
        _buy(cuzk, me)
    assert _rows(conn, me) == [("paid_unread", 9001, 50, True, True)]
    kept = conn.execute("select raw_report from cadastre_price_purchases where account_id = %s", (me,)).fetchone()[0]
    assert bytes(kept) != b"<not a report" and cuzk_login.unseal(kept) == b"<not a report"
    sent = cuzk.calls
    with pytest.raises(prices.PaidUnread):
        _buy(cuzk, me, resume=_id(conn, me))  # a free re-read: it still cannot be read
    with pytest.raises(prices.AlreadyBought):
        _buy(cuzk, me)
    assert cuzk.calls == sent and len(_checks(conn, me)) == 4, "nothing sent, no budget used"
    cuzk.document = REPORT
    _buy(cuzk, me, repeat=True)  # the confirmed new purchase is the other way out
    assert [r[0] for r in _rows(conn, me)] == ["paid_unread", "done"]


def test_a_kept_report_is_read_later_without_a_new_purchase_and_without_a_usable_login(conn, monkeypatch):
    """The parser is fixed after the fact: asking again stores the deeds from the kept file, sending nothing."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    real_parse = prices.cadastre_price_report.parse

    def broken(document: bytes) -> Any:
        raise ValueError("a field this parser does not know")

    monkeypatch.setattr(prices.cadastre_price_report, "parse", broken)
    with pytest.raises(prices.PaidUnread):
        _buy(cuzk, me)
    monkeypatch.setattr(prices.cadastre_price_report, "parse", real_parse)
    conn.execute("update account_cuzk_logins set refused_at = now() where account_id = %s", (me,))
    sent = cuzk.calls
    _buy(cuzk, me, resume=_id(conn, me))
    assert cuzk.calls == sent and cuzk.fetched == [9001], "nothing sent to ČÚZK for the re-read"
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_a_kept_file_whose_key_is_gone_is_fetched_again_for_free(conn, monkeypatch):
    from cryptography.fernet import Fernet

    cuzk, me = _Cuzk(monkeypatch, document=b"<not a report"), _account(conn)
    with pytest.raises(prices.PaidUnread):
        _buy(cuzk, me)
    stored = conn.execute("select username from account_cuzk_logins where account_id = %s", (me,)).fetchone()[0]
    monkeypatch.setenv(cuzk_login.KEY_ENV, Fernet.generate_key().decode())  # the key was rotated
    with pytest.raises(cuzk_login.Unreadable):
        _buy(cuzk, me, resume=_id(conn, me))  # the stored login went with the key: it must be entered again
    assert _lease_free(conn, me)
    cuzk_login.save(conn, me, stored, "x", datetime.datetime.now(datetime.UTC))  # the login, under the new key
    # The kept copy cannot be decrypted any more, but ČÚZK serves a fetched report again without charging: the
    # free way out is a re-fetch, never a second purchase.
    cuzk.document, cuzk.ready_state = REPORT, "zaúčtován"
    _buy(cuzk, me, resume=_id(conn, me))
    assert len(cuzk.generated) == 1 and cuzk.fetched == [9001, 9001]
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


def test_a_fetched_file_is_kept_even_if_the_purchase_was_closed_meanwhile(conn, monkeypatch):
    """The second review's B1 as a property of the write itself: once ČÚZK has answered the charging fetch, the
    file and its cost are stored whatever the row says, and the purchase reads as paid."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.hook["fetch"] = lambda: conn.execute(
        "update cadastre_price_purchases set status = 'failed', finished_at = now() where account_id = %s", (me,))
    _buy(cuzk, me)
    assert _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


# --- the stored login -------------------------------------------------------------------------------------------

def test_a_refused_login_fails_the_request_is_ledgered_and_is_not_used_again(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["find"] = [REFUSED]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert _rows(conn, me) == [("failed", None, None, False, False)] and _checks(conn, me) == ["refused"]
    assert cuzk_login.state(conn, me)["refused"]
    sent = cuzk.calls
    for kw in ({}, {"property_id": 8}):
        with pytest.raises(prices.LoginRefused):
            _buy(cuzk, me, **kw)
    assert cuzk.calls == sent and _checks(conn, me) == ["refused"], "nothing sent, nothing more spent"
    cuzk_login.save(conn, me, "firma01", "y", datetime.datetime.now(datetime.UTC))
    assert not cuzk_login.state(conn, me)["refused"]
    _buy(cuzk, me)
    assert _rows(conn, me)[-1][0] == "done"


def test_a_refusal_later_in_the_run_is_ledgered_and_ends_the_run_at_once(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch, ready_after=5), _account(conn)
    cuzk.raises["poll"] = [REFUSED]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert _checks(conn, me) == ["accepted", "accepted", "refused"], "each call its own row: two answered, one refused"
    assert cuzk_login.state(conn, me)["refused"] and cuzk.fetched == [] and cuzk.polls == 0
    assert _rows(conn, me) == [("ordered", 9001, None, False, False)], "the order stands for after the re-save"


def test_a_fetch_cuzk_refused_outright_charged_nothing_and_does_not_read_as_sent(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [REFUSED]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert _rows(conn, me) == [("ordered", 9001, None, False, False)]
    assert _checks(conn, me) == ["accepted"] * 3 + ["refused"]
    assert cuzk_login.state(conn, me)["refused"] and cuzk.fetched == []


def test_a_run_stops_before_its_next_call_once_its_login_is_refused_resaved_or_removed(conn, monkeypatch):
    """The second review's B2: the login is looked at again before EVERY call, not once a request."""
    for change in (
        "update account_cuzk_logins set refused_at = now() where account_id = %s",
        "update account_cuzk_logins set updated_at = now() + interval '1 second' where account_id = %s",
        "delete from account_cuzk_logins where account_id = %s",
    ):
        cuzk, me = _Cuzk(monkeypatch), _account(conn)
        cuzk.hook["generate"] = lambda change=change, me=me: conn.execute(change, (me,))
        sent_before = cuzk.calls
        with pytest.raises(prices.InProgress):
            _buy(cuzk, me)
        assert cuzk.calls - sent_before == 2 and cuzk.fetched == [], f"no poll and no fetch after: {change}"


def test_a_call_is_not_admitted_when_it_could_be_the_usernames_third_unaccepted_check(conn, monkeypatch):
    """Accounts sharing one ČÚZK username. Every call of a run is admitted like a save's check, so with two other
    checks on the username in flight this run's next call is not sent."""
    me, other, third = _account(conn, "sdilene"), _account(conn, "sdilene"), _account(conn, "sdilene")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "sdilene"), "accepted")
    cuzk_login.admit_check(conn, other, "sdilene")
    mine = cuzk_login.admit_check(conn, me, "sdilene", in_run=True)  # one other in flight: this makes two
    cuzk_login.settle_check(conn, mine, "accepted")
    cuzk_login.admit_check(conn, third, "sdilene")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, me, "sdilene", in_run=True)  # two others in flight: this would be the third


def test_a_runs_further_calls_do_not_count_toward_the_accounts_hourly_total(conn, monkeypatch):
    me = _account(conn)
    for _ in range(cuzk_login.ACCOUNT_TOTAL_BUDGET + 5):
        cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma", in_run=True), "accepted")
    for _ in range(cuzk_login.ACCOUNT_TOTAL_BUDGET):
        cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "accepted")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, me, "firma")  # the total caps runs and saves, not a run's calls


def test_a_run_whose_next_call_is_not_admitted_stops_and_keeps_its_order(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    real = cuzk_login.admit_check

    def admit(c: Any, account: Any, username: str, *, in_run: bool = False) -> int:
        if in_run and cuzk.calls >= 2:
            raise cuzk_login.Throttled("username", 3600)
        return real(c, account, username, in_run=in_run)

    monkeypatch.setattr(cuzk_login, "admit_check", admit)
    with pytest.raises(prices.InProgress) as stopped:
        _buy(cuzk, me)
    assert stopped.value.why == "login"
    assert cuzk.calls == 2 and cuzk.fetched == [] and cuzk.polls == 0
    assert _rows(conn, me) == [("ordered", 9001, None, False, False)] and _checks(conn, me) == ["accepted"] * 2


def test_a_run_that_ends_before_its_first_call_leaves_no_check_behind(conn, monkeypatch):
    """The third review's M2, the no-send half: a check that was admitted and never sent is withdrawn."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    monkeypatch.setattr(cuzk_login, "renew", lambda c, a, t, held: False)
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)
    assert cuzk.calls == 0 and _checks(conn, me) == [] and _lease_free(conn, me)


def test_a_dead_requests_check_does_not_throttle_the_username_for_a_day(conn, monkeypatch):
    """The third review's M2: an API deploy kills a run mid-call and its row stays in flight for ever. Once no
    call could still be alive, an accepted check resets it like a call that got no answer."""
    me = _account(conn)
    stranded = cuzk_login.admit_check(conn, me, "firma")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "accepted")
    one_more = cuzk_login.admit_check(conn, me, "firma")  # the stranded row still counts: this is the second
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, me, "firma")
    cuzk_login.settle_check(conn, one_more, "accepted")
    conn.execute("update account_cuzk_login_checks set admitted_at = admitted_at - interval '10 minutes' "
                 "where id = %s", (stranded,))
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "accepted")  # resets the dead row
    a, b = cuzk_login.admit_check(conn, me, "firma"), cuzk_login.admit_check(conn, me, "firma")
    assert a and b, "two fresh checks are admitted again"


def test_a_call_that_got_no_answer_is_not_reset_by_an_accepted_one_moments_later(conn, monkeypatch):
    """The fourth review's major. The moment we gave up is ours, not ČÚZK's: a wrong-password save that hangs
    before it is sent can reach ČÚZK AFTER a correct login was accepted. So an accepted check resets a no-answer
    only once it is admitted well after that call was."""
    me = _account(conn)
    hung = cuzk_login.admit_check(conn, me, "firma")
    cuzk_login.settle_check(conn, hung, "no_answer")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "accepted")  # one second later
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "refused")
    with pytest.raises(cuzk_login.Throttled):
        cuzk_login.admit_check(conn, me, "firma")  # the no-answer still counts: this would be the third

    other = _account(conn)
    old = cuzk_login.admit_check(conn, other, "jina")
    cuzk_login.settle_check(conn, old, "no_answer")
    conn.execute("update account_cuzk_login_checks set admitted_at = admitted_at - interval '10 minutes', "
                 "answered_at = answered_at - interval '10 minutes' where id = %s", (old,))
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, other, "jina"), "accepted")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, other, "jina"), "refused")
    assert cuzk_login.admit_check(conn, other, "jina"), "minutes later the accepted check did reset it"


def test_a_refused_check_is_reset_by_an_accepted_one_admitted_after_cuzk_answered_it(conn, monkeypatch):
    me = _account(conn)
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "refused")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "accepted")
    cuzk_login.settle_check(conn, cuzk_login.admit_check(conn, me, "firma"), "refused")
    assert cuzk_login.admit_check(conn, me, "firma"), "ČÚZK answered the first: its own counter was reset too"


def test_an_accepted_answer_is_never_wiped_by_the_runs_later_calls(conn, monkeypatch):
    """The third review's M3: two ordinary timeouts with an accepted answer between them are one unaccepted
    check, not two."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["poll"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    # Minutes pass: a call that got no answer is resettable only once ČÚZK must have handled it.
    conn.execute("update account_cuzk_login_checks set admitted_at = admitted_at - interval '10 minutes', "
                 "answered_at = answered_at - interval '10 minutes' where account_id = %s", (me,))
    cuzk.ready_after = 1  # the resume's first poll is answered (accepted), its fetch then times out
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me, resume=_id(conn, me))
    cuzk.raises["poll"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me, resume=_id(conn, me))
    assert _checks(conn, me) == ["accepted", "accepted", "no_answer", "accepted", "no_answer"]
    _buy(cuzk, me, resume=_id(conn, me))  # not throttled: the accepted poll reset the first timeout
    assert _rows(conn, me) == [DONE]


def test_marking_a_login_refused_marks_only_the_login_that_was_sent(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["find"] = [REFUSED]

    def resave() -> None:  # the owner saves a new login while the old one is on its way to ČÚZK
        conn.execute("update account_cuzk_logins set updated_at = now() + interval '1 second' "
                     "where account_id = %s", (me,))

    cuzk.hook["find"] = resave
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert not cuzk_login.state(conn, me)["refused"], "the new login was never refused"


def test_an_error_cuzk_gives_after_accepting_the_login_does_not_count_against_it(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    for _ in range(cuzk_login.USERNAME_BUDGET + 1):
        cuzk.raises["find"] = [OTHER_ERROR]
        with pytest.raises(wsdp_client.WsdpError):
            _buy(cuzk, me)
    assert _checks(conn, me) == ["accepted"] * (cuzk_login.USERNAME_BUDGET + 1)
    assert not cuzk_login.state(conn, me)["refused"]


def test_an_answer_that_never_came_is_not_a_refusal(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["find"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert _checks(conn, me) == ["no_answer"] and not cuzk_login.state(conn, me)["refused"]


def test_a_login_past_its_budget_sends_nothing_and_writes_no_purchase(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    for _ in range(cuzk_login.USERNAME_BUDGET):
        cuzk.raises["find"] = [TIMEOUT]
        with pytest.raises(wsdp_client.WsdpError):
            _buy(cuzk, me)
    sent, rows = cuzk.calls, len(_rows(conn, me))
    with pytest.raises(cuzk_login.Throttled):
        _buy(cuzk, me)
    assert cuzk.calls == sent and len(_rows(conn, me)) == rows and _lease_free(conn, me)


def test_every_call_of_a_run_is_in_flight_in_the_ledger_until_cuzk_answers_it(conn, monkeypatch):
    """The second review's B2, the ledger half: while a call is on its way it counts as unaccepted, so nobody is
    admitted beside a call that may be refused."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    seen: dict[str, Any] = {}
    for call in ("find", "generate", "poll", "fetch"):
        cuzk.hook[call] = lambda call=call: seen.__setitem__(call, _checks(conn, me))
    _buy(cuzk, me)
    assert seen == {"find": ["pending"], "generate": ["accepted", "pending"],
                    "poll": ["accepted", "accepted", "pending"], "fetch": ["accepted"] * 3 + ["pending"]}
    assert _checks(conn, me) == ["accepted"] * 4


def test_a_later_call_that_gets_no_answer_is_ledgered_as_such(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["poll"] = [TIMEOUT]
    with pytest.raises(wsdp_client.WsdpError):
        _buy(cuzk, me)
    assert _checks(conn, me) == ["accepted", "accepted", "no_answer"]


def test_the_fetched_file_is_kept_before_the_ledger_is_settled(conn, monkeypatch):
    """The third review's b: a failure settling the ledger must not cost a paid document."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    real = cuzk_login.settle_check

    def settle(c: Any, check_id: int, outcome: str) -> None:
        if cuzk.fetched:
            assert _rows(conn, me)[0][3], "the file is already kept"
            raise RuntimeError("the ledger write failed")
        real(c, check_id, outcome)

    monkeypatch.setattr(cuzk_login, "settle_check", settle)
    with pytest.raises(RuntimeError):
        _buy(cuzk, me)
    assert _rows(conn, me) == [("ordered", 9001, 50, True, True)]
    monkeypatch.setattr(cuzk_login, "settle_check", real)
    sent = cuzk.calls
    _buy(cuzk, me, resume=_id(conn, me))
    assert cuzk.calls == sent and _rows(conn, me) == [DONE] and _deeds(conn, me) == PARSED


# --- giving a purchase up ---------------------------------------------------------------------------------------

def _cancel(account, purchase_id: int, property_id: int = 7) -> None:
    prices.cancel(account, property_id, purchase_id, opener=_open)


def test_a_purchase_in_flight_that_was_never_fetched_is_given_up_uncharged_and_sends_nothing(conn, monkeypatch):
    """The exit for an order that can no longer be continued (a permanent error on its report)."""
    cuzk, me = _Cuzk(monkeypatch, ready_after=1), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)
    sent, checks = cuzk.calls, _checks(conn, me)
    _cancel(me, _id(conn, me))
    assert cuzk.calls == sent and _checks(conn, me) == checks and _lease_free(conn, me)
    assert _rows(conn, me) == [("failed", 9001, None, False, False)]
    cuzk.ready_after = 0
    _buy(cuzk, me)  # nothing was paid, so no `repeat`
    assert [r[0] for r in _rows(conn, me)] == ["failed", "done"]


def test_a_purchase_whose_fetch_was_sent_is_given_up_as_possibly_paid(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    cuzk.raises["fetch"] = [TIMEOUT]
    with pytest.raises(prices.ChargeUncertain):
        _buy(cuzk, me)
    with pytest.raises(prices.InProgress):
        _cancel(me, _id(conn, me))  # the fetch may still be being served: nothing is decided beside it
    _later(conn, me)
    _cancel(me, _id(conn, me))
    assert _rows(conn, me) == [("paid_unread", 9001, None, False, True)]
    with pytest.raises(prices.AlreadyBought):
        _buy(cuzk, me)


def test_only_a_purchase_in_flight_of_this_account_and_building_can_be_given_up(conn, monkeypatch):
    cuzk, me, other = _Cuzk(monkeypatch), _account(conn), _account(conn)
    _buy(cuzk, me)
    done = _id(conn, me)
    for who, purchase, prop in ((me, done, 7), (other, done, 7), (me, 10**9, 7)):
        with pytest.raises(prices.NotResumable):
            _cancel(who, purchase, prop)
    assert _rows(conn, me) == [DONE]
    with pytest.raises(prices.InProgress):
        cuzk.ready_after = 10
        _buy(cuzk, me, repeat=True)
    with pytest.raises(prices.NotResumable):
        _cancel(me, _id(conn, me), 8)


def test_nothing_is_given_up_beside_a_live_run(conn, monkeypatch):
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("fetch")
    first, first_outcome = _in_thread(cuzk, me)
    assert arrived.wait(20)
    with pytest.raises(prices.InProgress):
        _cancel(me, _id(conn, me))
    go.set()
    first.join(20)
    assert first_outcome == [None] and _rows(conn, me) == [DONE]


# --- the login's own row under a live run ------------------------------------------------------------------------

def test_the_login_cannot_be_removed_from_under_a_live_run(conn, monkeypatch):
    """The lease lives on the login's row: removing and re-saving it would let a second run start beside one
    whose fetch is still in flight."""
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    arrived, go = cuzk.hold("fetch")
    first, first_outcome = _in_thread(cuzk, me)
    assert arrived.wait(20)
    with pytest.raises(cuzk_login.LeaseHeld):
        cuzk_login.remove(conn, me)
    with pytest.raises(cuzk_login.LeaseHeld):
        cuzk_login.take_lease(conn, me, uuid.uuid4())
    go.set()
    first.join(20)
    assert first_outcome == [None] and _rows(conn, me) == [DONE]
    cuzk_login.remove(conn, me)
    assert cuzk_login.state(conn, me)["set"] is False


# --- a report with nothing in it ---------------------------------------------------------------------------------

def test_a_report_that_held_no_deed_keeps_its_file(conn, monkeypatch):
    """'The cadastre knows no price' and 'we read none' look the same; the file is the only proof of which."""
    empty = b"<CenoveUdajeDleNemovitosti></CenoveUdajeDleNemovitosti>"
    cuzk, me = _Cuzk(monkeypatch, document=empty), _account(conn)
    _buy(cuzk, me)
    assert _rows(conn, me) == [("done", 9001, 50, True, True)] and _deeds(conn, me) == 0
    kept = conn.execute("select raw_report from cadastre_price_purchases where account_id = %s", (me,)).fetchone()[0]
    assert cuzk_login.unseal(kept) == empty


def test_giving_up_and_reading_a_kept_file_need_no_stored_login(conn, monkeypatch):
    """The third review's f: neither sends anything to ČÚZK."""
    cuzk, me = _Cuzk(monkeypatch, ready_after=1, document=b"<not a report"), _account(conn)
    with pytest.raises(prices.InProgress):
        _buy(cuzk, me)
    cuzk_login.remove(conn, me)
    _cancel(me, _id(conn, me))
    assert _rows(conn, me) == [("failed", 9001, None, False, False)]

    other = _account(conn)
    cuzk.polls = 10
    with pytest.raises(prices.PaidUnread):
        _buy(cuzk, other)
    cuzk_login.remove(conn, other)
    monkeypatch.setattr(prices.cadastre_price_report, "parse", lambda document: [])
    sent = cuzk.calls
    _buy(cuzk, other, resume=_id(conn, other))
    assert cuzk.calls == sent and _rows(conn, other)[0][0] == "done"
    with pytest.raises(prices.NoLogin):
        _buy(cuzk, other, repeat=True)


def test_a_report_read_as_no_deed_can_be_read_again_and_does_not_hide_earlier_deeds(conn, monkeypatch):
    """The third review's h."""
    empty = b"<CenoveUdajeDleNemovitosti></CenoveUdajeDleNemovitosti>"
    cuzk, me = _Cuzk(monkeypatch), _account(conn)
    _buy(cuzk, me)
    cuzk.document = empty
    _buy(cuzk, me, repeat=True)
    shown = prices.state(conn, me, BUILDING.code)
    assert shown["purchase"]["file_kept"] is True and len(shown["deeds"]) == PARSED, "the earlier deeds still show"
    cuzk_copy = conn.execute("select raw_report from cadastre_price_purchases where id = %s", (_id(conn, me),))
    assert cuzk_login.unseal(cuzk_copy.fetchone()[0]) == empty
    monkeypatch.setattr(prices.cadastre_price_report, "parse", lambda document: prices.cadastre_price_report.Deed and [])
    sent = cuzk.calls
    _buy(cuzk, me, resume=_id(conn, me))  # read again, free; still nothing in it
    assert cuzk.calls == sent and [r[0] for r in _rows(conn, me)] == ["done", "done"]

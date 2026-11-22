"""The price routes, hermetic: the price must be acknowledged before anything starts, each refusal's status, the
caller's own account only, and the answer's shape. The purchase itself runs on a real schema in
tests/test_cadastre_prices_live.py."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import cadastre_prices, cuzk_login, tenant_pool, wsdp_client  # noqa: E402
from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from tests.api._script_conn import ScriptConn  # noqa: E402

_ME = uuid.UUID("22222222-2222-2222-2222-222222222222")
_OTHER = uuid.UUID("44444444-4444-4444-4444-444444444444")
URL = "/properties/7/building/prices"
STATE = {"price_czk": 50, "purchase": {"status": "done", "cost_czk": 50, "object_count": 3,
                                       "requested_at": "2026-11-22T10:00:00+00:00", "finished_at": None},
         "deeds": [{"proceeding": "V-1/2024-101", "kind": "Smlouva kupní", "price": 6450000.0, "currency": "CZK",
                    "signed_on": "2024-01-30", "effective_on": "2024-02-02", "entered_on": "2024-02-28",
                    "properties": [{"kind": "jednotka", "iskn_id": 1, "label": "565/14"}]}]}


@contextlib.contextmanager
def _tenant_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
    yield ScriptConn([])


repeats: list[bool] = []


@pytest.fixture()
def bought(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, int]]:
    calls: list[tuple[uuid.UUID, int]] = []
    def buy(account: uuid.UUID, pid: int, **kw: Any) -> None:
        calls.append((account, pid))
        repeats.append(kw["repeat"])

    monkeypatch.setattr(cadastre_prices, "buy", buy)
    return calls


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, bought: list) -> Any:
    monkeypatch.setattr(deps, "open_background_conn", lambda: contextlib.nullcontext(ScriptConn([])))
    monkeypatch.setattr(cadastre_prices, "building_of", lambda c, pid: cadastre_prices.Building(22948236, 470801805))
    monkeypatch.setattr(cadastre_prices, "state", lambda c, account, code: dict(STATE))
    monkeypatch.setattr(cuzk_login, "state", lambda c, account: {"set": True, "refused": False})
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: ScriptConn([])
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": "u1"}
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda c, claims: _ME)
    monkeypatch.setattr(tenant_pool, "tenant_transaction", _tenant_transaction)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_the_state_is_the_callers_own(client: Any) -> None:
    body = client.get(URL).json()
    assert body["available"] is True and body["login_set"] is True and body["price_czk"] == 50
    assert body["deeds"][0]["properties"][0]["label"] == "565/14"


def test_a_property_without_a_building_offers_nothing(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def none(conn: Any, pid: int) -> Any:
        raise cadastre_prices.NoBuilding(pid)

    monkeypatch.setattr(cadastre_prices, "building_of", none)
    assert client.get(URL).json() == {"available": False, "price_czk": 50, "purchase": None, "deeds": [],
                                      "login_set": True, "login_refused": False}


@pytest.mark.parametrize("body", [{}, {"cost_czk": 0}, {"cost_czk": "50"}, [50]])
def test_nothing_is_bought_without_the_price_acknowledged(client: Any, bought: list, body: Any) -> None:
    resp = client.post(URL, json=body)
    assert resp.status_code == 409 and resp.json()["detail"]["price_czk"] == 50
    assert bought == []


def test_an_acknowledged_request_buys_for_the_caller_and_answers_the_state(client: Any, bought: list) -> None:
    resp = client.post(URL, json={"cost_czk": 50})
    assert resp.status_code == 200 and resp.json()["purchase"]["status"] == "done"
    assert bought == [(_ME, 7)]


@pytest.mark.parametrize(("raised", "status"), [
    (cadastre_prices.NoProperty(7), 404), (cadastre_prices.NoBuilding(7), 422), (cadastre_prices.NoLogin("x"), 409),
    (cadastre_prices.LoginRefused("x"), 409), (cuzk_login.Unreadable("x"), 409),
    (cadastre_prices.TooManyUnits(340), 422), (cadastre_prices.ReportGone("x"), 409),
    (cadastre_prices.PriceChanged(60), 409), (cadastre_prices.ChargeUncertain("x"), 502),
    (cadastre_prices.PaidUnread("x"), 502), (wsdp_client.WsdpError("refused", 500), 422),
    (wsdp_client.WsdpError("timeout"), 502), (wsdp_client.WsdpError("misconfigured"), 502),
])
def test_each_refusal_has_its_own_answer(client: Any, monkeypatch: pytest.MonkeyPatch, raised: Exception,
                                         status: int) -> None:
    def fail(account: Any, pid: int, **kw: Any) -> None:
        raise raised

    monkeypatch.setattr(cadastre_prices, "buy", fail)
    assert client.post(URL, json={"cost_czk": 50}).status_code == status


@pytest.mark.parametrize(("body", "repeat"), [
    ({"cost_czk": 50}, False), ({"cost_czk": 50, "repeat": True}, True), ({"cost_czk": 50, "repeat": "yes"}, False),
])
def test_a_repeat_is_only_the_explicit_true(client: Any, bought: list, body: dict, repeat: bool) -> None:
    repeats.clear()
    assert client.post(URL, json=body).status_code == 200
    assert repeats == [repeat]


def test_a_building_already_bought_answers_the_price_and_the_date(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime

    def already(account: Any, pid: int, **kw: Any) -> None:
        raise cadastre_prices.AlreadyBought(datetime.datetime(2026, 11, 22, 10, 0, tzinfo=datetime.UTC))

    monkeypatch.setattr(cadastre_prices, "buy", already)
    resp = client.post(URL, json={"cost_czk": 50})
    detail = resp.json()["detail"]
    assert resp.status_code == 409 and detail["reason"] == "already_bought"
    assert detail["price_czk"] == 50 and detail["bought_at"].startswith("2026-11-22T10:00")


def test_a_login_past_its_budget_is_a_429_that_says_nothing_was_charged(client: Any,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    def spent(account: Any, pid: int, **kw: Any) -> None:
        raise cuzk_login.Throttled("username", 86_400)

    monkeypatch.setattr(cadastre_prices, "buy", spent)
    resp = client.post(URL, json={"cost_czk": 50})
    detail = resp.json()["detail"]
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "86400"
    assert "nothing was charged" in detail and "not locked" in detail and "about 24 h" in detail


def test_the_account_cap_names_its_own_cause_in_minutes(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def spent(account: Any, pid: int, **kw: Any) -> None:
        raise cuzk_login.Throttled("account", 1500)

    monkeypatch.setattr(cadastre_prices, "buy", spent)
    detail = client.post(URL, json={"cost_czk": 50}).json()["detail"]
    assert "too often in the last hour" in detail and "about 25 min" in detail


def test_a_purchase_in_flight_answers_its_state_not_an_error(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 202 with a `detail` body replaced the card's state in the SPA; the state itself says pending/ordered."""
    def waiting(account: Any, pid: int, **kw: Any) -> None:
        raise cadastre_prices.InProgress("vytváří se")

    monkeypatch.setattr(cadastre_prices, "buy", waiting)
    monkeypatch.setattr(cadastre_prices, "state", lambda c, a, code: {
        **STATE, "purchase": {**STATE["purchase"], "status": "ordered"}, "deeds": []})
    resp = client.post(URL, json={"cost_czk": 50})
    assert resp.status_code == 200 and resp.json()["purchase"]["status"] == "ordered"
    assert resp.json()["available"] is True and resp.json()["login_refused"] is False


@pytest.mark.parametrize(("raised", "says"), [
    (cadastre_prices.ChargeUncertain("x"), "MAY have been charged"),
    (cadastre_prices.PaidUnread("x"), "charged for it"),
    (cadastre_prices.ReportGone("x"), "nothing was charged"),
    (wsdp_client.WsdpError("timeout"), "charged nothing"),
])
def test_an_error_says_truthfully_whether_money_moved(client: Any, monkeypatch: pytest.MonkeyPatch,
                                                      raised: Exception, says: str) -> None:
    def fail(account: Any, pid: int, **kw: Any) -> None:
        raise raised

    monkeypatch.setattr(cadastre_prices, "buy", fail)
    assert says in client.post(URL, json={"cost_czk": 50}).json()["detail"]


def test_a_changed_price_is_answered_with_the_stated_one(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def changed(account: Any, pid: int, **kw: Any) -> None:
        raise cadastre_prices.PriceChanged(60)

    monkeypatch.setattr(cadastre_prices, "buy", changed)
    detail = client.post(URL, json={"cost_czk": 50}).json()["detail"]
    assert detail["reason"] == "price_changed" and detail["price_czk"] == 60


@pytest.mark.parametrize("method", ["get", "post"])
def test_prices_are_never_bought_or_read_for_another_account(client: Any, bought: list, method: str) -> None:
    kwargs = {"json": {"cost_czk": 50}} if method == "post" else {}
    assert getattr(client, method)(URL, headers={"X-Act-As": str(_OTHER)}, **kwargs).status_code == 403
    assert bought == []

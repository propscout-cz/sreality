"""The price routes, hermetic: the price must be acknowledged before anything starts, what each body asks for, each
refusal's status and what it says about money, the caller's own account only, and the answer's shape. The purchase
itself runs on a real schema in tests/test_cadastre_prices_live.py."""

from __future__ import annotations

import contextlib
import datetime
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
PAY = {"cost_czk": 50}
STATE = {"price_czk": 50, "running": False,
         "purchase": {"id": 31, "status": "done", "cost_czk": 50, "object_count": 3, "fetch_sent": True,
                      "file_kept": False, "requested_at": "2026-11-22T10:00:00+00:00", "finished_at": None},
         "deeds": [{"proceeding": "V-1/2024-101", "kind": "Smlouva kupní", "price": 6450000.0, "currency": "CZK",
                    "signed_on": "2024-01-30", "effective_on": "2024-02-02", "entered_on": "2024-02-28",
                    "properties": [{"kind": "jednotka", "iskn_id": 1, "label": "565/14"}]}]}


@contextlib.contextmanager
def _tenant_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
    yield ScriptConn([])


@pytest.fixture()
def bought(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def buy(account: uuid.UUID, pid: int, **kw: Any) -> None:
        calls.append({"account": account, "property": pid, "repeat": kw["repeat"], "resume": kw["resume"]})

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


def _raising(monkeypatch: pytest.MonkeyPatch, raised: Exception) -> None:
    def fail(account: Any, pid: int, **kw: Any) -> None:
        raise raised

    monkeypatch.setattr(cadastre_prices, "buy", fail)


def test_the_state_is_the_callers_own_and_names_its_purchase(client: Any) -> None:
    body = client.get(URL).json()
    assert body["available"] is True and body["login_set"] is True and body["login_refused"] is False
    assert body["price_czk"] == 50 and body["running"] is False and body["purchase"]["id"] == 31
    assert body["deeds"][0]["properties"][0]["label"] == "565/14"


def test_reading_the_state_never_buys(client: Any, bought: list) -> None:
    assert client.get(URL).status_code == 200 and bought == []


def test_a_property_without_a_building_offers_nothing(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def none(conn: Any, pid: int) -> Any:
        raise cadastre_prices.NoBuilding(pid)

    monkeypatch.setattr(cadastre_prices, "building_of", none)
    assert client.get(URL).json() == {"available": False, "price_czk": 50, "running": False, "purchase": None,
                                      "deeds": [], "login_set": True, "login_refused": False}


@pytest.mark.parametrize("body", [{}, {"cost_czk": 0}, {"cost_czk": "50"}, {"cost_czk": True}, [50],
                                  {"resume": 31}, {"repeat": True}])
def test_nothing_starts_without_the_price_acknowledged(client: Any, bought: list, body: Any) -> None:
    resp = client.post(URL, json=body)
    assert resp.status_code == 409 and resp.json()["detail"]["price_czk"] == 50
    assert bought == []


@pytest.mark.parametrize(("body", "repeat", "resume"), [
    (PAY, False, None), ({**PAY, "repeat": True}, True, None), ({**PAY, "repeat": "yes"}, False, None),
    ({**PAY, "resume": 31}, False, 31),
])
def test_each_body_asks_for_one_thing(client: Any, bought: list, body: dict, repeat: bool, resume: Any) -> None:
    resp = client.post(URL, json=body)
    assert resp.status_code == 200 and resp.json()["purchase"]["status"] == "done"
    assert bought == [{"account": _ME, "property": 7, "repeat": repeat, "resume": resume}]


@pytest.mark.parametrize("body", [{**PAY, "resume": "31"}, {**PAY, "resume": True}, {**PAY, "resume": 31.5},
                                  {**PAY, "resume": 31, "repeat": True}])
def test_a_resume_names_exactly_one_purchase_and_is_never_also_a_repeat(client: Any, bought: list, body: dict) -> None:
    assert client.post(URL, json=body).status_code == 422 and bought == []


@pytest.mark.parametrize(("raised", "status"), [
    (cadastre_prices.NoProperty(7), 404), (cadastre_prices.NoBuilding(7), 422), (cadastre_prices.NoLogin("x"), 409),
    (cadastre_prices.LoginRefused("x"), 409), (cuzk_login.Unreadable("x"), 409),
    (cuzk_login.NotConfigured("x"), 503), (cadastre_prices.TooManyUnits(340), 422),
    (cadastre_prices.NotResumable("31"), 409), (cadastre_prices.ReportGone(False), 409),
    (cadastre_prices.ReportGone(True), 409), (cadastre_prices.PriceChanged(60), 409),
    (cadastre_prices.ChargeUncertain("x"), 502), (cadastre_prices.PaidUnread("x"), 502),
    (wsdp_client.WsdpError("refused", 500), 422), (wsdp_client.WsdpError("timeout"), 502),
    (wsdp_client.WsdpError("service", 505, authenticated=True), 502), (wsdp_client.WsdpError("misconfigured"), 502),
])
def test_each_refusal_has_its_own_answer(client: Any, monkeypatch: pytest.MonkeyPatch, raised: Exception,
                                         status: int) -> None:
    _raising(monkeypatch, raised)
    assert client.post(URL, json=PAY).status_code == status


def _text(client: Any) -> str:
    detail = client.post(URL, json=PAY).json()["detail"]
    return detail["message"] if isinstance(detail, dict) else detail


@pytest.mark.parametrize(("raised", "says", "never_says"), [
    (cadastre_prices.ChargeUncertain("x"), "MOHLA být zaúčtována", "nic nezaúčtoval"),
    (cadastre_prices.PaidUnread("x"), "vydal a zaúčtoval", "nebylo nic zaúčtováno"),
    (cadastre_prices.ReportGone(True), "mohla být zaúčtována", "nebylo nic zaúčtováno"),
    (cadastre_prices.ReportGone(False), "nebylo nic zaúčtováno", "mohla být"),
    (cadastre_prices.PriceChanged(60), "nic nebylo zaúčtováno", "mohla být"),
    (cadastre_prices.TooManyUnits(340), "Nic nebylo objednáno", "zaúčtována"),
    (cadastre_prices.NotResumable("31"), "Nic nebylo odesláno ani zaúčtováno", "mohla být"),
    (cadastre_prices.LoginRefused("x"), "nic nezaúčtoval", "mohla být"),
    (wsdp_client.WsdpError("refused", 500), "nic nezaúčtoval", "nic nebylo objednáno"),
    (wsdp_client.WsdpError("timeout"), "nic nezaúčtoval", "mohla být"),
])
def test_every_error_says_truthfully_whether_money_moved(client: Any, monkeypatch: pytest.MonkeyPatch,
                                                         raised: Exception, says: str, never_says: str) -> None:
    _raising(monkeypatch, raised)
    text = _text(client)
    assert says in text and never_says not in text


def test_every_text_a_user_can_be_shown_is_czech(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The card is Czech; an English sentence about money inside it was the second review's s5."""
    english = (" the ", " was ", " your ", " not ", "charged", "again", "ČÚZK did", "Enter")
    for raised in (cadastre_prices.NoBuilding(7), cadastre_prices.NoLogin("x"), cadastre_prices.LoginRefused("x"),
                   cuzk_login.Unreadable("x"), cuzk_login.NotConfigured("x"), cadastre_prices.TooManyUnits(1),
                   cadastre_prices.NotResumable("1"), cadastre_prices.ReportGone(True),
                   cadastre_prices.ReportGone(False), cadastre_prices.PriceChanged(None),
                   cadastre_prices.AlreadyBought(None), cadastre_prices.ChargeUncertain("x"),
                   cadastre_prices.PaidUnread("x"), cuzk_login.Throttled("username", 90_000),
                   cuzk_login.Throttled("account", 600), wsdp_client.WsdpError("refused"),
                   wsdp_client.WsdpError("timeout")):
        _raising(monkeypatch, raised)
        text = _text(client)
        assert not [word for word in english if word in text], (type(raised).__name__, text)
    assert "50 Kč" in client.post(URL, json={}).json()["detail"]["message"]


def test_a_building_already_paid_for_answers_the_price_and_the_date(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _raising(monkeypatch, cadastre_prices.AlreadyBought(datetime.datetime(2026, 11, 22, 10, 0, tzinfo=datetime.UTC)))
    detail = client.post(URL, json=PAY).json()["detail"]
    assert detail["reason"] == "already_bought" and detail["price_czk"] == 50
    assert detail["bought_at"].startswith("2026-11-22T10:00") and "dalších 50 Kč" in detail["message"]


@pytest.mark.parametrize(("stated", "says"), [(60, "cenu 60 Kč"), (None, "žádnou cenu")])
def test_a_changed_price_is_answered_with_the_stated_one(client: Any, monkeypatch: pytest.MonkeyPatch,
                                                         stated: Any, says: str) -> None:
    _raising(monkeypatch, cadastre_prices.PriceChanged(stated))
    detail = client.post(URL, json=PAY).json()["detail"]
    assert detail["reason"] == "price_changed" and detail["price_czk"] == stated and says in detail["message"]


def test_a_login_past_its_budget_is_a_429_that_names_its_cause(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _raising(monkeypatch, cuzk_login.Throttled("username", 86_400))
    resp = client.post(URL, json=PAY)
    detail = resp.json()["detail"]
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "86400"
    assert "nic nebylo zaúčtováno" in detail and "aby se váš účet u ČÚZK nezablokoval" in detail
    assert "asi za 24 h" in detail
    assert "přihlašovací jméno" in detail
    _raising(monkeypatch, cuzk_login.Throttled("account", 1500))
    detail = client.post(URL, json=PAY).json()["detail"]
    assert "v poslední hodině příliš často" in detail and "asi za 25 min" in detail


def test_a_request_that_changed_nothing_answers_the_state_as_it_stands(client: Any,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """Another run is live, or ČÚZK is still preparing: a 200 with the stored state, which the card renders. (A 202
    with a `detail` body replaced the card's state: the first review's M6.)"""
    _raising(monkeypatch, cadastre_prices.InProgress("vytváří se"))
    monkeypatch.setattr(cadastre_prices, "state", lambda c, a, code: {
        **STATE, "running": True, "purchase": {**STATE["purchase"], "status": "ordered"}, "deeds": []})
    resp = client.post(URL, json=PAY)
    body = resp.json()
    assert resp.status_code == 200 and body["purchase"]["status"] == "ordered" and body["running"] is True
    assert body["available"] is True and body["login_refused"] is False


def test_a_purchase_in_flight_is_given_up_by_name_and_needs_no_price(client: Any, bought: list,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    given_up: list[tuple[Any, int, int]] = []
    monkeypatch.setattr(cadastre_prices, "cancel",
                        lambda account, pid, purchase, **kw: given_up.append((account, pid, purchase)))
    assert client.post(URL, json={"cancel": 31}).status_code == 200
    assert given_up == [(_ME, 7, 31)] and bought == []


@pytest.mark.parametrize("body", [{"cancel": "31"}, {"cancel": True}, {"cancel": 31, "cost_czk": 50},
                                  {"cancel": 31, "resume": 31}])
def test_giving_up_is_never_mixed_with_another_request(client: Any, bought: list, body: dict,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    given_up: list[Any] = []
    monkeypatch.setattr(cadastre_prices, "cancel", lambda *a, **kw: given_up.append(a))
    assert client.post(URL, json=body).status_code == 422 and given_up == [] and bought == []


def test_giving_up_a_purchase_that_is_not_in_flight_is_refused(client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def ended(*a: Any, **kw: Any) -> None:
        raise cadastre_prices.NotResumable("31")

    monkeypatch.setattr(cadastre_prices, "cancel", ended)
    resp = client.post(URL, json={"cancel": 31})
    assert resp.status_code == 409 and resp.json()["detail"]["reason"] == "not_resumable"


@pytest.mark.parametrize("method", ["get", "post"])
def test_prices_are_never_bought_or_read_for_another_account(client: Any, bought: list, method: str) -> None:
    kwargs = {"json": PAY} if method == "post" else {}
    assert getattr(client, method)(URL, headers={"X-Act-As": str(_OTHER)}, **kwargs).status_code == 403
    assert bought == []

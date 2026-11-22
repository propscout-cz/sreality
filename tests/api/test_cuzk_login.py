"""An account's own ČÚZK login, hermetic: the state, a checked save, a refused or unanswered check that
stores nothing, the removal, the never-acts 403, and that no response or log line carries the password."""

from __future__ import annotations

import contextlib
import datetime
import logging
import uuid
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from cryptography.fernet import Fernet  # noqa: E402

from api import cuzk_login, tenant_pool, wsdp_client  # noqa: E402
from api import dependencies as deps  # noqa: E402
from api import main as api_main  # noqa: E402
from tests.api._script_conn import ScriptConn  # noqa: E402

_SUB = "11111111-1111-1111-1111-111111111111"
_ME = uuid.UUID("22222222-2222-2222-2222-222222222222")
_OTHER = uuid.UUID("44444444-4444-4444-4444-444444444444")
_KEY = Fernet.generate_key().decode()
_WHEN = datetime.datetime(2026, 11, 22, 10, 0, tzinfo=datetime.UTC)
PASSWORD = " ".join(("heslo", "jen", "pro", "test"))
URL = "/account/cuzk-login"


class _Opener:
    def __init__(self, conn: ScriptConn) -> None:
        self.conn, self.opened = conn, 0

    def __call__(self) -> Any:
        self.opened += 1
        return contextlib.nullcontext(self.conn)


def _is(verb: str) -> Any:
    return lambda q: "account_cuzk_logins" in q and q.strip().lower().startswith(verb)


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> _Opener:
    opener = _Opener(ScriptConn([
        (_is("select"), [("firma01", _WHEN)]),
        (_is("insert"), [("firma01", _WHEN)]),
    ]))
    monkeypatch.setattr(deps, "open_background_conn", opener)
    return opener


@pytest.fixture(autouse=True)
def ledger(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """The check budget, scripted: every check is admitted; what was admitted and settled is recorded.
    Its own semantics run on the real schema in tests/test_cuzk_login_checks_live.py."""
    seen: dict[str, list] = {"admitted": [], "settled": []}
    monkeypatch.setattr(cuzk_login, "admit_check", lambda c, a, u: seen["admitted"].append((a, u)) or 7)
    monkeypatch.setattr(cuzk_login, "settle_check", lambda c, i, o: seen["settled"].append((i, o)))
    return seen


@pytest.fixture()
def checks(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(wsdp_client, "check_login", lambda u, p: seen.append((u, p)) or u)
    return seen


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, service: _Opener) -> Any:
    monkeypatch.setenv(cuzk_login.KEY_ENV, _KEY)
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: ScriptConn([])
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": _SUB}
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda c, claims: _ME)
    monkeypatch.setattr(tenant_pool, "tenant_transaction", _tenant_transaction)
    _EVENTS.clear()
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


_EVENTS: list[str] = []


@contextlib.contextmanager
def _tenant_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
    _EVENTS.append("tenant open")
    try:
        yield ScriptConn([])
    finally:
        _EVENTS.append("tenant closed")


def _writes(opener: _Opener) -> list[tuple[str, Any]]:
    return [(q, p) for q, p in opener.conn.executed if not q.strip().lower().startswith("select")]


def test_the_state_names_the_username_and_never_the_password(client: Any, service: _Opener) -> None:
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"set": True, "username": "firma01", "checked_at": _WHEN.isoformat()}
    assert service.conn.executed[0][1] == (_ME,)


def test_a_save_checks_the_login_then_stores_it_encrypted(client: Any, service: _Opener, checks: list,
                                                          ledger: dict) -> None:
    resp = client.put(URL, json={"username": "firma01", "password": PASSWORD})
    assert resp.status_code == 200 and resp.json()["set"] is True
    assert checks == [("firma01", PASSWORD)]
    assert ledger == {"admitted": [(_ME, "firma01")], "settled": [(7, "accepted")]}
    (sql, params), = _writes(service)
    assert params[0] == _ME and params[1] == "firma01"
    assert PASSWORD not in str(params) and PASSWORD not in resp.text
    assert Fernet(_KEY.encode()).decrypt(params[2].encode()).decode() == PASSWORD


@pytest.mark.parametrize(("reason", "status"), [("refused", 422), ("timeout", 502), ("status", 502)])
def test_a_login_that_is_not_accepted_stores_nothing(client: Any, service: _Opener, ledger: dict, reason: str,
                                                     status: int,
                                                     monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    def refuse(u: str, p: str) -> str:
        raise wsdp_client.WsdpError(reason, 500)

    monkeypatch.setattr(wsdp_client, "check_login", refuse)
    with caplog.at_level(logging.DEBUG):
        resp = client.put(URL, json={"username": "firma01", "password": PASSWORD})
    assert resp.status_code == status
    assert _writes(service) == []
    assert ledger["settled"] == [(7, "refused" if reason == "refused" else "no_answer")]
    assert PASSWORD not in resp.text and PASSWORD not in caplog.text


def test_without_a_key_nothing_is_sent_to_cuzk(client: Any, service: _Opener, checks: list,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cuzk_login.KEY_ENV)
    assert client.put(URL, json={"username": "firma01", "password": PASSWORD}).status_code == 503
    assert checks == [] and service.opened == 0


@pytest.mark.parametrize("body", [
    {"username": "", "password": PASSWORD}, {"username": "a b", "password": PASSWORD},
    {"username": "firma01", "password": ""}, {"username": "firma01"},
    {"username": "firma01", "password": PASSWORD * 9}, {"username": "firma01", "password": 5},
    {"username": "firma01", "password": PASSWORD, "extra": 1},
    {"password": PASSWORD}, {"user": "firma01", "password": PASSWORD}, {"username": 7, "password": PASSWORD},
    [PASSWORD], PASSWORD,
])
def test_a_malformed_body_is_refused_without_echoing_the_password(client: Any, service: _Opener, checks: list,
                                                                  body: Any) -> None:
    resp = client.put(URL, json=body)
    assert resp.status_code == 422
    assert checks == [] and service.opened == 0
    assert PASSWORD not in resp.text


def test_a_removal_deletes_the_callers_row(client: Any, service: _Opener) -> None:
    assert client.delete(URL).status_code == 204
    (sql, params), = _writes(service)
    assert sql.strip().lower().startswith("delete from account_cuzk_logins") and params == (_ME,)


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_a_login_is_never_acted_for(client: Any, service: _Opener, checks: list, method: str) -> None:
    kwargs = {"json": {"username": "firma01", "password": PASSWORD}} if method == "put" else {}
    resp = getattr(client, method)(URL, headers={"X-Act-As": str(_OTHER)}, **kwargs)
    assert resp.status_code == 403
    assert checks == [] and service.opened == 0


def test_the_store_round_trips_and_a_changed_key_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cuzk_login.KEY_ENV, _KEY)
    conn = ScriptConn([(_is("insert"), [("firma01", _WHEN)])])
    cuzk_login.save(conn, _ME, "firma01", PASSWORD, _WHEN)
    token = conn.executed[0][1][2]
    reader = ScriptConn([(_is("select"), [("firma01", token)])])
    assert cuzk_login.login_of(reader, _ME) == ("firma01", PASSWORD)
    monkeypatch.setenv(cuzk_login.KEY_ENV, Fernet.generate_key().decode())
    with pytest.raises(cuzk_login.Unreadable):
        cuzk_login.login_of(ScriptConn([(_is("select"), [("firma01", token)])]), _ME)


class _Resp:
    def __init__(self, status: int, body: str) -> None:
        self.status_code, self.content = status, body.encode()


def test_the_client_reads_an_accepted_and_a_refused_login(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    ok = ('<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body><a:StavWSResponse xmlns:a="u" '
          'xmlns:c="urn:cz:gov:cuzk:iskn:types:common:3.1"><c:stavSluzby><c:prihlasovaciJmeno>firma01'
          "</c:prihlasovaciJmeno></c:stavSluzby></a:StavWSResponse></S:Body></S:Envelope>")
    bad = ('<e:Envelope xmlns:e="http://schemas.xmlsoap.org/soap/envelope/"><e:Body><e:Fault><faultcode>'
           "wsse:FailedAuthentication</faultcode></e:Fault></e:Body></e:Envelope>")

    def post(url: str, **kw: Any) -> _Resp:
        sent.update(url=url, **kw)
        return _Resp(200, ok) if b"good" in kw["data"] else _Resp(500, bad)

    monkeypatch.delenv(wsdp_client.BASE_ENV, raising=False)
    monkeypatch.setattr(wsdp_client.requests, "post", post)
    assert wsdp_client.check_login("firma01", "good<&>") == "firma01"
    assert sent["url"] == "https://katastr.cuzk.gov.cz/ws/wsdp/3.1/vyhledat"
    assert b"good&lt;&amp;&gt;" in sent["data"] and sent["timeout"] == wsdp_client.TIMEOUT
    assert sent["allow_redirects"] is False
    with pytest.raises(wsdp_client.WsdpError) as refused:
        wsdp_client.check_login("firma01", PASSWORD)
    assert refused.value.reason == "refused" and PASSWORD not in str(refused.value)


def test_a_redirect_is_no_answer_and_is_never_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(wsdp_client.BASE_ENV, raising=False)
    monkeypatch.setattr(wsdp_client.requests, "post", lambda url, **kw: _Resp(307, ""))
    with pytest.raises(wsdp_client.WsdpError) as moved:
        wsdp_client.check_login("firma01", PASSWORD)
    assert (moved.value.reason, moved.value.status) == ("status", 307)


@pytest.mark.parametrize("base", [
    "http://katastr.cuzk.gov.cz/ws/wsdp/3.1/", "https://example.com/ws/wsdp/3.1/",
    "https://katastr.cuzk.gov.cz.example.com/", "https://katastr.cuzk.gov.cz/ws/wsdp/3.1",
])
def test_a_login_is_sent_only_to_cuzk_over_https(monkeypatch: pytest.MonkeyPatch, base: str) -> None:
    sent: list[str] = []
    monkeypatch.setenv(wsdp_client.BASE_ENV, base)
    monkeypatch.setattr(wsdp_client.requests, "post", lambda url, **kw: sent.append(url))
    with pytest.raises(wsdp_client.WsdpError) as refused:
        wsdp_client.check_login("firma01", PASSWORD)
    assert refused.value.reason == "misconfigured" and sent == []


def test_the_test_service_is_an_allowed_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(wsdp_client.BASE_ENV, "https://wsdptrial.cuzk.gov.cz/trial/ws/wsdp/3.1/")
    assert wsdp_client.base_url().startswith("https://wsdptrial.cuzk.gov.cz/")


@pytest.mark.parametrize("by", ["username", "account"])
def test_past_the_budget_nothing_is_sent_to_cuzk(client: Any, service: _Opener, checks: list, by: str,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    def spent(conn: Any, account: Any, username: str) -> int:
        raise cuzk_login.Throttled(by, 900)

    monkeypatch.setattr(cuzk_login, "admit_check", spent)
    resp = client.put(URL, json={"username": "firma01", "password": PASSWORD})
    assert resp.status_code == 429 and "15 minutes" in resp.json()["detail"]
    assert checks == [] and _writes(service) == []
    assert PASSWORD not in resp.text


def test_no_tenant_transaction_is_open_while_the_route_waits_on_cuzk(client: Any, service: _Opener,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wsdp_client, "check_login", lambda u, p: _EVENTS.append("cuzk") or u)
    assert client.put(URL, json={"username": "firma01", "password": PASSWORD}).status_code == 200
    assert _EVENTS == ["tenant open", "tenant closed", "cuzk"]


def test_a_throttled_save_says_how_long_to_wait_and_names_no_username(client: Any, monkeypatch: pytest.MonkeyPatch,
                                                                      caplog: pytest.LogCaptureFixture) -> None:
    def spent(conn: Any, account: Any, username: str) -> int:
        raise cuzk_login.Throttled("username", 86_000)

    monkeypatch.setattr(cuzk_login, "admit_check", spent)
    with caplog.at_level(logging.DEBUG):
        resp = client.put(URL, json={"username": "firma01", "password": PASSWORD})
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "86000"
    assert "24 hours" in resp.json()["detail"] and "not locked" in resp.json()["detail"]
    assert "firma01" not in resp.text and "firma01" not in caplog.text


def test_the_released_account_helper_has_one_caller() -> None:
    """`own_account_id_released` takes a route out of both route censuses, so who calls it is pinned here."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "api"
    callers = set()
    for path in root.rglob("*.py"):
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(fn, ast.FunctionDef) and any(
                    isinstance(n, ast.Attribute) and n.attr == "own_account_id_released" for n in ast.walk(fn)):
                callers.add(f"{path.relative_to(root.parent).as_posix()}::{fn.name}")
    assert callers == {"api/routes/cuzk_login.py::put_cuzk_login"}

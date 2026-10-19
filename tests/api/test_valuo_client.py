"""api/valuo_client.py: which credential answers, and how every way a call can go maps onto the one
closed error table. Hermetic: `requests.request` is a fake transport, so nothing reaches Valuo."""

from __future__ import annotations

import logging
from typing import Any

import pytest
import requests

from api import valuo_client as vc

TOKEN = "prod-secret-token"


class _Resp:
    def __init__(self, status: int, body: Any = None, text: bool = False) -> None:
        self.status_code, self._body, self._text = status, body, text

    def json(self) -> Any:
        if self._text:
            raise requests.JSONDecodeError("Expecting value", "<html>", 0)
        return self._body


@pytest.fixture()
def transport(monkeypatch):
    calls: list[dict[str, Any]] = []
    answers: list[Any] = []

    def fake(method: str, url: str, **kw: Any) -> _Resp:
        calls.append({"method": method, "url": url, **kw})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "request", fake)
    return calls, answers


CRED = vc.Credential("production", TOKEN)
PRICED = {"success": True, "errors": [], "form_errors": [], "ignored_ids": [],
          "result": {"avg_price": 1_097_785, "min_price": 1_010_600, "max_price": 1_183_650}}


def test_no_token_is_unconfigured():
    assert vc.credential() is None


def test_production_wins_over_sandbox(monkeypatch):
    monkeypatch.setenv("VALUO_SANDBOX_API_TOKEN", "sandbox-token")
    assert vc.credential().environment == "sandbox"
    monkeypatch.setenv("VALUO_API_TOKEN", TOKEN)
    assert vc.credential() == vc.Credential("production", TOKEN)
    monkeypatch.setenv("VALUO_API_TOKEN", "   ")
    assert vc.credential().environment == "sandbox"


def test_the_token_is_only_ever_seen_as_its_hash():
    assert len(CRED.token_sha) == 12 and int(CRED.token_sha, 16) >= 0
    assert TOKEN not in repr(CRED)


def test_a_priced_answer_is_returned_verbatim_after_one_call(transport):
    calls, answers = transport
    answers.append(_Resp(200, PRICED))
    assert vc.calculate(CRED, {"kind": "sale"}) == PRICED
    assert calls == [{"method": "POST", "url": "https://api.valuo.cz/v1/calculation", "params": None,
                      "json": {"kind": "sale"}, "headers": {"Authorization": f"Bearer {TOKEN}"},
                      "timeout": (5, 20)}]


@pytest.mark.parametrize(("answer", "code", "status"), [
    (_Resp(401, {"message": "bad token"}), "auth_rejected", 401),
    (_Resp(403, None, text=True), "auth_rejected", 403),
    (_Resp(429, {}), "rate_limited", 429),
    (_Resp(500, None, text=True), "unavailable", 500),
    (_Resp(503, {"success": False}), "unavailable", 503),
    (_Resp(404, None, text=True), "unavailable", 404),
    (requests.ConnectionError("down"), "unavailable", None),
    (requests.Timeout("slow"), "timeout", None),
    (_Resp(200, None, text=True), "invalid_response", 200),
    (_Resp(200, ["not", "an", "object"]), "invalid_response", 200),
    (_Resp(200, {"success": False, "form_errors": {"floor_area": ["required"]}}), "form_errors", 200),
    (_Resp(400, {"success": False, "form_errors": {"rating": ["invalid"]}}), "form_errors", 400),
    (_Resp(200, {"success": False, "errors": ["Adresa nenalezena"], "form_errors": []}), "no_price", 200),
    (_Resp(200, {"success": True, "result": {}}), "no_price", 200),
    (_Resp(200, {"success": True, "result": {"avg_price": "1000"}}), "no_price", 200),
])
def test_every_failure_lands_on_one_code_and_is_never_retried(transport, caplog, answer, code, status):
    calls, answers = transport
    answers.append(answer)
    with caplog.at_level(logging.WARNING, logger=vc.LOG.name), pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, {"kind": "sale"})
    assert err.value.code == code and err.value.http_status == status
    assert err.value.message == vc.ERRORS[code]
    assert len(calls) == 1
    assert TOKEN not in str(err.value) and TOKEN not in caplog.text
    assert f"code={code}" in caplog.text


def test_a_failed_answer_keeps_valuos_own_words(transport):
    _calls, answers = transport
    body = {"success": False, "errors": ["Adresa nenalezena"], "form_errors": []}
    answers.append(_Resp(200, body))
    with pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, {})
    assert err.value.response == body


def test_every_code_has_one_czech_sentence():
    assert set(vc.ERRORS) == {"unconfigured", "auth_rejected", "rate_limited", "unavailable", "timeout",
                              "invalid_response", "form_errors", "no_price"}
    assert all(m.endswith(".") and m[0].isupper() for m in vc.ERRORS.values())

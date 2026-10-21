"""api/valuo_client.py: which credential answers, and how every way a call can go maps onto the one
closed error table. Hermetic: `requests.request` is a fake transport, so nothing reaches Valuo."""

from __future__ import annotations

import json
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
FLAT = {"property_type": "flat", "kind": "sale"}
PRICED = {"ver": "3.0.0-sandbox", "keep_ids": [], "ignored_ids": [],
          "result": {"avg_price": 1_097_785, "min_price": 1_010_600, "max_price": 1_183_650, "records": []}}
ISSUES = [{"code": "invalid_enum_value", "path": ["rating"], "message": "Invalid enum value"},
          {"code": "custom", "path": [], "message": "floor_area or lot_area required"}]
REFUSED = {"message": json.dumps(ISSUES)}


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
    assert vc.calculate(CRED, FLAT) == PRICED
    assert calls == [{"method": "POST", "url": "https://api.valuo.com/v1/market-value/flat", "params": None,
                      "json": FLAT, "headers": {"Authorization": f"Bearer {TOKEN}"},
                      "timeout": (5, 20)}]


@pytest.mark.parametrize("ptype", ["flat", "house", "land", "commercial"])
def test_each_property_type_has_its_own_endpoint(transport, ptype):
    calls, answers = transport
    answers.append(_Resp(200, PRICED))
    vc.calculate(CRED, {"property_type": ptype})
    assert calls[0]["url"] == f"https://api.valuo.com/v1/market-value/{ptype}"


@pytest.mark.parametrize(("answer", "code", "status"), [
    (_Resp(401, {"message": "bad token"}), "auth_rejected", 401),
    (_Resp(403, None, text=True), "auth_rejected", 403),
    (_Resp(429, {}), "rate_limited", 429),
    (_Resp(500, None, text=True), "unavailable", 500),
    (_Resp(500, {"message": "Cannot read properties of undefined (reading 'latMin')"}), "unavailable", 500),
    (_Resp(500, {"message": "[]"}), "unavailable", 500),
    (_Resp(500, {"message": "[1, 2]"}), "unavailable", 500),
    (_Resp(503, {"success": False}), "unavailable", 503),
    (_Resp(404, {"message": "Not Found"}), "unavailable", 404),
    (_Resp(404, None, text=True), "unavailable", 404),
    (requests.ConnectionError("down"), "unavailable", None),
    (requests.Timeout("slow"), "timeout", None),
    (_Resp(200, None, text=True), "invalid_response", 200),
    (_Resp(200, ["not", "an", "object"]), "invalid_response", 200),
    (_Resp(500, REFUSED), "form_errors", 500),
    (_Resp(400, REFUSED), "form_errors", 400),
    (_Resp(500, {"message": ISSUES}), "form_errors", 500),
    (_Resp(422, REFUSED), "form_errors", 422),
    (_Resp(400, None, text=True), "calculation", 400),
    (_Resp(400, {"message": ""}), "calculation", 400),
    (_Resp(200, {"ver": "3.0.0", "result": {}}), "no_price", 200),
    (_Resp(200, {"ver": "3.0.0", "result": None}), "no_price", 200),
    (_Resp(200, {"result": {"avg_price": "1000"}}), "no_price", 200),
    (_Resp(200, {"result": {"avg_price": True}}), "no_price", 200),
])
def test_every_failure_lands_on_one_code_and_is_never_retried(transport, caplog, answer, code, status):
    calls, answers = transport
    answers.append(answer)
    with caplog.at_level(logging.WARNING, logger=vc.LOG.name), pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, FLAT)
    assert err.value.code == code and err.value.http_status == status
    assert err.value.message == vc.ERRORS[code]
    assert len(calls) == 1
    assert TOKEN not in str(err.value) and TOKEN not in caplog.text
    assert f"code={code}" in caplog.text


def test_a_failed_answer_keeps_valuos_own_words(transport):
    _calls, answers = transport
    body = {"ver": "3.0.0", "result": {"avg_price": None}}
    answers.append(_Resp(200, body))
    with pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, FLAT)
    assert err.value.response == body


def test_a_refused_form_keeps_valuos_body_and_names_each_parameter(transport):
    _calls, answers = transport
    answers.append(_Resp(500, REFUSED))
    with pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, FLAT)
    assert err.value.code == "form_errors" and err.value.response == REFUSED
    assert vc.form_issues(err.value.response) == [
        {"field": "rating", "message": "Invalid enum value"},
        {"field": None, "message": "floor_area or lot_area required"},
    ]


def test_an_issue_list_reads_the_same_encoded_or_as_a_list():
    assert vc.form_issues({"message": ISSUES}) == vc.form_issues(REFUSED) != []


@pytest.mark.parametrize("body", [None, [], "x", {}, {"message": None}, {"message": "not json"},
                                  {"message": "{}"}, {"message": "[]"}, {"message": '["rating"]'},
                                  {"message": []}, {"message": ["rating"]}])
def test_anything_but_an_issue_list_is_no_issues(body):
    assert vc.form_issues(body) == []


def test_the_engines_refusal_says_valuos_reason_in_one_line(transport):
    _calls, answers = transport
    answers.append(_Resp(400, {"message": "  Place could not be resolved  "}))
    with pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, FLAT)
    assert err.value.code == "calculation" and err.value.http_status == 400
    assert err.value.message == "Valuo nemovitost neocenilo: Place could not be resolved"
    answers.append(_Resp(400, {"message": "x" * 500}))
    with pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, FLAT)
    assert err.value.detail == "x" * 300 and err.value.message.endswith(": " + "x" * 300)


def test_an_outage_keeps_valuos_words_beside_the_fixed_sentence(transport):
    _calls, answers = transport
    answers.append(_Resp(500, {"message": "Cannot read properties of undefined (reading 'latMin')"}))
    with pytest.raises(vc.ValuoError) as err:
        vc.calculate(CRED, FLAT)
    assert err.value.message == "Valuo je teď nedostupné."
    assert err.value.detail == "Cannot read properties of undefined (reading 'latMin')"


@pytest.mark.parametrize(("cred", "body", "environment"), [
    (CRED, {"ver": "3.0.0-sandbox"}, "sandbox"),
    (CRED, {"ver": "3.0.0"}, "production"),
    (CRED, None, "production"),
    (CRED, {"ver": 3}, "production"),
    (vc.Credential("sandbox", "s"), {"ver": "3.0.0"}, "sandbox"),
])
def test_the_answer_names_its_environment_over_the_variables_name(cred, body, environment):
    assert vc.answered_by(cred, body) == environment


ADVERTS = [{"id": 101, "address": "Testovací 148, Sandboxov", "images": ["ab/cd.jpg"], "price": 5_900_000}]


def test_the_comparables_details_are_one_call_answered_verbatim(transport):
    calls, answers = transport
    answers.append(_Resp(200, ADVERTS))
    assert vc.advert_details(CRED, [101, "r2"]) == ADVERTS
    assert calls == [{"method": "POST", "url": "https://api.valuo.com/v1/advert/detail", "params": None,
                      "json": {"country_code": "cs", "ids": [101, "r2"]},
                      "headers": {"Authorization": f"Bearer {TOKEN}"}, "timeout": (5, 20)}]


@pytest.mark.parametrize(("answer", "code"), [
    (_Resp(200, {"id": 101}), "invalid_response"),
    (_Resp(403, {"message": "out of scope"}), "auth_rejected"),
    (_Resp(500, None, text=True), "unavailable"),
    (requests.Timeout("slow"), "timeout"),
])
def test_a_failed_details_call_is_one_code_and_never_retried(transport, answer, code):
    calls, answers = transport
    answers.append(answer)
    with pytest.raises(vc.ValuoError) as err:
        vc.advert_details(CRED, [101])
    assert err.value.code == code and len(calls) == 1


def test_every_code_has_one_czech_sentence():
    assert set(vc.ERRORS) == {"unconfigured", "auth_rejected", "rate_limited", "unavailable", "timeout",
                              "invalid_response", "form_errors", "calculation", "no_price"}
    assert all(m.endswith(".") and m[0].isupper() for m in vc.ERRORS.values())

"""api/http_json.py: the API's one outbound JSON call names host and status in its errors, never the
URL's query — the path by which Mapy's `apikey` used to reach the log. Hermetic: a fake transport."""

from __future__ import annotations

import logging
import traceback
from typing import Any

import pytest
import requests

from api import http_json, maps

SECRET = "mapy-secret-key"
LEAKY_URL = f"https://api.mapy.cz/v1/suggest?query=Praha&apikey={SECRET}"


class _Resp:
    def __init__(self, status: int, body: Any = None, text: bool = False) -> None:
        self.status_code, self._body, self._text = status, body, text

    def json(self) -> Any:
        if self._text:
            raise requests.JSONDecodeError("Expecting value", "<html>", 0)
        return self._body


def _answer(monkeypatch, answer: Any) -> None:
    def fake(method: str, url: str, **kw: Any) -> _Resp:
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "request", fake)


def _fail(**kw: Any) -> http_json.HttpJsonError:
    with pytest.raises(http_json.HttpJsonError) as err:
        http_json.fetch_json("GET", "https://api.mapy.cz/v1/suggest", timeout=10,
                             params={"apikey": SECRET}, **kw)
    return err.value


@pytest.mark.parametrize(("answer", "reason", "status"), [
    (requests.ConnectionError(f"Max retries exceeded with url: {LEAKY_URL}"), "unreachable", None),
    (requests.Timeout(f"Read timed out: {LEAKY_URL}"), "timeout", None),
    (_Resp(403, {"detail": "forbidden"}), "status", 403),
    (_Resp(200, None, text=True), "not_json", 200),
])
def test_a_failure_names_host_and_status_only(monkeypatch, answer, reason, status):
    _answer(monkeypatch, answer)
    exc = _fail()
    assert (exc.host, exc.reason, exc.status) == ("api.mapy.cz", reason, status)
    assert SECRET not in str(exc) and "apikey" not in str(exc)
    # requests' own, URL-bearing error is never chained onto it
    assert exc.__cause__ is None and (exc.__context__ is None or exc.__suppress_context__)
    assert SECRET not in "".join(traceback.format_exception(exc))


def test_an_error_status_keeps_the_upstream_json_answer(monkeypatch):
    _answer(monkeypatch, _Resp(422, {"form_errors": {"rating": ["invalid"]}}))
    assert _fail().body == {"form_errors": {"rating": ["invalid"]}}


def test_a_good_answer_is_its_json(monkeypatch):
    _answer(monkeypatch, _Resp(200, {"items": []}))
    assert http_json.fetch_json("GET", "https://example.test/x", timeout=1) == {"items": []}


def test_the_mapy_suggest_proxy_logs_no_key_when_both_keys_fail(monkeypatch, caplog):
    monkeypatch.setenv("MAPY_CZ_API_KEY", SECRET)
    monkeypatch.setenv("MAPY2_CZ_API_KEY", SECRET + "-backup")
    maps.clear_suggest_cache()
    _answer(monkeypatch, requests.HTTPError(f"403 Client Error: Forbidden for url: {LEAKY_URL}"))
    with caplog.at_level(logging.WARNING, logger=maps.LOG.name), pytest.raises(Exception) as err:
        maps.suggest("Praha")
    assert getattr(err.value, "status_code", None) == 503
    assert SECRET not in caplog.text and caplog.text
    maps.clear_suggest_cache()

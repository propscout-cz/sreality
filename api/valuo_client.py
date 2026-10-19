"""Valuo's calculation API (valuo.cz/docs/api): which credential answers, and one paid call.

The variable's NAME is the environment — `VALUO_API_TOKEN` (production) wins over
`VALUO_SANDBOX_API_TOKEN`, neither set means Valuo is unconfigured — so there is no flag. Both
environments call the one documented URL and differ only by token (unverified until Valuo says
otherwise in writing). One call, never retried: a retry is a second payment. The token rides the
Authorization header only and is never logged or returned; a row records `token_sha` instead.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from api import http_json

LOG = logging.getLogger(__name__)

URL = "https://api.valuo.cz/v1/calculation"
TIMEOUT = (5, 20)  # connect, read — seconds
TOKEN_ENV: tuple[tuple[str, str], ...] = (
    ("VALUO_API_TOKEN", "production"),
    ("VALUO_SANDBOX_API_TOKEN", "sandbox"),
)

# The closed set of ways a call can fail, each with the one sentence the page shows.
ERRORS: dict[str, str] = {
    "unconfigured": "Valuo zatím není nastaveno.",
    "auth_rejected": "Valuo odmítlo přístupový klíč.",
    "rate_limited": "Valuo hlásí příliš mnoho dotazů, zkuste to později.",
    "unavailable": "Valuo je teď nedostupné.",
    "timeout": "Valuo neodpovědělo do 20 sekund.",
    "invalid_response": "Valuo vrátilo odpověď, které nerozumíme.",
    "form_errors": "Valuo odmítlo některé parametry.",
    "no_price": "Valuo cenu nevrátilo.",
}


@dataclass(frozen=True)
class Credential:
    environment: str
    token: str = field(repr=False)

    @property
    def token_sha(self) -> str:
        return hashlib.sha256(self.token.encode()).hexdigest()[:12]


def credential() -> Credential | None:
    """Production when its token is set, else sandbox, else None — read per call."""
    for name, environment in TOKEN_ENV:
        token = (os.environ.get(name) or "").strip()
        if token:
            return Credential(environment, token)
    return None


class ValuoError(Exception):
    """A call that bought no price. `response` is Valuo's JSON answer when it sent one."""

    def __init__(self, code: str, http_status: int | None = None, response: Any = None) -> None:
        super().__init__(f"{code} status={http_status}")
        self.code, self.http_status, self.response = code, http_status, response

    @property
    def message(self) -> str:
        return ERRORS[self.code]


def calculate(cred: Credential, request: dict[str, Any]) -> dict[str, Any]:
    """Valuo's verbatim answer when it carries a price; ValuoError otherwise."""
    status: int | None = 200
    try:
        body = http_json.fetch_json(
            "POST", URL, json=request, timeout=TIMEOUT, headers={"Authorization": f"Bearer {cred.token}"},
        )
    except http_json.HttpJsonError as exc:
        status = exc.status
        if exc.reason == "timeout":
            raise _failed("timeout", None) from None
        if status in (401, 403):
            raise _failed("auth_rejected", status, exc.body) from None
        if status == 429:
            raise _failed("rate_limited", status, exc.body) from None
        if exc.reason == "not_json":
            raise _failed("invalid_response", status) from None
        if status is None or status >= 500 or not isinstance(exc.body, dict):
            raise _failed("unavailable", status, exc.body) from None
        body = exc.body  # a 4xx that still answers in Valuo's own shape: judged below
    if not isinstance(body, dict):
        raise _failed("invalid_response", status)
    if body.get("form_errors"):
        raise _failed("form_errors", status, body)
    result = body.get("result")
    if body.get("success") is False or not isinstance(result, dict) or not _is_number(result.get("avg_price")):
        raise _failed("no_price", status, body)
    return body


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _failed(code: str, status: int | None, response: Any = None) -> ValuoError:
    LOG.warning("VALUO call failed code=%s status=%s", code, status)
    return ValuoError(code, status, response)

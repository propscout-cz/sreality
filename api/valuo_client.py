"""Valuo's API v3 (docs.valuo.com): which credential answers, one paid market-value call
(`POST api.valuo.com/v1/market-value/{type}`), and one advert-detail call for its comparables' photos.

The variable's NAME picks the token — `VALUO_API_TOKEN` (production) wins over
`VALUO_SANDBOX_API_TOKEN`, neither set means Valuo is unconfigured — so there is no flag. Both
call the one host and differ only by token; the sandbox answers synthetic data and says so in `ver`,
which `answered_by` believes over the variable's name. A call is never retried: a retry is a second
payment. The token rides the Authorization header only and is never logged or returned; a row records
`token_sha` instead.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from api import http_json

LOG = logging.getLogger(__name__)

BASE_URL = "https://api.valuo.com/v1"
TIMEOUT = (5, 20)  # connect, read — seconds
COUNTRY_CODE = "cs"  # Valuo's code for Czechia; api/valuo.py refuses every other country before a request exists
TOKEN_ENV = (("VALUO_API_TOKEN", "production"), ("VALUO_SANDBOX_API_TOKEN", "sandbox"))
DETAIL_LIMIT = 300

# The closed set of ways a call can fail, each with the one sentence the page shows.
ERRORS: dict[str, str] = {
    "unconfigured": "Valuo zatím není nastaveno.",
    "auth_rejected": "Valuo odmítlo přístupový klíč.",
    "rate_limited": "Valuo hlásí příliš mnoho dotazů, zkuste to později.",
    "unavailable": "Valuo je teď nedostupné.",
    "timeout": "Valuo neodpovědělo do 20 sekund.",
    "invalid_response": "Valuo vrátilo odpověď, které nerozumíme.",
    "form_errors": "Valuo odmítlo některé parametry.",
    "calculation": "Valuo nemovitost neocenilo.",
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


def answered_by(cred: Credential, body: Any) -> str:
    """The environment that ANSWERED: a sandbox token pasted into VALUO_API_TOKEN still says `-sandbox`."""
    ver = body.get("ver") if isinstance(body, dict) else None
    return "sandbox" if isinstance(ver, str) and ver.endswith("-sandbox") else cred.environment


class ValuoError(Exception):
    """A call that bought no answer. `response` is Valuo's JSON when it sent one, `detail` its own words."""

    def __init__(self, code: str, http_status: int | None = None, response: Any = None) -> None:
        super().__init__(f"{code} status={http_status}")
        self.code, self.http_status, self.response = code, http_status, response
        said = response.get("message") if isinstance(response, dict) else None
        self.detail = (said.strip()[:DETAIL_LIMIT] or None) if isinstance(said, str) else None

    @property
    def message(self) -> str:
        if self.code == "calculation" and self.detail:
            return f"{ERRORS['calculation'].rstrip('.')}: {self.detail}"
        return ERRORS[self.code]


def form_issues(body: Any) -> list[dict[str, Any]]:
    """Valuo's refused form as [{field, message}]: its error body's `message` is the list of zod issues (JSON-encoded,
    or already a list), each naming the parameter as `path[0]`. [] when the body is anything else."""
    issues = body.get("message") if isinstance(body, dict) else None
    if isinstance(issues, str):
        try:
            issues = json.loads(issues)
        except ValueError:
            return []
    if not isinstance(issues, list) or not issues or not all(isinstance(i, dict) for i in issues):
        return []
    out = []
    for issue in issues:
        path = issue.get("path")
        head = path[0] if isinstance(path, list) and path else None
        out.append({"field": head if isinstance(head, str) else None, "message": str(issue.get("message") or "")})
    return out


def _post(cred: Credential, path: str, body: dict[str, Any]) -> Any:
    try:
        return http_json.fetch_json("POST", f"{BASE_URL}/{path}", json=body, timeout=TIMEOUT,
                                    headers={"Authorization": f"Bearer {cred.token}"})
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
        # Request validation answers HTTP 500 today (400 later, Valuo says); 400 itself is the engine's refusal.
        if form_issues(exc.body):
            raise _failed("form_errors", status, exc.body) from None
        raise _failed("calculation" if status == 400 else "unavailable", status, exc.body) from None


def calculate(cred: Credential, request: dict[str, Any]) -> dict[str, Any]:
    """Valuo's verbatim answer when it carries a price; ValuoError otherwise."""
    body = _post(cred, f"market-value/{request['property_type']}", request)
    if not isinstance(body, dict):
        raise _failed("invalid_response", 200)
    result = body.get("result")
    if not isinstance(result, dict) or type(result.get("avg_price")) not in (int, float):  # a bool is no price
        raise _failed("no_price", 200, body)
    return body


def advert_details(cred: Credential, ids: list[Any]) -> list[Any]:
    """Valuo's verbatim per-advert answer (photos as storage paths) for its comparables' ids; ValuoError otherwise."""
    body = _post(cred, "advert/detail", {"country_code": COUNTRY_CODE, "ids": ids})
    if not isinstance(body, list):
        raise _failed("invalid_response", 200)
    return body


def _failed(code: str, status: int | None, response: Any = None) -> ValuoError:
    LOG.warning("VALUO call failed code=%s status=%s", code, status)
    return ValuoError(code, status, response)

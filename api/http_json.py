"""The API's one outbound JSON call: `requests`, an explicit timeout, no retry. A failure is an
`HttpJsonError` naming the host and the HTTP status only: requests' own exceptions carry the full URL,
query string included, so Mapy's `apikey` used to reach the log through them; they are never chained."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import requests

Timeout = float | tuple[float, float]


class HttpJsonError(Exception):
    """`reason` is timeout | unreachable | status | not_json; `body` is the upstream's JSON answer, if any."""

    def __init__(self, host: str, reason: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(f"{reason} host={host} status={status}")
        self.host, self.reason, self.status, self.body = host, reason, status, body


def fetch_json(
    method: str,
    url: str,
    *,
    timeout: Timeout,
    params: dict[str, Any] | None = None,
    json: Any = None,
    headers: dict[str, str] | None = None,
) -> Any:
    host = urlsplit(url).hostname or "?"
    try:
        resp = requests.request(method, url, params=params, json=json, headers=headers, timeout=timeout)
    except requests.Timeout:
        raise HttpJsonError(host, "timeout") from None
    except requests.RequestException:
        raise HttpJsonError(host, "unreachable") from None
    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code >= 400:
        raise HttpJsonError(host, "status", resp.status_code, body)
    if body is None:
        raise HttpJsonError(host, "not_json", resp.status_code)
    return body

"""Shared HTTP layer for portal clients (Phase 4 portal framework).

Every portal fetches over HTTP with the same machinery: a `requests.Session`
with browser-like headers, a shared `RateLimiter` (paced + `penalize()` on an
HTTP 429/403 throttle), a retry loop with exponential backoff over transient
statuses, and `ListingGoneError` on a 404/410. `BasePortalClient` owns all of
that; a concrete client supplies only what differs between portals: the `Accept`
header (JSON vs HTML), how it builds URLs / paginates, and how it interprets a
response body. An HTML portal's "this ad is no longer offered" signals are two
declarations (`DETAIL_URL`, `GONE_PAGE`) that `_fetch_detail_page` applies.

The portal-specific clients (`scraper.sreality_client`, `scraper.bazos_client`)
subclass this and stay thin. New portals do the same — see the modularity rule
in CLAUDE.md.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, NoReturn

import requests

from scraper import stop

if TYPE_CHECKING:
    from scraper.rate_limit import RateLimiter

LOG = logging.getLogger(__name__)

# 403/429 are how a portal throttles a too-fast egress IP; treat them as
# retryable (penalize + back off + retry) rather than fatal, so a transient
# block can't crash a whole run.
RETRYABLE_STATUS: frozenset[int] = frozenset(
    {403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
)

# Statuses that mean "this listing no longer exists" rather than transient.
GONE_STATUSES: frozenset[int] = frozenset({404, 410})

# What a portal paints into an ad's OWN status slot (price cell, badge) once the deal is done: the
# page and card stay up as a reference, the ad is no longer offered. Reserved is still on offer.
CLOSED_STATUS = r"(?:prodáno|pronajato)"


def states_closed(text: str | None) -> bool:
    """True iff a status slot's whole text says the ad was sold or let."""
    return text is not None and re.fullmatch(CLOSED_STATUS, text.strip(), re.IGNORECASE) is not None


def gone_phrases(*phrases: str) -> re.Pattern[str]:
    """One case-insensitive pattern for the texts a removed ad's page carries (any one
    whitespace character between words, an &nbsp; included)."""
    return re.compile(
        "|".join(r"\s".join(map(re.escape, p.split(" "))) for p in phrases), re.IGNORECASE)


def stated_closed(slot: str) -> re.Pattern[str]:
    """A page whose own status slot (`slot`: the regex up to where its text starts) says sold/let;
    the anchor keeps a sold neighbour in a similar-listings block from closing a live ad."""
    return re.compile(slot + r"\s*" + CLOSED_STATUS + r"\s*<", re.IGNORECASE)

# UA + language shared by every portal; the per-portal `Accept` is set by the
# subclass's ACCEPT class attribute (JSON for an API, HTML for a crawler).
_BASE_HEADERS: dict[str, str] = {
    "Accept-Language": "cs,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    ),
}


_CHROME_MAJOR = re.compile(r"Chrome/(\d+)")
# UAs whose real client hints differ from desktop Google Chrome's (mobile flag,
# platform or brand), so desktop-Chrome hints would contradict them.
_NOT_DESKTOP_CHROME = ("Android", "Mobile", "CrOS", "Edg/", "HeadlessChrome")
# Chromium's GREASE brand: name, version and list order are all seeded by the major.
_GREASE_CHARS = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
_GREASE_VERSIONS = ("8", "99", "24")
_GREASE_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


def _sec_ch_ua(major: int) -> str:
    """The sec-ch-ua brand list desktop Google Chrome of this major version sends."""
    grease = (
        f'"Not{_GREASE_CHARS[major % 11]}A{_GREASE_CHARS[(major + 1) % 11]}Brand";'
        f'v="{_GREASE_VERSIONS[major % 3]}"'
    )
    brands = ["", "", ""]
    order = _GREASE_ORDERS[major % 6]
    brands[order[0]] = grease
    brands[order[1]] = f'"Chromium";v="{major}"'
    brands[order[2]] = f'"Google Chrome";v="{major}"'
    return ", ".join(brands)


def client_hints(user_agent: str) -> dict[str, str]:
    """Desktop Chrome's low-entropy client hints for this UA ({} unless desktop Google Chrome)."""
    match = _CHROME_MAJOR.search(user_agent)
    if match is None or any(token in user_agent for token in _NOT_DESKTOP_CHROME):
        return {}
    if "Windows NT" in user_agent:
        platform = "Windows"
    elif "Macintosh" in user_agent:
        platform = "macOS"
    elif "Linux" in user_agent:
        platform = "Linux"
    else:
        platform = "Windows"
    return {
        "sec-ch-ua": _sec_ch_ua(int(match.group(1))),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": f'"{platform}"',
    }


class ListingGoneError(Exception):
    """A listing's detail endpoint signals it no longer exists."""

    def __init__(self, url: str, status: int | None) -> None:
        self.status = status
        super().__init__(f"listing gone (status={status}) at {url}")


class BasePortalClient:
    """Session + retry/backoff + adaptive throttle shared by all portal clients.

    Subclasses set `ACCEPT` and call `_request(...)` to get a `< 400` response
    (or a raised `ListingGoneError` / `requests.HTTPError`), then interpret the
    body their own way; an HTML detail page goes through `_fetch_detail_page`.
    """

    ACCEPT: str = "*/*"
    # The detail page's gone signals beyond a 404/410 (rule #3), decided before any body reaches the
    # archive (W10): a final URL off DETAIL_URL is a redirect off the ad; a GONE_PAGE match is a removed
    # ad's page or one whose own status slot says sold/let.
    DETAIL_URL: re.Pattern[str] | None = None
    GONE_PAGE: tuple[re.Pattern[str], ...] = ()
    # An optional honest/identifying override of the shared browser User-Agent,
    # set by a subclass that crawls a site we want to identify ourselves to.
    # None = the shared browser UA. A per-portal fetcher concern, not a default.
    USER_AGENT: str | None = None
    # Opt-in: route every request through the residential proxy in the
    # SCRAPER_PROXY_URL env var, for portals whose edge punishes our datacenter
    # (GitHub-Actions / Railway) IP. Unset env or False = direct (our IP), so the
    # default for every other portal is unchanged and free.
    USE_PROXY: bool = False
    # …and there are TWO proxy postures, which callers must not confuse.
    # ceskereality and mmreality are PROXY_REQUIRED: unproxied they are not run
    # at all (the realtime worker skips them entirely — `_skip_for_proxy`). idnes
    # instead SOFT-throttles the datacenter IP — every page still arrives, roughly
    # 13x slower — so skipping it would trade degraded data for NO data.
    # Hence: USE_PROXY says "route through it when available"; PROXY_REQUIRED says
    # "without it, do not bother". Default True preserves the two incumbents.
    PROXY_REQUIRED: bool = True
    PROXY_ENV = "SCRAPER_PROXY_URL"
    # Opt-in: send the Chrome client hints (sec-ch-ua*) derived from the UA, as a
    # real Chrome does on every HTTPS request. Per portal, so every other portal's
    # request profile stays unchanged.
    CLIENT_HINTS: bool = False

    def __init__(
        self,
        *,
        limiter: "RateLimiter | None" = None,
        request_delay_s: float = 1.5,
        timeout_s: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        # A shared RateLimiter paces requests globally across worker threads and
        # widens its interval on a throttle. Serial callers pass none and fall
        # back to the per-instance request_delay_s self-throttle.
        self._limiter = limiter
        self.request_delay_s = request_delay_s
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._session = requests.Session()
        headers = {**_BASE_HEADERS, "Accept": self.ACCEPT}
        if self.USER_AGENT is not None:
            headers["User-Agent"] = self.USER_AGENT
        if self.CLIENT_HINTS:
            headers.update(client_hints(headers["User-Agent"]))
        self._session.headers.update(headers)
        if self.USE_PROXY:
            proxy = os.environ.get(self.PROXY_ENV)
            if proxy:
                self._session.proxies = {"http": proxy, "https": proxy}
                LOG.info("PROXY enabled (residential egress via %s)", self.PROXY_ENV)
            else:
                LOG.warning(
                    "USE_PROXY set but %s is empty — falling back to the direct "
                    "(datacenter) IP.", self.PROXY_ENV)
        self._last_at = 0.0

    def _pace(self) -> None:
        if self._limiter is not None:
            self._limiter.acquire()
            return
        if stop.wait(self.request_delay_s - (time.monotonic() - self._last_at)):
            self._stopped()

    def _send(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Perform ONE request and return a response-like object.

        The single seam for a portal whose bytes do not arrive over `requests` —
        ceskereality drives a headless browser through it, because its pages sit behind a
        JS proof-of-work interstitial (`scraper.browser_fetch`). Everything around this
        call stays shared and portal-generic: pacing, `penalize()` on a throttle, the
        retryable-status retry/backoff, gone detection and the stop signal.

        A replacement must offer what the loop and the callers read: `.status_code`,
        `.text`, `.url` and `raise_for_status()` (a JSON portal's client also reads
        `.json()`), hence the loose return type.
        """
        if json_body is not None or method == "POST":
            return self._session.post(url, json=json_body, timeout=self.timeout_s)
        if params is None:
            return self._session.get(url, timeout=self.timeout_s)
        return self._session.get(url, params=params, timeout=self.timeout_s)

    def _fetch_detail_page(self, url: str) -> tuple[str, int]:
        """A detail page the portal still offers, as (html, status); `ListingGoneError` otherwise."""
        response = self._request(url)
        final_url = getattr(response, "url", url) or url
        if self.DETAIL_URL is not None and not self.DETAIL_URL.search(final_url):
            raise ListingGoneError(url, response.status_code)
        text = response.text
        if any(pattern.search(text) for pattern in self.GONE_PAGE):
            raise ListingGoneError(url, response.status_code)
        return text, response.status_code

    def close(self) -> None:
        """Release whatever the fetcher holds. A no-op for every `requests` portal; the
        walk/probe/drain teardown calls it, so a portal holding a browser (ceskereality)
        does not leak one per run."""

    def _request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        method: str = "GET",
        pace: bool = True,
    ) -> Any:
        """GET (or POST) with retry/backoff + adaptive throttle. Returns a
        `< 400` response.

        A `json_body` (or `method="POST"`) sends a POST with that JSON body —
        for JSON-API portals whose index/detail is a GraphQL/RPC call rather
        than a URL GET (bezrealitky). Everything else is identical to a GET.

        Raises `ListingGoneError` immediately on a 404/410 (no retry — a gone
        listing won't come back), retries the `RETRYABLE_STATUS` set with
        exponential backoff, `raise_for_status()` on any other `>= 400` (e.g.
        sreality's 422 deep-pagination cap, which the caller catches), and
        re-raises the last `RequestException` once retries are exhausted.

        `pace=False` lets a caller that already paced (e.g. a per-fetch limiter
        acquire outside the retry loop) skip the built-in throttle.
        """
        error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if (attempt > 0 and stop.wait(2.0 ** (attempt - 1))) or stop.requested():
                self._stopped()
            if pace:
                self._pace()
            try:
                with stop.interruptible():
                    response = self._send(
                        url, method=method, params=params, json_body=json_body
                    )
                self._last_at = time.monotonic()
                status = response.status_code
                if status in (429, 403) and self._limiter is not None:
                    LOG.warning("RATE penalize status=%d url=%s", status, url)
                    self._limiter.penalize()
                if status in GONE_STATUSES:
                    raise ListingGoneError(url, status)
                if status in RETRYABLE_STATUS:
                    raise requests.HTTPError(
                        f"{status} from {url}", response=response
                    )
                if status >= 400:
                    response.raise_for_status()
                return response
            except ListingGoneError:
                raise
            except stop.StopRequested:
                self._stopped()
            except requests.RequestException as exc:
                error = exc
                LOG.warning(
                    "GET %s attempt %d/%d failed: %s",
                    url, attempt + 1, self.max_retries + 1, exc,
                )
        assert error is not None
        raise error

    def _stopped(self) -> NoReturn:
        if self._limiter is not None and not self._limiter.refused:
            self._limiter.refuse("stop")
        raise stop.StopRequested("a stop signal arrived")

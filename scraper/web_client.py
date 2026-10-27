"""HTTP for the tenth portal, `web` (any site an operator pastes): fetch only.

Every request goes through `scraper.egress` (address checks + pinning on every hop, one
20 s deadline, a 2 MB decoded cap, a desktop Chrome identity). On top of it:

  * **per-host pacing**: one page load per HOST_INTERVAL_S per host, process-wide (the
    worker builds a client per drain pass; the spacing outlives it). A page load is its
    redirect hops included, as a browser's is;
  * the drain's limiter (`make_client(limiter)`): acquired after the host's slot, so the
    shared ledger's deadline still refuses a late fetch (`shared_rate_limiter=True`). The
    host's slot is never reserved past that deadline: a fetch whose slot would start after it
    refuses the run instead (`limiter.refuse('deadline')`, `RateBudgetUnavailable`), so a
    chunk of same-host ads cannot sleep the worker's drain lane past its budget, and the
    runner hands the unfetched claims back without spending an attempt; once refused, no
    later fetch sleeps at all;
  * no hop into one of the nine portals' ads: a redirect there (a shortener, a link out) is
    returned as it stands (`egress.fetch(stop_before=...)`) and the adapter re-keys the ad
    to that portal, whose own client, limits and proxy fetch it (rule 21);
  * 404/410 → `ListingGoneError`; any other status ≥ 400 raises `requests.HTTPError`
    (the drain counts an attempt). No in-client retry: the queue's five attempts are the
    retry, and a foreign site must never hold a drain thread past its deadline.

No proxy: a pasted site is not a portal that blocks our address, and an operator who pastes
one expects it fetched from ours.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from scraper import egress, stop
from scraper.portal_base import GONE_STATUSES, ListingGoneError
from scraper.portal_identity import WEB, Resolved, resolve_url
from scraper.rate_ledger import RateBudgetUnavailable

if TYPE_CHECKING:
    from scraper.rate_limit import RateLimiter

LOG = logging.getLogger(__name__)
HOST_INTERVAL_S = 10.0
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_\-]+)""", re.IGNORECASE)

_HOST_LOCK = threading.Lock()
_HOST_NEXT_AT: dict[str, float] = {}


def _host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def reserve_host_slot(url: str, now: float | None = None, *,
                      not_after: float | None = None) -> float | None:
    """Reserve this host's next slot; returns how long the caller must wait for it, or None
    (nothing reserved) when that slot would start after `not_after`."""
    key = _host_key(url)
    with _HOST_LOCK:
        now = time.monotonic() if now is None else now
        at = max(now, _HOST_NEXT_AT.get(key, 0.0))
        if not_after is not None and at > not_after:
            return None
        _HOST_NEXT_AT[key] = at + HOST_INTERVAL_S
    return at - now


def is_portal_ad(url: str) -> bool:
    """A URL that names one of the nine portals' ads (never fetched by this client)."""
    target = resolve_url(url)
    return isinstance(target, Resolved) and target.source != WEB


def decode_html(body: bytes, content_type: str | None) -> str:
    """The page's text: the header's charset, else a <meta charset>, else UTF-8 (lossless
    on what it can read, replacement characters on what it cannot)."""
    charset = None
    if content_type:
        m = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type, re.IGNORECASE)
        charset = m.group(1) if m else None
    if charset is None:
        m = _CHARSET_RE.search(body[:4096])
        charset = m.group(1).decode("ascii") if m else None
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class WebClient:
    """One page of any site: (html, status, final_url) — the URL the redirects ended on."""

    def __init__(self, *, limiter: "RateLimiter | None" = None) -> None:
        self._limiter = limiter
        self._session = egress.safe_session({"Accept": ACCEPT})

    def fetch_detail(self, url: str) -> tuple[str, int, str]:
        limiter = self._limiter
        if limiter is not None and limiter.refused:
            raise RateBudgetUnavailable(f"{WEB}: the run was already refused ({limiter.refused_reason})")
        deadline = limiter.deadline if limiter is not None else None
        wait = reserve_host_slot(url, not_after=deadline)
        if wait is None:
            assert limiter is not None  # a deadline exists only on a limiter
            limiter.refuse("deadline")
            LOG.info("RATE deadline reached source=%s: the next slot on %s starts past the "
                     "run's time budget", WEB, _host_key(url))
            raise RateBudgetUnavailable(f"{WEB}: the next slot on {_host_key(url)} is past the deadline")
        stop.wait(wait)
        if limiter is not None:
            limiter.acquire()  # refuses the run if a stop arrived during the host wait
        result = egress.fetch(url, session=self._session, stop_before=is_portal_ad)
        if result.status in GONE_STATUSES:
            raise ListingGoneError(url, result.status)
        egress.raise_for_status(result)
        return decode_html(result.body, result.content_type), result.status, result.final_url

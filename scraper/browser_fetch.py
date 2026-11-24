"""Headless-browser fetch for a portal sitting behind a JS proof-of-work interstitial.

ceskereality.cz put an [Anubis](https://github.com/TecharoHQ/anubis) v1.28.2 interstitial
in front of every page on 2026-10-31. It answers **HTTP 200** with challenge HTML
(`<script id="anubis_challenge">`, `{"algorithm":"fast","difficulty":2}`) instead of
listings, so a `requests` fetch collects nothing, the parser finds no cards, and the walk
reads the page as EMPTY — an our-stop, so it never reaches the portal's end and nominates
nothing (rule #3). That is the #2479 regression: pages/walk fell ~2,400 -> ~140 the day
the wall went up, and nominations stopped.

A real browser runs the proof of work in JS and is issued a ~7-day auth cookie; the
context then carries it, so every later page in the session loads the real HTML. Measured
live 2026-11-16: 20/20 index pages parsed, ONE challenge (the first), ~1.3 s and ~30 KB
per steady page.

Four things this module exists to get right:

  * **One solve per session, on ONE pinned IP.** The challenge binds to the caller's IP, and
    the residential gateway rotates its exit IP per request — on a rotating IP every page
    re-challenges. `sticky_proxy` pins one exit IP for the whole session. A residential IP is
    still mandatory for a different reason: a datacenter IP is refused by nginx with a 403
    BEFORE Anubis runs, so this does not replace `SCRAPER_PROXY_URL`, it runs behind it.
  * **An unsolved wall is a FAILED FETCH, never content.** `classify_status` turns a
    challenged 200 into a 403, which `BasePortalClient._request` already treats as retryable
    (penalize + back off, then raise). So a difficulty bump surfaces as a red walk
    (the empty-walk check) and can never be mistaken for "this page has no ads".
  * **ONE OWNER THREAD does every browser call.** Playwright's sync API is thread-AFFINE,
    not merely thread-unsafe: its dispatcher is a greenlet created on the starting thread, so
    touching a page — or even closing it — from any other thread raises
    `greenlet.error: Cannot switch to a different thread` (verified 2026-11-16). A lock does
    NOT fix that. The drain is multi-threaded (a pool per claim chunk, sized by a per-portal
    DB row), so launch, fetch and close are all submitted to one private worker thread and
    callers just wait. That is what makes correctness independent of `detail_workers`, and
    what stops a browser leaking when the teardown runs on a different thread than the launch.
  * **A transport failure retries like any other.** Everything raised here is a
    `requests.RequestException`, because the shared retry loop only retries those; otherwise
    one flaky page load would end a whole walk slice and cost the walk its end — the very
    symptom this module exists to cure.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

import requests

from scraper import cgroup, stop

LOG = logging.getLogger(__name__)

T = TypeVar("T")

# The interstitial's own element id. Present = the wall, absent = the page we asked for.
CHALLENGE_MARKER = 'id="anubis_challenge"'

# Cleared = the challenge element is gone AND the replacement document is past loading.
# Anubis reloads the page itself once the proof of work validates.
_CLEARED_JS = (
    "() => !document.getElementById('anubis_challenge') && document.readyState !== 'loading'"
)

# Defaults; a client passes its own budget (see `timeout_s`), so one page can never
# outlive the walk's deadline by more than one navigation + one solve.
NAV_TIMEOUT_MS = 45_000
SOLVE_TIMEOUT_MS = 45_000
# Residential exits are metered, and a walk only ever needs the HTML: aborting
# sub-resources took a measured page from ~261 KB to ~30 KB.
_DOCUMENT_TYPES = frozenset({"document"})
# The interstitial's own assets must load, or it can never solve itself.
_CHALLENGE_PATH_PREFIX = "/.within.website/"
# `proxy_url=` default: None means "no proxy", this means "read SCRAPER_PROXY_URL".
FROM_ENV = "<from-env>"
# Chromium forks helpers that outlive the browser process; the kernel hands them to PID 1,
# and in the worker's container PID 1 is this process, which never waited for them. Two
# zombies per launch held a PID each until the container had none left (#2565: `can't start
# new thread` 24-25 h after every boot). Only chromium's own names are reaped: any other
# child's exit status belongs to whoever spawned it.
_CHROMIUM_COMM = ("chrome", "headless_shell")


def reap_browser_orphans() -> tuple[int, int]:
    """Wait for every exited chromium child of this process: (reaped, children still held)."""
    me, reaped, held = os.getpid(), 0, 0
    try:
        pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return 0, 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
        except OSError:
            continue
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        state, ppid = stat[stat.rindex(")") + 2:].split()[:2]
        if int(ppid) != me:
            continue
        if state == "Z" and comm.startswith(_CHROMIUM_COMM):
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    reaped += 1
                    continue
            except ChildProcessError:
                pass
        held += 1
    return reaped, held


class BrowserTransportError(requests.RequestException):
    """A browser fetch failed. A `RequestException` on purpose: the shared retry loop in
    `BasePortalClient._request` retries only those, so a nav timeout or a crashed context
    backs off and retries exactly as a socket error did on the `requests` path."""


class BrowserUnavailable(BrowserTransportError):
    """Playwright, or its chromium build, is missing or will not launch. Raised at launch,
    not at import, so a runtime without the optional dependency fails on the portal that
    needs it instead of breaking every import of the scraper."""


@dataclass(frozen=True)
class BrowserResponse:
    """The response-like `BasePortalClient._request` reads: `.status_code`, `.text`, `.url`."""

    status_code: int
    text: str
    url: str

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code, response.url = self.status_code, self.url
            raise requests.HTTPError(
                f"{self.status_code} for url: {self.url}", response=response
            )


def is_challenge(html: str) -> bool:
    return CHALLENGE_MARKER in (html or "")


def classify_status(status: int, html: str) -> int:
    """A page still showing the interstitial is a retryable failure (403), whatever status
    it was served with. Everything else passes through untouched, so a 404/410 still reaches
    the gone handling."""
    return 403 if is_challenge(html) else status


def sticky_proxy(
    proxy_url: str | None, session_id: str, *, country: str = "cz"
) -> dict[str, str] | None:
    """A Playwright proxy pinned to ONE residential exit IP for this session.

    The provider's sticky-session syntax lives in the username
    (`<login>__cr.<country>;sessid-<id>`). The separator must stay a RAW `;`: measured
    2026-11-15, a percent-encoded `%3B` is not decoded, so the session silently does not
    apply and the exit IP rotates — which re-challenges every page.
    """
    if not proxy_url:
        return None
    parts = urlsplit(proxy_url)
    if not parts.hostname or not parts.username:
        LOG.warning("BROWSER proxy URL carries no credentials; running unpinned")
        return None
    port = f":{parts.port}" if parts.port else ""
    return {
        "server": f"{parts.scheme}://{parts.hostname}{port}",
        "username": f"{parts.username}__cr.{country};sessid-{session_id}",
        "password": parts.password or "",
    }


def playwright_available() -> bool:
    """Is the optional dependency importable at all? The worker asks before claiming work
    for this portal, so a deploy that reaches the image before its `browser` extra does
    skips the portal instead of spending a queue row's attempts on every claim."""
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def fetch_through_page(
    page: Any,
    url: str,
    *,
    solve_timeout_ms: int = SOLVE_TIMEOUT_MS,
    nav_timeout_ms: int = NAV_TIMEOUT_MS,
    status_of: Callable[[], int | None] | None = None,
) -> BrowserResponse:
    """Load one URL in an already-open page, clearing the interstitial if it appears.

    Split out from `BrowserFetcher` so the contract — wait only when challenged, return a
    403 when it never clears, pass a gone status through — is testable without chromium.

    `status_of()` reports the status of the LAST main-frame document response, which is how
    a status served *after* the solve is read: the `goto` response is the challenge's own
    200, so without this a listing that is really a 404 would be reported as 200 on the one
    challenged page of a session and never reach the gone handling.
    """
    response = page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
    if response is None:
        raise BrowserTransportError(f"no response for {url}")
    status = int(getattr(response, "status", 0) or 0)
    html = page.content()
    if is_challenge(html):
        try:
            page.wait_for_function(_CLEARED_JS, timeout=solve_timeout_ms)
        except stop.StopRequested:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure to clear is one outcome
            LOG.warning("BROWSER interstitial did not clear for %s: %r", url, exc)
        else:
            # Anubis navigates on success; settle before reading the replacement.
            try:
                page.wait_for_load_state("domcontentloaded")
            except stop.StopRequested:
                raise
            except Exception as exc:  # noqa: BLE001 - already have the content we need
                LOG.debug("BROWSER load-state wait after the solve: %r", exc)
        html = page.content()
        if status_of is not None:
            after = status_of()
            if after is not None:
                status = after
    return BrowserResponse(
        status_code=classify_status(status, html), text=html, url=page.url
    )


class BrowserFetcher:
    """One chromium + one persistent context, launched on first use and reused, with every
    browser call run on ONE private owner thread.

    The context is what makes this affordable: the interstitial is solved once and its
    cookie is carried by every later fetch in the session. Launch is lazy so a run that
    never touches this portal pays no browser cost, and `close()` must be called by the
    owner (the client's `close()`, which the walk/probe/drain teardown calls).
    """

    def __init__(
        self,
        *,
        proxy_url: str | None = FROM_ENV,
        session_id: str | None = None,
        country: str = "cz",
        user_agent: str | None = None,
        locale: str = "cs-CZ",
        block_subresources: bool = True,
        timeout_s: float | None = None,
        rotate_after: int = 0,
        page: Any | None = None,
    ) -> None:
        self._proxy_url = (
            os.environ.get("SCRAPER_PROXY_URL") if proxy_url == FROM_ENV else proxy_url
        )
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._country = country
        self._user_agent = user_agent
        self._locale = locale
        self._block_subresources = block_subresources
        budget_ms = int(timeout_s * 1000) if timeout_s else None
        self._nav_timeout_ms = budget_ms or NAV_TIMEOUT_MS
        self._solve_timeout_ms = budget_ms or SOLVE_TIMEOUT_MS
        # One sticky session is ONE residential exit IP. A whole walk on a single IP is
        # what the portal blocked (2026-11-16: a 30-minute 403 window cost three category
        # groups their walk), so the session is retired every `rotate_after` pages and
        # whenever a caller reports a refusal. 0 keeps one session for the whole run.
        self._rotate_after = max(0, int(rotate_after))
        self._on_session = 0
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = page
        self._last_doc_status: int | None = None
        # Playwright's sync API is thread-AFFINE (its greenlet dispatcher belongs to the
        # thread that started it), so EVERY call below — launch, fetch, close — runs on
        # this one thread and callers block on the result. The drain hands work to a new
        # pool per claim chunk and tears down on yet another thread; without this, the
        # second chunk raised `greenlet.error` and the teardown silently leaked a chromium
        # per pass.
        self._owner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-fetch")
        self._lifecycle = threading.Lock()
        self._closed = False

    def _on_owner(self, call: Callable[[], T]) -> T:
        return self._owner.submit(call).result()

    def _ensure_page(self) -> Any:
        """Runs ON the owner thread."""
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "playwright is not installed; install the scraper's 'browser' extra "
                "and `playwright install --with-deps chromium`"
            ) from exc
        proxy = sticky_proxy(self._proxy_url, self._session_id, country=self._country)
        if proxy is None:
            LOG.warning(
                "BROWSER no residential proxy (%s unset): a datacenter IP is refused "
                "before the interstitial even runs", "SCRAPER_PROXY_URL")
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True, proxy=proxy)
            context_args: dict[str, Any] = {"locale": self._locale}
            if self._user_agent:
                context_args["user_agent"] = self._user_agent
            self._context = self._browser.new_context(**context_args)
            self._context.set_default_timeout(self._nav_timeout_ms)
            if self._block_subresources:
                self._context.route("**/*", _abort_subresources)
            self._page = self._context.new_page()
            self._page.on("response", self._note_document_status)
        except BrowserUnavailable:
            raise
        except Exception as exc:
            self._close_on_owner()
            raise BrowserUnavailable(f"could not launch chromium: {exc}") from exc
        LOG.info("BROWSER launched (session=%s proxied=%s)", self._session_id, proxy is not None)
        return self._page

    def _note_document_status(self, response: Any) -> None:
        """The status of the last main-frame document response, so a status served AFTER
        the interstitial clears is the one reported (see `fetch_through_page`)."""
        try:
            request = response.request
            if request.resource_type == "document" and request.frame == self._page.main_frame:
                self._last_doc_status = int(response.status)
        except Exception as exc:  # noqa: BLE001 - a listener must never break a fetch
            LOG.debug("BROWSER response listener: %r", exc)

    def _fetch_on_owner(self, url: str) -> BrowserResponse:
        if self._rotate_after and self._on_session >= self._rotate_after:
            self._rotate_on_owner(f"{self._on_session} pages on this exit IP")
        page = self._ensure_page()
        self._on_session += 1
        self._last_doc_status = None
        try:
            return fetch_through_page(
                page, url,
                solve_timeout_ms=self._solve_timeout_ms,
                nav_timeout_ms=self._nav_timeout_ms,
                status_of=lambda: self._last_doc_status,
            )
        except (BrowserTransportError, stop.StopRequested):
            raise
        except Exception as exc:
            # Playwright's own TimeoutError/Error are not RequestExceptions, so the shared
            # retry loop would not retry them and one flaky page would end a walk slice.
            raise BrowserTransportError(f"browser fetch failed for {url}: {exc}") from exc

    def fetch(self, url: str) -> BrowserResponse:
        with self._lifecycle:
            if self._closed:
                raise BrowserTransportError("this browser fetcher is closed")
        return self._on_owner(lambda: self._fetch_on_owner(url))

    @property
    def session_id(self) -> str:
        """The sticky session currently pinned — i.e. which residential exit IP we are."""
        return self._session_id

    def rotate(self, reason: str = "") -> None:
        """Retire this exit IP: drop the browser holding it and pick a new sticky session,
        so the next fetch launches on a different residential IP.

        Keeping a blocked session is pointless — every later page draws the same refusal
        until the portal lifts it, which is how one 30-minute block cost a whole walk three
        category groups. The browser must go too: its context carries the interstitial
        cookie, which is bound to both the old IP and that browser."""
        with self._lifecycle:
            if self._closed:
                raise BrowserTransportError("this browser fetcher is closed")
        self._on_owner(lambda: self._rotate_on_owner(reason))

    def _rotate_on_owner(self, reason: str) -> None:
        """Runs ON the owner thread: it closes a browser, which is thread-affine."""
        retired = self._session_id
        self._close_on_owner()
        self._session_id = uuid.uuid4().hex[:12]
        self._on_session = 0
        LOG.warning(
            "BROWSER rotating exit IP: session %s retired -> %s (%s)",
            retired, self._session_id, reason or "requested",
        )

    def close(self) -> None:
        """Tear the browser down on its owner thread. Safe to call twice and never raises:
        a teardown failure must not fail a run whose pages were all fetched."""
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
        try:
            self._on_owner(self._close_on_owner)
        except Exception as exc:  # noqa: BLE001 - teardown is best-effort
            LOG.warning("BROWSER teardown failed (ignored): %r", exc)
        finally:
            self._owner.shutdown(wait=True)

    def _close_on_owner(self) -> None:
        """Runs ON the owner thread, so the greenlet dispatcher is the one that started it."""
        for name in ("_context", "_browser"):
            held = getattr(self, name)
            if held is not None:
                setattr(self, name, None)
                try:
                    held.close()
                except Exception as exc:  # noqa: BLE001 - teardown is best-effort
                    LOG.warning("BROWSER %s.close() failed (ignored): %r", name, exc)
        if self._playwright is not None:
            held_pw, self._playwright = self._playwright, None
            try:
                held_pw.stop()
            except Exception as exc:  # noqa: BLE001 - teardown is best-effort
                LOG.warning("BROWSER playwright.stop() failed (ignored): %r", exc)
            reaped, held = reap_browser_orphans()
            used, limit = cgroup.pids()
            LOG.info("BROWSER closed (reaped=%d orphans; %d child processes, %d threads live; "
                     "container pids %s of %s)",
                     reaped, held, threading.active_count(), used, limit)
        self._page = None


def _abort_subresources(route: Any) -> None:
    """HTML only, except the interstitial's own assets — it cannot solve itself without them."""
    request = route.request
    try:
        path = urlsplit(request.url).path
    except ValueError:
        path = ""
    if request.resource_type in _DOCUMENT_TYPES or path.startswith(_CHALLENGE_PATH_PREFIX):
        route.continue_()
        return
    route.abort()

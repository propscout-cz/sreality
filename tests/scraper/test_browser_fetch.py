"""Hermetic tests for scraper.browser_fetch: the sticky-session proxy builder, the
interstitial classifier and the fetch contract.

No chromium and no network. Playwright is imported lazily (only when a browser is
actually launched), so every test here runs without the optional dependency; the
fetch contract is driven through a fake page object.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from scraper.browser_fetch import (
    BrowserFetcher,
    BrowserResponse,
    BrowserTransportError,
    classify_status,
    fetch_through_page,
    is_challenge,
    sticky_proxy,
)

# The real shape ceskereality served on 2026-11-15 (trimmed): HTTP 200 carrying the
# Anubis interstitial, not listings.
CHALLENGE_HTML = (
    '<html><head><title>Ujišťujeme se, že nejste robot!</title></head><body>'
    '<script id="anubis_challenge" type="application/json">'
    '{"rules":{"algorithm":"fast","difficulty":2},"challenge":{"method":"fast"}}'
    "</script></body></html>"
)
REAL_HTML = (
    '<html><body><article class="i-estate">'
    '<a class="i-estate__title-link" href="/prodej/byty/x/y/a-1.html">Byt</a>'
    "</article></body></html>"
)
PROXY = "http://login123:secret456@gw.example.com:823"


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class _FakePage:
    """Enough of a Playwright page for the fetch contract: goto, content, url and
    wait_for_function. `bodies` is the sequence content() returns."""

    def __init__(self, status: int, bodies: list[str], *, solves: bool = True,
                 final_url: str | None = None) -> None:
        self._status = status
        self._bodies = list(bodies)
        self._solves = solves
        self.url = final_url or "https://www.ceskereality.cz/prodej/byty/"
        self.waited = 0
        self.goto_calls: list[str] = []

    def goto(self, url: str, **_kw: object) -> _FakeResponse:
        self.goto_calls.append(url)
        return _FakeResponse(self._status)

    def content(self) -> str:
        return self._bodies.pop(0) if len(self._bodies) > 1 else self._bodies[0]

    def wait_for_function(self, _expr: str, **_kw: object) -> None:
        self.waited += 1
        if not self._solves:
            raise TimeoutError("the interstitial never cleared")


def test_is_challenge_reads_the_interstitial_marker() -> None:
    assert is_challenge(CHALLENGE_HTML) is True
    assert is_challenge(REAL_HTML) is False
    assert is_challenge("") is False


def test_sticky_proxy_pins_one_residential_exit_ip() -> None:
    """A rotating gateway re-challenges every page, so the session id pins one exit IP."""
    proxy = sticky_proxy(PROXY, "run-7", country="cz")
    assert proxy == {
        "server": "http://gw.example.com:823",
        "username": "login123__cr.cz;sessid-run-7",
        "password": "secret456",
    }


def test_sticky_proxy_keeps_a_raw_semicolon() -> None:
    """Measured 2026-11-15: the provider does not url-decode %3B — a percent-encoded
    separator loses the session (and the pinned IP) silently."""
    assert ";sessid-" in sticky_proxy(PROXY, "abc")["username"]
    assert "%3B" not in sticky_proxy(PROXY, "abc")["username"]


def test_sticky_proxy_without_a_proxy_url_is_none() -> None:
    assert sticky_proxy(None, "abc") is None
    assert sticky_proxy("", "abc") is None


def test_classify_turns_a_challenged_200_into_a_retryable_403() -> None:
    """The interstitial must never reach a parser as a 200 'page with no ads' —
    that is what made a walled walk read as an empty index (#2479)."""
    assert classify_status(200, CHALLENGE_HTML) == 403


def test_classify_leaves_a_real_page_and_a_gone_status_alone() -> None:
    assert classify_status(200, REAL_HTML) == 200
    assert classify_status(404, "not found") == 404


def test_fetch_waits_for_the_interstitial_then_returns_the_real_page() -> None:
    page = _FakePage(200, [CHALLENGE_HTML, REAL_HTML])
    result = fetch_through_page(page, "https://www.ceskereality.cz/prodej/byty/")
    assert isinstance(result, BrowserResponse)
    assert (result.status_code, result.text) == (200, REAL_HTML)
    assert page.waited == 1, "a challenged page must wait for the solve"


def test_fetch_does_not_wait_when_the_page_is_already_real() -> None:
    page = _FakePage(200, [REAL_HTML])
    result = fetch_through_page(page, "https://www.ceskereality.cz/prodej/byty/")
    assert (result.status_code, result.text) == (200, REAL_HTML)
    assert page.waited == 0, "an unchallenged page must not pay the solve wait"


def test_fetch_reports_a_failure_when_the_interstitial_never_clears() -> None:
    page = _FakePage(200, [CHALLENGE_HTML], solves=False)
    result = fetch_through_page(page, "https://www.ceskereality.cz/prodej/byty/")
    assert result.status_code == 403, "an unsolved wall is a failed fetch, never content"
    assert is_challenge(result.text)


def test_fetch_passes_a_gone_status_through() -> None:
    page = _FakePage(404, ["nenalezeno"], final_url="https://www.ceskereality.cz/404")
    result = fetch_through_page(page, "https://www.ceskereality.cz/x.html")
    assert result.status_code == 404


def test_browser_response_raise_for_status_mirrors_requests() -> None:
    BrowserResponse(200, "ok", "u").raise_for_status()
    with pytest.raises(Exception):
        BrowserResponse(500, "boom", "u").raise_for_status()


# --- one context, many drain workers ----------------------------------------------

class _SlowPage(_FakePage):
    """Records which thread touched the page, and any overlap.

    Playwright's sync API is thread-AFFINE, not merely thread-unsafe: its dispatcher is a
    greenlet owned by the thread that started it, so a call from any other thread raises
    `greenlet.error: Cannot switch to a different thread` — a lock does not help. Verified
    against real chromium 2026-11-16. So the real requirement is that every page call land
    on ONE thread, which this records.
    """

    def __init__(self) -> None:
        super().__init__(200, [REAL_HTML])
        self.inside = 0
        self.max_inside = 0
        self.threads: set[int] = set()

    def goto(self, url: str, **kw: object) -> _FakeResponse:
        self.threads.add(threading.get_ident())
        self.inside += 1
        self.max_inside = max(self.max_inside, self.inside)
        time.sleep(0.01)
        self.inside -= 1
        return super().goto(url, **kw)

    def content(self) -> str:
        self.threads.add(threading.get_ident())
        return super().content()


def test_every_browser_call_lands_on_one_owner_thread() -> None:
    """The drain hands work to a NEW thread pool per claim chunk and tears down on yet
    another thread, and its worker count is config (the live row asks for more than one),
    so correctness must not depend on who calls. Every call must reach the one owner."""
    page = _SlowPage()
    fetcher = BrowserFetcher(page=page, proxy_url=None)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(3):
                assert fetcher.fetch("https://www.ceskereality.cz/prodej/byty/").status_code == 200
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    # A second wave on fresh threads, as the next claim chunk's pool would be.
    second = [threading.Thread(target=work) for _ in range(2)]
    for thread in second:
        thread.start()
    for thread in second:
        thread.join()

    assert errors == []
    assert len(page.threads) == 1, f"the page was touched from {len(page.threads)} threads"
    assert threading.get_ident() not in page.threads, "the browser ran on a caller's thread"
    assert page.max_inside == 1
    assert len(page.goto_calls) == 18


def test_close_from_a_foreign_thread_is_clean_and_final() -> None:
    """The drain's teardown runs on a different thread than its fetches. Closing the
    browser off-thread raised `greenlet.error`, which the best-effort handler swallowed —
    leaking a chromium every pass. Close must reach the owner, and a closed fetcher must
    refuse work rather than relaunch a browser nothing would ever close."""
    page = _SlowPage()
    fetcher = BrowserFetcher(page=page, proxy_url=None)
    fetcher.fetch("https://www.ceskereality.cz/prodej/byty/")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(fetcher.close).result()
    fetcher.close()  # idempotent

    with pytest.raises(BrowserTransportError):
        fetcher.fetch("https://www.ceskereality.cz/prodej/byty/")


def test_close_without_a_launched_browser_is_safe() -> None:
    BrowserFetcher(page=_FakePage(200, [REAL_HTML]), proxy_url=None).close()
    BrowserFetcher(proxy_url=None).close()


# --- retiring a blocked exit IP -------------------------------------------------------

URL = "https://www.ceskereality.cz/prodej/byty/"


class _RelaunchingFetcher(BrowserFetcher):
    """Records the sticky session each launch would pin, with no real browser.

    `_ensure_page` is the seam a launch goes through, so overriding it here exercises
    rotation honestly: a rotation must CLEAR the page, so the next fetch comes back
    through this and pins the new session.
    """

    def __init__(self, **kw: object) -> None:
        super().__init__(proxy_url=PROXY, **kw)  # type: ignore[arg-type]
        self.launched: list[str] = []

    def _ensure_page(self) -> object:
        if self._page is None:
            self.launched.append(self._session_id)
            self._page = _FakePage(200, [REAL_HTML] * 64)
        return self._page


def test_a_rotation_retires_the_exit_ip_and_relaunches_on_a_new_sticky_session() -> None:
    """One sticky session is ONE residential exit IP. When the portal blocks it, keeping
    it is pointless — every later page gets the same 403 (measured 2026-11-16: a 30-minute
    403 window cost three category groups their walk). Rotating must therefore both pick a
    new session AND drop the browser holding the old one."""
    fetcher = _RelaunchingFetcher()
    fetcher.fetch(URL)
    blocked = fetcher.session_id
    fetcher.rotate("403")
    fetcher.fetch(URL)
    assert fetcher.session_id != blocked
    assert fetcher.launched == [blocked, fetcher.session_id]
    fetcher.close()


def test_a_long_walk_rotates_on_its_own_before_the_portal_blocks_it() -> None:
    """A whole walk on one exit IP is what drew the block, so the fetcher retires its
    session every N pages without waiting to be refused."""
    fetcher = _RelaunchingFetcher(rotate_after=3)
    for _ in range(7):
        fetcher.fetch(URL)
    assert len(fetcher.launched) == 3, fetcher.launched
    assert len(set(fetcher.launched)) == 3, "each stretch must pin a DIFFERENT exit IP"
    fetcher.close()


def test_rotate_after_zero_keeps_one_session_for_the_whole_run() -> None:
    fetcher = _RelaunchingFetcher(rotate_after=0)
    for _ in range(5):
        fetcher.fetch(URL)
    assert fetcher.launched == [fetcher.session_id]
    fetcher.close()


def test_a_rotation_asked_from_a_foreign_thread_runs_on_the_owner() -> None:
    """Playwright is thread-affine, and a rotation CLOSES a browser. The drain calls from
    its own pool threads, so a rotation must reach the owner like every other call."""
    fetcher = _RelaunchingFetcher()
    fetcher.fetch(URL)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            fetcher.rotate("403")
            fetcher.fetch(URL)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    page = fetcher._page
    assert page is not None and len(page.threads if hasattr(page, "threads") else {0}) <= 1
    fetcher.close()


def test_rotating_a_closed_fetcher_is_refused_like_a_fetch() -> None:
    fetcher = _RelaunchingFetcher()
    fetcher.close()
    with pytest.raises(BrowserTransportError):
        fetcher.rotate("403")


def _subreaper(on: bool) -> None:
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(36, int(on), 0, 0, 0)


def _zombie_children() -> set[int]:
    import os
    out = set()
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as fh:
                rest = fh.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if rest[0] == "Z" and int(rest[1]) == os.getpid():
            out.add(int(name))
    return out


@pytest.mark.skipif(not __import__("os").path.isdir("/proc/self"), reason="needs Linux /proc")
def test_an_orphaned_chromium_helper_is_reaped_and_no_other_child_is(tmp_path) -> None:
    """#2565: as PID 1 the worker inherits chromium's orphans; each one held a PID for good."""
    import shutil
    import subprocess

    from scraper.browser_fetch import reap_browser_orphans

    fake = tmp_path / "chrome-headless"
    shutil.copy(shutil.which("true"), fake)
    _subreaper(True)
    try:
        before = _zombie_children()
        # The parent exits without waiting, so its child is handed to us, as to PID 1.
        subprocess.run(["sh", "-c", f"{fake} & exec true"], check=True)
        other = subprocess.Popen(["true"])
        deadline = time.monotonic() + 5
        while len(_zombie_children() - before) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(_zombie_children() - before) == 2
        reaped, _held = reap_browser_orphans()
        assert reaped == 1
        assert _zombie_children() - before == {other.pid}
        assert other.wait() == 0
    finally:
        _subreaper(False)

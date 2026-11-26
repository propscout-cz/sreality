"""scripts/browser_image_check.py: the browser images' offline check (#2031). The real run is CI's, inside the image;
here a fake fetcher stands in for chromium and reads the check's own loopback server over plain HTTP."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from scraper.browser_fetch import BrowserResponse
from scripts import browser_image_check as check


class _Fetcher:
    def __init__(self, *, mangle: bool = False, **_: object) -> None:
        self.mangle = mangle
        self.urls: list[str] = []
        self.closed = False
        self.session = requests.Session()
        self.session.trust_env = False  # loopback, never an environment proxy

    def fetch(self, url: str) -> BrowserResponse:
        self.urls.append(url)
        response = self.session.get(url, timeout=5)
        text = response.text.replace(check.MARKER, "") if self.mangle else response.text
        return BrowserResponse(status_code=response.status_code, text=text, url=url)

    def close(self) -> None:
        self.closed = True


def _install(monkeypatch: pytest.MonkeyPatch, fetcher: _Fetcher) -> None:
    monkeypatch.setattr(check, "BrowserFetcher", lambda **kwargs: fetcher)
    monkeypatch.setattr(check, "_children", lambda procs: [])


def test_a_served_marker_page_passes_and_the_fetcher_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _Fetcher()
    _install(monkeypatch, fetcher)
    assert check.main([]) == 0
    assert len(fetcher.urls) == 1 and fetcher.urls[0].startswith("http://127.0.0.1:")
    assert fetcher.closed


def test_a_page_without_the_marker_fails_and_still_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = _Fetcher(mangle=True)
    _install(monkeypatch, fetcher)
    assert check.main([]) == 1
    assert fetcher.closed


def test_the_memory_reading_loads_the_page_that_many_times(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<html><body>a listing page</body></html>", encoding="utf-8")
    fetcher = _Fetcher()
    _install(monkeypatch, fetcher)
    assert check.main(["--page", str(page), "--pages", "3"]) == 0
    assert [u.rsplit("/", 1)[1] for u in fetcher.urls] == ["", "page?n=1", "page?n=2", "page?n=3"]


def test_a_child_left_after_the_close_fails_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _Fetcher())
    monkeypatch.setattr(check, "_children", lambda procs: ["42:chrome-headless:S"])
    monkeypatch.setattr(check, "TEARDOWN_WAIT_S", 0.0)
    assert check.main([]) == 1


def test_only_this_process_s_children_count() -> None:
    me = check.os.getpid()
    procs = {7: ("chrome-headless", "Z", me), 8: ("node", "S", me), 9: ("chrome", "S", 1)}
    assert check._children(procs) == ["7:chrome-headless:Z", "8:node:S"]

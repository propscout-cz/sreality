"""Hermetic tests for scraper.ceskereality_client: URL building (?strana paging)
+ retry/penalize + the ceskereality gone signals (404, redirect off the .html
detail path, body marker) + the interstitial classification.

No network and no chromium: a fake FETCHER feeds canned responses. Since 2026-10-31
this portal's bytes arrive through a headless browser rather than `requests`
(`scraper.browser_fetch`), so the injection point is `fetcher=`, not `_session` —
the behaviour asserted here is otherwise unchanged. Mirrors test_idnes_client.py.
"""

from __future__ import annotations

import re
import time

import pytest
import requests

from scraper.ceskereality_client import CeskerealityClient, detail_url, index_url
from scraper.portal_base import BasePortalClient, ListingGoneError, client_hints
from scraper.rate_limit import RateLimiter

_DETAIL = (
    "https://www.ceskereality.cz/prodej/byty/byty-1-1/praha/"
    "prodej-bytu-1-1-41-m2-moldavska-3754200.html"
)


class FakeResponse:
    def __init__(self, status_code: int, text: str = "ok", url: str | None = None):
        self.status_code = status_code
        self.text = text
        self.url = url if url is not None else _DETAIL
        self.headers = {"Content-Type": "text/html"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)


class FakeFetcher:
    """Stands in for the headless browser: hands back canned responses and records the
    URLs it was asked for, so retry counting still reads the same way."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[str] = []
        self.closed = 0
        self.rotations: list[str] = []

    def fetch(self, url: str) -> FakeResponse:
        self.calls.append(url)
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed += 1

    def rotate(self, reason: str = "") -> None:
        self.rotations.append(reason)


def _client(responses: list[FakeResponse], **kw) -> CeskerealityClient:
    return CeskerealityClient(fetcher=FakeFetcher(responses), **kw)


def _calls(client: CeskerealityClient) -> list[str]:
    return client._fetcher.calls


def test_index_url_building():
    # page=None / page=1 -> the bare first page; ?strana=N for N>=2.
    assert index_url("prodej", "byty") == "https://www.ceskereality.cz/prodej/byty/"
    assert index_url("prodej", "byty", 1) == "https://www.ceskereality.cz/prodej/byty/"
    assert (
        index_url("prodej", "byty", 2)
        == "https://www.ceskereality.cz/prodej/byty/?strana=2"
    )
    assert (
        index_url("pronajem", "komercni-prostory", 5)
        == "https://www.ceskereality.cz/pronajem/komercni-prostory/?strana=5"
    )


def test_detail_url_building():
    assert (
        detail_url("/prodej/byty/x/y/abc-123.html")
        == "https://www.ceskereality.cz/prodej/byty/x/y/abc-123.html"
    )
    assert detail_url(_DETAIL) == _DETAIL


def test_fetch_index_ok():
    c = _client([FakeResponse(200, "<html>index</html>")])
    text, status = c.fetch_index("prodej", "byty")
    assert text == "<html>index</html>"
    assert status == 200
    assert _calls(c) == ["https://www.ceskereality.cz/prodej/byty/"]


def test_fetch_detail_ok():
    c = _client([FakeResponse(200, "<html>detail</html>", url=_DETAIL)])
    text, status = c.fetch_detail(_DETAIL)
    assert text == "<html>detail</html>"
    assert status == 200


def test_fetch_detail_gone_404_raises_immediately():
    c = _client([FakeResponse(404, "not found")])
    with pytest.raises(ListingGoneError):
        c.fetch_detail(_DETAIL)
    assert len(_calls(c)) == 1  # no retry on a gone status


def test_fetch_detail_redirect_off_html_is_gone():
    # A removed listing redirects to the category results; requests follows it
    # (200) but the final URL is no longer a .html listing page.
    c = _client(
        [FakeResponse(200, "<html>list</html>", url="https://www.ceskereality.cz/prodej/byty/")]
    )
    with pytest.raises(ListingGoneError):
        c.fetch_detail(_DETAIL)


def test_fetch_detail_gone_body_marker():
    c = _client([FakeResponse(200, "Inzerát byl odstraněn.", url=_DETAIL)])
    with pytest.raises(ListingGoneError):
        c.fetch_detail(_DETAIL)


def test_retry_then_success_penalizes_on_429(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    limiter = RateLimiter(1000.0)
    base = limiter.interval
    c = _client(
        [FakeResponse(429), FakeResponse(200, "ok", url=_DETAIL)],
        limiter=limiter, max_retries=2,
    )
    text, status = c.fetch_detail(_DETAIL)
    assert text == "ok"
    assert status == 200
    assert len(_calls(c)) == 2
    assert limiter.interval > base  # penalize widened the interval


def test_retry_exhausted_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    c = _client([FakeResponse(500), FakeResponse(500)], max_retries=1)
    with pytest.raises(requests.HTTPError):
        c.fetch_detail(_DETAIL)
    assert len(_calls(c)) == 2


def test_fetch_detail_archived_page_is_gone():
    """2026-09-07: a removed listing's URL keeps answering 200 with the old H1
    and prices inside `<section class="s-estate-archive">` plus "Nemovitost již
    byla smazána". Presence checks re-fetch removed listings on purpose, so this
    must read as gone, never as a live page to refresh."""
    body = ('<html><section class="base-section s-estate-archive"><h1>Prodej byty 2+1 52 m²</h1>'
            '<div class="overlay">Smazaná nemovitost</div><h2>Nemovitost již byla smazána</h2>'
            '</section></html>')
    c = _client([FakeResponse(200, body, url=_DETAIL)])
    with pytest.raises(ListingGoneError):
        c.fetch_detail(_DETAIL)


def test_sends_chrome_client_hints_matching_its_ua():
    """The client sends the sec-ch-ua hints derived from its Chrome UA."""
    headers = CeskerealityClient()._session.headers
    major = re.search(r"Chrome/(\d+)", headers["User-Agent"]).group(1)
    assert headers["sec-ch-ua"] == client_hints(headers["User-Agent"])["sec-ch-ua"]
    assert f'"Chromium";v="{major}"' in headers["sec-ch-ua"]
    assert f'"Google Chrome";v="{major}"' in headers["sec-ch-ua"]
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["sec-ch-ua-platform"] == '"Windows"'


def test_client_hints_leave_the_other_headers_unchanged():
    ours = CeskerealityClient()._session.headers
    base = BasePortalClient()._session.headers
    assert ours["User-Agent"] == base["User-Agent"]
    assert ours["Accept-Language"] == base["Accept-Language"] == "cs,en;q=0.9"
    assert ours["Accept"] == CeskerealityClient.ACCEPT


# --- the interstitial (2026-10-31 wall; #2479) -------------------------------------

CHALLENGE_HTML = (
    '<html><head><title>Ujišťujeme se, že nejste robot!</title></head><body>'
    '<script id="anubis_challenge" type="application/json">{"rules":{"difficulty":2}}'
    "</script></body></html>"
)


def test_a_challenged_page_is_a_failed_fetch_not_an_empty_index(monkeypatch):
    """The wall answers 200 with challenge HTML. If that reached the parser it would read
    as a page with no ads, which is what stopped walks reaching the portal's end (#2479).
    It must fail the fetch instead, whatever status it was served with."""
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    c = _client([FakeResponse(200, CHALLENGE_HTML)] * 2, max_retries=1)
    with pytest.raises(requests.HTTPError):
        c.fetch_search(index_url("prodej", "byty"))
    assert len(_calls(c)) == 2, "a challenged page is retried like any throttle"


def test_a_challenge_that_clears_on_a_retry_returns_the_real_page(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    c = _client(
        [FakeResponse(200, CHALLENGE_HTML), FakeResponse(200, "<html>index</html>")],
        max_retries=2,
    )
    text, status = c.fetch_search(index_url("prodej", "byty"))
    assert (text, status) == ("<html>index</html>", 200)


def test_a_challenged_page_penalizes_the_shared_limiter(monkeypatch):
    """It is a 403, so the walk backs off the portal rather than hammering the wall."""
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    limiter = RateLimiter(1000.0)
    base = limiter.interval
    c = _client(
        [FakeResponse(200, CHALLENGE_HTML), FakeResponse(200, "<html>ok</html>")],
        limiter=limiter, max_retries=2,
    )
    c.fetch_search(index_url("prodej", "byty"))
    assert limiter.interval > base


def test_close_releases_the_browser_and_is_idempotent():
    c = _client([FakeResponse(200, "<html>ok</html>")])
    fetcher = c._fetcher
    c.close()
    c.close()
    assert fetcher.closed == 1, "closed once, and a second close must not raise"


def test_no_browser_is_launched_until_the_first_fetch():
    """A run that never reaches this portal must pay no browser cost, and constructing a
    client must not need playwright installed."""
    assert CeskerealityClient()._fetcher is None


def test_a_run_of_refusals_retires_the_exit_ip() -> None:
    """A 403 here is the portal refusing the IP, NOT an unsolved interstitial (the browser
    solved 1,558 of 1,558 on 2026-11-16; the refusals were nginx, in front of Anubis).
    Retrying the same blocked exit IP is what turned one block into a 30-minute outage, so
    a run of them retires the session and the next attempt leaves on a fresh IP."""
    c = _client([FakeResponse(403, "<html>nope</html>")] * 4)
    with pytest.raises(Exception):
        c.fetch_index("prodej", "byty")
    assert c._fetcher.rotations, "a blocked exit IP must be retired, not retried forever"


def test_a_single_refusal_does_not_retire_the_exit_ip() -> None:
    """One 403 among successes is noise; rotating on it would throw away a good IP and pay
    a fresh interstitial solve for nothing."""
    c = _client([FakeResponse(403, "<html>nope</html>"), FakeResponse(200, "<html>index</html>")])
    c.fetch_index("prodej", "byty")
    assert c._fetcher.rotations == []


def test_a_gone_status_never_retires_the_exit_ip() -> None:
    """404/410 is the LISTING speaking, not the portal refusing us — rotating would be
    wrong and would cost a solve on every delisted ad the drain verifies."""
    c = _client([FakeResponse(404, "<html>gone</html>")])
    with pytest.raises(Exception):
        c.fetch_index("prodej", "byty")
    assert c._fetcher.rotations == []


def _intro(*badges: str) -> str:
    items = "".join(f'<li> <div class="badge --gray"> <span class="badge__title">{b}</span> </div> </li>'
                    for b in badges)
    return (f'<div class="s-estate-detail-intro__badges"> <ul class="g-badges --horizontal"> {items} '
            '</ul> </div> <header class="s-estate-detail-intro__header"><h1>Prodej bytu</h1></header>')


# The "Podobné nemovitosti" slider: other ads' badges, below the ad's own intro.
_SLIDER = ('<section class="s-estates-slide"><article class="i-estate"><div class="i-estate__image-badges">'
           '<div class="badge --gray"> <span class="badge__title">Prodáno</span> </div></div></article></section>')


@pytest.mark.parametrize("badges", [("Prodáno",), ("Pronajato",), ("Developerský projekt", "Prodáno")])
def test_a_page_whose_own_intro_badge_says_sold_or_let_is_gone(badges):
    c = _client([FakeResponse(200, f"<html>{_intro(*badges)}</html>", url=_DETAIL)])
    with pytest.raises(ListingGoneError):
        c.fetch_detail(_DETAIL)


@pytest.mark.parametrize("badges", [(), ("Rezervováno",), ("Novinka",)])
def test_a_live_or_reserved_page_beside_a_sold_slider_card_is_returned(badges):
    body = f"<html>{_intro(*badges)}<p>Hala 1 je již prodána.</p>{_SLIDER}</html>"
    c = _client([FakeResponse(200, body, url=_DETAIL)])
    assert c.fetch_detail(_DETAIL) == (body, 200)

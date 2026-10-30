"""mmreality_client: the removed-listing markers must match the portal's typography."""
from types import SimpleNamespace

import pytest

from scraper.mmreality_client import MmRealityClient
from scraper.portal_base import ListingGoneError


def _client(monkeypatch, body: str) -> MmRealityClient:
    monkeypatch.delenv(MmRealityClient.PROXY_ENV, raising=False)
    client = MmRealityClient()
    monkeypatch.setattr(
        client, "_request",
        lambda url, **kw: SimpleNamespace(url=url, text=body, status_code=200))
    return client


def test_fetch_detail_reads_the_banner_with_a_no_break_space_as_gone(monkeypatch):
    """Since 2026-10-27 the portal renders "již není v\\xa0nabídce" (no-break space
    after the preposition); 2026-10-27..30 that page was written as a live listing
    because the plain-space marker never matched."""
    body = ('<h3 class="tw-typo-h3">Je nám líto, ale tato nemovitost již není v\xa0nabídce '
            'M&amp;M\xa0Reality.</h3><script>{"id":957752,"active":true}</script>')
    with pytest.raises(ListingGoneError):
        _client(monkeypatch, body).fetch_detail("957752")


def test_fetch_detail_returns_a_live_page(monkeypatch):
    body = '<h1>Pronájem garáže, 17 m²</h1><script>{"id":956315,"active":true}</script>'
    text, status = _client(monkeypatch, body).fetch_detail("956315")
    assert (text, status) == (body, 200)

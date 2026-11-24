"""Hermetic tests for the remax client: URL builders + the detail page's gone signals (no network)."""

from __future__ import annotations

import pytest

from scraper.portal_base import ListingGoneError
from scraper.remax_client import RemaxClient, detail_url, index_url


def test_index_url_sale_and_pagination():
    # Every agenda page asks for the ads on offer only (`sold=on`); page 1 carries no stranka.
    base = "https://www.remax-czech.cz/reality/vyhledavani/"
    assert index_url(sale=1) == f"{base}?sale=1&sold=on"
    assert index_url(sale=2) == f"{base}?sale=2&sold=on"
    assert index_url(sale=1, stranka=1) == f"{base}?sale=1&sold=on"
    assert index_url(sale=1, stranka=3) == f"{base}?sale=1&sold=on&stranka=3"
    assert index_url(sale=2, stranka=2) == f"{base}?sale=2&sold=on&stranka=2"
    assert index_url() == f"{base}?sold=on"


def test_detail_url_forms():
    full = "https://www.remax-czech.cz/reality/detail/440872/prodej-bytu"
    assert detail_url(full) == full
    assert detail_url("/reality/detail/440872/prodej-bytu") == full
    assert detail_url("440872") == "https://www.remax-czech.cz/reality/detail/440872/"


_URL = "https://www.remax-czech.cz/reality/detail/443897/prodej-domu-815-m2-slany"


def _header(text: str) -> str:
    # The live markup, whitespace included (2026-11-24).
    return ('<h2 class="pd-header__price">\n\t\t\t\t\t\t\t\t' + text
            + ' \n\t\t\t\t\t\t\t\t<div class="pd-header__price-energy-rating">')


# A let ad's "Podobné nemovitosti" block and map data, as a live page carries them for its
# sold/let neighbours.
_SIMILAR_LET = (
    '<div class="row similar-property__items"><a class="col-md-4" href="https://www.remax-czech.cz'
    '/reality/detail/352361/pronajem-bytu-1-kk"><div class="similar-property__item">'
    '<div class="similar-property__item-img"><div class="tags "> <span class="tags__item '
    'tags__item--sold">Pronajato</span> </div></div><div class="similar-property__item-text">'
    '<p> Na žertvách, Libeň, Praha 8<br/> Pronajato </p></div></div></a></div>'
    '<div data-url="/reality/detail/348271/pronajem-bytu-1-kk" data-price="Pronajato"></div>'
)


class _Response:
    def __init__(self, text: str, url: str) -> None:
        self.status_code = 200
        self.text = text
        self.url = url


def _client(text: str, final_url: str = _URL) -> RemaxClient:
    client = object.__new__(RemaxClient)
    client._request = lambda url: _Response(text, final_url)  # noqa: SLF001
    return client


@pytest.mark.parametrize("status", ["Prodáno", "Pronajato"])
def test_a_page_whose_own_price_header_says_sold_or_let_is_gone(status):
    with pytest.raises(ListingGoneError):
        _client(f"<html>{_header(status)}</html>").fetch_detail(_URL)


def test_a_live_page_beside_sold_neighbours_is_returned():
    body = "<html>" + _header("3\xa0700\xa0000\xa0Kč <small>(za nemovitost)</small>") + _SIMILAR_LET
    text, status = _client(body).fetch_detail(_URL)
    assert status == 200 and text == body


def test_a_redirect_off_the_detail_path_is_gone():
    with pytest.raises(ListingGoneError):
        _client("<html>results</html>", "https://www.remax-czech.cz/reality/vyhledavani/").fetch_detail(_URL)

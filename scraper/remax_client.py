"""HTTP layer for remax-czech.cz (portal framework).

RE/MAX publishes its franchise catalogue as a server-rendered listing site (no
JSON API), so this returns raw HTML for `scraper.remax_parser`. The shared
retry/backoff + adaptive throttle (`RateLimiter` + `penalize()` on 429/403) +
`ListingGoneError` on a 404/410 all live in `scraper.portal_base.BasePortalClient`;
this client adds only the HTML `Accept` header, the remax URL builders (a single
mixed search index paged `?sale={1|2}&stranka=N`, a `/reality/detail/{id}/{slug}`
detail), and its gone declarations. A removed remax ad answers 410; a sold or let
one keeps answering 200 with "Prodáno"/"Pronajato" in its own price header.
"""

from __future__ import annotations

import re

from scraper.portal_base import BasePortalClient, stated_closed

BASE_URL = "https://www.remax-czech.cz"
SEARCH_PATH = "/reality/vyhledavani/"


def index_url(sale: int | None = None, stranka: int | None = None) -> str:
    """The mixed-category search index. `sale` is remax's offer-type flag (1 =
    prodej / sale, 2 = pronájem / rent); `stranka` is the 1-based page (omitted
    for page 1, which is the bare filtered URL)."""
    # `sold=on` drops the sold/let reference cards the plain search lists after the
    # ads on offer (1,735 of 9,759 on 2026-11-24): a card of an ad nobody can buy is
    # no sighting (rule #3). The ads its total counts are the ones on offer.
    params: list[str] = []
    if sale is not None:
        params.append(f"sale={sale}")
    params.append("sold=on")
    if stranka is not None and stranka >= 2:
        params.append(f"stranka={stranka}")
    return f"{BASE_URL}{SEARCH_PATH}" + (f"?{'&'.join(params)}" if params else "")


def detail_url(path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    if path_or_url.startswith("/"):
        return f"{BASE_URL}{path_or_url}"
    return f"{BASE_URL}/reality/detail/{path_or_url}/"


class RemaxClient(BasePortalClient):
    ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    DETAIL_URL = re.compile(r"/reality/detail/")
    GONE_PAGE = (stated_closed(r'<h2 class="pd-header__price">'),)

    def fetch_index(
        self, sale: int | None = None, stranka: int | None = None
    ) -> tuple[str, int]:
        response = self._request(index_url(sale, stranka))
        return response.text, response.status_code

    def fetch_detail(self, path_or_url: str) -> tuple[str, int]:
        return self._fetch_detail_page(detail_url(path_or_url))

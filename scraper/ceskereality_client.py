"""HTTP layer for ceskereality.cz (portal framework).

ceskereality.cz is a server-rendered listing site (no public JSON API — its
/real-estate/ajax/* endpoints are filter/autocomplete helpers, not a listings
feed), so this returns raw HTML for `scraper.ceskereality_parser`. The shared
retry/backoff + adaptive throttle (`RateLimiter` + `penalize()` on 429/403) +
`ListingGoneError` on a 404/410 all live in `scraper.portal_base.BasePortalClient`.

Four ceskereality specifics live here:
  - **Headless-browser fetch** (`_send`): since 2026-10-31 every page sits behind a JS
    proof-of-work interstitial that answers 200 with challenge HTML, so `requests`
    collects nothing and a walk reads each page as empty (#2479). `scraper.browser_fetch`
    solves it once per session and the context carries the cookie; an unsolved page comes
    back as a retryable 403, never as content. The browser is launched on the first fetch
    and released by `close()`.
  - **Residential egress** (`USE_PROXY`): every request routes through the
    residential proxy in `SCRAPER_PROXY_URL`, with the shared BROWSER User-Agent. Still
    mandatory with the browser: a datacenter IP is refused before the interstitial runs.
  - **Client hints** (`CLIENT_HINTS`): this client sends the sec-ch-ua client
    hints, derived from the shared UA.
  - **The kraj search URLs**: the 12-page cap is NOT site-wide — it applies to
    UNFILTERED category URLs only (`/prodej/byty/?strana=13` 404s, but
    `/prodej/byty/stredocesky-kraj/?strana=13` is fine and that slice pages to 50
    of 50). A *filtered* URL caps at 99 pages (1,980 rows; `?strana=100` = 404).
    So the walk partitions each category by the 14 declared `KRAJ_SLUGS`
    (`search_url`), descending onto a subtype axis and then an okres axis only
    where a slice would exceed 99 pages. Detail pages are always fetched on www.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode, urlsplit

from scraper.browser_fetch import BrowserFetcher, BrowserResponse, classify_status
from scraper.portal import CZ_KRAJ_SLUGS
from scraper.portal_base import BasePortalClient, ListingGoneError

LOG = logging.getLogger(__name__)

# Consecutive 403s before the exit IP is retired. Two, not one: a lone refusal among
# successes is noise, and rotating on it discards a working IP and pays a fresh
# interstitial solve for nothing.
REFUSALS_BEFORE_ROTATE = 2
# Pages one exit IP serves before it is retired unasked. The 2026-11-16 walk put all
# 1,558 pages of a walk on ONE IP and was blocked for 30 minutes partway through.
PAGES_PER_EXIT_IP = 250

BASE_URL = "https://www.ceskereality.cz"

# The 14 modern kraje — a ROW-LEVEL partition, proved by ID enumeration
# (2026-08-27): a 48-row category yielded 48 ids across these 14 with zero
# overlap and zero gap, and every category's kraj sum matched its national
# declared total inside the live drift band. DECLARED, never scraped: the
# rendered facet list is a top-10-by-popularity list, not a partition (whole
# okresy never appear in it).
# TRAP: 'kraj-vysocina' is irregular ('vysocina-kraj' and 'vysocina' both 404).
# TRAP: 'praha' 301-redirects to 'praha-hlavni-mesto' (query string preserved);
#       requests follows it, so the slice is correct — it just costs a hop.
# TRAP: the site also serves 7 LEGACY regions (severoceský, východoceský, …)
#       and 7 macro-region SUBDOMAINS. Mixing vocabularies double-counts.
# TRAP: /zahranicni/ is a separate tree outside the national CZ totals
#       (12,942 flats vs 8,855 national). Never walk it from here.
# The tuple itself is shared (scraper.portal.CZ_KRAJ_SLUGS): idnes publishes the
# same 14 slugs, character for character, so a second copy would only be a second
# place to get `kraj-vysocina` wrong. The TRAPs above stay here because they are
# about THIS site's URL space, not about the vocabulary.
KRAJ_SLUGS: tuple[str, ...] = CZ_KRAJ_SLUGS

# The second axis, used ONLY when a kraj slice would exceed the 99-page cap.
# Declared per category, never scraped: a kraj page's facet block OMITS
# zero-count subtypes (`ostatni-rd` is absent from stredocesky), so walking the
# rendered list would silently drop a subtype the moment it emptied. Every entry
# is a measured TRUE partition of a kraj (the slugs' declared counts sum to the
# kraj total exactly). Categories absent from this map fall back to the kraj
# page's own `/{sale}/{cat}/{subtype}/{kraj}/` links
# (`ceskereality_parser.extract_subtype_slugs`), and the descent self-verifies its
# children's declared sum against the parent either way.
SUBTYPE_SLUGS: dict[str, tuple[str, ...]] = {
    # 2026-08-27: these 10 sum to 2,312 in stredocesky-kraj, the kraj total.
    "rodinne-domy": (
        "rodinne-domy", "chaty", "chalupy", "cinzovni-domy",
        "zemedelske-usedlosti", "vily", "na-klic", "dvougeneracni-domy",
        "historicke-objekty", "ostatni-rd",
    ),
    # 2026-10-13: prodej/praha 2,161 and pronajem/praha 1,229, each = the sum of
    # these 12 (diff 0). Praha prodej crossed 1,980 on 2026-09-16 and, unmapped,
    # held byt/prodej un-nominated for 89 walks. TRAP: `atypicke-byty` and `pokoj`
    # do not start with `byty-`. TRAP: `pokoj` 301s to `pokoje`
    # (/prodej/byty/pokoje/praha-hlavni-mesto/, 2026-10-13); the page links the
    # alias, so keep it in step with `extract_subtype_slugs`.
    "byty": (
        "byty-1-kk", "byty-1-1", "byty-2-kk", "byty-2-1", "byty-3-kk",
        "byty-3-1", "byty-4-kk", "byty-4-1", "byty-5-kk", "byty-5-1-vetsi",
        "atypicke-byty", "pokoj",
    ),
    # 2026-10-13: prodej/stredocesky-kraj 1,976 = the sum of these 8 (diff 0) —
    # four rows under the 1,980 ceiling, mapped before it crosses.
    "pozemky": (
        "stavebni-parcely", "orna-puda", "ostatni-pozemky", "komercni",
        "zahrady", "louky", "lesy", "vodni-plochy",
    ),
}

# The THIRD axis, used ONLY when a subtype child of a kraj is itself past the
# 99-page cap: the okresy (districts) of that kraj, from RÚIAN (76 + Praha). The
# site takes an okres slug in the kraj slot of the same grammar `search_url`
# builds (/prodej/rodinne-domy/rodinne-domy/praha-vychod/ -> H1 "Rodinné domy na
# prodej v okrese Praha-východ"). DECLARED, never scraped: a kraj page's
# one-segment facet block lists only ~10 okresy by popularity (stredocesky omits
# kutna-hora and nymburk). Slug = the RÚIAN name lowercased, diacritics stripped,
# spaces to hyphens. MEASURED 2026-10-13 on prodej/rodinne-domy/rodinne-domy (116
# GETs, all HTTP 200, no redirect, every H1 "… v okrese <Name>"): in each of the
# 13 kraje the okres totals sum to the kraj child's EXACTLY (diff 0, child read
# before and after) — the per-kraj figures below. 8,369 rows in all. That is ONE
# category: any other relies on the per-level self-verify (region-filed ads with
# no okres exist — see Praha below), which reads a short sum as residue.
# `praha` is deliberately ABSENT: praha-1..praha-10 summed to 2,148 of prodej/byty
# 2,161 on 2026-10-13 (13 short), so they are not a partition, and no Praha subtype
# is near the ceiling (largest byty-2-kk 638 = 32 pages). An over-ceiling Praha
# child stays `ceiling` and logs "DESCENT no okres axis" — the map it is owed.
OKRES_SLUGS: dict[str, tuple[str, ...]] = {
    # child 1,918 = okres sum
    "stredocesky-kraj": (
        "benesov", "beroun", "kladno", "kolin", "kutna-hora", "melnik",
        "mlada-boleslav", "nymburk", "praha-vychod", "praha-zapad", "pribram",
        "rakovnik",
    ),
    # child 947 = okres sum
    "jihocesky-kraj": (
        "ceske-budejovice", "cesky-krumlov", "jindrichuv-hradec", "pisek",
        "prachatice", "strakonice", "tabor",
    ),
    # child 654 = okres sum
    "plzensky-kraj": (
        "domazlice", "klatovy", "plzen-jih", "plzen-mesto", "plzen-sever",
        "rokycany", "tachov",
    ),
    # child 307 = okres sum
    "karlovarsky-kraj": ("cheb", "karlovy-vary", "sokolov"),
    # child 714 = okres sum
    "ustecky-kraj": (
        "chomutov", "decin", "litomerice", "louny", "most", "teplice",
        "usti-nad-labem",
    ),
    # child 396 = okres sum
    "liberecky-kraj": ("ceska-lipa", "jablonec-nad-nisou", "liberec", "semily"),
    # child 456 = okres sum
    "kralovehradecky-kraj": (
        "hradec-kralove", "jicin", "nachod", "rychnov-nad-kneznou", "trutnov",
    ),
    # child 445 = okres sum
    "pardubicky-kraj": ("chrudim", "pardubice", "svitavy", "usti-nad-orlici"),
    # child 382 = okres sum
    "kraj-vysocina": (
        "havlickuv-brod", "jihlava", "pelhrimov", "trebic", "zdar-nad-sazavou",
    ),
    # child 839 = okres sum
    "jihomoravsky-kraj": (
        "blansko", "breclav", "brno-mesto", "brno-venkov", "hodonin", "vyskov",
        "znojmo",
    ),
    # child 423 = okres sum
    "olomoucky-kraj": ("jesenik", "olomouc", "prerov", "prostejov", "sumperk"),
    # child 559 = okres sum
    "moravskoslezsky-kraj": (
        "bruntal", "frydek-mistek", "karvina", "novy-jicin", "opava",
        "ostrava-mesto",
    ),
    # child 329 = okres sum
    "zlinsky-kraj": ("kromeriz", "uherske-hradiste", "vsetin", "zlin"),
}

# Substrings ceskereality serves (HTTP 200) for a listing no longer offered.
_GONE_MARKERS: tuple[str, ...] = (
    "nemovitost nebyla nalezena",
    "inzerát byl odstraněn",
    "nabídka již není aktivní",
    "tato nabídka již není",
    # The archived page (2026-09-07): a removed listing's URL keeps answering
    # 200 with its old H1 and prices inside `<section class="s-estate-archive">`,
    # an overlay "Smazaná nemovitost" and "Nemovitost již byla smazána". Presence
    # checks re-fetch removed listings on purpose, so without these three every
    # archived listing read as alive and would never close.
    "nemovitost již byla smazána",
    "smazaná nemovitost",
    "s-estate-archive",
)


def search_url(
    sale_type: str,
    category: str,
    *,
    kraj: str | None = None,
    subtype: str | None = None,
    page: int | None = None,
) -> str:
    """`https://www.ceskereality.cz/{sale}/{cat}[/{subtype}][/{kraj}]/[?strana=N]`.

    Subtype precedes kraj — live-verified 2026-08-27:
    /prodej/byty/byty-2-1/stredocesky-kraj/ is correctly filtered (105 results).
    Page 1 is the bare URL; `?strana=N` for N>=2."""
    path = f"/{sale_type}/{category}/"
    if subtype:
        path += f"{subtype}/"
    if kraj:
        path += f"{kraj}/"
    url = f"{BASE_URL}{path}"
    if page is not None and page >= 2:
        url += f"?strana={page}"
    return url


def index_url(sale_type: str, category: str, page: int | None = None) -> str:
    """Back-compat nationwide (www) search URL."""
    return search_url(sale_type, category, page=page)


def detail_url(path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    return f"{BASE_URL}{path_or_url}"


class CeskerealityClient(BasePortalClient):
    ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    # Route through the residential proxy (SCRAPER_PROXY_URL) with the shared
    # browser UA, so no USER_AGENT override.
    USE_PROXY = True
    # Send the sec-ch-ua hints derived from that UA.
    CLIENT_HINTS = True
    # Read by the worker (`_skip_for_browser`): this portal's bytes arrive through a
    # headless browser, so without playwright installed its lanes must be skipped rather
    # than spend every queue row's attempts on a fetch that can only raise.
    USE_BROWSER = True

    def __init__(self, *, fetcher: Any | None = None, **kwargs: Any) -> None:
        """`fetcher` replaces the headless browser (tests inject a fake; nothing else
        does). Left None it is built on the first fetch, so a run that never reaches this
        portal launches no browser."""
        super().__init__(**kwargs)
        self._fetcher = fetcher
        self._refusals = 0

    def _browser(self) -> Any:
        if self._fetcher is None:
            # The same Chrome UA the session advertises: the interstitial records the UA
            # it issued its cookie to, so the two must not disagree.
            self._fetcher = BrowserFetcher(
                proxy_url=os.environ.get(self.PROXY_ENV),
                user_agent=self._session.headers.get("User-Agent"),
                timeout_s=self.timeout_s,
                rotate_after=PAGES_PER_EXIT_IP,
            )
        return self._fetcher

    def _note_refusal(self, status: int, url: str) -> None:
        """Retire the exit IP after a RUN of refusals.

        A 403 here is the portal refusing this residential IP — the interstitial is a
        separate thing and the browser solves it (1,558 of 1,558 on 2026-11-16; every one
        of that walk's 403s was nginx, in front of Anubis). Retrying a blocked IP only
        draws the same refusal, which is how one block cost three category groups a whole
        walk. One 403 is noise, so the rotation waits for a run of them; a 404/410 is the
        LISTING speaking and never counts."""
        if status != 403:
            self._refusals = 0
            return
        self._refusals += 1
        if self._refusals < REFUSALS_BEFORE_ROTATE:
            return
        self._refusals = 0
        fetcher = self._fetcher
        rotate = getattr(fetcher, "rotate", None)
        if rotate is None:
            return
        try:
            rotate(f"{REFUSALS_BEFORE_ROTATE} refusals, last {url}")
        except Exception as exc:  # noqa: BLE001 - a failed rotation must not end the walk
            LOG.warning("BROWSER rotation failed (ignored): %r", exc)

    def _send(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Fetch through the headless browser instead of `requests`: every page of this
        portal sits behind a JS proof-of-work interstitial (`scraper.browser_fetch`). The
        shared loop still does the pacing, retrying, penalizing and gone detection — a
        page that stays challenged comes back as a 403 and is retried, then fails, so it
        can never reach the parser as a page with no ads."""
        if method != "GET" or json_body is not None:
            raise ValueError("ceskereality is fetched with GET only")
        if params:
            url = f"{url}{'&' if urlsplit(url).query else '?'}{urlencode(params)}"
        response = self._browser().fetch(url)
        # Belt and braces: the fetcher classifies too, but the invariant that an
        # interstitial is a FAILED FETCH and never content is enforced here, at the seam
        # every caller goes through, whatever fetcher is in place.
        status = classify_status(response.status_code, response.text)
        self._note_refusal(status, url)
        if status != response.status_code:
            LOG.warning(
                "CHALLENGE unsolved url=%s served=%d -> retryable %d",
                url, response.status_code, status,
            )
            return BrowserResponse(
                status_code=status, text=response.text,
                url=getattr(response, "url", url) or url,
            )
        return response

    def close(self) -> None:
        if self._fetcher is not None:
            self._fetcher.close()
            self._fetcher = None

    def fetch_search(self, url: str) -> tuple[str, int]:
        """Fetch one search-results page (any region host / facet path / page)."""
        response = self._request(url)
        return response.text, response.status_code

    def fetch_index(
        self, sale_type: str, category: str, page: int | None = None
    ) -> tuple[str, int]:
        return self.fetch_search(index_url(sale_type, category, page))

    def fetch_detail(self, path_or_url: str) -> tuple[str, int]:
        url = detail_url(path_or_url)
        response = self._request(url)
        # A removed listing redirects off its .html detail page (to the category
        # results); after requests follows it the status is 200 but the URL is
        # no longer a listing page.
        final_url = getattr(response, "url", url) or url
        if ".html" not in final_url:
            raise ListingGoneError(url, response.status_code)
        text = response.text
        if any(marker in text.lower() for marker in _GONE_MARKERS):
            raise ListingGoneError(url, response.status_code)
        return text, response.status_code

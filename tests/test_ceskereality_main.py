"""The ceskereality 14-kraj index partition + the opt-in residential proxy.

What this file used to test — a 7-subdomain × rendered-facet fan-out capped at 12
pages — was the defect, not the contract: the facet block is a top-10-by-popularity
list, so whole okresy were never visited and the walk collected 7,566 of a declared
8,828 while the completeness gate suppressed every delisting sweep. The 12-page cap
turned out to belong to UNFILTERED category URLs only. The walk now partitions on
the 14 DECLARED kraje, pages each slice to its own declared tail (up to the site's
real 99-page ceiling on a filtered URL), and descends onto the subtype axis when a
kraj needs more than that.

The 5th element of walk_category is STRUCTURAL since 2026-09-08 (rule #3): it says
the walk reached ceskereality's OWN end — every kraj stopped on the pager, on the
region's declared tail, or on a confirmed-empty region — not that the counts
reconciled. A region one row short of its declared count still reaches the end;
a barren page, an error, a cap or the deadline still does not. The arithmetic is
unchanged and still decides each slice's logged `outcome` and the subtype descent.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from scraper import ceskereality_main as m
from scraper import portal as m_portal
from scraper.ceskereality_client import (
    KRAJ_SLUGS,
    OKRES_SLUGS,
    SUBTYPE_SLUGS,
    CeskerealityClient,
    search_url,
)
from scraper.portal import default_config


def _page_html(
    total: int | None, ids: list[str], facets: tuple[str, ...] = (),
    next_page: int | None = None, heading: str | None = None,
) -> str:
    cards = "".join(
        '<article class="i-estate">'
        f'<a class="i-estate__image-link" href="/prodej/byty/x/y-{i}.html"></a>'
        "</article>"
        for i in ids
    )
    facet_links = "".join(f'<a href="/prodej/byty/{s}/">x</a>' for s in facets)
    meta = f'<meta name="description" content="Máme tady {total} bytů">' if total else ""
    # The site's real last page still renders the arrow, carrying `--disabled` —
    # that marker is what says "there is no more". A page with NO pagination block
    # is a truncated body, and the walk must not read the two alike.
    pager = (
        f'<a class="pagination-arrow --next" href="/x/?strana={next_page}"></a>'
        if next_page
        else '<a class="pagination-arrow --disabled --next" href="/x/?strana=1"></a>'
    )
    h1 = f"<h1>{heading}</h1>" if heading else ""
    return (
        f"<html><head>{meta}</head><body>{h1}{cards}{facet_links}{pager}</body></html>"
    )


def _kraj_of(url: str) -> str | None:
    """The kraj segment of a slice URL, for asserting what a walk actually touched."""
    for slug in KRAJ_SLUGS:
        if f"/{slug}/" in url:
            return slug
    return None


class _PartitionClient:
    """A whole category as a dict of {kraj: declared_count}, paged 20 to a page
    with ids derived from (kraj, page) — i.e. a real partition, so the union over
    the 14 kraje is exactly the sum of their counts."""

    def __init__(self, counts: dict[str, int], national: int | None = None) -> None:
        self.urls: list[str] = []
        self._counts = counts
        self._national = national if national is not None else sum(counts.values())

    def fetch_search(self, url):  # noqa: ANN001
        self.urls.append(url)
        kraj = _kraj_of(url)
        total = self._counts.get(kraj, 0)
        pg = _page_num(url)
        if total == 0:
            # The verified empty-slice signature: 200, a correct H1, zero cards,
            # and NO "Máme tady N" phrase anywhere on the page.
            return _page_html(None, [], heading=f"Prodej bytů {kraj}"), 200
        first = (pg - 1) * 20
        ids = [str(1_000_000 + KRAJ_SLUGS.index(kraj) * 100_000 + first + k)
               for k in range(max(0, min(20, total - first)))]
        last = max(1, -(-total // 20))
        return _page_html(
            total, ids, next_page=pg + 1 if pg < last else None,
            heading=f"Prodej bytů {kraj}"), 200

    def fetch_index(self, sale_type, cat, page):  # noqa: ANN001
        return _page_html(self._national, []), 200


def _walk(portal, client, monkeypatch, **kw):
    monkeypatch.setattr(m, "CeskerealityClient", lambda **k: client)
    return portal.walk_category(
        {"sale_type": "prodej", "category": "byty"},
        conn=None, dry_run=True, limiter=None, **kw,
    )


def _page_num(url: str) -> int:
    mm = re.search(r"strana=(\d+)", url)
    return int(mm.group(1)) if mm else 1


# --- the declared partition ------------------------------------------------


def test_kraj_table_is_declared_complete_and_spells_vysocina_irregularly():
    # 14 modern kraje, DECLARED — never scraped off a page. The two traps this
    # pins: kraj-vysocina's irregular slug (vysocina-kraj and vysocina both 404),
    # and the LEGACY 7-region vocabulary, which double-counts if mixed in.
    assert len(KRAJ_SLUGS) == len(set(KRAJ_SLUGS)) == 14
    assert "kraj-vysocina" in KRAJ_SLUGS
    assert "vysocina-kraj" not in KRAJ_SLUGS and "vysocina" not in KRAJ_SLUGS
    for legacy in ("severocesky", "vychodocesky", "zapadocesky", "severomoravsky",
                   "jihomoravsky", "stredocesky"):
        assert legacy not in KRAJ_SLUGS, f"legacy region {legacy} would double-count"
    assert "zahranicni" not in KRAJ_SLUGS


def test_full_walk_visits_all_fourteen_kraje_and_no_foreign_tree(monkeypatch):
    counts = {k: 40 for k in KRAJ_SLUGS}
    fake = _PartitionClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, _counts, total, _pages, complete = _walk(portal, fake, monkeypatch)

    assert {_kraj_of(u) for u in fake.urls if _kraj_of(u)} == set(KRAJ_SLUGS)
    assert len(seen) == 14 * 40
    assert total == 14 * 40
    assert complete is True
    # never the foreign tree, never a legacy region, never a macro subdomain
    for u in fake.urls:
        assert "zahranicni" not in u
        assert "severo." not in u and "vychodo." not in u and "moravskereality" not in u


def test_a_slice_pages_far_past_twelve(monkeypatch):
    # /prodej/byty/praha/?strana=93 returns the declared tail: the 12-page cap
    # was never a law about filtered URLs, and the old code stopped at 12.
    counts = {k: 20 for k in KRAJ_SLUGS}
    counts["praha"] = 1843
    fake = _PartitionClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, _c, _t, pages, complete = _walk(portal, fake, monkeypatch)

    praha_pages = [_page_num(u) for u in fake.urls if "/praha/" in u]
    assert max(praha_pages) == 93            # ceil(1843/20), the declared tail
    assert 94 not in praha_pages             # ...and never the 404 past it
    assert len(seen) == 1843 + 13 * 20
    assert pages >= 93
    assert complete is True


def test_a_kraj_with_no_listings_is_a_valid_slice(monkeypatch):
    # Reproduced live on pronajem/chaty-chalupy in karlovarsky + olomoucky: 200,
    # a correct H1, zero cards, and no "Máme tady N" phrase AT ALL. Read as a
    # fetch failure it would suppress every sweep forever.
    counts = {k: 40 for k in KRAJ_SLUGS}
    counts["karlovarsky-kraj"] = 0
    counts["olomoucky-kraj"] = 0
    fake = _PartitionClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)

    assert len(seen) == 12 * 40
    assert complete is True                  # empty is an ANSWER, not a failure
    empty = portal._walk_slice(fake, "prodej", "byty", "karlovarsky-kraj")
    assert (empty.outcome, empty.declared_total, empty.rows) == ("exhausted", 0, [])
    assert (empty.stop, empty.reached_end) == ("empty_confirmed", True)


class _DegradedClient(_PartitionClient):
    """The real throttle vector: a 200 with zero cards and no total — but the H1
    does NOT name the kraj we asked for, because the page is not that slice."""

    def fetch_search(self, url):  # noqa: ANN001
        if _kraj_of(url) == "ustecky-kraj":
            self.urls.append(url)
            return _page_html(None, [], heading="Reality na prodej"), 200
        return super().fetch_search(url)


def test_degraded_zero_card_page_is_not_a_finished_slice(monkeypatch):
    fake = _DegradedClient({k: 40 for k in KRAJ_SLUGS})
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)

    bad = portal._walk_slice(fake, "prodej", "byty", "ustecky-kraj")
    assert bad.outcome == "degraded"
    assert bad.positive is False
    # An items-less 200 nobody could corroborate is OURS, never the portal's end.
    assert (bad.stop, bad.reached_end) == ("barren", False)
    assert len(seen) == 13 * 40              # the other 13 kraje still collected
    assert complete is False                 # ...but the category is unproven


class _MidSliceBlankClient(_PartitionClient):
    """A slice that serves a correct page 1 and then a blank 200 mid-slice — the
    same throttle, arriving after the H1 has already been proven."""

    def fetch_search(self, url):  # noqa: ANN001
        if _kraj_of(url) == "praha" and _page_num(url) == 3:
            self.urls.append(url)
            return _page_html(None, [], heading="Prodej bytů praha"), 200
        return super().fetch_search(url)


def test_blank_page_mid_slice_is_degraded_not_the_end(monkeypatch):
    counts = {k: 40 for k in KRAJ_SLUGS}
    counts["praha"] = 200
    fake = _MidSliceBlankClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    r = portal._walk_slice(fake, "prodej", "byty", "praha")
    assert r.outcome == "degraded"
    # Items-first: the blank page has no next arrow either, and reading it as the
    # last page is exactly how a soft block would become a nomination.
    assert (r.stop, r.reached_end) == ("barren", False)
    assert len(r.rows) == 40                 # pages 1-2 kept, the slice unproven
    _seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert complete is False


class _BoomClient(_PartitionClient):
    def fetch_search(self, url):  # noqa: ANN001
        if _kraj_of(url) == "zlinsky-kraj":
            raise RuntimeError("connection reset")
        return super().fetch_search(url)


def test_a_fetch_exception_is_an_error_not_a_clean_finish(monkeypatch):
    fake = _BoomClient({k: 40 for k in KRAJ_SLUGS})
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    r = portal._walk_slice(fake, "prodej", "byty", "zlinsky-kraj")
    assert r.outcome == "error" and r.positive is False
    assert (r.stop, r.reached_end) == ("error", False)
    _seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert complete is False


def test_a_missing_kraj_forces_incomplete(monkeypatch):
    fake = _PartitionClient({k: 40 for k in KRAJ_SLUGS})
    portal = m.CeskerealityPortal(
        default_config("ceskereality"), kraje=("praha", "zlinsky-kraj"))
    seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert {_kraj_of(u) for u in fake.urls if _kraj_of(u)} == {"praha", "zlinsky-kraj"}
    assert len(seen) == 80
    assert complete is False                 # 2 of 14 is never a full walk


def test_union_short_of_the_national_total_is_still_complete(monkeypatch):
    """Rule #3 since 2026-09-07: the verdict is per region, against each
    region's own count. The nationwide total includes listings filed under no
    region (foreign flats in the domestic tree, Czech flats with no kraj), so
    the kraj union can never reach it -- demanding it parked rentals for good.
    Those region-less rows are nominated for a page check on every walk
    instead. The national number is still reported as the result size, so the
    RECONCILE line keeps showing the gap."""
    fake = _PartitionClient({k: 40 for k in KRAJ_SLUGS}, national=5000)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    _seen, _c, total, _p, complete = _walk(portal, fake, monkeypatch)
    assert total == 5000
    assert complete is True


# --- the structural verdict: the walk reached ceskereality's end -------------
#
# Rule #3, 2026-09-08. What the 5th element answers is "did every kraj stop
# because the portal said there was no more?", not "did the counts reconcile".
# The two came apart in production: prodej/rodinne-domy is 20,964 rows over 14
# regions, one of which (karlovarsky, 87 rows) came back one row short on every
# walk — and that single row held the whole category's nomination shut for two
# days while 20,963 rows sat unchecked.


class _ShortByOneClient(_PartitionClient):
    """One kraj declares 87 and serves 86: the tail moved under the walk. Every
    page is real, the pager runs out on the declared last page."""

    def __init__(self, counts, short_kraj: str) -> None:  # noqa: ANN001
        super().__init__(counts)
        self._short = short_kraj

    def fetch_search(self, url):  # noqa: ANN001
        kraj = _kraj_of(url)
        if kraj != self._short:
            return super().fetch_search(url)
        self.urls.append(url)
        total = self._counts[kraj]
        pg, last = _page_num(url), max(1, -(-self._counts[kraj] // 20))
        first = (pg - 1) * 20
        n = max(0, min(20, total - first)) - (1 if pg == last else 0)
        ids = [str(2_000_000 + first + k) for k in range(n)]
        return _page_html(total, ids, next_page=pg + 1 if pg < last else None,
                          heading=f"Prodej bytu {kraj}"), 200


def test_a_slice_one_row_short_of_its_declared_total_still_reaches_end(monkeypatch):
    """86 of 87 = 0.9885, under the 0.995 the arithmetic gate demanded. The slice
    walked every page it has and the pager ran out: that is a FINISHED walk over a
    live index, so it reaches the portal's end and the category nominates. The
    SLICE line still logs the shortfall as `degraded` — the number did not change,
    only what it is allowed to veto."""
    counts = {k: 40 for k in KRAJ_SLUGS}
    counts["karlovarsky-kraj"] = 87
    fake = _ShortByOneClient(counts, "karlovarsky-kraj")
    portal = m.CeskerealityPortal(default_config("ceskereality"))

    short = portal._walk_slice(fake, "prodej", "byty", "karlovarsky-kraj")
    assert len({r[0] for r in short.rows}) == 86
    assert short.outcome == "degraded"       # the numeric verdict is unchanged...
    assert short.positive is False           # ...and still numeric (PARTITION's positive=)
    assert m_portal.walk_is_complete(86, 87) is False
    assert short.stop == "pager_end"         # ...but the PORTAL ended the loop
    assert short.reached_end is True

    _seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert complete is True                  # 20,963 rows are no longer hostage


class _EarlyPagerClient(_PartitionClient):
    """A kraj whose next arrow goes disabled at page 2 of a declared 5 pages."""

    def fetch_search(self, url):  # noqa: ANN001
        html, status = super().fetch_search(url)
        if _kraj_of(url) == "praha" and _page_num(url) == 2:
            html = re.sub(
                r'<a class="pagination-arrow --next".*?</a>',
                '<a class="pagination-arrow --disabled --next" href="/x/?strana=2"></a>',
                html,
            )
        return html, status


class _PagerlessPageClient(_PartitionClient):
    """A kraj whose page 2 comes back with its cards but NO pagination block at all
    — the truncated body / edge shell an over-eager CDN serves."""

    def fetch_search(self, url):  # noqa: ANN001
        html, status = super().fetch_search(url)
        if _kraj_of(url) == "praha" and _page_num(url) == 2:
            html = re.sub(r'<a class="pagination-arrow[^>]*>.*?</a>', "", html)
        return html, status


def test_a_page_with_no_pagination_block_is_not_an_end(monkeypatch):
    """`_next_page` returns None for the site's own disabled arrow AND for a page
    that renders no pagination at all. Only the first is ceskereality saying "there
    is no more"; treating absence alike let a shortened Cloudflare body carrying one
    card end a slice at page 2 of 5 and nominate the rest of the category."""
    counts = {k: 40 for k in KRAJ_SLUGS}
    counts["praha"] = 100
    fake = _PagerlessPageClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))

    r = portal._walk_slice(fake, "prodej", "byty", "praha")
    assert (r.stop, r.reached_end) == ("barren", False)
    _seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert complete is False


def test_a_pager_that_ends_before_the_declared_tail_is_the_portals_end(monkeypatch):
    """The disabled next arrow is the site's own last-page signal, and it means
    the same thing wherever it appears. Reading an early one as a truncation was
    the second numeric veto, and it fired on exactly the live-tail churn the
    declared count cannot keep up with."""
    counts = {k: 40 for k in KRAJ_SLUGS}
    counts["praha"] = 100
    fake = _EarlyPagerClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))

    r = portal._walk_slice(fake, "prodej", "byty", "praha")
    assert r.pages == 2 and len(r.rows) == 40
    assert (r.stop, r.reached_end) == ("pager_end", True)
    assert r.outcome == "degraded"           # 40 of 100 is on the record
    _seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert complete is True


def test_a_full_slice_stops_on_the_portals_own_count(monkeypatch):
    fake = _PartitionClient({k: 40 for k in KRAJ_SLUGS})
    r = m.CeskerealityPortal(default_config("ceskereality"))._walk_slice(
        fake, "prodej", "byty", "praha")
    # page 2 of 2: the count says the tail and the arrow is gone; either way the
    # portal ended it, and both are in PORTAL_ENDS.
    assert r.stop in m_portal.PORTAL_ENDS
    assert (r.outcome, r.reached_end) == ("exhausted", True)


def test_a_barren_first_page_that_does_not_confirm_is_our_stop(monkeypatch):
    """The transient throttle: the shell comes back once, the re-read returns the
    real page. An items-less 200 nobody could corroborate is `barren` — OURS — so
    the category cannot nominate this walk. It costs one walk, not a region."""
    counts = {k: 400 for k in KRAJ_SLUGS}
    fake = _LaunderedKrajClient(counts, throttled="zlinsky-kraj", stay_empty=False)
    portal = m.CeskerealityPortal(default_config("ceskereality"))

    r = portal._walk_slice(fake, "prodej", "byty", "zlinsky-kraj")
    assert (r.stop, r.reached_end, r.outcome) == ("barren", False, "degraded")
    assert fake.rereads == 1                 # exactly one re-read, not a loop

    fresh = _LaunderedKrajClient(counts, throttled="zlinsky-kraj", stay_empty=False)
    _seen, _c, _t, _p, complete = _walk(portal, fresh, monkeypatch)
    assert complete is False


def test_max_pages_is_our_stop_even_when_every_slice_finishes(monkeypatch):
    """--max-pages is ours by definition: the slices under it stop where WE said,
    not where the portal did, so a capped walk may never nominate."""
    fake = _PartitionClient({k: 40 for k in KRAJ_SLUGS})
    portal = m.CeskerealityPortal(default_config("ceskereality"), max_pages=1)
    _seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert complete is False
    r = portal._walk_slice(fake, "prodej", "byty", "praha")
    assert (r.stop, r.reached_end, r.outcome) == ("page_cap", False, "ceiling")


def test_a_category_whose_slices_all_failed_to_start_never_reaches_the_end(monkeypatch):
    """all([]) is True: seeding the stop list from the slices that RAN rather than
    from the declared 14 would make a walk that collected nothing report the
    portal's end."""
    class _AllBoomClient(_PartitionClient):
        def fetch_search(self, url):  # noqa: ANN001
            raise RuntimeError("blocked")

    fake = _AllBoomClient({k: 40 for k in KRAJ_SLUGS})
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, _c, _t, _p, complete = _walk(portal, fake, monkeypatch)
    assert seen == set()
    assert complete is False


# --- the subtype descent (a kraj past the site's 99-page ceiling) ------------


class _CeilingClient:
    """One kraj of `cat` past the 99-page ceiling, the other 13 at 40 rows, and a
    subtype slice of that kraj serving `per_sub[subtype]`. Path shape:
    /{sale}/{cat}[/{subtype}]/{kraj-or-okres}/ — the segment BETWEEN the category
    and the last one is the subtype (and only that segment); an okres slug takes
    the kraj's slot, serving `per_okres[subtype][okres]`. `kraj_links` is extra
    markup on the over-ceiling kraj's own page, for the unmapped-category
    fallback."""

    def __init__(
        self, cat: str, kraj: str, per_sub: dict[str, int], kraj_links: str = "",
        per_okres: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self.urls: list[str] = []
        self._cat, self._kraj, self._per_sub = cat, kraj, per_sub
        self._subs = list(per_sub)
        self._links = kraj_links
        self._per_okres = per_okres or {}
        self.declared = sum(per_sub.values())

    def _parts(self, url: str) -> tuple[str | None, str | None]:
        segs = url.split("?")[0].split(f"/prodej/{self._cat}/")[1].strip("/")
        segs = segs.split("/") if segs else []
        return (segs[-2] if len(segs) == 2 else None), (segs[-1] if segs else None)

    def fetch_search(self, url: str) -> tuple[str, int]:
        self.urls.append(url)
        sub, kraj = self._parts(url)
        pg = _page_num(url)
        first = (pg - 1) * 20
        okresy = self._per_okres.get(sub or "", {})
        if kraj in okresy:
            total = okresy[kraj]
            base = (5_000_000 + 100_000 * self._subs.index(sub)
                    + 10_000 * list(okresy).index(kraj))
        else:
            if kraj != self._kraj:
                total = 4 if sub else 40
            else:
                total = self._per_sub[sub] if sub else self.declared
            base = (3_000_000 + 100_000 * (self._subs.index(sub) if sub else 99)
                    + 1_000 * KRAJ_SLUGS.index(kraj))
        n = max(0, min(20, total - first))
        ids = [str(base + first + k) for k in range(n)]
        last = max(1, -(-total // 20))
        html = _page_html(total, ids, next_page=pg + 1 if pg < last else None,
                          heading=f"Nabídka {kraj}")
        if kraj == self._kraj and sub is None:
            html = html.replace("</body>", f"{self._links}</body>")
        return html, 200

    def fetch_index(
        self, sale_type: str, cat: str, page: int | None,
    ) -> tuple[str, int]:
        return _page_html(self.declared + 13 * 40, []), 200


def _walk_ceiling(
    monkeypatch: pytest.MonkeyPatch, fake: _CeilingClient, cat: str,
    deadline: float | None = None,
) -> tuple[set[str], dict[str, int], int | None, int, bool]:
    monkeypatch.setattr(m, "CeskerealityClient", lambda **k: fake)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    return portal.walk_category(
        {"sale_type": "prodej", "category": cat},
        conn=None, dry_run=True, limiter=None, deadline=deadline,
    )


def test_a_slice_over_the_page_ceiling_descends_onto_subtypes(monkeypatch):
    """/prodej/rodinne-domy/stredocesky-kraj/ is 2,312 rows = 116 pages, and a
    FILTERED url 404s at ?strana=100 — so 332 rows are unreachable on the kraj
    axis alone. The subtype slugs are declared (the rendered facet block omits
    zero-count subtypes) and sum to the kraj total exactly."""
    subs = SUBTYPE_SLUGS["rodinne-domy"]
    per_sub = {s: 240 for s in subs}
    per_sub[subs[0]] = 2312 - 240 * (len(subs) - 1)
    fake = _CeilingClient("rodinne-domy", "stredocesky-kraj", per_sub)

    seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "rodinne-domy")

    # the over-ceiling kraj bailed at page 1 and was re-walked per subtype...
    stredo = [u for u in fake.urls if _kraj_of(u) == "stredocesky-kraj"]
    assert max(_page_num(u) for u in stredo) <= 99   # never asks for the 404
    for slug in subs:
        assert any(f"/{slug}/stredocesky-kraj/" in u for u in fake.urls), slug
    assert len(seen) == 2312 + 13 * 40
    assert complete is True


# 2026-10-13, measured on the live site: prodej/byty/praha by subtype. Its sum is
# the 2,161 the kraj page declared at that moment; the walk that parked the
# category read 2,171, so the extra ten (the count is live) sit on byty-2-kk.
_PRAHA_BYTY_PRODEJ = {
    "byty-1-kk": 339, "byty-1-1": 55, "byty-2-kk": 638 + 10, "byty-2-1": 172,
    "byty-3-kk": 439, "byty-3-1": 226, "byty-4-kk": 181, "byty-4-1": 50,
    "byty-5-kk": 29, "byty-5-1-vetsi": 19, "atypicke-byty": 9, "pokoj": 4,
}


def test_praha_byty_over_the_ceiling_descends_onto_all_twelve_subtypes(monkeypatch):
    """The production defect: Praha byt/prodej crossed 1,980 rows on 2026-09-16,
    byty had no declared subtypes, the page-scraped fallback found none, and
    `DESCENT no subtype axis` left the kraj at `ceiling` — so byt/prodej, the
    whole category, nominated nothing for 89 walks. It now descends onto the
    twelve declared slugs, two of which (`atypicke-byty`, `pokoj`) a `byty-*`
    prefix would have dropped."""
    assert set(SUBTYPE_SLUGS["byty"]) == set(_PRAHA_BYTY_PRODEJ)
    fake = _CeilingClient("byty", "praha", dict(_PRAHA_BYTY_PRODEJ))
    assert fake.declared == 2171

    seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "byty")

    for slug in SUBTYPE_SLUGS["byty"]:
        assert any(f"/prodej/byty/{slug}/praha/" in u for u in fake.urls), slug
    assert len(seen) == 2171 + 13 * 40
    assert complete is True


def test_an_unmapped_category_descends_on_the_kraj_pages_own_subtype_links(
    monkeypatch,
):
    """No declared map: the kraj page's two-segment `/{sale}/{cat}/{subtype}/{kraj}/`
    links ARE the axis (subtype precedes kraj in this site's URLs). The page's
    other links — okresy, části, kraj-first filters, other kraje — are not, and
    the children's declared sum still has to add back up to the parent's."""
    monkeypatch.setattr(m, "SUBTYPE_SLUGS", {})
    per_sub = {"atypicke-byty": 900, "byty-2-kk": 900, "pokoj": 371}
    links = "".join(
        f'<a href="/prodej/byty/{s}/praha/">x</a>' for s in per_sub
    ) + (
        '<a href="/prodej/byty/praha-4/">okres</a>'
        '<a href="/prodej/byty/cast-praha-zizkov/">cast</a>'
        '<a href="/prodej/byty/praha/soukromy/">filter</a>'
        '<a href="/prodej/byty/byty-3-1/stredocesky-kraj/">other kraj</a>'
    )
    fake = _CeilingClient("byty", "praha", per_sub, kraj_links=links)

    seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "byty")

    walked = {fake._parts(u)[0] for u in fake.urls if _kraj_of(u) == "praha"}
    assert walked - {None} == set(per_sub)
    assert len(seen) == 2171 + 13 * 40
    assert complete is True


def test_an_unmapped_kraj_page_without_subtype_links_stays_incomplete(monkeypatch):
    """The last resort, unchanged: no declared map and no two-segment link on the
    page leaves the kraj at `ceiling`, and the category cannot nominate."""
    monkeypatch.setattr(m, "SUBTYPE_SLUGS", {})
    fake = _CeilingClient(
        "byty", "praha", {"byty-2-kk": 2171},
        kraj_links='<a href="/prodej/byty/praha-4/">okres</a>'
                   '<a href="/prodej/byty/praha/soukromy/">filter</a>')

    _seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "byty")

    assert not any("/byty-2-kk/" in u for u in fake.urls)
    assert complete is False


def test_every_category_measured_at_the_ceiling_has_a_declared_subtype_map():
    """A rail: these three were measured at or over the 1,980-row ceiling in one
    kraj (byty praha 2,171; rodinne-domy stredocesky 2,312; pozemky stredocesky
    1,978). Without a map each depends on whatever the kraj page happens to link."""
    walked = {c["category"] for c in default_config("ceskereality").categories}
    for cat in ("byty", "rodinne-domy", "pozemky"):
        assert cat in walked
        slugs = SUBTYPE_SLUGS.get(cat)
        assert slugs, f"{cat} lost its declared subtype axis"
        assert len(set(slugs)) == len(slugs)


def test_a_descent_that_loses_the_residue_reports_incomplete(monkeypatch):
    """Self-verification: if the children's declared totals do not add back up to
    the parent's, the category reads incomplete rather than silently dropping the
    difference."""
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    parent = m.SliceResult(
        "stredocesky-kraj", None, [], 2312, 1, "ceiling", "page_cap")

    class _ThinClient:
        def fetch_search(self, url):  # noqa: ANN001
            pg = _page_num(url)
            ids = [str(9_100_000 + pg * 20 + k) for k in range(20)]
            return _page_html(100, ids, next_page=pg + 1 if pg < 5 else None,
                              heading="Domy stredocesky-kraj"), 200

    monkeypatch.setattr(m, "SUBTYPE_SLUGS", {"rodinne-domy": ("vily",)})
    kids = portal._descend_slice(_ThinClient(), "prodej", "rodinne-domy", parent)
    assert any(k.outcome == "ceiling" for k in kids)   # residue surfaced
    assert not all(k.positive for k in kids)
    # The residue child carries the parent's stop, so the category cannot nominate
    # while a slice of it is unreachable.
    assert not all(k.reached_end for k in kids)


# --- the okres descent (a subtype child itself past the ceiling) -------------
#
# 2026-10-13: prodej/rodinne-domy/rodinne-domy/stredocesky-kraj declared 1,922 —
# 97 of the 99 pages a filtered URL serves. Past 1,980 the subtype descent alone
# leaves that child at `ceiling` and parks the whole 11,487-row category, so the
# child descends once more, onto the kraj's declared okresy.


def _rd_stredocesky(
    child: int, okres_each: int | dict[str, int],
    cls: type[_CeilingClient] = _CeilingClient, *, kraj: str = "stredocesky-kraj",
    **kw,  # noqa: ANN003
) -> _CeilingClient:
    """`kraj` rodinne-domy: the `rodinne-domy` subtype child declares `child` rows,
    its okresy `okres_each` each (or per okres, from a dict), the other 9 subtypes
    400 between them."""
    per_sub = {s: 40 for s in SUBTYPE_SLUGS["rodinne-domy"]}
    per_sub["chaty"] = 80
    per_sub["rodinne-domy"] = child
    okresy = (okres_each if isinstance(okres_each, dict)
              else dict.fromkeys(OKRES_SLUGS[kraj], okres_each))
    return cls("rodinne-domy", kraj, per_sub,
               per_okres={"rodinne-domy": okresy}, **kw)


# The per-result SLICE line is key=value pairs only; the prose SLICE lines (an empty page that
# did not confirm, a missing pager, a pager that ended early) carry words between them.
_SLICE_RESULT = re.compile(r"SLICE cm=\S+ ct=\S+( \w+=\S+)* outcome=\S+ stop=\S+")


def _slices(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    """Each SLICE line the walk logged (one per slice result, in walk order) as its fields."""
    return [dict(kv.split("=", 1) for kv in message.split()[1:])
            for message in (r.getMessage() for r in caplog.records)
            if _SLICE_RESULT.fullmatch(message)]


def test_a_subtype_child_over_the_ceiling_descends_onto_the_okresy(monkeypatch, caplog):
    """Grandchildren keep their parent kraj and subtype, so the walk's per-kraj verdict holds however
    deep the descent went."""
    fake = _rd_stredocesky(child=2100, okres_each=175)
    assert fake.declared == 2500

    with caplog.at_level("INFO", logger=m.LOG.name):
        seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "rodinne-domy")

    assert max(_page_num(u) for u in fake.urls) <= 99     # never asks for the 404
    child = search_url("prodej", "rodinne-domy", kraj="stredocesky-kraj",
                       subtype="rodinne-domy")
    assert [u for u in fake.urls if u.startswith(child)] == [child]   # bailed at p.1
    for okres in OKRES_SLUGS["stredocesky-kraj"]:
        assert any(f"/rodinne-domy/rodinne-domy/{okres}/" in u for u in fake.urls), okres
    # only the over-ceiling child descends; its siblings are walked whole
    assert not any(f"/chaty/{o}/" in u for u in fake.urls
                   for o in OKRES_SLUGS["stredocesky-kraj"])
    assert len(seen) == 2500 + 13 * 40
    assert complete is True
    grandchildren = [s for s in _slices(caplog) if s["okres"] != "all"]
    assert {s["okres"] for s in grandchildren} == set(OKRES_SLUGS["stredocesky-kraj"])
    assert {(s["kraj"], s["subtype"]) for s in grandchildren} == {("stredocesky-kraj", "rodinne-domy")}


def test_okres_grandchildren_short_of_their_child_leave_a_residue(monkeypatch):
    """Self-verification one level down: 12 x 150 = 1,800 of a 2,100-row child is
    a hole, and the category must read incomplete rather than drop 300 rows."""
    fake = _rd_stredocesky(child=2100, okres_each=150)

    seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "rodinne-domy")

    assert len(seen) == 2500 - 300 + 13 * 40
    assert complete is False
    parent = m.SliceResult(
        "stredocesky-kraj", None, [], 2500, 1, "ceiling", "page_cap")
    kids = m.CeskerealityPortal(default_config("ceskereality"))._descend_slice(
        fake, "prodej", "rodinne-domy", parent)
    residue = [k for k in kids if k.outcome == "ceiling"]
    # the residue declares only the hole, so the kraj's declared sum stays 2,500
    assert [(k.subtype, k.okres, k.declared_total, k.reached_end) for k in residue] == [
        ("rodinne-domy", None, 300, False)]
    assert sum(k.declared_total or 0 for k in kids) == 2500
    assert all(k.positive for k in kids if k.okres)


class _ClockedCeiling(_CeilingClient):
    """After `trip_after` fetches, the fake monotonic clock jumps past the deadline."""

    def __init__(self, *a, clock: dict, trip_after: int, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self._clock, self._trip_after = clock, trip_after

    def fetch_search(self, url: str) -> tuple[str, int]:
        out = super().fetch_search(url)
        if len(self.urls) >= self._trip_after:
            self._clock["t"] = 9_999.0
        return out


def test_the_deadline_inside_the_okres_loop_is_our_stop(monkeypatch):
    """praha (2 pages) + stredocesky p.1 + the rodinne-domy child p.1 + benesov's
    9 pages = 13 fetches; the clock trips inside beroun. Every level stops there,
    and a walk cut short at depth two is still never 'reached the end'."""
    clock = {"t": 0.0}
    monkeypatch.setattr(m_portal, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    fake = _rd_stredocesky(2100, 175, _ClockedCeiling, clock=clock, trip_after=15)

    seen, _c, _t, _p, complete = _walk_ceiling(
        monkeypatch, fake, "rodinne-domy", deadline=10.0)

    assert any("/rodinne-domy/beroun/" in u for u in fake.urls)
    assert not any("/rodinne-domy/kladno/" in u for u in fake.urls)   # next okres
    assert not any("/chalupy/" in u for u in fake.urls)                # next subtype
    assert {_kraj_of(u) for u in fake.urls} <= {"praha", "stredocesky-kraj", None}
    assert len(seen) >= 175                 # benesov's rows are kept
    assert complete is False


def test_a_deadline_between_okresy_in_the_last_kraj_is_our_stop(monkeypatch, caplog):
    """The okres loop's OWN deadline marker, with nothing else to catch it:
    zlinsky is the LAST kraj, so the kraj loop never sees the deadline, and the
    clock trips exactly as kromeriz's last page lands (13 x 2 + zlinsky p.1 + the
    child's p.1 + kromeriz's 27 = 55 fetches), so no slice walk sees it either.
    Each marker declares only the part it stands for: zlinsky's SLICE lines sum
    to its 2,500."""
    assert KRAJ_SLUGS[-1] == "zlinsky-kraj"
    clock = {"t": 0.0}
    monkeypatch.setattr(m_portal, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    fake = _rd_stredocesky(2100, 525, _ClockedCeiling, kraj="zlinsky-kraj",
                           clock=clock, trip_after=55)

    with caplog.at_level("INFO", logger=m.LOG.name):
        _seen, _c, _t, _p, complete = _walk_ceiling(
            monkeypatch, fake, "rodinne-domy", deadline=10.0)

    assert len([u for u in fake.urls if "/rodinne-domy/kromeriz/" in u]) == 27
    assert not any("/uherske-hradiste/" in u for u in fake.urls)   # next okres
    assert complete is False
    zlin = [s for s in _slices(caplog) if s["kraj"] == "zlinsky-kraj"]
    assert [(s["subtype"], s["okres"], s["stop"], s["declared"]) for s in zlin] == [
        ("rodinne-domy", "kromeriz", zlin[0]["stop"], "525"),
        ("rodinne-domy", "all", "deadline", str(2100 - 525)),
        ("all", "all", "deadline", str(2500 - 2100)),
    ]
    assert [m_portal.stop_is_portal_end(s["stop"]) for s in zlin] == [True, False, False]
    assert any(
        r.getMessage().startswith("DESCENT ") and "axis=okres" in r.getMessage()
        for r in caplog.records)


def test_an_okres_still_over_the_ceiling_keeps_the_category_incomplete(
    monkeypatch, caplog,
):
    """No axis below okres: an okres grandchild past 99 pages comes back as
    itself, still `ceiling`. Its declared total already sits in the child's okres
    sum, so the level above sees an exact partition and adds no residue — this
    return is the ONLY thing keeping its unwalked tail from reading as the end."""
    okresy = dict.fromkeys(OKRES_SLUGS["stredocesky-kraj"], 10)
    okresy["praha-vychod"] = 1990                 # 100 pages
    fake = _rd_stredocesky(2100, okresy)

    with caplog.at_level("INFO", logger=m.LOG.name):
        _seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "rodinne-domy")

    assert [u for u in fake.urls if "/rodinne-domy/praha-vychod/" in u] == [
        search_url("prodej", "rodinne-domy", kraj="praha-vychod",
                   subtype="rodinne-domy")]                       # bailed at p.1
    assert complete is False
    ceiling = [(s["subtype"], s["okres"]) for s in _slices(caplog)
               if s["outcome"] == "ceiling"]
    assert ceiling == [("rodinne-domy", "praha-vychod")]          # no residue
    assert any("DESCENT no axis left" in r.getMessage()
               and "okres=praha-vychod" in r.getMessage() for r in caplog.records)


def test_an_unmapped_kraj_keeps_an_over_ceiling_child_at_the_ceiling(
    monkeypatch, caplog,
):
    """Praha has no okres map (praha-1..10 summed to 2,148 of 2,161 on 2026-10-13:
    not a partition). A Praha subtype past the ceiling stays `ceiling`, the
    category cannot nominate, and the warning names the map it is owed."""
    assert "praha" not in OKRES_SLUGS
    per_sub = dict(_PRAHA_BYTY_PRODEJ)
    per_sub["byty-2-kk"] = 2100
    fake = _CeilingClient("byty", "praha", per_sub)

    with caplog.at_level("WARNING", logger=m.LOG.name):
        _seen, _c, _t, _p, complete = _walk_ceiling(monkeypatch, fake, "byty")

    child = search_url("prodej", "byty", kraj="praha", subtype="byty-2-kk")
    assert [u for u in fake.urls if u.startswith(child)] == [child]
    assert not any(re.search(r"/byty-2-kk/praha-\d+/", u) for u in fake.urls)
    msgs = [r.getMessage() for r in caplog.records if "DESCENT" in r.getMessage()]
    assert any(
        "DESCENT no okres axis" in x and "kraj=praha subtype=byty-2-kk" in x
        and "OKRES_SLUGS['praha']" in x for x in msgs), msgs
    assert complete is False


# RÚIAN (admin_boundaries), 2026-10-13: okresy per kraj.
_RUIAN_OKRES_COUNT = {
    "stredocesky-kraj": 12, "jihocesky-kraj": 7, "plzensky-kraj": 7,
    "karlovarsky-kraj": 3, "ustecky-kraj": 7, "liberecky-kraj": 4,
    "kralovehradecky-kraj": 5, "pardubicky-kraj": 4, "kraj-vysocina": 5,
    "jihomoravsky-kraj": 7, "olomoucky-kraj": 5, "moravskoslezsky-kraj": 6,
    "zlinsky-kraj": 4,
}


def test_every_kraj_but_praha_declares_its_ruian_okresy():
    """A rail on the declared okres axis: 76 okresy, each in exactly one kraj,
    every slug a plain URL segment. A kraj losing its entry would quietly bring
    back the parked-category failure the moment one of its subtypes crossed."""
    assert set(OKRES_SLUGS) == set(KRAJ_SLUGS) - {"praha"}
    assert {k: len(v) for k, v in OKRES_SLUGS.items()} == _RUIAN_OKRES_COUNT
    slugs = [o for v in OKRES_SLUGS.values() for o in v]
    assert len(slugs) == len(set(slugs)) == 76
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", o) for o in slugs), slugs
    assert not set(slugs) & set(KRAJ_SLUGS)


class _OkresPageClient:
    """One page for every url: zero cards, no total, H1 `heading`."""

    def __init__(self, heading: str) -> None:
        self.urls: list[str] = []
        self._heading = heading

    def fetch_search(self, url: str) -> tuple[str, int]:
        self.urls.append(url)
        return _page_html(None, [], heading=self._heading), 200


def test_an_okres_slice_puts_the_okres_in_the_kraj_slot_and_in_the_h1_proof():
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    fake = _OkresPageClient("Rodinné domy na prodej v okrese Nymburk")
    r = portal._walk_slice(fake, "prodej", "rodinne-domy", "stredocesky-kraj",
                           subtype="rodinne-domy", okres="nymburk")
    assert fake.urls[0] == (
        "https://www.ceskereality.cz/prodej/rodinne-domy/rodinne-domy/nymburk/")
    assert (r.kraj, r.subtype, r.okres) == ("stredocesky-kraj", "rodinne-domy", "nymburk")
    assert (r.stop, r.outcome) == ("empty_confirmed", "exhausted")
    # an H1 naming the KRAJ is not proof that the OKRES is empty
    fake = _OkresPageClient("Rodinné domy na prodej Středočeský kraj")
    r = portal._walk_slice(fake, "prodej", "rodinne-domy", "stredocesky-kraj",
                           subtype="rodinne-domy", okres="nymburk")
    assert (r.stop, r.reached_end) == ("barren", False)


def test_an_okres_page_archives_under_its_own_key(monkeypatch):
    """portal_raw_pages is UNIQUE(source, source_id_native, page_kind): without the
    okres segment, the 12 okres page-1s would share the subtype child's key."""
    keys: list[str] = []
    monkeypatch.setattr(m.db, "upsert_portal_raw_page",
                        lambda _c, **kw: keys.append(kw["source_id_native"]))
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    for okres, h1 in ((None, "Středočeský kraj"), ("benesov", "v okrese Benešov")):
        portal._walk_slice(
            _OkresPageClient(f"Rodinné domy na prodej {h1}"), "prodej", "rodinne-domy",
            "stredocesky-kraj", subtype="rodinne-domy", okres=okres, conn=object(),
            archive_week="2026-W42")
    assert keys == [
        "v2/prodej/rodinne-domy/stredocesky-kraj/rodinne-domy/1/2026-W42",
        "v2/prodej/rodinne-domy/stredocesky-kraj/rodinne-domy/benesov/1/2026-W42",
    ]


# --- the deadline + the unmeasurable total ----------------------------------


class _ClockClient(_PartitionClient):
    """`trip_after` fetches, the fake monotonic clock jumps past the deadline."""

    def __init__(self, counts, clock: dict, trip_after: int) -> None:  # noqa: ANN001
        super().__init__(counts)
        self._clock = clock
        self._trip_after = trip_after

    def fetch_search(self, url):  # noqa: ANN001
        out = super().fetch_search(url)
        if len(self.urls) >= self._trip_after:
            self._clock["t"] = 9_999.0
        return out


def test_deadline_stops_walk_and_forces_incomplete(monkeypatch):
    """A walk cut short by the wall-clock budget must NEVER report complete=True —
    the kraje it never reached hold listings it never saw (rule #3)."""
    clock = {"t": 0.0}
    monkeypatch.setattr(
        m_portal, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    fake = _ClockClient({k: 40 for k in KRAJ_SLUGS}, clock, trip_after=1)
    portal = m.CeskerealityPortal(default_config("ceskereality"))

    seen, _counts, _total, _pages, complete = _walk(
        portal, fake, monkeypatch, deadline=10.0)

    assert seen                             # rows collected before the stop are kept
    assert complete is False                # the deadline poisons the whole verdict
    assert {_kraj_of(u) for u in fake.urls if _kraj_of(u)} == {KRAJ_SLUGS[0]}


class _NoTotalClient:
    """Cards on the page but no "Máme tady N" and no H1 — unmeasurable, which is
    ceskereality's live failure mode and now reads degraded, not complete."""

    def __init__(self) -> None:
        self._n = 0

    def fetch_search(self, url):  # noqa: ANN001
        self._n += 1
        return _page_html(None, [str(8_000_000 + self._n)]), 200

    def fetch_index(self, sale_type, cat, page):  # noqa: ANN001
        raise RuntimeError("nationwide total unavailable")


def test_unmeasurable_total_is_unknown_not_complete(monkeypatch):
    """A page with cards but no count is a BROKEN page, not a tail: the count
    renders from the same query as the cards, so one without the other is the
    throttle signature. Unmeasurable is never the portal's end (rule #3); the old
    fail-open said complete=True and delisted on a guess."""
    fake = _NoTotalClient()
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    _seen, _counts, total, _pages, complete = _walk(portal, fake, monkeypatch)

    assert total == 0                       # nothing measurable to reconcile against
    assert complete is False                # ...so the walk cannot nominate
    r = m.CeskerealityPortal(default_config("ceskereality"))._walk_slice(
        fake, "prodej", "byty", "praha")
    assert (r.stop, r.reached_end) == ("error", False)


def test_walk_slice_reports_deadline_when_budget_already_spent(monkeypatch):
    """The page loop's own guard: budget spent -> no request at all."""
    monkeypatch.setattr(
        m_portal, "time", SimpleNamespace(monotonic=lambda: 100.0))
    fake = _PartitionClient({"praha": 400})
    r = m.CeskerealityPortal(default_config("ceskereality"))._walk_slice(
        fake, "prodej", "byty", "praha", deadline=1.0)
    assert fake.urls == []                  # not one request past the budget
    assert (r.rows, r.pages, r.outcome, r.positive) == ([], 0, "deadline", False)
    assert (r.stop, r.reached_end) == ("deadline", False)


def test_search_url_puts_subtype_before_kraj():
    # live-verified: /prodej/byty/byty-2-1/stredocesky-kraj/ is correctly filtered
    assert search_url("prodej", "byty", kraj="stredocesky-kraj", subtype="byty-2-1") == (
        "https://www.ceskereality.cz/prodej/byty/byty-2-1/stredocesky-kraj/")
    assert search_url("prodej", "byty", kraj="praha", page=93) == (
        "https://www.ceskereality.cz/prodej/byty/praha/?strana=93")


# --- cross-slice nomination ('rodinne-domy' + 'chaty-chalupy' -> dum) --------------------------
#
# Rule #3 since 2026-09-07: a complete walk nominates its unseen rows for a page
# check, it does not delist. Sibling slices that collapse onto one canonical
# category still have to be buffered, because the runner calls the seam per
# slice and one slice's seen set would nominate the sibling's whole population.

def _nominating_portal(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        m.db, "presence_candidates",
        lambda _c, src, cm, ct, seen, **kw: calls.append(
            {"src": src, "cm": cm, "ct": ct, "seen": set(seen), **kw}) or ([], len(seen)),
    )
    return m.CeskerealityPortal(default_config("ceskereality")), calls


def test_nomination_buffers_the_collapsing_group_and_nominates_once_with_union(monkeypatch):
    portal, calls = _nominating_portal(monkeypatch)
    # First dum slice buffers only: nominating here would send every chaty-chalupy row
    # (same (dum, pronajem), never in the rodinne-domy slice's seen set) to the drain.
    assert portal.presence_candidates(
        object(), {"sale_type": "pronajem", "category": "rodinne-domy"}, {"r1", "r2"}) is None
    assert calls == []
    # The group's last complete slice nominates with the UNION.
    out = portal.presence_candidates(
        object(), {"sale_type": "pronajem", "category": "chaty-chalupy"}, {"c1"})
    assert out == ([], 3)
    assert calls == [{"src": "ceskereality", "cm": "dum", "ct": "pronajem", "seen": {"r1", "r2", "c1"}}]


def test_nomination_missing_sibling_slice_nominates_nothing(monkeypatch):
    # The runner only reaches this seam for COMPLETE slices; if rodinne-domy walked
    # incomplete or failed, chaty-chalupy alone must not nominate (dum, prodej).
    portal, calls = _nominating_portal(monkeypatch)
    assert portal.presence_candidates(
        object(), {"sale_type": "prodej", "category": "chaty-chalupy"}, {"c1"}) is None
    assert calls == []


def test_nomination_single_slice_group_nominates_immediately(monkeypatch):
    portal, calls = _nominating_portal(monkeypatch)
    assert portal.presence_candidates(
        object(), {"sale_type": "prodej", "category": "byty"}, {"b1"}) == ([], 1)
    assert calls == [{"src": "ceskereality", "cm": "byt", "ct": "prodej", "seen": {"b1"}}]


def test_the_old_sweep_seam_is_gone():
    """No portal may flip rows from index absence any more; the runner never
    calls mark_inactive and the seam must not linger to tempt anyone."""
    assert not hasattr(m.CeskerealityPortal, "mark_inactive")
    assert not hasattr(m.CeskerealityPortal, "mark_gone")


# --- newest-first delta probe (/nejnovejsi/ on the www host) -----------------


def _priced_page_html(
    total: int | None, id_price_pairs: list[tuple[str, str]],
    next_page: int | None = None,
) -> str:
    cards = "".join(
        '<article class="i-estate">'
        f'<a class="i-estate__image-link" href="/prodej/byty/x/y-{i}.html"></a>'
        f'<div class="i-estate__footer-price-value">{p}</div>'
        "</article>"
        for i, p in id_price_pairs
    )
    meta = f'<meta name="description" content="Máme tady {total} bytů">' if total else ""
    # The site's real last page still renders the arrow, carrying `--disabled` —
    # that marker is what says "there is no more". A page with NO pagination block
    # is a truncated body, and the walk must not read the two alike.
    pager = (
        f'<a class="pagination-arrow --next" href="/x/?strana={next_page}"></a>'
        if next_page
        else '<a class="pagination-arrow --disabled --next" href="/x/?strana=1"></a>'
    )
    return f"<html><head>{meta}</head><body>{cards}{pager}</body></html>"


class _NewestClient:
    """Scripted /nejnovejsi/ pages: page -> [(id, price_text)]."""

    def __init__(self, pages: dict[int, list[tuple[str, str]]], total: int = 8439) -> None:
        self.urls: list[str] = []
        self._pages = pages
        self._total = total

    def fetch_search(self, url):  # noqa: ANN001
        self.urls.append(url)
        pg = _page_num(url)
        pairs = self._pages.get(pg, [])
        return _priced_page_html(
            self._total, pairs, next_page=pg + 1 if pairs else None), 200


def test_probe_category_reads_nejnovejsi_on_www(monkeypatch):
    fake = _NewestClient({
        1: [("7000001", "3 200 000 Kč"), ("7000002", "4 100 000 Kč")],
        2: [("7000003", "2 900 000 Kč")],
    })
    monkeypatch.setattr(m, "CeskerealityClient", lambda **kw: fake)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, counts, total, pages, complete = portal.probe_category(
        {"sale_type": "prodej", "category": "byty"},
        conn=None, dry_run=True, limiter=None, probe_pages=2,
    )
    # The verified URL shape: nationwide www host + the nejnovejsi sort slug in
    # search_url's sub_slug slot, ?strana=N for page 2+.
    assert fake.urls == [
        "https://www.ceskereality.cz/prodej/byty/nejnovejsi/",
        "https://www.ceskereality.cz/prodej/byty/nejnovejsi/?strana=2",
    ]
    assert pages == 2 and len(seen) == 3
    assert counts["found_new"] == 3
    assert total == 8439
    assert complete is False        # a probe can never justify a delisting sweep


def test_probe_category_early_stops_on_all_known_page(monkeypatch):
    fake = _NewestClient({
        1: [("7000001", "3 200 000 Kč"), ("7000002", "4 100 000 Kč")],
        2: [("7000003", "2 900 000 Kč")],
    })
    monkeypatch.setattr(m, "CeskerealityClient", lambda **kw: fake)
    stored = {
        "7000001": {"id": 51, "sreality_id": -1, "price_czk": 3_200_000, "last_seen_at": None},
        "7000002": {"id": 52, "sreality_id": -2, "price_czk": 4_100_000, "last_seen_at": None},
    }
    touched: list[int] = []
    enqueued: list[tuple] = []
    monkeypatch.setattr(
        m.db, "index_summary_native",
        lambda _c, src, ids: {i: stored[i] for i in ids if i in stored})
    monkeypatch.setattr(
        m.db, "touch_listings_by_id", lambda _c, pks: touched.extend(pks) or len(pks))
    monkeypatch.setattr(
        m.db, "enqueue_detail", lambda _c, src, entries: enqueued.extend(entries) or len(entries))
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    _seen, counts, _total, pages, _complete = portal.probe_category(
        {"sale_type": "prodej", "category": "byty"},
        conn=object(), dry_run=False, limiter=None, probe_pages=3,
    )
    assert pages == 1               # page 1 all-known -> never fetched page 2
    assert len(fake.urls) == 1
    assert counts["found_new"] == 0
    assert enqueued == []           # unchanged prices -> nothing enqueued
    assert sorted(touched) == [51, 52]   # but last_seen was bumped (by surrogate id)


def test_probe_category_enqueues_new_and_changed_with_priorities(monkeypatch):
    fake = _NewestClient({
        1: [("7000001", "3 200 000 Kč"), ("7000002", "4 100 000 Kč")],
    })
    monkeypatch.setattr(m, "CeskerealityClient", lambda **kw: fake)
    stored = {  # 7000002 known at an OLD price -> changed; 7000001 unknown -> new
        "7000002": {"id": 52, "sreality_id": -2, "price_czk": 3_900_000, "last_seen_at": None},
    }
    enqueued: list[tuple] = []
    touched: list[int] = []
    monkeypatch.setattr(
        m.db, "index_summary_native",
        lambda _c, src, ids: {i: stored[i] for i in ids if i in stored})
    monkeypatch.setattr(
        m.db, "touch_listings_by_id", lambda _c, pks: touched.extend(pks) or len(pks))
    monkeypatch.setattr(
        m.db, "enqueue_detail", lambda _c, src, entries: enqueued.extend(entries) or len(entries))
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    _seen, counts, _total, _pages, _complete = portal.probe_category(
        {"sale_type": "prodej", "category": "byty"},
        conn=object(), dry_run=False, limiter=None, probe_pages=1,
    )
    assert counts["found_new"] == 1
    by_id = {e[0]: e for e in enqueued}
    assert by_id["7000001"][3] == m.db.QUEUE_PRIORITY_NEW
    assert by_id["7000002"][3] == m.db.QUEUE_PRIORITY_CHANGED
    assert by_id["7000002"][2] == 4_100_000     # refreshed observed price
    assert touched == [52]                       # the repriced row is touched too (touch-all)
    # detail_ref is the absolute detail URL the drain fetches
    assert by_id["7000001"][1].startswith("https://www.ceskereality.cz/")


def test_client_routes_through_proxy_when_env_set(monkeypatch):
    monkeypatch.setenv("SCRAPER_PROXY_URL", "http://u:p@gw.example.com:823")
    c = CeskerealityClient()
    assert c._session.proxies.get("https") == "http://u:p@gw.example.com:823"


def test_client_no_proxy_when_env_unset(monkeypatch):
    monkeypatch.delenv("SCRAPER_PROXY_URL", raising=False)
    c = CeskerealityClient()
    assert not c._session.proxies            # falls back to the direct IP


# --- the laundered kraj: a throttled slice must not read as an empty one -----
#
# Found by adversarial review of the first cut of this walk, and REPRODUCED: a
# throttled page renders the shell with a correct H1, zero cards and no count
# phrase — byte-for-byte the shape of a genuinely empty kraj, because the count
# comes from the same query as the cards and vanishes with them. With the
# national cross-check written fail-open, that produced complete=True while a
# whole kraj was missing: 5,200 of 5,600 rows collected, and the walk claimed a
# clean sweep. Throttling is correlated, so the national probe is degraded at
# exactly the moment the slices are.


class _LaunderedKrajClient(_PartitionClient):
    """One kraj is throttled: it answers 200 with a correct H1 and no results,
    exactly as a real empty kraj does. Every other kraj is healthy."""

    def __init__(self, counts, throttled: str, national=None, stay_empty=True):
        super().__init__(counts, national=national)
        self._throttled = throttled
        self._stay_empty = stay_empty
        self.rereads = 0

    def fetch_search(self, url):  # noqa: ANN001
        kraj = _kraj_of(url)
        if kraj == self._throttled:
            self.urls.append(url)
            if not self._stay_empty and self.rereads:
                # A transient throttle: the re-read succeeds and the real page
                # comes back. The walk must NOT have called it empty.
                return super().fetch_search(url)
            self.rereads += 1
            return _page_html(None, [], heading=f"Prodej bytů {kraj}"), 200
        return super().fetch_search(url)


def test_a_throttled_kraj_laundered_as_empty_costs_fetches_not_listings(monkeypatch):
    """A throttled kraj that renders an empty shell twice reads as an empty
    region, and the national cross-check that used to catch it is gone from
    the verdict (it made rentals impossible to complete). What protects the
    region's rows now is the rule itself: a complete walk NOMINATES, the page
    decides. The 400 unseen rows get a page check, come back alive, and are
    refreshed; nothing is deleted. The walk still reports the national total
    as its result size, so the shortfall stays visible on the RECONCILE line."""
    counts = {k: 400 for k in KRAJ_SLUGS}
    fake = _LaunderedKrajClient(counts, throttled="zlinsky-kraj")
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    seen, _c, total, _pages, complete = _walk(portal, fake, monkeypatch)
    assert complete is True
    assert total == 5600 and len(seen) == 5200      # the gap is on the record


def test_an_empty_slice_is_confirmed_by_a_second_read(monkeypatch):
    """The mechanism: a genuinely empty kraj is stable across two reads, a
    throttle is not. One extra request, only for one-page slices."""
    counts = {k: 400 for k in KRAJ_SLUGS}
    counts["zlinsky-kraj"] = 0
    fake = _PartitionClient(counts)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    _seen, _c, _t, _pages, complete = _walk(portal, fake, monkeypatch)
    assert complete is True
    empty_reads = [u for u in fake.urls if "zlinsky-kraj" in u]
    assert len(empty_reads) >= 2, "the empty slice was accepted on a single read"
    empty = portal._walk_slice(fake, "prodej", "byty", "zlinsky-kraj")
    assert (empty.stop, empty.reached_end) == ("empty_confirmed", True)


def test_an_unmeasurable_national_probe_no_longer_decides(monkeypatch):
    """The national probe is observability, not verdict: fourteen exhausted
    regions make the category complete whether or not the national page could
    be read, and the result size falls back to the regions' declared sum."""
    counts = {k: 400 for k in KRAJ_SLUGS}
    fake = _PartitionClient(counts)
    monkeypatch.setattr(
        m.CeskerealityPortal, "_nationwide_total", lambda *a, **k: None)
    portal = m.CeskerealityPortal(default_config("ceskereality"))
    _seen, _c, total, _pages, complete = _walk(portal, fake, monkeypatch)
    assert complete is True
    assert total == 5600                            # declared_sum, not None

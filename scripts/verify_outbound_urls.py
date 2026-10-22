"""Weekly live conformance of stored portal URLs (`outbound_url_conformance`).

The portal-URL contract (docs/design/portal-listing-url.md) makes `listings.source_url` a
stored fact for all nine portals; sreality's is assembled from a closed codebook of its
own sub-category slugs. Two offline rails already guard it — the codebook test and the
weekly re-derivation parity — but neither asks the portal. This one does, once a week,
with ~44 HEAD requests: it samples ACTIVE ads, sends `HEAD` to each stored URL, and
records what came back.

A registered check of scripts/verify_pipeline.py (cadence one week, budget `PROBE_BUDGET_S`),
run when due from the GitHub lanes, so the probe leaves from an address other than the
scrapers'. The HEAD loop keeps that budget itself, since nothing outside can preempt a request:
each HEAD's connect and read timeouts are min(20 s, time left); DNS is not bounded, so one
request can run past it, and the job's 180 s headroom absorbs that. The URLs it did not reach
are reported as `unprobed`, never as failures.
Status-code HEAD only — never follow a redirect, never read a body, never authenticate,
never reuse this as an ingest surface (sreality's SSR page 302s into a login.seznam.cz
chain; the scraper reaches sreality by id through /api/v1/estates). The HEADs go out on
scraper.egress's session: a stored URL is a page's (the tenth portal's are any site's), so
each connection is resolved, checked and pinned to a public address, and a URL on a private
one is a link that does not open, never a request into our own network.

Rule 21 (no portal name in shared check code) does not apply here: its rail scans
scripts/checks/, and this module sits outside it on purpose, keeping sreality's codebook
rotation and URL-segment rule until a Portal seam takes them (#1860).

Sampling is STRATIFIED and ROTATING, not uniform: uniform sampling would never test a
rare code. Each run covers a quarter of the sreality codebook chosen by the ISO week
(so the whole codebook is covered every four weeks with no new state table), three
random active ads per code, plus one active ad per crawler portal. The draw is probed in an
order shuffled by the ISO week (reproducible within it), so a slow sreality never starves the
crawler portals that used to queue behind every sreality code.

One cell per portal of the fleet (location_data.claims_common.SOURCES) plus one `sample`
cell, and thresholding is by CONCENTRATION, not raw rate. A 404 on a single ad is most likely
a delisting rule #3 has not caught up with; a URL-SHAPE defect is systematic — every ad of
one (category_main, category_sub_cb) stratum fails together. So a portal's cell is a Problem
when one of its strata with >= cell_min samples is more than cell_fail_share non-conforming,
and OK otherwise with any scattered failure named; `sample` is Watch when the Wilson lower
bound of the whole sample's non-conforming share passes warn_share. A 403 or 405 is the portal
refusing the HEAD (a bot wall, a server that allows no HEAD), not an answer: it judges nothing,
and a portal whose every link was refused, or of which nothing was drawn or probed, is Not
measured. The whole check is Not measured when nothing was drawn, or the budget ran out before
half of the draw was probed (a verdict over a sliver of the sample could read a slug defect it
never reached as OK).
Active ads only — a delisted sreality page 404s at any URL, so the archive is guarded by
the offline rails, not this.
"""

from __future__ import annotations

import datetime as _dt
import logging
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests

from location_data.claims_common import SOURCES
from scraper import egress, sreality_url
from scraper.sreality_url import critical_segments
from scripts.checks.clock import fetchall
from scripts.checks.common import cell, result, wilson_lower

LOG = logging.getLogger(__name__)

CHECK_KEY = "outbound_url_conformance"
ROTATION = 4                 # quarters of the codebook per ISO week
ROWS_PER_CB = 3
# One active ad of every other portal of the fleet; sreality is drawn by its codebook.
CRAWLER_SOURCES = tuple(source for source in SOURCES if source != "sreality")
SAMPLE_SUBJECT = "sample"
# What one HEAD said: the link opens its page, it does not, or the portal refused to answer.
OPENS, BROKEN, REFUSED = "opens", "broken", "refused"
# A bot wall answers 403 and a server that allows no HEAD 405: neither says whether a link opens.
_REFUSED_STATUSES = frozenset({403, 405})
_PROBE_INTERVAL_S = 0.5
# A verdict needs at least this share of the draw probed: the concentration rule judges whole
# strata, and the strata the budget never reached could hold the defect.
_MIN_PROBED_SHARE = 0.5
# The check's whole budget, sampling included: verify_pipeline registers the check with it.
PROBE_BUDGET_S = 120.0
_REQUEST_TIMEOUT_S = 20.0
_PROBE_HEADERS = {
    "Accept-Language": "cs,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

_SREALITY_SAMPLE_SQL = """
    SELECT id, source, category_main, category_sub_cb, source_url
    FROM listings
    WHERE source = 'sreality' AND is_active AND source_url IS NOT NULL
      AND category_sub_cb = %(cb)s
    ORDER BY random()
    LIMIT %(n)s::int
"""

_CRAWLER_SAMPLE_SQL = """
    SELECT id, source, category_main, category_sub_cb, source_url
    FROM listings
    WHERE source = %(source)s AND is_active AND source_url IS NOT NULL
    ORDER BY random()
    LIMIT 1
"""


@dataclass(frozen=True)
class Sample:
    id: int
    source: str
    category_main: str | None
    category_sub_cb: int | None
    source_url: str


def codes_for_week(week: int, rotation: int = ROTATION) -> list[int]:
    """The rotating quarter of the codebook this ISO week probes."""
    return [cb for i, cb in enumerate(sorted(sreality_url.SUB_SLUG)) if i % rotation == week % rotation]


def conforms(source: str, status: int, location: str | None, stored: str) -> bool:
    """200 passes. A redirect passes only when it keeps the page: for sreality the
    404-critical segments (type/main/sub/id) must match — a locality-only drift is the
    same page; for a crawler portal any same-host 3xx is its own canonicalisation. A
    relative Location (sreality 301s to a bare path) is read against the URL it answered."""
    if status == 200:
        return True
    if status in (301, 302, 307, 308) and location:
        target = urljoin(stored, location)
        if source == "sreality":
            segments = critical_segments(stored)
            return segments is not None and critical_segments(target) == segments
        return target.split("/", 3)[:3] == stored.split("/", 3)[:3]
    return False


def outcome(source: str, status: int, location: str | None, stored: str) -> str:
    """What one HEAD said about a stored link: OPENS, BROKEN, or REFUSED (a 403 or 405)."""
    if status in _REFUSED_STATUSES:
        return REFUSED
    return OPENS if conforms(source, status, location, stored) else BROKEN


def _portal_cell(portal: str, probed: list[tuple[Sample, str]], drawn: int,
                 thresholds: dict[str, Any]) -> dict[str, Any]:
    """One portal: a Problem when a stratum of its links fails together, else OK."""
    cell_min = int(thresholds["outbound_url_conformance_cell_min"])
    cell_fail = float(thresholds["outbound_url_conformance_cell_fail_share"])
    answered = [(s, said) for s, said in probed if said != REFUSED]
    broken = sum(said == BROKEN for _, said in answered)
    refused = len(probed) - len(answered)
    facts: dict[str, Any] = {"drawn": drawn, "probed": len(probed), "refused": refused,
                             "broken": broken}
    if not drawn:
        return cell(portal, "unknown", "No draw: no active ad of this portal with a stored link "
                    "was drawn this week.", unit="%", portal=portal, facts=facts)
    if not probed:
        return cell(portal, "unknown", f"Not probed: the check's budget ran out before its {drawn} "
                    "sampled link(s).", unit="%", portal=portal, facts=facts)
    if not answered:
        return cell(portal, "unknown", f"Refused: the portal answered all {refused} sampled "
                    "link(s) with 403 or 405 (a bot wall), so nothing was verified.", unit="%",
                    portal=portal, facts=facts)
    strata: dict[tuple[str | None, int | None], list[bool]] = defaultdict(list)
    for s, said in answered:
        strata[(s.category_main, s.category_sub_cb)].append(said == BROKEN)
    facts["strata"] = [{"category_main": main, "category_sub_cb": cb, "sampled": len(bad),
                        "broken": sum(bad)} for (main, cb), bad in strata.items()]
    failed = [f for f in facts["strata"]
              if f["sampled"] >= cell_min and f["broken"] / f["sampled"] > cell_fail]
    value = round(100 * broken / len(answered), 2)
    if failed:
        return cell(portal, "fail", (
            f"The links of {len(failed)} sub-categor{'y' if len(failed) == 1 else 'ies'} fail "
            "together — a slug defect, not a delisting: "
            + "; ".join(f"{f['category_main']}/{f['category_sub_cb']} {f['broken']}/{f['sampled']}"
                        for f in failed[:4])
            + " — check how its links are built (sreality's: scraper/sreality_url.py against its "
              "sitemap)."), value=value, unit="%", portal=portal, facts=facts)
    said = f"{len(answered) - broken} of {len(answered)} sampled links open the right page"
    if broken:
        said += (f"; {broken} did not, scattered (most likely delistings the presence check has "
                 "not reached)")
    if refused:
        said += f"; {refused} refused (403 or 405)"
    return cell(portal, "ok", said + ".", value=value, unit="%", portal=portal, facts=facts)


def _sample_cell(probed: list[tuple[Sample, str]], thresholds: dict[str, Any],
                 facts: dict[str, Any]) -> dict[str, Any]:
    """The whole sample: Watch when the Wilson lower bound of its broken share passes warn_share."""
    warn_share = float(thresholds["outbound_url_conformance_warn_share"])
    answered = [said for _, said in probed if said != REFUSED]
    broken = answered.count(BROKEN)
    facts = {**facts, "probed": len(probed), "answered": len(answered), "broken": broken,
             "refused": len(probed) - len(answered)}
    if not answered:
        return cell(SAMPLE_SUBJECT, "unknown", f"Not measured: all {len(probed)} probed links were "
                    "refused (403 or 405).", unit="%", facts=facts)
    share, lower = broken / len(answered), wilson_lower(broken, len(answered)) or 0.0
    facts.update(share=round(share, 4), lower_bound=round(lower, 4))
    said = (f"{broken} of {len(answered)} answered links did not open the right page ({share:.1%}, "
            f"lower bound {lower:.1%})")
    if lower > warn_share:
        return cell(SAMPLE_SUBJECT, "warn", f"{said}: Watch over {warn_share:.0%}, more than "
                    "scattered delistings explain.", value=round(100 * share, 2), unit="%",
                    facts=facts)
    return cell(SAMPLE_SUBJECT, "ok", said + ".", value=round(100 * share, 2), unit="%",
                facts=facts)


def classify(probed: list[tuple[Sample, str]], thresholds: dict[str, Any], *,
             drawn: list[Sample] | None = None,
             facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """The verdict over (sample, outcome) pairs: a cell per portal and the `sample` cell."""
    drawn_n = Counter(s.source for s in (drawn if drawn is not None else [s for s, _ in probed]))
    by_portal: dict[str, list[tuple[Sample, str]]] = defaultdict(list)
    for s, said in probed:
        by_portal[s.source].append((s, said))
    portals = [*SOURCES, *sorted(set(drawn_n) - set(SOURCES))]
    cells = [_portal_cell(p, by_portal[p], drawn_n[p], thresholds) for p in portals]
    cells.append(_sample_cell(probed, thresholds, {"drawn": sum(drawn_n.values()), **(facts or {})}))
    unprobed = sum(drawn_n.values()) - len(probed)
    budget = (facts or {}).get("budget_seconds")
    spent = f"the check's {budget:.0f} s budget" if budget is not None else "the check's budget"
    not_read = ({"links": f"{unprobed} of {sum(drawn_n.values())} sampled links were not probed: "
                          f"{spent} ran out"} if unprobed > 0 else {})
    return result(CHECK_KEY, cells, not_read)


def draw_samples(conn: Any, week: int) -> list[Sample]:
    """This ISO week's sample, in an order shuffled by the week so no portal queues last."""
    out: list[Sample] = []
    for cb in codes_for_week(week):
        out.extend(Sample(*r) for r in fetchall(conn, _SREALITY_SAMPLE_SQL, {"cb": cb, "n": ROWS_PER_CB}))
    for source in CRAWLER_SOURCES:
        out.extend(Sample(*r) for r in fetchall(conn, _CRAWLER_SAMPLE_SQL, {"source": source}))
    random.Random(week).shuffle(out)
    return out


def probe_all(samples: list[Sample], budget_s: float) -> list[tuple[Sample, str]]:
    """HEAD each sample's stored URL until `budget_s` is spent: each sample probed, in order,
    with what its HEAD said (OPENS, BROKEN or REFUSED)."""
    session = egress.safe_session()
    deadline = time.monotonic() + budget_s
    results: list[tuple[Sample, str]] = []
    for i, s in enumerate(samples):
        if i:
            time.sleep(_PROBE_INTERVAL_S)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout = min(_REQUEST_TIMEOUT_S, remaining)
        try:
            # One wall-clock deadline per HEAD, header read included (egress's watchdog): a
            # server dripping its status line cannot hold the check past its budget.
            resp = egress.fetch(s.source_url, method="HEAD", follow_redirects=False,
                                headers=_PROBE_HEADERS, session=session, deadline_s=timeout,
                                read_timeout_s=timeout)
        except requests.Timeout as exc:
            # Cut short by our own deadline, the request judged nothing: it joins the
            # unprobed rest instead of counting as a URL that does not resolve.
            if timeout < _REQUEST_TIMEOUT_S:
                break
            LOG.warning("CONFORMANCE %s id=%s error=%s", s.source, s.id, exc)
            said = BROKEN
        except (requests.RequestException, egress.EgressRefused) as exc:
            LOG.warning("CONFORMANCE %s id=%s error=%s", s.source, s.id, exc)
            said = BROKEN
        else:
            location = resp.headers.get("Location")
            said = outcome(s.source, resp.status, location, s.source_url)
            if said != OPENS:
                LOG.warning("CONFORMANCE %s id=%s cb=%s status=%s location=%s url=%s outcome=%s",
                            s.source, s.id, s.category_sub_cb, resp.status, location,
                            s.source_url, said)
        results.append((s, said))
    if len(results) < len(samples):
        LOG.warning("CONFORMANCE budget %.0fs spent: %d of %d sampled URLs not probed",
                    budget_s, len(samples) - len(results), len(samples))
    return results


def check_outbound_url_conformance(conn: Any, thresholds: dict[str, Any], *,
                                   budget_s: float = PROBE_BUDGET_S) -> dict[str, Any]:
    """This ISO week's sample of stored portal URLs, probed by HEAD within `budget_s` and
    judged per portal over the links it reached, once those are half the draw or more."""
    started = time.monotonic()
    week = _dt.datetime.now(_dt.timezone.utc).isocalendar().week
    codes = codes_for_week(week)
    samples = draw_samples(conn, week)
    LOG.info("CONFORMANCE week=%d codes=%s sampled=%d", week, codes, len(samples))
    if not samples:
        detail = (f"no active ad with a stored URL was drawn for ISO week {week}, so "
                  "nothing was verified")
        return {
            "check_key": CHECK_KEY, "status": "unknown", "value": None,
            "details": {"skipped": detail, "week": week, "codes": codes},
            "message": f"Portal URL conformance verified NOTHING — {detail}.",
        }
    probed = probe_all(samples, budget_s - (time.monotonic() - started))
    spent = {"week": week, "codes": codes, "unprobed": len(samples) - len(probed),
             "budget_seconds": round(budget_s, 1)}
    if len(probed) < len(samples) * _MIN_PROBED_SHARE:
        detail = (f"probed {len(probed)} of {len(samples)} sampled URLs before the "
                  f"{budget_s:.0f} s budget ran out")
        return {
            "check_key": CHECK_KEY, "status": "unknown", "value": None,
            "details": {"skipped": detail, **spent},
            "message": (f"Portal URL conformance is Not measured: {detail}, under the half "
                        "of the draw a verdict needs."),
        }
    return classify(probed, thresholds, drawn=samples, facts=spent)

"""Weekly live conformance of stored portal URLs (`outbound_url_conformance`).

The portal-URL contract (docs/design/portal-listing-url.md) makes `listings.source_url` a
stored fact for all nine portals; sreality's is assembled from a closed codebook of its
own sub-category slugs. Two offline rails already guard it — the codebook test and the
weekly re-derivation parity — but neither asks the portal. This one does, once a week,
with ~44 HEAD requests: it samples ACTIVE ads, sends `HEAD` to each stored URL, and
records what came back.

A registered check of scripts/verify_pipeline.py (cadence one week, budget 120 s), run when
due from the GitHub lanes, so the probe leaves from an address other than the scrapers'.
Status-code HEAD only — never follow a redirect, never read a body, never authenticate,
never reuse this as an ingest surface (sreality's SSR page 302s into a login.seznam.cz
chain; the scraper reaches sreality by id through /api/v1/estates).

Sampling is STRATIFIED and ROTATING, not uniform: uniform sampling would never test a
rare code. Each run covers a quarter of the sreality codebook chosen by the ISO week
(so the whole codebook is covered every four weeks with no new state table), three
random active ads per code, plus one active ad per crawler portal.

Thresholding is by CONCENTRATION, not raw rate. A 404 on a single ad is most likely a
delisting rule #3 has not caught up with; a URL-SHAPE defect is systematic — every ad
of one (category_main, category_sub_cb) cell fails together. So: `fail` when any cell
with >= cell_min samples is >= cell_fail_share non-conforming; `warn` when the overall
non-conformance exceeds warn_share; `ok` otherwise; `unknown` when nothing was drawn.
Active ads only — a delisted sreality page 404s at any URL, so the archive is guarded by
the offline rails, not this.
"""

from __future__ import annotations

import datetime as _dt
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import requests

from scraper import sreality_url
from scraper.sreality_url import critical_segments

LOG = logging.getLogger(__name__)

CHECK_KEY = "outbound_url_conformance"
ROTATION = 4                 # quarters of the codebook per ISO week
ROWS_PER_CB = 3
CRAWLER_SOURCES = ("bazos", "bezrealitky", "idnes", "mmreality", "remax",
                   "ceskereality", "realitymix", "maxima")
_PROBE_INTERVAL_S = 0.5
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
    same page; for a crawler portal any same-host 3xx is its own canonicalisation."""
    if status == 200:
        return True
    if status in (301, 302, 307, 308) and location:
        if source == "sreality":
            return critical_segments(location) == critical_segments(stored)
        return location.split("/", 3)[:3] == stored.split("/", 3)[:3]
    return False


def classify(samples: list[tuple[Sample, bool]], thresholds: dict[str, Any]) -> dict[str, Any]:
    """The pure status decision over (sample, conforming) pairs."""
    warn_share = float(thresholds["outbound_url_conformance_warn_share"])
    cell_min = int(thresholds["outbound_url_conformance_cell_min"])
    cell_fail = float(thresholds["outbound_url_conformance_cell_fail_share"])
    cells: dict[tuple[str, str | None, int | None], list[bool]] = defaultdict(list)
    for s, ok in samples:
        cells[(s.source, s.category_main, s.category_sub_cb)].append(ok)
    failed_cells = []
    for (src, main, cb), oks in cells.items():
        bad = oks.count(False)
        if len(oks) >= cell_min and bad / len(oks) >= cell_fail:
            failed_cells.append({"source": src, "category_main": main,
                                 "category_sub_cb": cb, "sampled": len(oks), "failed": bad})
    total = len(samples)
    bad_total = sum(1 for _, ok in samples if not ok)
    share = (bad_total / total) if total else 0.0
    status = "fail" if failed_cells else "warn" if share > warn_share else "ok"
    return {
        "check_key": CHECK_KEY,
        "status": status,
        "value": round(share * 100, 2),
        "details": {
            "sampled": total, "non_conforming": bad_total,
            "failed_cells": failed_cells,
            # Not `cells`: that key is the health ladder's per-cell verdict contract.
            "sampled_cells": [{"source": k[0], "category_main": k[1], "category_sub_cb": k[2],
                               "sampled": len(v), "failed": v.count(False)}
                              for k, v in cells.items()],
            "warn_share": warn_share, "cell_min": cell_min, "cell_fail_share": cell_fail,
        },
        "message": (
            f"{len(failed_cells)} URL cell(s) fail together — a slug defect, not a delisting: "
            + "; ".join(f"{c['source']} {c['category_main']}/{c['category_sub_cb']} "
                        f"{c['failed']}/{c['sampled']}" for c in failed_cells[:4])
            + " — check scraper/sreality_url.py against sreality's sitemap."
            if failed_cells else
            f"{bad_total} of {total} sampled portal URLs did not resolve"
            + (" (scattered — most likely delistings the presence check has not reached)."
               if bad_total else ".")
        ),
    }


def draw_samples(conn: Any, week: int) -> list[Sample]:
    out: list[Sample] = []
    with conn.cursor() as cur:
        for cb in codes_for_week(week):
            cur.execute(_SREALITY_SAMPLE_SQL, {"cb": cb, "n": ROWS_PER_CB})
            out.extend(Sample(*r) for r in cur.fetchall())
        for source in CRAWLER_SOURCES:
            cur.execute(_CRAWLER_SAMPLE_SQL, {"source": source})
            out.extend(Sample(*r) for r in cur.fetchall())
    return out


def probe_all(samples: list[Sample]) -> list[tuple[Sample, bool]]:
    session = requests.Session()
    results: list[tuple[Sample, bool]] = []
    for s in samples:
        try:
            resp = session.head(s.source_url, headers=_PROBE_HEADERS,
                                allow_redirects=False, timeout=20)
            ok = conforms(s.source, resp.status_code, resp.headers.get("Location"), s.source_url)
            if not ok:
                LOG.warning("CONFORMANCE %s id=%s cb=%s status=%s location=%s url=%s",
                            s.source, s.id, s.category_sub_cb, resp.status_code,
                            resp.headers.get("Location"), s.source_url)
        except requests.RequestException as exc:
            LOG.warning("CONFORMANCE %s id=%s error=%s", s.source, s.id, exc)
            ok = False
        results.append((s, ok))
        time.sleep(_PROBE_INTERVAL_S)
    return results


def check_outbound_url_conformance(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """This ISO week's sample of stored portal URLs, probed by HEAD and judged by concentration."""
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
    result = classify(probe_all(samples), thresholds)
    result["details"].update({"week": week, "codes": codes})
    return result

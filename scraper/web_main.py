"""Orchestrator for the tenth portal, `web` — any site an operator pastes — on the shared framework.

Runnable as `python -m scraper.web_main`. `web` is a portal like the nine (rule 21): a client
(`web_client`, over `scraper.egress`), a parser (`web_parser`, deterministic), this adapter,
a config row (`scraper.portal._DEFAULTS['web']`) and the shared `portal_runner`. Its ads enter
through `POST /listings/ingest`, which queues the pasted URL on `listing_detail_queue`
(source='web', native id = `portal_identity.canonical_url`); the worker's drain fetches the
page, parses it and writes it through `listing_write.write_listings` like any other ad.

Its two honest differences are declared seams, not branches:

  * **No index.** `indexless = True`: the walk has nothing to page, so `walk_category`
    returns an empty seen set that reached the end (declared total 0, so coverage reads
    `complete`); its one unit has no labels, so no nomination scope, and every active `web`
    row is an orphan the runner nominates without a seen set (it refuses an empty one for
    every other portal): no live queue row, no completion within
    `delist_policy.RECHECK_THROTTLE`, oldest sighting first. `web_index_walk.yml` (every
    6 h, so the 24 h throttle and not the cron sets the cadence: each ad is checked every
    24–30 h) is that nomination; the drain fetches each page and the page decides (rule 3).
    `delist_flip_cap` never throttles
    a portal under its 2,000-row floor, and the gone-rate breaker needs 20 ingest fetches in
    a run, so neither binds here: a dead web ad closes after two gone verdicts at least
    RECHECK_THROTTLE (24 h) apart — 24–60 h after it dies. Nothing special-cases that.
  * **A page we have never seen.** 404/410 is gone. A redirect that LEAVES the page — to
    another site, or up to an ancestor of its path (the home page, the listing index), or
    off the query that named it — is gone too, except when it points at one of the nine
    portals' ads (the client never requests that hop: the portal's own client fetches it):
    then the paste was a shortener or a link out, and the ad is that portal's.
    The fetch returns a re-key item; `write_details` hands it to the runner's
    `rekey_detail` (the portal ad queued at NEW, the `web` row's gone verdict through the one
    gone step: a never-written row is just dequeued as gone, a held one closes on rule 3's
    hysteresis) and writes everything else through the unmodified `PortalDefaults.write_details`.

The probe lane has nothing to probe: `probe_category` returns an empty sighting and
`set_index_page_cap` is a no-op. The Chrome extension does not reach `web`: its manifest
`matches` derive from the nine portals' hosts.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any
from urllib.parse import urlsplit

from scraper import portal_runner
from scraper.portal import PortalConfig, deadline_reached
from scraper.portal_base import ListingGoneError
from scraper.portal_identity import WEB, Resolved, canonical_url, resolve_url
from scraper.portal_runner import DrainItem
from scraper.rate_limit import RateLimiter
from scraper.web_client import WebClient
from scraper.web_parser import parse_detail

LOG = logging.getLogger(__name__)
SOURCE = WEB

_EMPTY_COUNTS = {"found_new": 0, "enqueued": 0}



def _site(host: str | None) -> str:
    host = (host or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def left_the_page(requested: str, final: str) -> bool:
    """Did the fetch's redirects leave the ad's page? Another site, an ancestor of its path
    (the home page included) or the same path without the query that named the ad. A
    scheme change, a `www.`, a trailing slash, a tracking key or a sibling path (a new slug)
    is the page."""
    a, b = urlsplit(canonical_url(requested) or requested), urlsplit(canonical_url(final) or final)
    if _site(a.hostname) != _site(b.hostname):
        return True
    asked, landed = a.path.rstrip("/"), b.path.rstrip("/")
    if asked == landed:
        return bool(a.query) and not b.query
    return asked.startswith(landed + "/")


class WebPortal(portal_runner.PortalDefaults):
    """Any site as a Portal: no index, one agenda-wide descriptor, the page decides."""

    source = SOURCE
    indexless = True
    index_rate = 1.0

    def __init__(self, config: PortalConfig) -> None:
        self._categories = config.categories
        self.index_rate = config.limits.index_rate
        self.shared_rate_limiter = config.limits.shared_rate_limiter
        self.price_change_min_pct = config.limits.price_change_min_pct

    # --- index-walk seams (there is no index) ---
    def categories(self) -> list[dict[str, Any]]:
        return list(self._categories)

    def category_labels(self, category: dict[str, Any]) -> tuple[str | None, str | None]:
        return (None, None)

    def walk_category(
        self, category: dict[str, Any], conn: Any, dry_run: bool, limiter: RateLimiter,
        deadline: float | None = None,
    ) -> tuple[set[str], dict[str, int], int | None, int, bool]:
        # Nothing to page, so the walk ends where it starts; a deadline already spent is
        # still OUR stop and nominates nothing (rule 3).
        if deadline_reached(deadline):
            return set(), dict(_EMPTY_COUNTS), 0, 0, False
        return set(), dict(_EMPTY_COUNTS), 0, 0, True

    def set_index_page_cap(self, pages: int | None) -> None:
        return None

    def probe_category(
        self, category: dict[str, Any], conn: Any, dry_run: bool, limiter: RateLimiter,
        probe_pages: int,
    ) -> tuple[set[str], dict[str, int], int | None, int, bool]:
        return set(), dict(_EMPTY_COUNTS), 0, 0, True

    # --- detail-drain seams ---
    def fetch_detail(self, client: WebClient, native_id: str, detail_ref: str | None) -> DrainItem:
        url = detail_ref or native_id
        try:
            html, status, final_url = client.fetch_detail(url)
        except ListingGoneError:
            return DrainItem(native_id=native_id, kind="gone")
        except Exception as exc:  # noqa: BLE001 - one listing must not kill the run
            return DrainItem(native_id=native_id, kind="error", error=str(exc))
        if left_the_page(url, final_url):
            target = resolve_url(final_url)
            if isinstance(target, Resolved) and target.source != SOURCE:
                LOG.info("DETAIL id=%s redirects to a %s ad (%s): re-keyed",
                         native_id, target.source, target.native_id)
                return DrainItem(native_id=native_id, kind="ok", payload={"rekey": target})
            LOG.info("DETAIL id=%s left its page for %s: gone", native_id, final_url)
            return DrainItem(native_id=native_id, kind="gone")
        try:
            listing = parse_detail(html, source_url=native_id, final_url=final_url)
        except Exception as exc:  # noqa: BLE001
            return DrainItem(native_id=native_id, kind="error", error=str(exc))
        if listing.source_id_native != native_id:
            return DrainItem(native_id=native_id, kind="error",
                             error=f"queued id is not canonical: {listing.source_id_native}")
        return DrainItem(
            native_id=native_id, kind="ok",
            payload={"listing": listing, "html": html, "status": status, "url": native_id},
        )

    def write_details(self, conn: Any, items: list[DrainItem]) -> dict[str, int]:
        """Re-keys first (`portal_runner.rekey_detail`: the portal ad queued at NEW, the `web`
        row's gone verdict through the runner's one gone step), then the default flush for
        every page we parsed."""
        pages: list[DrainItem] = []
        for item in items:
            target = item.payload.get("rekey") if isinstance(item.payload, dict) else None
            if target is None:
                pages.append(item)
                continue
            portal_runner.rekey_detail(conn, SOURCE, item.native_id, target)
        if not pages:
            return {"new": 0, "updated": 0, "unchanged": 0, "images_discovered": 0}
        return super().write_details(conn, pages)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    portal_runner.configure_logging(args.verbose)

    config = portal_runner.load_config(SOURCE, args.dry_run)
    portal = WebPortal(config)

    workers = args.workers if args.workers is not None else config.limits.detail_workers
    rate = args.rate if args.rate is not None else config.limits.detail_rate
    max_detail = (
        args.max_detail if args.max_detail is not None
        else config.limits.max_detail_per_run
    )

    # Omitting both flags runs the nominating walk then the drain in one job. In production
    # the walk runs from web_index_walk.yml and the worker's drain lane fetches.
    rc = 0
    if not args.drain_only:
        rc = portal_runner.run_phase(
            portal, "index", portal_runner.run_index_walk, args.dry_run,
            max_seconds=args.max_seconds,
        )
    if rc == 0 and not args.index_only:
        rc = portal_runner.run_phase(
            portal, "detail", portal_runner.run_detail_drain, args.dry_run,
            max_claims=max_detail, detail_workers=workers, detail_rate=rate,
            max_seconds=args.max_seconds,
        )
    return rc


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="web: any pasted site (portal framework)")
    p.add_argument(
        "--max-detail", type=int, default=None,
        help="cap detail-drain claims per run (omit = drain the queue)",
    )
    p.add_argument(
        "--workers", type=int, default=None,
        help="detail-fetch workers (default: per-portal config)",
    )
    p.add_argument(
        "--rate", type=float, default=None,
        help="detail-fetch requests/second ceiling (default: per-portal config)",
    )
    p.add_argument(
        "--max-seconds", type=float, default=None,
        help="wall-clock budget for EITHER phase; the phase stops and finalizes cleanly",
    )
    p.add_argument(
        "--index-only", action="store_true",
        help="nominate the active web ads due a page check (no detail drain)",
    )
    p.add_argument(
        "--drain-only", action="store_true",
        help="drain the detail queue only (no nomination)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())

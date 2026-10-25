"""Ingestion entrypoint for price-stats datasets.

Resolves the configured municipalities (localities/suggest, cached), then walks
ONE queue of (dataset, locality) stalest first across the active datasets,
fetching each locality's {prodej, pronájem} monthly series from estate_prices
until the time budget; recomputes the per-city derived metrics of every dataset
it reached, and refreshes the map choropleth. Pure HTTP fetch + psycopg writes;
the session cookie comes from `scraper.sreality_auth`.

  python -m scraper.price_stats_main                 # all active datasets
  python -m scraper.price_stats_main --dataset-id 3  # one dataset
  python -m scraper.price_stats_main --resolve-only  # just refresh localities
  python -m scraper.price_stats_main --dry-run       # no writes
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scraper import price_stats_db as db
from scraper.portal import deadline_reached
from scraper.price_stats_client import AuthExpiredError, PriceStatsClient
from scraper.rate_limit import RateLimiter
from scraper.sreality_auth import get_session_cookies

LOG = logging.getLogger(__name__)

_DEFAULT_CITIES = Path(__file__).resolve().parents[1] / "data" / "price_stat_cities.json"
CATEGORIES = (1, 2)  # prodej, pronajem — every dataset covers both


def _load_city_names(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    cities = data.get("cities") if isinstance(data, dict) else data
    return [str(c).strip() for c in cities if str(c).strip()]


def _parse_ym(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        year, month = str(s).split("-")
        return (int(year), int(month))
    except (ValueError, AttributeError):
        return None


def _dataset_window(
    dataset: dict[str, Any],
    default_start: tuple[int, int],
    default_end: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Per-dataset scrape window, falling back to the CLI/today defaults."""
    return (
        _parse_ym(dataset.get("start_ym")) or default_start,
        _parse_ym(dataset.get("end_ym")) or default_end,
    )


def _dataset_localities(
    conn: Any,
    client: PriceStatsClient,
    dataset: dict[str, Any],
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Localities for a dataset (its selected obce, else all), stalest first.

    Selected obce with a precomputed admin_boundaries.sreality_id map straight
    through (resolve_obce). Any WITHOUT one are resolved on demand via
    localities/suggest + coordinate PIP (resolve_obce_by_geo), so a dataset can
    target the full obec breakdown — not only the spatial-join-mapped subset.
    """
    ids = _obec_ids(dataset)
    if ids and not dry_run:
        db.resolve_obce(conn, ids)
        names = db.unresolved_obec_names(conn, ids)
        if names:
            LOG.info("OBEC resolve-by-geo: %d unmapped obce", len(names))
            resolve_obce_by_geo(conn, client, names, dry_run=dry_run)
    return db.localities_ordered(conn, dataset["id"], ids)


def resolve_obce_by_geo(
    conn: Any, client: PriceStatsClient, names: list[str], *, dry_run: bool
) -> int:
    """Resolve unmapped obce by name, caching EVERY muni candidate per name.

    Each candidate carries its own coordinates, and upsert_locality PIP-places it
    to its own RÚIAN obec — so one 'Nová Ves' suggest resolves all ~30 distinct
    obce of that name, not just the first (the bug that left ~600 same-name obce
    unscrapeable). One suggest call per distinct name; ON CONFLICT makes the
    candidate upserts idempotent."""
    seen: set[str] = set()
    cached = 0
    for name in names:
        key = name.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            candidates = client.suggest_municipalities(name)
        except Exception as exc:  # one bad name must not sink the whole resolve
            LOG.warning("SUGGEST error name=%r: %s", name, exc)
            continue
        for c in candidates:
            if c.get("entity_id") is None or c.get("lat") is None:
                continue
            if not dry_run:
                db.upsert_locality(conn, c)
            cached += 1
    LOG.info("OBEC geo-resolve names=%d candidates_cached=%d", len(seen), cached)
    return cached


def resolve_localities(
    conn: Any, client: PriceStatsClient, city_names: list[str], *, dry_run: bool
) -> int:
    """Resolve any not-yet-cached city names to municipality entities."""
    known = {row["name"].casefold() for row in db.list_localities(conn)}
    added = 0
    for name in city_names:
        if name.casefold() in known:
            continue
        match = client.suggest_municipality(name)
        if not match or match.get("entity_id") is None:
            LOG.warning("SUGGEST miss city=%r", name)
            continue
        LOG.info("SUGGEST city=%r -> entity_id=%s", name, match["entity_id"])
        if not dry_run:
            db.upsert_locality(conn, match)
            known.add((match.get("name") or name).casefold())
        added += 1
    return added


@dataclass
class DatasetRun:
    """One dataset's share of a run: what to fetch, and its run row's tallies once reached."""

    dataset: dict[str, Any]
    localities: list[dict[str, Any]]
    start_ym: tuple[int, int]
    end_ym: tuple[int, int]
    run_id: int | None = None
    done: int = 0
    observations: int = 0


QueueItem = tuple[DatasetRun, dict[str, Any]]


def work_queue(runs: list[DatasetRun]) -> list[QueueItem]:
    """Every (dataset, locality) of the run, stalest first ACROSS datasets, never fetched first: a
    dataset bigger than one budget can no longer starve the datasets after it."""
    def staleness(item: QueueItem) -> tuple[bool, float, str, int]:
        run, loc = item
        last = loc.get("last_fetched_at")
        return last is not None, last.timestamp() if last else 0.0, loc["name"], run.dataset["id"]

    return sorted(((run, loc) for run in runs for loc in run.localities), key=staleness)


def _scrape_locality(
    conn: Any, client: PriceStatsClient, run: DatasetRun, loc: dict[str, Any], *,
    chunk_months: int, dry_run: bool,
) -> int:
    """Both categories' series for one locality; returns the observations written."""
    loc_obs = 0
    for category_type_cb in CATEGORIES:
        series = _fetch_with_auth_retry(
            client, run.dataset, loc, category_type_cb,
            start_ym=run.start_ym, end_ym=run.end_ym, chunk_months=chunk_months,
        )
        months = series["months"]
        LOG.info(
            "SERIES dataset=%s city=%s ct=%d months=%d",
            run.dataset["id"], loc["name"], category_type_cb, len(months),
        )
        if not dry_run and months:
            loc_obs += db.upsert_observations(
                conn,
                dataset_id=run.dataset["id"],
                entity_type=loc["entity_type"],
                entity_id=loc["entity_id"],
                category_type_cb=category_type_cb,
                months=months,
                run_id=run.run_id,
            )
    if not dry_run:
        # No prodej AND no pronájem series → mark insufficient-data so
        # future runs skip it (TTL); data → drop any stale marker.
        if loc_obs > 0:
            db.clear_no_data(conn, run.dataset["id"], loc["entity_type"], loc["entity_id"])
        else:
            db.record_no_data(conn, run.dataset["id"], loc["entity_type"], loc["entity_id"])
    return loc_obs


def run_queue(
    conn: Any,
    client: PriceStatsClient,
    queue: list[QueueItem],
    *,
    window_years: int,
    chunk_months: int,
    dry_run: bool,
    deadline: float | None = None,
) -> bool:
    """Scrape the queue in order. Returns True if it stopped early on the time budget
    (partial — the stalest localities were done; the next run continues)."""
    reached: list[DatasetRun] = []
    finished: set[int] = set()
    current: DatasetRun | None = None
    budget_hit = False
    try:
        for i, (run, loc) in enumerate(queue):
            if deadline_reached(deadline):
                LOG.warning("BUDGET reached, stopped at locality %d/%d", i, len(queue))
                budget_hit = True
                break
            current = run
            if run.run_id is None:
                run.run_id = 0 if dry_run else db.start_run(
                    conn, run.dataset["id"], cities_total=len(run.localities)
                )
                reached.append(run)
            run.observations += _scrape_locality(
                conn, client, run, loc, chunk_months=chunk_months, dry_run=dry_run
            )
            run.done += 1
            if not dry_run:
                db.update_run_progress(
                    conn, run.run_id, cities_done=run.done, observations=run.observations
                )
        if not dry_run:
            for run in reached:
                current = run
                metrics = db.recompute_metrics(conn, run.dataset["id"], window_years=window_years)
                db.finish_run(
                    conn, run.run_id, status="success",
                    localities=len(run.localities), observations=run.observations,
                )
                finished.add(run.dataset["id"])
                LOG.info(
                    "DATASET done id=%s observations=%d cities=%d/%d metrics=%d",
                    run.dataset["id"], run.observations, run.done, len(run.localities), metrics,
                )
    except Exception as exc:
        if not dry_run:
            # The dataset that raised carries the error; the others name it, so no banner blames
            # a dataset for another one's failure.
            for run in reached:
                if run.dataset["id"] in finished:
                    continue
                error = (str(exc) if run is current
                         else f"stopped by dataset {current.dataset['id']}: {exc}")
                db.finish_run(conn, run.run_id, status="failed", error=error[:2000])
        raise
    return budget_hit


def _fetch_with_auth_retry(
    client: PriceStatsClient,
    dataset: dict[str, Any],
    loc: dict[str, Any],
    category_type_cb: int,
    **kw: Any,
) -> dict[str, Any]:
    try:
        return client.fetch_series(
            dataset, entity_id=loc["entity_id"],
            entity_type=loc["entity_type"], category_type_cb=category_type_cb, **kw
        )
    except AuthExpiredError:
        LOG.warning("session expired mid-run; re-minting cookie")
        client.set_cookies(get_session_cookies(force_login=True))
        return client.fetch_series(
            dataset, entity_id=loc["entity_id"],
            entity_type=loc["entity_type"], category_type_cb=category_type_cb, **kw
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape sreality price stats")
    parser.add_argument("--dataset-id", type=int, default=None)
    parser.add_argument("--cities-file", type=Path, default=_DEFAULT_CITIES)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--window-years", type=int, default=5)
    parser.add_argument("--chunk-months", type=int, default=24)
    parser.add_argument("--rate", type=float, default=2.0, help="requests/sec")
    # Stop fetching this many seconds into the run so finish_run + recompute +
    # choropleth refresh complete inside the workflow's 60-min cap. A dataset
    # bigger than one budget is scraped across runs (stalest cities first).
    parser.add_argument("--max-seconds", type=int, default=2880)
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    today = dt.date.today()
    start_ym = (args.start_year, 1)
    end_ym = (today.year, today.month)
    deadline = time.monotonic() + args.max_seconds if args.max_seconds > 0 else None

    conn = db.connect()
    swept = db.sweep_stale_runs(conn)
    if swept:
        LOG.info("SWEEP marked %d stale 'running' runs as failed", swept)
    cookies = get_session_cookies()
    client = PriceStatsClient(cookies=cookies, limiter=RateLimiter(args.rate))

    city_names = _load_city_names(args.cities_file)
    added = resolve_localities(conn, client, city_names, dry_run=args.dry_run)
    LOG.info("RESOLVE added=%d total_cities=%d", added, len(city_names))
    if args.resolve_only:
        return 0

    if args.dataset_id is not None:
        ds = db.get_dataset(conn, args.dataset_id)
        datasets = [ds] if ds else []
    else:
        datasets = db.load_active_datasets(conn)
    if not datasets:
        LOG.warning("no active datasets")
        return 0

    runs: list[DatasetRun] = []
    for dataset in datasets:
        locs = _dataset_localities(conn, client, dataset, dry_run=args.dry_run)
        if not locs:
            LOG.warning("dataset %s has no localities to fetch", dataset["id"])
            continue
        runs.append(DatasetRun(dataset, locs, *_dataset_window(dataset, start_ym, end_ym)))

    # Counted after the localities resolve, so a newly resolved locality is not mistaken for progress.
    before = 0 if args.dry_run else _never_scraped(conn, datasets)
    if run_queue(
        conn, client, work_queue(runs),
        window_years=args.window_years, chunk_months=args.chunk_months,
        dry_run=args.dry_run, deadline=deadline,
    ):
        LOG.warning("time budget exhausted; the stalest localities left wait for the next run")

    if not args.dry_run:
        db.refresh_choropleth(conn)
        LOG.info("CHOROPLETH refreshed")
        after = _never_scraped(conn, datasets)
        # A run that shrank the never-scraped set chains once more; one that did not never re-triggers,
        # and a complete set leaves refresh to the weekly schedule.
        _emit_chain(0 < after < before, before=before, after=after)
    return 0


def _obec_ids(dataset: dict[str, Any]) -> list[int] | None:
    """The dataset's selected obce; None = the global resolved set."""
    return [int(x) for x in dataset["obec_ids"]] if dataset.get("obec_ids") else None


def _never_scraped(conn: Any, datasets: list[dict[str, Any]]) -> int:
    """Localities the datasets have never scraped (neither data nor a no-data marker), summed."""
    return sum(db.coverage_remaining(conn, ds["id"], _obec_ids(ds)) for ds in datasets)


def _emit_chain(chain: bool, *, before: int, after: int) -> None:
    """Hand the chain decision to the workflow's `Chain next run` step."""
    LOG.info("CHAIN=%s never-scraped localities before=%d after=%d",
             "1" if chain else "0", before, after)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"chain={'1' if chain else '0'}\n")


if __name__ == "__main__":
    raise SystemExit(main())

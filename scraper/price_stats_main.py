"""Ingestion entrypoint for price-stats datasets.

Confirms or adds the sreality key of each town from active ads (`ruian_translation`,
#2362), then walks ONE queue of (dataset, town) stalest first across the active
datasets, fetching each town's {prodej, pronájem} monthly series from estate_prices
under its key until the time budget; recomputes the per-town derived metrics of every
dataset it reached, and refreshes the map choropleth. Rows are keyed by RÚIAN town
code. Pure HTTP fetch + psycopg writes; the session cookie comes from
`scraper.sreality_auth`.

  python -m scraper.price_stats_main                 # all active datasets
  python -m scraper.price_stats_main --dataset-id 3  # one dataset
  python -m scraper.price_stats_main --dry-run       # no writes
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from scraper import price_stats_db as db
from scraper.portal import deadline_reached
from scraper.price_stats_client import AuthExpiredError, PriceStatsClient
from scraper.price_stats_enums import ENTITY_MUNICIPALITY
from scraper.rate_limit import RateLimiter
from scraper.sreality_auth import get_session_cookies

LOG = logging.getLogger(__name__)

CATEGORIES = (1, 2)  # prodej, pronajem — every dataset covers both


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


def confirm_translation(conn: Any) -> None:
    """Confirm or add each town's sreality key from active ads; a stored row stands, so a disagreeing ad is logged."""
    confirmed, added, disputed = db.confirm_translation(conn)
    LOG.info("TRANSLATION confirmed=%d added=%d", confirmed, len(added))
    for row in added:
        LOG.info("TRANSLATION added key=%s obec=%s", row["source_key"], row["ruian_code"])
    for d in disputed:
        LOG.warning("TRANSLATION disagree key=%s stored=%s ad=%s ads=%d", d["source_key"], d["stored"], d["code"],
                    d["ads"])


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
                obec_id=loc["obec_id"],
                category_type_cb=category_type_cb,
                months=months,
                run_id=run.run_id,
            )
    if not dry_run:
        # No prodej AND no pronájem series → mark insufficient-data so
        # future runs skip it (TTL); data → drop any stale marker.
        if loc_obs > 0:
            db.clear_no_data(conn, run.dataset["id"], loc["obec_id"])
        else:
            db.record_no_data(conn, run.dataset["id"], loc["obec_id"])
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
    entity = {"entity_id": int(loc["source_key"]), "entity_type": ENTITY_MUNICIPALITY}
    try:
        return client.fetch_series(dataset, category_type_cb=category_type_cb, **entity, **kw)
    except AuthExpiredError:
        LOG.warning("session expired mid-run; re-minting cookie")
        client.set_cookies(get_session_cookies(force_login=True))
        return client.fetch_series(dataset, category_type_cb=category_type_cb, **entity, **kw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape sreality price stats")
    parser.add_argument("--dataset-id", type=int, default=None)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--window-years", type=int, default=5)
    parser.add_argument("--chunk-months", type=int, default=24)
    parser.add_argument("--rate", type=float, default=2.0, help="requests/sec")
    # Stop fetching this many seconds into the run so finish_run + recompute +
    # choropleth refresh complete inside the workflow's 60-min cap. A dataset
    # bigger than one budget is scraped across runs (stalest cities first).
    parser.add_argument("--max-seconds", type=int, default=2880)
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

    if not args.dry_run:
        confirm_translation(conn)

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
        locs = db.localities_ordered(conn, dataset["id"], _obec_ids(dataset))
        if not locs:
            LOG.warning("dataset %s has no localities to fetch", dataset["id"])
            continue
        runs.append(DatasetRun(dataset, locs, *_dataset_window(dataset, start_ym, end_ym)))

    # Counted after the keys are confirmed, so a newly added town is not mistaken for progress.
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
    """The dataset's selected obce; None = every translated town."""
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

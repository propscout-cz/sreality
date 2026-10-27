"""Every scheduled job's one definition (Actions exit PROGRAM §4.2). A scheduler reads runner_jobs() only."""

from __future__ import annotations

import functools
import importlib
from dataclasses import dataclass
from typing import Literal

Host = Literal["job-runner", "github-actions", "github-actions-until-moved"]
RUNNER: Host = "job-runner"
GITHUB: Host = "github-actions"
# A workflow still schedules the job; the entry's values are the targets its move PR takes over.
UNTIL_MOVED: Host = "github-actions-until-moved"

HOUR, DAY = 3600, 86400
# Children per replica (job_runner.MAX_CHILDREN enforces it); the largest that many declarations fit this share of
# one replica.
RUNNER_MAX_CONCURRENT = 3
# Railway's 8 GB = 8e9 B (.railway/railway.ts RUNNER_MEMORY_BYTES), in MiB.
REPLICA_MEMORY_MB, CO_TENANCY_SHARE = 7629, 0.70
# The runner's SIGKILL grace after its SIGTERM stays under this; the API's stuck-run cutoff exceeds the longest
# walk by it, and the DB-heavy hour census counts it (tests/scraper/test_job_registry.py).
STUCK_RUN_GRACE_S = 600
LOCATION_HEAVY = "location-heavy"
WALK_PREFIX = "walk_"
WALK_MEMORY_MB, WALK_CPUS = 512, 1


@dataclass(frozen=True)
class Job:
    name: str
    argv: tuple[str, ...]  # module first: the runner runs python -m <argv> through an oom_score_adj exec shim (same
    # pid), request args appended
    every_s: int  # upserted as scheduled_jobs.cadence
    offset_s: int  # the slot on the epoch-anchored cadence grid
    timeout_s: int
    memory_mb: int
    cpus: int  # exported to the child as JOB_CPUS
    host: Host
    resource: str | None = None  # jobs sharing one never overlap
    db_heavy: bool = False  # the hour census, and the runner keeps a DB-heavy job's retries in its hour


_FIXED: tuple[Job, ...] = (
    Job("stale_images",
        ("scripts.refresh_stale_image_urls", "--limit", "5000", "--min-age", "3 days", "--cooldown", "14 days"),
        6 * HOUR, 20 * 60, 20 * 60, 256, 1, RUNNER),
    # The long grids sit at, or up to 3 days after, the calendar run they replace (5th 03:00; Jan/Apr/Jul/Oct 2nd).
    Job("rent_map", ("scripts.fetch_rent_map",), 30 * DAY, 356400, 15 * 60, 512, 1, RUNNER),
    Job("map_overlays", ("scripts.ingest_map_overlays",), 91 * DAY, 6300000, 2 * HOUR, 1024, 1, RUNNER,
        db_heavy=True),
    Job("location_full_resolve",
        ("location_data.resolver.drain", "--full-sweep", "--max-seconds", "1800", "--batch-size", "250"),
        DAY, 1 * HOUR, 1 * HOUR, 1024, 1, UNTIL_MOVED, LOCATION_HEAVY, db_heavy=True),
    # 03:00, not 02:00: full_resolve may wait on intake's run for location-heavy, and holds through the kill grace.
    Job("property_sweep", ("scripts.recompute_property_stats", "--batch-size", "2000", "--max-seconds", "6000"),
        DAY, 3 * HOUR, 130 * 60, 1024, 1, UNTIL_MOVED, db_heavy=True),
    Job("broker_sweep", ("scripts.resolve_brokers", "--max-seconds", "1500"),
        DAY, 6 * HOUR, 3 * HOUR, 1024, 1, UNTIL_MOVED, db_heavy=True),
    # Not DB-heavy: hourly, it runs beside the daily sweeps today; its one exclusion is location-heavy.
    Job("location_intake",
        ("location_data.claims_intake", "--mode", "incremental", "--max-seconds", "2400", "--batch-size", "10000"),
        HOUR, 35 * 60, 55 * 60, 1024, 2, UNTIL_MOVED, LOCATION_HEAVY),
    # One unsharded run: tag, then retag on one model load; it re-selects inside its budget; 75 under a backlog (#2032).
    Job("clip", ("scripts.clip_tag_backfill", "--max-seconds", "1500", "--limit", "5000", "--workers", "16"),
        15 * 60, 5 * 60, 55 * 60, 2048, 4, RUNNER),
    Job("ruian_load", ("location_data.ruian_load",), 30 * DAY, 10 * HOUR, 6 * HOUR, 2048, 1, UNTIL_MOVED,
        LOCATION_HEAVY, db_heavy=True),
    Job("price_stats", ("scraper.price_stats_main", "--start-year", "2015", "--window-years", "5"),
        7 * DAY, 4 * DAY + 4 * HOUR, 1 * HOUR, 1024, 1, GITHUB),  # the epoch began on a Thursday: Monday 04:00
    Job("verify_pipeline", ("scripts.verify_pipeline", "--host", "github-actions"),
        HOUR, 15 * 60, 19 * 60, 256, 1, GITHUB),
)


def _walk(source: str, portal: type, offset_s: int) -> Job:
    return Job(f"{WALK_PREFIX}{source}", (f"scraper.{source}_main", "--index-only"), portal.walk_every_s,
               offset_s, portal.walk_timeout_s, WALK_MEMORY_MB, WALK_CPUS, UNTIL_MOVED)


@functools.cache
def _walks() -> tuple[Job, ...]:
    from scraper.portal_factory import PORTAL_CLASSES  # lazy: importing jobs stays stdlib-only

    portals = {s: getattr(importlib.import_module(m), c) for s, (m, c) in PORTAL_CLASSES.items()}
    walks: list[Job] = []
    for every_s in sorted({p.walk_every_s for p in portals.values()}):
        group = sorted(s for s, p in portals.items() if p.walk_every_s == every_s)
        # Evenly spread over the cadence, on whole minutes, so walks of one cadence never start together.
        walks += [_walk(s, portals[s], i * every_s // len(group) // 60 * 60) for i, s in enumerate(group)]
    return tuple(walks)


def declared() -> tuple[Job, ...]:
    """Every entry, for the censuses; a scheduler never reads it."""
    return _FIXED + _walks()


def runner_jobs() -> tuple[Job, ...]:
    """The only entries a scheduler may upsert, schedule, claim or request."""
    return tuple(j for j in declared() if j.host == RUNNER)


def host_of(name: str) -> Host | None:
    """The declared host of a name, for a refusal's message; never an entry: a scheduler reads runner_jobs()."""
    return next((j.host for j in declared() if j.name == name), None)

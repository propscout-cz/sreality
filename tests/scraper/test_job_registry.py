"""The job registry's censuses (Actions exit W2, #2023; PROGRAM §4.2).

Each census is a pure `*_problems(...) -> list[str]`: green on the real registry, red on a deliberate violation.
The helpers live here, not in `scraper/jobs.py`: a test may name portals and workflows, the registry may not.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import inspect
import io
import itertools
import re
import sys
from collections.abc import Sequence
from dataclasses import fields, replace
from pathlib import Path
from unittest import mock

import pytest

from location_data.resolver import drain
from scraper import jobs
from scraper.jobs import Job
from scraper.portal import _LIMIT_COERCERS, PortalConfig, PortalLimits
from scraper.portal_factory import PORTAL_CLASSES

FIXED = 11
ROOT = Path(__file__).resolve().parents[2]
API_MAIN = ROOT / "api" / "main.py"


class _Parsed(BaseException):
    """Raised from inside parse_args, so a broad `except Exception` in a main() cannot swallow it."""

    def __init__(self, namespace: argparse.Namespace) -> None:
        super().__init__()
        self.namespace = namespace


def _parse(job: Job) -> argparse.Namespace | str:
    """The Namespace `python -m <argv>` parses to, or why it does not get that far."""
    try:
        module = importlib.import_module(job.argv[0])
    except ImportError as exc:
        return f"cannot import {job.argv[0]}: {exc}"
    source = inspect.getsource(module)
    main = getattr(module, "main", None)
    if '__name__ == "__main__"' not in source or "parse_args(" not in source or main is None:
        return f"{job.argv[0]} has no argparse main() to run"
    original = argparse.ArgumentParser.parse_args

    def parse_and_stop(self: argparse.ArgumentParser, *args: object, **kwargs: object) -> None:
        self.allow_abbrev = False
        raise _Parsed(original(self, *args, **kwargs))

    stderr = io.StringIO()
    try:
        with mock.patch.object(sys, "argv", list(job.argv)), contextlib.redirect_stderr(stderr), \
                mock.patch.object(argparse.ArgumentParser, "parse_args", parse_and_stop):
            main()
    except _Parsed as parsed:
        return parsed.namespace
    except SystemExit as exc:
        return f"its parser refused the argv (exit {exc.code}): {stderr.getvalue().strip().splitlines()[-1:]}"
    return "main() returned without parsing its argv"


def argv_problems(job: Job) -> list[str]:
    """The argv parses through its module, runs for real, and keeps its own budget inside the runner's timeout."""
    ns = _parse(job)
    if isinstance(ns, str):
        return [f"{job.name}: {ns}"]
    problems = []
    if getattr(ns, "dry_run", False):
        problems.append(f"{job.name}: --dry-run")
    budget = getattr(ns, "max_seconds", None)
    if job.name.startswith(jobs.WALK_PREFIX):
        if not getattr(ns, "index_only", False) or getattr(ns, "drain_only", False):
            problems.append(f"{job.name}: a walk runs --index-only alone")
        if budget is not None:
            problems.append(f"{job.name}: a walk's budget is the runner's SIGTERM at timeout_s, not --max-seconds")
    elif budget is not None and not 0 < budget < job.timeout_s:
        problems.append(f"{job.name}: --max-seconds {budget} must fire inside timeout_s {job.timeout_s}")
    return problems


def _entry(name: str) -> Job:
    return next(j for j in jobs.declared() if j.name == name)


def test_nothing_has_moved_yet() -> None:
    assert len(PORTAL_CLASSES) >= 10, "the portal list shrank: a census over its walks proves less"
    assert len(jobs.declared()) == FIXED + len(PORTAL_CLASSES)
    assert jobs.runner_jobs() == ()  # #2025 deletes this line: its three jobs are the first to move


@pytest.mark.parametrize("job", jobs.declared(), ids=lambda j: j.name)
def test_every_argv_parses_through_its_module(job: Job) -> None:
    assert argv_problems(job) == []


def test_the_argv_census_fires() -> None:
    stale = _entry("stale_images")
    assert argv_problems(replace(stale, argv=(*stale.argv, "--limitt", "5")))
    assert argv_problems(replace(stale, argv=("scripts.no_such_module",)))
    assert argv_problems(replace(stale, argv=("scripts.health_canary",)))
    assert argv_problems(replace(stale, argv=(*stale.argv, "--dry-run")))
    sweep = _entry("property_sweep")
    assert argv_problems(replace(sweep, timeout_s=6000))
    walk = _entry("walk_bazos")
    assert argv_problems(replace(walk, argv=("scraper.bazos_main", "--index")))
    assert argv_problems(replace(walk, argv=("scraper.bazos_main", "--index-only", "--drain-only")))
    assert argv_problems(replace(walk, argv=("scraper.bazos_main", "--index-only", "--max-seconds", "60")))


WALK_ATTRS = ("walk_every_s", "walk_timeout_s")


def _portal_classes() -> dict[str, type]:
    return {s: getattr(importlib.import_module(m), c) for s, (m, c) in PORTAL_CLASSES.items()}


def walk_problems(entries: Sequence[Job], portals: dict[str, type]) -> list[str]:
    """One walk per portal, each through `scraper.<source>_main` on its class's own walk schedule."""
    problems = []
    entrypoints = {f"scraper.{s}_main" for s in portals} | {cls.__module__ for cls in portals.values()}
    walks = {j.name: j for j in entries if j.argv[0] in entrypoints}
    wanted = {f"{jobs.WALK_PREFIX}{s}" for s in portals}
    if set(walks) != wanted:
        problems.append(f"walk entries and the portal list differ by {sorted(set(walks) ^ wanted)}")
    problems += [f"{j.name}: the walk prefix on a job that runs no portal entrypoint" for j in entries
                 if j.name.startswith(jobs.WALK_PREFIX) and j.argv[0] not in entrypoints]
    for source, cls in portals.items():
        schedule = tuple(getattr(cls, a, None) for a in WALK_ATTRS)
        if not all(type(v) is int and v > 0 for v in schedule):
            problems.append(f"{cls.__name__}: {WALK_ATTRS} must be positive ints, not {schedule}")
        walk = walks.get(f"{jobs.WALK_PREFIX}{source}")
        if walk and (walk.argv[0], walk.every_s, walk.timeout_s) != (f"scraper.{source}_main", *schedule):
            problems.append(f"{walk.name}: not scraper.{source}_main on {cls.__name__}'s {WALK_ATTRS}")
    return problems


def test_the_walk_set_is_the_portal_list() -> None:
    assert walk_problems(jobs.declared(), _portal_classes()) == []


def test_the_walk_census_fires() -> None:
    entries, portals = jobs.declared(), _portal_classes()
    assert walk_problems([j for j in entries if j.name != "walk_web"], portals)
    stray = Job("walk_x", ("scraper.main", "--index-only"), 3600, 0, 600, 512, 1, jobs.UNTIL_MOVED)
    assert walk_problems([*entries, stray], portals)
    assert walk_problems([*entries, replace(_entry("clip"), name="walk_clip")], portals)

    class NoTimeout:
        walk_every_s = 3600

    assert walk_problems(entries, {**portals, "web": NoTimeout})


# The runner adopts an existing scheduled_jobs row a registry entry names, so the registry never names a lane's
# row (held() would block its own child), a row migration 675 retired, or a lane name migration 384 reserved.
RESERVED = frozenset({
    drain.JOB_NAME,
    "location_claims_llm", "location_claims_remine", "location_claims_remine_archive", "location_payload_backfill",
    "location_refetch_cohort", "payload_archive_prune", "pin_collision_recompute",
    "location_resolve_sweep", "resolution_campaign_runner", "registry_verification_advance", "llm_claims_lane",
})


def name_problems(entries: Sequence[Job]) -> list[str]:
    """Names are unique, snake_case, and never a row the runner must not adopt."""
    names = [j.name for j in entries]
    problems = [f"{n}: declared {names.count(n)} times" for n in sorted(set(names)) if names.count(n) > 1]
    problems += [f"{n!r}: not snake_case" for n in names if not re.fullmatch(r"[a-z][a-z0-9_]*", n)]
    problems += [f"{n}: a reserved scheduled_jobs row" for n in names if n in RESERVED]
    return problems


def test_every_name_is_unique_snake_case_and_unreserved() -> None:
    assert name_problems(jobs.declared()) == []


def test_the_name_census_fires() -> None:
    entries, stale = jobs.declared(), _entry("stale_images")
    assert name_problems([*entries, stale])
    assert name_problems([*entries, replace(stale, name="location_resolve_incremental")])
    assert name_problems([*entries, replace(stale, name="location_claims_remine")])
    assert name_problems([*entries, replace(stale, name="Bad-Name")])


def limits_key_problems(keysets: dict[str, set[str]]) -> list[str]:
    """No layer an operator or the database can override holds a walk's schedule (AC3)."""
    return [f"{where}: {k} is a walk schedule key" for where, keys in keysets.items() for k in sorted(keys)
            if k in WALK_ATTRS or k.startswith(jobs.WALK_PREFIX)]


def _limits_keysets() -> dict[str, set[str]]:
    from api.routes.admin import UpdatePortalLimitsIn

    return {
        "scraper.portal._LIMIT_COERCERS": set(_LIMIT_COERCERS),
        "PortalLimits": {f.name for f in fields(PortalLimits)},
        "PortalConfig": {f.name for f in fields(PortalConfig)},
        "the admin limits route": set(UpdatePortalLimitsIn.model_fields),
    }


def test_the_walk_schedule_is_no_limits_key() -> None:
    assert limits_key_problems(_limits_keysets()) == []


def test_the_limits_key_census_fires() -> None:
    assert limits_key_problems({"coercers": {"index_rate", "walk_every_s"}})
    assert limits_key_problems({"route": {"walk_budget_s"}})


def co_tenancy_problems(entries: Sequence[Job], cap: int = jobs.RUNNER_MAX_CONCURRENT) -> list[str]:
    """The `cap` largest memory declarations that can run together fit the share of one replica."""
    largest: dict[str, int] = {}
    for j in entries:
        if j.host != jobs.GITHUB:
            key = j.resource or j.name  # siblings on one resource never run together
            largest[key] = max(largest.get(key, 0), j.memory_mb)
    top = sorted(largest.values(), reverse=True)[:cap]
    budget = jobs.CO_TENANCY_SHARE * jobs.REPLICA_MEMORY_MB
    return [] if sum(top) <= budget else [f"the {cap} largest declarations {top} exceed {budget:.0f} MB"]


def test_the_largest_concurrent_declarations_fit_a_replica() -> None:
    assert co_tenancy_problems(jobs.declared()) == []


def test_the_co_tenancy_census_fires() -> None:
    entries = jobs.declared()
    assert co_tenancy_problems([*entries, replace(_entry("clip"), name="clip_twin")])
    assert co_tenancy_problems(entries, cap=4)


def _occupied_hours(job: Job) -> set[int]:
    """The UTC hours of the day a run of `job` can hold, at its declared timeout."""
    if job.every_s % jobs.DAY == 0:
        starts: range | list[int] = [job.offset_s % jobs.DAY]
    elif jobs.DAY % job.every_s == 0:
        starts = range(job.offset_s % job.every_s, jobs.DAY, job.every_s)
    else:
        return set(range(24))
    return {h % 24 for start in starts for h in range(start // jobs.HOUR, -(-(start + job.timeout_s) // jobs.HOUR))}


def db_heavy_problems(entries: Sequence[Job]) -> list[str]:
    """No two DB-heavy jobs can hold the database in one hour, unless one resource already keeps them apart."""
    problems = []
    for a, b in itertools.combinations([j for j in entries if j.db_heavy], 2):
        if a.resource is not None and a.resource == b.resource:
            continue
        if shared := _occupied_hours(a) & _occupied_hours(b):
            problems.append(f"{a.name} and {b.name} are both DB-heavy in UTC hours {sorted(shared)}")
    return problems


def test_no_two_db_heavy_jobs_share_an_hour() -> None:
    assert db_heavy_problems(jobs.declared()) == []


def test_the_db_heavy_census_fires() -> None:
    entries = [j for j in jobs.declared() if j.name not in ("property_sweep", "broker_sweep")]
    today = [replace(_entry("property_sweep"), offset_s=4 * 3600 + 15 * 60),
             replace(_entry("broker_sweep"), offset_s=4 * 3600 + 35 * 60)]
    assert db_heavy_problems([*entries, *today])
    heavy_intake = replace(_entry("location_intake"), db_heavy=True)
    assert db_heavy_problems([*[j for j in jobs.declared() if j.name != "location_intake"], heavy_intake])
    overlapping = replace(_entry("property_sweep"), name="property_twin", resource="elsewhere")
    assert db_heavy_problems([*jobs.declared(), overlapping])


def _module_constant(source: str, name: str) -> object:
    """A module-level `NAME = <literal>`, read from the source so the test needs no API import."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == [name]:
            return ast.literal_eval(node.value)
    return None


def cutoff_problems(cutoff_min: object, walk_timeouts: Sequence[int], api_source: str) -> list[str]:
    """The API's boot sweep never finalizes a running walk: its cutoff outlasts the longest walk and the kill grace.

    The walks are the only scrape_runs writers. Tests disable the boot sweep, so its call is read from the source.
    """
    floor_s = max(walk_timeouts) + jobs.STUCK_RUN_GRACE_S
    problems = []
    if not isinstance(cutoff_min, int) or cutoff_min * 60 <= floor_s:
        problems.append(f"STUCK_RUN_CUTOFF_MINUTES={cutoff_min!r} must exceed {floor_s} s")
    if "older_than_minutes=STUCK_RUN_CUTOFF_MINUTES" not in api_source:
        problems.append("the boot sweep does not pass STUCK_RUN_CUTOFF_MINUTES")
    if re.search(r"older_than_minutes=\s*\d", api_source):
        problems.append("the boot sweep passes an inline cutoff")
    return problems


def _walk_timeouts() -> list[int]:
    return [j.timeout_s for j in jobs.declared() if j.name.startswith(jobs.WALK_PREFIX)]


def test_the_api_stuck_run_cutoff_outlasts_every_walk() -> None:
    source = API_MAIN.read_text()
    assert cutoff_problems(_module_constant(source, "STUCK_RUN_CUTOFF_MINUTES"), _walk_timeouts(), source) == []


def test_the_cutoff_census_fires() -> None:
    call = "sweep_stuck_scrape_runs(conn, older_than_minutes=STUCK_RUN_CUTOFF_MINUTES)"
    assert cutoff_problems(240, [14400], call)
    assert cutoff_problems(300, [14400], "sweep_stuck_scrape_runs(conn, older_than_minutes=300)")
    assert cutoff_problems(None, [14400], call)

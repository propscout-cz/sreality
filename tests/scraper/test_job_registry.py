"""The job registry's censuses (Actions exit W2, #2023; PROGRAM §4.2).

Each census is a pure `*_problems(...) -> list[str]`: green on the real registry, red on a deliberate violation.
The helpers live here, not in `scraper/jobs.py`: a test may name portals and workflows, the registry may not.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import functools
import importlib
import inspect
import io
import itertools
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from unittest import mock

import pytest
import yaml

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
    problems: list[str] = []
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
    problems: list[str] = []
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


def _slots(job: Job) -> list[int] | None:
    """The seconds into the UTC day at which `job` falls due; None when its cadence does not divide a day."""
    if job.every_s % jobs.DAY == 0:
        return [job.offset_s % jobs.DAY]
    if jobs.DAY % job.every_s == 0:
        return list(range(job.offset_s % job.every_s, jobs.DAY, job.every_s))
    return None


def _hold_s(job: Job) -> int:
    return job.timeout_s + jobs.STUCK_RUN_GRACE_S  # the lease is released only after the killed child exits


def _resource_wait_s(slot: int, siblings: Sequence[Job]) -> int:
    """How long a job due at `slot` can wait for its resource: the runner claims the oldest due job first, so at
    most until a sibling's run that fell due at or before the slot lets go (siblings taken at their own slots)."""
    wait = 0
    for sibling in siblings:
        hold, slots = _hold_s(sibling), _slots(sibling)
        if slots is None:
            wait = max(wait, hold)
            continue
        for start in (s - days * jobs.DAY for s in slots for days in range(-(-hold // jobs.DAY) + 1)):
            if start <= slot < start + hold:
                wait = max(wait, start + hold - slot)
    return wait


def _occupied_hours(job: Job, siblings: Sequence[Job] = ()) -> set[int]:
    """The UTC hours of the day a run of `job` can hold: from its slot, through a wait for its resource, to its
    timeout plus the kill grace."""
    slots = _slots(job)
    if slots is None:
        return set(range(24))
    hours: set[int] = set()
    for slot in slots:
        end = slot + _resource_wait_s(slot, siblings) + _hold_s(job)
        hours |= {h % 24 for h in range(slot // jobs.HOUR, -(-end // jobs.HOUR))}
    return hours


def db_heavy_problems(entries: Sequence[Job]) -> list[str]:
    """No two DB-heavy jobs can hold the database in one hour, unless one resource already keeps them apart."""
    heavy = [j for j in entries if j.db_heavy]
    hours = {j.name: _occupied_hours(j, [s for s in entries if j.resource is not None and s.resource == j.resource
                                         and s.name != j.name]) for j in heavy}
    problems: list[str] = []
    for a, b in itertools.combinations(heavy, 2):
        if a.resource is not None and a.resource == b.resource:
            continue
        if shared := hours[a.name] & hours[b.name]:
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
    # property_sweep at 02:00: full_resolve's 01:00 run, killed at its timeout, holds through the kill grace.
    at_two = [replace(j, offset_s=2 * jobs.HOUR) if j.name == "property_sweep" else j for j in jobs.declared()]
    assert db_heavy_problems(at_two)
    # A 40-minute full_resolve is done by 02:00 on its slot, not after waiting on intake's 00:35 run.
    short = [replace(j, timeout_s=40 * 60) if j.name == "location_full_resolve" else j for j in at_two]
    assert db_heavy_problems(short)
    assert db_heavy_problems([j for j in short if j.name != "location_intake"]) == []


def _module_constant(source: str, name: str) -> object:
    """A module-level `NAME = <literal>`, read from the source so the test needs no API import."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and [getattr(t, "id", None) for t in node.targets] == [name]:
            return ast.literal_eval(node.value)
    return None


def cutoff_problems(cutoff_min: object, walk_timeouts: Sequence[int], api_source: str) -> list[str]:
    """The boot sweep outlasts the longest walk (the only scrape_runs writer) plus the kill grace; tests disable
    the sweep, so its call is read from the source."""
    floor_s = max(walk_timeouts) + jobs.STUCK_RUN_GRACE_S
    problems: list[str] = []
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


@dataclass(frozen=True)
class Invocation:
    module: str
    flags: frozenset[str]  # the literal --flags on the command line; a flag built in a shell variable is unseen


@dataclass(frozen=True)
class Workflow:
    file: str
    scheduled: bool  # a timer or another workflow starts it, not only a person
    timeout_s: int | None  # the largest job-level timeout-minutes
    invocations: tuple[Invocation, ...]


_PYTHON_M = re.compile(r"\bpython3?\s+(?:-[A-Za-z]+\s+)*?-m\s+([A-Za-z_][\w.]*)([^;|&]*)")


def _invocations(run: str, root: Path) -> list[Invocation]:
    calls: list[Invocation] = []
    for line in re.sub(r"\$\{\{.*?\}\}", "X", run.replace("\\\n", " ")).splitlines():
        line = re.sub(r"(^|\s)#.*", "", line)
        if line.lstrip().startswith("echo "):
            continue
        for module, rest in _PYTHON_M.findall(line):
            if (root / module.split(".")[0]).is_dir():
                calls.append(Invocation(module, frozenset(re.findall(r"(?<![\w-])(--[a-z][\w-]*)", rest))))
    return calls


def read_workflows(root: Path) -> dict[str, Workflow]:
    """Every workflow file: whether it runs unattended, its timeout, and each `python -m <repo module>` it runs."""
    workflows: dict[str, Workflow] = {}
    for path in sorted((root / ".github" / "workflows").glob("*.yml")):
        data = yaml.safe_load(path.read_text()) or {}
        on = data.get(True, data.get("on"))  # PyYAML reads the bare `on:` key as True
        triggers = on if isinstance(on, dict) else dict.fromkeys([on] if isinstance(on, str) else on or [])
        scheduled = bool(triggers.get("schedule")) or bool({"workflow_run", "repository_dispatch"} & set(triggers))
        job_defs = (data.get("jobs") or {}).values()
        timeouts = [j["timeout-minutes"] * 60 for j in job_defs if isinstance(j.get("timeout-minutes"), int)]
        calls = [c for j in job_defs for step in j.get("steps") or [] if step.get("run")
                 for c in _invocations(step["run"], root)]
        workflows[path.name] = Workflow(path.name, scheduled, max(timeouts, default=None), tuple(calls))
    return workflows


_SYNTHETIC_WORKFLOW = """
name: synthetic
on:
  schedule:
    - cron: "5 * * * *"
  workflow_dispatch:
jobs:
  one:
    timeout-minutes: 7
    steps:
      - run: python -m pip install -e ".[api]"
      - run: |
          # python -m scripts.commented_out --never
          echo "python -m scripts.only_echoed $ARGS"
          python -m scripts.refresh_stale_image_urls \\
            --limit "${{ inputs.limit || '5000' }}" --min-age "3 days"  # --not-a-flag
          eval "exec python3 -u -m location_data.resolver.drain --full-sweep $ARGS" | tee log
  two:
    timeout-minutes: 12
    steps:
      - run: python -m scraper.web_main --index-only
"""


def test_the_workflow_reader(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    for tree in ("scripts", "scraper", "location_data"):
        (tmp_path / tree).mkdir()
    (tmp_path / ".github" / "workflows" / "synthetic.yml").write_text(_SYNTHETIC_WORKFLOW)
    (tmp_path / ".github" / "workflows" / "chained.yml").write_text(
        "on:\n  workflow_run:\n    workflows: [x]\njobs:\n  a:\n    steps:\n      - run: python -m scripts.x\n")
    (tmp_path / ".github" / "workflows" / "by_hand.yml").write_text(
        "on: workflow_dispatch\njobs:\n  a:\n    steps:\n      - run: python -m scripts.y --z\n")
    workflows = read_workflows(tmp_path)
    assert workflows["synthetic.yml"] == Workflow("synthetic.yml", True, 12 * 60, (
        Invocation("scripts.refresh_stale_image_urls", frozenset({"--limit", "--min-age"})),
        Invocation("location_data.resolver.drain", frozenset({"--full-sweep"})),
        Invocation("scraper.web_main", frozenset({"--index-only"})),
    ))
    assert workflows["chained.yml"] == Workflow("chained.yml", True, None, (Invocation("scripts.x", frozenset()),))
    assert workflows["by_hand.yml"] == Workflow("by_hand.yml", False, None,
                                                (Invocation("scripts.y", frozenset({"--z"})),))


# Scheduled files leaving by another PR, tolerated present or gone so the merge order does not matter.
LEAVERS = {
    "compute_image_phash.yml": "#2018 (R6a): pHash moves off the hourly schedule",
    "recompute_city_proximity.yml": "#2012 / PR #2040: city proximity moves onto pg_cron",
}
# A RUNNER entry's module may still run from these attended (dispatch-only) files.
ATTENDED = {
    ("ingest_boundaries.yml", "scripts.ingest_map_overlays"): "re-renders kraje after a human-run boundary ingest",
}
# A step that runs an entry's module for another job of its file, so it never hosts that entry.
COMPANIONS = {
    ("fetch_rent_map.yml", "scripts.ingest_map_overlays"): "#2025: re-renders the rent_map section after an ingest",
}
GITHUB_ENTRIES = frozenset({"verify_pipeline", "price_stats"})
GITHUB_SCHEDULES = frozenset({"verify_pipeline.yml", "scrape_price_stats.yml"})
# The waiting set only shrinks: a new job or portal is born on the runner, never as one more GitHub schedule.
ORIGINAL_UNTIL_MOVED = frozenset({
    "stale_images", "rent_map", "map_overlays", "location_full_resolve", "property_sweep", "broker_sweep",
    "location_intake", "clip", "ruian_load",
    "walk_bazos", "walk_bezrealitky", "walk_ceskereality", "walk_idnes", "walk_maxima", "walk_mmreality",
    "walk_realitymix", "walk_remax", "walk_sreality", "walk_web",
})


@functools.cache
def _repo_workflows() -> dict[str, Workflow]:
    return read_workflows(ROOT)


def _moved(entries: Sequence[Job], names: set[str]) -> list[Job]:
    return [replace(j, host=jobs.RUNNER) if j.name in names else j for j in entries]


def _without(workflows: dict[str, Workflow], *files: str) -> dict[str, Workflow]:
    return {f: w for f, w in workflows.items() if f not in files}


def _switches(argv: tuple[str, ...]) -> set[str]:
    """The entry's value-less flags, the ones that pick a mode (`--full-sweep`, `--index-only`)."""
    return {a for a, after in zip(argv[1:], (*argv[2:], "--")) if a.startswith("--") and "=" not in a
            and after.startswith("--")}


def _invokes(job: Job, workflow: Workflow) -> bool:
    """The file runs this entry's job: its module, carrying the entry's switches unless the command builds every
    flag in a shell variable (location_resolve.yml runs the drain twice; only the --full-sweep call is the job),
    and never as a companion step."""
    return any(c.module == job.argv[0] and (workflow.file, c.module) not in COMPANIONS
               and (not c.flags or _switches(job.argv) <= c.flags) for c in workflow.invocations)


def pairing_problems(entries: Sequence[Job], workflows: dict[str, Workflow], *,
                     leavers: dict[str, str] = LEAVERS, attended: dict[tuple[str, str], str] = ATTENDED,
                     ) -> list[str]:
    """Each job runs on exactly one host: a RUNNER job on no workflow, every waiting job on one, and every
    scheduled workflow runs a registry job."""
    problems: list[str] = []
    for job in entries:
        if job.host == jobs.RUNNER:
            problems += [f"{job.name}: on the runner, and {w.file} still runs {job.argv[0]}"
                         for w in workflows.values() if (w.file, job.argv[0]) not in attended
                         and any(c.module == job.argv[0] for c in w.invocations)]
        elif job.host == jobs.UNTIL_MOVED and not any(w.scheduled and _invokes(job, w) for w in workflows.values()):
            problems.append(f"{job.name}: waiting to move, and no scheduled workflow runs it")
    for w in workflows.values():
        if w.scheduled and w.file not in leavers and not any(_invokes(j, w) for j in entries):
            problems.append(f"{w.file}: scheduled, and runs no registry job")
    return problems


def test_every_job_has_exactly_one_host() -> None:
    assert pairing_problems(jobs.declared(), _repo_workflows()) == []


def test_a_move_is_a_host_flip_plus_its_workflows_deletion() -> None:
    first_wave = {"stale_images", "rent_map", "map_overlays"}
    moved = _moved(jobs.declared(), first_wave)
    deleted = _without(_repo_workflows(), "refresh_stale_images.yml", "fetch_rent_map.yml", "ingest_map_overlays.yml")
    assert pairing_problems(moved, deleted) == []
    assert pairing_problems(moved, deleted, attended={})  # ingest_boundaries.yml still runs the overlays
    assert pairing_problems(moved, _repo_workflows())  # flipped, workflow kept: two hosts
    assert pairing_problems(jobs.declared(), deleted)  # workflow deleted, not flipped: no host


def test_the_pairing_census_fires() -> None:
    workflows = _repo_workflows()
    resolve = workflows["location_resolve.yml"]
    tick_only = replace(resolve, invocations=tuple(c for c in resolve.invocations if "--full-sweep" not in c.flags))
    assert pairing_problems(jobs.declared(), {**workflows, "location_resolve.yml": tick_only})
    stray = Workflow("phash2.yml", True, 600, (Invocation("scripts.compute_image_phash2", frozenset()),))
    assert pairing_problems(jobs.declared(), {**workflows, "phash2.yml": stray})
    # fetch_rent_map.yml still runs the overlay module, but only to re-render rent_map: no host for map_overlays.
    assert pairing_problems(jobs.declared(), _without(workflows, "ingest_map_overlays.yml"))


def github_set_problems(entries: Sequence[Job], workflows: dict[str, Workflow], *,
                        leavers: dict[str, str] = LEAVERS) -> list[str]:
    """GitHub keeps exactly the canary and price stats. The workflow side is what is left of the schedules once the
    waiting jobs' files and the leavers are taken off, so it tightens by itself when the last job moves."""
    problems: list[str] = []
    github = {j.name for j in entries if j.host == jobs.GITHUB}
    if github != GITHUB_ENTRIES:
        problems.append(f"GitHub-hosted entries {sorted(github)}, not {sorted(GITHUB_ENTRIES)}")
    waiting = [j for j in entries if j.host == jobs.UNTIL_MOVED]
    problems += [f"{j.name}: a new job joins the waiting set" for j in waiting if j.name not in ORIGINAL_UNTIL_MOVED]
    left = {w.file for w in workflows.values()
            if w.scheduled and w.file not in leavers and not any(_invokes(j, w) for j in waiting)}
    if left != GITHUB_SCHEDULES:
        problems.append(f"GitHub schedules {sorted(left)} beyond the waiting jobs, not {sorted(GITHUB_SCHEDULES)}")
    return problems


def timeout_floor_problems(entries: Sequence[Job], workflows: dict[str, Workflow]) -> list[str]:
    """A job not on the runner declares at least its workflow's timeout, so a move's first slot (now() + timeout_s)
    comes after any GitHub run still in flight at the cutover."""
    return [f"{j.name}: timeout_s {j.timeout_s} is under {w.file}'s {w.timeout_s}"
            for j in entries if j.host != jobs.RUNNER
            for w in workflows.values() if w.scheduled and _invokes(j, w) and j.timeout_s < (w.timeout_s or 0)]


def test_github_keeps_only_the_canary_and_price_stats() -> None:
    assert github_set_problems(jobs.declared(), _repo_workflows()) == []


def test_the_github_set_tightens_itself_when_the_last_job_moves() -> None:
    entries, workflows = jobs.declared(), _repo_workflows()
    all_moved = _moved(entries, set(ORIGINAL_UNTIL_MOVED))
    github_only = {f: w for f, w in workflows.items() if f in GITHUB_SCHEDULES or not w.scheduled}
    assert github_set_problems(all_moved, github_only) == []
    assert pairing_problems(all_moved, github_only) == []
    assert github_set_problems(all_moved, {**github_only, "bazos_index_walk.yml": workflows["bazos_index_walk.yml"]})


def test_the_github_set_census_fires() -> None:
    entries, workflows = jobs.declared(), _repo_workflows()
    assert github_set_problems([*entries, replace(_entry("price_stats"), name="price_stats_twin")], workflows)
    assert github_set_problems([*entries, replace(_entry("clip"), name="clip_twin")], workflows)
    canary = workflows["verify_pipeline.yml"]
    assert github_set_problems(entries, {**workflows, "canary_twin.yml": replace(canary, file="canary_twin.yml")})


def test_a_waiting_job_outlasts_its_github_run() -> None:
    assert timeout_floor_problems(jobs.declared(), _repo_workflows()) == []


def test_the_timeout_floor_census_fires() -> None:
    entries = [replace(j, timeout_s=3600) if j.name == "walk_idnes" else j for j in jobs.declared()]
    assert timeout_floor_problems(entries, _repo_workflows())


def _production_sources() -> dict[str, str]:
    trees = ("scraper", "api", "scripts", "location_data", "toolkit", "autodedup")
    return {str(path.relative_to(ROOT)): path.read_text() for tree in trees for path in (ROOT / tree).rglob("*.py")}


_READS_DECLARED = re.compile(r"\bjobs\.declared\b|from\s+scraper\.jobs\s+import\s+(?:\([^)]*|[^\n]*)\bdeclared\b")


def accessor_problems(sources: dict[str, str]) -> list[str]:
    """Only the censuses see a waiting entry: production code (the runner included) reads runner_jobs()."""
    return [f"{path}: reads jobs.declared(); a scheduler reads runner_jobs()" for path, text in sources.items()
            if path != "scraper/jobs.py" and _READS_DECLARED.search(text)]


def test_a_scheduler_reads_runner_jobs_only() -> None:
    assert accessor_problems(_production_sources()) == []


def test_the_accessor_census_fires() -> None:
    assert accessor_problems({"scraper/job_runner.py": "for j in jobs.declared():\n    upsert(j)\n"})
    assert accessor_problems({"scraper/job_runner.py": "from scraper.jobs import Job, declared\n"})
    assert accessor_problems({"scraper/job_runner.py": "from scraper.jobs import (\n    Job,\n    declared,\n)\n"})
    assert accessor_problems({"scraper/jobs.py": "def declared():\n    return jobs.declared()\n"}) == []

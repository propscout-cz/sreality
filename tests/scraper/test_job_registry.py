"""The job registry's censuses (Actions exit W2, #2023; PROGRAM §4.2).

Each census is a pure `*_problems(...) -> list[str]`: green on the real registry, red on a deliberate violation.
The helpers live here, not in `scraper/jobs.py`: a test may name portals and workflows, the registry may not.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import io
import sys
from dataclasses import replace
from unittest import mock

import pytest

from scraper import jobs
from scraper.jobs import Job
from scraper.portal_factory import PORTAL_CLASSES

FIXED = 11


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

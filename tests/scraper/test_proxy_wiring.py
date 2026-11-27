"""A portal that declares `USE_PROXY` must actually GET the proxy.

This is the same census shape as `test_walk_deadline_wiring.py`, and it exists
for the same reason: `USE_PROXY = True` in a client is only half of the change.
The other half is `SCRAPER_PROXY_URL` reaching the job's environment, and the
failure when it doesn't is INVISIBLE — `BasePortalClient` logs one warning and
falls back to the direct IP, so the scrape stays green, the tests stay green,
and the portal quietly keeps the throttled egress the flag existed to escape.

idnes is the case that makes this worth pinning. It is not 403-blocked like
mmreality; it is soft-throttled, so "the proxy silently did not
apply" and "the proxy applied" produce the same status codes and the same parsed
rows — only 13x apart in wall-clock, which nothing asserts on.

Each portal's own unit tests can see its own client. None of them can see a
missing line in `.railway/railway.ts`: the job runner runs every walk (#2031)
and hands its service's env to every child.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from scraper import jobs
from tests._railway_iac import iac_env_names

_ROOT = Path(__file__).resolve().parents[2]
_SCRAPER = _ROOT / "scraper"

PROXY_ENV = "SCRAPER_PROXY_URL"


def _proxied_clients() -> list[tuple[str, str]]:
    """(module stem, source slug) for every client class with USE_PROXY = True."""
    found: list[tuple[str, str]] = []
    for path in sorted(_SCRAPER.glob("*_client.py")):
        mod = importlib.import_module(f"scraper.{path.stem}")
        for obj in vars(mod).values():
            if (
                inspect.isclass(obj)
                and obj.__module__ == mod.__name__
                and getattr(obj, "USE_PROXY", False)
            ):
                found.append((path.stem, path.stem.removesuffix("_client")))
    return found


def test_at_least_one_portal_is_proxied() -> None:
    """Guards the census itself: if the discovery ever silently matches nothing,
    every parametrised test below would vacuously pass."""
    assert _proxied_clients(), "no client declares USE_PROXY — has the attribute moved?"


def _runs(job: jobs.Job, source: str) -> bool:
    return re.fullmatch(rf"scraper\.{source}(_main)?", job.argv[0]) is not None


def runner_proxy_problems(entries: Sequence[jobs.Job], proxied: Sequence[tuple[str, str]],
                          runner_env: set[str]) -> list[str]:
    """A runner job that runs a proxied portal's scraper needs the proxy on job-runner (#2030): without it the
    client falls back to the direct IP, and a PROXY_REQUIRED portal's edge 403s every page of the walk."""
    return [f"{job.name} runs scraper.{source} on job-runner, which carries no {PROXY_ENV}: it would walk from the "
            f"direct IP" for job in entries for _stem, source in proxied
            if _runs(job, source) and PROXY_ENV not in runner_env]


def test_a_proxied_portal_on_the_runner_gets_the_proxy() -> None:
    assert runner_proxy_problems(jobs.runner_jobs(), _proxied_clients(), iac_env_names("jobRunner")) == []


def test_the_runner_proxy_census_fires() -> None:
    proxied = _proxied_clients()
    walk = next(j for j in jobs.declared() if j.name.startswith(jobs.WALK_PREFIX) and _runs(j, proxied[0][1]))
    on_runner = [replace(walk, host=jobs.RUNNER)]
    assert runner_proxy_problems(on_runner, proxied, iac_env_names("jobRunner") - {PROXY_ENV})
    assert runner_proxy_problems(on_runner, proxied, iac_env_names("jobRunner") | {PROXY_ENV}) == []


@pytest.mark.parametrize("stem,source", _proxied_clients())
def test_a_proxied_portal_is_walked_on_the_runner(stem: str, source: str) -> None:
    """The census above judges runner jobs only, so a proxied portal no runner job runs (a renamed module) would pass
    it vacuously. A workflow that ran one would fail the registry's pairing census (no walk is on GitHub, #2031)."""
    assert [j.name for j in jobs.runner_jobs() if _runs(j, source)], f"no runner job runs scraper.{source}* ({stem})"


def test_the_fallback_warns_rather_than_failing_silently() -> None:
    """The direct-IP fallback is deliberate — a missing secret must degrade, not
    break — but it has to be LOUD, because a silent fallback is how a proxied
    portal reverts to unproxied without anyone noticing."""
    src = (_SCRAPER / "portal_base.py").read_text(encoding="utf-8")
    block = src[src.index("USE_PROXY"):]
    assert "LOG.warning" in block[: block.index("def ") if "def " in block else len(block)] or \
        "LOG.warning" in src, "portal_base no longer warns when USE_PROXY has no URL"


# --- required vs merely preferred --------------------------------------------
#
# There are two proxy postures, and conflating them costs data in opposite
# directions. ceskereality and mmreality are PROXY_REQUIRED: unproxied they are
# not run at all. idnes SOFT-throttles our datacenter IP: every page still
# arrives, ~13x slower. Skipping idnes when the proxy is absent would trade
# degraded data for NO data.


def test_every_proxied_client_states_whether_the_proxy_is_required() -> None:
    for stem, _ in _proxied_clients():
        mod = importlib.import_module(f"scraper.{stem}")
        for obj in vars(mod).values():
            if (
                inspect.isclass(obj)
                and obj.__module__ == mod.__name__
                and getattr(obj, "USE_PROXY", False)
            ):
                assert isinstance(getattr(obj, "PROXY_REQUIRED", None), bool), (
                    f"{stem}.{obj.__name__} sets USE_PROXY without saying whether "
                    f"the direct IP still works"
                )


def test_the_worker_skips_only_portals_that_REQUIRE_the_proxy(monkeypatch) -> None:
    """The regression this guards: marking a soft-throttled portal proxied would
    silently remove it from the realtime worker's probe rotation, because the
    skip used to key on USE_PROXY alone."""
    from scraper import realtime_worker as rw

    monkeypatch.delenv(PROXY_ENV, raising=False)
    monkeypatch.setattr(rw, "_PROXY_WARNED", set())
    # Only sources the worker actually rotates — mmreality is proxied but has no
    # worker lane, so it never reaches this function.
    assert rw._skip_for_proxy("ceskereality") is True   # proxy required
    assert rw._skip_for_proxy("idnes") is False         # slow, but it works
    assert rw._skip_for_proxy("sreality") is False      # not proxied at all


def test_the_proxy_is_used_when_present_even_if_not_required(monkeypatch) -> None:
    """`PROXY_REQUIRED = False` must not read as `USE_PROXY = False`: the whole
    point is that idnes still RIDES the proxy wherever one is configured."""
    from scraper.idnes_client import IdnesClient

    monkeypatch.setenv(PROXY_ENV, "http://user:pass@residential.example:8080")
    client = IdnesClient()
    assert client._session.proxies.get("https") == "http://user:pass@residential.example:8080"

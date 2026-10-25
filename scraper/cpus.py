"""How many CPUs a job may use: the job runner's `JOB_CPUS`, else the container's cgroup quota, else the host.

`os.cpu_count()` alone is the HOST's count, so a 4-vCPU replica on a 32-core machine would start
32 extraction processes or torch threads and the kernel would throttle every one (#2021).
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path

LOG = logging.getLogger("scraper.cpus")

JOB_CPUS_ENV = "JOB_CPUS"
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_CGROUP = Path("/proc/self/cgroup")
CGROUP_V1_QUOTA = CGROUP_ROOT / "cpu" / "cpu.cfs_quota_us"
CGROUP_V1_PERIOD = CGROUP_ROOT / "cpu" / "cpu.cfs_period_us"


def job_cpus() -> int:
    """`JOB_CPUS` when exported, else the host's usable CPUs capped by the container's quota; never below 1."""
    exported = os.environ.get(JOB_CPUS_ENV, "").strip()
    if exported.isdecimal() and int(exported) >= 1:
        return int(exported)
    if exported:
        _warn_bad_export(exported)
    host = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    quota = _container_quota()
    return max(1, min(host, quota) if quota else host)


@functools.cache
def _warn_bad_export(value: str) -> None:
    LOG.warning("CPUS ignoring %s=%r: not a positive integer", JOB_CPUS_ENV, value)


def _container_quota() -> int | None:
    """Whole CPUs the tightest cgroup on this process's path allows (floored, at least 1); None for no limit."""
    quotas = [q for q in map(_v2_quota, _v2_dirs()) if q is not None]
    if not quotas:
        quotas = [q for q in [_v1_quota()] if q is not None]
    return min(quotas) if quotas else None


def _v2_dirs() -> list[Path]:
    """This process's cgroup v2 directory and every parent up to the mount root."""
    try:
        own = next((line.split("::", 1)[1] for line in PROC_CGROUP.read_text().splitlines()
                    if line.startswith("0::")), "/")
    except OSError:
        own = "/"
    parts = [p for p in own.strip().split("/") if p]
    return [CGROUP_ROOT.joinpath(*parts[:depth]) for depth in range(len(parts), -1, -1)]


def _v2_quota(directory: Path) -> int | None:
    try:
        quota, period = (directory / "cpu.max").read_text().split()[:2]
    except (OSError, ValueError):
        return None
    return _whole_cpus(quota, period)


def _v1_quota() -> int | None:
    try:
        return _whole_cpus(CGROUP_V1_QUOTA.read_text().strip(), CGROUP_V1_PERIOD.read_text().strip())
    except OSError:
        return None


def _whole_cpus(quota: str, period: str) -> int | None:
    if quota in ("max", "-1"):
        return None
    try:
        return max(1, int(quota) // int(period))
    except (ValueError, ZeroDivisionError):
        return None

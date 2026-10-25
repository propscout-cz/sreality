"""How many CPUs a job may use (Actions exit W1, #2021).

The job runner exports each job's declared CPUs as `JOB_CPUS`; outside it, a container's
cgroup quota caps what the host reports. `os.cpu_count()` alone is the HOST's count, so a
4-vCPU Railway replica on a 32-core machine would start 32 extraction processes or torch
threads, and the kernel throttles every one of them.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

LOG = logging.getLogger("scraper.cpus")

JOB_CPUS_ENV = "JOB_CPUS"
CGROUP_V2_MAX = Path("/sys/fs/cgroup/cpu.max")
CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")


def job_cpus() -> int:
    """`JOB_CPUS` when exported, else the host's usable CPUs capped by the container's quota; never below 1."""
    exported = os.environ.get(JOB_CPUS_ENV, "").strip()
    if exported:
        if exported.isdigit() and int(exported) >= 1:
            return int(exported)
        LOG.warning("CPUS ignoring %s=%r: not a positive integer", JOB_CPUS_ENV, exported)
    host = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
    quota = _container_quota()
    return max(1, min(host, quota) if quota else host)


def _container_quota() -> int | None:
    """Whole CPUs the cgroup allows (floored, at least 1), or None when it sets no limit."""
    try:
        quota, period = CGROUP_V2_MAX.read_text().split()[:2]
    except (OSError, ValueError):
        try:
            quota, period = CGROUP_V1_QUOTA.read_text().strip(), CGROUP_V1_PERIOD.read_text().strip()
        except OSError:
            return None
    if quota in ("max", "-1"):
        return None
    try:
        return max(1, int(quota) // int(period))
    except (ValueError, ZeroDivisionError):
        return None

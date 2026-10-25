"""The memory limit a process runs under, read from its cgroup: the worker's autodedup cap and the seed's ceiling."""

from __future__ import annotations

import os

CGROUP_ROOT = "/sys/fs/cgroup"
# cgroup v1 spells "no limit" as its page counter's maximum (~8 EiB), never as "max".
_CGROUP_V1_UNLIMITED_BYTES = 1 << 62


def memory_limit_mb(root: str = CGROUP_ROOT) -> float | None:
    """The container's memory limit in MiB (cgroup v2, else v1); None when unlimited or unread."""
    for name in ("memory.max", os.path.join("memory", "memory.limit_in_bytes")):
        try:
            with open(os.path.join(root, name), encoding="ascii") as handle:
                limit = int(handle.read().strip())
        except FileNotFoundError:
            continue
        except Exception:  # noqa: BLE001 — "max", or unreadable: no limit to report
            return None
        return round(limit / 1_048_576.0, 1) if limit < _CGROUP_V1_UNLIMITED_BYTES else None
    return None

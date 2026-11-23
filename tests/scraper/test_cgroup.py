"""scraper.cgroup: the memory a process may use (the worker's reader is tested in test_realtime_worker_autodedup)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scraper import cgroup


def test_the_ceiling_is_the_cgroup_limit_capped_at_the_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cgroup, "physical_memory_mb", lambda: 16_384.0)
    (tmp_path / "memory.max").write_text("7516192768\n")
    assert cgroup.memory_ceiling_mb(str(tmp_path)) == 7168.0
    (tmp_path / "memory.max").write_text("max\n")  # no limit: the machine's memory
    assert cgroup.memory_ceiling_mb(str(tmp_path)) == 16_384.0
    (tmp_path / "memory.max").write_text("34359738368\n")  # a limit above the machine's memory
    assert cgroup.memory_ceiling_mb(str(tmp_path)) == 16_384.0
    assert cgroup.memory_ceiling_mb(str(tmp_path / "absent")) == 16_384.0  # a GitHub VM's root has no file

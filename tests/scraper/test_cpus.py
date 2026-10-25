"""`scraper.cpus.job_cpus`: the runner's export wins, a container quota caps the host, and the answer is never 0."""

from __future__ import annotations

from pathlib import Path

import pytest

from scraper import cpus


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A 16-CPU host with no cgroup files and nothing exported; tests write the files they need."""
    monkeypatch.delenv(cpus.JOB_CPUS_ENV, raising=False)
    monkeypatch.setattr(cpus.os, "sched_getaffinity", lambda _pid: set(range(16)), raising=False)
    monkeypatch.setattr(cpus, "CGROUP_V2_MAX", tmp_path / "cpu.max")
    monkeypatch.setattr(cpus, "CGROUP_V1_QUOTA", tmp_path / "cpu.cfs_quota_us")
    monkeypatch.setattr(cpus, "CGROUP_V1_PERIOD", tmp_path / "cpu.cfs_period_us")
    return tmp_path


def test_the_runners_export_wins_over_everything(host: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (host / "cpu.max").write_text("200000 100000\n")
    monkeypatch.setenv(cpus.JOB_CPUS_ENV, "3")
    assert cpus.job_cpus() == 3


@pytest.mark.parametrize("bad", ["0", "-2", "two", "1.5"])
def test_a_bad_export_falls_back_to_the_container(host: Path, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    (host / "cpu.max").write_text("400000 100000\n")
    monkeypatch.setenv(cpus.JOB_CPUS_ENV, bad)
    assert cpus.job_cpus() == 4


@pytest.mark.parametrize("cpu_max,expected", [
    ("400000 100000\n", 4),    # a 4-vCPU replica on a 16-CPU host
    ("150000 100000\n", 1),    # a fraction floors, so a process pool never overcommits
    ("50000 100000\n", 1),     # never 0
    ("max 100000\n", 16),      # no limit: the host
    ("6400000 100000\n", 16),  # a quota above the host never raises it
])
def test_a_cgroup_v2_quota_caps_the_host(host: Path, cpu_max: str, expected: int) -> None:
    (host / "cpu.max").write_text(cpu_max)
    assert cpus.job_cpus() == expected


@pytest.mark.parametrize("quota,expected", [("200000", 2), ("-1", 16)])
def test_a_cgroup_v1_quota_caps_the_host(host: Path, quota: str, expected: int) -> None:
    (host / "cpu.cfs_quota_us").write_text(quota + "\n")
    (host / "cpu.cfs_period_us").write_text("100000\n")
    assert cpus.job_cpus() == expected


def test_no_cgroup_at_all_is_the_host(host: Path) -> None:
    assert cpus.job_cpus() == 16

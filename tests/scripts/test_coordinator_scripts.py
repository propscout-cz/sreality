"""The coordinator's scripts over stubbed `gh` and `git`: nothing leaves the box."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.coordinator import _gh, deploy_status, light_check, next_migration
from tests.sql_corpus import discover

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "scripts/coordinator/engine_reading.sql").read_text(encoding="utf-8")
SHA = "b" * 40


class Done:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class Box:
    """`gh api` answers by path, `gh pr view` from its one rollup, `git ls-tree` from a list; each call recorded."""

    def __init__(self, main: list[str] | None = None, api: dict[str, Any] | None = None) -> None:
        self.main, self.api, self.calls = main or [], api or {}, []
        self.rollup: list[dict[str, Any]] = []

    def __call__(self, args: list[str], **_: Any) -> Done:
        self.calls.append(args)
        if args[:2] == ["git", "fetch"]:
            return Done()
        if args[:2] == ["git", "ls-tree"]:
            return Done("\n".join(self.main) + "\n")
        if args[:3] == ["gh", "pr", "view"]:
            return Done(json.dumps({"statusCheckRollup": self.rollup, "mergeStateStatus": "CLEAN"}))
        if args[:2] == ["gh", "api"]:
            path = args[2]
            for key, value in self.api.items():
                if path.split("?")[0] == key:
                    match = re.search(r"[?&]page=(\d+)", path)
                    page = int(match.group(1)) if match else 1
                    if isinstance(value, list):
                        return Done(json.dumps(value[(page - 1) * 100:page * 100]))
                    return Done(json.dumps(value))
            return Done(returncode=1, stderr=f"HTTP 404: {path}")
        raise AssertionError(f"unexpected command {args}")


@pytest.fixture()
def box(monkeypatch: pytest.MonkeyPatch) -> Box:
    stub = Box()
    monkeypatch.setattr(_gh.subprocess, "run", stub)
    return stub


def _open_prs(box: Box, prs: dict[int, list[str]]) -> None:
    box.api[f"repos/{_gh.REPO}/pulls"] = [{"number": number} for number in prs]
    for number, files in prs.items():
        box.api[f"repos/{_gh.REPO}/pulls/{number}/files"] = [{"filename": path} for path in files]


# --- next_migration


def test_next_is_above_main_when_no_pr_is_higher(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    box.main = ["migrations/715_a.sql", "migrations/716_b.sql", "migrations/reverts/900_x.sql", "migrations/README.md"]
    _open_prs(box, {1: ["toolkit/a.py"], 2: ["migrations/700_old.sql"]})
    assert next_migration.main([]) == 0
    assert capsys.readouterr().out == "717\n"


def test_next_is_above_an_open_prs_claim(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    box.main = ["migrations/715_a.sql"]
    _open_prs(box, {1: ["migrations/720_a.sql"], 2: ["migrations/718_b.sql"]})
    assert next_migration.main([]) == 0
    assert capsys.readouterr().out == "721\n"


def test_next_reads_every_page_of_the_open_prs(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    box.main = ["migrations/001_a.sql"]
    _open_prs(box, {n: ["toolkit/a.py"] for n in range(1, 101)} | {101: ["migrations/800_a.sql"]})
    assert next_migration.main([]) == 0
    assert capsys.readouterr().out == "801\n"


def test_next_with_github_down_prints_no_number(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert next_migration.main([]) == 2
    out = capsys.readouterr().out
    assert out.startswith("NO ANSWER") and "HTTP 404" in out


# --- light_check


def _pr(box: Box, number: int, files: list[dict[str, Any]], labels: tuple[str, ...] = ()) -> None:
    box.api[f"repos/{_gh.REPO}/pulls/{number}"] = {
        "title": "t", "head": {"sha": SHA}, "mergeable": True, "draft": False,
        "labels": [{"name": name} for name in labels]}
    box.api[f"repos/{_gh.REPO}/pulls/{number}/files"] = files


def test_light_check_prints_the_whole_reading(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    box.main = ["migrations/716_on_main.sql"]
    _open_prs(box, {9: ["migrations/717_theirs.sql"]})
    _pr(box, 5, [{"filename": "migrations/716_mine.sql"}, {"filename": "migrations/717_mine.sql"},
                 {"filename": "autodedup/x.py"}, {"filename": "toolkit/y.py"},
                 {"filename": "toolkit/moved.py", "previous_filename": "scraper/job_lease.py"}], ("engine-light",))
    box.api[f"repos/{_gh.REPO}/pulls"].append({"number": 5})
    box.rollup = [{"name": "pytest", "conclusion": "SUCCESS", "status": "COMPLETED"},
                  {"name": "vitest", "conclusion": "FAILURE", "status": "COMPLETED"},
                  {"name": "build", "conclusion": "", "status": "IN_PROGRESS"},
                  {"context": "realtime-worker - sreality", "state": "SUCCESS"}]
    assert light_check.main(["5"]) == 0
    out = capsys.readouterr().out
    for line in (f"head SHA:  {SHA}", "mergeable: True (CLEAN)", "draft:     False", "light:     True",
                 "checks:    2 passed, 1 failed, 1 pending", "failed: vitest", "pending: build",
                 "sensitive: 4 file(s)", "  autodedup/x.py", "  scraper/job_lease.py",
                 "migrations: migrations/716_mine.sql, migrations/717_mine.sql", "clashes:   2",
                 "migrations/716_mine.sql clashes with migrations/716_on_main.sql on main",
                 "migrations/717_mine.sql clashes with migrations/717_theirs.sql in #9"):
        assert line in out, line


def test_light_check_with_no_migration_reads_nothing_else(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    _pr(box, 5, [{"filename": "toolkit/y.py"}])
    assert light_check.main(["5"]) == 0
    out = capsys.readouterr().out
    assert "sensitive: 0 file(s)" in out and "migrations: none" in out and "light:     False" in out
    assert ["git", "fetch", "-q", "origin", "main"] not in box.calls


def test_light_check_reads_past_the_first_page_of_files(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    _pr(box, 5, [{"filename": f"toolkit/{i}.py"} for i in range(100)] + [{"filename": "autodedup/late.py"}])
    assert light_check.main(["5"]) == 0
    assert "autodedup/late.py" in capsys.readouterr().out


def test_light_check_on_a_missing_pr_is_no_answer(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert light_check.main(["5"]) == 2
    assert "NO ANSWER" in capsys.readouterr().out


@pytest.mark.parametrize("path,sensitive", [
    ("migrations/9_a.sql", True), ("migrations/reverts/9_a.sql", False), ("autodedup/a.py", True),
    (".railway/railway.ts", True), ("scraper/realtime_worker.py", True), ("scraper/job_lease.py", True),
    ("tests/autodedup/a.py", False), ("scraper/job_runner.py", False),
])
def test_is_sensitive(path: str, sensitive: bool) -> None:
    assert light_check.is_sensitive(path) is sensitive


# --- deploy_status


def _statuses(box: Box, *statuses: dict[str, str]) -> None:
    box.api[f"repos/{_gh.REPO}/commits/{SHA}/status"] = {"statuses": list(statuses)}


DEPLOYED = {"context": "API - sreality", "state": "success", "description": "Success - api.propscout.cz"}
SKIPPED = {"context": "vite - sreality", "state": "success",
           "description": "No deployment needed - watched paths not modified"}
FAILED = {"context": "realtime-worker - sreality", "state": "failure", "description": "Build failed"}
ROLLING = {"context": "realtime-worker - job-runner", "state": "pending", "description": "Deploying"}


def test_all_deployed_is_exit_0_with_no_flag(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    _statuses(box, DEPLOYED)
    assert deploy_status.main([SHA]) == 0
    assert capsys.readouterr().out == "API - sreality | success | Success - api.propscout.cz\n"


def test_a_skip_is_flagged_but_is_no_failure(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    _statuses(box, SKIPPED, DEPLOYED)
    assert deploy_status.main([SHA]) == 0
    assert "vite - sreality | success | No deployment needed - watched paths not modified | SKIPPED, NOT DEPLOYED" \
        in capsys.readouterr().out


@pytest.mark.parametrize("status", [FAILED, ROLLING])
def test_a_non_success_is_flagged_and_exit_1(box: Box, capsys: pytest.CaptureFixture[str],
                                             status: dict[str, str]) -> None:
    _statuses(box, DEPLOYED, status)
    assert deploy_status.main([SHA]) == 1
    assert "NOT SUCCESS" in capsys.readouterr().out


def test_none_posted_is_exit_1(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    _statuses(box)
    assert deploy_status.main([SHA]) == 1
    assert "no status posted" in capsys.readouterr().out


def test_deploy_status_with_github_down_is_exit_2(box: Box, capsys: pytest.CaptureFixture[str]) -> None:
    assert deploy_status.main([SHA]) == 2
    assert "NO ANSWER" in capsys.readouterr().out


# --- every script


@pytest.mark.parametrize("module", ["next_migration", "light_check", "deploy_status"])
def test_help_runs_as_the_readme_says(module: str) -> None:
    done = subprocess.run([sys.executable, "-m", f"scripts.coordinator.{module}", "--help"], cwd=ROOT,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout
    assert f"scripts.coordinator.{module}" in (ROOT / "scripts/coordinator/README.md").read_text(encoding="utf-8")


# --- engine_reading.sql (CI's schema-and-sql lane PREPAREs it: tests/sql_corpus.py)


def test_the_reading_is_in_the_sql_sweep() -> None:
    assert any(item.origin == "scripts/coordinator/engine_reading.sql" for item in discover())


def _statements() -> list[str]:
    no_comments = re.sub(r"--[^\n]*", "", SQL)
    return [part for part in no_comments.split(";") if part.strip()]


def test_the_reading_is_one_read_only_statement() -> None:
    statements = _statements()
    assert len(statements) == 1
    assert statements[0].split()[0].lower() == "with"
    assert not re.search(r"\b(insert|update|delete|truncate|alter|drop|create|grant|for update|set_config|nextval)\b",
                         statements[0], re.I)
    # An aggregate with no GROUP BY in the outer select: one row, even with nothing unread.
    assert statements[0].rstrip().endswith("from unread u")


@pytest.mark.parametrize("column,codes", [
    ("unread_lib", "3501 3504 3505 3608"),
    ("unread_pak", "3603 3606 3609 3611"),
    ("unread_khk", "3602 3604 3605 3607 3610"),
    ("unread_vys", "3304 3601 3707 3710 3714"),
])
def test_each_kraj_counts_exactly_its_okresy(column: str, codes: str) -> None:
    line = next(line for line in SQL.splitlines() if line.rstrip().endswith(f"as {column},"))
    assert sorted(re.findall(r"okres:(\d+)", line)) == sorted(codes.split())


def test_total_unread_is_the_four_kraje() -> None:
    body = SQL.split("coalesce(sum(u.ads) filter", 5)[5]
    total = sorted(re.findall(r"okres:(\d+)", body.split("as total_unread")[0]))
    kraje = sorted(re.findall(r"okres:(\d+)", "".join(
        line for line in SQL.splitlines() if re.search(r"as unread_\w+,$", line.rstrip()))))
    assert total == kraje and len(total) == 18


@pytest.mark.parametrize("column", [
    "read_at", "unread_lib", "unread_pak", "unread_khk", "unread_vys", "total_unread", "total_all_blocks",
    "merges_last_hour", "merged_ads_last_hour", "claim_cap", "schema_mb", "brake_interval_seconds",
    "lease_holder", "lease_expires_at",
])
def test_the_reading_returns_its_columns(column: str) -> None:
    assert re.search(rf"\bas {column}\b", SQL)

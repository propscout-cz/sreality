"""The docs budget's byte caps judged against a base commit, its one runner, and the real pre-push hook over a bare origin."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "docs-budget-check.sh"
SOURCE = SCRIPT.read_text()
CAP = int(re.search(r"^CLAUDE_MD_BYTES=(\d+)$", SOURCE, re.M).group(1))
NEW_SKILL = int(re.search(r"^NEW_SKILL_BYTES=(\d+)$", SOURCE, re.M).group(1))
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITHUB_"))}


def _git(repo: Path, *args: str) -> str:
    cfg = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    return subprocess.run(
        ["git", "-C", str(repo), *cfg, *args], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


def _write(path: Path, size: int, head: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + "x" * (size - len(head) - 1) + "\n")


def _commit(repo: Path) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path, claude_md: int) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _write(repo / "CLAUDE.md", claude_md)
    return repo, _commit(repo)


def _run(repo: Path, base: str | None, script: Path = SCRIPT) -> tuple[int, str, str]:
    args = ["bash", str(script), str(repo), *([base] if base else [])]
    done = subprocess.run(args, capture_output=True, text=True, env=ENV)
    return done.returncode, done.stdout, done.stderr


@pytest.mark.parametrize("merged", [False, True], ids=["base-sha", "merge-parent"])
def test_growth_past_the_cap_fails_with_the_bytes_to_remove(tmp_path: Path, merged: bool) -> None:
    repo, base = _repo(tmp_path, CAP - 10)
    _write(repo / "CLAUDE.md", CAP + 10)
    if merged:
        base = "HEAD^1"
        _commit(repo)
    code, out, _ = _run(repo, base)
    assert code == 1
    assert f"::error file=CLAUDE.md::CLAUDE.md is {CAP + 10}/{CAP} bytes (+20 B in this PR)" in out
    assert "remove at least 10 bytes from CLAUDE.md" in out


@pytest.mark.parametrize("head", [CAP + 50, CAP + 20], ids=["unchanged", "shrunk"])
def test_over_cap_on_the_base_only_warns_a_pr_that_does_not_grow_it(tmp_path: Path, head: int) -> None:
    repo, base = _repo(tmp_path, CAP + 50)
    _write(repo / "CLAUDE.md", head)
    code, out, _ = _run(repo, base)
    assert code == 0
    assert "::warning file=CLAUDE.md::" in out
    assert f"first removes {head - CAP} bytes" in out
    assert "::error" not in out


@pytest.mark.parametrize("head", [CAP * 90 // 100, 1000], ids=["trimmed", "tiny"])
def test_a_trim_under_95_percent_fails_until_the_printed_cap_is_set(tmp_path: Path, head: int) -> None:
    repo, base = _repo(tmp_path, CAP - 10)
    _write(repo / "CLAUDE.md", head)
    code, out, _ = _run(repo, base)
    assert code == 1
    suggested = int(re.search(r"set CLAUDE_MD_BYTES=(\d+) in ", out).group(1))
    assert head < suggested < CAP and suggested % 250 == 0

    locked = tmp_path / "locked.sh"
    locked.write_text(SOURCE.replace(f"\nCLAUDE_MD_BYTES={CAP}\n", f"\nCLAUDE_MD_BYTES={suggested}\n"))
    assert locked.read_text() != SOURCE
    code, out, _ = _run(repo, base, locked)
    assert code == 0, out
    assert f"OK   CLAUDE.md: {head}/{suggested} bytes" in out


def test_inside_the_band_passes(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, CAP - 100)
    _write(repo / "CLAUDE.md", CAP - 50)
    code, out, _ = _run(repo, base)
    assert code == 0
    assert f"OK   CLAUDE.md: {CAP - 50}/{CAP} bytes (+50 B in this PR)" in out
    assert "::" not in out


def test_a_file_the_pr_does_not_trim_only_warns_under_95_percent(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, CAP * 90 // 100)
    code, out, _ = _run(repo, base)
    assert code == 0
    assert "::warning file=CLAUDE.md::" in out and "set CLAUDE_MD_BYTES=" in out


def test_a_missing_base_exits_2_with_usage_before_any_check(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, CAP + 1)
    code, out, err = _run(repo, None)
    assert code == 2
    assert "usage: " in err and "<base-ref>" in err
    assert out == ""


def test_an_unknown_base_exits_2_with_an_error_annotation_before_any_check(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, CAP + 1)
    code, out, _ = _run(repo, "no-such-ref")
    assert code == 2
    assert out.startswith("::error::") and "no-such-ref" in out
    assert "CLAUDE.md" not in out


def test_a_new_skill_gets_the_default_cap(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, CAP - 1000)
    _write(repo / "CLAUDE.md", CAP - 1000, "| x | `.claude/skills/fresh` |\n")
    _write(repo / ".claude" / "skills" / "fresh" / "SKILL.md", NEW_SKILL + 1, "---\ndescription: fresh\n---\n")
    code, out, _ = _run(repo, base)
    assert code == 1
    assert f".claude/skills/fresh/SKILL.md is {NEW_SKILL + 1}/{NEW_SKILL} bytes" in out
    assert "remove at least 1 bytes from .claude/skills/fresh/SKILL.md" in out


RULING = "  # raised 2026-10-27: operator ruling"


@pytest.mark.parametrize(
    ("base_mark", "head_mark", "code"),
    [("", "", 1), ("", RULING, 0), (RULING, RULING, 1)],
    ids=["unmarked", "marked", "stale-mark"],
)
def test_a_cap_raise_needs_a_fresh_operator_ruling_on_its_line(
    tmp_path: Path, base_mark: str, head_mark: str, code: int
) -> None:
    repo, _ = _repo(tmp_path, CAP - 50)
    old = repo / ".github" / "scripts" / "docs-budget-check.sh"
    old.parent.mkdir(parents=True)
    old.write_text(f"CLAUDE_MD_BYTES={CAP - 250}{base_mark}\n")
    base = _commit(repo)
    raised = tmp_path / "raised.sh"
    raised.write_text(SOURCE.replace(f"\nCLAUDE_MD_BYTES={CAP}\n", f"\nCLAUDE_MD_BYTES={CAP}{head_mark}\n"))
    got, out, _ = _run(repo, base, raised)
    assert got == code, out
    assert f"CLAUDE_MD_BYTES rises from {CAP - 250} to {CAP}" in out
    assert ("::error file=.github/scripts/docs-budget-check.sh::" in out) == bool(code)


def test_a_new_skill_cannot_take_a_raised_default(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, CAP - 1000)
    _write(repo / "CLAUDE.md", CAP - 1000, "| x | `.claude/skills/fresh` |\n")
    _write(repo / ".claude" / "skills" / "fresh" / "SKILL.md", 1000, "---\ndescription: fresh\n---\n")
    old = repo / ".github" / "scripts" / "docs-budget-check.sh"
    old.parent.mkdir(parents=True)
    old.write_text(f"NEW_SKILL_BYTES={NEW_SKILL - 1000}\n")
    code, out, _ = _run(repo, _commit(repo))
    assert code == 1
    assert f"NEW_SKILL_BYTES rises from {NEW_SKILL - 1000} to {NEW_SKILL}" in out


SKILL = ".claude/skills/database/SKILL.md"
SKILL_HEAD = "---\ndescription: fixture\n---\n"
SKILL_CAP = int(re.search(r"^SKILL_BYTES\[database\]=(\d+)$", SOURCE, re.M).group(1))
RUNNER = ".github/scripts/docs-check.sh"
TOOLS = [
    RUNNER,
    ".github/scripts/docs-budget-check.sh",
    ".github/scripts/docs-staleness-check.sh",
    "scripts/docs_symbol_check.py",
]


def _stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def pushable(tmp_path: Path) -> Path:
    """A repo on branch work at origin's main, carrying the real docs tools and a stub secret scanner."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for tool in TOOLS:
        (repo / tool).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / tool, repo / tool)
    _stub(repo / "scripts" / "gitleaks.sh", 'echo "$*" >> "$SCAN_LOG"\nexit "${SCAN_RC:-0}"\n')
    _write(repo / "CLAUDE.md", CAP - 100, "| x | `.claude/skills/database` |\n")
    _write(repo / SKILL, SKILL_CAP - 500, SKILL_HEAD)
    _commit(repo)
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True, env=ENV)
    _git(repo, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(repo, "checkout", "-qb", "work")
    return repo


def _push(repo: Path, ref: str = "work", scan_rc: int = 0, log_dir: Path | None = None) -> tuple[int, str]:
    env = {**ENV, "SCAN_LOG": str((log_dir or repo.parent) / "scan.log"), "SCAN_RC": str(scan_rc)}
    done = subprocess.run(
        ["git", "-C", str(repo), "-c", f"core.hooksPath={ROOT / '.githooks'}", "push", "origin", ref],
        capture_output=True, text=True, env=env,
    )
    return done.returncode, done.stdout + done.stderr


def _on_origin(repo: Path) -> str:
    return subprocess.run(
        ["git", f"--git-dir={repo.parent / 'origin.git'}", "for-each-ref", "--format=%(refname)"],
        check=True, capture_output=True, text=True, env=ENV,
    ).stdout


def _scans(repo: Path) -> list[str]:
    log = repo.parent / "scan.log"
    return log.read_text().splitlines() if log.exists() else []


def _skipped(out: str) -> list[str]:
    return [line for line in out.splitlines() if "skipped" in line]


def test_a_commit_past_a_skill_cap_is_refused_and_origin_is_unchanged(pushable: Path) -> None:
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    _commit(pushable)
    before = _on_origin(pushable)
    code, out = _push(pushable)
    assert code != 0
    assert f"::error file={SKILL}::" in out
    assert "nothing was pushed" in out
    assert "git fetch origin main" in out and "amend" in out and "push again" in out
    assert "also skips the secret scan" in out
    assert _on_origin(pushable) == before
    assert _scans(pushable) == []


def test_a_within_cap_commit_is_pushed_with_no_ok_lines_then_scanned(pushable: Path) -> None:
    _write(pushable / SKILL, SKILL_CAP - 100, SKILL_HEAD)
    head = _commit(pushable)
    code, out = _push(pushable)
    assert code == 0, out
    assert "refs/heads/work" in _on_origin(pushable)
    assert "OK " not in out and "Docs budget check passed" not in out and "::" not in out
    assert _skipped(out) == []
    assert _scans(pushable) == [f"git --log-opts=--remerge-diff {head} --not --remotes"]


def test_a_failing_secret_scan_still_refuses_a_within_cap_push(pushable: Path) -> None:
    _write(pushable / SKILL, SKILL_CAP - 100, SKILL_HEAD)
    _commit(pushable)
    code, out = _push(pushable, scan_rc=1)
    assert code != 0
    assert "refs/heads/work" not in _on_origin(pushable)
    assert len(_scans(pushable)) == 1
    assert "nothing was pushed" not in out


def test_an_uncommitted_over_cap_edit_does_not_block_a_within_cap_push(pushable: Path) -> None:
    _write(pushable / "notes.txt", 10)
    _commit(pushable)
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    code, out = _push(pushable)
    assert code == 0, out
    assert "refs/heads/work" in _on_origin(pushable)
    assert len(_skipped(out)) == 1 and "uncommitted" in out
    assert len(_scans(pushable)) == 1


def test_with_an_over_cap_head_pushing_another_branch_succeeds(pushable: Path) -> None:
    _git(pushable, "branch", "other")
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    _commit(pushable)
    code, out = _push(pushable, "other")
    assert code == 0, out
    assert "refs/heads/other" in _on_origin(pushable)
    assert len(_skipped(out)) == 1
    assert len(_scans(pushable)) == 1


def test_a_refspec_sending_the_head_under_another_name_is_still_judged(pushable: Path) -> None:
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    _commit(pushable)
    code, out = _push(pushable, "HEAD:refs/heads/elsewhere")
    assert code != 0
    assert "nothing was pushed" in out


def test_a_push_from_a_linked_worktree_is_judged_too(pushable: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    _git(pushable, "worktree", "add", "-q", "-b", "linked", str(linked))
    _write(linked / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    _commit(linked)
    code, out = _push(linked, "linked", log_dir=tmp_path)
    assert code != 0
    assert f"::error file={SKILL}::" in out and "nothing was pushed" in out


def _no_origin_main(repo: Path) -> None:
    _git(repo, "update-ref", "-d", "refs/remotes/origin/main")


def _no_runner_on_the_branch(repo: Path) -> None:
    _git(repo, "rm", "-q", RUNNER)
    _commit(repo)


def _budget_crashes_with_exit_1(repo: Path) -> None:
    _stub(repo / ".github" / "scripts" / "docs-budget-check.sh", "exit 1\n")
    _commit(repo)


@pytest.mark.parametrize("break_it", [_no_origin_main, _no_runner_on_the_branch, _budget_crashes_with_exit_1])
def test_an_environment_failure_prints_one_skipped_line_and_the_push_proceeds(
    pushable: Path, break_it: Callable[[Path], None]
) -> None:
    break_it(pushable)
    code, out = _push(pushable, "HEAD:refs/heads/work")
    assert code == 0, out
    assert "refs/heads/work" in _on_origin(pushable)
    assert len(_skipped(out)) == 1
    assert "::" not in out
    assert len(_scans(pushable)) == 1


def _runner(repo: Path, *base: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(repo / RUNNER), *base], cwd=repo, capture_output=True, text=True, env={**ENV, **env})


def _as_ci_merge(repo: Path) -> None:
    """Check out what CI checks out: the merge of branch work into main."""
    _git(repo, "checkout", "-q", "--detach", "origin/main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge", "work")


def test_runner_as_ci_a_missing_symbol_warns_and_does_not_block(pushable: Path) -> None:
    (pushable / "docs").mkdir()
    (pushable / "docs" / "architecture.md").write_text("calls `no_such_symbol_anywhere` first\n")
    _commit(pushable)
    _as_ci_merge(pushable)
    done = _runner(pushable, "HEAD^1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "::warning file=docs/architecture.md,line=1::`no_such_symbol_anywhere`" in done.stdout
    assert "OK " not in done.stdout


def test_runner_as_ci_judges_a_merge_against_its_first_parent(pushable: Path) -> None:
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    _commit(pushable)
    _as_ci_merge(pushable)
    done = _runner(pushable, "HEAD^1")
    assert done.returncode == 1
    assert "(+510 B in this PR)" in done.stdout


def test_runner_symbols_read_the_committed_head_not_the_working_tree(pushable: Path) -> None:
    (pushable / "docs").mkdir()
    (pushable / "docs" / "architecture.md").write_text("`committed_symbol` and `uncommitted_symbol`\n")
    (pushable / "toolkit").mkdir()
    (pushable / "toolkit" / "a.py").write_text("committed_symbol = 1\n")
    _commit(pushable)
    (pushable / "toolkit" / "b.py").write_text("uncommitted_symbol = 1\n")
    done = _runner(pushable)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "`uncommitted_symbol`" in done.stdout
    assert "`committed_symbol`" not in done.stdout


def test_runner_the_budget_reads_the_working_tree(pushable: Path) -> None:
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    done = _runner(pushable)
    assert done.returncode == 1
    assert f"::error file={SKILL}::" in done.stdout


def test_runner_the_staleness_nudge_warns_and_reads_the_pr_body(pushable: Path) -> None:
    agent_map = pushable / "frontend" / "src" / "lib" / "agentPlatformMap.ts"
    agent_map.parent.mkdir(parents=True)
    agent_map.write_text("{ n: 77, done: false }\n")
    _git(pushable, "update-ref", "refs/remotes/origin/main", _commit(pushable))
    (pushable / "migrations").mkdir()
    (pushable / "migrations" / "001_x.sql").write_text("select 1;\n")
    _commit(pushable)
    done = _runner(pushable, PR_BODY="Closes #77")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "changes migrations/ but doesn't touch .claude/skills/database/SKILL.md" in done.stdout
    assert "This PR closes #77" in done.stdout


def test_runner_a_refused_budget_still_prints_the_warnings(pushable: Path) -> None:
    (pushable / "docs").mkdir()
    (pushable / "docs" / "architecture.md").write_text("`no_such_symbol_anywhere`\n")
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    _commit(pushable)
    done = _runner(pushable)
    assert done.returncode == 1
    assert f"::error file={SKILL}::" in done.stdout and "`no_such_symbol_anywhere`" in done.stdout


@pytest.mark.parametrize("base", [(), ("no-such-ref",)], ids=["no-origin-main", "unknown-base"])
def test_runner_with_no_computable_base_exits_2_before_any_check(pushable: Path, base: tuple[str, ...]) -> None:
    _write(pushable / SKILL, SKILL_CAP + 10, SKILL_HEAD)
    if not base:
        _no_origin_main(pushable)
    done = _runner(pushable, *base)
    assert done.returncode == 2
    assert done.stdout.startswith("::error::") and "git fetch origin main" in done.stdout
    assert SKILL not in done.stdout


def test_runner_a_budget_crash_is_no_verdict(pushable: Path) -> None:
    _budget_crashes_with_exit_1(pushable)
    done = _runner(pushable)
    assert done.returncode == 3


def test_the_job_keeps_its_required_name_and_is_one_call_to_the_runner() -> None:
    job = yaml.safe_load((ROOT / ".github" / "workflows" / "docs-budget.yml").read_text())["jobs"]["docs-budget"]
    assert [step["run"] for step in job["steps"] if "run" in step] == [f"{RUNNER} HEAD^1"]
    assert job["steps"][0]["with"]["fetch-depth"] == 0

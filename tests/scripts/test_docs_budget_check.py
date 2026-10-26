"""The docs budget's byte caps, judged against a base commit: growth past a cap fails, a trim locks the cap down."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "docs-budget-check.sh"
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


def _run(repo: Path, base: str | None, script: Path = SCRIPT) -> tuple[int, str]:
    args = ["bash", str(script), str(repo), *([base] if base else [])]
    done = subprocess.run(args, capture_output=True, text=True, env=ENV)
    return done.returncode, done.stdout


@pytest.mark.parametrize("merged", [False, True], ids=["base-sha", "merge-parent"])
def test_growth_past_the_cap_fails_with_the_bytes_to_remove(tmp_path: Path, merged: bool) -> None:
    repo, base = _repo(tmp_path, CAP - 10)
    _write(repo / "CLAUDE.md", CAP + 10)
    if merged:
        base = "HEAD^1"
        _commit(repo)
    code, out = _run(repo, base)
    assert code == 1
    assert f"::error file=CLAUDE.md::CLAUDE.md is {CAP + 10}/{CAP} bytes (+20 B in this PR)" in out
    assert "remove at least 10 bytes from CLAUDE.md" in out


@pytest.mark.parametrize("head", [CAP + 50, CAP + 20], ids=["unchanged", "shrunk"])
def test_over_cap_on_the_base_only_warns_a_pr_that_does_not_grow_it(tmp_path: Path, head: int) -> None:
    repo, base = _repo(tmp_path, CAP + 50)
    _write(repo / "CLAUDE.md", head)
    code, out = _run(repo, base)
    assert code == 0
    assert "::warning file=CLAUDE.md::" in out
    assert f"first removes {head - CAP} bytes" in out
    assert "::error" not in out


@pytest.mark.parametrize("head", [CAP * 90 // 100, 1000], ids=["trimmed", "tiny"])
def test_a_trim_under_95_percent_fails_until_the_printed_cap_is_set(tmp_path: Path, head: int) -> None:
    repo, base = _repo(tmp_path, CAP - 10)
    _write(repo / "CLAUDE.md", head)
    code, out = _run(repo, base)
    assert code == 1
    suggested = int(re.search(r"set CLAUDE_MD_BYTES=(\d+) in ", out).group(1))
    assert head < suggested < CAP and suggested % 250 == 0

    locked = tmp_path / "locked.sh"
    locked.write_text(SOURCE.replace(f"\nCLAUDE_MD_BYTES={CAP}\n", f"\nCLAUDE_MD_BYTES={suggested}\n"))
    assert locked.read_text() != SOURCE
    code, out = _run(repo, base, locked)
    assert code == 0, out
    assert f"OK   CLAUDE.md: {head}/{suggested} bytes" in out


def test_inside_the_band_passes(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, CAP - 100)
    _write(repo / "CLAUDE.md", CAP - 50)
    code, out = _run(repo, base)
    assert code == 0
    assert f"OK   CLAUDE.md: {CAP - 50}/{CAP} bytes (+50 B in this PR)" in out
    assert "::" not in out


def test_without_a_base_only_the_absolute_cap_applies(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, CAP + 1)
    code, out = _run(repo, None)
    assert code == 1
    assert "remove at least 1 bytes from CLAUDE.md" in out
    assert "in this PR" not in out

    _write(repo / "CLAUDE.md", CAP * 90 // 100)
    code, out = _run(repo, None)
    assert code == 0
    assert "::warning file=CLAUDE.md::" in out and "set CLAUDE_MD_BYTES=" in out


def test_an_unknown_base_falls_back_to_absolute_caps(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path, CAP - 50)
    code, out = _run(repo, "no-such-ref")
    assert code == 0
    assert "::warning::base ref 'no-such-ref' not found" in out
    assert f"OK   CLAUDE.md: {CAP - 50}/{CAP} bytes\n" in out


def test_a_new_skill_gets_the_default_cap(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, CAP - 1000)
    _write(repo / "CLAUDE.md", CAP - 1000, "| x | `.claude/skills/fresh` |\n")
    _write(repo / ".claude" / "skills" / "fresh" / "SKILL.md", NEW_SKILL + 1, "---\ndescription: fresh\n---\n")
    code, out = _run(repo, base)
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
    got, out = _run(repo, base, raised)
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
    code, out = _run(repo, _commit(repo))
    assert code == 1
    assert f"NEW_SKILL_BYTES rises from {NEW_SKILL - 1000} to {NEW_SKILL}" in out

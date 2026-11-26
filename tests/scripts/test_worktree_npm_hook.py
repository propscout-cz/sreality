"""The post-checkout hook, through a real `git worktree add` in a temporary repo with a fake install in main."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REAL_HOOKS = f"core.hooksPath={Path(__file__).resolve().parents[2] / '.githooks'}"
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GITHUB_"))}
LOCK = {
    "node_modules/left-pad": {"version": "1.0.0", "integrity": "sha512-a"},
    "node_modules/native-linux": {"version": "2.0.0", "integrity": "sha512-b", "optional": True},
}
INSTALLED = {"node_modules/left-pad": LOCK["node_modules/left-pad"]}
TOOL_OUTPUT = "left-pad 1.0.0\n"


def _git(repo: Path, *args: str, env: dict[str, str] = ENV) -> subprocess.CompletedProcess[str]:
    cfg = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    return subprocess.run(
        ["git", "-C", str(repo), *cfg, *args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL
    )


def _track_lockfile(main: Path, folder: str, packages: dict) -> None:
    lockfile = {"name": "app", "lockfileVersion": 3, "packages": {"": {"name": "app"}, **packages}}
    (main / folder).mkdir(parents=True, exist_ok=True)
    (main / folder / "package-lock.json").write_text(json.dumps(lockfile))
    assert _git(main, "add", "-A").returncode == 0
    assert _git(main, "commit", "-qm", f"lockfile {folder}").returncode == 0


def _install(main: Path, folder: str) -> Path:
    """What `npm ci` leaves: packages, the .bin links, npm's hidden lockfile; then the tools' build caches."""
    modules = main / folder / "node_modules"
    (modules / "left-pad").mkdir(parents=True)
    (modules / "left-pad" / "cli.sh").write_text(f"#!/bin/sh\nprintf '{TOOL_OUTPUT}'\n".replace("\n'", "\\n'"))
    (modules / "left-pad" / "cli.sh").chmod(0o755)
    (modules / ".bin").mkdir()
    (modules / ".bin" / "left-pad").symlink_to("../left-pad/cli.sh")
    (modules / ".package-lock.json").write_text(json.dumps({"lockfileVersion": 3, "packages": INSTALLED}))
    (modules / ".vite" / "deps").mkdir(parents=True)
    (modules / ".vite" / "deps" / "chunk.js").write_text("main's cache")
    (modules / ".tmp").mkdir()
    (modules / ".tmp" / "app.tsbuildinfo").write_text("main's build info")
    return modules


def _tool_output(modules: Path) -> str:
    return subprocess.run([str(modules / ".bin" / "left-pad")], capture_output=True, text=True).stdout


@pytest.fixture
def main(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    (repo / ".gitignore").write_text("node_modules/\n")
    return repo


def _add_worktree(
    main: Path, commit: str = "HEAD", env: dict[str, str] = ENV
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    worktree = main.parent / "wt"
    return worktree, _git(main, "-c", REAL_HOOKS, "worktree", "add", "-q", "--detach", str(worktree), commit, env=env)


def _assert_only_the_command(worktree: Path, done: subprocess.CompletedProcess[str], folder: str) -> None:
    assert done.returncode == 0, done.stderr
    assert f"npm ci --prefix {worktree / folder}\n" in done.stderr
    assert [p.name for p in (worktree / folder).iterdir()] == ["package-lock.json"]


def test_a_matching_lockfile_gives_the_worktree_a_usable_install(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    _install(main, "frontend")
    worktree, done = _add_worktree(main)
    assert done.returncode == 0, done.stderr
    assert _tool_output(worktree / "frontend" / "node_modules") == TOOL_OUTPUT
    assert "npm ci" not in done.stderr


def test_a_build_cache_is_not_inherited(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    _install(main, "frontend")
    worktree, _ = _add_worktree(main)
    modules = worktree / "frontend" / "node_modules"
    assert sorted(p.name for p in modules.iterdir()) == [".bin", ".package-lock.json", "left-pad"]


def test_emptying_the_worktree_install_leaves_main_intact(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    modules = _install(main, "frontend")
    worktree, _ = _add_worktree(main)
    # `npm ci` empties node_modules first: through a symlink that would empty main's.
    for child in (worktree / "frontend" / "node_modules").iterdir():
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    assert (modules / ".package-lock.json").is_file()
    assert _tool_output(modules) == TOOL_OUTPUT


@pytest.mark.parametrize(
    "installed",
    [
        '{"packages": {"node_modules/left-pad": {"version": "1.0.1", "integrity": "sha512-a"}}}',
        '{"packages": {"node_modules/left-pad": {"version": "1.0.0", "integrity": "sha512-other"}}}',
        '{"packages": {}}',
        json.dumps({"packages": {**INSTALLED, "node_modules/stray": {"version": "1.0.0", "integrity": "sha512-c"}}}),
        '{"packages": ',
        "[]",
        '{"packages": {"node_modules/left-pad": "1.0.0"}}',
    ],
    ids=["version", "integrity", "missing-package", "extra-package", "truncated", "not-a-lockfile", "bad-entry"],
)
def test_an_install_that_does_not_match_prints_the_command_and_copies_nothing(main: Path, installed: str) -> None:
    _track_lockfile(main, "frontend", LOCK)
    (_install(main, "frontend") / ".package-lock.json").write_text(installed)
    worktree, done = _add_worktree(main)
    _assert_only_the_command(worktree, done, "frontend")


def test_no_install_in_main_prints_the_command(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    worktree, done = _add_worktree(main)
    _assert_only_the_command(worktree, done, "frontend")


def test_an_install_without_its_lockfile_prints_the_command(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    (_install(main, "frontend") / ".package-lock.json").unlink()
    worktree, done = _add_worktree(main)
    _assert_only_the_command(worktree, done, "frontend")


def test_a_commit_with_an_older_lockfile_prints_the_command(main: Path) -> None:
    _track_lockfile(main, "frontend", {"node_modules/left-pad": {"version": "0.9.0", "integrity": "sha512-z"}})
    older = _git(main, "rev-parse", "HEAD").stdout.strip()
    _track_lockfile(main, "frontend", LOCK)
    _install(main, "frontend")
    worktree, done = _add_worktree(main, older)
    _assert_only_the_command(worktree, done, "frontend")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a folder it has no permission on")
def test_a_copy_that_fails_midway_leaves_no_partial_copy(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    unreadable = _install(main, "frontend") / "unreadable"
    unreadable.mkdir()
    unreadable.chmod(0)
    try:
        worktree, done = _add_worktree(main)
    finally:
        unreadable.chmod(0o755)
    _assert_only_the_command(worktree, done, "frontend")


def test_each_package_folder_is_handled_on_its_own(main: Path) -> None:
    for folder in ("frontend", "chrome-extension", ".railway", "tools/no-install"):
        _track_lockfile(main, folder, LOCK)
    _install(main, "frontend")
    (_install(main, "chrome-extension") / ".package-lock.json").write_text("{")
    _install(main, ".railway")
    worktree, done = _add_worktree(main)
    assert done.returncode == 0, done.stderr
    assert _tool_output(worktree / "frontend" / "node_modules") == TOOL_OUTPUT
    assert _tool_output(worktree / ".railway" / "node_modules") == TOOL_OUTPUT
    _assert_only_the_command(worktree, done, "chrome-extension")
    _assert_only_the_command(worktree, done, "tools/no-install")
    assert done.stderr.count("npm ci") == 2


def test_an_existing_destination_is_left_untouched(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    modules = _install(main, "frontend")
    (modules / "kept.txt").write_text("committed")
    assert _git(main, "add", "-f", "frontend/node_modules/kept.txt").returncode == 0
    assert _git(main, "commit", "-qm", "a tracked file in the destination").returncode == 0
    worktree, done = _add_worktree(main)
    assert done.returncode == 0, done.stderr
    existing = worktree / "frontend" / "node_modules"
    assert [p.name for p in existing.iterdir()] == ["kept.txt"]
    assert (existing / "kept.txt").read_text() == "committed"
    assert "npm ci" not in done.stderr


@pytest.mark.parametrize("where", ["worktree", "main"])
def test_an_ordinary_checkout_changes_nothing(main: Path, where: str) -> None:
    _track_lockfile(main, "frontend", LOCK)
    modules = _install(main, "frontend")
    worktree, _ = _add_worktree(main)
    shutil.rmtree(worktree / "frontend" / "node_modules")
    done = _git(worktree if where == "worktree" else main, "-c", REAL_HOOKS, "checkout", "-q", "-b", "other")
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert not (worktree / "frontend" / "node_modules").exists()
    assert sorted(p.name for p in modules.iterdir()) == [".bin", ".package-lock.json", ".tmp", ".vite", "left-pad"]


def test_a_clone_is_not_treated_as_a_worktree(main: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    _install(main, "frontend")
    clone = main.parent / "clone"
    done = _git(main.parent, "-c", REAL_HOOKS, "clone", "-q", str(main), str(clone))
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert [p.name for p in (clone / "frontend").iterdir()] == ["package-lock.json"]


def test_a_hook_that_cannot_run_python_still_exits_zero(main: Path, tmp_path: Path) -> None:
    _track_lockfile(main, "frontend", LOCK)
    _install(main, "frontend")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "python3").write_text("#!/bin/sh\nexit 3\n")
    (stubs / "python3").chmod(0o755)
    worktree, done = _add_worktree(main, env={**ENV, "PATH": f"{stubs}:{ENV['PATH']}"})
    assert done.returncode == 0, done.stderr
    assert "npm ci" in done.stderr
    assert (worktree / "frontend" / "package-lock.json").is_file()

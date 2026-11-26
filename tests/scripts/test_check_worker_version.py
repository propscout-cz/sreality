"""The worker-version check over a throwaway git repo; `gh` answers from fixtures, nothing touches the network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts import check_worker_version as cwv
from tests._railway_iac import iac_constant, iac_text

WATCH = ["/Dockerfile", "/scraper/**"]
REAL_DEPLOY = {"context": "realtime-worker - sreality", "state": "success", "description": "Success"}
SKIP = {"context": "realtime-worker - sreality", "state": "success",
        "description": "No deployment needed - watched paths not modified"}
VITE_SKIP = {"context": "vite - sreality", "state": "success",
             "description": "No deployment needed - watched paths not modified"}


class Repo:
    """A git repo whose `main` is also origin/main, and a `gh` stub keyed by commit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.statuses: dict[str, list[dict[str, Any]]] = {}
        self.git("init", "-q", "-b", "main")
        self.write(".railway/railway.ts", f"const X = 1;\nconst BACKEND_WATCH = {json.dumps(WATCH)};\n")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                               "-c", "core.hooksPath=/dev/null", *args],
                              cwd=self.path, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, name: str, text: str = "x\n") -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, *names: str) -> str:
        for name in names:
            self.write(name, f"{name} {self.git('rev-list', '--all', '--count')}\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "c", "--allow-empty")
        sha = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        return sha

    def merge(self, branch: str) -> str:
        self.git("merge", "-q", "--no-ff", "-m", "m", branch)
        sha = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        return sha

    def run(self, args: list[str]) -> tuple[int, str, str]:
        if args[0] == "gh":
            sha = args[2].split("/commits/")[1].split("/")[0]
            return 0, json.dumps({"statuses": self.statuses.get(sha, [])}), ""
        done = subprocess.run(args, cwd=self.path, capture_output=True, text=True)
        return done.returncode, done.stdout, done.stderr


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def test_the_real_iac_file_parses_to_its_own_value() -> None:
    assert cwv.backend_watch(iac_text()) == iac_constant("BACKEND_WATCH")


def test_no_backend_watch_is_no_verdict() -> None:
    with pytest.raises(cwv.CheckError, match="BACKEND_WATCH"):
        cwv.backend_watch("const OTHER = [];")


def test_a_commit_at_the_newest_image_input_passes(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    sha = repo.commit("scraper/a.py")
    assert cwv.judge(sha, repo.run) == 0
    assert "AT OR AFTER" in capsys.readouterr().out


def test_a_commit_after_it_passes_a_commit_before_it_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    before = repo.commit("scraper/a.py")
    repo.commit("scraper/b.py")
    after = repo.commit("frontend/x.ts")
    assert cwv.judge(after, repo.run) == 0
    assert cwv.judge(before, repo.run) == 1
    assert "BEHIND" in capsys.readouterr().out


def test_a_change_outside_the_image_does_not_make_the_worker_stale(repo: Repo) -> None:
    deployed = repo.commit("scraper/a.py")
    repo.commit("frontend/x.ts", "docs/y.md")
    assert cwv.judge(deployed, repo.run) == 0


def test_a_glob_pattern_and_a_file_pattern_both_count(repo: Repo) -> None:
    deployed = repo.commit("scraper/a.py")
    repo.commit("scraper/deep/er/b.py")
    assert cwv.judge(deployed, repo.run) == 1
    deployed = repo.git("rev-parse", "HEAD")
    repo.commit("Dockerfile")
    assert cwv.judge(deployed, repo.run) == 1


def test_a_branch_commit_older_than_the_deploy_is_judged_by_its_merge(repo: Repo) -> None:
    base = repo.commit("scraper/a.py")
    repo.git("checkout", "-q", "-b", "side")
    repo.commit("scraper/side.py")
    repo.git("checkout", "-q", "main")
    deployed = repo.commit("frontend/x.ts")
    merged = repo.merge("side")
    assert cwv.newest_image_commit(repo.run) == merged
    assert cwv.judge(deployed, repo.run) == 1
    assert cwv.judge(merged, repo.run) == 0
    assert cwv.judge(base, repo.run) == 1


def test_an_unknown_sha_is_no_verdict(repo: Repo) -> None:
    repo.commit("scraper/a.py")
    with pytest.raises(cwv.CheckError):
        cwv.judge("f" * 40, repo.run)


@pytest.mark.parametrize("statuses,kind", [
    ([VITE_SKIP, REAL_DEPLOY], "deployed"),
    ([SKIP], "skipped"),
    ([VITE_SKIP], "absent"),
    ([], "absent"),
    ([{**REAL_DEPLOY, "state": "pending"}], "pending"),
    ([{**REAL_DEPLOY, "state": "failure"}], "failure"),
])
def test_classify(statuses: list[dict[str, Any]], kind: str) -> None:
    assert cwv.classify(statuses) == kind


def test_main_walks_past_a_skip_to_the_real_deploy(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    deployed = repo.commit("scraper/a.py")
    skipped = repo.commit("docs/y.md")
    repo.statuses = {deployed: [REAL_DEPLOY], skipped: [SKIP, VITE_SKIP]}
    assert cwv.judge("main", repo.run) == 0
    out = capsys.readouterr().out
    assert f"worker's deployed commit: {deployed}" in out
    assert f"{skipped[:9]} skipped" in out and "fallback" in out


def test_a_skip_over_a_real_image_change_reads_behind(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    deployed = repo.commit("frontend/x.ts")
    skipped = repo.commit("scraper/a.py")
    repo.statuses = {deployed: [REAL_DEPLOY], skipped: [SKIP]}
    assert cwv.judge("main", repo.run) == 1
    assert "BEHIND" in capsys.readouterr().out


def test_a_failed_deploy_leaves_the_old_one_live(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    deployed = repo.commit("scraper/a.py")
    failed = repo.commit("scraper/b.py")
    repo.statuses = {deployed: [REAL_DEPLOY], failed: [{**REAL_DEPLOY, "state": "failure"}]}
    assert cwv.judge("main", repo.run) == 1
    assert f"{failed[:9]} failure" in capsys.readouterr().out


def test_statuses_with_no_real_deploy_are_no_verdict(repo: Repo) -> None:
    only = repo.commit("scraper/a.py")
    repo.statuses = {only: [SKIP]}
    with pytest.raises(cwv.CheckError, match="no real deploy"):
        cwv.judge("main", repo.run)


def test_main_reports_no_verdict_as_exit_2(monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cwv, "_run", lambda args: (128, "", "could not resolve host"))
    assert cwv.main(["main"]) == 2
    assert "could not resolve host" in capsys.readouterr().out


def test_help_names_the_heartbeat_read(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        cwv.main(["--help"])
    assert stop.value.code == 0
    assert cwv.HEARTBEAT_SQL in capsys.readouterr().out

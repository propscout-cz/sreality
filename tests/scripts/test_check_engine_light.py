"""The engine light's verdict and its workflow, offline: `gh` is a stub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import check_engine_light as cel

ROOT = Path(__file__).resolve().parents[2]
REPO = "o/r"
SHA = "a" * 40


class Stub:
    """A GitHub API: one PR, its files paged at 100, every call recorded."""

    def __init__(self, files: list[dict[str, Any]], labels: list[str], changed: int | None = None) -> None:
        self.files, self.labels, self.changed, self.calls = files, labels, changed, []

    def __call__(self, path: str, method: str = "GET") -> Any:
        self.calls.append((method, path))
        if method == "DELETE":
            self.labels.remove(path.rsplit("/", 1)[1])
            return None
        if "/files" in path:
            page = int(path.rsplit("page=", 1)[1])
            return self.files[(page - 1) * 100:page * 100]
        return {"labels": [{"name": name} for name in self.labels], "head": {"sha": SHA},
                "changed_files": len(self.files) if self.changed is None else self.changed}

    @property
    def deletes(self) -> list[str]:
        return [path for method, path in self.calls if method == "DELETE"]


def _pr_event(action: str = "labeled", number: int = 7) -> dict[str, Any]:
    return {"action": action, "pull_request": {"number": number}}


def _files(*paths: str) -> list[dict[str, Any]]:
    return [{"filename": path} for path in paths]


@pytest.mark.parametrize("path", [
    "migrations/715_x.sql", "migrations/1000_x.sql", "autodedup/decide.py", "autodedup/models/v1.json",
    ".railway/railway.ts", "scraper/realtime_worker.py", "scraper/job_lease.py",
])
def test_sensitive_paths(path: str) -> None:
    assert cel.is_sensitive(path)


@pytest.mark.parametrize("path", [
    "migrations/reverts/715_revert_x.sql", "migrations/README.md", "migrations/x_715.sql", "migrations/715_x.txt",
    "tests/autodedup/test_x.py", ".github/workflows/autodedup.yml", "scraper/realtime_worker_helper.py",
    "scraper/job_runner.py", "docs/autodedup/x.md", "toolkit/migrations/715_x.sql", ".railwayignore",
])
def test_paths_that_are_not_sensitive(path: str) -> None:
    assert not cel.is_sensitive(path)


def test_verdict() -> None:
    assert cel.verdict(["toolkit/a.py"], []) == (True, [])
    assert cel.verdict(["toolkit/a.py"], ["engine-light"]) == (True, [])
    assert cel.verdict(["toolkit/a.py", "autodedup/b.py"], ["bug"]) == (False, ["autodedup/b.py"])
    assert cel.verdict(["migrations/9_a.sql", "autodedup/b.py"], ["engine-light"]) == (
        True, ["autodedup/b.py", "migrations/9_a.sql"])


@pytest.mark.parametrize("head_ref,number", [
    (f"refs/heads/gh-readonly-queue/main/pr-2371-{SHA}", 2371),
    (f"gh-readonly-queue/main/pr-5-{SHA}", 5),
    (f"refs/heads/gh-readonly-queue/release/2026/pr-12-{SHA}", 12),
    ("refs/heads/main", None),
    ("refs/heads/feature/pr-12-abc", None),
    ("refs/heads/gh-readonly-queue/main/pr-x-abc", None),
])
def test_queue_pr_number(head_ref: str, number: int | None) -> None:
    assert cel.queue_pr_number(head_ref) == number


@pytest.mark.parametrize("count,calls", [(0, 1), (99, 1), (100, 2), (250, 3)])
def test_pagination_reads_every_page(count: int, calls: int) -> None:
    stub = Stub(_files(*[f"toolkit/{i}.py" for i in range(count)]), [])
    paths = cel.pr_files(lambda page: stub(f"x/files?page={page}"), count)
    assert len(paths) == count and len(stub.calls) == calls


def test_a_sensitive_file_past_the_first_page_still_fails(capsys: pytest.CaptureFixture[str]) -> None:
    stub = Stub(_files(*[f"toolkit/{i}.py" for i in range(100)], "autodedup/late.py"), [])
    assert cel.judge("pull_request", _pr_event(), REPO, stub) == 1
    assert "autodedup/late.py" in capsys.readouterr().out


def test_a_listing_shorter_than_the_pr_gets_no_verdict() -> None:
    with pytest.raises(cel.ApiError, match="listed 2 of the PR's 3001"):
        cel.judge("pull_request", _pr_event(), REPO, Stub(_files("a", "b"), [], changed=3001))


def test_a_file_renamed_out_of_a_sensitive_tree_counts() -> None:
    stub = Stub([{"filename": "toolkit/x.py", "previous_filename": "autodedup/x.py"}], [])
    assert cel.judge("pull_request", _pr_event(), REPO, stub) == 1


def test_failure_says_what_to_do_in_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    stub = Stub(_files("migrations/9_a.sql", "autodedup/b.py"), [])
    assert cel.judge("pull_request", _pr_event(number=42), REPO, stub) == 1
    out = capsys.readouterr().out.strip()
    assert "\n" not in out and out.startswith("::error::")
    for part in ("merge coordinator", "#42", SHA, "migrations/9_a.sql", "autodedup/b.py"):
        assert part in out


def test_a_lit_or_insensitive_pr_passes() -> None:
    assert cel.judge("pull_request", _pr_event(), REPO, Stub(_files("autodedup/b.py"), ["engine-light"])) == 0
    assert cel.judge("pull_request", _pr_event(), REPO, Stub(_files("toolkit/b.py"), [])) == 0


def test_a_push_spends_the_light_then_judges_without_it() -> None:
    stub = Stub(_files("autodedup/b.py"), ["engine-light", "bug"])
    assert cel.judge("pull_request", _pr_event("synchronize"), REPO, stub) == 1
    assert stub.deletes == [f"repos/{REPO}/issues/7/labels/engine-light"]
    assert stub.labels == ["bug"]


def test_a_push_spends_the_light_on_an_insensitive_pr_too() -> None:
    stub = Stub(_files("toolkit/b.py"), ["engine-light"])
    assert cel.judge("pull_request", _pr_event("synchronize"), REPO, stub) == 0
    assert stub.deletes and stub.labels == []


@pytest.mark.parametrize("action", ["opened", "reopened", "labeled", "unlabeled"])
def test_only_a_push_spends_the_light(action: str) -> None:
    stub = Stub(_files("autodedup/b.py"), ["engine-light"])
    assert cel.judge("pull_request", _pr_event(action), REPO, stub) == 0
    assert stub.deletes == []


def test_a_push_with_no_light_deletes_nothing() -> None:
    stub = Stub(_files("toolkit/b.py"), [])
    assert cel.judge("pull_request", _pr_event("synchronize"), REPO, stub) == 0
    assert stub.deletes == []


def test_merge_group_judges_the_pr_in_its_head_ref() -> None:
    event = {"merge_group": {"head_ref": f"refs/heads/gh-readonly-queue/main/pr-55-{SHA}"}}
    stub = Stub(_files("autodedup/b.py"), [])
    assert cel.judge("merge_group", event, REPO, stub) == 1
    assert stub.calls[0] == ("GET", f"repos/{REPO}/pulls/55")

    lit = Stub(_files("autodedup/b.py"), ["engine-light"])
    assert cel.judge("merge_group", event, REPO, lit) == 0
    assert lit.deletes == []


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, event: dict[str, Any]) -> int:
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    return cel.main()


def test_an_api_error_fails_closed_saying_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    class Done:
        returncode, stdout, stderr = 1, "", "HTTP 502: Bad Gateway"

    monkeypatch.setattr(cel.subprocess, "run", lambda *a, **k: Done())
    event = {"merge_group": {"head_ref": f"refs/heads/gh-readonly-queue/main/pr-55-{SHA}"}}
    assert _run_main(monkeypatch, tmp_path, "merge_group", event) == 1
    out = capsys.readouterr().out
    assert "HTTP 502" in out and "re-run" in out


def test_an_unreadable_head_ref_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    assert _run_main(monkeypatch, tmp_path, "merge_group", {"merge_group": {"head_ref": "refs/heads/x"}}) == 1
    assert "refs/heads/x" in capsys.readouterr().out


def test_a_failed_removal_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    class Done:
        def __init__(self, code: int, stdout: str, stderr: str) -> None:
            self.returncode, self.stdout, self.stderr = code, stdout, stderr

    def run(args: list[str], **_: Any) -> Done:
        if "DELETE" in args:
            return Done(1, "", "HTTP 403: Resource not accessible")
        return Done(0, json.dumps({"labels": [{"name": "engine-light"}], "head": {"sha": SHA}, "changed_files": 0}), "")

    monkeypatch.setattr(cel.subprocess, "run", run)
    assert _run_main(monkeypatch, tmp_path, "pull_request", _pr_event("synchronize")) == 1
    assert "HTTP 403" in capsys.readouterr().out


def test_the_workflow_always_reports() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/engine_light.yml").read_text())
    on = workflow[True]
    assert set(on["pull_request"]["types"]) == {"opened", "synchronize", "reopened", "labeled", "unlabeled"}
    assert "merge_group" in on
    assert not {"paths", "paths-ignore", "branches"} & set(on["pull_request"])
    job = workflow["jobs"]["engine-light"]
    assert "if" not in job and job["permissions"]["pull-requests"] == "write"
    assert all("if" not in step for step in job["steps"])

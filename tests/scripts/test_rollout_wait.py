"""scripts/rollout_wait.sh as a subprocess: a stub gh answers canned JSON per path, a stub sleep records the seconds."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Any

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "rollout_wait.sh"
MERGE = "a" * 40
PARENT = "b" * 40
SERVICES = ("API - sreality", "realtime-worker - sreality", "realtime-worker - job-runner", "vite - sreality")
TWENTY_MINUTES = 20 * 60
SKIPPED = "No deployment needed - watched paths not modified"

STUB_GH = r"""#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys

args = sys.argv[1:]
path = args[1].split("/commits/", 1)[1]
state = pathlib.Path(os.environ["STUB_DIR"])
answers = json.loads((state / "answers.json").read_text()).get(path, ["No commit found for SHA (HTTP 422)"])
seen_file = state / (path.replace("/", "_") + ".seen")
seen = int(seen_file.read_text()) if seen_file.exists() else 0
seen_file.write_text(str(seen + 1))
answer = answers[min(seen, len(answers) - 1)]
if isinstance(answer, str):
    print(json.dumps({"message": answer}))
    print(f"gh: {answer}", file=sys.stderr)
    sys.exit(1)
body = json.dumps(answer)
if "--jq" in args:
    body = subprocess.run(["jq", "-r", args[args.index("--jq") + 1]], input=body, capture_output=True, text=True,
                          check=True).stdout
print(body, end="")
"""


def _status(*rows: tuple[str, ...]) -> dict[str, Any]:
    return {"statuses": [{"context": row[0], "state": row[1], "description": row[2] if len(row) > 2 else "Success"}
                         for row in rows]}


def _green(*services: str) -> dict[str, Any]:
    return _status(*[(service, "success") for service in services])


def _answers(polls: list[Any], parent: dict[str, Any] | None = None) -> dict[str, list[Any]]:
    return {MERGE: [{"sha": MERGE, "parents": [{"sha": PARENT}, {"sha": "c" * 40}]}],
            f"{MERGE}/status": polls,
            f"{PARENT}/status": [parent if parent is not None else _green(*SERVICES)]}


class Run:
    def __init__(self, done: subprocess.CompletedProcess[str], waited: int) -> None:
        self.code, self.out, self.err, self.waited = done.returncode, done.stdout, done.stderr, waited


@pytest.fixture
def run(tmp_path: pathlib.Path) -> Any:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(STUB_GH)
    (bin_dir / "sleep").write_text(f'#!/bin/sh\necho "$1" >> {tmp_path}/slept\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)

    def _run(answers: dict[str, list[Any]], *args: str) -> Run:
        (tmp_path / "answers.json").write_text(json.dumps(answers))
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_DIR": str(tmp_path)}
        done = subprocess.run([str(SCRIPT), *args], env=env, capture_output=True, text=True, cwd=tmp_path, timeout=60)
        slept = tmp_path / "slept"
        waited = sum(int(s) for s in slept.read_text().split()) if slept.exists() else 0
        return Run(done, waited)

    return _run


def test_four_green_services_exit_zero_with_a_line_each_and_a_skip_reads_as_a_skip(run: Any) -> None:
    merge = _status(("API - sreality", "success", "Success - api.propscout.cz"),
                    ("realtime-worker - sreality", "success"), ("realtime-worker - job-runner", "success"),
                    ("vite - sreality", "success", SKIPPED))
    result = run(_answers([merge]), MERGE)
    assert result.code == 0, result.err
    lines = result.out.splitlines()
    for service in SERVICES:
        assert any(service in line and "success" in line for line in lines), result.out
    assert any("vite - sreality" in line and SKIPPED in line for line in lines), result.out
    assert any("API - sreality" in line and "Success - api.propscout.cz" in line for line in lines), result.out


def test_pending_then_green_exits_zero(run: Any) -> None:
    pending = _status(*[(service, "pending", "Deploying") for service in SERVICES])
    result = run(_answers([{"statuses": []}, pending, _green(*SERVICES)]), MERGE)
    assert result.code == 0, result.err
    assert "vite - sreality" in result.out


@pytest.mark.parametrize("state", ["failure", "error"])
def test_a_failure_exits_one_at_once_though_others_are_pending(run: Any, state: str) -> None:
    merge = _status(("API - sreality", state, "Deployment failed"), ("realtime-worker - sreality", "pending"),
                    ("realtime-worker - job-runner", "pending"), ("vite - sreality", "pending"))
    result = run(_answers([merge, _green(*SERVICES)]), MERGE)
    assert result.code == 1
    assert result.waited == 0
    assert any("API - sreality" in line and "Deployment failed" in line for line in result.out.splitlines())


def test_a_service_that_never_reports_times_out_named_within_the_budget(run: Any) -> None:
    result = run(_answers([_green(*SERVICES[:3])]), MERGE)
    assert result.code == 2
    assert 0 < result.waited <= TWENTY_MINUTES
    assert "vite - sreality" in result.err
    assert "API - sreality" not in result.err


def test_a_service_stuck_pending_times_out_named(run: Any) -> None:
    result = run(_answers([_status(*[(s, "success") for s in SERVICES[:3]], ("vite - sreality", "pending"))]), MERGE)
    assert result.code == 2
    assert "vite - sreality" in result.err


def test_no_status_anywhere_is_never_green(run: Any) -> None:
    result = run(_answers([{"statuses": []}], parent={"statuses": []}), MERGE)
    assert result.code == 2
    assert 0 < result.waited <= TWENTY_MINUTES


def test_a_new_service_the_parent_lacks_is_waited_on(run: Any) -> None:
    new = "realtime-worker - new-lane"
    result = run(_answers([_status(*[(s, "success") for s in SERVICES], (new, "pending"))]), MERGE)
    assert result.code == 2
    assert new in result.err


def test_a_new_service_that_goes_green_counts_as_green(run: Any) -> None:
    result = run(_answers([_green(*SERVICES, "realtime-worker - new-lane")]), MERGE)
    assert result.code == 0, result.err
    assert "realtime-worker - new-lane" in result.out


@pytest.mark.parametrize("flaky", [MERGE, f"{PARENT}/status", f"{MERGE}/status"], ids=["commit", "parent", "merge"])
def test_a_poll_error_then_success_exits_zero(run: Any, flaky: str) -> None:
    answers = _answers([_green(*SERVICES)])
    answers[flaky] = ["Bad Gateway (HTTP 502)", answers[flaky][0]]
    result = run(answers, MERGE)
    assert result.code == 0, result.err


def test_poll_errors_for_ever_time_out_within_the_budget(run: Any) -> None:
    result = run(_answers(["Bad Gateway (HTTP 502)"]), MERGE)
    assert result.code == 2
    assert 0 < result.waited <= TWENTY_MINUTES


def test_a_short_commit_reaches_the_statuses_of_the_full_one(run: Any) -> None:
    short = MERGE[:9]
    answers = _answers([_green(*SERVICES)])
    answers[short] = answers.pop(MERGE)
    result = run(answers, short)
    assert result.code == 0, result.err
    assert "vite - sreality" in result.out


@pytest.mark.parametrize("args", [(), (MERGE, "extra"), ("main",), ("--help",), ("",), ("d" * 40,)],
                         ids=["none", "two", "a-branch", "a-flag", "empty", "unresolvable"])
def test_bad_usage_or_an_unresolvable_commit_exits_two_at_once(run: Any, args: tuple[str, ...]) -> None:
    result = run(_answers([_green(*SERVICES)]), *args)
    assert result.code == 2
    assert result.waited == 0
    assert result.err.strip()

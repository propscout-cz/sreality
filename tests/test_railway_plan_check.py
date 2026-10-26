"""The wizard's plan check, .railway/plan_check.mjs (#2025), run under node on pinned-plan fixtures."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / ".railway" / "plan_check.mjs"
PLANS = ROOT / "tests" / "fixtures" / "railway_plans"


def _check(fixture: str, *args: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is missing in CI, so the plan check would go untested")
        pytest.skip("no node")
    return subprocess.run([node, str(CHECK), str(PLANS / fixture), *args], capture_output=True, text=True,
                          timeout=60, check=False)


# (fixture, arguments after the plan, exit code, the REFUSED line it must print)
CASES = [
    # The CLI's own create: the reference variable names service.sreality INSIDE job-runner's change.
    ("create_job_runner.json", ("1", "0", "job-runner"), 0, None),
    ("create_job_runner_with_variable_sets.json", ("1", "0", "job-runner"), 0, None),
    ("create_job_runner_and_update_sreality.json", ("1", "0", "job-runner"), 1,
     "outside job-runner: Update sreality deploy"),
    ("sreality_source_update.json", ("0", "0"), 1, "touches source: Update sreality source"),
    ("no_changes.json", ("1", "0", "job-runner"), 1, "0 to add, expected 1"),
    ("no_changes.json", ("0", "0"), 0, None),
    ("job_runner_domain.json", ("1", "0", "job-runner"), 1, "touches domain.create"),
    # Its text names job-runner (a reference to it); its target is sreality.
    ("sreality_update_naming_job_runner.json", ("0", "0", "job-runner"), 1,
     "outside job-runner: Set variable sreality.RUNNER_PRIVATE_DOMAIN"),
    ("unreadable_target.json", ("0", "0", "job-runner"), 1, "no readable target: change"),
    ("conflicting_targets.json", ("0", "0", "job-runner"), 1, "conflicting targets"),
    # An echo with no structural field falls back to the summary's service token.
    ("echo_without_addresses.json", ("1", "0", "job-runner"), 0, None),
    ("echo_sreality_update_without_addresses.json", ("0", "0", "job-runner"), 1,
     "outside job-runner: Update sreality deploy"),
    ("create_job_runner.json", ("1", "0"), 1, "a create needs its service named"),
    ("create_job_runner.json", ("1", "0", "sreality"), 1, "outside sreality: Create service job-runner"),
    # U-1 for the service add: one destructive delete, expected.
    ("delete_job_runner.json", ("0", "1", "job-runner"), 0, None),
    ("delete_job_runner.json", ("0", "0", "job-runner"), 1, "destructive: Delete service job-runner"),
]


@pytest.mark.parametrize(("fixture", "args", "code", "refused"), CASES,
                         ids=[f"{f[:-5]}:{'_'.join(a)}" for f, a, _, _ in CASES])
def test_the_plan_check(fixture: str, args: tuple[str, ...], code: int, refused: str | None) -> None:
    run = _check(fixture, *args)
    assert run.returncode == code, run.stdout + run.stderr
    refusals = [line.strip() for line in run.stdout.splitlines() if line.strip().startswith("REFUSED: ")]
    if refused is None:
        assert refusals == []
    else:
        assert any(refused in line for line in refusals), refusals


def test_a_bad_call_is_a_usage_error() -> None:
    assert _check("no_changes.json", "one", "0").returncode == 2


def test_the_plan_check_needs_no_npm_install() -> None:
    """CI runs it on the image's own Node, without `npm ci` in .railway/."""
    text = CHECK.read_text(encoding="utf-8")
    assert re.findall(r'^import .* from "([^"]+)";$', text, re.M) == ["node:fs"]
    assert not re.search(r"\brequire\(|\bimport\(", text)


def test_the_fixtures_carry_no_variable_value_but_the_literals_the_file_declares() -> None:
    """The real `--out` list replaces them at #2025 B4, names only: a plan never runs with --show-values."""
    literals = {"api,clip,geo", "1"}
    for path in PLANS.glob("*.json"):
        values = set(re.findall(r'"value": "([^"]*)"', path.read_text(encoding="utf-8")))
        assert values <= literals, (path.name, values - literals)

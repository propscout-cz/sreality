"""The "Jobs: run now" form (.github/workflows/run_job.yml, #2026 AC2): it files a request and runs nothing.

D1 is a census of the file, each rule with a mutated copy that turns it red. D2 runs the file's real `run:` text under
bash with a stub `python` on PATH, so the argv the runner's CLI would receive is the one asserted, quoting included.
The live half (the filed row, then one runner tick) is tests/scraper/test_job_runner_live.py.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.scraper.test_job_registry import read_workflows

ROOT = Path(__file__).resolve().parents[2]
FORM = ROOT / ".github" / "workflows" / "run_job.yml"
CALL = 'python -m scraper.job_runner request "$JOB" --by "github:$GITHUB_TRIGGERING_ACTOR" --'
GUARD = '[[ "$JOB" =~ ^[a-z0-9_]+$ ]]'


def _text() -> str:
    return FORM.read_text(encoding="utf-8")


def _python_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for j in (data.get("jobs") or {}).values() for s in j.get("steps") or []
            if re.search(r"\bpython3?\b", s.get("run") or "")]


def form_problems(text: str) -> list[str]:
    """What makes the form more than a request filer, or a filer that can file silently or wrongly."""
    data = yaml.safe_load(text) or {}
    problems: list[str] = []
    on = data.get(True, data.get("on"))  # PyYAML reads the bare `on:` key as True
    if not isinstance(on, dict) or set(on) != {"workflow_dispatch"}:
        problems.append(f"its only trigger is workflow_dispatch, not {on!r}")
    dispatch = on.get("workflow_dispatch") if isinstance(on, dict) else None
    inputs = (dispatch or {}).get("inputs") or {}
    job, args = inputs.get("job") or {}, inputs.get("args") or {}
    if set(inputs) != {"job", "args"} or job.get("required") is not True or job.get("type") != "string":
        problems.append("inputs are a required string `job` and a string `args`")
    if args.get("type") != "string" or args.get("default") != "" or args.get("required"):
        problems.append("`args` is an optional string defaulting to empty")
    if data.get("permissions") != {"contents": "read"}:
        problems.append(f"permissions {data.get('permissions')!r}, not contents: read")
    if "concurrency" in data or any("concurrency" in j for j in (data.get("jobs") or {}).values()):
        problems.append("a concurrency group would cancel an older click; the database refuses a second request")
    minutes = [j.get("timeout-minutes") for j in (data.get("jobs") or {}).values()]
    if not minutes or not all(isinstance(m, int) and m <= 10 for m in minutes):
        problems.append(f"timeout-minutes {minutes}, not at most 10")
    secrets = sorted(set(re.findall(r"secrets\.(\w+)", text)))
    if secrets != ["SUPABASE_DB_URL"]:
        problems.append(f"secrets {secrets}, not SUPABASE_DB_URL alone")
    steps = [s for j in (data.get("jobs") or {}).values() for s in j.get("steps") or []]
    if any(re.search(r"\$\{\{\s*(?:github\.event\.)?inputs\.", s.get("run") or "") for s in steps):
        problems.append("an input is expanded inside run: (shell injection); inputs reach the shell through env: only")
    python = _python_steps(data)
    if len(python) != 1:
        return [*problems, f"{len(python)} steps run python, not one"]
    step, run = python[0], python[0]["run"]
    calls = re.findall(r"\bpython3?\b[^|;&\n]*", run)
    if len(calls) != 1 or not calls[0].startswith(CALL):
        problems.append(f"its one repo call is `{CALL}`, not {calls}")
    if re.search(r"job_runner\s+once\b", run):
        problems.append("the once verb runs the job on GitHub")
    env = step.get("env") or {}
    if env.get("JOB") != "${{ inputs.job }}" or env.get("ARGS") != "${{ inputs.args }}":
        problems.append("JOB and ARGS come from the inputs through env:")
    if step.get("shell") != "bash":
        problems.append("the step names shell: bash")
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    if not lines or lines[0] != "set -euo pipefail":
        problems.append("the step opens with set -euo pipefail")
    elif len(lines) < 2 or not lines[1].startswith(GUARD):
        problems.append("the step's first command after set is the job-name guard")
    return problems


def test_the_form_only_files_a_request() -> None:
    assert form_problems(_text()) == []


@pytest.mark.parametrize(("old", "new"), [
    ("on:\n  workflow_dispatch:\n", 'on:\n  schedule:\n    - cron: "0 * * * *"\n  workflow_dispatch:\n'),
    ("        required: true\n", "        required: false\n"),
    ('        default: ""\n', '        default: "--dry-run"\n'),
    ("permissions:\n  contents: read\n", "permissions:\n  contents: write\n"),
    ("permissions:\n", "concurrency: run-job\n\npermissions:\n"),
    ("    timeout-minutes: 10\n", "    timeout-minutes: 30\n"),
    ("          ARGS: ${{ inputs.args }}\n",
     "          ARGS: ${{ inputs.args }}\n          R2_BUCKET_NAME: ${{ secrets.R2_BUCKET_NAME }}\n"),
    ('[[ "$JOB" =~', '[[ "${{ inputs.job }}" =~'),
    ("      - name: Install\n        run: pip install -e .\n",
     "      - name: Install\n        run: pip install -e . && python -m scripts.verify_pipeline --host github-actions\n"),
    ('request "$JOB"', 'once "$JOB"'),
    (' --by "github:$GITHUB_TRIGGERING_ACTOR"', ""),
    ("          JOB: ${{ inputs.job }}\n", "          JOB: ${{ inputs.args }}\n"),
    ("        shell: bash\n", ""),
    ("set -euo pipefail", "set -eu"),
    ('          [[ "$JOB" =~ ^[a-z0-9_]+$ ]] || { echo "not a job name: $JOB" | tee -a "$GITHUB_STEP_SUMMARY"; exit 2; }\n',
     ""),
])
def test_the_form_census_fires(old: str, new: str) -> None:
    text = _text()
    assert text.count(old) == 1, old
    assert form_problems(text.replace(old, new))


def test_the_form_is_no_schedule_and_hosts_no_job() -> None:
    """read_workflows (the registry's pairing census) reads it as unscheduled, and its one call is no job's module."""
    form = read_workflows(ROOT)["run_job.yml"]
    assert form.scheduled is False
    assert [c.module for c in form.invocations] == ["scraper.job_runner"]


# --- D2: the real run: text under bash ------------------------------------------------------------------------------

_STUB = """#!/bin/sh
echo called >> "$STUB_LOG"
"$STUB_PY" -c 'import json, sys; print("argv " + json.dumps(sys.argv[1:]))' "$@"
exit "${STUB_EXIT:-0}"
"""


@dataclass(frozen=True)
class FormRun:
    code: int
    argv: list[str] | None  # what `python` received, from -m on; None when it was never called
    summary: str
    called: int


def form_run(job: str, args: str, actor: str, tmp: Path, *, stub_exit: int = 0) -> FormRun:
    """The form's step as GitHub runs it, with a stub `python` that prints its argv as JSON."""
    run = _python_steps(yaml.safe_load(_text()))[0]["run"]
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "bin").mkdir(exist_ok=True)
    stub = tmp / "bin" / "python"
    stub.write_text(_STUB)
    stub.chmod(0o755)
    (tmp / "step.sh").write_text(run)
    summary, log = tmp / "summary.md", tmp / "stub.log"
    summary.write_text("")
    log.write_text("")
    env = {"PATH": f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", "JOB": job, "ARGS": args,
           "GITHUB_TRIGGERING_ACTOR": actor, "GITHUB_STEP_SUMMARY": str(summary), "STUB_LOG": str(log),
           "STUB_PY": sys.executable, "STUB_EXIT": str(stub_exit)}
    done = subprocess.run(["bash", "--noprofile", "--norc", "-e", str(tmp / "step.sh")], cwd=tmp, env=env,
                          capture_output=True, text=True, timeout=60)
    said = summary.read_text()
    lines = [line for line in said.splitlines() if line.startswith("argv ")]
    return FormRun(done.returncode, json.loads(lines[-1][5:]) if lines else None, said,
                   log.read_text().count("called"))


def form_argv(job: str, args: str, actor: str, tmp: Path) -> list[str]:
    """The argv `python` receives for this click: ["-m", "scraper.job_runner", "request", ...]."""
    done = form_run(job, args, actor, tmp)
    assert done.code == 0 and done.argv is not None, done
    return done.argv


@pytest.mark.parametrize(("args", "after"), [
    ("", []),
    ("--dry-run", ["--dry-run"]),
    ("--sections rent_map --accept-shrink", ["--sections", "rent_map", "--accept-shrink"]),
    ('--min-age "3 days"', ["--min-age", "3 days"]),
    ("$(touch pwned)", ["$(touch", "pwned)"]),
])
def test_the_args_reach_the_cli_as_a_shell_quotes_them_and_nothing_is_evaluated(
        args: str, after: list[str], tmp_path: Path) -> None:
    argv = form_argv("map_overlays", args, "op", tmp_path)
    assert argv == ["-m", "scraper.job_runner", "request", "map_overlays", "--by", "github:op", "--", *after]
    assert not (tmp_path / "pwned").exists()


def test_a_refusal_turns_the_run_red_through_tee_and_lands_on_the_summary(tmp_path: Path) -> None:
    done = form_run("map_overlays", "", "op", tmp_path, stub_exit=1)
    assert done.code != 0 and done.called == 1
    assert done.summary.startswith("argv ")


@pytest.mark.parametrize("job", ["--help", "-h", "x;y", "", "Map_Overlays"])
def test_a_job_box_that_is_not_a_name_is_refused_before_python_runs(job: str, tmp_path: Path) -> None:
    done = form_run(job, "", "op", tmp_path)
    assert (done.code, done.called) == (2, 0)
    assert done.summary == f"not a job name: {job}\n"

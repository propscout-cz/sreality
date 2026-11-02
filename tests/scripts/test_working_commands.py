"""Two helpers tell a session what to run next: test-summary's last line and the filter-registry generator's commands."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "scripts" / "test-summary.sh"
GENERATOR = ROOT / "scripts" / "generate_filter_registry.py"
GENERATED = [ROOT / "frontend" / "src" / "lib" / f"{n}Registry.generated.ts" for n in ("filter", "stats")]
MODULE_FORM = "python3 -m scripts.generate_filter_registry"
PATH_FORM = "scripts/generate_filter_registry.py"
ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "PYTEST"))},
    "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
}


def _summary(tmp_path: Path, *flags: str, body: str = "def test_ok():\n    pass\n") -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_tiny.py").write_text(body)
    return subprocess.run(
        ["bash", str(SUMMARY), *flags, "test_tiny.py"], cwd=tmp_path, capture_output=True, text=True, env=ENV
    )


@pytest.mark.parametrize("flags", [(), ("-q",), ("-qq",), ("-qqq",), ("-v",), ("--verbosity=-2",), ("-qq", "--")])
def test_summary_ends_with_the_pass_count_whatever_the_callers_verbosity(tmp_path, flags):
    run = _summary(tmp_path, *flags)
    assert run.returncode == 0, run.stdout
    assert re.match(r"1 passed\b", run.stdout.splitlines()[-1]), run.stdout


def test_summary_of_a_failure_under_a_quiet_flag_names_the_test_and_ends_with_the_count(tmp_path):
    run = _summary(tmp_path, "-qq", body="def test_ok():\n    pass\n\ndef test_bad():\n    assert 0\n")
    assert run.returncode == 1
    assert "FAILED test_tiny.py::test_bad" in run.stdout
    assert re.match(r"1 failed, 1 passed\b", run.stdout.splitlines()[-1]), run.stdout


def _by_path(cwd: Path, *python_flags: str, pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**ENV, "PYTHONPATH": str(pythonpath)} if pythonpath else ENV
    return subprocess.run(
        [sys.executable, *python_flags, str(GENERATOR), "--check"], cwd=cwd, capture_output=True, text=True, env=env
    )


def test_generator_from_outside_the_repo_names_the_module_form(tmp_path):
    # -S: no site-packages, so no editable install can hand this run a toolkit.
    run = _by_path(tmp_path, "-S")
    assert run.returncode == 2, run.stderr
    assert MODULE_FORM in run.stderr
    assert "Traceback" not in run.stderr
    assert PATH_FORM not in run.stderr


def test_generator_refuses_another_trees_toolkit_without_importing_it(tmp_path):
    other = tmp_path / "other-tree"
    (other / "toolkit").mkdir(parents=True)
    (other / "toolkit" / "__init__.py").write_text("raise SystemExit(99)\n")
    run = _by_path(tmp_path, pythonpath=other)
    assert run.returncode == 2, run.stderr
    assert str(other) in run.stderr
    assert MODULE_FORM in run.stderr


def test_generator_by_path_reading_its_own_toolkit_runs(tmp_path):
    run = _by_path(tmp_path, pythonpath=ROOT)
    assert run.returncode == 0, run.stderr


def test_the_command_a_refusal_prints_works(tmp_path):
    refusal = _by_path(tmp_path, "-S").stderr
    command = next(line for line in refusal.splitlines() if line.startswith("Run: ")).removeprefix("Run: ")
    assert command.endswith(MODULE_FORM)
    run = subprocess.run(["bash", "-c", f"{command} --check"], cwd=tmp_path, capture_output=True, text=True, env=ENV)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize("stale", [False, True], ids=["missing", "stale"])
def test_generator_check_names_only_the_module_form(tmp_path, monkeypatch, capsys, stale):
    from scripts import generate_filter_registry as generator

    lib = tmp_path / "frontend" / "src" / "lib"
    lib.mkdir(parents=True)
    if stale:
        for path in GENERATED:
            (lib / path.name).write_text("stale\n")
    monkeypatch.setattr(generator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate_filter_registry", "--check"])
    assert generator.main() == 1
    err = capsys.readouterr().err
    assert MODULE_FORM in err
    assert PATH_FORM not in err


@pytest.mark.parametrize("path", [GENERATOR, *GENERATED], ids=lambda p: p.name)
def test_no_printed_or_committed_command_uses_the_path_form(path):
    assert PATH_FORM not in path.read_text(encoding="utf-8")

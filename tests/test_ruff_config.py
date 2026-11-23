"""Ruff's exclusion of the autodedup decision modules is exactly incremental.DECISION_MODULES: one byte edit there moves the live engine digest."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _decision_modules() -> set[str]:
    for node in ast.parse((ROOT / "autodedup" / "incremental.py").read_text()).body:
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if isinstance(node, ast.Assign) else None
        if isinstance(target, ast.Name) and target.id == "DECISION_MODULES" and node.value is not None:
            return {f"autodedup/{name}.py" for name in ast.literal_eval(node.value)}
    raise AssertionError("DECISION_MODULES is no longer a literal in autodedup/incremental.py")


def test_ruff_excludes_exactly_the_decision_modules() -> None:
    ruff = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]
    excluded = {path for path in ruff["extend-exclude"] if path.startswith("autodedup/")}
    assert excluded == _decision_modules()
    assert ruff.get("force-exclude") is True, "without force-exclude, `ruff check <file>` lints an excluded module"

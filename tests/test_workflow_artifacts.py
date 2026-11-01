"""Artifacts before the repository goes private (#2016): every upload expires, and a backup is never one."""

from __future__ import annotations

import pathlib
from typing import Any, Iterator

import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _upload_steps() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                    yield path.name, step


def test_every_artifact_upload_expires_within_90_days() -> None:
    # Live artifacts count against the plan's storage allowance once the repository is private.
    unbounded = [f"{name}: {step.get('name', step['uses'])}" for name, step in _upload_steps()
                 if not 1 <= int((step.get("with") or {}).get("retention-days", 0)) <= 90]
    assert not unbounded, unbounded

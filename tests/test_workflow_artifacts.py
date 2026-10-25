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


def test_the_retired_claims_backup_goes_to_r2_never_to_an_artifact() -> None:
    doc = yaml.safe_load((WORKFLOWS / "location_claims_retire.yml").read_text())
    (job,) = doc["jobs"].values()
    assert not any(str(s.get("uses", "")).startswith("actions/upload-artifact") for s in job["steps"])
    (store,) = [s for s in job["steps"] if "scripts.backup_before_drop" in s.get("run", "")]
    assert "retired-claims --file" in store["run"]
    assert {"R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME"} <= set(store["env"])
    # One object a run: a second run the same day must not overwrite the first one's rows.
    dump = next(s for s in job["steps"] if "\\copy" in s.get("run", ""))
    assert "${GITHUB_RUN_ID}" in dump["run"] and "${GITHUB_RUN_ID}" in store["run"]

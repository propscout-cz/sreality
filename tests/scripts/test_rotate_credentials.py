"""The credential rotation wizard (scripts/rotate_credentials.sh, M0 #2061) stays in step with what it writes to.

It cannot run in CI (it opens browsers and waits on a person), so these are static rails: the script parses,
every GitHub secret it sets is one a workflow reads (or a named exception), every row it records exists in
MVP.md § 9, and the inventory names every secret the script touches.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rotate_credentials.sh"
MVP = ROOT / "docs" / "design" / "agent-platform" / "MVP.md"
INVENTORY = ROOT / ".claude" / "skills" / "toolkit-api" / "references" / "secrets-inventory.md"

# Set by the wizard but read by no workflow, each for a stated reason.
_UNREAD_BY_WORKFLOWS = {
    "SUPABASE_SERVICE_ROLE_KEY": "kept for forward compatibility; no code reads it (inventory)",
}

_STAGES = SCRIPT.read_text(encoding="utf-8").split("# STAGES: credential rotation", 1)[1]


def _github_secrets_set() -> set[str]:
    """`set_secret NAME …`, plus the names of a `for pair in "NAME|$value" …` loop whose body sets `$name`."""
    names = set(re.findall(r"\bset_secret ([A-Z][A-Z0-9_]+) ", _STAGES))
    for head, body in re.findall(r"^for pair in ([^\n]+); do\n(.*?)^done$", _STAGES, re.M | re.S):
        if 'set_secret "$name"' in body:
            names |= set(re.findall(r'"([A-Z][A-Z0-9_]+)\|', head))
    return names


def test_the_script_parses() -> None:
    if not shutil.which("bash"):
        pytest.skip("no bash")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_every_github_secret_it_sets_is_read_by_a_workflow() -> None:
    workflows = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    names = _github_secrets_set()
    assert {"SUPABASE_DB_URL", "R2_ACCESS_KEY_ID", "MAPY_CZ_API_KEY"} <= names, names
    unread = sorted(n for n in names if f"secrets.{n}" not in workflows and n not in _UNREAD_BY_WORKFLOWS)
    assert unread == [], f"the wizard sets GitHub secrets no workflow reads: {unread}"


def test_every_recorded_row_exists_in_the_rotation_log() -> None:
    log = MVP.read_text(encoding="utf-8").split("## 9. Credential rotation log", 1)[1]
    labels = set(re.findall(r'(?:^\s*|&& )record "([^"]+)" "', _STAGES, re.M))
    assert len(labels) >= 10, labels
    missing = sorted(lab for lab in labels if not re.search(rf"^\| {re.escape(lab)} \| ", log, re.M))
    assert missing == [], f"MVP.md § 9 has no row for: {missing}"


def test_the_inventory_names_every_secret_the_script_touches() -> None:
    inventory = INVENTORY.read_text(encoding="utf-8")
    touched = set(re.findall(r"\b(?:stage_on_railway|delete_on_railway) \"\$RW_[A-Z]+\" [a-zA-Z]+ \"?([A-Z][A-Z0-9_]+)",
                             _STAGES)) | _github_secrets_set()
    assert len(touched) >= 12, touched
    absent = sorted(n for n in touched if f"`{n}`" not in inventory)
    assert absent == [], f"secrets-inventory.md does not name: {absent}"

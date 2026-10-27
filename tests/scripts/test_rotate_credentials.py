"""Static rails for the credential rotation wizard (scripts/rotate_credentials.sh, M0 #2061), which cannot run in CI (it opens browsers and waits on a person): it parses, its library is the /wizard template's, every GitHub secret it sets is one a workflow reads (or a named exception), every row it records exists in MVP.md's M0 rotation log, and the inventory names every secret it touches."""

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
_MARKER = "# STAGES: credential rotation"

# Set by the wizard but read by no workflow, each for a stated reason.
_UNREAD_BY_WORKFLOWS = {
    "SUPABASE_SERVICE_ROLE_KEY": "kept for forward compatibility; no code reads it (inventory)",
}


def _stages() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    assert _MARKER in text, f"the stages marker {_MARKER!r} moved; update this test"
    return "\n".join(line for line in text.split(_MARKER, 1)[1].splitlines() if not line.lstrip().startswith("#"))


def _loop_names(call: str) -> set[str]:
    """Names of a `for pair in "NAME|$value" …` loop whose body passes `$name` to `call`."""
    out: set[str] = set()
    for _indent, head, body in re.findall(r"^( *)for pair in ([^\n]+); do\n(.*?)^\1done$", _stages(), re.M | re.S):
        if re.search(rf'\b{call}\b[^\n]*"\$name"', body):
            out |= set(re.findall(r'"([A-Z][A-Z0-9_]+)\|', head))
    return out


def _github_secrets_set() -> set[str]:
    return set(re.findall(r"\b(?:set_secret|publish) ([A-Z][A-Z0-9_]+) ", _stages())) | _loop_names("publish")


def _railway_names() -> set[str]:
    literal = set(re.findall(r"\bstage_on_railway \S+ \S+ ([A-Z][A-Z0-9_]+) ", _stages()))
    deleted = re.findall(r"for name in ([A-Z_ ]+); do\n\s*if confirm \"Delete \$name from the Railway", _stages())
    return literal | _loop_names("stage_on_railway") | {n for group in deleted for n in group.split()}


def test_the_script_parses() -> None:
    if not shutil.which("bash"):
        pytest.skip("no bash")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_the_library_is_the_wizard_template() -> None:
    template = Path.home() / ".claude" / "skills" / "wizard" / "template.sh"
    if not template.exists():
        pytest.skip("the /wizard skill is not installed here")
    lib = template.read_text(encoding="utf-8").split("# STAGES: author this section.")[0]
    assert SCRIPT.read_text(encoding="utf-8").startswith(lib)


def test_every_github_secret_it_sets_is_read_by_a_workflow() -> None:
    workflows = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    names = _github_secrets_set()
    assert {"SUPABASE_DB_URL", "R2_ACCESS_KEY_ID", "MAPY_CZ_API_KEY", "MAPY2_CZ_API_KEY"} <= names, names
    unread = sorted(n for n in names if f"secrets.{n}" not in workflows and n not in _UNREAD_BY_WORKFLOWS)
    assert unread == [], f"the wizard sets GitHub secrets no workflow reads: {unread}"


def test_every_recorded_row_exists_in_the_rotation_log() -> None:
    log = MVP.read_text(encoding="utf-8").split("### M0 credential rotation log", 1)[1].split("\n**M1", 1)[0]
    labels = set(re.findall(r'\brecord "([^"]+)" "', _stages()))
    assert len(labels) >= 11, labels
    missing = sorted(lab for lab in labels if not re.search(rf"^\| {re.escape(lab)} \| ", log, re.M))
    assert missing == [], f"MVP.md's M0 rotation log has no row for: {missing}"


def test_the_inventory_names_every_secret_the_script_touches() -> None:
    inventory = INVENTORY.read_text(encoding="utf-8")
    touched = _railway_names() | _github_secrets_set()
    assert {"VALUO_API_TOKEN", "TENANT_POOL_DB_URL", "API_TOKEN", "GH_DISPATCH_TOKEN"} <= touched, touched
    absent = sorted(n for n in touched if f"`{n}`" not in inventory)
    assert absent == [], f"secrets-inventory.md does not name: {absent}"


def test_the_database_rotation_follows_the_runner_that_references_the_worker() -> None:
    """job-runner reads the worker's SUPABASE_DB_URL by reference (#2025-R3): Railway redeploys it after the worker,
    and the rotation is dated only once both runner heartbeats are fresh again. Its service id exists only after the
    apply, so the step opens the project canvas."""
    stages = _stages()
    db = stages.split("\ns_db() {", 1)[1].split("\n}\n", 1)[0]
    before, after = db.split('deploy_on_railway "$RW_WORKER" worker must', 1)
    assert "runner_follows_worker" in after, "the worker's deploy redeploys job-runner: the step follows it"
    assert "runner-list" in before, "the quiet-moment courtesy comes before the deploy"
    assert re.search(r'eyeball "Health: [^"]*job-runner[^"]*" \|\| ok=0', db)
    helper = stages.split("\nrunner_follows_worker() {", 1)[1].split("\n}\n", 1)[0]
    assert 'open_url "$RW_WORKER_PROJECT"' in helper and "Deploy Latest Commit" in helper
    canvas = re.search(r'^RW_WORKER_PROJECT="([^"]+)"$', SCRIPT.read_text(encoding="utf-8"), re.M)
    assert canvas and canvas.group(1) == "https://railway.com/project/6f6ac731-10a5-4a28-b831-350ead8a4a97"


def test_an_r2_rotation_follows_the_runner_that_references_the_worker() -> None:
    """job-runner reads the worker's R2 set by reference too (#2032): stage 11 deploys the worker's staged R2 pair,
    and Railway redeploys the runner after it, so the step follows that deploy as the database stage's does."""
    stages = _stages()
    deploy = stages.split("\ns_deploy() {", 1)[1].split("\n}\n", 1)[0]
    _before, after = deploy.split('deploy_on_railway "$RW_WORKER" worker', 1)
    assert "runner_follows_worker" in after, "the worker's deploy redeploys job-runner: the step follows it"
    helper = stages.split("\nrunner_follows_worker() {", 1)[1].split("\n}\n", 1)[0]
    assert "SUPABASE_DB_URL" in helper and "R2_" in helper, "the step names every reference that redeploys it"

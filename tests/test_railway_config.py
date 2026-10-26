"""Railway settings live only in .railway/railway.ts (#2060)."""

import re
from pathlib import Path

from tests._railway_iac import iac_entry, iac_text

ROOT = Path(__file__).resolve().parents[1]


def test_no_config_as_code_file_is_left_to_override_the_iac_file() -> None:
    """Until 2026-12-01 Railway still reads a railway.json/railway.toml on deploy, over the applied settings."""
    left = sorted(str(p.relative_to(ROOT)) for d in (ROOT, ROOT / "frontend")
                  for p in d.glob("railway*") if p.suffix in {".json", ".toml"})
    assert left == []


def test_each_railway_project_id_plans_only_its_own_definition() -> None:
    """All three services are named `sreality`, so a swapped entry would plan one project's settings onto
    another with no destructive line: the map is pinned by id, name and definition."""
    entries = re.findall(r'"([0-9a-f-]{36})": \{ name: "([^"]+)", define: (\w+) \}', iac_text())
    assert entries == [
        ("78652c43-bb7f-401b-a3de-5f532f76f72c", "API", "api"),
        ("6f6ac731-10a5-4a28-b831-350ead8a4a97", "realtime-worker", "worker"),
        ("cc62432a-5cd0-4c1a-8e3f-ba5d0bf79140", "vite", "vite"),
    ]


def test_the_wizard_plans_every_project_the_iac_file_declares() -> None:
    """The wizard keeps its own project tables: a project missing there is never planned or drift-checked, and its
    drift mode still exits 0."""
    wizard = (ROOT / "scripts" / "railway_iac_wizard.sh").read_text(encoding="utf-8")

    def table(name: str) -> dict[str, str]:
        match = re.search(rf"^declare -A {name}=\((.*?)\)$", wizard, re.M | re.S)
        assert match, name
        return dict(re.findall(r"\[(\w+)\]=(\S+)", match.group(1)))

    labels_line = re.search(r"^LABELS=\((.*?)\)$", wizard, re.M)
    assert labels_line
    labels = labels_line.group(1).split()
    ids, names = table("PROJECT_ID"), table("PROJECT_NAME")
    assert sorted(ids) == sorted(names) == sorted(labels)
    declared = re.findall(r'"([0-9a-f-]{36})": \{ name: "([^"]+)"', iac_text())
    assert sorted((ids[label], names[label]) for label in labels) == sorted(declared)


def test_only_the_worker_declares_a_start_command() -> None:
    """Ruling R5: the API and vite run their image's own command. Railway runs a start command in exec form, so a
    copied `uvicorn ... --port $PORT` never expands $PORT and fails every health check (8a362a0ea). #2075 scopes this
    to the `sreality` service when it adds agent-worker to `api`."""
    for name in ("api", "vite"):
        assert not re.search(r"\bstart(Command)?\s*:", iac_entry(name)), name


def test_the_api_and_the_worker_pass_their_extras_to_the_root_dockerfile_build() -> None:
    """Railway has no build-args field: a service variable reaches the Dockerfile ARG of the same name. Unset,
    the image installs `api` as before, and the build log's echo tells a delivered value from the fallback."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG EXTRAS\n" in dockerfile
    assert 'echo "EXTRAS=${EXTRAS:-unset}"' in dockerfile
    assert 'pip install ".[${EXTRAS:-api}]"' in dockerfile
    for name in ("api", "worker"):
        assert 'EXTRAS: "api"' in iac_entry(name), name

"""ADR-0007: the photo assessment's cache is read by its one tool and by nothing else. Browse shows no facet, the
estimator and every cohort keep the condition the portal states, and rule 14's parked levels stay unread by this tool."""

from __future__ import annotations

import re
from pathlib import Path

from toolkit import photo_assessment

ROOT = Path(__file__).resolve().parents[1]
CACHE = "photo_set_assessments"
OWNER = "toolkit/photo_assessment.py"
_TREES = {
    "api": "*.py", "toolkit": "*.py", "scraper": "*.py", "scripts": "*.py", "autodedup": "*.py", "location_data": "*.py",
    "contracts": "*", "frontend/src": "*", "chrome-extension/src": "*", ".github/workflows": "*", ".railway": "*",
}
# The one migration that creates it; a later one naming it is a new reader or writer to look at.
_MIGRATION = "migrations/705_photo_set_assessments.sql"


def _naming(needle: str, trees: dict[str, str]) -> set[str]:
    hits = set()
    for tree, pattern in trees.items():
        for path in (ROOT / tree).rglob(pattern):
            if path.is_file() and "node_modules" not in path.parts and needle in path.read_text("utf-8", errors="ignore"):
                hits.add(str(path.relative_to(ROOT)))
    return hits


def test_only_its_tool_names_the_cache_and_no_later_migration_does() -> None:
    assert _naming(CACHE, _TREES) == {OWNER}
    assert _naming(CACHE, {"migrations": "*.sql"}) == {_MIGRATION}


def test_the_census_sees_the_trees_it_claims_to() -> None:
    assert all((ROOT / tree).is_dir() for tree in _TREES)
    assert CACHE in (ROOT / OWNER).read_text(encoding="utf-8")


def test_no_other_python_imports_the_module_but_the_tool_and_the_comparison() -> None:
    importers = _naming("photo_assessment", {t: p for t, p in _TREES.items() if p == "*.py"}) - {OWNER}
    assert importers == {"api/capabilities/visuals.py", "scripts/photo_assessment_compare.py"}


def test_no_python_but_the_tool_the_comparison_and_the_module_names_the_tool() -> None:
    """The estimator's hard-wired tool list (api/agent.py), Browse's routes and the watchdog are all held here."""
    names = _naming("assess_property_visuals", {t: p for t, p in _TREES.items() if p == "*.py"})
    assert names == {OWNER, "api/capabilities/visuals.py"}


def test_the_cache_holds_nothing_of_an_account() -> None:
    """A row is shared by every agent: no account, user, run, property or ad, and no llm_calls id, which can name a run."""
    sql = (ROOT / _MIGRATION).read_text(encoding="utf-8")
    table = re.search(r"create table if not exists public\.photo_set_assessments \((.*?)\n\);", sql, re.S).group(1)
    columns = {line.split()[0] for line in table.splitlines() if line.strip() and not line.strip().startswith("primary")}
    assert columns == {"photo_set_hash", "prompt_version", "facets", "model", "photos_used", "plans_used", "cost_usd",
                       "created_at"}


def test_what_the_model_is_sent_has_no_argument_of_the_caller_but_the_property() -> None:
    """The tool's one argument picks a set of public photos and never reaches the prompt or the row."""
    from api.capabilities.visuals import AssessArgs

    assert set(AssessArgs.model_fields) == {"property_id"}
    assert "property_id" not in photo_assessment._STORE_SQL
    assert "listing_id" not in photo_assessment._STORE_SQL


def test_its_reads_name_no_operator_attachment() -> None:
    """Attachments (building_attachments) are an account's own; a photo set is `images` alone."""
    source = (ROOT / OWNER).read_text(encoding="utf-8")
    assert "attachment" not in source.lower().replace("no operator attachment", "")

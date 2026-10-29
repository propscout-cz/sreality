"""An agent is one folder: the loader refuses any folder that could not run, and every folder in the repo loads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from api.agents import definition as d
from api.agents import terminator
from api.capabilities import registry
from api.providers import ModelPrice
from tests.api._agent_fixtures import READS, WRITE, frontmatter, install_probes, write_folder

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def probes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_probes(monkeypatch)


# --- the repo's own folders --------------------------------------------------------------------------------------------

def test_every_agent_folder_in_the_repo_loads() -> None:
    """The CI check: a folder under skills/ that would not load fails here, before any image carries it."""
    for name, agent in d.load_all().items():
        assert agent.name == name
        assert set(agent.tools) <= set(registry.ENTRIES)


def test_the_image_carries_the_folders_the_loader_reads() -> None:
    assert d.SKILLS_DIR == ROOT / "skills"
    assert "COPY skills/ ./skills/" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


# --- a folder that loads ----------------------------------------------------------------------------------------------

def test_a_folder_loads_into_its_definition(probes: None, tmp_path: Path) -> None:
    folder = write_folder(tmp_path, references={"customer-groups.md": "Families want schools.", "facets.md": "  \n"})
    agent = d.load(folder)
    assert (agent.name, agent.description, agent.tools) == ("probe_scout_v1", "A probe agent.", READS)
    assert dict(agent.models) == {"anthropic": "claude-sonnet-4-5"}
    assert agent.limits == d.Limits(max_iterations=6, max_cost_usd=1.0, wall_clock_timeout_s=60.0)
    assert agent.instructions == ("Find properties and cite them.\n\n"
                                  "## references/customer-groups.md\n\nFamilies want schools.")
    assert agent.output_schema is None


def test_the_sha_covers_every_file_and_nothing_else(probes: None, tmp_path: Path) -> None:
    first = d.load(write_folder(tmp_path / "a", references={"facets.md": "view"}))
    again = d.load(write_folder(tmp_path / "b", references={"facets.md": "view"}))
    edited = d.load(write_folder(tmp_path / "c", references={"facets.md": "views"}))
    assert first.sha == again.sha != edited.sha


def test_the_input_schema_refuses_an_input_before_any_spend(probes: None, tmp_path: Path) -> None:
    agent = d.load(write_folder(tmp_path))
    agent.check_input({"town": "Brno"})
    with pytest.raises(d.InputError, match="town: 5 is not of type 'string'"):
        agent.check_input({"town": 5})
    with pytest.raises(d.InputError, match="Additional properties are not allowed"):
        agent.check_input({"town": "Brno", "price": 1})


# --- folders that do not load -------------------------------------------------------------------------------------------

_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {}}


@pytest.mark.parametrize(("meta", "match"), [
    ({"tools": ["probe_a"]}, r"unknown frontmatter keys \['tools'\]"),
    ({"limits": None}, r"missing frontmatter keys \['limits'\]"),
    ({"name": "other_v1"}, "must equal the folder name"),
    ({"allowed_tools": ["probe_a", "nope"]}, "unknown tool 'nope'"),
    ({"allowed_tools": ["probe_a", "probe_a"]}, "listed twice"),
    ({"allowed_tools": []}, "non-empty list"),
    ({"preferred_model": {"mistral": "x"}}, "unknown provider 'mistral'"),
    ({"preferred_model": {"anthropic": "claude-unpriced-9"}}, "no price"),
    ({"limits": {"max_iterations": 0, "max_cost_usd": 1, "wall_clock_timeout_s": 60}}, "max_iterations"),
    ({"limits": {"max_iterations": 5, "max_cost_usd": 25, "wall_clock_timeout_s": 60}}, "max_cost_usd"),
    ({"limits": {"max_iterations": True, "max_cost_usd": 1, "wall_clock_timeout_s": 60}}, "max_iterations"),
    ({"input": {"type": "object", "properties": {"a": {"type": "nonsense"}}}}, "not a valid JSON Schema"),
    ({"input": {"type": "object", "properties": {}}}, "refuses unknown keys"),
    ({"output": {"type": "array"}}, "output must be an object"),
    ({"input": {"type": "object", "additionalProperties": False, "properties": {"town": {"$ref": "file:///etc/passwd"}}}},
     r"uses \['\$ref'\]"),
    ({"output": {"type": "object", "additionalProperties": False, "properties": {"a": {"const": 1}}}},
     r"uses \['const'\]"),
    ({"output": {"type": "object", "additionalProperties": False, "properties": {"a": {"type": ["number", "null"]}}}},
     "names one type"),
    ({"output": {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "integer", "enum": [1]}}}},
     "enum values are strings"),
    ({"output": {"type": "object", "additionalProperties": False,
                 "properties": {"a": {"type": "object", "properties": {}}}}}, "every object refuses unknown keys"),
    ({"description": " "}, "description must be a non-empty string"),
])
def test_a_malformed_frontmatter_fails_with_what_to_fix(
        probes: None, tmp_path: Path, meta: dict[str, Any], match: str) -> None:
    folder = write_folder(tmp_path, meta=frontmatter("probe_scout_v1", **meta))
    with pytest.raises(d.DefinitionError, match=match) as info:
        d.load(folder)
    assert str(info.value).startswith("probe_scout_v1: ")


@pytest.mark.parametrize(("make", "match"), [
    (lambda f: (f / "SKILL.md").unlink(), "no SKILL.md"),
    (lambda f: (f / "SKILL.md").write_text("name: x\n", encoding="utf-8"), "must open with a --- frontmatter"),
    (lambda f: (f / "SKILL.md").write_text("---\nname: [\n---\nbody\n", encoding="utf-8"), "not valid YAML"),
    (lambda f: (f / "SKILL.md").write_text("---\nname: x\n", encoding="utf-8"), "not closed"),
    (lambda f: (f / "SKILL.md").write_text("---\n- a\n---\nbody\n", encoding="utf-8"), "must be a mapping"),
    (lambda f: (f / "run.py").write_text("x = 1\n", encoding="utf-8"), r"not \['run.py'\]"),
    (lambda f: (f / "references").mkdir() or (f / "references" / "a.md").write_bytes(b"\xff"), "UTF-8"),
    (lambda f: (f / "references").mkdir() or (f / "references" / "a.md").symlink_to("/etc/hostname"), "not links"),
])
def test_a_malformed_folder_fails_with_what_to_fix(probes: None, tmp_path: Path, make: Any, match: str) -> None:
    folder = write_folder(tmp_path)
    make(folder)
    with pytest.raises(d.DefinitionError, match=match):
        d.load(folder)


def test_a_linked_folder_is_refused(probes: None, tmp_path: Path) -> None:
    real = write_folder(tmp_path / "real")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "probe_scout_v1").symlink_to(real, target_is_directory=True)
    with pytest.raises(d.DefinitionError, match="not links"):
        d.load_all(tmp_path / "skills")


def test_an_empty_body_is_no_skill(probes: None, tmp_path: Path) -> None:
    with pytest.raises(d.DefinitionError, match="the skill body must be a non-empty string"):
        d.load(write_folder(tmp_path, body=""))


def test_one_bad_folder_fails_the_whole_tree(probes: None, tmp_path: Path) -> None:
    write_folder(tmp_path, "good_v1", meta=frontmatter("good_v1"))
    write_folder(tmp_path, "bad_v1", meta=frontmatter("bad_v1", allowed_tools=["nope"]))
    (tmp_path / "README.md").write_text("not an agent", encoding="utf-8")
    with pytest.raises(d.DefinitionError, match="bad_v1: unknown tool"):
        d.load_all(tmp_path)


# --- the checks the estimator's skills table shares --------------------------------------------------------------------

class _Priced:
    def __init__(self, *priced: str) -> None:
        self.priced = priced

    def price_for(self, model: str) -> ModelPrice | None:
        return ModelPrice(1.0, 1.0) if model in self.priced else None


def test_a_model_map_may_name_a_subset_of_the_providers() -> None:
    providers: Any = {"anthropic": _Priced("a"), "gemini": _Priced("g"), "oss": _Priced("o")}
    assert d.models({"anthropic": "a", "gemini": "g"}, providers) == {"anthropic": "a", "gemini": "g"}
    for bad in ({}, [], {"anthropic": 1}):
        with pytest.raises(d.DefinitionError, match="preferred_model"):
            d.models(bad, providers)


def test_the_checks_return_what_they_accept() -> None:
    assert d.text("ok", "x") == "ok"
    assert d.tool_names(["a", "b"], {"a", "b", "c"}) == ["a", "b"]
    assert d.limits({"max_iterations": 8, "max_cost_usd": 0.5, "wall_clock_timeout_s": 90}) == d.Limits(8, 0.5, 90.0)


# --- what the model is shown --------------------------------------------------------------------------------------------

def test_submit_result_offers_proposals_and_output_only_to_an_agent_that_has_them(
        probes: None, tmp_path: Path) -> None:
    plain = d.load(write_folder(tmp_path / "a"))
    properties = terminator.schema(plain)["input_schema"]["properties"]
    assert set(properties) == {"markdown", "cited_property_ids", "files"}

    output = {"type": "object", "additionalProperties": False, "required": ["verdict"],
              "properties": {"verdict": {"type": "string", "enum": ["under", "fair", "over"],
                                         "description": "Priced against its cohort."}}}
    rich = d.load(write_folder(tmp_path / "b", meta=frontmatter(
        "probe_scout_v1", allowed_tools=[*READS, WRITE], output=output)))
    shown = terminator.schema(rich)
    assert set(shown["input_schema"]["properties"]) == {"markdown", "cited_property_ids", "files",
                                                        "proposed_actions", "output"}
    assert shown["input_schema"]["required"] == ["markdown", "output"]
    assert f"You may propose {WRITE}: Add a note to a property." in shown["description"]


def test_submit_result_builds_with_the_gemini_sdk(probes: None, tmp_path: Path) -> None:
    """Gemini's client refuses what the other providers take silently; an agent's output schema reaches it."""
    types = pytest.importorskip("google.genai.types")
    from api.providers.gemini import _schema_for_gemini
    output = {"type": "object", "additionalProperties": False, "required": ["rank"], "properties": {
        "rank": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1, "nullable": True, "description": "Best 1."},
        "why": {"type": "string", "minLength": 1, "maxLength": 300, "pattern": "^.+$", "format": "text",
                "description": "One line."},
        "verdicts": {"type": "array", "minItems": 1, "maxItems": 20, "description": "One per property.",
                     "items": {"type": "string", "enum": ["under", "fair", "over"], "description": "One."}},
    }}
    assert set().union(*[set(n) for n in d.nodes(output)]) == d.PORTABLE_KEYWORDS
    agent = d.load(write_folder(tmp_path, meta=frontmatter("probe_scout_v1", output=output)))
    shown = terminator.schema(agent)
    types.FunctionDeclaration(name=shown["name"], description=shown["description"],
                              parameters=_schema_for_gemini(shown["input_schema"]))

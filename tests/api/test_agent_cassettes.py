"""Every recorded run replays on every pull request (#2074; PROGRAM § 3.9's E0): the lane over `tests/cassettes/`.

No real cassette exists yet: the scout's graded runs are #2069's. Until they land the lane replays nothing, and everything
below `test_every_agent_has_its_floor_of_recordings` proves it on synthetic recordings made here by the real loop. The
floor turns on by itself the moment an agent folder lands in `skills/`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from api.agents import cassette, loop, terminator
from api.agents.definition import load
from api.capabilities import run_scope
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from api.providers import ModelPrice
from tests.api._agent_fixtures import READS, frontmatter, install_probes, reply, write_folder
from tests.api._agent_replay import CASSETTES, FLOORS, failures, floors_unmet, recorded, replay_file
from tests.api._fakes import _FakeConn, _ScriptedProvider

AGENT = "probe_scout_v1"
MODEL = "claude-sonnet-4-5"
SUBMIT = terminator.NAME
CHEAP = ModelPrice(1.0, 1.0)


def test_every_recorded_cassette_replays() -> None:
    assert CASSETTES.is_dir(), "a moved directory would make this lane replay nothing, green"
    assert failures() == {}


def test_every_agent_has_its_floor_of_recordings() -> None:
    """Legitimately empty today. When #2069's folder lands this asks for its ten graded runs, scrubbed: see below."""
    assert floors_unmet() == [], ("record with scripts/agent_run.py --record, scrub what the user can see (the repository "
                                  "is public), and save it under tests/cassettes/")


# --- the lane itself, on synthetic recordings ----------------------------------------------------------------------------

@pytest.fixture()
def skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    install_probes(monkeypatch)
    root = tmp_path / "skills"
    write_folder(root, meta=frontmatter(AGENT))
    return root


def _record(path: Path, skills: Path, *replies: Any, results: dict[str, Any] | None = None,
            bills: dict[str, float] | None = None) -> Path:
    """One real run of the loop over scripted replies, saved as `scripts/agent_run.py --record` saves it."""
    definition = load(skills / AGENT)
    steps: list[loop.Step] = []

    def call_tool(name: str, args: Any) -> dict[str, Any]:
        run_scope.current().charge((bills or {}).get(name, 0.0), None)
        result = (results or {}).get(name, {"data": []})
        if isinstance(result, ToolError):
            raise result
        return result

    provider = _ScriptedProvider("anthropic", list(replies), prices={MODEL: CHEAP})
    outcome = loop.run(definition, {"town": "Brno"}, provider="anthropic",
                       llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=call_tool,
                       record=steps.append)
    path.parent.mkdir(parents=True, exist_ok=True)
    cassette.dump(path, definition=definition, provider="anthropic", model=MODEL, price=CHEAP, value={"town": "Brno"},
                  steps=steps, outcome=outcome)
    return path


def _good(path: Path, skills: Path) -> Path:
    return _record(path, skills, reply((READS[0], {"q": 1})), reply((SUBMIT, {"markdown": "Find 7.", "cited_property_ids": [7]})),
                   results={READS[0]: {"data": [{"property_id": 7}]}})


def _edit(path: Path, change: Any) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    change(document)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_a_directory_of_recordings_replays_and_a_dotfile_is_no_recording(tmp_path: Path, skills: Path) -> None:
    root = tmp_path / "cassettes"
    _good(root / "one.json", skills)
    _good(root / "nested" / "two.json", skills)
    (root / ".gitkeep").touch()
    assert [p.name for p in recorded(root)] == ["two.json", "one.json"]
    assert failures(root, skills) == {}


def test_a_recording_holding_denials_replays_to_the_same_denials(tmp_path: Path, skills: Path) -> None:
    """What this suite exists for: a refused tool, an unlisted tool and a refused answer all come back as they went."""
    path = _record(
        tmp_path / "denied.json", skills,
        reply((READS[0], {}), ("delete_listing", {})),
        reply((SUBMIT, {"markdown": "Find 9.", "cited_property_ids": [9]})),
        reply((SUBMIT, {"markdown": "None."})),
        results={READS[0]: ToolError(ToolErrorCode.REFUSED, "no")})
    document = cassette.load(path)
    assert [s["output"]["error"]["code"] for s in document["steps"] if "error" in s["output"]] == [
        "refused", "unknown_tool", "refused"]
    replay_file(path, skills)


def test_a_paid_tools_bill_counts_into_the_replayed_cost(tmp_path: Path, skills: Path) -> None:
    """A scout's photo assessment: its bill sits on the tool step, and the recorded outcome's cost includes it."""
    path = _record(tmp_path / "paid.json", skills, reply((READS[0], {})), reply((SUBMIT, {"markdown": "Done."})),
                   bills={READS[0]: 0.25})
    assert cassette.load(path)["outcome"]["cost_usd"] > 0.25
    replay_file(path, skills)


def _corrupt_cases() -> dict[str, tuple[Any, str]]:
    def set_key(key: str, value: Any) -> Any:
        return lambda d: d.__setitem__(key, value)

    return {
        "a step gone": (lambda d: d["steps"].pop(1), "AssertionError"),
        "a step too many": (lambda d: d["steps"].insert(3, {**d["steps"][1], "n": 99}), "AssertionError"),
        "a reply gone": (lambda d: d["steps"].pop(2), "RuntimeError"),
        "a tool result changed so the citation fails": (lambda d: d["steps"][1]["output"].update(data=[]), "RuntimeError"),
        "an argument changed": (lambda d: d["steps"][1]["input"].update(q=2), "AssertionError"),
        "the recorded outcome changed": (lambda d: d["outcome"].update(cost_usd=99.0), "AssertionError"),
        "no outcome (a run that raised)": (set_key("outcome", None), "ValueError"),
        "another version": (set_key("version", 0), "ValueError"),
        "no steps": (lambda d: d.pop("steps"), "KeyError"),
        "an agent with no folder": (set_key("agent", "nobody"), "DefinitionError"),
        "an input the agent refuses": (set_key("input", {"city": "Brno"}), "InputError"),
    }


@pytest.mark.parametrize("name", list(_corrupt_cases()))
def test_a_corrupt_recording_fails_by_name_and_the_rest_still_replay(tmp_path: Path, skills: Path, name: str) -> None:
    change, error = _corrupt_cases()[name]
    root = tmp_path / "cassettes"
    _good(root / "fine.json", skills)
    _edit(_good(root / "bad.json", skills), change)
    found = failures(root, skills)
    assert list(found) == ["bad.json"], found
    assert found["bad.json"].startswith(error), found


@pytest.mark.parametrize(("file", "body", "says"), [
    ("broken.json", "{not json", "JSONDecodeError"),
    ("empty.json", "", "JSONDecodeError"),
    ("run.jsonl", "{}", "a cassette is a .json file"),
    ("leak.json", '{"owner_email": "someone@example.test"}', "scrub it"),
])
def test_a_file_that_is_no_recording_fails_loudly(tmp_path: Path, skills: Path, file: str, body: str, says: str) -> None:
    (tmp_path / file).write_text(body, encoding="utf-8")
    assert says in failures(tmp_path, skills)[file]


# --- the floor ---------------------------------------------------------------------------------------------------------------

def test_no_agent_folder_owes_nothing_so_todays_empty_lane_is_green(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "README.md").touch()
    (tmp_path / "cassettes").mkdir()
    assert floors_unmet(tmp_path / "cassettes", tmp_path / "skills") == []


def test_an_agent_folder_owes_a_recording_the_moment_it_lands(tmp_path: Path, skills: Path) -> None:
    root = tmp_path / "cassettes"
    root.mkdir()
    assert floors_unmet(root, skills) == [f"{AGENT} has 0 of 1"]
    _good(root / "one.json", skills)
    assert floors_unmet(root, skills) == []


def test_the_scouts_floor_is_its_ten_graded_runs_and_a_corrupt_one_does_not_count(tmp_path: Path, skills: Path) -> None:
    scout = next(iter(FLOORS))
    assert FLOORS[scout] == 10
    write_folder(skills, scout, meta=frontmatter(scout))
    root = tmp_path / "cassettes"
    for n in range(10):
        _good(root / f"{n}.json", skills)
        _edit(root / f"{n}.json", lambda d: d.__setitem__("agent", scout))
    (root / "broken.json").write_text("{not json", encoding="utf-8")
    probe_owes = f"{AGENT} has 0 of 1"
    assert floors_unmet(root, skills) == [probe_owes]
    (root / "9.json").unlink()
    assert floors_unmet(root, skills) == [probe_owes, f"{scout} has 9 of 10"]

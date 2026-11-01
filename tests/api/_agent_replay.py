"""Replay a cassette through the real loop: its model replies from the scripted provider, its tool results in order.

The loop answers two kinds of tool call itself (submit_result, and a name the agent does not list, refused as
unknown_tool); every other recorded tool step is one the registry ran, and is served back only if the replayed run
asks for the same tool with the same arguments at the same point.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from api.agents import cassette, terminator
from api.agents.definition import SKILLS_DIR, Definition, load
from api.agents.loop import Outcome, run
from api.capabilities import run_scope
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from api.providers import Completion, HostedFile, ModelPrice, ProviderError, ToolCall, Usage
from tests.api._agent_fixtures import ScriptedCodeProvider
from tests.api._fakes import _FakeConn, _ScriptedProvider

# Where a recorded run lives, in git, for every pull request to replay.
CASSETTES = Path(__file__).resolve().parents[1] / "cassettes"
# How many recordings an agent folder owes. #2069's acceptance: ten graded shortlists, each saved as a cassette. Any other
# agent owes one, or its green replay would prove nothing about it.
FLOORS = {"regional_scout_v1": 10}
DEFAULT_FLOOR = 1
# The repository is public and a production cassette carries what its user can see (skills/README.md): an account's
# owner_email, or an address in an ad's text, is the first thing a scrub missed.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*\w")


def _completion(output: dict[str, Any]) -> Completion:
    return Completion(
        text_blocks=list(output["text"]),
        tool_calls=[ToolCall(**call) for call in output["tool_calls"]],
        stop_reason=output["stop_reason"],
        usage=Usage(**output["usage"]),
        model=output["model"],
        raw=None,
        hosted_calls=tuple(ToolCall(**call) for call in output.get("hosted_calls", ())),
        hosted_pending=tuple(output.get("hosted_pending", ())),
        container=output.get("container"),
        file_ids=tuple(output.get("file_ids", ())),
    )


def _code_provider(document: dict[str, Any], completions: list[Completion], prices: dict[str, ModelPrice]) -> ScriptedCodeProvider:
    """Each produced file as its journal step remembers it: right name, type and size, its bytes never recorded; one that
    failed fails at the point it did, before its name was known or after."""
    provider = ScriptedCodeProvider(completions, name=document["provider"], prices=prices)
    for step in document["steps"]:
        if step["kind"] != "file":
            continue
        output, file_id = step["output"], step["input"]["file_id"]
        if "error" not in output:
            provider.files[file_id] = HostedFile(name=output["name"], media_type=output["media_type"], data=bytes(output["bytes"]))
        elif "name" not in output:
            provider.files[file_id] = ProviderError(output["error"])
        else:
            provider.files[file_id] = HostedFile(name=output["name"], media_type="application/octet-stream", data=b"")
            provider.byte_errors[file_id] = ProviderError(output["error"])
    return provider


def _ran_in_the_registry(step: dict[str, Any]) -> bool:
    error = step["output"].get("error") if set(step["output"]) == {"error"} else None
    return (step["kind"] == "tool" and step["name"] != terminator.NAME
            and not (error and error["code"] == ToolErrorCode.UNKNOWN_TOOL))


def replay(document: dict[str, Any], definition: Definition) -> Outcome:
    if document["outcome"] is None:
        raise ValueError("an incomplete cassette (its run raised) is for reading, not replay")
    steps = document["steps"]
    completions = [_completion(s["output"]) for s in steps if s["kind"] == "model"]
    prices = {document["model"]: ModelPrice(**document["price"])}
    provider = (_code_provider(document, completions, prices) if definition.code
                else _ScriptedProvider(document["provider"], completions, prices=prices))
    served = iter([s for s in steps if _ran_in_the_registry(s)])

    def call_tool(name: str, args: Any) -> dict[str, Any]:
        step = next(served, None)
        assert step is not None, f"the replay called {name} past the end of the recording"
        assert (step["name"], step["input"]) == (name, args), f"the replay left the recording at step {step['n']}"
        # What the real call charged, so a paid tool's bill counts into the replayed run as it did into the recorded one.
        run_scope.current().charge(step["cost_usd"], step["llm_call_id"])
        if set(step["output"]) == {"error"}:
            error = step["output"]["error"]
            raise ToolError(ToolErrorCode(error["code"]), error["message"], details=error.get("details", ()))
        return step["output"]

    llm = LLMClient(_FakeConn(), providers={document["provider"]: provider})
    outcome = run(definition, document["input"], provider=document["provider"], llm=llm, call_tool=call_tool,
                  record=lambda step: None)
    assert next(served, None) is None and not provider._queue, "the replay ended before the recording did"
    return outcome


def recorded(root: Path = CASSETTES) -> list[Path]:
    """Every file under `root` but a dotfile: a misnamed recording is a failure, not a file nobody replays."""
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def replay_file(path: Path, skills: Path = SKILLS_DIR) -> None:
    """Replay one recording through the current loop and terminator against the agent's current folder: it must end as it
    ended. A changed skill body is no failure (the replies are recorded); a tool removed or an answer now refused is."""
    if path.suffix != ".json":
        raise ValueError("a cassette is a .json file")
    if found := _EMAIL.search(path.read_text(encoding="utf-8")):
        raise ValueError(f"holds an e-mail address ({found[0]}): scrub it, the repository is public")
    document = cassette.load(path)
    outcome = replay(document, load(skills / document["agent"]))
    assert cassette.plain_outcome(outcome) == document["outcome"], "the replay ended differently from the recording"


def failures(root: Path = CASSETTES, skills: Path = SKILLS_DIR) -> dict[str, str]:
    """Every recording that does not replay, with why; empty when all do (or there are none yet)."""
    failed: dict[str, str] = {}
    for path in recorded(root):
        try:
            replay_file(path, skills)
        except Exception as exc:  # noqa: BLE001 - a corrupt file must name itself and leave the rest to run
            failed[path.name] = f"{type(exc).__name__}: {exc}"
    return failed


def floors_unmet(root: Path = CASSETTES, skills: Path = SKILLS_DIR) -> list[str]:
    """Each agent folder short of its recordings. No agent folder, no floor: that is today's legitimately empty lane."""
    held: dict[str, int] = {}
    for path in recorded(root):
        try:
            agent = cassette.load(path)["agent"]
        except (ValueError, KeyError):  # failures() names it; it just counts for nothing here
            continue
        held[agent] = held.get(agent, 0) + 1
    agents = sorted(p.name for p in skills.iterdir() if p.is_dir())
    return [f"{agent} has {held.get(agent, 0)} of {FLOORS.get(agent, DEFAULT_FLOOR)}" for agent in agents
            if held.get(agent, 0) < FLOORS.get(agent, DEFAULT_FLOOR)]

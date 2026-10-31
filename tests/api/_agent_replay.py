"""Replay a cassette through the real loop: its model replies from the scripted provider, its tool results in order.

The loop answers two kinds of tool call itself (submit_result, and a name the agent does not list, refused as
unknown_tool); every other recorded tool step is one the registry ran, and is served back only if the replayed run
asks for the same tool with the same arguments at the same point.
"""

from __future__ import annotations

from typing import Any

from api.agents import terminator
from api.agents.definition import Definition
from api.agents.loop import Outcome, run
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from api.providers import Completion, HostedFile, ModelPrice, ProviderError, ToolCall, Usage
from tests.api._agent_fixtures import ScriptedCodeProvider
from tests.api._fakes import _FakeConn, _ScriptedProvider


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
        step = next(served)
        assert (step["name"], step["input"]) == (name, args), f"the replay left the recording at step {step['n']}"
        if set(step["output"]) == {"error"}:
            error = step["output"]["error"]
            raise ToolError(ToolErrorCode(error["code"]), error["message"], details=error.get("details", ()))
        return step["output"]

    llm = LLMClient(_FakeConn(), providers={document["provider"]: provider})
    return run(definition, document["input"], provider=document["provider"], llm=llm, call_tool=call_tool,
               record=lambda step: None)

"""The one way a run ends: submit_result, accepted only when this run's own tool results back what it claims."""

from __future__ import annotations

import json
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import Field, ValidationError

from api.agents.definition import Definition
from api.capabilities import registry
from api.capabilities.entry import Effect
from api.capabilities.errors import ToolError, ToolErrorCode, invalid_arguments
from api.capabilities.schema import portable
from api.filter_args import Args
from api.providers import HostedFile

NAME = "submit_result"


class _Proposal(Args):
    entry: str = Field(description="The write tool to apply, by name.")
    args: dict[str, Any] = Field(description="Its arguments, as that tool's schema asks.")


class _Submission(Args):
    markdown: str = Field(min_length=1, description="The answer for the user, in Markdown.")
    cited_property_ids: list[int] = Field(
        default_factory=list,
        description="Every property the answer names, by the property_id a tool returned in this run.")
    files: list[str] = Field(default_factory=list, description="Files this run produced that go with the answer.")
    proposed_actions: list[_Proposal] = Field(
        default_factory=list,
        description="Changes the user may apply with one click; nothing is written unless they do.")


@dataclass(frozen=True)
class Answer:
    """An accepted submit_result."""

    markdown: str
    cited_property_ids: tuple[int, ...]
    files: tuple[HostedFile, ...]
    proposed_actions: tuple[Mapping[str, Any], ...]
    output: Mapping[str, Any] | None


def proposable(definition: Definition) -> tuple[str, ...]:
    """The write tools this agent lists: never called by the run, only proposed to the user."""
    return tuple(t for t in definition.tools if registry.ENTRIES[t].effect is Effect.WRITE)


def schema(definition: Definition) -> dict[str, Any]:
    """submit_result as this agent's model sees it: proposals only if it may propose, `output` only if declared."""
    input_schema = portable(_Submission)
    properties = input_schema["properties"]
    entries = proposable(definition)
    if not entries:
        del properties["proposed_actions"]
    if definition.output_schema is not None:
        properties["output"] = dict(definition.output_schema)
        input_schema["required"] = [*input_schema.get("required", ()), "output"]
    description = ("Hand in the answer; the run ends when it is accepted. Call it alone in its turn. Cite only "
                   "properties a tool returned in this run; a refusal says what to fix.")
    if definition.code:
        description += ("\n\nA file reaches the user only from $OUTPUT_DIR: copy it there in the command that lists "
                        "that directory, then name it in `files` as listed. Each command has its own $OUTPUT_DIR.")
    for name in entries:
        entry = registry.ENTRIES[name]
        description += f"\n\nYou may propose {name}: {entry.description} Its arguments: {json.dumps(entry.input_schema)}"
    return {"name": NAME, "description": description, "input_schema": input_schema}


def property_ids(result: Mapping[str, Any]) -> set[int]:
    """Every value keyed `property_id` under a tool result's `data`; never an argument echoed in its metadata."""
    found: set[int] = set()
    stack: list[Any] = [result.get("data")]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "property_id" and isinstance(value, int) and not isinstance(value, bool):
                    found.add(value)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _check_output(definition: Definition, output: Any) -> None:
    if definition.output_schema is None:
        if output is not None:
            raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, "this agent declares no output; answer in markdown")
        return
    errors = sorted(Draft202012Validator(definition.output_schema).iter_errors(output), key=lambda e: list(e.path))
    if errors:
        details = [{"loc": ["output", *e.path], "msg": e.message[:200], "type": str(e.validator)} for e in errors]
        where = "; ".join(f"{'.'.join(map(str, d['loc']))}: {d['msg']}" for d in details)
        raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, f"invalid output: {where}", details=details)


def accept(raw: Any, *, definition: Definition, seen: Set[int], produced: Mapping[str, HostedFile]) -> Answer:
    """The answer, or a ToolError the model can act on: a shape error, an uncited property, a file this run did
    not produce, or a proposal that names no write tool this agent lists or fails that tool's schema."""
    if not isinstance(raw, dict):
        raise ToolError(ToolErrorCode.INVALID_ARGUMENTS, f"{NAME} takes an object")
    fields = dict(raw)
    output = fields.pop("output", None)
    try:
        submission = _Submission.model_validate(fields)
    except ValidationError as exc:
        raise invalid_arguments(exc) from exc
    _check_output(definition, output)
    if uncited := [i for i in submission.cited_property_ids if i not in seen]:
        raise ToolError(ToolErrorCode.REFUSED,
                        f"no tool returned property {uncited} in this run; cite only property_ids a tool returned")
    if unknown := [f for f in submission.files if f not in produced]:
        raise ToolError(ToolErrorCode.REFUSED,
                        f"this run produced no file named {unknown}; the files it has: {sorted(produced)}")
    entries = proposable(definition)
    for i, proposal in enumerate(submission.proposed_actions):
        if proposal.entry not in entries:
            raise ToolError(ToolErrorCode.REFUSED, f"proposed_actions[{i}]: {proposal.entry!r} is not a write tool "
                                                   f"this agent may propose; it may propose {list(entries)}")
        try:
            registry.validate(proposal.entry, proposal.args)
        except ToolError as exc:
            raise ToolError(exc.code, f"proposed_actions[{i}]: {exc.message}", details=exc.details) from None
    return Answer(
        markdown=submission.markdown,
        cited_property_ids=tuple(submission.cited_property_ids),
        files=tuple(produced[name] for name in dict.fromkeys(submission.files)),
        proposed_actions=tuple({"entry": p.entry, "args": p.args} for p in submission.proposed_actions),
        output=output,
    )

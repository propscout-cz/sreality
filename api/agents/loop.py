"""The one loop every agent runs: render its input, call its model, run its tools, stop on a cap or an accepted answer."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from api.agents import terminator
from api.agents.definition import Definition, DefinitionError
from api.capabilities import registry
from api.capabilities.entry import Effect
from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.truncate import encode
from api.llm_client import LLMClient
from api.providers import Message, ModelPrice, TextBlock, ToolResultBlock, ToolUseBlock

CALLED_FOR = "agent_run"
# A reply's limit covers the model's thinking too (Opus/Sonnet 5.5 always think): the ceiling is the non-streaming
# size Anthropic recommends, and a budget that buys less than the floor ends the run rather than pay for a reply
# likely to be cut off.
MAX_REPLY_TOKENS = 16_000
MIN_REPLY_TOKENS = 4_096

StopReason = Literal["submit_result", "max_iterations", "max_cost", "timeout", "end_turn", "max_tokens"]


@dataclass(frozen=True)
class Step:
    """One journal line, in the order the run made it: a model call (`name` = the model) or a tool call."""

    n: int
    kind: Literal["model", "tool"]
    name: str
    input: Any
    output: Mapping[str, Any]
    cost_usd: float
    duration_ms: int
    llm_call_id: int | None


@dataclass(frozen=True)
class Outcome:
    stop_reason: StopReason
    answer: terminator.Answer | None
    model_calls: int
    cost_usd: float


def _bytes(value: Any) -> int:
    """An upper bound on the tokens `value` costs as input: a byte-level tokenizer spends at most one per byte."""
    return len(encode(value).encode())


def _reply_tokens(remaining_usd: float, prompt_tokens: int, price: ModelPrice) -> int:
    """The longest reply whose worst case fits the budget: every prompt token at the dearer of the input and
    cache-write rates (the provider may write the prefix to its cache), every reply token at the output rate."""
    left = remaining_usd - prompt_tokens * max(price.input_per_mtok, price.cache_write_per_mtok) / 1e6
    if left <= 0:
        return 0
    if price.output_per_mtok == 0:
        return MAX_REPLY_TOKENS
    return min(MAX_REPLY_TOKENS, math.floor(left * 1e6 / price.output_per_mtok))


def tool_schemas(definition: Definition) -> list[dict[str, Any]]:
    """What this agent's model is offered: its tools it may call, then the terminator."""
    callable_names = {t for t in definition.tools if registry.ENTRIES[t].effect is not Effect.WRITE}
    return [s for s in registry.schemas() if s["name"] in callable_names] + [terminator.schema(definition)]


def run(
    definition: Definition,
    value: Mapping[str, Any],
    *,
    provider: str,
    llm: LLMClient,
    call_tool: Callable[[str, Any], dict[str, Any]],
    record: Callable[[Step], None],
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Run `definition` on `value`. `call_tool` is the registry call under the run's caller; `record` journals."""
    definition.check_input(value)
    model = definition.models.get(provider)
    if model is None:
        raise DefinitionError(f"{definition.name} names no model for {provider}; it runs on {sorted(definition.models)}")
    price = llm.provider(provider).price_for(model)
    if price is None:
        raise DefinitionError(f"{provider} has no price for {model!r}")

    callable_names = {t for t in definition.tools if registry.ENTRIES[t].effect is not Effect.WRITE}
    tools = tool_schemas(definition)
    opening = json.dumps(value, ensure_ascii=False, indent=2)
    messages = [Message(role="user", content=[TextBlock(text=opening)])]
    prompt_tokens = _bytes([definition.instructions, tools, opening])
    deadline = clock() + definition.limits.wall_clock_timeout_s
    seen: set[int] = set()
    produced: set[str] = set()
    steps = 0
    calls = 0
    spent = 0.0

    def journal(kind: Literal["model", "tool"], name: str, given: Any, output: Mapping[str, Any],
                started: float, cost_usd: float = 0.0, llm_call_id: int | None = None) -> None:
        nonlocal steps
        steps += 1
        record(Step(n=steps, kind=kind, name=name, input=given, output=output, cost_usd=cost_usd,
                    duration_ms=int((clock() - started) * 1000), llm_call_id=llm_call_id))

    def outcome(stop_reason: StopReason, answer: terminator.Answer | None = None) -> Outcome:
        return Outcome(stop_reason=stop_reason, answer=answer, model_calls=calls, cost_usd=round(spent, 6))

    while True:
        if calls >= definition.limits.max_iterations:
            return outcome("max_iterations")
        if clock() >= deadline:
            return outcome("timeout")
        max_tokens = _reply_tokens(definition.limits.max_cost_usd - spent, prompt_tokens, price)
        if max_tokens < MIN_REPLY_TOKENS:
            return outcome("max_cost")

        started = clock()
        response = llm.call(called_for=CALLED_FOR, messages=messages, system=definition.instructions, tools=tools,
                            model=model, provider=provider, max_tokens=max_tokens, timeout_s=deadline - started)
        calls += 1
        spent += response.cost_usd
        completion = response.completion
        journal("model", model, {"max_tokens": max_tokens}, {
            "text": list(completion.text_blocks),
            "tool_calls": [{"id": c.id, "name": c.name, "input": c.input} for c in completion.tool_calls],
            "stop_reason": completion.stop_reason,
            "usage": asdict(completion.usage),
            "model": completion.model,
        }, started, response.cost_usd, response.llm_call_id)
        # A turn's calls run only when the model ended it on them: one cut at its limit or stopped by a refusal may
        # carry a half-written call, and nothing in it runs.
        if completion.stop_reason != "tool_use" or not completion.tool_calls:
            return outcome("max_tokens" if completion.stop_reason == "max_tokens" else "end_turn")

        messages.append(Message(role="assistant", content=[
            *(TextBlock(text=t) for t in completion.text_blocks if t),
            *(ToolUseBlock(id=c.id, name=c.name, input=c.input) for c in completion.tool_calls),
        ]))
        results: list[ToolResultBlock] = []
        for call in completion.tool_calls:
            started = clock()
            try:
                if call.name == terminator.NAME:
                    if len(completion.tool_calls) > 1:
                        raise ToolError(ToolErrorCode.REFUSED, f"{terminator.NAME} must be the only call in its turn")
                    answer = terminator.accept(call.input, definition=definition, seen=seen, produced=produced)
                    journal("tool", call.name, call.input, asdict(answer), started)
                    return outcome("submit_result", answer)
                if call.name not in callable_names:
                    raise ToolError(ToolErrorCode.UNKNOWN_TOOL, f"{call.name!r} is not one of this agent's tools")
                # An answer already paid for is checked even past the deadline; a tool call is not started.
                if started >= deadline:
                    return outcome("timeout")
                result = call_tool(call.name, call.input)
                seen |= terminator.property_ids(result)
                error = False
            except ToolError as exc:
                result, error = exc.payload(), True
            journal("tool", call.name, call.input, result, started)
            results.append(ToolResultBlock(tool_use_id=call.id, content=encode(result), is_error=error))
        messages.append(Message(role="user", content=list(results)))
        usage = completion.usage
        prompt_tokens = (usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
                         + usage.output_tokens + _bytes([asdict(r) for r in results]))

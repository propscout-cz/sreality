"""The one loop every agent runs: render its input, call its model, run its tools, stop on a cap or an accepted answer."""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from api.agents import terminator
from api.agents.definition import HOSTED_CODE_PROVIDERS, Definition, DefinitionError
from api.agents.hosted_code import RunFiles, describe
from api.capabilities import registry
from api.capabilities.entry import Effect
from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.truncate import encode
from api.llm_client import LLMClient, LLMResponse
from api.providers import (
    Completion,
    HostedCode,
    HostedFile,
    Message,
    ModelPrice,
    ProviderError,
    TextBlock,
    ToolResultBlock,
    is_transient,
)

CALLED_FOR = "agent_run"
# A reply's limit covers the model's thinking too (Opus/Sonnet 5.5 always think): the ceiling is the non-streaming
# size Anthropic recommends, and a budget that buys less than the floor ends the run rather than pay for a reply
# likely to be cut off.
MAX_REPLY_TOKENS = 16_000
MIN_REPLY_TOKENS = 4_096
# A rate limit or an overload is retried inside the step, each wait inside the wall clock (PROGRAM § 3.4's numbers). The
# provider refused those before doing any work, so a retry bills nothing and the call's budget check still holds.
RETRY_DELAYS_S = (2.0, 8.0, 30.0)
# With hosted code one request can sample the model this many times on its own before it pauses (docs: stop reasons);
# whether `max_tokens` spans them the docs do not say, so a code call is budgeted as if each sample paid in full.
SERVER_LOOP_SAMPLES = 10
# The hosted tool's own description is in the prompt too; the docs publish no figure for it.
HOSTED_TOOL_PROMPT_TOKENS = 2_000

StopReason = Literal["submit_result", "max_iterations", "max_cost", "timeout", "end_turn", "max_tokens", "refusal",
                     "model_context_window_exceeded"]


@dataclass(frozen=True)
class Step:
    """One journal line, in the order the run made it: a model call (`name` = the model), a tool call, or a produced
    file fetched (`name` = its id)."""

    n: int
    kind: Literal["model", "tool", "file"]
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


def plain_answer(answer: terminator.Answer) -> dict[str, Any]:
    """An answer for a journal or a cassette: a file's facts, never its bytes."""
    return {**asdict(replace(answer, files=())), "files": [describe(f) for f in answer.files]}


def _bytes(value: Any) -> int:
    """An upper bound on the tokens `value` costs as input: a byte-level tokenizer spends at most one per byte."""
    return len(encode(value).encode())


def _reply_tokens(remaining_usd: float, prompt_tokens: int, price: ModelPrice, samples: int = 1) -> int:
    """The longest reply whose worst case fits the budget: every prompt token at the dearer of the input and
    cache-write rates (the provider may write the prefix to its cache), every reply token at the output rate, for
    each of the `samples` one call may take."""
    left = remaining_usd / samples - prompt_tokens * max(price.input_per_mtok, price.cache_write_per_mtok) / 1e6
    if left <= 0:
        return 0
    if price.output_per_mtok == 0:
        return MAX_REPLY_TOKENS
    return min(MAX_REPLY_TOKENS, math.floor(left * 1e6 / price.output_per_mtok))


def _callable(definition: Definition) -> set[str]:
    return {t for t in definition.tools if registry.ENTRIES[t].effect is not Effect.WRITE}


def tool_schemas(definition: Definition) -> list[dict[str, Any]]:
    """What this agent's model is offered: its tools it may call, then the terminator."""
    return [s for s in registry.schemas() if s["name"] in _callable(definition)] + [terminator.schema(definition)]


def _call_in_time(call: Callable[[float], LLMResponse], *, deadline: float, clock: Callable[[], float],
                  sleep: Callable[[float], None]) -> LLMResponse | None:
    """The model's reply, or None when the wall clock ran out; a failure that is not transient, or the last retry's,
    raises."""
    for delay in (*RETRY_DELAYS_S, None):
        try:
            return call(deadline - clock())
        except ProviderError as exc:
            if clock() >= deadline:
                return None
            if delay is None or not is_transient(exc):
                raise
        sleep(min(delay * random.uniform(0.75, 1.25), deadline - clock()))
        if clock() >= deadline:
            return None
    raise AssertionError("the last try returns or raises")


def run(
    definition: Definition,
    value: Mapping[str, Any],
    *,
    provider: str,
    llm: LLMClient,
    call_tool: Callable[[str, Any], dict[str, Any]],
    record: Callable[[Step], None],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    inputs: Sequence[HostedFile] = (),
) -> Outcome:
    """Run `definition` on `value`. `call_tool` is the registry call under the run's caller; `record` journals.
    `inputs` go into a code agent's container. Every file the run put at or got from the provider leaves it at the end."""
    if definition.code and provider not in HOSTED_CODE_PROVIDERS:
        raise DefinitionError(f"{definition.name} runs code, which is Claude only (ADR-0005), not {provider}")
    if inputs and not definition.code:
        raise DefinitionError(f"{definition.name} does not allow code, so it takes no input files")
    files = RunFiles(llm, provider)
    try:
        return _run(definition, value, provider=provider, llm=llm, call_tool=call_tool, record=record, clock=clock,
                    sleep=sleep, inputs=inputs, files=files)
    finally:
        files.release()


def _run(
    definition: Definition,
    value: Mapping[str, Any],
    *,
    provider: str,
    llm: LLMClient,
    call_tool: Callable[[str, Any], dict[str, Any]],
    record: Callable[[Step], None],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    inputs: Sequence[HostedFile],
    files: RunFiles,
) -> Outcome:
    definition.check_input(value)
    model = definition.models.get(provider)
    if model is None:
        raise DefinitionError(f"{definition.name} names no model for {provider}; it runs on {sorted(definition.models)}")
    price = llm.provider(provider).price_for(model)
    if price is None:
        raise DefinitionError(f"{provider} has no price for {model!r}")

    callable_names = _callable(definition)
    tools = tool_schemas(definition)
    opening = json.dumps(value, ensure_ascii=False, indent=2)
    messages = [Message(role="user", content=[TextBlock(text=opening), *files.upload(inputs)])]
    prompt_tokens = _bytes([definition.instructions, tools, opening])
    samples = 1
    container_reserve = 0.0
    if definition.code:
        prompt_tokens += HOSTED_TOOL_PROMPT_TOKENS
        samples = SERVER_LOOP_SAMPLES
        container_reserve = files.container_cost_usd(definition.limits.wall_clock_timeout_s)
    container: str | None = None
    deadline = clock() + definition.limits.wall_clock_timeout_s
    seen: set[int] = set()
    steps = 0
    calls = 0
    spent = 0.0

    def journal(kind: Literal["model", "tool", "file"], name: str, given: Any, output: Mapping[str, Any],
                started: float, cost_usd: float = 0.0, llm_call_id: int | None = None) -> None:
        nonlocal steps
        steps += 1
        record(Step(n=steps, kind=kind, name=name, input=given, output=output, cost_usd=cost_usd,
                    duration_ms=int((clock() - started) * 1000), llm_call_id=llm_call_id))

    def next_prompt(completion: Completion, results: Sequence[ToolResultBlock] = ()) -> int:
        """The next call's prompt, from what this one reported. A server-side loop may report the sum of its samples, so a
        code call's report can read many times the real prompt: the prompt plus what the reply added also bounds it."""
        usage = completion.usage
        added = _bytes([asdict(r) for r in results])
        reported = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens + usage.output_tokens + added
        return min(reported, prompt_tokens + _bytes(completion.reply) + added) if definition.code else reported

    def outcome(stop_reason: StopReason, answer: terminator.Answer | None = None) -> Outcome:
        return Outcome(stop_reason=stop_reason, answer=answer, model_calls=calls, cost_usd=round(spent, 6))

    while True:
        if calls >= definition.limits.max_iterations:
            return outcome("max_iterations")
        if clock() >= deadline:
            return outcome("timeout")
        max_tokens = _reply_tokens(definition.limits.max_cost_usd - spent - container_reserve, prompt_tokens, price, samples)
        if max_tokens < MIN_REPLY_TOKENS:
            return outcome("max_cost")

        started = clock()
        # A retry resends this same request: the paused reply a continuation carries, and the container it resumes.
        response = _call_in_time(
            lambda timeout_s: llm.call(called_for=CALLED_FOR, messages=messages, system=definition.instructions,
                                       tools=tools, model=model, provider=provider, max_tokens=max_tokens,
                                       timeout_s=timeout_s,
                                       hosted_code=HostedCode(container=container) if definition.code else None),
            deadline=deadline, clock=clock, sleep=sleep)
        if response is None:
            return outcome("timeout")
        calls += 1
        spent += response.cost_usd
        completion = response.completion
        journal("model", model, {"max_tokens": max_tokens}, {
            "text": list(completion.text_blocks),
            "tool_calls": [{"id": c.id, "name": c.name, "input": c.input} for c in completion.tool_calls],
            "stop_reason": completion.stop_reason,
            "usage": asdict(completion.usage),
            "model": completion.model,
            **({"hosted_calls": [asdict(c) for c in completion.hosted_calls], "hosted_pending": list(completion.hosted_pending),
                "container": completion.container, "file_ids": list(completion.file_ids)} if definition.code else {}),
        }, started, response.cost_usd, response.llm_call_id)
        container = completion.container or container
        for file_id in completion.file_ids:
            started = clock()
            journal("file", file_id, {"file_id": file_id}, files.collect(file_id, late=started >= deadline), started)
        # A paused server-side loop resumes on its own reply sent back as it came: another billed call, so the caps
        # above bound it like any other.
        if completion.stop_reason == "pause_turn":
            messages.append(Message(role="assistant", content=completion.reply))
            prompt_tokens = next_prompt(completion)
            continue
        # A turn's calls run only when the model ended it on them: one cut at its limit or stopped by a refusal may
        # carry a half-written call, and nothing in it runs.
        if completion.stop_reason != "tool_use" or not completion.tool_calls:
            return outcome("end_turn" if completion.stop_reason in ("stop", "tool_use") else completion.stop_reason)

        messages.append(Message(role="assistant", content=completion.reply))
        results: list[ToolResultBlock] = []
        for call in completion.tool_calls:
            started = clock()
            try:
                if call.name == terminator.NAME:
                    # Code still waiting on this turn counts: its output could not have backed the answer.
                    if len(completion.tool_calls) > 1 or completion.hosted_pending:
                        raise ToolError(ToolErrorCode.REFUSED, f"{terminator.NAME} must be the only call in its turn")
                    answer = terminator.accept(call.input, definition=definition, seen=seen, produced=files.produced)
                    journal("tool", call.name, call.input, plain_answer(answer), started)
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
        prompt_tokens = next_prompt(completion, results)

"""Claude's hosted code as an agent's run_python: the tool only where allowed, files as bytes, caps across every pause."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from api.agents import cassette, hosted_code, loop, terminator
from api.agents.definition import Definition, DefinitionError, Limits, load
from api.llm_client import LLMClient
from api.providers import (
    HostedCode,
    HostedFile,
    HostedFileInfo,
    Message,
    ModelPrice,
    OpaqueBlock,
    ProviderError,
    TextBlock,
    ToolSchema,
    compute_cost_usd,
)
from api.providers.anthropic import PRICES, AnthropicProvider, _container_usd, _normalise_stop_reason
from api.providers.gemini import GeminiProvider, _msg_to_gemini
from api.providers.openai_compatible import _msg_to_openai
from tests.api._agent_fixtures import READS, ScriptedCodeProvider, code_reply, frontmatter, install_probes, reply, write_folder
from tests.api._agent_replay import replay
from tests.api._fakes import _FakeConn, _ScriptedProvider
from tests.api._wire_stub import anthropic_reply, gemini_reply, wire_stub

OPUS = "claude-opus-5-5"
GEMINI = "gemini-3.1-pro-preview"
SUBMIT = terminator.NAME
CHEAP = ModelPrice(1.0, 1.0)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REPORT = HostedFile(name="model.xlsx", media_type=XLSX, data=b"PK\x03\x04 a fake workbook")
CODE_TOOL = {"type": "code_execution_20260521", "name": "code_execution"}


@pytest.fixture(autouse=True)
def probes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_probes(monkeypatch)


def _agent(*, code: bool = True, provider: str = "anthropic", model: str = OPUS, **overrides: Any) -> Definition:
    agent = Definition(
        name="probe_scout_v1", description="A probe agent.", instructions="Find properties and cite them.",
        tools=READS, models={provider: model}, limits=Limits(6, 20.0, 60.0),
        input_schema={"type": "object", "additionalProperties": False, "properties": {"town": {"type": "string"}}},
        output_schema=None, sha="0" * 64, code=code,
    )
    return replace(agent, **overrides)


class _Run:
    """One code run over the stand-in, keeping what each side saw."""

    def __init__(self, *replies: Any, files: Mapping[str, HostedFile | Exception] | None = None,
                 agent: Definition | None = None, inputs: tuple[HostedFile, ...] = (), price: ModelPrice = CHEAP,
                 clock: Any = None) -> None:
        self.provider = ScriptedCodeProvider(list(replies), files=dict(files or {}), prices={OPUS: price})
        self.conn = _FakeConn()
        self.steps: list[loop.Step] = []
        kwargs = {"clock": clock} if clock else {}
        self.outcome = loop.run(agent or _agent(), {"town": "Brno"}, provider="anthropic",
                                llm=LLMClient(self.conn, providers={"anthropic": self.provider}),
                                call_tool=lambda name, args: {"data": []}, record=self.steps.append,
                                inputs=inputs, **kwargs)


def _submit(*files: str) -> tuple[str, dict[str, Any]]:
    return SUBMIT, {"markdown": "Here it is.", "files": list(files)}


# --- wire shapes for one real SDK path --------------------------------------------------------------------------------

def _bash(call_id: str, *file_ids: str) -> list[dict[str, Any]]:
    return [{"type": "server_tool_use", "id": call_id, "name": "bash_code_execution", "input": {"command": "python m.py"}},
            {"type": "bash_code_execution_tool_result", "tool_use_id": call_id, "content": {
                "type": "bash_code_execution_result", "stdout": "model.xlsx\n", "stderr": "", "return_code": 0,
                "content": [{"type": "bash_code_execution_output", "file_id": f} for f in file_ids]}}]


def _tool_use(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": args, "caller": {"type": "direct"}}


def _coded(*content: dict[str, Any], stop: str = "tool_use", container: str = "cntr_1", requests: int = 1) -> dict[str, Any]:
    body = anthropic_reply(*content, stop=stop)
    body["container"] = {"id": container, "expires_at": "2026-10-31T23:00:00Z"}
    body["usage"]["server_tool_use"] = {"web_search_requests": 0, "web_fetch_requests": 0, "code_execution_requests": requests}
    return body


def _wire_run(agent: Definition, inputs: tuple[HostedFile, ...] = ()) -> loop.Outcome:
    provider = AnthropicProvider(api_key="test")
    return loop.run(agent, {"town": "Brno"}, provider="anthropic", llm=LLMClient(_FakeConn(), providers={"anthropic": provider}),
                    call_tool=lambda name, args: {"data": []}, record=lambda step: None, inputs=inputs)


# --- a code agent's call carries the tool; every other agent's does not --------------------------------------------------

def test_a_code_agents_call_carries_the_tool_and_a_produced_file_reaches_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    first = [{"type": "thinking", "thinking": "", "signature": "c2ln"}, *_bash("srvtoolu_1", "file_out_1")]
    files = {"file_out_1": {"filename": "model.xlsx", "mime_type": XLSX, "bytes": REPORT.data}}
    replies = [_coded(*first, stop="pause_turn"),
               _coded(_tool_use("toolu_1", SUBMIT, {"markdown": "Done.", "files": ["model.xlsx"]}))]
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", replies, files=files) as stub:
        outcome = _wire_run(_agent())
    opening, continued = stub.requests
    assert outcome.stop_reason == "submit_result"
    assert outcome.answer.files == (REPORT,)
    assert opening["tools"][0] == CODE_TOOL
    assert opening["tools"][-1]["name"] == SUBMIT and opening["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "container" not in opening
    # The pause goes back as it came, with its container and with no user message added.
    assert continued["container"] == "cntr_1"
    assert continued["messages"][1:] == [{"role": "assistant", "content": first}]
    assert continued["tools"] == opening["tools"]
    assert stub.deleted == ["file_out_1"]


def test_a_code_call_deferred_behind_a_client_tool_resumes_in_its_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docs, server tools: a server call beside a client call waits, unrun, for the tool_result message alone."""
    pytest.importorskip("anthropic")
    waiting = {"type": "server_tool_use", "id": "srvtoolu_1", "name": "bash_code_execution", "input": {"command": "ls"}}
    client = _tool_use("toolu_1", "probe_a", {})
    replies = [_coded(waiting, client), _coded(*_bash("srvtoolu_1")[1:], _tool_use("toolu_2", SUBMIT, {"markdown": "Done."}))]
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", replies) as stub:
        outcome = _wire_run(_agent())
    assert outcome.stop_reason == "submit_result"
    resumed = stub.requests[1]
    assert resumed["container"] == "cntr_1"
    assert resumed["messages"][1] == {"role": "assistant", "content": [waiting, client]}
    assert [b["type"] for b in resumed["messages"][2]["content"]] == ["tool_result"]


def test_an_answer_beside_code_still_waiting_is_refused_and_the_code_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    waiting = {"type": "server_tool_use", "id": "srvtoolu_1", "name": "bash_code_execution", "input": {"command": "ls"}}
    early = _tool_use("toolu_1", SUBMIT, {"markdown": "Before the code ran."})
    replies = [_coded(waiting, early), _coded(*_bash("srvtoolu_1")[1:], _tool_use("toolu_2", SUBMIT, {"markdown": "After."}))]
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", replies) as stub:
        outcome = _wire_run(_agent())
    assert outcome.answer.markdown == "After."
    assert stub.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_an_answer_after_its_code_finished_in_the_same_reply_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    replies = [_coded(*_bash("srvtoolu_1"), _tool_use("toolu_1", SUBMIT, {"markdown": "Done."}))]
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", replies) as stub:
        outcome = _wire_run(_agent())
    assert outcome.stop_reason == "submit_result" and len(stub.requests) == 1


def test_no_request_without_hosted_code_changes_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every other lane: the exact request, tools and all, with hosted code never asked for."""
    pytest.importorskip("anthropic")
    tool = ToolSchema(name="record", description="d", input_schema={"type": "object", "properties": {}})
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [anthropic_reply({"type": "text", "text": "x", "citations": None}, stop="end_turn")]) as stub:
        completion = AnthropicProvider(api_key="test").complete(
            system="s", messages=[Message(role="user", content=[TextBlock(text="go")])], tools=[tool], model=OPUS, tool_choice="record")
    assert stub.requests == [{
        "model": OPUS, "max_tokens": 4096,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
        "tool_choice": {"type": "tool", "name": "record"},
        "system": [{"type": "text", "text": "s", "cache_control": {"type": "ephemeral"}}],
        "tools": [{"name": "record", "description": "d", "input_schema": {"type": "object", "properties": {}},
                   "cache_control": {"type": "ephemeral"}}],
    }]
    assert (completion.container, completion.file_ids, completion.hosted_calls, completion.hosted_pending) == (None, (), (), ())
    assert (completion.usage.code_execution_requests, completion.usage.hosted_code_usd) == (0, 0.0)


def test_a_batch_request_never_carries_the_code_tool() -> None:
    params = AnthropicProvider(api_key="test").build_batch_request_params(
        system="s", messages=[{"role": "user", "content": "go"}], tools=[{"name": "record", "input_schema": {}}], model=OPUS)
    assert "container" not in params and "code_execution" not in json.dumps(params)


def test_a_cost_with_no_hosted_code_is_the_tokens_alone() -> None:
    usage = reply(usage=(1_234, 567)).usage
    assert compute_cost_usd(price=ModelPrice(4.0, 20.0), model=OPUS, usage=usage) == round((1_234 * 4.0 + 567 * 20.0) / 1e6, 6)


def test_a_non_code_agents_call_carries_no_tool_and_no_container(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    replies = [anthropic_reply(_tool_use("toolu_1", "probe_a", {})), anthropic_reply(_tool_use("toolu_2", SUBMIT, {"markdown": "Done."}))]
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", replies) as stub:
        _wire_run(_agent(code=False))
    for body in stub.requests:
        assert set(body) == {"model", "max_tokens", "messages", "system", "tools"}
        assert all("type" not in tool for tool in body["tools"])
        assert "code_execution" not in json.dumps(body)


def test_a_non_code_agent_on_gemini_declares_only_its_own_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("google.genai")
    provider = GeminiProvider(api_key="test")
    replies = [gemini_reply({"functionCall": {"name": SUBMIT, "args": {"markdown": "Done."}}})]
    with wire_stub(monkeypatch, "GOOGLE_GEMINI_BASE_URL", replies) as stub:
        loop.run(_agent(code=False, provider="gemini", model=GEMINI), {"town": "Brno"}, provider="gemini",
                 llm=LLMClient(_FakeConn(), providers={"gemini": provider}), call_tool=lambda n, a: {"data": []},
                 record=lambda step: None)
    assert "code_execution" not in json.dumps(stub.requests[0])
    declared = [d["name"] for t in stub.requests[0]["tools"] for d in t["functionDeclarations"]]
    assert sorted(declared) == sorted([*READS, SUBMIT])


@pytest.mark.parametrize("name", ["anthropic", "gemini", "openai", "qwen", "oss"])
def test_no_provider_is_asked_for_code_by_an_agent_that_does_not_allow_it(name: str) -> None:
    """A provider with no `hosted_code` parameter takes the call: the client forwards the argument only when set."""
    provider = _ScriptedProvider(name, [reply((SUBMIT, {"markdown": "Done."}))], prices={"m": CHEAP})
    outcome = loop.run(_agent(code=False, provider=name, model="m"), {"town": "Brno"}, provider=name,
                       llm=LLMClient(_FakeConn(), providers={name: provider}), call_tool=lambda n, a: {"data": []},
                       record=lambda step: None)
    assert outcome.stop_reason == "submit_result"
    assert sorted(t.name for t in provider.calls[0]["tools"]) == sorted([*READS, SUBMIT])


def test_a_model_calling_the_code_tool_by_hand_is_refused_when_its_agent_has_none() -> None:
    provider = _ScriptedProvider("anthropic", [reply(("code_execution", {"code": "1"})), reply((SUBMIT, {"markdown": "Done."}))],
                                 prices={OPUS: CHEAP})
    loop.run(_agent(code=False), {"town": "Brno"}, provider="anthropic",
             llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: pytest.fail("ran"),
             record=lambda step: None)
    assert "unknown_tool" in provider.calls[1]["messages"][-1].content[0].content


def test_the_answer_tool_tells_only_a_code_agent_how_a_file_reaches_the_user() -> None:
    assert "$OUTPUT_DIR" in terminator.schema(_agent())["description"]
    assert "$OUTPUT_DIR" not in terminator.schema(_agent(code=False))["description"]


# --- the definition: Claude only, refused at load ----------------------------------------------------------------------

def _load(root: Path, **overrides: Any) -> Definition:
    return load(write_folder(root, meta=frontmatter("probe_scout_v1", **overrides)))


@pytest.fixture()
def priced(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = {"anthropic": AnthropicProvider(api_key="test"), "gemini": GeminiProvider(api_key="test")}
    monkeypatch.setattr("api.dependencies.get_providers", lambda: providers)


@pytest.mark.usefixtures("priced")
def test_a_definition_allows_code_only_when_it_says_so(tmp_path: Path) -> None:
    assert _load(tmp_path).code is False
    assert _load(tmp_path / "b", code=True, preferred_model={"anthropic": OPUS}).code is True


@pytest.mark.usefixtures("priced")
@pytest.mark.parametrize("models", [{"gemini": GEMINI}, {"anthropic": OPUS, "gemini": GEMINI}])
def test_a_definition_allowing_code_on_another_model_is_refused_at_load(tmp_path: Path, models: dict[str, str]) -> None:
    with pytest.raises(DefinitionError, match="Claude only"):
        _load(tmp_path, code=True, preferred_model=models)


@pytest.mark.usefixtures("priced")
@pytest.mark.parametrize("value", ["yes", 1, []])
def test_code_is_a_plain_yes_or_no(tmp_path: Path, value: Any) -> None:
    with pytest.raises(DefinitionError, match="code must be true or false"):
        _load(tmp_path, code=value)


def test_the_loop_refuses_a_hand_built_code_agent_on_another_provider() -> None:
    provider = _ScriptedProvider("gemini", [], prices={"m": CHEAP})
    with pytest.raises(DefinitionError, match="Claude only"):
        loop.run(_agent(provider="gemini", model="m"), {"town": "Brno"}, provider="gemini",
                 llm=LLMClient(_FakeConn(), providers={"gemini": provider}), call_tool=lambda n, a: {"data": []},
                 record=lambda step: None)
    assert provider.calls == []


def test_input_files_need_an_agent_that_allows_code() -> None:
    provider = ScriptedCodeProvider([], prices={OPUS: CHEAP})
    with pytest.raises(DefinitionError, match="does not allow code"):
        loop.run(_agent(code=False), {"town": "Brno"}, provider="anthropic",
                 llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: {"data": []},
                 record=lambda step: None, inputs=(REPORT,))
    assert provider.uploaded == []


# --- pause_turn and the caps -------------------------------------------------------------------------------------------

def test_pause_turn_is_its_own_stop_reason() -> None:
    assert _normalise_stop_reason("pause_turn") == "pause_turn"


def test_a_pause_continues_with_the_same_container_and_counts_as_a_model_call() -> None:
    run = _Run(code_reply(stop="pause_turn"), code_reply(stop="pause_turn"), code_reply(_submit()))
    assert (run.outcome.stop_reason, run.outcome.model_calls) == ("submit_result", 3)
    assert [h.container for h in run.provider.hosted] == [None, "cntr_1", "cntr_1"]
    assert [len(c["messages"]) for c in run.provider.calls] == [1, 2, 3]
    assert [m.role for m in run.provider.calls[2]["messages"]] == ["user", "assistant", "assistant"]
    assert [s.kind for s in run.steps] == ["model", "model", "model", "tool"]
    assert len(run.conn.llm_calls_rows) == 3


def test_a_model_that_only_ever_pauses_stops_at_the_iteration_cap() -> None:
    limit = Limits(3, 20.0, 60.0)
    run = _Run(*[code_reply(stop="pause_turn") for _ in range(10)], agent=_agent(limits=limit))
    assert (run.outcome.stop_reason, run.outcome.model_calls) == ("max_iterations", 3)
    assert len(run.provider.calls) == 3


def test_a_model_that_only_ever_pauses_stops_at_the_cost_cap() -> None:
    dear = ModelPrice(4.0, 20.0, 0.2, 5.0)
    run = _Run(*[code_reply(stop="pause_turn", usage=(1_000, 40_000)) for _ in range(20)], price=dear,
               agent=_agent(limits=Limits(50, 3.0, 60.0)))
    assert run.outcome.stop_reason == "max_cost"
    assert run.outcome.cost_usd <= 3.0
    assert run.outcome.cost_usd == pytest.approx(sum(s.cost_usd for s in run.steps if s.kind == "model"))


def test_a_model_that_only_ever_pauses_stops_at_the_wall_clock() -> None:
    ticks = iter(range(0, 1000, 25))
    run = _Run(*[code_reply(stop="pause_turn") for _ in range(10)], agent=_agent(limits=Limits(50, 20.0, 60.0)),
               clock=lambda: float(next(ticks)))
    assert run.outcome.stop_reason == "timeout"
    assert run.outcome.model_calls < 10


def test_one_code_call_is_budgeted_for_every_sample_of_its_server_side_loop() -> None:
    """A request samples the model up to ten times on its own: a budget that buys one sample buys no code call."""
    dear = ModelPrice(4.0, 20.0, 0.2, 5.0)
    one_sample = Limits(6, 0.15, 60.0)
    plain = _Run(reply((SUBMIT, {"markdown": "Done."}), model=OPUS), price=dear, agent=_agent(code=False, limits=one_sample))
    assert plain.outcome.stop_reason == "submit_result"
    coded = _Run(code_reply(_submit()), price=dear, agent=_agent(limits=one_sample))
    assert (coded.outcome.stop_reason, coded.outcome.model_calls) == ("max_cost", 0)
    assert coded.provider.calls == []


def test_container_time_comes_out_of_the_budget_before_a_call() -> None:
    class _DearContainer(ScriptedCodeProvider):
        def container_cost_usd(self, seconds: float) -> float:
            return 1.9

    provider = _DearContainer([code_reply(_submit())], prices={OPUS: ModelPrice(4.0, 20.0, 0.2, 5.0)})
    outcome = loop.run(_agent(limits=Limits(6, 2.0, 60.0)), {"town": "Brno"}, provider="anthropic",
                       llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: {"data": []},
                       record=lambda step: None)
    assert (outcome.stop_reason, provider.calls) == ("max_cost", [])


def test_a_code_calls_worst_case_fits_what_is_left() -> None:
    dear = ModelPrice(4.0, 20.0, 0.2, 5.0)
    run = _Run(code_reply(_submit()), price=dear, agent=_agent(limits=Limits(6, 2.0, 60.0)))
    call = run.provider.calls[0]
    assert call["max_tokens"] * 20.0 / 1e6 * loop.SERVER_LOOP_SAMPLES <= 2.0
    assert call["max_tokens"] >= loop.MIN_REPLY_TOKENS


def test_a_long_server_loop_does_not_read_as_a_huge_prompt_for_the_next_call() -> None:
    """Usage sums the loop's samples; the prompt only grew by the reply, so the next call's reserve stays honest."""
    dear = ModelPrice(4.0, 20.0, 0.2, 5.0)
    run = _Run(code_reply(stop="pause_turn", usage=(100_000, 100)), code_reply(_submit()), price=dear,
               agent=_agent(limits=Limits(6, 3.0, 60.0)))
    assert run.outcome.stop_reason == "submit_result"


# --- what a code turn costs --------------------------------------------------------------------------------------------

def test_container_time_is_part_of_the_model_calls_cost() -> None:
    usage = code_reply(usage=(1_000_000, 0), hosted_usd=0.0042).usage
    assert compute_cost_usd(price=ModelPrice(4.0, 20.0), model=OPUS, usage=usage) == pytest.approx(4.0042)


def test_a_run_attributes_each_code_turn_to_its_own_model_call() -> None:
    run = _Run(code_reply(stop="pause_turn", usage=(1_000_000, 0), hosted_usd=0.01), code_reply(_submit(), usage=(0, 0), hosted_usd=0.02))
    models = [s for s in run.steps if s.kind == "model"]
    assert [s.cost_usd for s in models] == pytest.approx([1.01, 0.02])
    assert run.outcome.cost_usd == pytest.approx(1.03)
    assert [row["params"][7] for row in run.conn.llm_calls_rows] == pytest.approx([1.01, 0.02])
    assert models[0].output["usage"]["code_execution_requests"] == 1
    assert models[0].output["container"] == "cntr_1"
    assert models[0].output["hosted_calls"][0]["name"] == "bash_code_execution"


def test_a_non_code_turn_journals_as_it_always_did() -> None:
    provider = _ScriptedProvider("anthropic", [reply((SUBMIT, {"markdown": "Done."}))], prices={OPUS: CHEAP})
    steps: list[loop.Step] = []
    loop.run(_agent(code=False), {"town": "Brno"}, provider="anthropic",
             llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: {"data": []},
             record=steps.append)
    assert set(steps[0].output) == {"text", "tool_calls", "stop_reason", "usage", "model"}


@pytest.mark.parametrize(("seconds", "new", "expected"), [
    (30.0, True, 0.05 * 300 / 3600),    # a new container bills its five-minute minimum
    (600.0, True, 0.05 * 600 / 3600),
    (30.0, False, 0.05 * 30 / 3600),    # the same container goes on: only the time this call held it
])
def test_a_container_is_priced_as_the_docs_state(seconds: float, new: bool, expected: float) -> None:
    assert _container_usd(seconds, new=new) == pytest.approx(expected, abs=1e-6)


def test_a_resumed_container_bills_no_second_minimum_and_a_replaced_one_does(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    reply_body = _coded({"type": "text", "text": "x", "citations": None}, stop="end_turn")
    costs = []
    for resumed in ("cntr_1", "cntr_gone"):
        with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [reply_body]):
            costs.append(AnthropicProvider(api_key="test").complete(
                system="", messages=[Message(role="user", content=[TextBlock(text="go")])], tools=[], model=OPUS,
                hosted_code=HostedCode(container=resumed)).usage.hosted_code_usd)
    assert costs[0] < 0.0005
    assert costs[1] == pytest.approx(0.05 * 300 / 3600, abs=1e-6)


def test_the_provider_reports_a_code_calls_container_and_its_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [_coded({"type": "text", "text": "x", "citations": None}, stop="end_turn")]):
        completion = AnthropicProvider(api_key="test").complete(
            system="", messages=[Message(role="user", content=[TextBlock(text="go")])], tools=[], model=OPUS,
            hosted_code=HostedCode())
    assert completion.usage.hosted_code_usd == pytest.approx(0.05 * 300 / 3600, abs=1e-6)
    assert completion.usage.code_execution_requests == 1
    assert completion.container == "cntr_1"


def test_a_call_that_opened_no_container_bills_none(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    body = anthropic_reply({"type": "text", "text": "x", "citations": None}, stop="end_turn")
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [body]):
        completion = AnthropicProvider(api_key="test").complete(
            system="", messages=[Message(role="user", content=[TextBlock(text="go")])], tools=[], model=OPUS,
            hosted_code=HostedCode())
    assert completion.usage.hosted_code_usd == 0.0
    assert completion.container is None


def test_every_priced_claude_model_prices_a_code_call() -> None:
    assert AnthropicProvider(api_key="test").container_cost_usd(600.0) == pytest.approx(0.05 * 600 / 3600, abs=1e-6)
    assert OPUS in PRICES and AnthropicProvider(api_key="test").price_for(OPUS) is not None


# --- files coming back -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "safe"), [
    ("report.xlsx", "report.xlsx"),
    ("nájemné (1).xlsx", "nájemné (1).xlsx"),
    ("../../etc/passwd", "passwd"),
    ("/abs/olute/x.csv", "x.csv"),
    ("..\\..\\windows\\x.exe", "x.exe"),
    (".bashrc", "bashrc"),
    ("a\x00b\nc.txt", "a_b_c.txt"),
    ("x;rm -rf $HOME.csv", "x_rm -rf _HOME.csv"),
    ("..", "file"),
    (".", "file"),
    ("", "file"),
    ("   ", "file"),
    ("name. ", "name"),
])
def test_a_produced_files_name_cannot_escape(raw: str, safe: str) -> None:
    assert hosted_code.safe_name(raw) == safe


def test_a_very_long_name_keeps_its_extension() -> None:
    name = hosted_code.safe_name("x" * 400 + ".xlsx")
    assert name.endswith(".xlsx") and len(name.encode()) <= hosted_code.MAX_NAME_BYTES


@pytest.mark.parametrize(("raw", "safe"), [
    (XLSX, XLSX), ("text/csv", "text/csv"), ("TEXT/PLAIN", "text/plain"),
    ("text/html; x=1", "application/octet-stream"), ("", "application/octet-stream"), ("../x", "application/octet-stream"),
])
def test_a_produced_files_type_is_a_plain_media_type(raw: str, safe: str) -> None:
    file = HostedFile(name="a", media_type=raw, data=b"")
    run = _Run(code_reply(files=("f",), stop="pause_turn"), code_reply(_submit("a")), files={"f": file})
    assert run.outcome.answer.files[0].media_type == safe


def test_a_model_written_name_lands_sanitised_and_is_answerable_by_that_name() -> None:
    evil = HostedFile(name="../../evil.sh", media_type="text/plain", data=b"#!/bin/sh")
    run = _Run(code_reply(_submit("../../evil.sh"), files=("f",)), code_reply(_submit("evil.sh")), files={"f": evil})
    assert run.outcome.answer.files[0].name == "evil.sh"
    assert "evil.sh" in run.provider.calls[1]["messages"][-1].content[0].content


def test_a_file_this_run_did_not_produce_is_refused_and_the_refusal_names_what_it_did() -> None:
    run = _Run(code_reply(_submit("other.xlsx"), files=("f",)), code_reply(_submit("model.xlsx")), files={"f": REPORT})
    refusal = run.provider.calls[1]["messages"][-1].content[0].content
    assert "other.xlsx" in refusal and "model.xlsx" in refusal
    assert run.outcome.answer.files == (REPORT,)


def test_a_rerun_of_the_same_name_replaces_the_earlier_file() -> None:
    newer = replace(REPORT, data=b"newer")
    run = _Run(code_reply(files=("f1",), stop="pause_turn"), code_reply(_submit("model.xlsx"), files=("f2",)),
               files={"f1": REPORT, "f2": newer})
    assert run.outcome.answer.files == (newer,)
    assert run.provider.deleted == ["f1", "f2"]


def _with_bytes_failing(file_id: str, error: Exception, *replies: Any, files: dict[str, HostedFile]) -> ScriptedCodeProvider:
    provider = ScriptedCodeProvider(list(replies), files=dict(files), prices={OPUS: CHEAP})
    provider.byte_errors[file_id] = error
    loop.run(_agent(), {"town": "Brno"}, provider="anthropic", llm=LLMClient(_FakeConn(), providers={"anthropic": provider}),
             call_tool=lambda n, a: {"data": []}, record=lambda step: None)
    return provider


def test_a_rerun_that_cannot_be_fetched_never_leaves_the_older_file_answering_for_it() -> None:
    newer = replace(REPORT, data=b"newer")
    provider = _with_bytes_failing(
        "f2", ProviderError("502"),
        code_reply(files=("f1",), stop="pause_turn"),
        code_reply(_submit("model.xlsx"), files=("f2",)),
        code_reply((SUBMIT, {"markdown": "No file."})),
        files={"f1": REPORT, "f2": newer})
    refusal = provider.calls[2]["messages"][-1].content[0].content
    assert "produced no file" in refusal and "model.xlsx" in refusal


def test_a_rerun_too_large_never_leaves_the_older_file_answering_for_it() -> None:
    big = replace(REPORT, data=b"x" * (hosted_code.MAX_FILE_BYTES + 1))
    run = _Run(code_reply(files=("f1",), stop="pause_turn"), code_reply(_submit("model.xlsx"), files=("big",)),
               code_reply((SUBMIT, {"markdown": "No file."})), files={"f1": REPORT, "big": big})
    assert run.outcome.answer.files == ()
    assert "produced no file" in run.provider.calls[2]["messages"][-1].content[0].content


def test_a_file_whose_name_could_not_be_read_clears_what_the_run_held() -> None:
    """It may be a rerun of any of them, and an answer must not carry older bytes than the model thinks."""
    other = replace(REPORT, name="other.csv")
    run = _Run(code_reply(files=("f1", "f2"), stop="pause_turn"), code_reply(_submit("model.xlsx"), files=("f3",)),
               code_reply((SUBMIT, {"markdown": "No file."})),
               files={"f1": REPORT, "f2": other, "f3": ProviderError("502")})
    assert run.outcome.answer.files == ()
    assert "the files it has: []" in run.provider.calls[2]["messages"][-1].content[0].content


def test_a_failed_file_of_a_new_name_keeps_the_others() -> None:
    provider = _with_bytes_failing(
        "f2", ProviderError("502"),
        code_reply(_submit("model.xlsx"), files=("f1", "f2")),
        files={"f1": REPORT, "f2": replace(REPORT, name="other.csv")})
    assert len(provider.calls) == 1


def test_an_oversized_file_is_refused_journalled_and_still_deleted() -> None:
    big = replace(REPORT, data=b"x" * (hosted_code.MAX_FILE_BYTES + 1))
    run = _Run(code_reply(_submit("model.xlsx"), files=("big",)), code_reply((SUBMIT, {"markdown": "No file."})), files={"big": big})
    step = next(s for s in run.steps if s.kind == "file")
    assert step.output["name"] == "model.xlsx" and "over" in step.output["error"]
    assert run.outcome.answer.files == ()
    assert run.provider.deleted == ["big"]


def test_a_run_keeps_a_bounded_number_of_files() -> None:
    ids = tuple(f"f{i}" for i in range(hosted_code.MAX_RUN_FILES + 2))
    files = {i: replace(REPORT, name=f"{i}.csv") for i in ids}
    run = _Run(code_reply((SUBMIT, {"markdown": "Done."}), files=ids), files=files)
    errors = [s for s in run.steps if s.kind == "file" and "error" in s.output]
    assert len(errors) == 2
    assert sorted(run.provider.deleted) == sorted(ids)


def test_a_full_run_still_takes_a_rerun_of_a_name_it_holds() -> None:
    ids = tuple(f"f{i}" for i in range(hosted_code.MAX_RUN_FILES))
    files: dict[str, Any] = {i: replace(REPORT, name=f"{i}.csv") for i in ids}
    files["again"] = replace(REPORT, name="f0.csv", data=b"newer")
    run = _Run(code_reply((SUBMIT, {"markdown": "Done.", "files": ["f0.csv"]}), files=(*ids, "again")), files=files)
    assert [f.data for f in run.outcome.answer.files] == [b"newer"]


def test_the_journal_holds_a_files_facts_never_its_bytes() -> None:
    run = _Run(code_reply(_submit("model.xlsx"), files=("f",)), files={"f": REPORT})
    step = next(s for s in run.steps if s.kind == "file")
    assert step.output["name"] == "model.xlsx" and step.output["media_type"] == XLSX
    assert step.output["bytes"] == len(REPORT.data) and len(step.output["sha256"]) == 64
    assert "data" not in step.output


def test_a_download_that_fails_loses_no_paid_call_and_the_model_hears_of_it() -> None:
    run = _Run(code_reply(_submit("model.xlsx"), files=("f",)), code_reply((SUBMIT, {"markdown": "Without it."})),
               files={"f": ProviderError("502")})
    assert len(run.conn.llm_calls_rows) == 2
    assert "produced no file" in run.provider.calls[1]["messages"][-1].content[0].content
    assert run.outcome.answer.files == ()
    assert next(s for s in run.steps if s.kind == "file").output == {"error": "502"}


def test_past_the_deadline_a_file_is_not_fetched() -> None:
    ticks = iter([0.0, 0.0, 0.0, 0.0, 100.0] + [100.0] * 50)
    run = _Run(code_reply(_submit("model.xlsx"), files=("f",)), files={"f": ProviderError("must not fetch")},
               agent=_agent(limits=Limits(6, 20.0, 60.0)), clock=lambda: next(ticks))
    assert run.outcome.stop_reason == "timeout"
    assert "past the run's deadline" in next(s for s in run.steps if s.kind == "file").output["error"]
    assert run.provider.deleted == ["f"]


def test_the_real_sdk_refuses_a_file_past_its_bound_whatever_its_metadata_says(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    files = {"f": {"filename": "x", "mime_type": "text/plain", "bytes": b"x" * 100, "size_bytes": 1}}
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [], files=files):
        with pytest.raises(ProviderError, match="over 10 bytes"):
            AnthropicProvider(api_key="test").download_file("f", max_bytes=10)


def test_the_real_sdk_returns_a_files_bytes_name_and_type(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    files = {"f": {"filename": "model.xlsx", "mime_type": XLSX, "bytes": REPORT.data}}
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [], files=files):
        provider = AnthropicProvider(api_key="test")
        assert provider.file_info("f") == HostedFileInfo(name="model.xlsx", media_type=XLSX, size_bytes=len(REPORT.data))
        assert provider.download_file("f", max_bytes=1000) == REPORT.data


def test_the_real_sdk_turns_a_missing_file_into_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", []):
        with pytest.raises(ProviderError, match="metadata failed"):
            AnthropicProvider(api_key="test").file_info("gone")


def test_the_other_code_result_shape_yields_its_files_too(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    block = {"type": "code_execution_tool_result", "tool_use_id": "srvtoolu_1", "content": {
        "type": "code_execution_result", "stdout": "", "stderr": "", "return_code": 0,
        "content": [{"type": "code_execution_output", "file_id": "file_old"}]}}
    editor = {"type": "text_editor_code_execution_tool_result", "tool_use_id": "srvtoolu_2", "content": {
        "type": "text_editor_code_execution_view_result", "file_type": "text", "content": "not a list",
        "num_lines": 1, "start_line": 1, "total_lines": 1}}
    error = {"type": "bash_code_execution_tool_result", "tool_use_id": "srvtoolu_3", "content": {
        "type": "bash_code_execution_tool_result_error", "error_code": "unavailable"}}
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [_coded(block, editor, error, stop="end_turn")]):
        completion = AnthropicProvider(api_key="test").complete(
            system="", messages=[Message(role="user", content=[TextBlock(text="go")])], tools=[], model=OPUS,
            hosted_code=HostedCode())
    assert completion.file_ids == ("file_old",)


# --- files going in, and leaving Anthropic -------------------------------------------------------------------------------

CSV = HostedFile(name="rents.csv", media_type="text/csv", data=b"a,b\n1,2\n")


def test_an_input_goes_in_through_the_files_api_and_is_deleted_at_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    last = _coded(_tool_use("toolu_1", SUBMIT, {"markdown": "Read it."}), requests=0)
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [last]) as stub:
        _wire_run(_agent(), inputs=(CSV,))
    opening = stub.requests[0]["messages"][0]["content"]
    assert {"type": "container_upload", "file_id": "file_up_1"} in opening
    assert "rents.csv" in json.dumps(opening)
    form = stub.uploads[0]
    assert b'filename="rents.csv"' in form and b"a,b\n1,2\n" in form
    assert b'name="expires_in_seconds"' in form and str(hosted_code.UPLOAD_EXPIRY_S).encode() in form
    assert stub.deleted == ["file_up_1"]


def test_an_input_is_named_safely_before_it_leaves() -> None:
    run = _Run(code_reply(_submit()), inputs=(replace(CSV, name="../../rents.csv"),))
    assert run.provider.uploaded[0][0] == "rents.csv"
    assert run.provider.uploaded[0][3] == hosted_code.UPLOAD_EXPIRY_S


def test_files_leave_anthropic_when_a_run_stops_on_a_cap() -> None:
    run = _Run(code_reply(files=("f",), stop="pause_turn"), agent=_agent(limits=Limits(1, 20.0, 60.0)), files={"f": REPORT}, inputs=(CSV,))
    assert run.outcome.stop_reason == "max_iterations"
    assert sorted(run.provider.deleted) == ["f", "file_up_1"]


def test_files_leave_anthropic_when_a_run_raises() -> None:
    class _Dies(ScriptedCodeProvider):
        def complete(self, **kwargs: Any) -> Any:
            if len(self.hosted) == 1:
                raise ProviderError("the model is down")
            return super().complete(**kwargs)

    provider = _Dies([code_reply(files=("f",), stop="pause_turn")], files={"f": REPORT}, prices={OPUS: CHEAP})
    with pytest.raises(ProviderError, match="the model is down"):
        loop.run(_agent(), {"town": "Brno"}, provider="anthropic",
                 llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: {"data": []},
                 record=lambda step: None, inputs=(CSV,))
    assert sorted(provider.deleted) == ["f", "file_up_1"]


def test_a_failed_upload_spends_nothing_and_clears_what_went_up() -> None:
    class _FullStore(ScriptedCodeProvider):
        def upload_file(self, *, name: str, media_type: str, data: bytes, expires_in_s: int) -> str:
            if self.uploaded:
                raise ProviderError("storage limit")
            return super().upload_file(name=name, media_type=media_type, data=data, expires_in_s=expires_in_s)

    provider = _FullStore([], prices={OPUS: CHEAP})
    with pytest.raises(ProviderError, match="storage limit"):
        loop.run(_agent(), {"town": "Brno"}, provider="anthropic",
                 llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: {"data": []},
                 record=lambda step: None, inputs=(CSV, CSV))
    assert provider.calls == [] and provider.deleted == ["file_up_1"]


def test_a_delete_that_fails_never_costs_the_run_its_answer(caplog: pytest.LogCaptureFixture) -> None:
    provider = ScriptedCodeProvider([code_reply(_submit("model.xlsx"), files=("f",))], files={"f": REPORT}, prices={OPUS: CHEAP})
    provider.refuse_delete = ProviderError("503")
    outcome = loop.run(_agent(), {"town": "Brno"}, provider="anthropic",
                       llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), call_tool=lambda n, a: {"data": []},
                       record=lambda step: None)
    assert outcome.answer.files == (REPORT,)
    assert "f" in caplog.text and "503" in caplog.text


def test_other_providers_drop_an_upload_block() -> None:
    types = pytest.importorskip("google.genai.types")
    upload = OpaqueBlock(provider="anthropic", data={"type": "container_upload", "file_id": "file_1"})
    message = Message(role="user", content=[TextBlock(text="go"), upload])
    assert _msg_to_openai(message)[0]["content"] == [{"type": "text", "text": "go"}]
    assert _msg_to_gemini(message, types).model_dump(exclude_none=True)["parts"] == [{"text": "go"}]


# --- a cassette of a code run ------------------------------------------------------------------------------------------

def test_a_recorded_code_run_replays_to_the_same_outcome_and_holds_no_bytes(tmp_path: Path) -> None:
    run = _Run(code_reply(files=("f",), stop="pause_turn"), code_reply(_submit("model.xlsx")), files={"f": REPORT})
    path = tmp_path / "run.json"
    agent = _agent()
    cassette.dump(path, definition=agent, provider="anthropic", model=OPUS, price=CHEAP, value={"town": "Brno"},
                  steps=run.steps, outcome=run.outcome)
    assert "fake workbook" not in path.read_text()
    document = cassette.load(path)
    assert document["outcome"]["answer"]["files"] == [{"name": "model.xlsx", "media_type": XLSX, "bytes": len(REPORT.data)}]
    assert cassette.plain_outcome(replay(document, agent)) == document["outcome"]

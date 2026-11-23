"""A model's reply goes back to it whole and in order: proved on the wire, through the real SDK and the real loop."""

from __future__ import annotations

from typing import Any

import pytest

from api.agents import loop, terminator
from api.agents.definition import Definition, Limits
from api.llm_client import LLMClient
from api.providers import Completion, Message, OpaqueBlock, TextBlock, ToolCall, ToolSchema, ToolUseBlock, Usage
from api.providers.anthropic import AnthropicProvider, _msg_to_anthropic
from api.providers.gemini import GeminiProvider, _msg_to_gemini
from api.providers.openai_compatible import _msg_to_openai
from api.providers.oss import _promote_json_content
from tests.api._agent_fixtures import READS, install_probes
from tests.api._fakes import _FakeConn
from tests.api._wire_stub import anthropic_reply, gemini_reply, wire_stub

SUBMIT = terminator.NAME
OPUS = "claude-opus-5-5"
GEMINI = "gemini-3.1-pro-preview"


@pytest.fixture(autouse=True)
def probes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_probes(monkeypatch)


def _run(provider: Any, model: str) -> loop.Outcome:
    agent = Definition(
        name="probe_scout_v1", description="A probe agent.", instructions="Find properties and cite them.",
        tools=READS, models={provider.name: model}, limits=Limits(6, 1.0, 60.0),
        input_schema={"type": "object", "additionalProperties": False, "properties": {"town": {"type": "string"}}},
        output_schema=None, sha="0" * 64,
    )
    return loop.run(agent, {"town": "Brno"}, provider=provider.name,
                    llm=LLMClient(_FakeConn(), providers={provider.name: provider}),
                    call_tool=lambda name, args: {"data": []}, record=lambda step: None)


# --- Anthropic ---------------------------------------------------------------------------------------------------------

def _anthropic_tool_use(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": args, "caller": {"type": "direct"}}


def test_a_thinking_block_goes_back_first_and_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("anthropic")
    first = [{"type": "thinking", "thinking": "", "signature": "c2lnLTE="},
             {"type": "redacted_thinking", "data": "ZW5jcnlwdGVk"},
             _anthropic_tool_use("toolu_1", "probe_a", {"q": 1})]
    between = [{"type": "thinking", "thinking": "Summary.", "signature": "c2lnLTI="},
               {"type": "text", "text": "One more look.", "citations": None},
               {"type": "thinking", "thinking": "", "signature": "c2lnLTM="},
               _anthropic_tool_use("toolu_2", "probe_b", {})]
    last = _anthropic_tool_use("toolu_3", SUBMIT, {"markdown": "Done."})
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL",
                   [anthropic_reply(*first), anthropic_reply(*between), anthropic_reply(last)]) as stub:
        outcome = _run(AnthropicProvider(api_key="test"), OPUS)
    assert outcome.stop_reason == "submit_result"
    _, second, third = stub.requests
    assert second["messages"][1] == {"role": "assistant", "content": first}
    assert third["messages"][3] == {"role": "assistant", "content": between}
    # The signature also binds what came before it: history only ever grows, and system and tools never move.
    assert third["messages"][:3] == second["messages"]
    assert (third["system"], third["tools"]) == (second["system"], second["tools"])


def test_a_block_this_code_has_never_heard_of_goes_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """#2068's server_tool_use and code-execution results need no new path."""
    pytest.importorskip("anthropic")
    first = [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "code_execution", "input": {"code": "print(1)"}},
             {"type": "bash_code_execution_tool_result", "tool_use_id": "srvtoolu_1",
              "content": {"type": "bash_code_execution_result", "stdout": "1\n", "stderr": "", "return_code": 0, "content": []}},
             {"type": "a_block_from_next_year", "payload": {"kept": True}},
             {"type": "text", "text": "Cited.", "citations": [{"type": "char_location", "cited_text": "x", "document_index": 0,
                                                                "document_title": None, "start_char_index": 0, "end_char_index": 1}]},
             _anthropic_tool_use("toolu_1", "probe_a", {})]
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL",
                   [anthropic_reply(*first), anthropic_reply(_anthropic_tool_use("toolu_2", SUBMIT, {"markdown": "Done."}))]) as stub:
        _run(AnthropicProvider(api_key="test"), OPUS)
    assert stub.requests[1]["messages"][1]["content"] == first


@pytest.mark.parametrize("stop", ["refusal", "model_context_window_exceeded"])
def test_a_stop_the_model_did_not_choose_keeps_its_name_and_runs_nothing(
        monkeypatch: pytest.MonkeyPatch, stop: str) -> None:
    pytest.importorskip("anthropic")
    half_written = _anthropic_tool_use("toolu_1", "probe_a", {"q": 1})
    with wire_stub(monkeypatch, "ANTHROPIC_BASE_URL", [anthropic_reply(half_written, stop=stop)]) as stub:
        outcome = _run(AnthropicProvider(api_key="test"), OPUS)
    assert (outcome.stop_reason, outcome.model_calls, len(stub.requests)) == (stop, 1, 1)


# --- Gemini ------------------------------------------------------------------------------------------------------------

def test_function_call_parts_keep_their_thought_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("google.genai")
    first = [{"text": "Looking."},
             {"functionCall": {"name": "probe_a", "args": {"q": 1}}, "thoughtSignature": "c2lnLTE="},
             {"functionCall": {"name": "probe_b", "args": {}}},
             {"text": "Noted.", "thoughtSignature": "c2lnLTI="}]
    last = {"functionCall": {"name": SUBMIT, "args": {"markdown": "Done."}}, "thoughtSignature": "c2lnLTM="}
    with wire_stub(monkeypatch, "GOOGLE_GEMINI_BASE_URL", [gemini_reply(*first), gemini_reply(last)]) as stub:
        outcome = _run(GeminiProvider(api_key="test"), GEMINI)
    assert outcome.stop_reason == "submit_result"
    assert stub.requests[1]["contents"][1] == {"role": "model", "parts": first}


def test_a_gemini_reply_still_reads_as_text_and_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("google.genai")
    reply = gemini_reply({"text": "Noted.", "thoughtSignature": "c2ln"},
                         {"functionCall": {"name": "probe_a", "args": {"q": 1}}, "thoughtSignature": "c2ln"})
    with wire_stub(monkeypatch, "GOOGLE_GEMINI_BASE_URL", [reply]):
        completion = GeminiProvider(api_key="test").complete(
            system="", messages=[Message(role="user", content=[TextBlock(text="go")])], tools=[], model=GEMINI)
    assert completion.text_blocks == ["Noted."]
    assert completion.tool_calls == [ToolCall(id="probe_a", name="probe_a", input={"q": 1})]


# --- any other provider drops it ---------------------------------------------------------------------------------------

THINKING = {"type": "thinking", "thinking": "", "signature": "c2ln"}


def _turn(provider: str, written: Any) -> Message:
    own = OpaqueBlock(provider=provider, data=written)
    return Message(role="assistant", content=[
        own, TextBlock(text="Looking.", opaque=own), ToolUseBlock(id="call_1", name="probe_a", input={"q": 1}, opaque=own),
    ])


def test_openai_shaped_providers_drop_what_another_model_wrote() -> None:
    assert _msg_to_openai(_turn("anthropic", THINKING)) == [{
        "role": "assistant", "content": [{"type": "text", "text": "Looking."}],
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "probe_a", "arguments": '{"q": 1}'}}],
    }]


def test_anthropic_drops_what_gemini_wrote() -> None:
    assert _msg_to_anthropic(_turn("gemini", object())) == {"role": "assistant", "content": [
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "call_1", "name": "probe_a", "input": {"q": 1}},
    ]}


def test_gemini_drops_what_anthropic_wrote() -> None:
    types = pytest.importorskip("google.genai.types")
    content = _msg_to_gemini(_turn("anthropic", THINKING), types)
    assert content.model_dump(exclude_none=True) == {"role": "model", "parts": [
        {"text": "Looking."}, {"function_call": {"name": "probe_a", "args": {"q": 1}}},
    ]}


# --- the neutral shape -------------------------------------------------------------------------------------------------

def _completion(**kwargs: Any) -> Completion:
    return Completion(**{"text_blocks": [], "tool_calls": [], "stop_reason": "tool_use", "usage": Usage(), "model": "m",
                         **kwargs})


def test_a_provider_with_no_blocks_of_its_own_replies_with_its_text_and_calls() -> None:
    completion = _completion(text_blocks=["Looking.", ""], tool_calls=[ToolCall(id="c", name="n", input={"q": 1})])
    assert completion.reply == [TextBlock(text="Looking."), ToolUseBlock(id="c", name="n", input={"q": 1})]


def test_a_call_the_oss_provider_promoted_from_prose_is_in_its_reply() -> None:
    tool = ToolSchema(name="record", description="d", input_schema={"properties": {"a": {}}})
    promoted = _promote_json_content(_completion(text_blocks=['{"a": 1}'], stop_reason="end_turn"),
                                     tools=[tool], tool_choice="record")
    assert promoted.reply == [TextBlock(text='{"a": 1}'), ToolUseBlock(id="record-json-fallback", name="record", input={"a": 1})]

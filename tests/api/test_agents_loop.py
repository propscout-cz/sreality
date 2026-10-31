"""The one loop: it journals every call, stops on each cap with its reason, and ends only on an answer it accepts."""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from typing import Any

import pytest

from api.agents import loop, terminator
from api.agents.definition import Definition, DefinitionError, InputError, Limits
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from api.providers import ModelPrice
from tests.api._agent_fixtures import READS, WRITE, install_probes, reply
from tests.api._fakes import _FakeConn, _ScriptedProvider

MODEL = "claude-sonnet-4-5"
SUBMIT = terminator.NAME
CHEAP = ModelPrice(1.0, 1.0)


@pytest.fixture(autouse=True)
def probes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_probes(monkeypatch)


def _agent(**overrides: Any) -> Definition:
    agent = Definition(
        name="probe_scout_v1", description="A probe agent.", instructions="Find properties and cite them.",
        tools=READS, models={"anthropic": MODEL}, limits=Limits(6, 1.0, 60.0),
        input_schema={"type": "object", "additionalProperties": False, "properties": {"town": {"type": "string"}}},
        output_schema=None, sha="0" * 64,
    )
    return replace(agent, **overrides)


class _Run:
    """One loop run over scripted replies and canned tool results, keeping what each side saw."""

    def __init__(self, *replies: Any, results: dict[str, Any] | None = None, price: ModelPrice = CHEAP,
                 agent: Definition | None = None, clock: Any = None) -> None:
        self.provider = _ScriptedProvider("anthropic", list(replies), prices={MODEL: price})
        self.conn = _FakeConn()
        self.results = results or {}
        self.dispatched: list[tuple[str, Any]] = []
        self.steps: list[loop.Step] = []
        kwargs = {"clock": clock} if clock else {}
        self.outcome = loop.run(agent or _agent(), {"town": "Brno"}, provider="anthropic",
                                llm=LLMClient(self.conn, providers={"anthropic": self.provider}),
                                call_tool=self._call, record=self.steps.append, **kwargs)

    def _call(self, name: str, args: Any) -> dict[str, Any]:
        self.dispatched.append((name, args))
        result = self.results.get(name, {"data": []})
        if isinstance(result, ToolError):
            raise result
        return result

    def tool_results(self, call: int) -> list[Any]:
        """The tool results the model was sent with its `call`-th request (1-based)."""
        return list(self.provider.calls[call - 1]["messages"][-1].content)


# --- the acceptance script ---------------------------------------------------------------------------------------------

def test_a_three_tool_script_ends_on_an_accepted_answer_with_one_step_per_call() -> None:
    run = _Run(
        reply(("probe_a", {"q": 1})),
        reply(("probe_b", {}), ("probe_c", {"q": 2})),
        reply((SUBMIT, {"markdown": "Two finds.", "cited_property_ids": [7, 8]})),
        results={"probe_b": {"data": [{"property_id": 7, "listing_id": 70}]},
                 "probe_c": {"data": {"rows": [{"property_id": 8}]}, "metadata": {"filters_used": {"property_id": 9}}}},
    )
    assert run.outcome.stop_reason == "submit_result"
    assert run.outcome.answer == terminator.Answer("Two finds.", (7, 8), (), (), None)
    assert [(s.n, s.kind, s.name) for s in run.steps] == [
        (1, "model", MODEL), (2, "tool", "probe_a"), (3, "model", MODEL), (4, "tool", "probe_b"),
        (5, "tool", "probe_c"), (6, "model", MODEL), (7, "tool", SUBMIT)]
    assert run.dispatched == [("probe_a", {"q": 1}), ("probe_b", {}), ("probe_c", {"q": 2})]
    assert run.outcome.model_calls == 3
    assert [t.name for t in run.provider.calls[0]["tools"]] == [*READS, SUBMIT]
    assert run.provider.calls[0]["system"] == "Find properties and cite them."
    assert run.provider.calls[0]["messages"][0].content[0].text == json.dumps({"town": "Brno"}, indent=2)
    assert [row["params"][0] for row in run.conn.llm_calls_rows] == ["agent_run"] * 3
    assert run.outcome.cost_usd == pytest.approx(sum(s.cost_usd for s in run.steps)) and run.outcome.cost_usd > 0


def test_every_tool_result_goes_back_to_the_model_as_the_registry_shaped_it() -> None:
    refusal = ToolError(ToolErrorCode.INVALID_ARGUMENTS, "q must be positive")
    run = _Run(reply(("probe_a", {"q": -1}), ("probe_b", {})), reply(),
               results={"probe_a": refusal, "probe_b": {"data": [1]}})
    sent = run.tool_results(2)
    assert [(r.is_error, json.loads(r.content)) for r in sent] == [(True, refusal.payload()), (False, {"data": [1]})]


def test_a_tool_step_keeps_what_the_model_asked_and_what_it_was_sent() -> None:
    """The run's own record of every read (#2063: broker contacts are recorded on the run), refusals included."""
    refusal = ToolError(ToolErrorCode.INVALID_ARGUMENTS, "q must be positive")
    brokers = {"data": [{"property_id": 7, "broker": {"name": "Jana", "phone": "+420 600 000 000"}}]}
    run = _Run(reply(("probe_a", {"q": -1}), ("probe_b", {"q": 7}), ("probe_x", {})), reply(),
               results={"probe_a": refusal, "probe_b": brokers})
    assert [(s.name, s.input, s.output) for s in run.steps if s.kind == "tool"] == [
        ("probe_a", {"q": -1}, refusal.payload()),
        ("probe_b", {"q": 7}, brokers),
        ("probe_x", {}, ToolError(ToolErrorCode.UNKNOWN_TOOL, "'probe_x' is not one of this agent's tools").payload()),
    ]


# --- the caps --------------------------------------------------------------------------------------------------------

def test_the_iteration_cap_counts_model_calls() -> None:
    run = _Run(*(reply(("probe_a", {})) for _ in range(5)), agent=_agent(limits=Limits(2, 1.0, 60.0)))
    assert (run.outcome.stop_reason, run.outcome.model_calls, len(run.provider.calls)) == ("max_iterations", 2, 2)


def test_the_wall_clock_stops_a_run_between_calls_even_mid_turn() -> None:
    ticks = itertools.count(0, 10)
    run = _Run(reply(("probe_a", {}), ("probe_b", {})), agent=_agent(limits=Limits(6, 1.0, 25.0)),
               clock=lambda: next(ticks))
    assert run.outcome.stop_reason == "timeout"
    assert run.dispatched == []


def test_the_cost_cap_bounds_each_reply_so_its_worst_case_fits_what_is_left() -> None:
    price = ModelPrice(10.0, 20.0)
    run = _Run(reply(("probe_a", {}), usage=(1_000, 8_000)), reply(("probe_a", {})),
               price=price, agent=_agent(limits=Limits(6, 0.2, 60.0)))
    assert (run.outcome.stop_reason, run.outcome.model_calls) == ("max_cost", 1)
    granted = run.provider.calls[0]["max_tokens"]
    assert loop.MIN_REPLY_TOKENS <= granted < loop.MAX_REPLY_TOKENS
    assert run.outcome.cost_usd <= 0.2


def test_a_budget_that_buys_no_full_reply_spends_nothing() -> None:
    run = _Run(reply(("probe_a", {})), price=ModelPrice(10.0, 20.0), agent=_agent(limits=Limits(6, 0.05, 60.0)))
    assert (run.outcome.stop_reason, run.outcome.model_calls, run.provider.calls) == ("max_cost", 0, [])


def test_the_reply_bound_prices_the_prompt_at_its_dearest_rate() -> None:
    assert loop._reply_tokens(0.01, 0, ModelPrice(1.0, 10.0)) == 1_000
    assert loop._reply_tokens(0.01, 1_000, ModelPrice(1.0, 10.0, 0.1, 5.0)) == 500
    assert loop._reply_tokens(1.0, 0, ModelPrice(1.0, 1.0)) == loop.MAX_REPLY_TOKENS
    assert loop._reply_tokens(0.01, 0, ModelPrice(0.0, 0.0)) == loop.MAX_REPLY_TOKENS
    assert loop._reply_tokens(0.001, 10_000, ModelPrice(1.0, 1.0)) == 0


def test_a_reply_cut_at_its_limit_runs_nothing() -> None:
    run = _Run(reply(("probe_a", {}), stop="max_tokens"))
    assert run.outcome.stop_reason == "max_tokens" and run.dispatched == []


@pytest.mark.parametrize("stop", ["end_turn", "refusal", "model_context_window_exceeded"])
def test_a_turn_that_did_not_end_on_its_calls_runs_nothing(stop: str) -> None:
    """A refusal or a context-window stop can sit over a half-written call; the run ends on the stop's own name."""
    run = _Run(reply(("probe_a", {}), stop=stop))
    assert run.outcome.stop_reason == stop and run.dispatched == []


def test_a_stop_sequence_and_a_tool_turn_with_no_call_read_as_an_ended_turn() -> None:
    assert _Run(reply(text="Done.", stop="stop")).outcome.stop_reason == "end_turn"
    assert _Run(reply(text="Done.", stop="tool_use")).outcome.stop_reason == "end_turn"


def test_an_answer_that_arrives_after_the_deadline_is_still_checked() -> None:
    ticks = itertools.count(0, 10)
    run = _Run(reply((SUBMIT, {"markdown": "Late but whole."})), agent=_agent(limits=Limits(6, 1.0, 25.0)),
               clock=lambda: next(ticks))
    assert (run.outcome.stop_reason, run.outcome.answer.markdown) == ("submit_result", "Late but whole.")


def test_a_reply_without_a_call_ends_the_run_without_an_answer() -> None:
    run = _Run(reply(text="I think the market is fine."))
    assert (run.outcome.stop_reason, run.outcome.answer) == ("end_turn", None)


# --- what the model may call ------------------------------------------------------------------------------------------

def test_a_tool_the_agent_does_not_list_and_a_write_tool_are_refused_without_running() -> None:
    run = _Run(reply(("probe_c", {}), (WRITE, {"property_id": 7, "text": "hi"})), reply(),
               agent=_agent(tools=("probe_a", WRITE)))
    assert run.dispatched == []
    assert [json.loads(r.content)["error"]["code"] for r in run.tool_results(2)] == ["unknown_tool"] * 2
    assert [t.name for t in run.provider.calls[0]["tools"]] == ["probe_a", SUBMIT]


def test_the_input_and_the_provider_are_checked_before_any_spend() -> None:
    with pytest.raises(InputError):
        loop.run(_agent(), {"town": 5}, provider="anthropic", llm=LLMClient(_FakeConn(), providers={}),
                 call_tool=lambda n, a: {}, record=lambda s: None)
    with pytest.raises(DefinitionError, match="names no model for gemini"):
        loop.run(_agent(), {"town": "Brno"}, provider="gemini", llm=LLMClient(_FakeConn(), providers={}),
                 call_tool=lambda n, a: {}, record=lambda s: None)


# --- the answer -------------------------------------------------------------------------------------------------------

def test_a_citation_no_tool_returned_is_refused_and_the_model_may_fix_it() -> None:
    run = _Run(
        reply(("probe_c", {})),
        reply((SUBMIT, {"markdown": "Found 9.", "cited_property_ids": [9]})),
        reply((SUBMIT, {"markdown": "Found 8.", "cited_property_ids": [8]})),
        results={"probe_c": {"data": [{"property_id": 8}], "metadata": {"filters_used": {"property_id": 9}}}},
    )
    refusal = json.loads(run.tool_results(3)[0].content)["error"]
    assert (refusal["code"], "property [9]" in refusal["message"]) == ("refused", True)
    assert (run.outcome.stop_reason, run.outcome.answer.cited_property_ids) == ("submit_result", (8,))


def test_an_answer_must_be_the_only_call_in_its_turn() -> None:
    run = _Run(reply(("probe_a", {}), (SUBMIT, {"markdown": "Done."})), reply((SUBMIT, {"markdown": "Done."})))
    assert "only call in its turn" in json.loads(run.tool_results(2)[1].content)["error"]["message"]
    assert run.outcome.stop_reason == "submit_result" and run.outcome.model_calls == 2


def _accept(raw: Any, agent: Definition | None = None, seen: set[int] = frozenset({7})) -> terminator.Answer:
    return terminator.accept(raw, definition=agent or _agent(), seen=seen, produced=set())


@pytest.mark.parametrize(("raw", "code", "match"), [
    ({"markdown": ""}, "invalid_arguments", "markdown"),
    ({"markdown": "x", "verdict": "fair"}, "invalid_arguments", "verdict"),
    ({"markdown": "x", "files": ["model.xlsx"]}, "refused", "produced no file"),
    ({"markdown": "x", "proposed_actions": [{"entry": "nope", "args": {}}]}, "refused", "may propose"),
    ({"markdown": "x", "proposed_actions": [{"entry": "probe_a", "args": {}}]}, "refused", "may propose"),
    ({"markdown": "x", "output": {"a": 1}}, "invalid_arguments", "declares no output"),
    ("done", "invalid_arguments", "takes an object"),
])
def test_an_answer_is_refused_with_what_to_fix(raw: Any, code: str, match: str) -> None:
    with pytest.raises(ToolError, match=match) as info:
        _accept(raw)
    assert info.value.code == code


def test_a_proposal_names_a_write_tool_the_agent_lists_and_passes_its_schema() -> None:
    agent = _agent(tools=(*READS, WRITE))
    with pytest.raises(ToolError, match=r"proposed_actions\[0\]: invalid arguments: text") as info:
        _accept({"markdown": "x", "proposed_actions": [{"entry": WRITE, "args": {"property_id": 7}}]}, agent)
    assert info.value.code == "invalid_arguments"
    note = {"entry": WRITE, "args": {"property_id": 7, "text": "Call the broker."}}
    assert _accept({"markdown": "x", "proposed_actions": [note]}, agent).proposed_actions == (note,)


def test_a_declared_output_is_required_and_checked() -> None:
    agent = _agent(output_schema={"type": "object", "additionalProperties": False, "required": ["verdict"],
                                  "properties": {"verdict": {"enum": ["under", "fair", "over"]}}})
    with pytest.raises(ToolError, match="invalid output: output: None is not of type 'object'"):
        _accept({"markdown": "x"}, agent)
    with pytest.raises(ToolError, match="output.verdict: 'cheap' is not one of"):
        _accept({"markdown": "x", "output": {"verdict": "cheap"}}, agent)
    assert _accept({"markdown": "x", "output": {"verdict": "fair"}}, agent).output == {"verdict": "fair"}


def test_only_a_property_id_under_data_counts_as_returned() -> None:
    result = {"data": {"rows": [{"property_id": 1, "nested": [{"property_id": 2}]}], "property_id": True,
                       "listing_id": 3}, "metadata": {"property_id": 4}}
    assert terminator.property_ids(result) == {1, 2}

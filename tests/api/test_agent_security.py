"""The agent security suite's database-free half (#2074, MVP § 3's M1 exit, PROGRAM § 3.9's E0). What a green run means, and
what it does not: every model in this suite is SCRIPTED. It does not test whether a model resists an injection; it assumes
the worst, a model that obeys the injected text, and tests that the server's rails hold whatever that model does: a proposal
outside the run's scope, a citation no tool returned, Valuo past 3, an unlisted tool, a model-supplied account id and spending
past a cap are each refused and recorded. Whether a real model falls for an ad is #2075's live eval script, not this suite.
The other half, `tests/test_agent_security_live.py`, runs the twenty ad texts through the real executor, the real dossier read
and the real diary on the replayed schema; `CONTRACT` below states the whole contract, by reference where another test proves
a line."""

from __future__ import annotations

import ast
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import BaseModel

from api.agents import loop, terminator
from api.agents.definition import SKILLS_DIR, Definition, Limits, load_all
from api.capabilities import registry
from api.capabilities.entry import Effect, Entry, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.schema import portable
from api.filter_args import Args
from api.llm_client import LLMClient
from api.providers.anthropic import AnthropicProvider
from api.providers.gemini import GeminiProvider
from tests.api._adversarial_ads import ADS, INVENTED_PROPERTY, AdText
from tests.api._agent_fixtures import READS, WRITE, install_probes
from tests.api._fakes import _FakeConn
from tests.api._wire_stub import anthropic_reply, gemini_reply, wire_stub
from tests.api.test_capabilities_census import _identity_fields, _walk

ROOT = Path(__file__).resolve().parents[2]
MODEL = "claude-opus-5-5"
GEMINI = "gemini-3.5-flash"
READ = READS[0]
ADD = "probe_collection_add"
NOTE = WRITE
SUBMIT = terminator.NAME
SYSTEM = "Find properties and cite them."

RUNS_LIVE = "tests/test_agent_runs_live.py"
PROPOSALS_LIVE = "tests/test_agent_proposals_live.py"
SECURITY_LIVE = "tests/test_agent_security_live.py"
CENSUS = "tests/api/test_capabilities_census.py"

# The whole contract: each line, and the tests that prove it. A test renamed or removed fails below.
CONTRACT: dict[str, list[tuple[str, str]]] = {
    "a proposal writes once, only on the user's click, only inside the run's own scope, and Undo removes exactly that": [
        (PROPOSALS_LIVE, "test_a_double_apply_writes_once_and_undo_removes_exactly_what_it_added"),
        (PROPOSALS_LIVE, "test_two_concurrent_applies_write_once"),
        (PROPOSALS_LIVE, "test_another_accounts_token_gets_404_and_writes_nothing"),
        (PROPOSALS_LIVE, "test_the_marker_is_no_browsers_to_write_and_only_the_pools_login_may_call_it"),
        ("tests/api/test_agent_security.py", "test_a_proposal_into_a_collection_the_run_did_not_create_is_refused"),
        ("tests/api/test_agent_security.py", "test_the_only_door_to_a_write_is_the_apply_and_undo_routes"),
    ],
    "forged claims see zero rows": [
        (RUNS_LIVE, "test_a_claim_naming_an_account_the_user_is_not_in_reads_nothing"),
        (RUNS_LIVE, "test_only_a_top_level_claim_narrows_so_a_users_own_metadata_cannot"),
        (RUNS_LIVE, "test_an_empty_claim_fails_loudly_rather_than_reading_as_absent"),
        (SECURITY_LIVE, "test_a_run_whose_principal_left_the_account_reads_nothing_and_spends_nothing"),
    ],
    "a cross-account id is refused": [
        (RUNS_LIVE, "test_a_principal_cannot_submit_on_an_account_they_are_not_in"),
        (RUNS_LIVE, "test_cancel_reaches_only_this_accounts_runs_and_leaves_an_ended_run_as_it_is"),
        (RUNS_LIVE, "test_another_accounts_share_cannot_spend_its_cap_or_reach_its_ledger"),
        (RUNS_LIVE, "test_a_run_for_account_a_reads_as_a_and_never_bs_rows"),
        ("tests/test_capabilities_live.py", "test_the_users_own_lenses_are_judged_under_their_login"),
        (SECURITY_LIVE, "test_an_obedient_model_stays_inside_the_rails"),
    ],
    "an admin principal's run has no admin powers": [
        (RUNS_LIVE, "test_an_admin_principals_run_has_no_admin_power"),
        (RUNS_LIVE, "test_an_admin_principals_run_is_no_admin_when_it_runs"),
    ],
    "a run acts for no other account": [
        (RUNS_LIVE, "test_acting_for_another_account_is_refused_even_with_an_edit_share"),
        (RUNS_LIVE, "test_a_two_membership_users_run_on_one_account_reads_that_account_only"),
    ],
    "no identity field in any input schema": [
        (CENSUS, "test_no_input_schema_names_whose_data_it_is"),
        ("tests/api/test_valuo_tool.py", "test_no_model_argument_can_name_an_account_or_widen_a_cap"),
        ("tests/api/test_agent_security.py", "test_no_field_of_the_terminator_names_an_identity"),
        ("tests/api/test_agent_security.py", "test_no_agent_folder_takes_an_identity_as_input_or_declares_one_as_output"),
    ],
    "twenty injected ads: no out-of-scope proposal, no cap breached, every denial in the diary": [
        (SECURITY_LIVE, "test_an_obedient_model_stays_inside_the_rails"),
        ("tests/api/test_agent_security.py", "test_the_twenty_texts_are_twenty_and_cover_the_tickets_three"),
    ],
    "ad text reaches a model only as tool output": [
        (SECURITY_LIVE, "test_an_obedient_model_stays_inside_the_rails"),
        ("tests/api/test_agent_security.py", "test_on_the_wire_ad_text_is_a_tool_result_and_never_the_system_prompt"),
    ],
    "caps hold": [
        (RUNS_LIVE, "test_a_fourth_valuo_call_is_refused_and_each_of_three_is_on_the_run_and_the_ledger"),
        (RUNS_LIVE, "test_a_restarted_run_keeps_counting_what_it_paid_for"),
        (RUNS_LIVE, "test_two_runs_of_one_account_cannot_both_take_the_last_slot"),
        (RUNS_LIVE, "test_a_worker_counts_the_ten_per_run_and_leaks_nothing_into_the_next"),
        (RUNS_LIVE, "test_a_ceiling_of_a_cent_parks_new_claims_and_raising_it_frees_them"),
        (RUNS_LIVE, "test_a_retry_spends_what_the_runs_cap_has_left_not_the_whole_cap_again"),
        ("tests/api/test_agents_loop.py", "test_the_cost_cap_bounds_each_reply_so_its_worst_case_fits_what_is_left"),
    ],
    "cited ids, files and proposals come from this run": [
        ("tests/api/test_agents_loop.py", "test_a_citation_no_tool_returned_is_refused_and_the_model_may_fix_it"),
        ("tests/api/test_agents_loop.py", "test_a_tool_the_agent_does_not_list_and_a_write_tool_are_refused_without_running"),
        ("tests/api/test_agents_loop.py", "test_an_answer_must_be_the_only_call_in_its_turn"),
        ("tests/api/test_agents_loop.py", "test_an_answer_is_refused_with_what_to_fix"),
        ("tests/api/test_agent_security.py", "test_a_proposal_on_a_property_no_tool_returned_is_refused"),
        ("tests/api/test_agent_security.py", "test_a_declared_output_cannot_name_a_property_no_tool_returned"),
    ],
    "every recorded run replays": [
        ("tests/api/test_agent_cassettes.py", "test_every_recorded_cassette_replays"),
        ("tests/api/test_agent_cassettes.py", "test_every_agent_has_its_floor_of_recordings"),
    ],
}


def _test_names(path: str) -> set[str]:
    return {node.name for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8")))
            if isinstance(node, ast.FunctionDef)}


def test_every_line_of_the_contract_names_tests_that_exist() -> None:
    for line, tests in CONTRACT.items():
        for path, name in tests:
            assert name in _test_names(path), f"{line}: {path} no longer holds {name}"


# --- the twenty texts -------------------------------------------------------------------------------------------------

def test_the_twenty_texts_are_twenty_and_cover_the_tickets_three() -> None:
    assert len(ADS) == 20 and len({ad.key for ad in ADS}) == 20 and len({ad.text for ad in ADS}) == 20
    assert Counter(ad.lang for ad in ADS) == {"cs": 10, "en": 10}
    themes = " / ".join(ad.theme for ad in ADS if "(ticket" in ad.theme)
    for asked in ("add everything to my pipeline", "call Valuo", "tag property X of account B"):
        assert asked in themes
    for ad in ADS:
        assert ad.marker in ad.text, ad.key
        assert not set(ad.marker) & set('"\\\n'), ad.key


# --- the tool-result path, on the wire ---------------------------------------------------------------------------------

class _Add(Args):
    property_ids: list[int]
    collection_id: int | None = None


@pytest.fixture()
def probes(monkeypatch: pytest.MonkeyPatch) -> None:
    install_probes(monkeypatch)
    add = Entry(ADD, "Add properties to a collection.", _Add, registry.ENTRIES[NOTE].handler, Tenancy.TENANT, Effect.WRITE)
    monkeypatch.setattr(registry, "ENTRIES", MappingProxyType({**registry.ENTRIES, ADD: add}))


def _agent(**overrides: Any) -> Definition:
    agent = Definition(name="probe_scout_v1", description="A probe agent.", instructions=SYSTEM, tools=(READ, ADD, NOTE),
                       models={"anthropic": MODEL, "gemini": GEMINI}, limits=Limits(6, 1.0, 60.0),
                       input_schema={"type": "object", "additionalProperties": False,
                                     "properties": {"town": {"type": "string"}}}, output_schema=None, sha="0" * 64)
    return replace(agent, **overrides)


def _anthropic_call(name: str, call_id: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": args}


def _on_the_wire(monkeypatch: pytest.MonkeyPatch, provider: str, ad: AdText) -> list[dict[str, Any]]:
    """A real SDK serialises what the loop sends: the requests as a model's provider receives them."""
    result = {"data": [{"property_id": 7, "description": ad.text}]}
    answer = {"markdown": "Hotovo.", "cited_property_ids": [7]}
    if provider == "anthropic":
        pytest.importorskip("anthropic")
        client, env = AnthropicProvider(api_key="test"), "ANTHROPIC_BASE_URL"
        replies = [anthropic_reply(_anthropic_call(READ, "toolu_1", {})),
                   anthropic_reply(_anthropic_call(SUBMIT, "toolu_2", answer))]
    else:
        pytest.importorskip("google.genai")
        client, env = GeminiProvider(api_key="test"), "GOOGLE_GEMINI_BASE_URL"
        replies = [gemini_reply({"functionCall": {"name": READ, "args": {}}}),
                   gemini_reply({"functionCall": {"name": SUBMIT, "args": answer}})]
    with wire_stub(monkeypatch, env, replies) as stub:
        outcome = loop.run(_agent(), {"town": "Brno"}, provider=provider,
                           llm=LLMClient(_FakeConn(), providers={provider: client}),
                           call_tool=lambda name, args: result, record=lambda step: None)
    assert outcome.stop_reason == "submit_result"
    return stub.requests


def _strip_tool_results(node: Any) -> Any:
    """A request with every tool result taken out, in either provider's shape: what is left must hold no ad text."""
    if isinstance(node, list):
        return [_strip_tool_results(n) for n in node]
    if isinstance(node, dict):
        if node.get("type") == "tool_result" or "functionResponse" in node:
            return None
        return {k: _strip_tool_results(v) for k, v in node.items()}
    return node


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
@pytest.mark.parametrize("ad", [next(a for a in ADS if a.key == key) for key in ("pipeline_cs", "tool_result_en")],
                         ids=lambda ad: ad.key)
def test_on_the_wire_ad_text_is_a_tool_result_and_never_the_system_prompt(
        probes: None, monkeypatch: pytest.MonkeyPatch, provider: str, ad: AdText) -> None:
    opening, after = _on_the_wire(monkeypatch, provider, ad)
    assert ad.marker not in json.dumps(opening, ensure_ascii=False), "nothing of it before the tool ran"
    assert ad.marker in json.dumps(after, ensure_ascii=False), "the model did read it"
    assert ad.marker not in json.dumps(_strip_tool_results(after), ensure_ascii=False), "and only in a tool result"
    system = after["system"] if provider == "anthropic" else after["systemInstruction"]
    assert SYSTEM in json.dumps(system, ensure_ascii=False)


# --- what the terminator refuses ---------------------------------------------------------------------------------------

def _accept(raw: Any, seen: set[int] = frozenset({7})) -> terminator.Answer:
    return terminator.accept(raw, definition=_agent(), seen=seen, produced={})


def _proposing(entry: str, **args: Any) -> dict[str, Any]:
    return {"markdown": "Hotovo.", "proposed_actions": [{"entry": entry, "args": args}]}


@pytest.mark.usefixtures("probes")
@pytest.mark.parametrize(("entry", "args"), [
    (NOTE, {"property_id": INVENTED_PROPERTY, "text": "koupit"}),
    (NOTE, {"property_id": str(INVENTED_PROPERTY), "text": "koupit"}),
    (ADD, {"property_ids": [7, INVENTED_PROPERTY]}),
], ids=["one", "one_as_a_string", "one_of_a_list"])
def test_a_proposal_on_a_property_no_tool_returned_is_refused(entry: str, args: dict[str, Any]) -> None:
    """The citation rule, for what a proposal would write to: an id only an ad's text named reaches no proposal."""
    with pytest.raises(ToolError, match=rf"proposed_actions\[0\]: .*{INVENTED_PROPERTY}") as refused:
        _accept(_proposing(entry, **args))
    assert refused.value.code is ToolErrorCode.REFUSED


@pytest.mark.usefixtures("probes")
def test_a_proposal_on_a_property_a_tool_returned_is_accepted() -> None:
    """The control: the rail refuses what no tool returned, not every proposal."""
    assert _accept(_proposing(ADD, property_ids=[7])).proposed_actions == ({"entry": ADD, "args": {"property_ids": [7]}},)
    assert _accept(_proposing(NOTE, property_id=7, text="Volat.")).proposed_actions[0]["entry"] == NOTE


@pytest.mark.usefixtures("probes")
@pytest.mark.parametrize("smuggled", [{"account_id": "00000000-0000-0000-0000-00000000000b"}, {"act_as": "b"}, {"user_id": 1}])
def test_a_proposal_cannot_name_whose_data_it_writes(smuggled: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match=next(iter(smuggled))) as refused:
        _accept(_proposing(ADD, property_ids=[7], **smuggled))
    assert refused.value.code is ToolErrorCode.INVALID_ARGUMENTS


def _shortlist(id_type: str) -> dict[str, Any]:
    row = {"type": "object", "additionalProperties": False, "required": ["property_id"],
           "properties": {"property_id": {"type": id_type}}}
    return {"type": "object", "additionalProperties": False, "required": ["shortlist"],
            "properties": {"shortlist": {"type": "array", "items": row}}}


@pytest.mark.usefixtures("probes")
@pytest.mark.parametrize(("id_type", "named"), [
    ("integer", INVENTED_PROPERTY),
    ("integer", float(INVENTED_PROPERTY)),  # JSON Schema's integer takes 987654321.0
    ("string", str(INVENTED_PROPERTY)),
    ("string", "7"),  # a text is no id a tool returned, whatever it spells
], ids=["int", "a_float_the_schema_calls_an_integer", "text", "text_spelling_a_returned_id"])
def test_a_declared_output_cannot_name_a_property_no_tool_returned(id_type: str, named: Any) -> None:
    """A shortlist is the answer's structured half (#2069, spec story 2): the citation rule holds there too."""
    agent = _agent(output_schema=_shortlist(id_type))
    with pytest.raises(ToolError, match="output: ") as refused:
        terminator.accept({"markdown": "Hotovo.", "output": {"shortlist": [{"property_id": named}]}},
                          definition=agent, seen={7}, produced={})
    assert refused.value.code is ToolErrorCode.REFUSED


@pytest.mark.usefixtures("probes")
def test_a_declared_output_may_name_what_a_tool_returned() -> None:
    returned = {"markdown": "Hotovo.", "output": {"shortlist": [{"property_id": 7}]}}
    agent = _agent(output_schema=_shortlist("integer"))
    assert terminator.accept(returned, definition=agent, seen={7}, produced={}).output == returned["output"]


# A gap nothing owes a fix for yet, kept visible: the answer's text is free, so an obedient model may write an invented property,
# or any link, in it. The rails hold the structured fields; #2071's page must draw property links only from the cited ids.
@pytest.mark.usefixtures("probes")
@pytest.mark.xfail(strict=True, raises=pytest.fail.Exception,
                   reason="no rail reads markdown: an id an ad named can be written there, as the answer's claim")
def test_the_answers_text_cannot_name_a_property_no_tool_returned() -> None:
    with pytest.raises(ToolError):
        _accept({"markdown": f"Nejlepší je nemovitost {INVENTED_PROPERTY}.", "cited_property_ids": [7]})


# A proposal writes only into what its run creates (#2072): a new collection holds its own members, and no write entry takes the
# id of a collection, tag or stage, so a foreign one has nowhere to go. The census below holds every entry to that.
def _real_agent() -> Definition:
    return _agent(tools=tuple(e.name for e in registry.ENTRIES.values() if e.effect is Effect.WRITE))


_IN_SCOPE = {
    "create_collection": {"name": "Výběr", "property_ids": [7]},
    "add_note": {"property_id": 7, "body": "Zavolat"},
    "add_pipeline_card": {"property_id": 7},
    "create_watchdog": {"name": "Brno", "filters": {"category_type": "prodej"}},
}


def test_the_write_entries_are_the_ones_this_suite_holds_to_scope() -> None:
    assert set(_IN_SCOPE) == {e.name for e in registry.ENTRIES.values() if e.effect is Effect.WRITE}


@pytest.mark.parametrize("entry", sorted(_IN_SCOPE))
@pytest.mark.parametrize("foreign", ["collection_id", "tag_id", "stage_id", "note_id", "subscription_id"])
def test_a_proposal_into_a_collection_the_run_did_not_create_is_refused(entry: str, foreign: str) -> None:
    def submit(args: dict[str, Any]) -> terminator.Answer:
        return terminator.accept(_proposing(entry, **args), definition=_real_agent(), seen=frozenset({7}), produced={})

    assert submit(_IN_SCOPE[entry]).proposed_actions == ({"entry": entry, "args": _IN_SCOPE[entry]},), "the positive control"
    with pytest.raises(ToolError) as refused:
        submit({**_IN_SCOPE[entry], foreign: 4242})
    assert refused.value.code is ToolErrorCode.INVALID_ARGUMENTS


def test_no_write_entry_takes_an_id_but_a_propertys() -> None:
    """Nothing here names a row the run did not create: a filter's `category_sub_cb` is a vocabulary, and no id ends in `_id`."""
    for entry in registry.ENTRIES.values():
        if entry.effect is Effect.WRITE:
            fields = {d.rsplit(".", 1)[-1].removesuffix("[]") for d, _ in _walk(entry.input_schema)}
            ids = {f for f in fields if f.endswith(("_id", "_ids"))}
            assert ids <= terminator.PROPERTY_ARGS, entry.name


def test_a_watchdog_proposal_cannot_scope_itself_by_the_users_tags_collections_or_stages() -> None:
    fields = {d.rsplit(".", 1)[-1].removesuffix("[]") for d, _ in _walk(registry.ENTRIES["create_watchdog"].input_schema)}
    assert not fields & {"tags", "collections", "pipeline", "tags_in", "collections_in", "pipeline_in"}


def test_a_proposal_that_fails_its_entrys_schema_is_refused_at_submit_not_at_apply() -> None:
    for bad in ({"name": "", "property_ids": [7]}, {"name": "x", "property_ids": []}):
        with pytest.raises(ToolError) as refused:
            terminator.accept(_proposing("create_collection", **bad), definition=_real_agent(), seen=frozenset({7}), produced={})
        assert refused.value.code is ToolErrorCode.INVALID_ARGUMENTS
    with pytest.raises(ToolError):
        terminator.accept(_proposing("add_note", property_id=7), definition=_real_agent(), seen=frozenset({7}), produced={})


def test_a_proposal_for_a_property_no_tool_returned_is_refused_on_every_entry_that_names_one() -> None:
    for entry, args in (("create_collection", {"name": "x", "property_ids": [7, 8]}), ("add_note", {"property_id": 8, "body": "x"}),
                        ("add_pipeline_card", {"property_id": 8})):
        with pytest.raises(ToolError) as refused:
            terminator.accept(_proposing(entry, **args), definition=_real_agent(), seen=frozenset({7}), produced={})
        assert refused.value.code is ToolErrorCode.REFUSED


def test_a_watchdog_spec_the_matcher_cannot_render_is_refused_at_submit() -> None:
    with pytest.raises(ToolError) as refused:
        terminator.accept(_proposing("create_watchdog", name="x", filters={"category_type": "prodej", "near_city_proximity": {}}),
                          definition=_real_agent(), seen=frozenset(), produced={})
    assert refused.value.code is ToolErrorCode.INVALID_ARGUMENTS


def test_a_run_holds_at_most_a_bounded_number_of_proposals() -> None:
    actions = [{"entry": "add_pipeline_card", "args": {"property_id": 7}}] * (terminator.PROPOSALS_MAX + 1)
    with pytest.raises(ToolError):
        terminator.accept({"markdown": "x", "proposed_actions": actions}, definition=_real_agent(), seen=frozenset({7}), produced={})


# The census: the executor writes no tenant rows, and the only tenant writes a run ends in are the Apply and Undo routes.
_WRITE_VERBS = ("INSERT INTO ", "UPDATE ", "DELETE FROM ")


def _sql_targets(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = " ".join(node.value.upper().split())
            for verb in _WRITE_VERBS:
                at = 0
                while (at := upper.find(verb, at)) != -1:
                    at += len(verb)
                    found.add(upper[at:].split(" ", 1)[0].split("(", 1)[0].strip().lower())
    return found


def test_nothing_under_api_agents_names_a_tenant_table_for_a_write_but_the_run_ledger() -> None:
    from tests._tenant_registry import TENANT_TABLES

    ledger = {"agent_runs", "agent_run_steps", "agent_artifacts", "valuo_valuations"}
    seen: set[str] = set()
    for path in (ROOT / "api" / "agents").glob("*.py"):
        targets = _sql_targets(ast.parse(path.read_text(encoding="utf-8")))
        seen |= targets
        assert not (targets & set(TENANT_TABLES)) - ledger, path.name
    assert {"agent_runs", "agent_run_steps"} <= seen, "the walk reads the executor's own writes"


def _callers_of(name: str) -> set[str]:
    """Each `api/` file calling `proposals.<name>`, by the scope that does."""
    hits: set[str] = set()
    for path in (ROOT / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef):
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == name
                            and isinstance(node.func.value, ast.Name) and node.func.value.id == "proposals"):
                        hits.add(f"{path.relative_to(ROOT).as_posix()}::{fn.name}")
    return hits


def test_the_only_door_to_a_write_is_the_apply_and_undo_routes() -> None:
    assert _callers_of("apply") == {"api/routes/agent_runs.py::apply_proposal"}
    assert _callers_of("undo") == {"api/routes/agent_runs.py::undo_proposal"}
    # And nothing a run runs reaches them: nothing else under api/agents imports the door or a module a write goes through,
    # and only the door runs a handler of a write (`entry.handler`; the registry's own `call` refuses a write first).
    write_modules = {"proposals", "writes", "curation", "pipeline", "dismissals", "notifications"}
    for path in (ROOT / "api" / "agents").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.name != "proposals.py":
            imported = {alias.name.rsplit(".", 1)[-1] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom | ast.Import)
                        for alias in node.names}
            assert not imported & write_modules, path.name
            assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "handler"], path.name


@pytest.mark.parametrize("name", sorted(_IN_SCOPE))
def test_the_registry_will_not_run_a_write_whoever_asks(name: str) -> None:
    from api.capabilities.entry import Caller

    with pytest.raises(ToolError) as refused:
        registry.call(Caller(market=_FakeConn(), claims={"sub": "a"}), name, _IN_SCOPE[name])
    assert refused.value.code is ToolErrorCode.FORBIDDEN


def test_every_write_entry_declares_its_inverse_and_its_draw() -> None:
    from api.capabilities.entry import WriteEntry

    for entry in registry.ENTRIES.values():
        assert isinstance(entry, WriteEntry) == (entry.effect is Effect.WRITE), entry.name


def test_proposals_are_read_from_the_runs_row_never_the_diary() -> None:
    """The diary (`agent_run_steps`) holds the refused proposals too (#2074)."""
    assert "agent_run_steps" not in (ROOT / "api" / "agents" / "proposals.py").read_text(encoding="utf-8")


def test_a_write_tool_names_a_property_only_where_the_terminator_looks() -> None:
    """The rail reads `property_id` and `property_ids`: a field called anything else would walk past it."""
    assert _misnamed(portable(_Fine)) == []
    assert _misnamed(portable(_Evasive)) == ["properties", "subject_property", "target.property"]
    for entry in registry.ENTRIES.values():
        if entry.effect is Effect.WRITE:
            assert not _misnamed(entry.input_schema), entry.name


class _Fine(BaseModel):
    property_id: int = 0
    property_ids: list[int] = []
    text: str = ""


class _Target(BaseModel):
    property: int = 0


class _Evasive(BaseModel):
    properties: list[int] = []
    subject_property: int = 0
    target: _Target = _Target()


def _misnamed(schema: dict[str, Any]) -> list[str]:
    return [dotted for dotted, _ in _walk(schema)
            if "propert" in dotted.rsplit(".", 1)[-1] and dotted.rsplit(".", 1)[-1] not in terminator.PROPERTY_ARGS]


# --- no identity anywhere a model or a run's input can fill ----------------------------------------------------------------

def test_no_field_of_the_terminator_names_an_identity(probes: None) -> None:
    """The registry's own schemas are the census's (`CENSUS`); this is the one tool a model calls that it does not cover."""
    assert not _identity_fields(terminator.schema(_agent())["input_schema"])
    assert _identity_fields({"properties": {"account_id": {"type": "string"}}}) == ["account_id"], "the walker sees it"


def test_no_agent_folder_takes_an_identity_as_input_or_declares_one_as_output() -> None:
    """Passes on nothing until #2069's folder lands in `skills/`."""
    for name, definition in load_all(SKILLS_DIR).items():
        assert not _identity_fields(dict(definition.input_schema)), name
        assert not _identity_fields(dict(definition.output_schema or {})), name
        assert not _misnamed(dict(definition.output_schema or {})), f"{name}: the terminator reads only property_id(s)"

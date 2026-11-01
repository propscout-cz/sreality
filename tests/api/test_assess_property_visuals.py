"""assess_property_visuals through the registry and the loop: a repeat is served from the cache with no model call and
no spend; the eleventh paid property in one run is refused; a paid call happens only inside a run that can afford it."""

from __future__ import annotations

from typing import Any

import pytest

from api.agents import loop, terminator
from api.agents.definition import Definition, Limits
from api.capabilities import registry, run_scope, visuals
from api.capabilities.entry import Caller, Effect, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from api.providers import ModelPrice, ProviderError
from scraper import image_storage
from tests.api._agent_fixtures import reply
from tests.api._fakes import _FakeConn, _ScriptedProvider
from tests.api._script_conn import ScriptConn
from toolkit import photo_assessment as pa

NAME = "assess_property_visuals"
MODEL = pa.DEFAULT_MODEL
PRICE = ModelPrice(4.0, 20.0, 0.2, 5.0)


def _good() -> dict[str, Any]:
    return {"facets": {k: {"score": 4, "evidence": f"{k} vidět"} for k in pa.load_facets().keys}}


def _assessment(usage: tuple[int, int] = (10_000, 1_000)) -> Any:
    return reply((pa.TOOL_NAME, _good()), usage=usage, model=MODEL)


class _R2:
    def download_bytes(self, key: str) -> bytes:
        return b"bytes"


class World:
    """Everything but the SQL, which tests/test_photo_assessment_live.py runs: one photo set per property id."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cache: dict[tuple[str, str], dict[str, Any]] = {}
        self.sets: dict[int, pa.PhotoSet | None] = {}
        self.conn = ScriptConn([(lambda sql: "app_settings" in sql, [])])
        monkeypatch.setattr(pa, "photo_set", lambda conn, pid: self.sets.get(pid, self._set(pid)))
        monkeypatch.setattr(pa, "_lookup", lambda conn, h, v: self.cache.get((h, v)))

        def store(conn: Any, photos: pa.PhotoSet, facets: pa.FacetFile, judged: pa.Judgement) -> None:
            self.cache.setdefault((photos.hash, facets.version), {
                "facets": judged.facets, "model": judged.model, "created_at": "2026-11-01T10:00:00+00:00"})
        monkeypatch.setattr(pa, "_store", store)
        monkeypatch.setattr(image_storage, "is_configured", lambda: True)
        monkeypatch.setattr(image_storage.R2Client, "from_env", staticmethod(lambda: _R2()))

    @staticmethod
    def _set(pid: int) -> pa.PhotoSet:
        return pa.PhotoSet(property_id=pid, listing_id=pid * 10, stored=1, pending=0, plans=(),
                           photos=(pa.Photo(pid * 100, f"img/{pid}.jpg", None, 800, 600),))

    def scope(self, *replies: Any, left: float = 20.0) -> tuple[run_scope.RunScope, _ScriptedProvider]:
        provider = _ScriptedProvider("anthropic", list(replies), prices={MODEL: PRICE})
        llm = LLMClient(_FakeConn(), providers={"anthropic": provider})
        return run_scope.RunScope(llm=llm, left_usd=lambda: left, left_s=lambda: 60.0), provider

    def call(self, scope: run_scope.RunScope | None, pid: int) -> dict[str, Any]:
        with run_scope.running(scope):
            return registry.call(Caller(market=self.conn), NAME, {"property_id": pid})


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


def test_it_is_a_costly_market_tool_and_the_market_transaction_does_not_hold_it(world: World) -> None:
    entry = registry.ENTRIES[NAME]
    assert entry.tenancy is Tenancy.MARKET and entry.effect is Effect.COSTLY
    scope, _ = world.scope(_assessment())
    world.call(scope, 7)
    # No READ ONLY bound and no open transaction: the paid call takes a minute and then writes its cache row.
    assert world.conn.tx == [] and not [sql for sql, _ in world.conn.executed if "READ ONLY" in sql.upper()]


def test_a_second_request_for_the_same_photo_set_and_version_makes_no_model_call(world: World) -> None:
    scope, provider = world.scope(_assessment())
    first = world.call(scope, 7)
    assert first["data"]["cache_hit"] is False and first["data"]["cost_usd"] > 0
    assert scope.settle()[0] == pytest.approx(first["data"]["cost_usd"])
    second = world.call(scope, 7)
    assert len(provider.calls) == 1
    assert second["data"]["cache_hit"] is True and second["data"]["cost_usd"] == 0
    assert second["data"]["facets"] == first["data"]["facets"]
    assert scope.settle() == (0.0, None), "a hit is no spend"


def test_another_agents_run_is_served_the_same_judgement(world: World) -> None:
    scope, _ = world.scope(_assessment())
    first = world.call(scope, 7)
    other, provider = world.scope()
    assert world.call(other, 7)["data"]["facets"] == first["data"]["facets"]
    assert provider.calls == []


def test_a_new_prompt_version_is_judged_again(world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    scope, provider = world.scope(_assessment(), _assessment())
    world.call(scope, 7)
    edited = tmp_path / "facets.md"
    edited.write_text(pa.FACETS_PATH.read_text(encoding="utf-8") + "\nOne more thought.\n", encoding="utf-8")
    monkeypatch.setattr(pa, "FACETS_PATH", edited)
    assert world.call(scope, 7)["data"]["cache_hit"] is False
    assert len(provider.calls) == 2


def test_the_eleventh_paid_property_in_one_run_is_refused_with_a_clear_error(world: World) -> None:
    scope, provider = world.scope(*[_assessment() for _ in range(10)])
    for pid in range(1, 11):
        assert world.call(scope, pid)["data"]["status"] == "assessed"
    with pytest.raises(ToolError) as refused:
        world.call(scope, 11)
    assert refused.value.code is ToolErrorCode.REFUSED and not refused.value.retryable
    assert str(visuals.MAX_PAID_PER_RUN) in refused.value.message and "this run" in refused.value.message
    assert len(provider.calls) == 10


def test_a_hit_uses_no_slot_and_is_served_after_the_tenth(world: World) -> None:
    """Story 12 bounds vision cost, and a hit costs none."""
    scope, provider = world.scope(*[_assessment() for _ in range(10)])
    for pid in range(1, 11):
        world.call(scope, pid)
    assert world.call(scope, 3)["data"]["cache_hit"] is True
    world.cache[(World._set(50).hash, pa.load_facets().version)] = {
        "facets": _good()["facets"], "model": MODEL, "created_at": "2026-11-01T10:00:00+00:00"}
    assert world.call(scope, 50)["data"]["cache_hit"] is True
    assert len(provider.calls) == 10


def test_a_reply_outside_the_schema_is_not_cached_but_is_charged_and_counted(world: World) -> None:
    scope, provider = world.scope(reply((pa.TOOL_NAME, {"facets": {}}), model=MODEL), _assessment())
    with pytest.raises(ToolError) as refused:
        world.call(scope, 7)
    assert refused.value.code is ToolErrorCode.REFUSED and "not" in refused.value.message
    assert world.cache == {}
    cost, llm_call_id = scope.settle()
    assert cost > 0 and llm_call_id
    assert world.call(scope, 7)["data"]["cache_hit"] is False, "asked again, it is judged again"
    assert scope.counts[NAME] == 2, "each bill uses a slot, so ten is a bound on what a run pays"


def test_no_usable_photo_is_a_clear_result_with_no_model_call_and_no_slot(world: World) -> None:
    world.sets[7] = pa.PhotoSet(property_id=7, listing_id=70, stored=0, pending=3, photos=(), plans=())
    scope, provider = world.scope()
    out = world.call(scope, 7)
    assert out["data"]["status"] == "no_photos" and out["data"]["facets"] is None
    assert out["data"]["photos_pending"] == 3 and "photo" in out["data"]["note"]
    assert provider.calls == [] and scope.counts[NAME] == 0


def test_a_photo_on_its_way_defers_a_paid_judgement_because_the_finished_set_would_be_paid_for_again(world: World) -> None:
    photos = (pa.Photo(700, "img/7.jpg", None, 800, 600),)
    world.sets[7] = pa.PhotoSet(property_id=7, listing_id=70, stored=1, pending=2, photos=photos, plans=())
    scope, provider = world.scope()
    out = world.call(scope, 7)
    assert out["data"]["status"] == "photos_pending" and out["data"]["facets"] is None and "2" in out["data"]["note"]
    assert provider.calls == [] and scope.counts[NAME] == 0


def test_a_photo_on_its_way_does_not_hide_a_judgement_already_held(world: World) -> None:
    held = pa.PhotoSet(property_id=7, listing_id=70, stored=1, pending=2, plans=(),
                       photos=(pa.Photo(700, "img/7.jpg", None, 800, 600),))
    world.sets[7] = held
    world.cache[(held.hash, pa.load_facets().version)] = {
        "facets": _good()["facets"], "model": MODEL, "created_at": "2026-11-01T10:00:00+00:00"}
    assert world.call(None, 7)["data"]["cache_hit"] is True


def test_a_photo_on_its_way_past_the_cap_changes_nothing_so_it_defers_nothing(world: World) -> None:
    photos = tuple(pa.Photo(i, f"img/{i}.jpg", None, 800, 600) for i in range(pa.MAX_PHOTOS))
    world.sets[7] = pa.PhotoSet(property_id=7, listing_id=70, stored=pa.MAX_PHOTOS, pending=2, photos=photos, plans=())
    scope, _ = world.scope(_assessment())
    assert world.call(scope, 7)["data"]["status"] == "assessed"


def test_an_unknown_property_is_not_found(world: World) -> None:
    world.sets[7] = None
    with pytest.raises(ToolError) as refused:
        world.call(world.scope()[0], 7)
    assert refused.value.code is ToolErrorCode.NOT_FOUND


def test_outside_a_run_nothing_is_paid_but_a_hit_is_still_read(world: World) -> None:
    """A costly call needs a budget and a tally, which only a run has (capabilities_demo has neither)."""
    with pytest.raises(ToolError) as refused:
        world.call(None, 7)
    assert refused.value.code is ToolErrorCode.REFUSED and "run" in refused.value.message
    world.cache[(World._set(7).hash, pa.load_facets().version)] = {
        "facets": _good()["facets"], "model": MODEL, "created_at": "2026-11-01T10:00:00+00:00"}
    assert world.call(None, 7)["data"]["cache_hit"] is True


def test_a_run_that_cannot_afford_the_worst_case_is_refused_before_any_money(world: World) -> None:
    scope, provider = world.scope(left=0.01)
    with pytest.raises(ToolError) as refused:
        world.call(scope, 7)
    assert refused.value.code is ToolErrorCode.REFUSED and "budget" in refused.value.message
    assert provider.calls == [] and scope.counts[NAME] == 0


def test_an_unpriced_model_is_refused_because_its_cost_could_not_be_capped(world: World) -> None:
    class Unpriced(_ScriptedProvider):
        def price_for(self, model: str) -> ModelPrice | None:
            return None
    provider = Unpriced("anthropic", [])
    scope = run_scope.RunScope(llm=LLMClient(_FakeConn(), providers={"anthropic": provider}), left_usd=lambda: 20.0, left_s=lambda: 60.0)
    with pytest.raises(ToolError) as refused:
        world.call(scope, 7)
    assert refused.value.code is ToolErrorCode.UNAVAILABLE and provider.calls == []


def test_a_provider_failure_bills_nothing_uses_no_slot_and_may_be_retried(world: World) -> None:
    class Failing(_ScriptedProvider):
        def complete(self, **kwargs: Any) -> Any:
            raise ProviderError("overloaded")
    scope = run_scope.RunScope(
        llm=LLMClient(_FakeConn(), providers={"anthropic": Failing("anthropic", [], prices={MODEL: PRICE})}),
        left_usd=lambda: 20.0, left_s=lambda: 60.0)
    with pytest.raises(ToolError) as refused:
        world.call(scope, 7)
    assert refused.value.code is ToolErrorCode.UNAVAILABLE and refused.value.retryable
    assert scope.counts[NAME] == 0 and world.cache == {}


def test_a_bill_whose_ledger_row_failed_keeps_its_slot_and_is_charged_at_its_worst(
        world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The INSERT follows the provider's bill: a database error there must not leave a retry free of both limits."""
    scope, _ = world.scope(_assessment())
    monkeypatch.setattr(scope.llm, "_record_call", lambda **kw: (_ for _ in ()).throw(RuntimeError("ledger down")))
    with pytest.raises(ToolError) as failed:
        world.call(scope, 7)
    assert failed.value.code is ToolErrorCode.INTERNAL and scope.counts[NAME] == 1
    cost, llm_call_id = scope.settle()
    assert cost == pytest.approx(pa.worst_case_usd(World._set(7), pa.load_facets(), PRICE)) and llm_call_id is None


def test_the_tool_echoes_the_id_it_was_asked_and_names_its_survivor_in_the_data(world: World) -> None:
    world.sets[7] = World._set(9)
    scope, _ = world.scope(_assessment())
    out = world.call(scope, 7)
    assert out["metadata"]["filters_used"] == {"property_id": 7} and out["data"]["property_id"] == 9


def test_a_photo_it_cannot_read_pays_nothing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def download_bytes(self, key: str) -> bytes:
            raise OSError("gone")
    monkeypatch.setattr(image_storage.R2Client, "from_env", staticmethod(lambda: Broken()))
    scope, provider = world.scope()
    with pytest.raises(ToolError) as refused:
        world.call(scope, 7)
    assert refused.value.code is ToolErrorCode.UNAVAILABLE and provider.calls == [] and scope.counts[NAME] == 0


# --- through the loop -------------------------------------------------------------------------------------------

def _agent(max_cost: float = 5.0) -> Definition:
    return Definition(
        name="probe_scout_v1", description="A probe.", instructions="Judge the shortlist.", tools=(NAME,),
        models={"anthropic": MODEL}, limits=Limits(30, max_cost, 60.0),
        input_schema={"type": "object", "additionalProperties": False, "properties": {}}, output_schema=None,
        sha="0" * 64)


def _run(world: World, replies: list[Any], agent: Definition | None = None) -> tuple[loop.Outcome, list[loop.Step], Any]:
    provider = _ScriptedProvider("anthropic", replies, prices={MODEL: PRICE})
    steps: list[loop.Step] = []
    caller = Caller(market=world.conn)
    outcome = loop.run(agent or _agent(), {}, provider="anthropic", llm=LLMClient(_FakeConn(), providers={"anthropic": provider}),
                       call_tool=lambda name, args: registry.call(caller, name, args), record=steps.append)
    return outcome, steps, provider


def _submit(*cited: int) -> Any:
    return reply((terminator.NAME, {"markdown": "Hotovo.", "cited_property_ids": list(cited)}), model=MODEL)


def test_in_a_run_the_paid_call_counts_towards_its_cost_and_its_journal_and_the_answer_may_cite_it(world: World) -> None:
    outcome, steps, _ = _run(world, [reply((NAME, {"property_id": 7}), model=MODEL), _assessment(), _submit(7)])
    assert outcome.stop_reason == "submit_result"
    tool_step = next(s for s in steps if s.kind == "tool" and s.name == NAME)
    assert tool_step.cost_usd > 0 and tool_step.llm_call_id
    assert outcome.cost_usd == pytest.approx(sum(s.cost_usd for s in steps))


def test_in_a_run_the_eleventh_reaches_the_model_as_its_tool_error(world: World) -> None:
    asks = [(NAME, {"property_id": pid}) for pid in range(1, 12)]
    outcome, steps, _ = _run(world, [reply(*asks, model=MODEL), *[_assessment() for _ in range(10)], _submit(1)])
    errors = [s for s in steps if s.kind == "tool" and "error" in s.output]
    assert [s.input["property_id"] for s in errors] == [11]
    assert errors[0].output["error"]["code"] == "refused"
    assert outcome.stop_reason == "submit_result"


def test_a_cap_spent_on_photos_stops_the_run_on_max_cost(world: World) -> None:
    """A dear assessment leaves too little for another reply: the loop's own cost cap sees the tool's spend."""
    outcome, _, _ = _run(world, [reply((NAME, {"property_id": 7}), model=MODEL), _assessment(usage=(100_000, 12_000)), _submit(7)],
                         agent=_agent(max_cost=0.7))
    assert outcome.stop_reason == "max_cost"


def test_a_paid_tool_is_refused_rather_than_leave_the_run_unable_to_answer(world: World) -> None:
    """The cap covers an assessment's worst case but not that and the reply that must follow: the answer still comes."""
    photos, facets = World._set(7), pa.load_facets()
    room_for_the_answer = loop.MIN_REPLY_TOKENS * PRICE.output_per_mtok / 1e6
    cap = pa.worst_case_usd(photos, facets, PRICE) + room_for_the_answer * 0.9
    outcome, steps, provider = _run(world, [reply((NAME, {"property_id": 7}), usage=(1, 1), model=MODEL), _submit()],
                                    agent=_agent(max_cost=cap))
    refused = next(s for s in steps if s.kind == "tool" and s.name == NAME)
    assert refused.output["error"]["code"] == "refused" and "budget" in refused.output["error"]["message"]
    assert outcome.stop_reason == "submit_result" and world.cache == {}
    assert len(provider.calls) == 2


def test_a_hit_adds_nothing_to_the_runs_cost(world: World) -> None:
    world.cache[(World._set(7).hash, pa.load_facets().version)] = {
        "facets": _good()["facets"], "model": MODEL, "created_at": "2026-11-01T10:00:00+00:00"}
    outcome, steps, _ = _run(world, [reply((NAME, {"property_id": 7}), model=MODEL), _submit(7)])
    assert next(s for s in steps if s.kind == "tool" and s.name == NAME).cost_usd == 0
    assert outcome.cost_usd == pytest.approx(sum(s.cost_usd for s in steps if s.kind == "model"))


def test_the_wall_clock_left_bounds_the_vision_call_on_the_loops_path(world: World) -> None:
    """No executor here: this is the local runner's path, loop.run with a client of its own."""
    now = [100.0]

    class Slow(_ScriptedProvider):
        def complete(self, **kwargs: Any) -> Any:
            now[0] += 10.0
            return super().complete(**kwargs)

    provider = Slow("anthropic", [reply((NAME, {"property_id": 7}), model=MODEL), _assessment(), _submit(7)],
                    prices={MODEL: PRICE})
    loop.run(_agent(), {}, provider="anthropic", llm=LLMClient(_FakeConn(), providers={"anthropic": provider}),
             call_tool=lambda name, args: registry.call(Caller(market=world.conn), name, args), record=lambda s: None,
             clock=lambda: now[0])
    assert [c["timeout_s"] for c in provider.calls] == [pytest.approx(60.0), pytest.approx(50.0), pytest.approx(40.0)]


def test_a_vision_call_that_hangs_to_the_wall_clock_ends_the_run_as_a_timeout_and_keeps_no_slot(world: World) -> None:
    now = [100.0]

    class Hanging(_ScriptedProvider):
        def complete(self, **kwargs: Any) -> Any:
            if len(self.calls) == 1:
                now[0] += kwargs["timeout_s"]
                self.calls.append(kwargs)
                raise ProviderError("timed out")
            return super().complete(**kwargs)

    provider = Hanging("anthropic", [reply((NAME, {"property_id": 7}), model=MODEL)], prices={MODEL: PRICE})
    steps: list[loop.Step] = []
    outcome = loop.run(_agent(), {}, provider="anthropic", llm=LLMClient(_FakeConn(), providers={"anthropic": provider}),
                       call_tool=lambda name, args: registry.call(Caller(market=world.conn), name, args),
                       record=steps.append, clock=lambda: now[0])
    assert outcome.stop_reason == "timeout" and world.cache == {}
    failed = next(s for s in steps if s.kind == "tool")
    assert failed.output["error"]["code"] == "unavailable" and failed.cost_usd == 0


def test_a_clock_spent_reading_photos_pays_nothing(world: World) -> None:
    scope, provider = world.scope()
    scope.left_s = lambda: 0.0
    with pytest.raises(ToolError) as refused:
        world.call(scope, 7)
    assert refused.value.code is ToolErrorCode.REFUSED and "wall clock" in refused.value.message
    assert provider.calls == [] and scope.counts[NAME] == 0


def test_the_scope_is_gone_when_the_tool_call_ends(world: World) -> None:
    scope, _ = world.scope()
    with run_scope.running(scope):
        assert run_scope.current() is scope
    assert run_scope.current() is None

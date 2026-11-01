"""valuo_valuation through the registry: who may reach it and what is checked before any money. A stand-in for
api/valuo.new_valuation records whether the money path was reached; the ledger's own SQL and the whole path through a
worker are tests/test_valuo_valuations_live.py and tests/test_agent_runs_live.py. No test here can reach Valuo."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from api import tenant_pool, valuo, valuo_client
from api.agents import claims
from api.capabilities import registry, run_scope, valuo_tool
from api.capabilities.entry import Caller, Effect, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.llm_client import LLMClient
from tests.api._fakes import _FakeConn
from tests.api._script_conn import ScriptConn

NAME = "valuo_valuation"
USER, ACCOUNT, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
RUN_ID = 41
PRICED = {"id": 900, "created_at": "2026-11-01T10:00:00+01:00", "listing_id": 70, "environment": "sandbox",
          "status": "success", "request": {"kind": "sale"}, "provenance": {"kind": {"class": "ad"}},
          "result": {"avg_price": 6_000_000, "avg_price_m2": 85_700}, "records": [{"id": 1, "photos": [{"thumb": "t"}],
          "price": 5_000_000}], "summary": {"count": 1}, "kind_label": "Prodej", "error": None,
          "ppm2_basis": "sale_capital_czk_m2"}


class World:
    """A worker's service connection and the tenant pool's stand-in; `answer` is what the money path does."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cap: list[tuple[Any, ...]] = [(5,)]
        self.on_run: list[tuple[Any, ...]] = [(0,)]
        self.market = ScriptConn([(lambda sql: "app_settings" in sql, self.cap),
                                  (lambda sql: "agent_run_id" in sql, self.on_run)])
        self.member: list[tuple[Any, ...]] = [(ACCOUNT,)]
        self.tenant_open = 0
        self.tenant_calls: list[dict[str, Any]] = []
        self.paid: list[dict[str, Any]] = []
        self.answer: Callable[[valuo.RunCall], dict[str, Any]] = self.priced
        monkeypatch.setattr(tenant_pool, "tenant_transaction", self._tenant)
        monkeypatch.setattr(valuo_client, "credential", lambda: valuo_client.Credential("sandbox", "tok"))
        monkeypatch.setattr(valuo, "new_valuation", self._new_valuation)

    @staticmethod
    def priced(call: valuo.RunCall) -> dict[str, Any]:
        call.took_slot()
        return {"reused": False, "valuation": PRICED}

    @contextlib.contextmanager
    def _tenant(self, claim: dict[str, Any], *, act_as: Any, method: str) -> Iterator[ScriptConn]:
        self.tenant_calls.append({"claims": claim, "act_as": act_as, "method": method})
        self.tenant_open += 1
        try:
            yield ScriptConn([(lambda sql: "current_account_ids" in sql, self.member)])
        finally:
            self.tenant_open -= 1

    def _new_valuation(self, conn: Any, property_id: int, overrides: dict[str, Any], *, account_id: uuid.UUID,
                       cred: Any, run: valuo.RunCall | None = None) -> dict[str, Any]:
        self.paid.append({"conn": conn, "property_id": property_id, "overrides": overrides, "account_id": account_id,
                          "run": run, "tenant_open": self.tenant_open, "tx_depth": conn.depth})
        return self.answer(run)

    def scope(self, *, run_id: int | None = RUN_ID, left_usd: float = 1.0, left_s: float = 300.0) -> run_scope.RunScope:
        llm = LLMClient(_FakeConn(), providers={}, agent_run_id=run_id)
        return run_scope.RunScope(llm=llm, left_usd=lambda: left_usd, left_s=lambda: left_s)

    def caller(self, account: uuid.UUID = ACCOUNT) -> Caller:
        return claims.caller_for(self.market, principal_user_id=USER, account_id=account)

    def call(self, scope: run_scope.RunScope | None, pid: int = 7, *, caller: Caller | None = None,
             args: dict[str, Any] | None = None) -> dict[str, Any]:
        with run_scope.running(scope):
            return registry.call(caller or self.caller(), NAME, args if args is not None else {"property_id": pid})

    def refused(self, scope: run_scope.RunScope | None, **kw: Any) -> ToolError:
        with pytest.raises(ToolError) as err:
            self.call(scope, **kw)
        return err.value


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


# --- what it is ---------------------------------------------------------------------------------------------------

def test_it_is_a_costly_tenant_tool_taking_only_a_property() -> None:
    entry = registry.ENTRIES[NAME]
    assert entry.tenancy is Tenancy.TENANT and entry.effect is Effect.COSTLY
    assert set(entry.input_schema["properties"]) == {"property_id"}


@pytest.mark.parametrize("smuggled", [
    {"account_id": str(OTHER)}, {"act_as": str(OTHER)}, {"monthly_cap": 1000}, {"max_calls": 100},
    {"run_id": 1}, {"overrides": {"place": "Praha"}}, {"force": True},
])
def test_no_model_argument_can_name_an_account_or_widen_a_cap(world: World, smuggled: dict[str, Any]) -> None:
    error = world.refused(world.scope(), args={"property_id": 7, **smuggled})
    assert error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert not world.paid and not world.tenant_calls, "refused before any connection"


# --- whose money, and where it may start --------------------------------------------------------------------------

def test_the_account_is_the_runs_claim_read_under_its_login_and_released_before_the_money(world: World) -> None:
    out = world.call(world.scope())
    assert world.tenant_calls == [{"claims": dict(world.caller().claims), "act_as": None, "method": "GET"}]
    [paid] = world.paid
    assert paid["account_id"] == ACCOUNT and paid["run"].run_id == RUN_ID and paid["overrides"] == {}
    assert paid["conn"] is world.market, "the ledger is the service role's: no login may write it"
    assert paid["tenant_open"] == 0, "no tenant connection is held across the paid call"
    assert paid["tx_depth"] == 0, "and no transaction stays open round it"
    assert not [sql for sql, _ in world.market.executed if "READ ONLY" in sql.upper()]
    assert out["data"]["valuation_id"] == 900


def test_a_call_with_no_login_is_forbidden(world: World) -> None:
    error = world.refused(world.scope(), caller=Caller(market=world.market))
    assert error.code is ToolErrorCode.FORBIDDEN and not world.paid


def test_a_login_with_no_run_claim_is_forbidden_though_it_holds_one_account(world: World) -> None:
    error = world.refused(world.scope(), caller=Caller(market=world.market, claims={"sub": str(USER)}))
    assert error.code is ToolErrorCode.FORBIDDEN and not world.paid


def test_a_principal_no_longer_in_the_runs_account_is_forbidden(world: World) -> None:
    world.member.clear()
    error = world.refused(world.scope())
    assert error.code is ToolErrorCode.FORBIDDEN and not world.paid


def test_a_call_with_no_run_is_refused(world: World) -> None:
    error = world.refused(None)
    assert error.code is ToolErrorCode.REFUSED and "run" in error.message and not world.paid


def test_the_local_runner_has_a_scope_but_no_run_row_so_it_is_refused(world: World) -> None:
    error = world.refused(world.scope(run_id=None))
    assert error.code is ToolErrorCode.REFUSED and "run" in error.message and not world.paid


# --- the three per run --------------------------------------------------------------------------------------------

def test_a_fourth_call_in_one_run_is_refused_before_the_money_path(world: World) -> None:
    scope = world.scope()
    for pid in (1, 2, 3):
        world.call(scope, pid)
    error = world.refused(scope, pid=4)
    assert error.code is ToolErrorCode.REFUSED and "3" in error.message
    assert [p["property_id"] for p in world.paid] == [1, 2, 3]


def test_the_limit_does_not_leak_into_the_next_run(world: World) -> None:
    scope = world.scope()
    for pid in (1, 2, 3):
        world.call(scope, pid)
    assert world.call(world.scope(run_id=RUN_ID + 1), 4)["data"]["valuation_id"] == 900
    assert run_scope.current() is None


def test_a_restarted_run_starts_from_what_its_first_attempt_paid_for(world: World) -> None:
    world.on_run[:] = [(2,)]
    scope = world.scope()
    world.call(scope, 1)
    error = world.refused(scope, pid=2)
    assert error.code is ToolErrorCode.REFUSED and len(world.paid) == 1
    asked = [sql for sql, _ in world.market.executed if "agent_run_id" in sql]
    assert len(asked) == 1, "the ledger is read once per attempt; the scope's tally is the one counter"


def test_a_free_reuse_takes_no_slot(world: World) -> None:
    world.answer = lambda call: {"reused": True, "valuation": PRICED}
    scope = world.scope()
    for pid in (1, 2, 3, 4):
        out = world.call(scope, pid)
        assert out["data"]["reused"] is True and out["metadata"]["valuo_calls_paid"] == 0
    assert scope.counts[NAME] == 0


def test_a_failure_after_the_row_exists_takes_a_slot_and_says_what_it_cost(world: World) -> None:
    def failed(call: valuo.RunCall) -> dict[str, Any]:
        call.took_slot()
        return {"reused": False, "valuation": {**PRICED, "status": "failed", "result": None,
                                               "error": {"code": "unavailable", "message": "Valuo je teď nedostupné."}}}
    world.answer = failed
    scope = world.scope()
    error = world.refused(scope)
    assert error.code is ToolErrorCode.REFUSED and scope.counts[NAME] == 1
    assert error.details == ({"valuation_id": 900, "code": "unavailable", "valuo_calls_paid": 1},)


def test_a_crash_after_the_row_exists_keeps_the_slot(world: World) -> None:
    def crash(call: valuo.RunCall) -> dict[str, Any]:
        call.took_slot()
        raise RuntimeError("after the paid request")
    world.answer = crash
    scope = world.scope()
    assert world.refused(scope).code is ToolErrorCode.INTERNAL and scope.counts[NAME] == 1


def test_a_refusal_from_the_money_path_takes_no_slot(world: World) -> None:
    def refuse(call: valuo.RunCall) -> dict[str, Any]:
        raise valuo._refuse(429, valuo.MONTHLY_CAP, "over the cap")
    world.answer = refuse
    scope = world.scope()
    assert world.refused(scope).code is ToolErrorCode.REFUSED and scope.counts[NAME] == 0


# --- the monthly cap's setting ------------------------------------------------------------------------------------

@pytest.mark.parametrize("stored", [[], [(None,)], [("10",)], [(True,)], [({"cap": 3},)]])
def test_a_cap_the_operator_has_not_set_refuses_every_call(world: World, stored: list[tuple[Any, ...]]) -> None:
    world.cap[:] = stored
    scope = world.scope()
    error = world.refused(scope)
    assert error.code is ToolErrorCode.REFUSED and "operator" in error.message and valuo.CAP_SETTING in error.message
    assert not world.paid and scope.counts[NAME] == 0


@pytest.mark.parametrize("stored", [0, 3, 2.5])
def test_a_number_goes_to_the_money_path_which_holds_it(world: World, stored: float) -> None:
    world.cap[:] = [(stored,)]
    world.call(world.scope())
    assert world.paid[0]["run"].monthly_cap == stored


# --- a call the run could not use -----------------------------------------------------------------------------------

def test_a_run_that_could_not_afford_to_read_the_answer_does_not_buy_it(world: World) -> None:
    error = world.refused(world.scope(left_usd=0.0))
    assert error.code is ToolErrorCode.REFUSED and "budget" in error.message and not world.paid


def test_a_run_whose_clock_ends_before_the_answer_could_land_does_not_buy_it(world: World) -> None:
    error = world.refused(world.scope(left_s=valuo_tool.MIN_LEFT_S - 1))
    assert error.code is ToolErrorCode.REFUSED and "time" in error.message and not world.paid
    assert valuo_tool.MIN_LEFT_S == 50, "the market-value call and its photo call, each at the client's own (5, 20)"


# --- what the model and the diary read ------------------------------------------------------------------------------

def test_the_answer_names_its_ledger_row_its_cost_in_calls_and_its_unit(world: World) -> None:
    scope = world.scope()
    out = world.call(scope, 7)
    data, meta = out["data"], out["metadata"]
    assert data["property_id"] == 7 and data["valuation_id"] == 900 and data["reused"] is False
    assert data["result"]["avg_price_m2"] == 85_700 and data["ppm2_basis"] == "sale_capital_czk_m2"
    assert data["records"] == [{"id": 1, "price": 5_000_000}], "photos are the page's"
    assert meta == {"valuo_calls_paid": 1, "cost_usd": None, "run_calls_used": 1, "run_calls_limit": 3,
                    "cost_note": valuo_tool.COST_NOTE}
    assert scope.settle() == (0.0, None), "no price is known, so no dollars reach the run's cost"

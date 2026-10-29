"""The tool registry's one call path (#2062): validate, pick the connection, run, map the error, fit the result."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import psycopg
import pytest
from fastapi import HTTPException
from pydantic import Field, ValidationError

from api import tenant_pool
from api.capabilities import registry, truncate
from api.capabilities.entry import Caller, Context, Effect, Entry, Tenancy
from api.capabilities.errors import ToolError, ToolErrorCode
from api.filter_args import Args
from tests.api._script_conn import ScriptConn


class _Probe(Args):
    n: int = Field(1, ge=1, description="How many.")
    flag: bool = Field(False, description="A switch.")


def _entry(handler: Any, *, tenancy: Tenancy = Tenancy.MARKET, effect: Effect = Effect.READ) -> Entry:
    return Entry(name="probe", description="A probe.", args=_Probe, handler=handler, tenancy=tenancy, effect=effect)


@pytest.fixture()
def probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(handler: Any, **kw: Any) -> None:
        monkeypatch.setattr(registry, "ENTRIES", {"probe": _entry(handler, **kw)})
    return install


def _error(name: str, args: object, caller: Caller | None = None) -> ToolError:
    with pytest.raises(ToolError) as info:
        registry.call(caller or Caller(market=ScriptConn([])), name, args)
    return info.value


# --- validate ---------------------------------------------------------------------------------------------------

def test_an_unknown_tool_is_refused() -> None:
    assert _error("no_such_tool", {}).code is ToolErrorCode.UNKNOWN_TOOL


@pytest.mark.parametrize("args, loc", [
    ({"n": 1, "bogus": 2}, ["bogus"]),
    ({"n": 0}, ["n"]),
    ({"n": "many"}, ["n"]),
    ([1, 2], []),
])
def test_invalid_arguments_never_reach_the_handler(probe: Any, args: object, loc: list[str]) -> None:
    probe(lambda ctx, a: pytest.fail("handler ran"))
    err = _error("probe", args)
    assert err.code is ToolErrorCode.INVALID_ARGUMENTS and not err.retryable
    assert [d["loc"] for d in err.details][:1] == [loc]
    assert all(set(d) == {"loc", "msg", "type"} for d in err.details)   # the value sent is never echoed


def test_strings_parse_the_way_a_model_sends_them(probe: Any) -> None:
    seen: dict[str, Any] = {}
    probe(lambda ctx, a: seen.setdefault("a", a) and {"data": []})
    registry.call(Caller(market=ScriptConn([])), "probe", {"flag": "false", "n": "3"})
    assert (seen["a"].flag, seen["a"].n) == (False, 3)


# --- connection by tenancy --------------------------------------------------------------------------------------

def test_a_market_tool_reads_in_its_own_bounded_transaction_on_the_service_connection(probe: Any) -> None:
    envelope = {"data": {"rows": []}, "metadata": {"tool": "probe"}}
    conn, seen = ScriptConn([]), {}

    def handler(ctx: Context, a: _Probe) -> dict[str, Any]:
        seen["conn"] = ctx.conn
        with ctx.conn.cursor() as cur:
            cur.execute("SELECT 1")
        return envelope
    probe(handler)
    assert registry.call(Caller(market=conn, claims=None), "probe", {}) is envelope
    assert seen["conn"] is conn and conn.tx == ["commit"]
    assert [sql for sql, _ in conn.executed] == [*tenant_pool.READ_BOUND, "SELECT 1"]


def test_a_tenant_tool_without_a_signed_in_caller_is_forbidden_before_any_connection(probe: Any, monkeypatch) -> None:
    monkeypatch.setattr(registry.tenant_pool, "tenant_transaction", lambda *a, **k: pytest.fail("opened"))
    probe(lambda ctx, a: pytest.fail("handler ran"), tenancy=Tenancy.TENANT)
    assert _error("probe", {}).code is ToolErrorCode.FORBIDDEN


@pytest.mark.parametrize("effect, method", [(Effect.READ, "GET"), (Effect.WRITE, "POST"), (Effect.COSTLY, "POST")])
def test_a_tenant_tool_runs_in_the_callers_own_transaction_never_acting_as_anyone(
    probe: Any, monkeypatch: pytest.MonkeyPatch, effect: Effect, method: str,
) -> None:
    opened: list[tuple[dict, Any, str]] = []
    tenant_conn = ScriptConn([])

    @contextlib.contextmanager
    def fake_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
        opened.append((claims, act_as, method))
        yield tenant_conn
    monkeypatch.setattr(registry.tenant_pool, "tenant_transaction", fake_transaction)
    seen: dict[str, Any] = {}
    probe(lambda ctx, a: seen.setdefault("conn", ctx.conn) and {"data": []}, tenancy=Tenancy.TENANT, effect=effect)
    claims = {"sub": "a", "role": "authenticated"}
    market = ScriptConn([])
    registry.call(Caller(market=market, claims=claims), "probe", {})
    assert opened == [(claims, None, method)] and seen["conn"] is tenant_conn and not market.executed


@pytest.mark.parametrize("method, bounded", [("GET", True), ("HEAD", True), ("POST", False), ("PATCH", False)])
def test_a_tenant_read_is_bounded_before_its_role_and_claims_are_issued(
    monkeypatch: pytest.MonkeyPatch, method: str, bounded: bool,
) -> None:
    """What every signed-in GET/HEAD gets, a tenant READ tool's included: PostgREST's READ ONLY + 8 s."""
    conn = ScriptConn([])
    conn.close = lambda: None
    monkeypatch.setenv("TENANT_POOL_DB_URL", "postgresql://tenant-pool")
    monkeypatch.setattr(tenant_pool.psycopg, "connect", lambda *a, **k: conn)
    with tenant_pool.tenant_transaction({"sub": "a"}, act_as=None, method=method):
        pass
    bound = list(tenant_pool.READ_BOUND) if bounded else []
    assert [sql for sql, _ in conn.executed][: len(bound) + 1] == [*bound, "SET LOCAL ROLE authenticated"]
    assert conn.tx == ["commit"]


def test_a_tenant_tools_error_leaves_the_transaction_before_it_is_mapped(probe: Any, monkeypatch) -> None:
    states: list[str] = []

    @contextlib.contextmanager
    def fake_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
        try:
            yield ScriptConn([])
        except Exception:
            states.append("rolled back")
            raise
    monkeypatch.setattr(registry.tenant_pool, "tenant_transaction", fake_transaction)

    def handler(ctx: Context, a: _Probe) -> dict[str, Any]:
        raise HTTPException(status_code=409, detail="stage is not empty")
    probe(handler, tenancy=Tenancy.TENANT)
    err = _error("probe", {}, Caller(market=ScriptConn([]), claims={"sub": "a"}))
    assert (err.code, err.message, states) == (ToolErrorCode.CONFLICT, "stage is not empty", ["rolled back"])


# --- the error table --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raised, code, message", [
    (ToolError(ToolErrorCode.NOT_FOUND, "listing 9 is not in our database"), ToolErrorCode.NOT_FOUND,
     "listing 9 is not in our database"),
    (HTTPException(status_code=422, detail="bad"), ToolErrorCode.INVALID_ARGUMENTS, "bad"),
    (HTTPException(status_code=403, detail={"code": "x", "message": "not yours"}), ToolErrorCode.FORBIDDEN,
     "not yours"),
    (HTTPException(status_code=404, detail="gone"), ToolErrorCode.NOT_FOUND, "gone"),
    (HTTPException(status_code=409, detail="clash"), ToolErrorCode.CONFLICT, "clash"),
    (HTTPException(status_code=503, detail="later"), ToolErrorCode.UNAVAILABLE, "later"),
    (HTTPException(status_code=429, detail="slow down"), ToolErrorCode.REFUSED, "slow down"),
    (psycopg.errors.QueryCanceled("statement timeout"), ToolErrorCode.BUSY, None),
    (psycopg.errors.LockNotAvailable("lock"), ToolErrorCode.BUSY, None),
    (psycopg.errors.DeadlockDetected("deadlock"), ToolErrorCode.BUSY, None),
    (psycopg.errors.InsufficientPrivilege("permission denied for table x"), ToolErrorCode.FORBIDDEN, None),
    (psycopg.OperationalError("connection refused"), ToolErrorCode.UNAVAILABLE, None),
    (psycopg.errors.DatetimeFieldOverflow("timestamp out of range"), ToolErrorCode.REFUSED, None),
    (psycopg.errors.NumericValueOutOfRange("integer out of range"), ToolErrorCode.REFUSED, None),
    (ValueError("a cohort with no single per-m² basis"), ToolErrorCode.REFUSED,
     "a cohort with no single per-m² basis"),
])
def test_every_error_maps_through_the_one_table(probe: Any, raised: Exception, code: ToolErrorCode,
                                                message: str | None) -> None:
    def handler(ctx: Context, a: _Probe) -> dict[str, Any]:
        raise raised
    probe(handler)
    err = _error("probe", {})
    assert err.code is code
    assert err.retryable is (code in (ToolErrorCode.BUSY, ToolErrorCode.UNAVAILABLE))
    if message is not None:
        assert err.message == message
    else:   # a database's own text never reaches the model
        assert str(raised) not in err.message


_CANCELLED = (lambda sql: sql == "SELECT pg_sleep(9)",
              psycopg.errors.QueryCanceled("canceling statement due to statement timeout"))


@pytest.mark.parametrize("swallowed", [False, True])
@pytest.mark.parametrize("tenancy", [Tenancy.MARKET, Tenancy.TENANT])
def test_a_read_cancelled_at_its_time_limit_is_busy_and_rolled_back_even_when_the_handler_swallows_it(
    probe: Any, monkeypatch: pytest.MonkeyPatch, tenancy: Tenancy, swallowed: bool,
) -> None:
    """rent_map's reference swallows any database error: answered after a cancel, it would say "no reference"."""
    conn = ScriptConn([_CANCELLED])

    @contextlib.contextmanager
    def fake_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
        with conn.transaction():
            yield conn
    monkeypatch.setattr(registry.tenant_pool, "tenant_transaction", fake_transaction)

    def handler(ctx: Context, a: _Probe) -> dict[str, Any]:
        try:
            with ctx.conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(9)")
        except psycopg.errors.QueryCanceled:
            if not swallowed:
                raise
        return {"data": []}
    probe(handler, tenancy=tenancy)
    err = _error("probe", {}, Caller(market=conn, claims={"sub": "a"}))
    assert (err.code, err.retryable, conn.tx) == (ToolErrorCode.BUSY, True, ["rollback"])


@pytest.mark.parametrize("raised", [KeyError("cols"), TypeError("x"), RuntimeError("SELECT secret FROM t"),
                                    HTTPException(status_code=500, detail="SELECT secret")])
def test_anything_else_is_internal_with_a_reference_and_no_text(probe: Any, raised: Exception, caplog) -> None:
    def handler(ctx: Context, a: _Probe) -> dict[str, Any]:
        raise raised
    probe(handler)
    err = _error("probe", {})
    assert err.code is ToolErrorCode.INTERNAL and "secret" not in err.message and "ref " in err.message
    assert err.message.split("ref ")[1].rstrip(")") in caplog.text


def test_a_tenant_result_refused_for_its_size_rolls_the_transaction_back(probe: Any, monkeypatch) -> None:
    states: list[str] = []

    @contextlib.contextmanager
    def fake_transaction(claims: dict, *, act_as: Any, method: str) -> Any:
        try:
            yield ScriptConn([])
            states.append("committed")
        except Exception:
            states.append("rolled back")
            raise
    monkeypatch.setattr(registry.tenant_pool, "tenant_transaction", fake_transaction)
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 100)
    probe(lambda ctx, a: {"data": "x" * 500}, tenancy=Tenancy.TENANT, effect=Effect.WRITE)
    err = _error("probe", {}, Caller(market=ScriptConn([]), claims={"sub": "a"}))
    assert (err.code, states) == (ToolErrorCode.RESULT_TOO_LARGE, ["rolled back"])


def test_a_result_that_is_no_envelope_is_internal(probe: Any) -> None:
    probe(lambda ctx, a: ["no", "envelope"])
    assert _error("probe", {}).code is ToolErrorCode.INTERNAL


def test_the_payload_is_what_a_model_reads() -> None:
    err = ToolError(ToolErrorCode.BUSY, "retry", details=[{"loc": ["n"]}])
    assert err.payload() == {"error": {"code": "busy", "message": "retry", "retryable": True,
                                       "details": [{"loc": ["n"]}]}}


# --- the run's record -------------------------------------------------------------------------------------------

def _recorded() -> tuple[list[tuple[str, object, dict[str, Any]]], Any]:
    records: list[tuple[str, object, dict[str, Any]]] = []
    return records, lambda *record: records.append(record)


def test_the_run_records_what_the_model_reads_once(probe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 1_000)
    probe(lambda ctx, a: {"data": _rows(100), "metadata": {"tool": "probe"}})
    records, record = _recorded()
    raw = {"n": "2"}
    out = registry.call(Caller(market=ScriptConn([]), record=record), "probe", raw)
    assert records == [("probe", raw, out)] and records[0][1] is raw
    assert out["metadata"]["truncated"]   # the fitted envelope, not the handler's whole one


@pytest.mark.parametrize("name, args", [("probe", {"n": 0}), ("probe", [1]), ("no_such_tool", {})])
def test_a_refusal_before_any_database_is_recorded_once(probe: Any, name: str, args: object) -> None:
    probe(lambda ctx, a: pytest.fail("handler ran"))
    records, record = _recorded()
    conn = ScriptConn([])
    err = _error(name, args, Caller(market=conn, record=record))
    assert records == [(name, args, err.payload())] and not conn.executed
    assert isinstance(err.__cause__, ValidationError) is (err.code is ToolErrorCode.INVALID_ARGUMENTS)   # kept


@pytest.mark.parametrize("tenancy", [Tenancy.MARKET, Tenancy.TENANT])
def test_a_failure_inside_the_call_is_recorded_once(probe: Any, tenancy: Tenancy) -> None:
    def handler(ctx: Context, a: _Probe) -> dict[str, Any]:
        raise KeyError("cols")
    probe(handler, tenancy=tenancy)
    records, record = _recorded()
    err = _error("probe", {}, Caller(market=ScriptConn([]), record=record))   # a tenant tool: FORBIDDEN, no login
    assert err.code is (ToolErrorCode.INTERNAL if tenancy is Tenancy.MARKET else ToolErrorCode.FORBIDDEN)
    assert records == [("probe", {}, err.payload())]


# --- schemas ----------------------------------------------------------------------------------------------------

def test_schemas_are_name_description_and_a_portable_input_schema(probe: Any) -> None:
    probe(lambda ctx, a: {"data": []})
    assert registry.schemas() == [{
        "name": "probe", "description": "A probe.",
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "n": {"type": "integer", "minimum": 1, "default": 1, "description": "How many."},
                "flag": {"type": "boolean", "default": False, "description": "A switch."},
            },
        },
    }]


# --- truncation -------------------------------------------------------------------------------------------------

def _rows(n: int) -> list[dict[str, Any]]:
    return [{"listing_id": i, "price_per_m2": 90_000 + i, "price_per_m2_basis": "sale_czk_m2"} for i in range(n)]


def test_a_result_within_the_budget_is_returned_unchanged() -> None:
    result = {"data": {"listings": _rows(3)}, "metadata": {"tool": "t", "result_count": 3}}
    assert truncate.fit(result) is result


def test_an_oversized_result_keeps_whole_rows_from_the_head_and_all_its_metadata(monkeypatch) -> None:
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 2_000)
    result = {"data": {"listings": _rows(200), "notes": ["kept"]},
              "metadata": {"tool": "t", "result_count": 200, "filters_used": {"radius_m": 1000}}}
    fitted = truncate.fit(result)
    kept = fitted["data"]["listings"]
    assert len(truncate.encode(fitted)) <= 2_000
    assert kept == _rows(200)[: len(kept)] and 0 < len(kept) < 200
    assert fitted["data"]["notes"] == ["kept"]
    assert fitted["metadata"] == {**result["metadata"], "truncated": {"data.listings": {"kept": len(kept), "total": 200}}}
    assert len(result["data"]["listings"]) == 200   # the toolkit's own envelope is not mutated
    json.loads(truncate.encode(fitted))


def test_a_list_envelope_is_cut_the_same_way(monkeypatch) -> None:
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 1_000)
    fitted = truncate.fit({"data": _rows(100), "metadata": {"tool": "t"}})
    assert fitted["metadata"]["truncated"]["data"]["total"] == 100 and fitted["data"] == _rows(100)[: len(fitted["data"])]


@pytest.mark.parametrize("data", ["x" * 500, None, 12345])
def test_oversized_data_that_is_no_list_is_refused_not_crashed(monkeypatch, data: Any) -> None:
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 40)
    with pytest.raises(ToolError) as info:
        truncate.fit({"data": data, "metadata": {"notes": ["y" * 100]}})
    assert info.value.code is ToolErrorCode.RESULT_TOO_LARGE


def test_what_cannot_fit_with_every_list_empty_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 50)
    with pytest.raises(ToolError) as info:
        truncate.fit({"data": {"listings": _rows(5)}, "metadata": {"tool": "t", "notes": ["x" * 100]}})
    assert info.value.code is ToolErrorCode.RESULT_TOO_LARGE

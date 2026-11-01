"""POST + GET /runs, GET /runs/options, PUT /runs/limit, GET /runs/{id}[/steps], POST /runs/{id}/cancel (tenancy: references/tenancy.md, Fourth instance)."""

from __future__ import annotations

import dataclasses
import uuid
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from psycopg.rows import dict_row

from api import dependencies as deps
from api import schemas as s
from api import tenant_pool
from api.agents import executor
from api.agents.definition import DefinitionError, InputError

router = APIRouter(prefix="/runs", tags=["agent-runs"], dependencies=[Depends(tenant_pool.require_account_id)])

_NOT_FOUND = "no such run"
_REFUSED_STATUS = {"not_allowlisted": 403, "plan_without_agents": 403, "in_flight_cap": 429}
LIST_LIMIT = 50
# A bound on typing, not a policy: the account's limit is its own to set, and the platform ceiling still holds above it.
MAX_DAILY_LIMIT_USD = Decimal(1_000_000)
_CENT = Decimal("0.01")

_LIGHT = ("id, agent, status, attempts, provider, model, cost_usd::float8 AS cost_usd, stop_reason, error, input, "
          "cancel_requested_at, created_at, started_at, finished_at")
_LIST = f"SELECT {_LIGHT} FROM agent_runs ORDER BY created_at DESC, id DESC LIMIT %s"
_RUN = (f"SELECT {_LIGHT}, result_markdown, cited_property_ids, files, proposals, output, definition_sha, tool_set_sha, "
        "commit_sha FROM agent_runs WHERE id = %s")
# Light enough to poll: a step's question and the model's words, with `summary` standing in for a tool's whole result.
_STEPS = """
SELECT attempt, n, kind, name, summary, payload -> 'input' AS input,
       CASE WHEN kind = 'model' THEN payload #> '{output,text}' END AS text,
       cost_usd::float8 AS cost_usd, duration_ms, created_at
  FROM agent_run_steps WHERE run_id = %s ORDER BY attempt, n"""
_RUN_ACCOUNT = "SELECT account_id FROM agent_runs WHERE id = %s"


def _account_of_run(conn: psycopg.Connection, run_id: int) -> uuid.UUID:
    """The account of a run this caller can read; any other id, a foreign one included, is the same 404."""
    row = conn.execute(_RUN_ACCOUNT, (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return row[0]


def _rows(conn: psycopg.Connection, sql: str, *params: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _limit_usd(body: Any) -> Decimal:
    """`{"daily_limit_usd": n}`, n from 0 to the bound in whole cents; 0 is an amount: no run is claimed. Judged here, not by
    a model: a model's 422 echoes the input, and a NaN anywhere in it makes that answer a 500."""
    value = body["daily_limit_usd"] if isinstance(body, dict) and body.keys() == {"daily_limit_usd"} else None
    usd = None if isinstance(value, bool) or not isinstance(value, (int, float)) else Decimal(str(value))
    if usd is None or not usd.is_finite() or not 0 <= usd <= MAX_DAILY_LIMIT_USD or usd != usd.quantize(_CENT):
        raise HTTPException(status_code=422, detail=f"The limit must be a number of dollars from 0 to {MAX_DAILY_LIMIT_USD}, "
                                                     "with at most two decimals.")
    return usd


@router.get("/options")
def run_options(
    conn: tenant_pool.TenantConn,
    account: uuid.UUID = Depends(tenant_pool.require_account_id),
    claims: dict = Depends(deps.verify_jwt),
) -> dict[str, Any]:
    """What this caller's start form may offer. A refused account reads only why: no definition, no platform state."""
    with deps.open_background_conn() as svc:
        refusal = executor.start_refusal(svc, principal_user_id=uuid.UUID(claims["sub"]), account_id=account)
        if refusal is not None:
            return {"start": {"allowed": False, "reason": refusal}, "queue": None, "agents": [], "limit": None}
        limit = executor.account_limit(svc, conn, account_id=account)
        queue = executor.queue_state(svc, limit)
    agents = [{"name": d.name, "description": d.description, "input_schema": d.input_schema}
              for d in executor.offered_agents()]
    return {"start": {"allowed": True, "reason": None}, "queue": queue, "agents": agents,
            "limit": dataclasses.asdict(limit)}


@router.put("/limit")
def set_limit(
    conn: tenant_pool.TenantConn,
    body: Any = Body(),
    account: uuid.UUID = Depends(tenant_pool.require_account_id),
    claims: dict = Depends(deps.verify_jwt),
) -> dict[str, Any]:
    """This account's daily agent limit, its own to set: no browser grant writes it, so the route is what judges the value."""
    usd = _limit_usd(body)
    with deps.open_background_conn() as svc:
        refusal = executor.start_refusal(svc, principal_user_id=uuid.UUID(claims["sub"]), account_id=account)
        if refusal is not None:
            raise HTTPException(status_code=403, detail=refusal)
        executor.set_account_limit(svc, account_id=account, daily_limit_usd=usd)
        return dataclasses.asdict(executor.account_limit(svc, conn, account_id=account))


@router.post("", status_code=201)
def start_run(
    body: s.AgentRunIn,
    response: Response,
    account: uuid.UUID = Depends(tenant_pool.require_account_id),
    claims: dict = Depends(deps.verify_jwt),
) -> dict[str, Any]:
    try:
        with deps.open_background_conn() as svc:
            run = executor.submit(svc, principal_user_id=uuid.UUID(claims["sub"]), account_id=account, agent=body.agent,
                                  value=body.input, idempotency_key=body.idempotency_key)
    except executor.SubmitRefused as exc:
        raise HTTPException(status_code=_REFUSED_STATUS[exc.reason], detail=str(exc)) from exc
    except (DefinitionError, InputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not run.created:
        response.status_code = 200
    return {"id": run.run_id, "status": run.status, "created": run.created}


@router.get("")
def list_runs(conn: tenant_pool.TenantConn) -> dict[str, Any]:
    rows = _rows(conn, _LIST, LIST_LIMIT + 1)
    return {"data": rows[:LIST_LIMIT], "truncated": len(rows) > LIST_LIMIT}


@router.get("/{run_id}")
def read_run(run_id: int, conn: tenant_pool.TenantConn) -> dict[str, Any]:
    rows = _rows(conn, _RUN, run_id)
    if not rows:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return rows[0]


@router.get("/{run_id}/steps")
def read_run_steps(run_id: int, conn: tenant_pool.TenantConn) -> dict[str, Any]:
    _account_of_run(conn, run_id)
    return {"data": _rows(conn, _STEPS, run_id)}


@router.post("/{run_id}/cancel")
def cancel_run(run_id: int, conn: tenant_pool.TenantConn) -> dict[str, Any]:
    """A queued run ends now, a running one at its next step (`executor.cancel`); an ended one is left as it is."""
    account = _account_of_run(conn, run_id)
    with deps.open_background_conn() as svc:
        status = executor.cancel(svc, run_id=run_id, account_id=account)
    if status is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"id": run_id, "status": status}

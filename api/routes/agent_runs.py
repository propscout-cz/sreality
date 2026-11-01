"""POST + GET /runs, GET /runs/options, GET /runs/{id}[/steps], POST /runs/{id}/cancel (tenancy: references/tenancy.md).

`require_account_id` on the router makes an act-as header a 403 everywhere and resolves the account a start lands on: the
user's oldest membership, the app's one rule, with no picker. A second membership still reads both accounts' runs, since
reads go by RLS alone. Writes and settings go through `api/agents/executor.py` on the service connection.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.rows import dict_row

from api import dependencies as deps
from api import schemas as s
from api import tenant_pool
from api.agents import executor
from api.agents.definition import DefinitionError, InputError

router = APIRouter(prefix="/runs", tags=["agent-runs"], dependencies=[Depends(tenant_pool.require_account_id)])

_NOT_FOUND = "no such run"
_REFUSED_STATUS = {"not_allowlisted": 403, "in_flight_cap": 429}
LIST_LIMIT = 50
_LIST_MAX = 200

_LIGHT = ("id, agent, status, attempts, provider, model, cost_usd::float8 AS cost_usd, stop_reason, error, input, "
          "cancel_requested_at, created_at, started_at, finished_at")
_LIST = f"SELECT {_LIGHT} FROM agent_runs ORDER BY created_at DESC, id DESC LIMIT %s"
_RUN = (f"SELECT {_LIGHT}, result_markdown, cited_property_ids, files, proposals, output, definition_sha, tool_set_sha, "
        "commit_sha FROM agent_runs WHERE id = %s")
# Light enough to poll: a step's question and the model's words, with `summary` standing in for a tool's whole result.
_STEPS = """
SELECT attempt, n, kind, name, summary, payload -> 'input' AS input, payload #> '{output,text}' AS text,
       cost_usd::float8 AS cost_usd, duration_ms, created_at
  FROM agent_run_steps WHERE run_id = %s ORDER BY attempt, n"""
_OWNER = "SELECT account_id FROM agent_runs WHERE id = %s"


def _owner(conn: psycopg.Connection, run_id: int) -> uuid.UUID:
    row = conn.execute(_OWNER, (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return row[0]


def _rows(conn: psycopg.Connection, sql: str, *params: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@router.get("/options")
def run_options(
    account: uuid.UUID = Depends(tenant_pool.require_account_id),
    claims: dict = Depends(deps.verify_jwt),
    svc: Any = Depends(deps.get_db_conn),
) -> dict[str, Any]:
    """What this caller's start form may offer. A refused account reads only why: no definition, no platform state."""
    refusal = executor.start_refusal(svc, principal_user_id=uuid.UUID(claims["sub"]), account_id=account)
    if refusal is not None:
        return {"start": {"allowed": False, "reason": refusal}, "queue": None, "agents": []}
    agents = [{"name": d.name, "description": d.description, "input_schema": d.input_schema}
              for d in executor.offered_agents()]
    return {"start": {"allowed": True, "reason": None}, "queue": executor.queue_state(svc), "agents": agents}


@router.post("", status_code=201)
def start_run(
    body: s.AgentRunIn,
    response: Response,
    account: uuid.UUID = Depends(tenant_pool.require_account_id),
    claims: dict = Depends(deps.verify_jwt),
    svc: Any = Depends(deps.get_db_conn),
) -> dict[str, Any]:
    try:
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
def list_runs(conn: tenant_pool.TenantConn, limit: int = Query(LIST_LIMIT, ge=1, le=_LIST_MAX)) -> dict[str, Any]:
    rows = _rows(conn, _LIST, limit + 1)
    return {"data": rows[:limit], "truncated": len(rows) > limit}


@router.get("/{run_id}")
def read_run(run_id: int, conn: tenant_pool.TenantConn) -> dict[str, Any]:
    rows = _rows(conn, _RUN, run_id)
    if not rows:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return rows[0]


@router.get("/{run_id}/steps")
def read_run_steps(run_id: int, conn: tenant_pool.TenantConn) -> dict[str, Any]:
    _owner(conn, run_id)
    return {"data": _rows(conn, _STEPS, run_id)}


@router.post("/{run_id}/cancel")
def cancel_run(run_id: int, conn: tenant_pool.TenantConn, svc: Any = Depends(deps.get_db_conn)) -> dict[str, Any]:
    """A queued run ends now, a running one at its next step (`executor.cancel`); an ended one is left as it is."""
    status = executor.cancel(svc, run_id=run_id, account_id=_owner(conn, run_id))
    if status is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return {"id": run_id, "status": status}

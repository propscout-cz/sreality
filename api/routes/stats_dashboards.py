"""Browse › Stats dashboards (migration 647, docs/design/saved-dashboards.md): the saved-set instance over
`stats_dashboards`, its read-only shares, and the account's working board.

A NAMED dashboard is a saved set (api/saved_sets.py): `/stats/dashboards` lists the caller's own and those shared
with them, told apart by `owner_email` (null iff the caller's own). The WORKING board is the account's one
unnamed row: no `/stats/dashboards` route reaches it (`row_scope`), the database never lets it be shared, and
`GET`/`PUT /stats/dashboard` read and write it by the account `require_account_id` resolves, so the read and the
write name the same row (the working board is keyed by its account, the partial unique index; RLS still scopes).

Sharing never delegates: the share POST declares require_account_id, its tenant UPDATE is the ownership gate
(shape 1, rowcount 0 -> 404, the working board refused there too), and only then does api/shares.py open the
service role to write the grant and its one notification (tenancy.md, the second instance of that shape).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, ContextManager

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from api import dependencies as deps
from api import saved_sets
from api import schemas as s
from api import shares
from api import tenant_pool

DASHBOARDS = saved_sets.SavedSetKind(
    table="stats_dashboards",
    noun="dashboard",
    id_param="dashboard_id",
    blob_columns=("charts", "filter_spec"),
    create_in=s.CreateStatsDashboardIn,
    update_in=s.UpdateStatsDashboardIn,
    nullable=frozenset({"filter_spec", "color"}),
    projection_extra=(
        ("owner_email", "CASE WHEN t.account_id IN (SELECT current_account_ids()) THEN NULL "
                        f"ELSE {shares.party_label('t.account_id')} END"),
        ("shared_with", "(SELECT coalesce(json_agg(json_build_object("
                        "'account_id', s.recipient_account_id, "
                        f"'email', {shares.party_label('s.recipient_account_id')}) ORDER BY s.created_at), "
                        "'[]'::json) FROM stats_dashboard_shares s WHERE s.dashboard_id = t.id)"),
    ),
    row_scope="name IS NOT NULL",
)
# Every statement the kind runs, for the CI PREPARE sweep (tests/sql_corpus import-resolves `*_SQL`).
DASHBOARD_SQL = saved_sets.statements(DASHBOARDS)

# The ownership gate names the caller's OWN accounts and a NAMED dashboard: a recipient sees the row but
# matches nothing here, and the working board is never shared (647's trigger refuses it as well).
_SHARE_GATE_SQL = (
    "UPDATE stats_dashboards SET updated_at = now() "
    "WHERE id = %s AND account_id IN (SELECT current_account_ids()) AND name IS NOT NULL RETURNING name"
)

_WORKING_BOARD_SQL = "SELECT id, charts, updated_at FROM stats_dashboards WHERE account_id = %s AND name IS NULL"

_SAVE_WORKING_BOARD_SQL = (
    "INSERT INTO stats_dashboards (account_id, charts, updated_at) VALUES (%s, %s::jsonb, now()) "
    "ON CONFLICT (account_id) WHERE name IS NULL DO UPDATE "
    "SET charts = EXCLUDED.charts, updated_at = EXCLUDED.updated_at "
    "RETURNING updated_at"
)

router = APIRouter(tags=["stats-dashboards"])
router.include_router(saved_sets.saved_set_router(DASHBOARDS, "/stats/dashboards"))


def working_board(conn: psycopg.Connection, *, account_id: uuid.UUID) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(_WORKING_BOARD_SQL, (account_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "charts": row[1], "updated_at": row[2].isoformat()}


def save_working_board(
    conn: psycopg.Connection, charts: list[dict[str, Any]], *, account_id: uuid.UUID,
) -> dict[str, Any]:
    """Replace the account's working board with `charts` (inserted on the first save)."""
    with conn.cursor() as cur:
        cur.execute(_SAVE_WORKING_BOARD_SQL, (account_id, json.dumps(charts)))
        row = cur.fetchone()
    assert row is not None
    return {"charts": charts, "updated_at": row[0].isoformat()}


def share_dashboard(
    conn: psycopg.Connection,
    open_identity_conn: Callable[[], ContextManager[psycopg.Connection]],
    dashboard_id: uuid.UUID,
    body: s.ShareDashboardIn,
) -> dict[str, Any]:
    """Share one named dashboard read-only with the account behind a confirmed email (owner only).

    The service-role connection opens only after the tenant gate passed (tenancy.md)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(_SHARE_GATE_SQL, (dashboard_id,))
        gate = cur.fetchone()
    if gate is None:
        raise HTTPException(404, "dashboard not found")
    shares.grant(open_identity_conn, body.email, shares.DashboardShare(dashboard_id, gate[0]))
    return saved_sets.found(DASHBOARDS, saved_sets.get(conn, DASHBOARDS, str(dashboard_id)))


@router.post("/stats/dashboards/{dashboard_id}/shares")
def post_dashboard_share(
    dashboard_id: uuid.UUID,
    body: s.ShareDashboardIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
    _own: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any]:
    return share_dashboard(conn, deps.open_background_conn, dashboard_id, body)


@router.delete("/stats/dashboards/{dashboard_id}/shares/{account_id}")
def delete_dashboard_share(
    dashboard_id: uuid.UUID,
    account_id: uuid.UUID,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    """Revoke (owner) or leave (recipient): the party policy decides which; 0 rows -> 404."""
    return shares.revoke_dashboard(conn, str(dashboard_id), str(account_id))


@router.get("/stats/dashboard")
def get_stats_dashboard(
    conn: Any = Depends(tenant_pool.tenant_conn),
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any] | None:
    """The caller's working board, or null (the SPA then shows its default charts)."""
    return working_board(conn, account_id=account_id)


@router.put("/stats/dashboard")
def put_stats_dashboard(
    body: s.StatsDashboardIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any]:
    """Replace the caller's working board; every chart was validated by the stats registry (422)."""
    return save_working_board(conn, body.charts, account_id=account_id)

"""Persistence for Browse > Stats' saved board (`stats_dashboards`, migration 626).

One row per account holding the ordered list of charts, written WHOLE: the SPA sends
the full list on every change and this upserts it. The board passes
`toolkit.stats_registry.validate_board` first (a refusal is a 422 naming the chart), so
the table only ever holds specs the pivot RPC accepts, normalised to
`{id, view, stat, rows, cols, measure}` (a title or any other key the client sends is
dropped — the title is derived, never stored).

The SPA READS the row itself through RLS (`stats_dashboards_tenant_rw`); this module
is the one writer. It runs on the caller's tenant connection, and the upsert names
its one owner, resolved at the route edge by `tenant_pool.require_account_id`.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from toolkit import stats_registry as sr

if TYPE_CHECKING:
    import psycopg


def save_dashboard(
    conn: "psycopg.Connection", charts: list[Any], *, account_id: uuid.UUID,
) -> dict[str, Any]:
    """Validate, then replace the account's board with `charts` (insert on first save)."""
    try:
        valid = sr.validate_board(charts)
    except sr.SpecError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO stats_dashboards (account_id, charts, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (account_id) DO UPDATE "
            "SET charts = EXCLUDED.charts, updated_at = EXCLUDED.updated_at "
            "RETURNING updated_at",
            (account_id, json.dumps(valid)),
        )
        row = cur.fetchone()
    assert row is not None
    return {"charts": valid, "updated_at": row[0].isoformat()}

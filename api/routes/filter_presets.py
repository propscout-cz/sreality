"""FastAPI routes for Browse saved filter presets.

Mounted under `/filter-presets/*`. Per account (ticket #1761): every route
rides the caller's Supabase JWT and the **tenant pool**
(`tenant_pool.tenant_conn`), so migration 290's `filter_presets_tenant_rw`
policy is the boundary — the static bundle token reaches none of them. Reads,
the reorder and UPDATE/DELETE by id take no account (`current_account_ids()`
is the scope; a row of another account is a 404); `POST` is the one insert and
names exactly one owner via `tenant_pool.require_account_id`. Doctrine:
`.claude/skills/database/references/tenancy.md`.

Straight psycopg CRUD — a preset is a named filter set restored client-side,
so there is no matcher, no background work, and the `filter_spec` is
stored/returned as an opaque dict. Deliberately decoupled from the Watchdog
(`/notifications/*`) surface so a preset can never fire a notification; see
`migrations/151_filter_presets.sql`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field, TypeAdapter

from api import filter_presets as fp
from api import tenant_pool
from api.location_filter import DistrictChip
from api.schemas import TagColor

router = APIRouter(prefix="/filter-presets", tags=["filter-presets"])

_CHIPS = TypeAdapter(list[DistrictChip])


def _coded_chips(spec: dict[str, Any]) -> dict[str, Any]:
    """The blob is stored verbatim, but a place chip in it is a level plus a
    RÚIAN code or the write is a 422. Checked where the SPA reads them
    (`readPresetSpec`: `spec.filters`, else a legacy bare filter set)."""
    filters = spec["filters"] if spec.get("filters") is not None else spec
    if isinstance(filters, dict) and filters.get("districts") is not None:
        _CHIPS.validate_python(filters["districts"])
    return spec


PresetSpec = Annotated[dict[str, Any], AfterValidator(_coded_chips)]


class CreatePresetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    filter_spec: PresetSpec
    # Shared tag palette (api.schemas.TagColor); None = neutral default chip.
    color: TagColor | None = None


class UpdatePresetIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    filter_spec: PresetSpec | None = None
    color: TagColor | None = None


class ReorderPresetsIn(BaseModel):
    # `str`, not `UUID`: every other route here types the id as a plain string
    # and lets the DB's uuid cast be the format guard (a malformed id is a
    # cast error, not a real client scenario — the SPA only sends stored ids).
    ids: list[str] = Field(..., min_length=1)


@router.get("")
def get_presets(
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    rows = fp.list_presets(conn)
    return {"data": rows, "total": len(rows)}


@router.post("")
def post_preset(
    body: CreatePresetIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
    account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
) -> dict[str, Any]:
    return fp.create_preset(
        conn,
        name=body.name,
        filter_spec=body.filter_spec,
        color=body.color,
        account_id=account_id,
    )


# Declared before the `/{preset_id}` routes so PUT /filter-presets/reorder is
# matched here, not captured by `/{preset_id}` with preset_id="reorder".
@router.put("/reorder")
def reorder_presets(
    body: ReorderPresetsIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    rows = fp.reorder_presets(conn, body.ids)
    return {"data": rows, "total": len(rows)}


@router.get("/{preset_id}")
def get_one_preset(
    preset_id: str,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    row = fp.get_preset(conn, preset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return row


@router.put("/{preset_id}")
def put_preset(
    preset_id: str,
    body: UpdatePresetIn,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    # Only pass `color` when the client actually sent it, so an omitted color
    # leaves it untouched while an explicit `null` clears it (fp._UNSET default).
    kwargs: dict[str, Any] = {"name": body.name, "filter_spec": body.filter_spec}
    if "color" in body.model_fields_set:
        kwargs["color"] = body.color
    row = fp.update_preset(conn, preset_id, **kwargs)
    if row is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return row


@router.delete("/{preset_id}")
def delete_preset(
    preset_id: str,
    conn: Any = Depends(tenant_pool.tenant_conn),
) -> dict[str, Any]:
    if not fp.delete_preset(conn, preset_id):
        raise HTTPException(status_code=404, detail="preset not found")
    return {"deleted": True}

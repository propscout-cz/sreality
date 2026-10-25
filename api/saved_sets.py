"""Saved sets (docs/design/saved-dashboards.md): one implementation and router factory for presets and dashboards."""

# No `from __future__ import annotations`: the factory's routes annotate their bodies with the kind's models,
# which FastAPI must read as classes, not as strings it would resolve against this module's globals.

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from api import tenant_pool


@dataclass(frozen=True)
class SavedSetKind:
    """One instance of the primitive: its table, its blobs, and what its rows add to the shared row shape."""

    table: str
    noun: str                            # the 404's subject: "preset not found"
    id_param: str                        # the path parameter: /{preset_id}
    blob_columns: tuple[str, ...]        # jsonb columns, each validated by its type on the body models
    create_in: type[BaseModel]
    update_in: type[BaseModel]
    # A PUT's explicit null clears these; on every other field a null keeps the stored value.
    nullable: frozenset[str] = frozenset({"color"})
    # Blob columns a falsy stored value reads as `{}` from (a preset's filter, as its old reader did).
    empty_on_read: frozenset[str] = frozenset()
    projection_extra: tuple[tuple[str, str], ...] = ()  # (field, SQL over the row alias `t`)
    # Which of the table's rows are this kind's, on EVERY statement (the working board is no saved dashboard).
    row_scope: str = ""
    order_by: str = "t.position ASC, t.created_at DESC"
    # The caller's own rows, where RLS also shows rows shared with them: a new row's position counts only these.
    own_rows: str = ""
    # (constraint, 422 detail): a table CHECK a client can trip with a valid body; any other CHECK is drift (500).
    refusals: tuple[tuple[str, str], ...] = ()
    # The kind whose rows box this kind's (a preset's group, migration 672): each row then carries `group_id`,
    # the list answers with the groups too, and PUT <prefix>/layout rewrites boxes and membership in one go.
    groups: "SavedSetKind | None" = None


def _projection(kind: SavedSetKind) -> str:
    cols = ["t.id", "t.name", *(f"t.{c}" for c in kind.blob_columns),
            "t.created_at", "t.updated_at", "t.position", "t.color", *(["t.group_id"] if kind.groups else [])]
    return ", ".join([*cols, *(f"{sql} AS {field}" for field, sql in kind.projection_extra)])


def _where(kind: SavedSetKind, *conds: str) -> str:
    conds = (*conds, kind.row_scope) if kind.row_scope else conds
    return f" WHERE {' AND '.join(conds)}" if conds else ""


def _list_sql(kind: SavedSetKind) -> str:
    return f"SELECT {_projection(kind)} FROM {kind.table} t{_where(kind)} ORDER BY {kind.order_by}"


def _get_sql(kind: SavedSetKind) -> str:
    return f"SELECT {_projection(kind)} FROM {kind.table} t{_where(kind, 't.id = %s')}"


def _get_for_update_sql(kind: SavedSetKind) -> str:
    # A PUT that changes nothing still answers only for a row the caller may UPDATE (FOR UPDATE applies the
    # UPDATE policy): a shared dashboard's recipient gets the 404 a real change would, and nothing is bumped.
    return f"{_get_sql(kind)} FOR UPDATE OF t"


def _insert_sql(kind: SavedSetKind) -> str:
    # A new row goes last among the caller's OWN rows (RLS scopes MAX; own_rows drops the ones shared with them).
    cols = ", ".join(["name", *kind.blob_columns, "color", "position", "account_id"])
    blobs = "".join(", %s::jsonb" for _ in kind.blob_columns)
    own = (kind.own_rows,) if kind.own_rows else ()
    return (f"INSERT INTO {kind.table} AS t ({cols}) VALUES (%s{blobs}, %s, "
            f"(SELECT COALESCE(MAX(t.position), -1) + 1 FROM {kind.table} t{_where(kind, *own)}), %s) "
            f"RETURNING {_projection(kind)}")


def _update_sql(kind: SavedSetKind, fields: tuple[str, ...]) -> str:
    sets = ", ".join(f"{f} = %s::jsonb" if f in kind.blob_columns else f"{f} = %s" for f in fields)
    return (f"UPDATE {kind.table} t SET {sets}, updated_at = now()"
            f"{_where(kind, 't.id = %s')} RETURNING {_projection(kind)}")


def _delete_sql(kind: SavedSetKind) -> str:
    return f"DELETE FROM {kind.table} t{_where(kind, 't.id = %s')}"


def _reorder_sql(kind: SavedSetKind) -> str:
    return (f"UPDATE {kind.table} t SET position = v.pos "
            "FROM (SELECT id, (ord - 1) AS pos FROM unnest(%s::uuid[]) WITH ORDINALITY AS u(id, ord)) v"
            f"{_where(kind, 't.id = v.id')}")


def _layout_sql(kind: SavedSetKind) -> str:
    # One order over every row, each with its group (NULL = loose); the (group_id, account_id) key refuses a group
    # that is not the row's own account's.
    return (f"UPDATE {kind.table} t SET position = v.pos, group_id = v.group_id "
            "FROM (SELECT id, group_id, (ord - 1) AS pos "
            "FROM unnest(%s::uuid[], %s::uuid[]) WITH ORDINALITY AS u(id, group_id, ord)) v"
            f"{_where(kind, 't.id = v.id')}")


def _fields(kind: SavedSetKind) -> tuple[str, ...]:
    return ("name", *kind.blob_columns, "color")


def statements(kind: SavedSetKind) -> tuple[str, ...]:
    """Every statement the kind runs (its update with every field set), for the CI PREPARE sweep."""
    return (_list_sql(kind), _get_sql(kind), _get_for_update_sql(kind), _insert_sql(kind),
            _update_sql(kind, _fields(kind)), _delete_sql(kind), _reorder_sql(kind),
            *((_layout_sql(kind),) if kind.groups else ()))


def _jsonb(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _row(kind: SavedSetKind, row: tuple[Any, ...]) -> dict[str, Any]:
    fields = ("id", "name", *kind.blob_columns, "created_at", "updated_at", "position", "color",
              *(("group_id",) if kind.groups else ()), *(field for field, _sql in kind.projection_extra))
    out = dict(zip(fields, row))
    out["id"] = str(out["id"])
    if kind.groups:
        out["group_id"] = None if out["group_id"] is None else str(out["group_id"])
    for at in ("created_at", "updated_at"):
        out[at] = out[at].isoformat() if out[at] else ""
    for blob in kind.empty_on_read:
        out[blob] = out[blob] or {}
    return out


def _one(conn: psycopg.Connection, kind: SavedSetKind, sql: str, params: Any) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return None if row is None else _row(kind, row)


def list_rows(conn: psycopg.Connection, kind: SavedSetKind) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(_list_sql(kind))
        return [_row(kind, r) for r in cur.fetchall()]


def get(conn: psycopg.Connection, kind: SavedSetKind, set_id: str) -> dict[str, Any] | None:
    return _one(conn, kind, _get_sql(kind), (set_id,))


def create(
    conn: psycopg.Connection, kind: SavedSetKind, values: dict[str, Any], *, account_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Insert one row at the end of the list; `account_id` is REQUIRED (a dropped one is a TypeError, never NULL)."""
    params = [values["name"], *(_jsonb(values[c]) for c in kind.blob_columns), values.get("color"), account_id]
    row = _one(conn, kind, _insert_sql(kind), params)
    assert row is not None
    return row


def changes(kind: SavedSetKind, body: BaseModel) -> dict[str, Any]:
    """What a PUT changes: every field it sent, except a null where null means "keep" (outside `nullable`)."""
    return {
        f: getattr(body, f) for f in _fields(kind)
        if f in body.model_fields_set and (getattr(body, f) is not None or f in kind.nullable)
    }


def update(
    conn: psycopg.Connection, kind: SavedSetKind, set_id: str, fields: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply `fields` to one row; None when no row the caller may update matched (the write is the gate)."""
    assert set(fields) <= set(_fields(kind)), f"not a {kind.noun} field: {sorted(set(fields) - set(_fields(kind)))}"
    if not fields:
        return _one(conn, kind, _get_for_update_sql(kind), (set_id,))
    params = [_jsonb(v) if f in kind.blob_columns else v for f, v in fields.items()]
    try:
        return _one(conn, kind, _update_sql(kind, tuple(fields)), [*params, set_id])
    except psycopg.errors.CheckViolation as exc:
        detail = dict(kind.refusals).get(exc.diag.constraint_name or "")
        if detail is None:
            raise
        raise HTTPException(status_code=422, detail=detail) from exc


def delete(conn: psycopg.Connection, kind: SavedSetKind, set_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(_delete_sql(kind), (set_id,))
        return cur.rowcount > 0


def reorder(conn: psycopg.Connection, kind: SavedSetKind, ids: list[str]) -> None:
    """Rewrite `position` to the given id order (0 = first); unlisted ids keep theirs."""
    with conn.cursor() as cur:
        cur.execute(_reorder_sql(kind), (ids,))


def listing(conn: psycopg.Connection, kind: SavedSetKind) -> dict[str, Any]:
    """The list route's answer: the rows, and the kind's groups when it has them."""
    rows = list_rows(conn, kind)
    out: dict[str, Any] = {"data": rows, "total": len(rows)}
    if kind.groups:
        out["groups"] = list_rows(conn, kind.groups)
    return out


def layout(
    conn: psycopg.Connection, kind: SavedSetKind, groups: list[str], items: list[tuple[str, str | None]],
) -> dict[str, Any]:
    """Rewrite the boxes' order and every listed row's order and group in one transaction; the list comes back.
    Unlisted ids keep theirs; a group that is not the caller's own is a 422."""
    assert kind.groups is not None, f"{kind.noun} has no groups"
    with conn.cursor() as cur:
        if groups:
            cur.execute(_reorder_sql(kind.groups), (groups,))
        if items:
            try:
                cur.execute(_layout_sql(kind), ([i for i, _ in items], [g for _, g in items]))
            except psycopg.errors.ForeignKeyViolation as exc:
                raise HTTPException(status_code=422, detail=f"{kind.groups.noun} not found") from exc
    return listing(conn, kind)


def row_or_404(kind: SavedSetKind, row: dict[str, Any] | None) -> dict[str, Any]:
    """The row, or the kind's 404."""
    if row is None:
        raise HTTPException(status_code=404, detail=f"{kind.noun} not found")
    return row


class ReorderIn(BaseModel):
    ids: list[uuid.UUID] = Field(..., min_length=1)


class LayoutItem(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID | None = None


class LayoutIn(BaseModel):
    """The whole arrangement after a drag: the boxes in order, and every row in order with its box."""
    groups: list[uuid.UUID] = Field(default_factory=list)
    items: list[LayoutItem] = Field(default_factory=list)


def _runs(*sql: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Tag an endpoint with the SQL it runs: built from the kind, it is no literal that
    tests/api/test_acting_routes_census.py could read off the source."""
    def tag(endpoint: Callable[..., Any]) -> Callable[..., Any]:
        endpoint.runs_sql = sql  # type: ignore[attr-defined]
        return endpoint
    return tag


def saved_set_router(kind: SavedSetKind, prefix: str) -> APIRouter:
    """The kind's six CRUD routes, every one on the tenant connection; POST names its one owner. An id is a
    uuid at the edge, so a malformed one (a hand-edited link) is a 422, never a cast error."""
    router = APIRouter(prefix=prefix, tags=[prefix.strip("/").replace("/", "-")])
    one = f"/{{{kind.id_param}}}"

    lists = (_list_sql(kind), *((_list_sql(kind.groups),) if kind.groups else ()))

    @router.get("")
    @_runs(*lists)
    def list_route(conn: Any = Depends(tenant_pool.tenant_conn)) -> dict[str, Any]:
        return listing(conn, kind)

    @router.post("")
    @_runs(_insert_sql(kind))
    def create_route(
        body: kind.create_in,  # type: ignore[name-defined]
        conn: Any = Depends(tenant_pool.tenant_conn),
        account_id: uuid.UUID = Depends(tenant_pool.require_account_id),
    ) -> dict[str, Any]:
        return create(conn, kind, body.model_dump(), account_id=account_id)

    # Declared before the `/{id}` routes so PUT <prefix>/reorder is matched here, not captured by `/{id}`.
    @router.put("/reorder")
    @_runs(_reorder_sql(kind), *lists)
    def reorder_route(body: ReorderIn, conn: Any = Depends(tenant_pool.tenant_conn)) -> dict[str, Any]:
        reorder(conn, kind, [str(i) for i in body.ids])
        return listing(conn, kind)

    if kind.groups:
        @router.put("/layout")
        @_runs(_reorder_sql(kind.groups), _layout_sql(kind), *lists)
        def layout_route(body: LayoutIn, conn: Any = Depends(tenant_pool.tenant_conn)) -> dict[str, Any]:
            return layout(conn, kind, [str(g) for g in body.groups],
                          [(str(i.id), None if i.group_id is None else str(i.group_id)) for i in body.items])

    @router.get(one)
    @_runs(_get_sql(kind))
    def get_route(
        set_id: uuid.UUID = Path(alias=kind.id_param), conn: Any = Depends(tenant_pool.tenant_conn),
    ) -> dict[str, Any]:
        return row_or_404(kind, get(conn, kind, str(set_id)))

    @router.put(one)
    @_runs(_update_sql(kind, _fields(kind)), _get_for_update_sql(kind))
    def update_route(
        body: kind.update_in,  # type: ignore[name-defined]
        set_id: uuid.UUID = Path(alias=kind.id_param),
        conn: Any = Depends(tenant_pool.tenant_conn),
    ) -> dict[str, Any]:
        return row_or_404(kind, update(conn, kind, str(set_id), changes(kind, body)))

    @router.delete(one)
    @_runs(_delete_sql(kind))
    def delete_route(
        set_id: uuid.UUID = Path(alias=kind.id_param), conn: Any = Depends(tenant_pool.tenant_conn),
    ) -> dict[str, Any]:
        if not delete(conn, kind, str(set_id)):
            raise HTTPException(status_code=404, detail=f"{kind.noun} not found")
        return {"deleted": True}

    return router

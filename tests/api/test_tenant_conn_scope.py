"""The tenant transaction commits BEFORE the response (curation sync W0, docs/design/curation-sync.md § 3.6):
routes and helpers take it as the ONE function-scoped alias `tenant_pool.TenantConn`, which FastAPI closes after
the handler and the serialization but before the response is sent, so a failed COMMIT answers 5xx, never a 200
for a write that rolled back; and only ONE alias, because the dependency cache keys on the scope."""

from __future__ import annotations

import ast
import contextlib
import pathlib
import typing
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi.routing import APIRoute

from api import main as api_main
from api import pipeline as pipeline_module
from api import tenant_pool
from tests.api._jwt import mint
from tests.api._script_conn import ScriptConn

_API = pathlib.Path(__file__).resolve().parents[2] / "api"
_SUB = "11111111-1111-1111-1111-111111111111"
_ACCT = uuid.UUID("22222222-2222-2222-2222-222222222222")
_OWNER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _names_tenant_conn(node: ast.expr) -> bool:
    return (isinstance(node, ast.Name) and node.id == "tenant_conn") or (
        isinstance(node, ast.Attribute) and node.attr == "tenant_conn"
    )


def _is_depends(func: ast.expr) -> bool:
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name in ("Depends", "Security")


def test_census_no_bare_depends_on_tenant_conn_outside_the_alias() -> None:
    found: list[str] = []
    for path in sorted(_API.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        alias_calls: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "TenantConn" for t in node.targets
            ):
                alias_calls |= {id(n) for n in ast.walk(node.value)}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _is_depends(node.func)):
                continue
            target = node.args[0] if node.args else next(
                (k.value for k in node.keywords if k.arg == "dependency"), None
            )
            if target is not None and _names_tenant_conn(target) and id(node) not in alias_calls:
                found.append(f"{path.relative_to(_API.parent)}:{node.lineno}")
    assert not found, f"take `conn: tenant_pool.TenantConn`, never Depends(tenant_conn): {found}"


def _api_routes(routes: list[Any]) -> list[APIRoute]:
    """Flattened: since 0.13x include_router appends one wrapper holding the original router."""
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif nested := getattr(getattr(r, "original_router", None), "routes", None) or getattr(r, "routes", None):
            out.extend(_api_routes(nested))
    return out


def _subs(dependant: Any) -> Iterator[Any]:
    for sub in dependant.dependencies:
        yield sub
        yield from _subs(sub)


def test_every_route_reaches_the_tenant_connection_only_through_the_function_scoped_alias() -> None:
    _type, depends = typing.get_args(tenant_pool.TenantConn)
    assert depends.dependency is tenant_pool.tenant_conn and depends.scope == "function"
    reached: set[str] = set()
    for route in _api_routes(api_main.app.routes):
        for sub in _subs(route.dependant):
            if sub.call is tenant_pool.tenant_conn:
                reached.add(route.path)
                assert sub.scope == "function", f"{route.path}: tenant_conn outside TenantConn"
    # Sentinel: routes of main.py, of an included router and of a saved-set router were walked.
    assert {"/pipeline/cards/{property_id}", "/notifications/dispatches", "/filter-presets"} <= reached


@pytest.fixture()
def moves(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, uuid.UUID]]:
    seen: list[tuple[Any, uuid.UUID]] = []

    def move_card(conn: Any, pid: int, body: Any, *, account_id: uuid.UUID) -> dict[str, Any]:
        seen.append((conn, account_id))
        return {"property_id": pid}

    monkeypatch.setattr(pipeline_module, "move_card", move_card)
    monkeypatch.setattr(pipeline_module, "list_stages", lambda conn: {"data": []})
    return seen


@pytest.fixture()
def http(moves: list[tuple[Any, uuid.UUID]]) -> Any:
    client = TestClient(api_main.app, raise_server_exceptions=False)
    client.headers["Authorization"] = f"Bearer {mint({'sub': _SUB})}"
    return client


def _commit_raises(monkeypatch: pytest.MonkeyPatch, exc: BaseException | None) -> ScriptConn:
    conn = ScriptConn([(lambda sql: "account_members" in sql, [(_ACCT,)])])

    @contextlib.contextmanager
    def fake_transaction(claims: dict, *, act_as: uuid.UUID | None, method: str) -> Iterator[ScriptConn]:
        yield conn
        if exc is not None:  # only on a clean exit, as conn.transaction() commits only then
            raise exc

    monkeypatch.setattr(tenant_pool, "tenant_transaction", fake_transaction)
    return conn


def test_a_card_move_whose_commit_deadlocks_answers_503_db_busy(
    http: Any, moves: list[tuple[Any, uuid.UUID]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _commit_raises(monkeypatch, psycopg.errors.DeadlockDetected("deadlock detected"))
    res = http.patch("/pipeline/cards/7", json={"stage_id": 2})
    assert len(moves) == 1  # the write ran; its commit is what failed
    assert res.status_code == 503 and res.json()["detail"]["code"] == "db_busy"
    assert res.headers["retry-after"] == "5"


@pytest.mark.parametrize("exc, status", [
    (psycopg.errors.DeadlockDetected("deadlock detected"), 503),
    (psycopg.errors.LockNotAvailable("lock timeout"), 503),
    (psycopg.OperationalError("server closed the connection"), 500),
])
def test_a_read_whose_commit_fails_answers_5xx_never_200(
    http: Any, monkeypatch: pytest.MonkeyPatch, exc: BaseException, status: int,
) -> None:
    _commit_raises(monkeypatch, exc)
    assert http.get("/pipeline/stages").status_code == status


def test_a_clean_commit_answers_200(http: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _commit_raises(monkeypatch, None)
    assert http.patch("/pipeline/cards/7", json={"stage_id": 2}).status_code == 200
    assert http.get("/pipeline/stages").status_code == 200


class _CountedConn(ScriptConn):
    closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("act_as", [None, _OWNER])
def test_an_acting_write_resolves_its_account_and_writes_on_one_connection(
    http: Any, moves: list[tuple[Any, uuid.UUID]], monkeypatch: pytest.MonkeyPatch, act_as: uuid.UUID | None,
) -> None:
    opened: list[_CountedConn] = []

    def connect(dsn: str, **_kw: Any) -> _CountedConn:
        conn = _CountedConn([
            (lambda sql: "account_members" in sql, [(_ACCT,)]),
            (lambda sql: "acting_account_ids()" in sql, [(_OWNER,)]),
        ])
        opened.append(conn)
        return conn

    monkeypatch.setenv("TENANT_POOL_DB_URL", "postgresql://tenant-pool.invalid/db")
    monkeypatch.setattr(tenant_pool.psycopg, "connect", connect)
    headers = {"X-Act-As": str(act_as)} if act_as else {}
    res = http.patch("/pipeline/cards/7", json={"stage_id": 2}, headers=headers)
    assert res.status_code == 200, res.text
    assert len(opened) == 1, "require_acting_account_id and the route opened separate transactions"
    (conn,) = opened
    assert moves == [(conn, act_as or _ACCT)]
    resolve = "acting_account_ids()" if act_as else "account_members"
    assert any(resolve in sql for sql, _ in conn.executed)
    assert conn.tx == ["commit"] and conn.closed

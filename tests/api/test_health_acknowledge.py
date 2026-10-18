"""POST /admin/health/acknowledge (migration 619): the operator stops one check's reminders.

Hermetic like tests/api/test_admin_plans.py: require_admin is overridden for the happy
paths and the statement runs against a scripted connection, so the SQL is asserted.
"""

from __future__ import annotations

from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient
pytest.importorskip("jwt")  # PyJWT (api extra), for the real gate below

from api import dependencies as deps
from api import main as api_main
from tests.api._jwt import mint
from tests.api._script_conn import ScriptConn

_ROUTE = "/admin/health/acknowledge"
_ADMIN = {"sub": "00000000-0000-0000-0000-0000000000ad", "app_metadata": {"is_admin": True}}


def _is_acknowledge(q: str) -> bool:
    return q.startswith("UPDATE ops_incidents SET acknowledged_at = now()")


@pytest.fixture()
def make_client():
    def _install(conn: ScriptConn, *, admin: bool = True) -> Any:
        api_main.app.dependency_overrides[deps.get_db_conn] = lambda: conn
        if admin:
            api_main.app.dependency_overrides[deps.require_admin] = lambda: _ADMIN
        return TestClient(api_main.app)

    yield _install
    api_main.app.dependency_overrides.clear()


def test_acknowledge_stamps_the_open_unacknowledged_incidents_of_one_check(make_client) -> None:
    conn = ScriptConn([(_is_acknowledge, [(), ()])])
    res = make_client(conn).post(_ROUTE, json={"key": "worker_liveness"})
    assert res.status_code == 200, res.text
    assert res.json() == {"key": "worker_liveness", "acknowledged": 2}
    [(sql, params)] = conn.executed
    assert "WHERE resolved_at IS NULL AND acknowledged_at IS NULL" in sql
    # starts_with, not LIKE: every key holds `_`, a LIKE wildcard (the view uses the same form).
    assert sql.endswith("AND starts_with(signature, 'cell:' || %s::text || ':')")
    assert params == ("worker_liveness",)
    assert conn.tx == ["commit"]


def test_acknowledging_twice_or_with_nothing_open_reports_zero(make_client) -> None:
    res = make_client(ScriptConn([(_is_acknowledge, [])])).post(_ROUTE, json={"key": "llm_errors"})
    assert res.status_code == 200
    assert res.json() == {"key": "llm_errors", "acknowledged": 0}


@pytest.mark.parametrize("key", [
    "",
    "%",  # would match every open cell incident
    "llm_errors%",
    "llm_errors:openai",  # a subject belongs to the ladder, not to the request
    "LLM_ERRORS",
    "llm-errors",
    "a" * 81,
])
def test_a_key_outside_the_registry_alphabet_is_refused_before_any_write(make_client, key) -> None:
    conn = ScriptConn([(_is_acknowledge, [()])])
    assert make_client(conn).post(_ROUTE, json={"key": key}).status_code == 422
    assert conn.executed == []


def test_only_an_admin_may_acknowledge(make_client) -> None:
    conn = ScriptConn([(_is_acknowledge, [()])])
    client = make_client(conn, admin=False)
    assert client.post(_ROUTE, json={"key": "llm_errors"}).status_code == 401
    member = {"Authorization": f"Bearer {mint({'sub': 'member'})}"}
    assert client.post(_ROUTE, json={"key": "llm_errors"}, headers=member).status_code == 403
    assert conn.executed == []

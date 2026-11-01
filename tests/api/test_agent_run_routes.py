"""The run routes in the route census (#2071): each on the tenant gate, each its caller's own account, none acting."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from api import dependencies as deps  # noqa: E402
from api import tenant_pool  # noqa: E402
from api.main import app  # noqa: E402
from tests.api._jwt import mint  # noqa: E402
from tests.api._routes import flat_routes  # noqa: E402
from tests.api.test_admin_route_coverage import _bucket, _reachable_calls  # noqa: E402

ROUTES = frozenset({
    ("GET", "/runs/options"), ("POST", "/runs"), ("GET", "/runs"), ("GET", "/runs/{run_id}"),
    ("GET", "/runs/{run_id}/steps"), ("POST", "/runs/{run_id}/cancel"),
})


def _live() -> dict[tuple[str, str], object]:
    return {(method, r.path): r for r in flat_routes(app.routes) for method in r.methods
            if r.path == "/runs" or r.path.startswith("/runs/")}


def test_the_census_sees_exactly_the_run_routes() -> None:
    assert set(_live()) == ROUTES


@pytest.mark.parametrize("key", sorted(ROUTES))
def test_each_run_route_is_on_the_tenant_gate_and_never_acts(key: tuple[str, str]) -> None:
    route = _live()[key]
    assert _bucket(route) == "tenant"
    calls = _reachable_calls(route.dependant)
    assert tenant_pool.tenant_conn in calls, "reads go under RLS, and the account comes from the caller's membership"
    assert tenant_pool.require_account_id in calls, "an act-as header is a 403 and a run acts for its own user"
    assert tenant_pool.require_acting_account_id not in calls
    assert deps.get_db_conn not in calls, "the service role opens in the handler, after the gate (tenancy.md)"


@pytest.mark.parametrize("method, path", sorted(ROUTES))
def test_no_token_is_turned_away_before_any_database(method: str, path: str) -> None:
    assert TestClient(app).request(method, path.replace("{run_id}", "1")).status_code == 401


def test_a_garbled_act_as_header_is_a_400_before_any_database() -> None:
    client = TestClient(app, headers={"Authorization": f"Bearer {mint({'sub': 'u'})}", "X-Act-As": "not-a-uuid"})
    assert client.get("/runs").status_code == 400

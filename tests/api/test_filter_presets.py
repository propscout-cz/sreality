"""Tests for the /filter-presets endpoints (Browse saved filter presets).

Hermetic — overrides the tenant connection + JWT (the routes are per account
since ticket #1761) so no real DB is hit, and patches the saved-set persistence
functions (api/saved_sets.py, the presets instance) with in-memory fakes. Pydantic
validation (blank name, missing filter_spec) and the PUT's field rule
(`saved_sets.changes`) run unmodified through the route layer. The account
resolver is stubbed to a distinctive sentinel, never to None (tenancy.md's
#917 rule), and the tests assert the sentinel ARRIVES at the insert.
"""

from __future__ import annotations

from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from fastapi.routing import APIRoute

from api import dependencies as deps
from api import main as api_main
from api import saved_sets as ss
from api import tenant_pool
from api.routes.filter_presets import PRESETS

_SUB = "11111111-1111-1111-1111-111111111111"
_ACCT = "33333333-3333-3333-3333-333333333333"


def _id(n: int) -> str:
    """The n-th preset the fake store mints: ids are uuids at the route edge (a malformed one is a 422)."""
    return f"00000000-0000-0000-0000-{n:012d}"


@pytest.fixture()
def client(monkeypatch):
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": _SUB}
    monkeypatch.setattr(
        tenant_pool, "resolve_account_id", lambda conn, claims: _ACCT,
    )
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def store(monkeypatch):
    """In-memory replacement for the saved-set persistence functions, presets kind only."""
    state: dict[str, Any] = {"presets": {}, "owners": {}, "next_id": 1}

    def _to_dict(pid: str) -> dict[str, Any]:
        return dict(state["presets"][pid])

    def fake_create_preset(conn, kind, values, *, account_id):
        assert kind is PRESETS
        name, filter_spec, color = values["name"], values["filter_spec"], values["color"]
        n = state["next_id"]
        pid = _id(n)
        state["next_id"] += 1
        state["owners"][pid] = account_id
        state["presets"][pid] = {
            "id": pid,
            "name": name,
            "filter_spec": filter_spec,
            "created_at": f"2026-06-03T00:00:0{n}+00:00",
            "updated_at": f"2026-06-03T00:00:0{n}+00:00",
            # Append at end: one past the current max position.
            "position": max(
                (p["position"] for p in state["presets"].values()), default=-1
            )
            + 1,
            "color": color,
        }
        return _to_dict(pid)

    def fake_list_presets(conn, kind):
        assert kind is PRESETS
        rows = [_to_dict(pid) for pid in state["presets"]]
        rows.sort(key=lambda r: (r["position"], r["created_at"]))
        return rows

    def fake_get_preset(conn, kind, pid):
        return _to_dict(pid) if pid in state["presets"] else None

    def fake_update_preset(conn, kind, pid, fields):
        # The route hands over only what changes (saved_sets.changes), so the fake applies it whole.
        if pid not in state["presets"]:
            return None
        state["presets"][pid].update(fields)
        return _to_dict(pid)

    def fake_delete_preset(conn, kind, pid):
        return state["presets"].pop(pid, None) is not None

    def fake_reorder_presets(conn, kind, ids):
        for pos, pid in enumerate(ids):
            if pid in state["presets"]:
                state["presets"][pid]["position"] = pos
        return fake_list_presets(conn, kind)

    monkeypatch.setattr(ss, "create", fake_create_preset)
    monkeypatch.setattr(ss, "list_rows", fake_list_presets)
    monkeypatch.setattr(ss, "get", fake_get_preset)
    monkeypatch.setattr(ss, "update", fake_update_preset)
    monkeypatch.setattr(ss, "delete", fake_delete_preset)
    monkeypatch.setattr(ss, "reorder", fake_reorder_presets)

    return state


SPEC = {"categoryMain": "byt", "categoryType": "prodej", "priceMax": 6_000_000}


def test_create_preset_returns_row(client, store):
    res = client.post("/filter-presets", json={"name": "Praha pod 6M", "filter_spec": SPEC})
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == _id(1)
    assert body["name"] == "Praha pod 6M"
    assert body["filter_spec"] == SPEC


def test_create_preset_defaults_color_null(client, store):
    body = client.post(
        "/filter-presets", json={"name": "p", "filter_spec": SPEC}
    ).json()
    assert body["color"] is None


def test_create_preset_with_color(client, store):
    body = client.post(
        "/filter-presets", json={"name": "p", "filter_spec": SPEC, "color": "plum"}
    ).json()
    assert body["color"] == "plum"


def test_create_preset_invalid_color_422(client, store):
    res = client.post(
        "/filter-presets",
        json={"name": "p", "filter_spec": SPEC, "color": "fuchsia"},
    )
    assert res.status_code == 422


def test_update_preset_sets_and_clears_color(client, store):
    client.post(
        "/filter-presets", json={"name": "p", "filter_spec": SPEC, "color": "sage"}
    )
    # Recolour.
    assert client.put(f"/filter-presets/{_id(1)}", json={"color": "teal"}).json()["color"] == "teal"
    # Explicit null clears it.
    assert client.put(f"/filter-presets/{_id(1)}", json={"color": None}).json()["color"] is None


def test_update_preset_omitting_color_leaves_it(client, store):
    client.post(
        "/filter-presets", json={"name": "p", "filter_spec": SPEC, "color": "brick"}
    )
    # A name-only update must not wipe the colour (omitted != cleared).
    body = client.put(f"/filter-presets/{_id(1)}", json={"name": "renamed"}).json()
    assert body["name"] == "renamed"
    assert body["color"] == "brick"


def test_create_preset_blank_name_422(client, store):
    res = client.post("/filter-presets", json={"name": "", "filter_spec": SPEC})
    assert res.status_code == 422


def test_create_preset_missing_spec_422(client, store):
    res = client.post("/filter-presets", json={"name": "x"})
    assert res.status_code == 422


def test_create_preset_appends_at_end(client, store):
    """New presets append at MAX(position)+1, so list order is save order."""
    for name in ("a", "b", "c"):
        client.post("/filter-presets", json={"name": name, "filter_spec": SPEC})
    body = client.get("/filter-presets").json()
    assert body["total"] == 3
    assert [r["name"] for r in body["data"]] == ["a", "b", "c"]
    assert [r["position"] for r in body["data"]] == [0, 1, 2]


def test_reorder_presets_rewrites_order(client, store):
    for name in ("a", "b", "c"):
        client.post("/filter-presets", json={"name": name, "filter_spec": SPEC})
    # Move "c" (id 3) to the front, then "a" (id 1), then "b" (id 2).
    res = client.put("/filter-presets/reorder", json={"ids": [_id(3), _id(1), _id(2)]})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert [r["name"] for r in body["data"]] == ["c", "a", "b"]
    assert [r["position"] for r in body["data"]] == [0, 1, 2]
    # Order persists on a fresh list.
    assert [r["name"] for r in client.get("/filter-presets").json()["data"]] == [
        "c",
        "a",
        "b",
    ]


def test_reorder_unknown_id_is_noop_for_missing(client, store):
    client.post("/filter-presets", json={"name": "a", "filter_spec": SPEC})
    res = client.put("/filter-presets/reorder", json={"ids": [_id(999), _id(1)]})
    assert res.status_code == 200
    # The one real preset still resolves; the unknown id is ignored.
    assert [r["name"] for r in res.json()["data"]] == ["a"]


def test_reorder_presets_empty_422(client, store):
    assert client.put("/filter-presets/reorder", json={"ids": []}).status_code == 422


def test_get_preset_404_when_missing(client, store):
    assert client.get(f"/filter-presets/{_id(999)}").status_code == 404


def test_update_preset_renames_without_touching_spec(client, store):
    client.post("/filter-presets", json={"name": "old", "filter_spec": SPEC})
    res = client.put(f"/filter-presets/{_id(1)}", json={"name": "new"})
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "new"
    assert body["filter_spec"] == SPEC


def test_update_preset_replaces_spec(client, store):
    client.post("/filter-presets", json={"name": "p", "filter_spec": SPEC})
    new_spec = {"categoryMain": "dum", "categoryType": "prodej"}
    res = client.put(f"/filter-presets/{_id(1)}", json={"filter_spec": new_spec})
    assert res.status_code == 200
    assert res.json()["filter_spec"] == new_spec


_STREET = {"name": "Na Rybníčkách", "context": "Pardubice", "level": "ulice", "id": 666742}


@pytest.mark.parametrize("spec", [
    {"filters": {**SPEC, "districts": [{"name": "Brno", "context": None}]}, "sort": None},
    {"filters": {**SPEC, "districts": [_STREET, {"name": "Lipová", "level": "locality",
                                                  "id": 554782}]}},
    {"filters": {"districts": [{"name": "Praha", "level": "obec", "id": -1}]}},
    {**SPEC, "districts": [{"name": "Brno"}]},
    {"filters": {"districts": [{**_STREET, "id": "666742"}]}},
    {"filters": {"districts": [{**_STREET, "id": True}]}},
    {"filters": {"districts": [{**_STREET, "excluded": "true"}]}},
], ids=["name only", "stale locality beside a coded chip", "the old sentinel", "legacy bare blob",
        "id as a string", "id as a bool", "excluded as a string"])
def test_a_preset_holding_a_chip_without_a_code_is_a_422_and_writes_nothing(client, store, spec):
    """A chip is a level plus a RÚIAN code or it cannot exist — on a preset too,
    whose blob is stored verbatim, so strictly: a coercible "666742" or `true`
    would be stored as written. The legacy bare shape cannot bypass it."""
    assert client.post("/filter-presets", json={"name": "p", "filter_spec": spec}).status_code == 422
    assert store["presets"] == {}
    client.post("/filter-presets", json={"name": "p", "filter_spec": SPEC})
    assert client.put(f"/filter-presets/{_id(1)}", json={"filter_spec": spec}).status_code == 422
    assert store["presets"][_id(1)]["filter_spec"] == SPEC


def test_a_preset_with_coded_chips_is_stored_unchanged(client, store):
    spec = {"filters": {**SPEC, "districts": [_STREET, {**_STREET, "id": 447358,
                                                        "excluded": True}]}, "sort": "-price_czk"}
    res = client.post("/filter-presets", json={"name": "p", "filter_spec": spec})
    assert res.status_code == 200
    assert res.json()["filter_spec"] == spec
    assert client.put(f"/filter-presets/{_id(1)}", json={"filter_spec": spec}).json()["filter_spec"] == spec


@pytest.mark.parametrize("spec", [SPEC, {"filters": SPEC, "sort": None},
                                  {"filters": {**SPEC, "districts": None}}])
def test_a_preset_without_chips_is_untouched(client, store, spec):
    res = client.post("/filter-presets", json={"name": "p", "filter_spec": spec})
    assert res.status_code == 200
    assert res.json()["filter_spec"] == spec


def test_update_preset_404_when_missing(client, store):
    assert client.put(f"/filter-presets/{_id(999)}", json={"name": "x"}).status_code == 404


@pytest.mark.parametrize(("method", "path", "body"), [
    ("get", "/filter-presets/1", None),
    ("put", "/filter-presets/not-a-uuid", {"name": "x"}),
    ("delete", "/filter-presets/1", None),
    ("put", "/filter-presets/reorder", {"ids": ["1"]}),
])
def test_a_malformed_id_is_a_422_not_a_cast_error(client, store, method, path, body):
    """A hand-edited link must not reach the database's uuid cast (a 500)."""
    call = getattr(client, method)
    assert (call(path, json=body) if body is not None else call(path)).status_code == 422


def test_a_stored_blob_that_is_not_an_object_reads_as_empty():
    """PostgREST can write a JSON null into the NOT NULL column; the SPA reads `spec.filters`."""
    row = (_id(1), "p", None, None, None, 0, None)
    assert ss._row(PRESETS, row)["filter_spec"] == {}


def test_delete_preset(client, store):
    client.post("/filter-presets", json={"name": "p", "filter_spec": SPEC})
    assert client.delete(f"/filter-presets/{_id(1)}").json() == {"deleted": True}
    assert client.delete(f"/filter-presets/{_id(1)}").status_code == 404


# --- per account (ticket #1761) ---------------------------------------------


def test_create_preset_stamps_the_resolved_account(client, store):
    """The one insert names its owner: the account require_account_id resolved."""
    body = client.post("/filter-presets", json={"name": "p", "filter_spec": SPEC}).json()
    assert store["owners"][body["id"]] == _ACCT
    # The response shape is unchanged: the owner is not echoed back.
    assert "account_id" not in body


def test_create_preset_without_an_account_is_400(client, store, monkeypatch):
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda conn, claims: None)
    res = client.post("/filter-presets", json={"name": "p", "filter_spec": SPEC})
    assert res.status_code == 400
    assert res.json()["detail"] == "no account for caller"
    assert store["presets"] == {}


def _preset_routes() -> list[APIRoute]:
    routes = [
        r
        for r in api_main.app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/filter-presets")
    ]
    if not routes:  # FastAPI >= 0.13x wraps an included router
        for r in api_main.app.routes:
            nested = getattr(getattr(r, "original_router", None), "routes", None) or []
            routes += [
                n for n in nested
                if isinstance(n, APIRoute) and n.path.startswith("/filter-presets")
            ]
    return routes


def _calls(route: APIRoute) -> set:
    out, stack = set(), list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is not None:
            out.add(d.call)
        stack.extend(d.dependencies)
    return out


def test_every_preset_route_rides_the_tenant_connection():
    """No preset route accepts the static bundle token or the service-role
    connection: it proves only 'loaded the SPA', and RLS is the boundary."""
    routes = _preset_routes()
    assert len(routes) == 6, [f"{sorted(r.methods)} {r.path}" for r in routes]
    for r in routes:
        calls = _calls(r)
        label = f"{sorted(r.methods)} {r.path}"
        assert tenant_pool.tenant_conn in calls, f"{label} left the tenant connection"
        assert deps.require_token not in calls, f"{label} accepts the bundle token again"
        assert deps.get_db_conn not in calls, f"{label} reads on the service role again"


class _Cursor:
    def __init__(self, log: list) -> None:
        self.log = log
        self.rowcount = 1

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.log.append((sql, params))

    def fetchone(self) -> tuple:
        return ("p-1", "p", SPEC, None, None, 0, None)


class _Conn:
    def __init__(self) -> None:
        self.log: list = []

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)


def test_create_preset_sql_binds_the_account():
    """The persistence layer writes the owner into the row, and takes it as a
    REQUIRED keyword (no default: a dropped argument is a TypeError, not a NULL)."""
    import inspect

    param = inspect.signature(ss.create).parameters["account_id"]
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY

    conn = _Conn()
    ss.create(conn, PRESETS, {"name": "p", "filter_spec": SPEC, "color": None}, account_id=_ACCT)
    sql, params = conn.log[0]
    assert "account_id" in sql.split("VALUES")[0]
    assert params[-1] == _ACCT


def test_reads_and_writes_by_id_carry_no_account_predicate():
    """Doctrine shape 1: on the tenant connection RLS alone scopes a read and an
    UPDATE/DELETE by id; an explicit account predicate would be a second
    definition of the caller (the #917 bug), not a gate."""
    import inspect

    for fn in (ss.list_rows, ss.get, ss.update, ss.delete, ss.reorder, ss.changes):
        assert "account_id" not in inspect.getsource(fn), fn.__name__
    for sql in (ss._list_sql(PRESETS), ss._get_sql(PRESETS), ss._get_for_update_sql(PRESETS), ss._delete_sql(PRESETS),
                ss._update_sql(PRESETS, ("name", "filter_spec", "color")), ss._reorder_sql(PRESETS)):
        assert "account_id" not in sql, sql

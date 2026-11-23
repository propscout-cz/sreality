"""The address picker's write route (location W3b) — hermetic (no DB/HTTP), test_broker_routes idiom.

The router is admin-gated at the APIRouter level; overriding `verify_jwt` only means every 403
below is the REAL `require_admin`'s decision. The producer is monkeypatched, so these tests pin
the HTTP layer: gating, body shape, the caller's `sub` and error mapping.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from location_data import operator_corrections as oc


def _client(claims: dict):
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: claims
    return TestClient(api_main.app)


@pytest.fixture()
def client():
    yield _client({"sub": "u-1", "email": "user@example.com"})
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def admin_client():
    yield _client({"sub": "u-0", "is_admin": True})
    api_main.app.dependency_overrides.clear()


_PICK = {"property_id": 138994, "pick": {"level": "adresni_misto", "code": 24252301}}


def _producer(monkeypatch) -> dict:
    """The producer faked: what the route handed it; it answers the shape it builds."""
    seen: dict = {}

    def submit(conn, *, property_id, pick, by):
        seen.update(property_id=property_id, pick=pick, by=by)
        return {"property_id": property_id, "held": None if pick is None else True}

    monkeypatch.setattr(oc, "submit_pick", submit)
    return seen


def test_the_location_routes_403_a_plain_user(client):
    assert client.post("/location/corrections", json=_PICK).status_code == 403


def test_the_deleted_route_families_are_gone(admin_client):
    """A route that 404s for the ADMIN is the only proof the surface is actually gone.

    W1-b: `/quality/w1v-gate`, `/sample/{source}/score-shadow`. W2-b: the frozen labelled
    samples and the compare bench. W1: the two aggregates, folded into the gauge's stored cells.
    W3b: the listing inspector, by id and by native id (the address picker replaced it)."""
    for path in (
        "/location/quality/summary",
        "/location/quality/source/bezrealitky",
        "/location/quality/w1v-gate",
        "/location/sample/bezrealitky",
        "/location/sample/bezrealitky/score",
        "/location/sample/bezrealitky/score-shadow",
        "/location/compare/scope",
        "/location/compare/map?west=14&south=50&east=14.5&north=50.5",
        "/location/listing/1",
        "/location/listing/by-native/bezrealitky/abc",
    ):
        assert admin_client.get(path).status_code == 404, path
    assert admin_client.post(
        "/location/sample/bezrealitky/labels", json={"listing_id": 1, "labels": {}}
    ).status_code == 404


def test_the_route_hands_the_producer_the_pick_and_the_callers_sub(admin_client, monkeypatch):
    """The producer writes, resolves and reads back (`submit_pick`); the route maps it to HTTP."""
    seen = _producer(monkeypatch)
    res = admin_client.post("/location/corrections", json=_PICK)
    assert res.status_code == 200, res.text
    assert seen == {"property_id": 138994, "pick": ("adresni_misto", 24252301), "by": "u-0"}
    assert res.json() == {"data": {"property_id": 138994, "held": True}}


def test_a_clear_is_an_explicit_null(admin_client, monkeypatch):
    seen = _producer(monkeypatch)
    body = admin_client.post("/location/corrections",
                             json={"property_id": 7, "pick": None}).json()["data"]
    assert seen["pick"] is None and body["held"] is None


def test_errors_map_to_404_and_422(admin_client, monkeypatch):
    def raising(exc):
        def submit(conn, **kw):
            raise exc
        return submit

    monkeypatch.setattr(oc, "submit_pick", raising(oc.UnknownPropertyError("property 7")))
    assert admin_client.post("/location/corrections", json=_PICK).status_code == 404
    monkeypatch.setattr(oc, "submit_pick", raising(oc.CorrectionError("ulice 5 is not current")))
    assert admin_client.post("/location/corrections", json=_PICK).status_code == 422


@pytest.mark.parametrize("body", [
    {"property_id": 7, "pick": {"level": "cast_obce", "code": 5}},     # L3: refused until W5
    {"property_id": 7, "pick": {"level": "obec", "code": 0}},
    {"property_id": 7},                                                # a clear is explicit
    {"property_id": 0, "pick": None},
    {"listing_id": 7, "claim_type": "street_name", "value_text": "Dlouhá"},  # the old body
])
def test_a_body_outside_the_pick_shape_is_a_422(admin_client, monkeypatch, body):
    monkeypatch.setattr(oc, "submit_pick", lambda conn, **kw: pytest.fail("reached the producer"))
    assert admin_client.post("/location/corrections", json=body).status_code == 422

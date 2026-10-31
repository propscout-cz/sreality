"""The address picker's write route (location W3b) — hermetic (no DB/HTTP), test_broker_routes idiom.

The router is admin-gated at the APIRouter level; overriding `verify_jwt` only means every 403
below is the REAL `require_admin`'s decision. The producer is monkeypatched, so these tests pin
the HTTP layer: gating, body shape, error mapping, which ad resolves and how `held` is read.
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
_ADS = [{"listing_id": 11, "source": "sreality", "location_ad": False},
        {"listing_id": 156144, "source": "bezrealitky", "location_ad": True}]


def _producer(monkeypatch, *, location: dict | None, resolved: bool = True) -> dict:
    """The producer faked: what it was handed, and which ads resolved / were read."""
    seen: dict = {"resolved": [], "read": []}

    def submit(conn, *, property_id, pick, by):
        seen.update(property_id=property_id, pick=pick, by=by)
        return {"property_id": property_id, "ads": _ADS, "written": 2, "enqueued": 2,
                "pick": None if pick is None else {"level": pick[0], "code": pick[1]}}

    monkeypatch.setattr(oc, "submit_pick", submit)
    monkeypatch.setattr(oc, "resolve_now",
                        lambda conn, listing_id: seen["resolved"].append(listing_id) or resolved)
    monkeypatch.setattr(oc, "read_projection",
                        lambda conn, listing_id: seen["read"].append(listing_id) or location)
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


def test_a_pick_resolves_the_location_ad_only_and_says_whether_it_held(admin_client, monkeypatch):
    seen = _producer(monkeypatch, location={"ruian_adm_kod": 24252301, "label": "Týnská 7, Praha"})
    res = admin_client.post("/location/corrections", json=_PICK)
    assert res.status_code == 200, res.text
    body = res.json()["data"]
    assert (seen["property_id"], seen["pick"], seen["by"]) == (138994, ("adresni_misto", 24252301), "u-0")
    assert seen["resolved"] == seen["read"] == [156144]
    assert (body["resolved"], body["held"], body["location"]["label"]) == (True, True, "Týnská 7, Praha")


def test_a_pick_portal_evidence_defeats_is_saved_but_not_held(admin_client, monkeypatch):
    """Limit L1: a portal PSČ or kód ADM can defeat a street pick; the row then binds another
    code and the response says so, so the picker asks for an address."""
    _producer(monkeypatch, location={"ulice_kod": 1, "label": "Jinde"}, resolved=False)
    body = admin_client.post("/location/corrections", json={
        "property_id": 7, "pick": {"level": "ulice", "code": 472786}}).json()["data"]
    assert (body["resolved"], body["held"]) == (False, False)


def test_a_clear_holds_nothing(admin_client, monkeypatch):
    seen = _producer(monkeypatch, location={"ruian_adm_kod": None})
    body = admin_client.post("/location/corrections",
                             json={"property_id": 7, "pick": None}).json()["data"]
    assert seen["pick"] is None and body["held"] is None and body["pick"] is None


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

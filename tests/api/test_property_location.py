"""Tests for the phone's Poloha route: GET /properties/{id}/location (the location ad's
point, granularity, label and note). Hermetic: the DB is a fake cursor."""

from __future__ import annotations

from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api import property_location as location
from tests.api._jwt import mint

RANKS = {"obec": 40, "cast_obce_or_quarter": 50, "street": 60, "address_point": 100, "okres": 30}


def _row(granularity: str | None = "address_point", **over: Any) -> dict[str, Any]:
    row = {
        "lat": 50.105123456,
        "lon": 14.5,
        "granularity": granularity,
        "granularity_rank": RANKS.get(granularity) if granularity else None,
        "street_rank": 60,
        "quarter_rank": 50,
        "obec_rank": 40,
        "country_status": "cz",
        "label": "Freyova 534/2, Praha",
    }
    row.update(over)
    return row


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.conn.statements.append((" ".join(sql.split()), params))

    def fetchone(self) -> dict[str, Any] | None:
        return self.conn.row


class _Conn:
    def __init__(self) -> None:
        self.row: dict[str, Any] | None = _row()
        self.statements: list[tuple[str, tuple]] = []

    def cursor(self, row_factory: Any = None) -> _Cursor:
        return _Cursor(self)


AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}


@pytest.fixture()
def conn():
    c = _Conn()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: c
    yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(api_main.app)


@pytest.mark.parametrize(
    ("granularity", "level", "note"),
    [
        ("address_point", "street", None),
        ("street", "street", None),
        ("cast_obce_or_quarter", "quarter", "Poloha jen na úrovni části obce"),
        ("obec", "town", "Poloha jen na úrovni obce"),
        # A district point is not a town: it used to read "Poloha jen na úrovni obce".
        ("okres", "coarse", "Poloha jen přibližná, hrubší než obec"),
    ],
)
def test_location_answers_point_label_and_note_by_granularity(client, conn, granularity, level, note):
    conn.row = _row(granularity)
    res = client.get("/properties/7/location", headers=AUTH)
    assert res.status_code == 200
    assert res.json() == {
        "lat": 50.105123456,
        "lon": 14.5,
        "granularity": granularity,
        "granularity_rank": RANKS[granularity],
        "precision": level,
        "country_status": "cz",
        "label": "Freyova 534/2, Praha",
        "note": note,
    }


def test_location_reads_the_location_ad_and_compares_by_rank(client, conn):
    client.get("/properties/7/location", headers=AUTH)
    sql, params = conn.statements[0]
    assert params == (7,)
    assert "listing_location ll on ll.listing_id = p.location_listing_ref_id" in sql
    assert "repr_listing_ref_id" not in sql
    assert "location_display_label(" in sql
    assert "location_granularity_rank" in sql


def test_precision_steps_follow_the_rank_table_not_fixed_numbers():
    ranks = {"street_rank": 70, "quarter_rank": 55, "obec_rank": 45}
    assert location.precision(70, **ranks) == "street"
    assert location.precision(60, **ranks) == "quarter"
    assert location.precision(45, **ranks) == "town"
    assert location.precision(44, **ranks) == "coarse"
    assert location.precision(None, **ranks) == "coarse"


def test_location_without_coordinates_is_404_no_location(client, conn):
    conn.row = _row(None, lat=None, lon=None)
    res = client.get("/properties/7/location", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "no_location"


def test_unknown_property_is_404_not_found(client, conn):
    conn.row = None
    res = client.get("/properties/7/location", headers=AUTH)
    assert res.status_code == 404
    assert res.json()["detail"]["reason"] == "not_found"


def test_the_location_route_needs_a_signed_in_caller(client, conn, monkeypatch):
    monkeypatch.delenv("API_AUTH_OPTIONAL", raising=False)  # tests/conftest opens it for dev
    assert client.get("/properties/7/location", headers=AUTH).status_code == 200
    assert client.get("/properties/7/location").status_code == 401


"""GET /properties/{id}/building, hermetic: the register record's shape, a property without a building, an
unknown property. The SQL itself is PREPAREd against the replayed schema by the SQL sweep."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from tests.api._jwt import mint

AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}


def _label(code: int, label: str, unknown: bool = False) -> dict[str, Any]:
    return {"code": code, "label": label, "unknown": unknown}


def _row(**over: Any) -> dict[str, Any]:
    row = {
        "code": 22948236, "iskn_building_id": 470801805, "house_numbers": [564, 565], "typ_so": "č.p.",
        "completed_on": datetime.date(1990, 12, 31), "completed_from": 1981, "completed_to": 1990,
        "floors": 8, "dwelling_count": 46, "built_up_area_m2": 612, "floor_area_m2": None,
        "enclosed_volume_m3": None, "valid_from": datetime.date(2023, 12, 18),
        "source_date": datetime.date(2026, 10, 31), "entrances": 2,
        "usage": _label(6, "bytový dům"), "construction": _label(4, "Stěnové panely"), "lift": _label(1, "S výtahem"),
        "heating": _label(2, "Centrální dálkové (kotel mimo stavbu)"), "water": _label(1, "S vodovodem"),
        "sewer": _label(1, "Přípoj na kanalizační síť"), "gas": _label(9, "Nezjištěno", True),
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


@pytest.fixture()
def conn():
    c = _Conn()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: c
    yield c
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(api_main.app)


def test_the_building_is_what_the_register_states(client, conn):
    building = client.get("/properties/7/building", headers=AUTH).json()["building"]
    assert building["code"] == 22948236 and building["house_numbers"] == [564, 565]
    assert building["completed"] == {"on": "1990-12-31", "from": 1981, "to": 1990}
    assert "protected" not in building
    assert (building["floors"], building["dwellings"], building["entrances"]) == (8, 46, 2)
    assert building["floor_area_m2"] is None
    assert building["gas"] == {"code": 9, "label": "Nezjištěno", "unknown": True}
    assert building["source_date"] == "2026-10-31"


def test_the_read_goes_through_the_location_ads_door(client, conn):
    client.get("/properties/7/building", headers=AUTH)
    sql, params = conn.statements[0]
    assert params == (7,)
    assert "listing_location ll on ll.listing_id = p.location_listing_ref_id" in sql
    assert "ap.kod_adm = ll.ruian_adm_kod" in sql
    assert "b.code = ap.stavebni_objekt_code and b.valid_to is null" in sql


def test_a_register_silent_on_completion_gives_no_period(client, conn):
    conn.row = _row(completed_on=None, completed_from=None, completed_to=None)
    assert client.get("/properties/7/building", headers=AUTH).json()["building"]["completed"] is None


def test_an_open_ended_period_keeps_its_gap(client, conn):
    conn.row = _row(completed_on=datetime.date(1919, 12, 31), completed_from=None, completed_to=1919)
    assert client.get("/properties/7/building", headers=AUTH).json()["building"]["completed"] == {
        "on": "1919-12-31", "from": None, "to": 1919}


def test_a_property_whose_door_names_no_building_has_none(client, conn):
    conn.row = dict.fromkeys(_row())
    res = client.get("/properties/7/building", headers=AUTH)
    assert res.status_code == 200 and res.json() == {"building": None}


def test_an_unknown_property_is_404(client, conn):
    conn.row = None
    assert client.get("/properties/7/building", headers=AUTH).status_code == 404


"""Valuo valuations (api/valuo.py, api/routes/valuo.py): the mapping and its vocabulary census, the
careful defaults (operator ruling 2026-10-19), the refusals, and the save-before-call sequence over an
in-memory `valuo_valuations`. Hermetic: no DB, and Valuo is a fake (`valuo_client.calculate`), so no
call is ever paid for. The SQL itself runs against the replayed schema in
tests/test_valuo_valuations_live.py."""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import psycopg
import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api import tenant_pool, valuo, valuo_client
from tests.api._jwt import mint
from toolkit.filter_registry import COLUMN_CANONICAL_VALUES, SUBTYPE_OPTIONS

ACCOUNT = "11111111-1111-1111-1111-111111111111"
AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}
TOKEN = "sandbox-secret-token"

FLAT = {
    "listing_id": 42, "category_main": "byt", "category_type": "prodej", "subtype": None,
    "disposition": "2+kk", "area_m2": Decimal("54.5"), "estate_area": None, "garden_area": None,
    "floor": 3, "total_floors": 6, "condition": "po_rekonstrukci", "building_type": "cihla",
    "ownership": "osobni", "energy_rating": "C", "has_balcony": True, "cellar": True, "garage": False,
    "parking_lots": None,
}
EMPTY = {k: None for k in FLAT} | {"listing_id": 43}
HOUSE = EMPTY | {"category_main": "dum", "category_type": "prodej", "subtype": "vicegeneracni_dum",
                 "area_m2": 140, "estate_area": 800, "condition": "novostavba"}
LAND = EMPTY | {"category_main": "pozemek", "category_type": "prodej", "area_m2": Decimal("1200")}
OFFICE = EMPTY | {"category_main": "komercni", "category_type": "pronajem", "subtype": "kancelar",
                  "disposition": "2+kk", "area_m2": 180, "condition": "dobry", "floor": 2}
STREET = {
    "lat": 50.08, "lon": 14.42, "granularity": "address_point", "granularity_rank": 100,
    "precision": "street", "country_status": "cz", "uncertainty_radius_m": 5.0,
    "label": "Vinohradská 12, Praha", "zoom": 16, "note": None,
}
TOWN = STREET | {"granularity": "obec", "granularity_rank": 40, "precision": "town",
                 "uncertainty_radius_m": 2500.0, "note": "Poloha jen na úrovni obce"}


def _build(subject: dict[str, Any], location: dict[str, Any] = STREET,
           overrides: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    return valuo.build_request(subject, location, overrides or {})


def _today() -> str:
    return datetime.now(valuo._PRAGUE).date().isoformat()


# --- the vocabulary -------------------------------------------------------------------------------


def _params(column: str, raw: str) -> list[str]:
    params, _table = valuo.MAPS[column]
    if isinstance(params, str):
        return [params]
    group = next(o.group for o in SUBTYPE_OPTIONS if o.value == raw)
    return [params[{"dum": "house", "komercni": "commercial"}[group]]]


def test_every_canonical_value_of_a_mapped_column_is_ruled():
    """A new vocabulary member must be mapped or explicitly not sent before CI goes green."""
    for column, (_params_, table) in valuo.MAPS.items():
        assert set(table) == set(COLUMN_CANONICAL_VALUES[column]), column
        for raw, (value, rule) in table.items():
            assert rule in (valuo._DIRECT, valuo.INFERRED, valuo.ASSUMED, valuo.MISSING)
            for param in _params(column, raw):
                assert value is None or value in dict(valuo._BY_NAME[param].options), (column, raw, value)
    assert set(valuo.NEW_BUILDING) <= set(COLUMN_CANONICAL_VALUES["condition"])
    assert set(valuo.NEW_BUILDING.values()) <= {valuo.AD, valuo.INFERRED}


# Valuo's v3 request schema per endpoint (docs.valuo.com 2026-10-20 + sandbox probes): a field outside it is
# silently dropped, so none is offered. house's garage_area / other_area are left out: no column of ours feeds them.
V3_SCHEMA = {
    "flat": {"floor_area", "rating", "local_type", "ownership", "construction", "floor", "total_floors",
             "balcony_area", "cellar_area", "garden_area", "garages", "parking_spaces", "new_building", "as_of"},
    "house": {"floor_area", "lot_area", "rating", "house_type", "construction", "energy_performance", "cellar_area",
              "garage_area", "other_area", "garages", "parking_spaces", "new_building", "as_of"},
    "commercial": {"floor_area", "lot_area", "rating", "commercial_type", "as_of"},
    "land": {"lot_area", "land_type", "rating", "as_of"},
}
NO_SOURCE = {"garage_area", "other_area"}


@pytest.mark.parametrize("ptype", sorted(V3_SCHEMA))
def test_each_type_offers_exactly_its_v3_schema(ptype):
    names = {f.name for f in valuo.fields_for(ptype)}
    assert names == (V3_SCHEMA[ptype] - NO_SOURCE) | {"property_type", "place", "kind"}


def test_the_v3_enums_are_valuos_own():
    def values(name: str) -> set[str]:
        return {v for v, _lab in valuo._BY_NAME[name].options}
    assert values("ownership") == {"private", "cooperative", "council", "other"}
    assert values("construction") == {"brick", "wood", "stone", "montage", "panel", "mixed", "low_energy", "other"}
    assert values("house_type") == {"family_house", "cottage", "villa", "hut", "other"}
    assert values("energy_performance") == set("ABCDEFG")
    assert values("commercial_type") == {"other", "office", "storage", "retail", "production", "restaurant",
                                         "accommodation", "agriculture", "apartment_building", "apartment",
                                         "medical_office"}
    assert values("land_type") == {"building", "field", "garden", "meadow", "forest", "other", "commercial"}
    assert values("rating") == {"bad", "nothing_much", "good", "very_good", "excellent"}
    assert values("local_type") == {f"{n}+{k}" for n in range(1, 8) for k in ("1", "kk")} | {"1+0", "jiné"}
    assert dict(valuo._BY_NAME["rating"].options)["very_good"] == "Velmi dobrý"  # operator ruling 9, 2026-10-20
    names = [f.name for f in valuo.FIELDS]
    assert len(names) == len(set(names))
    assert all(f.label and all(lab for _v, lab in f.options) for f in valuo.FIELDS)


@pytest.mark.parametrize("ptype", sorted(V3_SCHEMA))
def test_a_new_building_is_asked_up_front_where_valuo_takes_it(ptype):
    specs = {f.name: f.spec(ptype) for f in valuo.fields_for(ptype)}
    if ptype in ("flat", "house"):
        assert specs["new_building"]["group"] == "basic" and not specs["new_building"]["required"]
    else:
        assert "new_building" not in specs
    assert specs["rating"]["required"]


def test_excellent_is_never_a_default():
    for _param, table in valuo.MAPS.values():
        assert ("excellent", valuo._DIRECT) not in table.values()
        assert all(value != "excellent" for value, _rule in table.values())


# --- the mapping ----------------------------------------------------------------------------------


def test_a_flat_maps_from_its_ad_and_its_property():
    request, prov = _build(FLAT)
    assert request == {
        "property_type": "flat", "place": "50.080000, 14.420000", "kind": "sale", "floor_area": 54.5,
        "rating": "very_good", "as_of": _today(), "local_type": "2+kk", "ownership": "private",
        "construction": "brick", "floor": 3, "total_floors": 6, "country_code": "cs",
    }
    assert list(request) == [f.name for f in valuo.FIELDS if f.name in request] + ["country_code"]
    assert "country_code" not in prov and "energy_performance" not in prov  # a flat's class: no v3 field
    assert prov["rating"] == {"class": "ad", "from": {"column": "condition", "value": "po_rekonstrukci"},
                              "note": "Po rekonstrukci → Velmi dobrý"}
    assert prov["construction"] == {"class": "property", "from": {"column": "building_type", "value": "cihla"},
                                    "note": "Cihla → Cihlová"}
    # The same word on both sides needs no note (Byt, Prodej, 2+kk).
    for name in ("property_type", "kind", "local_type"):
        assert prov[name]["note"] is None, name
    assert prov["place"] == {"class": "ad", "from": {"column": "listing_location.geom",
                                                     "value": "50.080000, 14.420000"},
                             "note": "Vinohradská 12, Praha"}
    assert prov["new_building"] == {"class": "missing", "from": None, "note": None}
    assert prov["garages"] == {"class": "missing", "from": {"column": "garage", "value": False},
                               "note": "Inzerát garáž neuvádí jako ano — neposílá se."}
    assert prov["as_of"]["class"] == "assumed"
    assert prov["balcony_area"] == {"class": "missing", "from": {"column": "has_balcony", "value": True},
                                    "note": "Inzerát uvádí balkon, ne plochu."}
    assert prov["cellar_area"]["note"] == "Inzerát uvádí sklep, ne plochu."
    assert "land_type" not in prov and "house_type" not in prov
    assert valuo.blocking(request) == []


def test_a_missing_condition_blocks_rather_than_guessing():
    request, prov = _build(FLAT | {"condition": None})
    assert "rating" not in request
    assert prov["rating"]["class"] == "missing"
    assert valuo.blocking(request) == ["rating"]


def test_a_yes_garage_is_sent_naming_its_rule_and_a_no_is_not_sent():
    request, prov = _build(FLAT | {"garage": True})
    assert request["garages"] == 1
    assert prov["garages"] == {"class": "inferred", "from": {"column": "garage", "value": True},
                               "note": "Garáž: ano → 1"}


def test_velmi_dobry_is_valuos_very_good():  # operator ruling 9 (2026-10-20) supersedes ruling 2's "good"
    request, prov = _build(FLAT | {"condition": "velmi_dobry"})
    assert request["rating"] == "very_good"
    assert prov["rating"] == {"class": "ad", "from": {"column": "condition", "value": "velmi_dobry"},
                              "note": None}


def test_energy_class_g_is_not_sent_but_one_override_sends_it():
    house = HOUSE | {"energy_rating": "G"}
    request, prov = _build(house)
    assert "energy_performance" not in request
    assert prov["energy_performance"]["class"] == "assumed"
    assert prov["energy_performance"]["from"] == {"column": "energy_rating", "value": "G"}
    assert "PENB" in prov["energy_performance"]["note"]
    request, prov = _build(house, overrides={"energy_performance": "G"})
    assert request["energy_performance"] == "G"
    assert prov["energy_performance"] == {"class": "user", "from": {"column": "energy_rating", "value": "G"},
                                          "note": None}
    assert _build(HOUSE | {"energy_rating": "B"})[0]["energy_performance"] == "B"


def test_a_new_building_is_rated_very_good_by_assumption_and_flagged_new():
    request, prov = _build(FLAT | {"condition": "novostavba"})
    assert request["rating"] == "very_good" and request["new_building"] is True
    assert prov["rating"] == {"class": "assumed", "from": {"column": "condition", "value": "novostavba"},
                              "note": "Novostavba → Velmi dobrý"}
    assert prov["new_building"] == {"class": "ad", "from": {"column": "condition", "value": "novostavba"},
                                    "note": None}
    for raw, word in (("projekt", "Projekt"), ("ve_vystavbe", "Ve výstavbě")):
        request, prov = _build(FLAT | {"condition": raw})
        assert request["rating"] == "very_good" and prov["rating"]["class"] == "inferred"
        assert request["new_building"] is True
        assert prov["new_building"] == {"class": "inferred", "from": {"column": "condition", "value": raw},
                                        "note": f"{word} → Novostavba"}
    request, _prov = _build(FLAT | {"condition": "novostavba"}, overrides={"new_building": False})
    assert request["new_building"] is False
    request, prov = _build(OFFICE | {"condition": "novostavba"})
    assert "new_building" not in request and "new_building" not in prov


@pytest.mark.parametrize(("column", "raw", "param", "value"), [
    ("condition", "projekt", "rating", "very_good"),
    ("condition", "spatny", "rating", "nothing_much"),
    ("building_type", "roubena", "construction", "wood"),
    ("disposition", "8+kk", "local_type", "jiné"),
])
def test_lossy_rules_are_inferred_and_named(column, raw, param, value):
    request, prov = _build(FLAT | {column: raw})
    assert request[param] == value
    assert prov[param]["class"] == "inferred"
    assert "→" in prov[param]["note"]


def test_only_valuos_own_dispositions_are_sent():
    allowed = {v for v, _lab in valuo._BY_NAME["local_type"].options}
    for raw in COLUMN_CANONICAL_VALUES["disposition"]:
        request, _prov = _build(FLAT | {"disposition": raw})
        assert request["local_type"] in allowed
        assert request["local_type"] == (raw if raw in valuo.LOCAL_TYPES else "jiné")
    request, prov = _build(FLAT | {"disposition": "atyp"})
    assert "local_type" not in request and prov["local_type"]["note"] == "Hodnota mimo slovník."
    with pytest.raises(valuo.OverrideError):
        _build(FLAT, overrides={"local_type": "8+kk"})
    assert _build(FLAT, overrides={"local_type": "1+0"})[0]["local_type"] == "1+0"


def test_an_off_canon_value_is_not_sent_and_says_so():
    request, prov = _build(FLAT | {"building_type": "slama"})
    assert "construction" not in request
    assert prov["construction"]["note"] == "Hodnota mimo slovník."


def test_an_auction_is_valued_as_a_sale_by_assumption():
    request, prov = _build(FLAT | {"category_type": "drazba"})
    assert request["kind"] == "sale"
    assert prov["kind"] == {"class": "assumed", "from": {"column": "category_type", "value": "drazba"},
                            "note": "Dražba → Prodej"}


def test_a_house_sends_its_type_and_withholds_its_plot_until_valuo_proves_it_counts():
    request, prov = _build(HOUSE | {"ownership": "osobni", "floor": 1})
    assert request["property_type"] == "house" and request["floor_area"] == 140
    assert request["house_type"] == "family_house"
    assert "ownership" not in prov and "floor" not in prov  # flat-only in v3
    assert prov["house_type"]["class"] == "inferred"
    assert request["new_building"] is True and request["rating"] == "very_good"
    assert "lot_area" not in request
    assert prov["lot_area"]["from"] == {"column": "estate_area", "value": 800}
    assert "local_type" not in prov
    assert valuo.blocking(request) == []


def test_land_sends_its_plot_and_waits_for_the_land_type():
    request, prov = _build(LAND)
    assert request["lot_area"] == 1200.0 and "floor_area" not in prov
    assert set(prov) == {f.name for f in valuo.fields_for("land")}
    assert request["rating"] == "good"
    assert prov["rating"] == {"class": "assumed", "from": None, "note": "Valuo vyžaduje stav i u pozemku."}
    assert valuo.blocking(request) == ["land_type"]
    request, prov = _build(LAND, overrides={"rating": "bad"})
    assert request["rating"] == "bad" and prov["rating"]["class"] == "user"
    request, _prov = _build(LAND, overrides={"land_type": "garden"})
    assert valuo.blocking(request) == []


def test_an_underground_floor_is_not_sent_unless_the_user_sends_it():
    request, prov = _build(FLAT | {"floor": -1})
    assert "floor" not in request and prov["floor"]["from"] == {"column": "floor", "value": -1}
    request, prov = _build(FLAT | {"floor": -1}, overrides={"floor": -1})
    assert request["floor"] == -1 and prov["floor"]["class"] == "user"


def test_an_ad_value_valuo_cannot_take_is_not_sent():
    request, prov = _build(FLAT | {"total_floors": 0, "garden_area": Decimal("0"), "parking_lots": 2})
    assert "total_floors" not in request and "garden_area" not in request
    assert request["parking_spaces"] == 2 and prov["parking_spaces"]["class"] == "property"


def test_commercial_sends_its_area_condition_and_premises_type():
    request, prov = _build(OFFICE | {"estate_area": 400})
    assert request == {
        "property_type": "commercial", "place": "50.080000, 14.420000", "kind": "lease", "floor_area": 180.0,
        "rating": "good", "as_of": _today(), "commercial_type": "office", "country_code": "cs",
    }
    assert prov["property_type"] == {"class": "ad", "from": {"column": "category_main", "value": "komercni"},
                                     "note": None}
    assert prov["commercial_type"] == {"class": "ad", "from": {"column": "subtype", "value": "kancelar"},
                                       "note": None}
    assert prov["lot_area"]["class"] == "missing" and prov["lot_area"]["from"] == {
        "column": "estate_area", "value": 400}
    assert "local_type" not in prov and "house_type" not in prov and "floor" not in prov
    assert valuo.blocking(request) == []
    assert valuo.blocking({k: v for k, v in request.items() if k != "floor_area"}) == ["floor_area"]
    _request, prov = _build(OFFICE | {"subtype": "virtualni_kancelar"})
    assert "commercial_type" not in _request and prov["commercial_type"]["class"] == "missing"


def test_a_subtype_of_another_type_is_never_sent():
    request, prov = _build(HOUSE | {"subtype": "kancelar"})
    assert "house_type" not in request and prov["house_type"]["class"] == "missing"


def test_a_town_level_point_is_sent_as_inferred_with_its_address_and_precision():
    _request, prov = _build(FLAT, TOWN)
    assert prov["place"]["class"] == "inferred"
    assert prov["place"]["note"] == "Vinohradská 12, Praha · Poloha jen na úrovni obce"


def test_the_place_is_the_users_to_change_as_an_address_or_a_point():
    for place in ("Vinohradská 12, Praha", "50.1, 14.4"):
        request, prov = _build(FLAT, overrides={"place": f"  {place} "})
        assert request["place"] == place
        assert prov["place"] == {"class": "user", "from": {"column": "listing_location.geom",
                                                           "value": "50.080000, 14.420000"}, "note": None}
    request, _prov = _build(FLAT, overrides={"place": None})
    assert valuo.blocking(request) == ["place"]
    assert _build(FLAT, overrides={"place": "x" * 200})[0]["place"] == "x" * 200
    with pytest.raises(valuo.OverrideError):
        _build(FLAT, overrides={"place": "x" * 201})
    # The box is told the same limit, so a long paste stops there, not at a 422.
    specs = {f.name: f.spec("flat") for f in valuo.fields_for("flat")}
    assert specs["place"]["max_length"] == 200
    assert all(spec["max_length"] is None for name, spec in specs.items() if name != "place")


def test_overrides_are_validated_against_the_field():
    request, prov = _build(FLAT, overrides={"floor": None, "as_of": "2025-06-30", "parking_spaces": 3})
    assert "floor" not in request and prov["floor"]["class"] == "user"
    assert request["as_of"] == "2025-06-30" and request["parking_spaces"] == 3
    for bad in ({"rating": "perfect"}, {"floor": -4}, {"floor_area": 0}, {"garages": "yes"},
                {"as_of": "30.6.2025"}, {"place": ""}, {"place": "   "}, {"place": 50.08},
                {"property_type": "house"}, {"country_code": "sk"}, {"land_type": "garden"},
                {"colour": "red"}, {"garages": True}, {"new_building": "ano"}, {"energy_performance": "C"},
                {"elevator": True}, {"rooms": 3}, {"rating": "new"}):
        with pytest.raises(valuo.OverrideError):
            _build(FLAT, overrides=bad)


@pytest.mark.parametrize(("subject", "location", "code"), [
    (OFFICE, STREET, None),
    (FLAT | {"category_main": "ostatni"}, STREET, "unsupported_type"),
    (FLAT | {"category_main": None}, STREET, "unsupported_type"),
    (FLAT, None, "no_location"),
    (FLAT, STREET | {"country_status": "foreign"}, "not_cz"),
    (FLAT, STREET | {"country_status": "undetermined"}, "country_unverified"),
    (FLAT, STREET | {"country_status": "disputed"}, "country_unverified"),
    (FLAT, STREET | {"precision": "coarse"}, "too_coarse"),
])
def test_refusals_name_one_reason(subject, location, code):
    expected = {"code": code, "message": valuo.REFUSALS[code]} if code else None
    assert valuo.refusal(subject, location) == expected


def test_the_per_m2_basis_follows_the_request_not_the_ad():
    assert valuo.result_basis({"property_type": "flat", "kind": "sale"}) == "sale_capital_czk_m2"
    assert valuo.result_basis({"property_type": "flat", "kind": "lease"}) == "rent_monthly_czk_m2"
    assert valuo.result_basis({"property_type": "land", "kind": "sale"}) == "land_capital_czk_m2"
    assert valuo.result_basis({"property_type": "commercial", "kind": "sale"}) == "sale_capital_czk_m2"
    assert valuo.result_basis({"property_type": "commercial", "kind": "lease"}) == "rent_monthly_czk_m2"


# --- the routes, over an in-memory valuo_valuations ----------------------------------------------


def _plain(v: Any) -> Any:
    return getattr(v, "obj", v)


class _Store:
    """valuo_valuations in memory, answering exactly the statements api/valuo.py issues."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def cursor(self, row_factory: Any = None) -> _Cur:
        return _Cur(self)

    def transaction(self) -> contextlib.nullcontext:
        return contextlib.nullcontext()

    def live_twin(self, account: Any, env: str, listing_id: int, request: Any) -> dict[str, Any] | None:
        key = json.dumps(request, sort_keys=True)
        return next((r for r in self.rows if (r["_account"], r["environment"], r["listing_id"])
                     == (account, env, listing_id) and json.dumps(r["request"], sort_keys=True) == key
                     and r["status"] in ("pending", "success")), None)


class _Cur:
    def __init__(self, store: _Store) -> None:
        self.store, self.result = store, []

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        text = " ".join(sql.split())
        p = [_plain(x) for x in params]
        rows = self.store.rows
        if text.startswith("insert into valuo_valuations"):
            account, listing_id, env, sha, request, provenance = p
            if self.store.live_twin(account, env, listing_id, request):
                self.result = []
                return
            row = {"id": len(rows) + 1, "created_at": datetime.now(timezone.utc), "listing_id": listing_id,
                   "environment": env, "status": "pending", "request": request, "provenance": provenance,
                   "response": None, "error": None, "adverts": None, "_account": account, "_sha": sha}
            rows.append(row)
            self.result = [row]
        elif text.startswith("update valuo_valuations set status = 'failed'"):
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
            for r in rows:
                if r["_account"] == p[1] and r["status"] == "pending" and r["created_at"] < cutoff:
                    r.update(status="failed", error=p[0])
            self.result = []
        elif text.startswith("update valuo_valuations set status = %s"):
            status, response, error, adverts, env, row_id = p
            hit = [r for r in rows if r["id"] == row_id and r["status"] == "pending"]
            if hit and status == "success" and env != hit[0]["environment"] and self.store.live_twin(
                    hit[0]["_account"], env, hit[0]["listing_id"], hit[0]["request"]):
                raise psycopg.errors.UniqueViolation("valuo_valuations_live_request_uidx")
            for r in hit:
                r.update(status=status, response=response, error=error, adverts=adverts, environment=env)
            self.result = hit
        elif "where account_id = %s and environment = %s and listing_id = %s" in text:
            twin = self.store.live_twin(*p)
            self.result = [twin] if twin else []
        elif "where account_id = %s and listing_id in" in text:
            self.result = sorted((r for r in rows if r["_account"] == p[0]), key=lambda r: -r["id"])
        else:
            raise AssertionError(f"unexpected statement: {text}")

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.result)


@pytest.fixture()
def store():
    s = _Store()
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: s
    yield s
    api_main.app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(api_main.app)


@pytest.fixture()
def world(monkeypatch, store):
    """The property the routes load, the caller's account, and a fake Valuo that records its calls."""
    state: dict[str, Any] = {"subject": dict(FLAT), "location": dict(STREET), "account": ACCOUNT,
                             "calls": [], "answer": None, "advert_calls": [], "adverts": ADVERTS}
    monkeypatch.setattr(valuo, "_load", lambda conn, pid: (state["subject"], state["location"]))
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda conn, claims: state["account"])

    def fake_calculate(cred: valuo_client.Credential, request: dict[str, Any]) -> dict[str, Any]:
        state["calls"].append((cred, request))
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"] or {"ver": "3.0.0-sandbox", "keep_ids": ["r1", "r2"], "ignored_ids": [], "result": {
            "avg_price": 6_104_321, "min_price": 5_100_000, "max_price": 7_000_000,
            "range_price": [5_600_000, 6_600_000],
            "avg_price_m2": 111_900, "range_price_m2": [102_700, 121_100], "std_price_m2": 7_200, "calc_area": 54.5,
            "distance": 2000, "age": 90, "flags": [], "lat": 50.08, "lng": 14.42, "avg_distance": 640.5,
            "avg_age": 12, "avg_score": 0.81,
            "records": [RECORD, RECORD | {"id": "r2", "sale_price": True}, "junk"]}}

    def fake_adverts(cred: valuo_client.Credential, ids: list[Any]) -> list[Any]:
        state["advert_calls"].append(ids)
        if isinstance(state["adverts"], Exception):
            raise state["adverts"]
        return state["adverts"]

    monkeypatch.setattr(valuo_client, "calculate", fake_calculate)
    monkeypatch.setattr(valuo_client, "advert_details", fake_adverts)
    return state


RECORD = {
    "id": "r1", "property_id": "p1", "place": "Testovací 148, Sandboxov", "lat": 50.081, "lon": 14.421,
    "local_type": "2+kk", "calc_area": 52, "lot_area": None, "rooms": 2, "floor": 4, "total_floors": 7,
    "rating": "good", "rating_int": 3, "new_building": False, "kind": "sale", "property_type": "flat",
    "price": 5_900_000, "price_m2": 113_461, "sale_price": False, "adjusted_price": 6_050_000,
    "adjusted_price_m2": 116_346, "distance": 420, "score": 81, "max_score": 100, "score_prct": 81,
    "days": 34, "first_seen_at": "2026-08-01", "last_seen_at": "2026-09-04", "coefs": {"floor": 1.02},
}
ADVERTS = [
    {"id": "r1", "address": "Testovací 148, Sandboxov", "advert_url": "https://example.test/r1",
     "images": [f"a/{n}.jpg" for n in range(14)] + [None, ""]},
    {"id": "r2", "hashed_images": ["0f3c"]},
]


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("VALUO_SANDBOX_API_TOKEN", TOKEN)


def _post(client, overrides: dict[str, Any] | None = None):
    return client.post("/properties/7/valuo", json={"overrides": overrides or {}}, headers=AUTH)


def test_unconfigured_get_answers_200_with_a_sentence_never_503(client, world):
    res = client.get("/properties/7/valuo", headers=AUTH)
    assert res.status_code == 200
    body = res.json()
    assert body["environment"] is None
    assert body["refusal"] == {"code": "unconfigured", "message": "Valuo zatím není nastaveno."}
    assert body["subject"]["label"] == "Byt · 2+kk"
    assert body["fields"] and body["defaults"]["property_type"] == "flat"


def test_unconfigured_post_is_503_and_writes_nothing(client, world, store):
    res = _post(client)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "valuo_unconfigured"
    assert store.rows == [] and world["calls"] == []


def test_get_returns_the_editor_and_the_callers_history(client, world, store, monkeypatch):
    _configure(monkeypatch)
    assert _post(client).status_code == 200
    body = client.get("/properties/7/valuo", headers=AUTH).json()
    assert body["environment"] == "sandbox" and body["refusal"] is None
    assert body["subject"]["location"] == {"label": "Vinohradská 12, Praha", "granularity": "address_point",
                                           "uncertainty_radius_m": 5.0, "note": None}
    rating = next(f for f in body["fields"] if f["name"] == "rating")
    assert rating["required"] and rating["group"] == "basic"
    assert {"value": "very_good", "label": "Velmi dobrý"} in rating["options"]
    assert body["provenance"]["rating"]["class"] == "ad"
    assert body["blocking"] == []
    assert [h["status"] for h in body["history"]] == ["success"]


def test_a_refused_get_carries_no_editor(client, world, monkeypatch):
    _configure(monkeypatch)
    world["subject"] = FLAT | {"category_main": "ostatni"}
    body = client.get("/properties/7/valuo", headers=AUTH).json()
    assert body["refusal"]["code"] == "unsupported_type"
    assert body["fields"] == [] and body["defaults"] == {} and body["blocking"] == []


@pytest.mark.parametrize(("subject", "location", "code"), [
    (FLAT | {"category_main": "ostatni"}, STREET, "unsupported_type"),
    (FLAT, None, "no_location"),
    (FLAT, STREET | {"precision": "coarse"}, "too_coarse"),
    (FLAT | {"condition": None}, STREET, "missing_required"),
])
def test_a_refusal_is_4xx_and_writes_no_row(client, world, store, monkeypatch, subject, location, code):
    _configure(monkeypatch)
    world["subject"], world["location"] = subject, location
    res = _post(client)
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == code
    assert store.rows == [] and world["calls"] == []


def test_a_bad_override_is_422_and_writes_no_row(client, world, store, monkeypatch):
    _configure(monkeypatch)
    res = _post(client, {"rating": "perfect"})
    assert res.status_code == 422 and res.json()["detail"]["code"] == "bad_override"
    assert store.rows == []


def test_a_valuation_is_saved_before_the_call_and_answered_once(client, world, store, monkeypatch):
    _configure(monkeypatch)
    res = _post(client)
    assert res.status_code == 200
    body = res.json()
    assert body["reused"] is False
    row = body["valuation"]
    assert row["status"] == "success" and row["environment"] == "sandbox"
    assert row["result"] == {"avg_price": 6_104_321, "range_price": [5_600_000, 6_600_000], "avg_price_m2": 111_900,
                             "range_price_m2": [102_700, 121_100], "std_price_m2": 7_200, "calc_area": 54.5,
                             "distance": 2000, "flags": []}
    assert row["error"] is None and row["kind_label"] == "Prodej"
    assert "ignored_ids" not in row and "ignored_labels" not in row
    assert row["ppm2_basis"] == "sale_capital_czk_m2"
    first, second = row["records"]
    assert set(first) == set(valuo.RECORD_KEYS) | {"photos", "advert_url", "address"}
    assert first["id"] == "r1" and second["id"] == "r2"
    assert first["place"] == "Testovací 148, Sandboxov" and first["adjusted_price_m2"] == 116_346
    assert first["sale_price"] is False and second["sale_price"] is True and "coefs" not in first
    assert len(first["photos"]) == 12 and first["photos"][0] == {
        "thumb": "https://pics.valuo.cz/pt/a/0.jpg", "full": "https://pics.valuo.cz/p/a/0.jpg"}
    assert first["advert_url"] == "https://example.test/r1" and first["address"] == "Testovací 148, Sandboxov"
    assert second["photos"] == [] and second["advert_url"] is None  # hashed images only: no photo to show
    assert row["summary"] == {"count": 2, "avg_distance": 640.5, "avg_age": 12, "avg_duration": None,
                              "avg_score": 0.81, "distance": 2000, "age": 90, "not_enough": False}
    assert world["advert_calls"] == [["r1", "r2"]]
    assert store.rows[0]["adverts"] == ADVERTS
    [(cred, request)] = world["calls"]
    assert cred.environment == "sandbox" and request == row["request"]
    assert store.rows[0]["_sha"] == cred.token_sha and len(cred.token_sha) == 12
    assert TOKEN not in res.text


def test_an_identical_request_reuses_the_paid_answer(client, world, store, monkeypatch):
    _configure(monkeypatch)
    first = _post(client).json()["valuation"]
    again = _post(client).json()
    assert again["reused"] is True and again["valuation"]["id"] == first["id"]
    assert len(world["calls"]) == 1 and len(store.rows) == 1


def test_an_identical_request_still_running_is_409(client, world, store, monkeypatch):
    _configure(monkeypatch)
    request, provenance = valuo.build_request(FLAT, STREET, {})
    store.rows.append({"id": 1, "created_at": datetime.now(timezone.utc), "listing_id": 42,
                       "environment": "sandbox", "status": "pending", "request": request,
                       "provenance": provenance, "response": None, "error": None, "_account": ACCOUNT,
                       "_sha": "x"})
    res = _post(client)
    assert res.status_code == 409
    assert res.json()["detail"] == {"code": "in_progress", "message": "Stejný dotaz právě běží."}
    assert world["calls"] == [] and len(store.rows) == 1


def test_a_stale_pending_twin_is_abandoned_and_the_call_proceeds(client, world, store, monkeypatch):
    _configure(monkeypatch)
    request, provenance = valuo.build_request(FLAT, STREET, {})
    store.rows.append({"id": 1, "created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
                       "listing_id": 42, "environment": "sandbox", "status": "pending", "request": request,
                       "provenance": provenance, "response": None, "error": None, "_account": ACCOUNT,
                       "_sha": "x"})
    res = _post(client)
    assert res.status_code == 200 and res.json()["valuation"]["status"] == "success"
    assert store.rows[0]["status"] == "failed" and store.rows[0]["error"]["code"] == "abandoned"
    assert len(world["calls"]) == 1


def test_the_get_settles_a_call_the_api_died_during(client, world, store, monkeypatch):
    _configure(monkeypatch)
    store.rows.append({"id": 1, "created_at": datetime.now(timezone.utc) - timedelta(minutes=5),
                       "listing_id": 42, "environment": "sandbox", "status": "pending", "request": {},
                       "provenance": {}, "response": None, "error": None, "_account": ACCOUNT, "_sha": "x"})
    [row] = client.get("/properties/7/valuo", headers=AUTH).json()["history"]
    assert row["status"] == "failed" and row["error"]["code"] == "abandoned"


def test_another_ads_identical_request_is_its_own_call(client, world, store, monkeypatch):
    _configure(monkeypatch)
    _post(client)
    world["subject"] = FLAT | {"listing_id": 99}
    again = _post(client).json()
    assert again["reused"] is False and again["valuation"]["listing_id"] == 99
    assert len(world["calls"]) == 2


def test_an_answer_landing_on_an_abandoned_row_is_logged_not_shown_as_running(
        client, world, store, monkeypatch, caplog):
    _configure(monkeypatch)

    def slow_calculate(cred: valuo_client.Credential, request: dict[str, Any]) -> dict[str, Any]:
        store.rows[0].update(status="failed", error=valuo.ABANDONED)
        return {"result": {"avg_price": 1}}

    monkeypatch.setattr(valuo_client, "calculate", slow_calculate)
    row = _post(client).json()["valuation"]
    assert row["status"] == "failed" and row["error"]["code"] == "abandoned"
    assert "VALUO answer lost" in caplog.text


def test_a_valuo_failure_is_a_stored_failed_row_answered_200(client, world, store, monkeypatch):
    _configure(monkeypatch)
    issues = [{"code": "too_small", "path": ["floor_area"], "message": "Number must be greater than 0"},
              {"code": "custom", "path": [], "message": "Unknown place"}]
    answer = {"message": json.dumps(issues)}
    world["answer"] = valuo_client.ValuoError("form_errors", 500, answer)
    res = _post(client)
    assert res.status_code == 200
    row = res.json()["valuation"]
    assert row["status"] == "failed" and row["result"] is None
    assert row["records"] == [] and row["summary"] is None
    assert row["error"] == {"code": "form_errors", "message": "Valuo odmítlo některé parametry.", "form_errors": [
        {"field": "floor_area", "message": "Number must be greater than 0"},
        {"field": None, "message": "Unknown place"},
    ]}
    assert store.rows[0]["response"] == answer
    assert store.rows[0]["error"]["http_status"] == 500
    # a failed row frees the slot: the user may try again, and pays again only by asking
    world["answer"] = None
    assert _post(client).json()["valuation"]["status"] == "success"
    assert len(world["calls"]) == 2


def test_a_sandbox_answer_under_the_production_name_is_stored_as_sandbox(client, world, store, monkeypatch):
    monkeypatch.setenv("VALUO_API_TOKEN", "a-sandbox-token-pasted-as-production")
    row = _post(client).json()["valuation"]
    assert row["environment"] == "sandbox" and store.rows[0]["environment"] == "sandbox"
    world["answer"] = {"ver": "3.0.0", "result": {"avg_price": 1}}
    row = _post(client, {"floor": 4}).json()["valuation"]
    assert row["environment"] == "production"


def test_a_sandbox_answer_meeting_its_stored_sandbox_twin_reuses_it(client, world, store, monkeypatch):
    _configure(monkeypatch)
    first = _post(client).json()["valuation"]
    monkeypatch.setenv("VALUO_API_TOKEN", "the-same-sandbox-token-named-production")
    again = _post(client).json()
    assert again["reused"] is True and again["valuation"]["id"] == first["id"]
    assert [(r["environment"], r["status"]) for r in store.rows] == [("sandbox", "success"), ("sandbox", "failed")]
    assert store.rows[1]["error"]["code"] == "duplicate"


def test_a_failed_photo_call_never_fails_the_valuation(client, world, store, monkeypatch, caplog):
    _configure(monkeypatch)
    world["adverts"] = valuo_client.ValuoError("unavailable", 500)
    row = _post(client).json()["valuation"]
    assert row["status"] == "success" and store.rows[0]["adverts"] is None
    assert all(r["photos"] == [] for r in row["records"])
    assert "VALUO adverts not fetched" in caplog.text


def test_no_comparables_means_no_photo_call(client, world, store, monkeypatch):
    _configure(monkeypatch)
    world["answer"] = {"ver": "3.0.0-sandbox", "result": {"avg_price": 1, "flags": ["NOT_ENOUGH_PROPERTIES"]}}
    row = _post(client).json()["valuation"]
    assert world["advert_calls"] == [] and row["records"] == []
    assert row["summary"]["count"] is None and row["summary"]["not_enough"] is True


def test_photos_are_asked_for_the_first_ten_comparables_only(client, world, store, monkeypatch):
    _configure(monkeypatch)
    world["answer"] = {"result": {"avg_price": 1, "records": [RECORD | {"id": n} for n in range(15)]}}
    _post(client)
    assert world["advert_calls"] == [list(range(10))]


def test_the_engines_refusal_is_stored_with_valuos_reason(client, world, store, monkeypatch):
    _configure(monkeypatch)
    world["answer"] = valuo_client.ValuoError("calculation", 400, {"message": "Place could not be resolved"})
    row = _post(client).json()["valuation"]
    assert row["error"] == {"code": "calculation", "message": "Valuo nemovitost neocenilo: Place could not be resolved"}
    assert store.rows[0]["error"]["detail"] == "Place could not be resolved"
    assert world["advert_calls"] == []


def test_a_caller_without_an_account_is_400(client, world, store, monkeypatch):
    _configure(monkeypatch)
    world["account"] = None
    for res in (client.get("/properties/7/valuo", headers=AUTH), _post(client)):
        assert res.status_code == 400 and res.json()["detail"]["code"] == "no_account"
    assert store.rows == []


def test_both_routes_need_a_signed_in_caller(client, world):
    assert client.get("/properties/7/valuo").status_code == 401
    assert client.post("/properties/7/valuo", json={"overrides": {}}).status_code == 401

"""Valuo valuations (api/valuo.py, api/routes/valuo.py): the mapping and its vocabulary census, the
careful defaults (operator ruling 2026-10-19), the refusals, and the save-before-call sequence over an
in-memory `valuo_valuations`. Hermetic: no DB, and Valuo is a fake (`valuo_client.calculate`), so no
call is ever paid for. The SQL itself runs against the replayed schema in
tests/test_valuo_valuations_live.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import dependencies as deps
from api import main as api_main
from api import tenant_pool, valuo, valuo_client
from tests.api._jwt import mint
from toolkit.filter_registry import COLUMN_CANONICAL_VALUES

ACCOUNT = "11111111-1111-1111-1111-111111111111"
AUTH = {"Authorization": f"Bearer {mint({'sub': 'u1'})}"}
TOKEN = "sandbox-secret-token"

FLAT = {
    "listing_id": 42, "category_main": "byt", "category_type": "prodej", "subtype": None,
    "disposition": "2+kk", "area_m2": Decimal("54.5"), "estate_area": None, "garden_area": None,
    "floor": 3, "total_floors": 6, "condition": "po_rekonstrukci", "building_type": "cihla",
    "ownership": "osobni", "energy_rating": "C", "furnished": "ne", "has_lift": True,
    "has_balcony": True, "terrace": False, "cellar": True, "garage": False, "parking_lots": None,
}
EMPTY = {k: None for k in FLAT} | {"listing_id": 43}
HOUSE = EMPTY | {"category_main": "dum", "category_type": "prodej", "subtype": "vicegeneracni_dum",
                 "area_m2": 140, "estate_area": 800, "condition": "novostavba"}
LAND = EMPTY | {"category_main": "pozemek", "category_type": "prodej", "area_m2": Decimal("1200")}
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


def test_every_canonical_value_of_a_mapped_column_is_ruled():
    """A new vocabulary member must be mapped or explicitly not sent before CI goes green."""
    for column, (param, table) in valuo.MAPS.items():
        assert set(table) == set(COLUMN_CANONICAL_VALUES[column]), column
        options = dict(valuo._BY_NAME[param].options)
        for value, rule in table.values():
            assert rule in (valuo._DIRECT, valuo.INFERRED, valuo.ASSUMED, valuo.MISSING)
            if valuo._BY_NAME[param].type == "boolean":
                assert value in (True, False, None)
            else:
                assert value is None or value in options, (column, value)


def test_one_field_per_documented_valuo_parameter():
    documented = {
        "place", "kind", "property_type", "floor_area", "lot_area", "rating", "land_type", "as_of",
        "house_type", "ownership", "local_type", "construction", "floor", "total_floors",
        "energy_performance", "equipment", "easy_access", "elevator", "loggia_area", "attic_area",
        "cellar_area", "terrace_area", "other_area", "balcony_area", "garden_area", "built_up_area",
        "rooms", "bathrooms", "bedrooms", "kitchens", "garages", "parking_spaces",
    }
    names = [f.name for f in valuo.FIELDS]
    assert len(names) == len(set(names)) and set(names) == documented
    assert all(f.label and all(lab for _v, lab in f.options) for f in valuo.FIELDS)


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
        "construction": "brick", "floor": 3, "total_floors": 6, "energy_performance": "C",
        "elevator": True, "equipment": False, "garages": 0,
    }
    assert list(request) == [f.name for f in valuo.FIELDS if f.name in request]
    assert prov["rating"] == {"class": "ad", "from": {"column": "condition", "value": "po_rekonstrukci"},
                              "note": None}
    assert prov["construction"]["class"] == "property"
    assert prov["place"]["class"] == "ad"
    assert prov["equipment"] == {"class": "inferred", "from": {"column": "furnished", "value": "ne"},
                                 "note": "Nevybaveno → ne"}
    assert prov["garages"]["note"] == "Garáž: ne → 0"
    assert prov["as_of"]["class"] == "assumed"
    assert prov["balcony_area"] == {"class": "missing", "from": {"column": "has_balcony", "value": True},
                                    "note": "Inzerát uvádí balkon, ne plochu."}
    assert prov["rooms"] == {"class": "missing", "from": None, "note": None}
    assert "land_type" not in prov and "house_type" not in prov
    assert valuo.blocking(request) == []


def test_a_missing_condition_blocks_rather_than_guessing():
    request, prov = _build(FLAT | {"condition": None})
    assert "rating" not in request
    assert prov["rating"]["class"] == "missing"
    assert valuo.blocking(request) == ["rating"]


def test_velmi_dobry_is_assumed_average_not_renovated():
    request, prov = _build(FLAT | {"condition": "velmi_dobry"})
    assert request["rating"] == "good"
    assert prov["rating"] == {"class": "assumed", "from": {"column": "condition", "value": "velmi_dobry"},
                              "note": "Velmi dobrý → Průměrný"}


def test_energy_class_g_is_not_sent_but_one_override_sends_it():
    request, prov = _build(FLAT | {"energy_rating": "G"})
    assert "energy_performance" not in request
    assert prov["energy_performance"]["class"] == "assumed"
    assert prov["energy_performance"]["from"] == {"column": "energy_rating", "value": "G"}
    assert "PENB" in prov["energy_performance"]["note"]
    request, prov = _build(FLAT | {"energy_rating": "G"}, overrides={"energy_performance": "G"})
    assert request["energy_performance"] == "G"
    assert prov["energy_performance"] == {"class": "user", "from": {"column": "energy_rating", "value": "G"},
                                          "note": None}


@pytest.mark.parametrize(("column", "raw", "param", "value"), [
    ("condition", "projekt", "rating", "new"),
    ("condition", "spatny", "rating", "nothing_much"),
    ("building_type", "roubena", "construction", "wood"),
    ("disposition", "6+kk", "local_type", "jiné"),
])
def test_lossy_rules_are_inferred_and_named(column, raw, param, value):
    request, prov = _build(FLAT | {column: raw})
    assert request[param] == value
    assert prov[param]["class"] == "inferred"
    assert "→" in prov[param]["note"]


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
    request, prov = _build(HOUSE)
    assert request["property_type"] == "house" and request["floor_area"] == 140
    assert request["house_type"] == "family_house"
    assert prov["house_type"]["class"] == "inferred"
    assert "lot_area" not in request
    assert prov["lot_area"]["from"] == {"column": "estate_area", "value": 800}
    assert "local_type" not in prov
    assert valuo.blocking(request) == []


def test_land_sends_its_plot_and_waits_for_the_land_type():
    request, prov = _build(LAND)
    assert request["lot_area"] == 1200.0 and "floor_area" not in prov
    assert set(prov) == {f.name for f in valuo.fields_for("land")}
    assert valuo.blocking(request) == ["land_type"]
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


def test_a_town_level_point_is_sent_as_inferred_with_its_note():
    _request, prov = _build(FLAT, TOWN)
    assert prov["place"]["class"] == "inferred"
    assert prov["place"]["note"] == "Poloha jen na úrovni obce"


def test_overrides_are_validated_against_the_field():
    request, prov = _build(FLAT, overrides={"elevator": None, "as_of": "2025-06-30", "rooms": 3})
    assert "elevator" not in request and prov["elevator"]["class"] == "user"
    assert request["as_of"] == "2025-06-30" and request["rooms"] == 3
    for bad in ({"rating": "perfect"}, {"floor": -4}, {"floor_area": 0}, {"elevator": "yes"},
                {"as_of": "30.6.2025"}, {"place": "Praha"}, {"property_type": "house"},
                {"land_type": "garden"}, {"colour": "red"}, {"rooms": True}):
        with pytest.raises(valuo.OverrideError):
            _build(FLAT, overrides=bad)


@pytest.mark.parametrize(("subject", "location", "code"), [
    (FLAT | {"category_main": "komercni"}, STREET, "unsupported_type"),
    (FLAT | {"category_main": "ostatni"}, STREET, "unsupported_type"),
    (FLAT | {"category_main": None}, STREET, "unsupported_type"),
    (FLAT, None, "no_location"),
    (FLAT, STREET | {"country_status": "foreign"}, "not_cz"),
    (FLAT, STREET | {"country_status": "undetermined"}, "country_unverified"),
    (FLAT, STREET | {"country_status": "disputed"}, "country_unverified"),
    (FLAT, STREET | {"precision": "coarse"}, "too_coarse"),
])
def test_refusals_name_one_reason(subject, location, code):
    assert valuo.refusal(subject, location) == {"code": code, "message": valuo.REFUSALS[code]}


def test_the_per_m2_basis_follows_the_request_not_the_ad():
    assert valuo.result_basis({"property_type": "flat", "kind": "sale"}) == "sale_capital_czk_m2"
    assert valuo.result_basis({"property_type": "flat", "kind": "lease"}) == "rent_monthly_czk_m2"
    assert valuo.result_basis({"property_type": "land", "kind": "sale"}) == "land_capital_czk_m2"


# --- the routes, over an in-memory valuo_valuations ----------------------------------------------


def _plain(v: Any) -> Any:
    return getattr(v, "obj", v)


class _Store:
    """valuo_valuations in memory, answering exactly the statements api/valuo.py issues."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def cursor(self, row_factory: Any = None) -> _Cur:
        return _Cur(self)

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
                   "response": None, "error": None, "_account": account, "_sha": sha}
            rows.append(row)
            self.result = [row]
        elif text.startswith("update valuo_valuations set status = 'failed'"):
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
            for r in rows:
                if r["_account"] == p[1] and r["status"] == "pending" and r["created_at"] < cutoff:
                    r.update(status="failed", error=p[0])
            self.result = []
        elif text.startswith("update valuo_valuations set status = %s"):
            status, response, error, row_id = p
            hit = [r for r in rows if r["id"] == row_id and r["status"] == "pending"]
            for r in hit:
                r.update(status=status, response=response, error=error)
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
                             "calls": [], "answer": None}
    monkeypatch.setattr(valuo, "_load", lambda conn, pid: (state["subject"], state["location"]))
    monkeypatch.setattr(tenant_pool, "resolve_account_id", lambda conn, claims: state["account"])

    def fake_calculate(cred: valuo_client.Credential, request: dict[str, Any]) -> dict[str, Any]:
        state["calls"].append((cred, request))
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"] or {"success": True, "ignored_ids": ["garages"], "result": {
            "avg_price": 6_100_000, "min_price": 5_600_000, "max_price": 6_600_000, "avg_price_m2": 111_900,
            "min_price_m2": 102_700, "max_price_m2": 121_100, "std_price_m2": 7_200, "calc_area": 54.5,
            "distance": 2000, "flags": [], "lat": 50.08, "lng": 14.42}}

    monkeypatch.setattr(valuo_client, "calculate", fake_calculate)
    return state


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
    assert {"value": "very_good", "label": "Po kompletní rekonstrukci"} in rating["options"]
    assert body["provenance"]["rating"]["class"] == "ad"
    assert body["blocking"] == []
    assert [h["status"] for h in body["history"]] == ["success"]


def test_a_refused_get_carries_no_editor(client, world, monkeypatch):
    _configure(monkeypatch)
    world["subject"] = FLAT | {"category_main": "komercni"}
    body = client.get("/properties/7/valuo", headers=AUTH).json()
    assert body["refusal"]["code"] == "unsupported_type"
    assert body["fields"] == [] and body["defaults"] == {} and body["blocking"] == []


@pytest.mark.parametrize(("subject", "location", "code"), [
    (FLAT | {"category_main": "komercni"}, STREET, "unsupported_type"),
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
    assert row["result"]["avg_price"] == 6_100_000 and row["result"]["calc_area"] == 54.5
    assert row["ignored_ids"] == ["garages"] and row["error"] is None
    assert row["ignored_labels"] == ["Garáže"] and row["kind_label"] == "Prodej"
    assert row["ppm2_basis"] == "sale_capital_czk_m2"
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
        return {"success": True, "result": {"avg_price": 1}}

    monkeypatch.setattr(valuo_client, "calculate", slow_calculate)
    row = _post(client).json()["valuation"]
    assert row["status"] == "failed" and row["error"]["code"] == "abandoned"
    assert "VALUO answer lost" in caplog.text


def test_a_valuo_failure_is_a_stored_failed_row_answered_200(client, world, store, monkeypatch):
    _configure(monkeypatch)
    answer = {"success": False, "errors": [], "form_errors": {"floor_area": ["too small"]}}
    world["answer"] = valuo_client.ValuoError("form_errors", 200, answer)
    res = _post(client)
    assert res.status_code == 200
    row = res.json()["valuation"]
    assert row["status"] == "failed" and row["result"] is None
    assert row["error"] == {"code": "form_errors", "message": "Valuo odmítlo některé parametry."}
    assert store.rows[0]["response"] == answer
    assert store.rows[0]["error"]["http_status"] == 200
    # a failed row frees the slot: the user may try again, and pays again only by asking
    world["answer"] = None
    assert _post(client).json()["valuation"]["status"] == "success"
    assert len(world["calls"]) == 2


def test_a_caller_without_an_account_is_400(client, world, store, monkeypatch):
    _configure(monkeypatch)
    world["account"] = None
    for res in (client.get("/properties/7/valuo", headers=AUTH), _post(client)):
        assert res.status_code == 400 and res.json()["detail"]["code"] == "no_account"
    assert store.rows == []


def test_both_routes_need_a_signed_in_caller(client, world):
    assert client.get("/properties/7/valuo").status_code == 401
    assert client.post("/properties/7/valuo", json={"overrides": {}}).status_code == 401

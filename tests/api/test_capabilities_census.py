"""Census of the tool registry (#2062): every tool declares its tenancy and effect, no input schema names an
identity, every schema is portable, only the registry opens a connection, no declared filter is dropped, and no
tool's columns name a parked condition level, a city index or a broker (#2063)."""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import importlib
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from api.capabilities import registry
from api.capabilities.entry import Caller, Effect, Entry, Tenancy
from api.capabilities.schema import PORTABLE_KEYWORDS, nodes, portable
from tests.api._script_conn import ScriptConn
from toolkit import filter_registry as fr

ROOT = Path(__file__).resolve().parents[2]
ENTRIES = dict(registry.ENTRIES)


def _walk(schema: dict[str, Any], path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every (dotted name, property schema) at any depth."""
    out = []
    for name, sub in (schema.get("properties") or {}).items():
        out.append((f"{path}{name}", sub))
        out += _walk(sub, f"{path}{name}.")
        if isinstance(sub.get("items"), dict):
            out += _walk(sub["items"], f"{path}{name}[].")
    return out


# --- 1. declarations --------------------------------------------------------------------------------------------

def test_every_tool_declares_a_real_tenancy_and_effect_and_a_usable_name() -> None:
    for key, entry in ENTRIES.items():
        assert type(entry.tenancy) is Tenancy and type(entry.effect) is Effect, key
        assert key == entry.name and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key), key
        assert 1 <= len(entry.description) <= 1024, key
    kinds = {e.tenancy for e in ENTRIES.values()}
    assert kinds == {Tenancy.MARKET, Tenancy.TENANT}, "the census must see both tenancies to mean anything"


def test_a_tool_cannot_be_declared_without_tenancy_or_effect() -> None:
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
               for f in dataclasses.fields(Entry))
    with pytest.raises(TypeError):
        Entry(name="x", description="x", args=BaseModel, handler=lambda c, a: {}, tenancy=Tenancy.MARKET)


def test_a_market_tool_only_reads_so_a_write_is_only_ever_to_the_users_own_data() -> None:
    """The service connection is bounded READ ONLY (registry._connection): a market tool that wrote would fail."""
    for entry in ENTRIES.values():
        if entry.tenancy is Tenancy.MARKET:
            assert entry.effect is Effect.READ, entry.name


# --- 2-3. no identity in any input schema -----------------------------------------------------------------------

_IDENTITY_TOKENS = {"account", "user", "acting", "act", "owner", "principal", "tenant", "member", "email", "login"}
_IDENTITY_NAMES = {"sub", "role", "uid"}


def _identity_fields(schema: dict[str, Any]) -> list[str]:
    hits = []
    for dotted, _ in _walk(schema):
        name = dotted.rsplit(".", 1)[-1].removesuffix("[]")
        tokens = {t.removesuffix("s") for t in name.split("_")} | set(name.split("_"))
        if tokens & _IDENTITY_TOKENS or name in _IDENTITY_NAMES:
            hits.append(dotted)
    return hits


def test_the_identity_walker_sees_identity_at_any_depth_and_nothing_else() -> None:
    class Owner(BaseModel):
        users: list[int] = []

    class Sentinel(BaseModel):
        acting_account_id: str = ""
        user_id: int = 0
        owner: Owner = Owner()
        category_sub_cb: int = 0
        ownership: list[str] = []
    assert sorted(_identity_fields(portable(Sentinel))) == ["acting_account_id", "owner", "owner.users", "user_id"]


def test_no_input_schema_names_whose_data_it_is() -> None:
    """The caller's identity comes from their verified login, never from an argument (ADR-0002)."""
    for name, entry in ENTRIES.items():
        assert not _identity_fields(entry.input_schema), name


def test_a_market_tool_takes_no_id_but_a_property_or_an_ad_and_never_forces_a_refresh() -> None:
    for name, entry in ENTRIES.items():
        fields = {d.rsplit(".", 1)[-1] for d, _ in _walk(entry.input_schema)}
        assert "force_refresh" not in fields, name
        if entry.tenancy is Tenancy.MARKET:
            ids = {f for f in fields if f.endswith(("_id", "_ids"))}
            assert ids <= {"property_id", "listing_id", "property_ids", "listing_ids"}, name


# --- 4. portable shape ------------------------------------------------------------------------------------------

def test_every_input_schema_is_portable_to_every_provider() -> None:
    for name, entry in ENTRIES.items():
        schema = entry.input_schema
        assert schema["type"] == "object", name
        for node in nodes(schema):
            assert set(node) <= PORTABLE_KEYWORDS, (name, set(node) - PORTABLE_KEYWORDS)
            assert not isinstance(node.get("type"), list), name
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, name
        for dotted, sub in _walk(schema):
            assert sub.get("description"), f"{name}.{dotted} has no description"


def test_every_input_schema_builds_with_the_gemini_sdk() -> None:
    """Gemini's client refuses what the other providers take silently (exclusive bounds, $ref, type arrays)."""
    types = pytest.importorskip("google.genai.types")
    from api.providers.gemini import _schema_for_gemini
    for name, entry in ENTRIES.items():
        types.FunctionDeclaration(name=name, description=entry.description,
                                  parameters=_schema_for_gemini(entry.input_schema))


def test_a_field_that_takes_null_says_so_because_null_is_not_omission() -> None:
    """A held subject's unset category takes its own; null asks for every category: the schema must offer null."""
    for name, entry in ENTRIES.items():
        rendered = entry.input_schema["properties"]
        for field, raw in entry.args.model_json_schema()["properties"].items():
            if {"type": "null"} in raw.get("anyOf", []):
                assert rendered[field].get("nullable") is True, (name, field)


def test_no_tool_offers_a_parked_condition_level() -> None:
    """Rule 14: condition scoring is parked, so no surface reads its levels."""
    from tests.toolkit.test_filter_registry import _PARKED_CONDITION_LEVELS
    for name, entry in ENTRIES.items():
        names = {dotted.rsplit(".", 1)[-1] for dotted, _ in _walk(entry.input_schema)}
        assert not names & _PARKED_CONDITION_LEVELS, name


# --- 6. only the registry opens a connection --------------------------------------------------------------------

_OPENERS = {"connect", "connect_session", "tenant_transaction", "tenant_conn", "get_db_conn", "open_background_conn"}


def test_no_tool_module_but_the_registry_opens_a_connection() -> None:
    for path in sorted((ROOT / "api" / "capabilities").glob("*.py")):
        if path.name == "registry.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not used & _OPENERS, (path.name, used & _OPENERS)


# --- 7. no declared filter is dropped ---------------------------------------------------------------------------

_POINT = {"lat": 50.08, "lng": 14.42, "area_m2": 60.0, "disposition": "2+kk", "floor": 3}
# compute_listing_velocity reads its subject first: is_active, disposition, lat, lng, categories, id, tom_days.
_VELOCITY_SUBJECT = (lambda sql: "AS tom_days" in sql and "l.is_active, l.disposition" in sql,
                     [(True, "2+kk", 50.08, 14.42, "byt", "prodej", 70, 12)])
_COUNT = (lambda sql: sql.startswith("SELECT count(*)"), [(0,)])
# The Stats dashboard's function answers one payload, which aggregate_properties and market_evidence read by key.
_PIVOT = (lambda sql: sql.startswith("SELECT public.browse_stats_properties("),
          [({"filtered": 0, "sample": 0, "excluded": 0, "basis": None, "min_n": 5, "refused": "empty", "places": [],
             "cells": []},)])
# A filter that only modifies another renders only beside it: the baseline sets the one it modifies. market_evidence's
# window other than the Stats tab's carries its reason.
_PARTNERS = {"include_no_price": {"min_price_czk": 1_000_000}, "portals_excluded_active_only": {"portals_excluded": ["bazos"]},
             "price_change_window_days": {"price_change_count_min": 2},
             "tom_days_min": {"window_reason": "a census window"}, "tom_days_max": {"window_reason": "a census window"}}


def _point_fields(entry: Entry) -> tuple[str, ...]:
    """The fields of the point model this tool declares (a place only, or a place with attributes)."""
    point = entry.args.model_fields.get("point")
    return tuple(point.annotation.__args__[0].model_fields) if point is not None else ()


def _baseline(entry: Entry) -> dict[str, Any]:
    fields = entry.args.model_fields
    args: dict[str, Any] = {}
    if "point" in fields:
        args["point"] = {k: v for k, v in _POINT.items() if k in _point_fields(entry)}
    if "listing_id" in fields and fields["listing_id"].is_required():
        args["listing_id"] = 70
    args.update({c: v for c, v in (("category_main", "byt"), ("category_type", "prodej")) if c in fields})
    if "lifecycle" in fields and fields["lifecycle"].default is None:
        args["lifecycle"] = "active"   # max_age_days renders only with a lifecycle gate
    if "floor_band" in fields:
        args["floor_band"] = 2         # the point's floor renders only inside a floor band
    for modifier, partner in _PARTNERS.items():
        if modifier in fields:
            args.update({k: v for k, v in partner.items() if k in fields})
    return args


def _distinct(prop: dict[str, Any], current: Any) -> Any:
    """A valid value for `prop` that is not `current`; a composite is built from its own properties."""
    if prop.get("type") == "array":
        return [_distinct(prop["items"], None)]
    if prop.get("type") == "object":
        return {k: _distinct(sub, None) for k, sub in prop["properties"].items()}
    if "enum" in prop:
        return next(v for v in prop["enum"] if v != current)
    floor = prop.get("minimum", 0)
    return {"integer": floor + 7, "number": floor + 0.37, "boolean": not current, "string": "x"}[prop["type"]]


def _statements(name: str, args: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    from tests.api.test_broker_board import head, is_board  # that module reads this one's _distinct

    # The broker board reads its statement's one totals row by name.
    conn = ScriptConn([_VELOCITY_SUBJECT, _COUNT, (is_board, [head()]), _PIVOT])

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        yield conn
    monkeypatch.setattr(registry.tenant_pool, "tenant_transaction", own)
    registry.call(Caller(market=conn, claims={"sub": "census"}), name, args)
    return conn.executed


@pytest.mark.parametrize("name", sorted(n for n, e in ENTRIES.items()
                                        if any(f in fr.REGISTRY for f in e.args.model_fields)))
def test_every_filter_a_tool_declares_changes_the_statement_it_runs(
        name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Either tenancy, a composite filter included: the census builds a valid value from the schema itself."""
    entry = ENTRIES[name]
    base = _baseline(entry)
    before = _statements(name, base, monkeypatch)
    props = entry.input_schema["properties"]
    dropped = []
    for fid in (f for f in entry.args.model_fields if f in fr.REGISTRY):
        current = base.get(fid, entry.args.model_fields[fid].default)
        if _statements(name, {**base, fid: _distinct(props[fid], current)}, monkeypatch) == before:
            dropped.append(fid)
    assert not dropped, f"{name} declares filters that never reach its SQL: {dropped}"


_OTHER_POINT = {"lat": 50.09, "lng": 14.43, "area_m2": 90.0, "disposition": "3+kk", "floor": 5}


@pytest.mark.parametrize("name", sorted(n for n, e in ENTRIES.items() if "point" in e.args.model_fields))
def test_every_point_attribute_a_tool_accepts_changes_the_statement_it_runs(
        name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = ENTRIES[name]
    base = _baseline(entry)
    before = _statements(name, base, monkeypatch)
    dropped = [f for f in _point_fields(entry)
               if _statements(name, {**base, "point": {**base["point"], f: _OTHER_POINT[f]}}, monkeypatch) == before]
    assert not dropped, f"{name} accepts point attributes it never applies: {dropped}"


# --- 8. no tool reads what no surface may show ------------------------------------------------------------------

def forbidden_columns() -> frozenset[str]:
    """Derived from the registry: rule 14's parked levels (the columns of the retired level bounds) and every rule 17
    city-quality column; a tool's column allowlist is disjoint from it and names no broker."""
    from toolkit.filter_compiler import RETIRED_FILTERS
    levels = {fid.rsplit("_", 1)[0] for fid in RETIRED_FILTERS if fid.endswith(("_min", "_max"))}
    city = {f.pg_column for f in fr.all_filters() if f.category == fr.CATEGORY_CITY_QUALITY and f.pg_column}
    return frozenset(levels | city)


def test_the_forbidden_set_names_what_the_read_models_still_publish() -> None:
    assert {"building_condition_level", "apartment_condition_level", "home_obec_pop", "near_pop_5km",
            "near_overall_15km"} <= forbidden_columns()


_READERS = ("api.property_search", "api.property_read", "api.market_evidence")
# Each tool reader's column allowlists: search's, the property state's, every *_COLS of the property page's reader and
# the two arms of the achieved-price evidence.
_ALLOWLISTS = ["api.property_search.SEARCH_COLUMNS", "api.curation.STATE_COLS", "api.market_evidence.PROXY_COLUMNS",
               "api.market_evidence.REGISTERED_COLUMNS"] + [
    f"api.property_read.{n}" for n in sorted(vars(importlib.import_module("api.property_read"))) if n.endswith("_COLS")]


def test_the_census_sees_every_allowlist_of_the_property_reader() -> None:
    assert {"PROPERTY_COLS", "SOURCE_COLS", "AD_MEASURE_COLS", "SNAPSHOT_COLS", "RUN_COLS"} <= {
        a.rsplit(".", 1)[1] for a in _ALLOWLISTS}


@pytest.mark.parametrize("columns", _ALLOWLISTS)
def test_no_tool_column_allowlist_names_a_forbidden_column_or_a_broker(columns: str) -> None:
    module, name = columns.rsplit(".", 1)
    allowed = set(getattr(importlib.import_module(module), name))
    assert not allowed & forbidden_columns(), columns
    assert not [c for c in allowed if c.startswith("broker")], columns


# Where a parked level, a city index, a broker or a listing's own stored place lives (the place reaches a reader only
# through the read models' location-ad columns, rule 25); the reader modules name none of them.
_FORBIDDEN_RELATIONS = re.compile(
    r"\b(curated_cities\w*|city_index_\w+|city_population|listing_condition_scores|listing_broker_public|"
    r"listing_location)\b")


@pytest.mark.parametrize("module", _READERS)
def test_no_tool_reader_names_a_relation_that_holds_what_no_surface_may_show(module: str) -> None:
    source = Path(importlib.import_module(module).__file__).read_text(encoding="utf-8")
    assert not _FORBIDDEN_RELATIONS.findall(source), module


# What the user's login reads as nothing, silently (RLS on and no policy, or the service role's alone): a tenant
# reader that named one would answer "none" where the page shows something.
_DARK_UNDER_LOGIN = re.compile(
    r"\b(?:from|join)\s+(?:public\.)?(?:listings|listing_snapshots)\b|\bproperty_canonical_listings\b|"
    r"\bcanonical_advert\b|\bsubject_mf_facts\b|\bmatch_listing\b", re.IGNORECASE)


# api/stats_pivot resolves Browse's city-index rules through curated_cities_matching, as Browse does: a FILTER (rule 17
# lets agents that rank use one), never a value in a result, so it is held to the login census only.
@pytest.mark.parametrize("module", (*_READERS, "api.stats_pivot"))
def test_no_tool_reader_reads_a_relation_dark_under_the_users_login(module: str) -> None:
    source = Path(importlib.import_module(module).__file__).read_text(encoding="utf-8")
    assert not _DARK_UNDER_LOGIN.findall(source), module
    assert _DARK_UNDER_LOGIN.findall("select 1 from listings l join listing_snapshots s on true")


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [d for v in value for d in _dicts(v)]
    if not isinstance(value, dict):
        return []
    return [value] + [d for v in value.values() for d in _dicts(v)]


def test_the_relation_pattern_sees_what_it_bans() -> None:
    assert _FORBIDDEN_RELATIONS.findall("from curated_cities_matching(x) join city_index_values v on listing_location")


@pytest.mark.parametrize("name", ["get_property_dossier", "get_property_brokers", "get_my_property_context"])
def test_a_property_read_returns_no_forbidden_key_at_any_depth_and_every_per_m2_figure_its_basis(name: str) -> None:
    from tests.api.test_property_read import _broker, run, script
    from toolkit.measures import PPM2_BASES, PPM2_BASIS_KEY, PPM2_VALUE_KEY
    out = run(name, {"property_ids": [7, 9]}, script(brokers=[_broker(71, 1), _broker(81, 3)]))
    rows = _dicts(out["data"])
    assert rows and not {k for d in rows for k in d} & forbidden_columns(), name
    priced = [d for d in rows if d.get(PPM2_VALUE_KEY) is not None]
    assert all(d.get(PPM2_BASIS_KEY) in PPM2_BASES for d in priced), name
    if name != "get_property_brokers":
        assert not [k for d in rows for k in d if k.startswith("broker")], name
    if name == "get_property_dossier":
        assert len(priced) >= 3


def test_an_achieved_price_read_returns_no_forbidden_key_at_any_depth_and_every_per_m2_figure_its_basis() -> None:
    """#2064: the prices achieved label every per-m² figure, row and summary alike, with its basis."""
    from tests.api.test_market_evidence import POINT, _run
    from toolkit.measures import PPM2_BASES, PPM2_BASIS_KEY, PPM2_VALUE_KEY
    rows = _dicts(_run(POINT)[0]["data"])
    assert not {k for d in rows for k in d} & forbidden_columns()
    priced = [d for d in rows if any(d.get(k) is not None for k in (PPM2_VALUE_KEY, "median_price_per_m2", "median"))]
    assert len(priced) >= 4 and all(d.get(PPM2_BASIS_KEY) in PPM2_BASES for d in priced)

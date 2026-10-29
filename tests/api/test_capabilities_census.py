"""Census of the tool registry (#2062): every tool declares its tenancy and effect, no input schema names an
identity, every schema is portable, only the registry opens a connection, and no declared filter is dropped."""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from api.capabilities import registry
from api.capabilities.entry import Caller, Effect, Entry, Tenancy
from api.capabilities.schema import portable
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
            assert {f for f in fields if f.endswith("_id")} <= {"property_id", "listing_id"}, name


# --- 4. portable shape ------------------------------------------------------------------------------------------

_UNPORTABLE = {"$ref", "$defs", "anyOf", "oneOf", "allOf", "title", "const", "exclusiveMinimum", "exclusiveMaximum"}


def _nodes(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [n for x in node for n in _nodes(x)]
    if not isinstance(node, dict):
        return []
    out = [node]
    for key, value in node.items():
        out += [n for sub in value.values() for n in _nodes(sub)] if key == "properties" else _nodes(value)
    return out


def test_every_input_schema_is_portable_to_every_provider() -> None:
    for name, entry in ENTRIES.items():
        schema = entry.input_schema
        assert schema["type"] == "object", name
        for node in _nodes(schema):
            assert not _UNPORTABLE & set(node), (name, _UNPORTABLE & set(node))
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
    return args


def _distinct(prop: dict[str, Any], current: Any) -> Any:
    """A valid value for `prop` that is not `current`."""
    if prop.get("type") == "array":
        return [prop["items"]["enum"][0]] if "enum" in prop["items"] else ["x"]
    if "enum" in prop:
        return next(v for v in prop["enum"] if v != current)
    floor = prop.get("minimum", 0)
    return {"integer": floor + 7, "number": floor + 0.37, "boolean": not current}[prop["type"]]


def _statements(name: str, args: dict[str, Any]) -> list[tuple[str, Any]]:
    conn = ScriptConn([_VELOCITY_SUBJECT])
    registry.call(Caller(market=conn), name, args)
    return conn.executed


@pytest.mark.parametrize("name", sorted(n for n, e in ENTRIES.items()
                                        if any(f in fr.REGISTRY for f in e.args.model_fields)))
def test_every_filter_a_tool_declares_changes_the_statement_it_runs(name: str) -> None:
    entry = ENTRIES[name]
    base = _baseline(entry)
    before = _statements(name, base)
    props = entry.input_schema["properties"]
    dropped = []
    for fid in (f for f in entry.args.model_fields if f in fr.REGISTRY):
        current = base.get(fid, entry.args.model_fields[fid].default)
        if _statements(name, {**base, fid: _distinct(props[fid], current)}) == before:
            dropped.append(fid)
    assert not dropped, f"{name} declares filters that never reach its SQL: {dropped}"


_OTHER_POINT = {"lat": 50.09, "lng": 14.43, "area_m2": 90.0, "disposition": "3+kk", "floor": 5}


@pytest.mark.parametrize("name", sorted(n for n, e in ENTRIES.items() if "point" in e.args.model_fields))
def test_every_point_attribute_a_tool_accepts_changes_the_statement_it_runs(name: str) -> None:
    entry = ENTRIES[name]
    base = _baseline(entry)
    before = _statements(name, base)
    dropped = [f for f in _point_fields(entry)
               if _statements(name, {**base, "point": {**base["point"], f: _OTHER_POINT[f]}}) == before]
    assert not dropped, f"{name} accepts point attributes it never applies: {dropped}"

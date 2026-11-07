"""The agent-tool tests' shared fixtures: the schema walkers, the broker board's totals row, and the scripted relations
the property and market-evidence reads answer from. Census and tool tests import them from here, so neither imports the other."""

from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from api import curation
from api import market_evidence as me
from api import property_read as pr
from api.capabilities import registry
from api.capabilities.entry import Caller
from tests.api._script_conn import ScriptConn
from toolkit.measures import RENT_MONTHLY_CZK_M2, SALE_CAPITAL_CZK_M2

# --- the schema walkers -----------------------------------------------------------------------------------------

def _walk(schema: dict[str, Any], path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """Every (dotted name, property schema) at any depth."""
    out = []
    for name, sub in (schema.get("properties") or {}).items():
        out.append((f"{path}{name}", sub))
        out += _walk(sub, f"{path}{name}.")
        if isinstance(sub.get("items"), dict):
            out += _walk(sub["items"], f"{path}{name}[].")
    return out


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


def _dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [d for v in value for d in _dicts(v)]
    if not isinstance(value, dict):
        return []
    return [value] + [d for v in value.values() for d in _dicts(v)]


# --- the broker board -------------------------------------------------------------------------------------------

def is_board(sql: str) -> bool:
    return "FROM c JOIN broker_book b USING (property_id)" in sql


def head(**over: Any) -> dict[str, Any]:
    """The board statement's one row with no broker: the cohort's totals."""
    return {"cohort_properties": 0, "mixed_main": False, "mixed_type": False, "data_freshness": None,
            "uncovered_portals": [], "attributed_properties": 0, "market_exited": 0, "market_not_exited": 0,
            "market_unknown": 0, "broker_id": None, "properties": None, "exited": None, "not_exited": None,
            "unknown": None, **over}


# --- the property reads -----------------------------------------------------------------------------------------

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def _at(hours: int) -> datetime:
    return T0 + timedelta(hours=hours)


def _row(columns: tuple[str, ...], **values: Any) -> tuple[Any, ...]:
    return tuple(values.get(c) for c in columns)


# Asked: 7, then 9 (merged into 8), then 404 (unknown). 7 has a live canonical ad (71) and an ended one (72); 8 one ad.
PROPS = {
    7: _row(pr.PROPERTY_COLS, property_id=7, listing_id=71, last_seen_at=_at(30), category_main="byt",
            category_type="prodej", price_czk=6_000_000, price_per_m2=Decimal("100000.5"),
            price_per_m2_basis=SALE_CAPITAL_CZK_M2, display_label="Nusle, Praha", total_price_change_pct=Decimal("-5")),
    8: _row(pr.PROPERTY_COLS, property_id=8, listing_id=81, last_seen_at=_at(40), category_main="byt",
            category_type="pronajem", price_czk=20_000, price_per_m2=Decimal("400"),
            price_per_m2_basis=RENT_MONTHLY_CZK_M2),
}
ADS = [
    _row(pr.SOURCE_COLS, id=71, property_id=7, source="sreality", is_active=True, price_czk=6_000_000,
         first_seen_at=_at(0)),
    _row(pr.SOURCE_COLS, id=81, property_id=8, source="idnes", is_active=True, price_czk=20_000, first_seen_at=_at(1),
         ad_reference="N-1"),
    _row(pr.SOURCE_COLS, id=72, property_id=7, source="bazos", is_active=False, price_czk=5_900_000,
         first_seen_at=_at(2)),
]
MEASURES = [(71, "prodej", Decimal("100000.5"), SALE_CAPITAL_CZK_M2), (72, "pronajem", Decimal("300"),
            RENT_MONTHLY_CZK_M2), (81, "pronajem", None, None)]
POINTS = [(71, _at(0), 6_300_000), (71, _at(20), 6_000_000)]
RUNS = [_row(pr.RUN_COLS, id=3, created_at=_at(9), status="success", input_listing_id=81,
             gross_yield_pct=Decimal("4.2")),
        _row(pr.RUN_COLS, id=2, created_at=_at(8), status="failed", input_listing_id=72),
        _row(pr.RUN_COLS, id=1, created_at=_at(7), status="success", input_listing_id=71)]
# The user's own: a card on 8; 7 in their collection 6 and in 5, a colleague's shared with them, tagged and dismissed.
STATES = [_row(curation.STATE_COLS, property_id=8, stage_id=3, stage_key="zajem", stage_label="Zájem",
               entered_stage_at=_at(3), collection_ids=[5], tag_ids=[], dismissed=False),
          _row(curation.STATE_COLS, property_id=7, collection_ids=[5, 6], tag_ids=[2], dismissed=True)]
COLLEAGUE = "kolega@rk.test"
NOTES = [(12, 8, "a colleague's", True, _at(5), _at(5), None, COLLEAGUE, None, None),
         (11, 7, "mine", False, _at(4), _at(4), _at(50), None, None, None)]
COLLECTIONS = [(6, "Mine", None, _at(0), _at(2), 1, True, True, None, [{"account_id": "x", "email": COLLEAGUE}]),
               (5, "Shared with me", None, _at(0), _at(1), 3, False, False, COLLEAGUE, [])]
TAGS = [(2, "balkon", "slate", _at(0), 4)]
STAGES = [(3, "zajem", "Zájem", 1, "sky", False, True, "Z", None), (4, "konec", "Konec", 2, "slate", True, False, None, None)]


def script(props: dict[int, tuple[Any, ...]] = PROPS, runs: list[tuple[Any, ...]] = RUNS,
           brokers: list[dict[str, Any]] = (), notes: list[tuple[Any, ...]] = NOTES) -> ScriptConn:
    """The page's relations for the seeds above, every read answered as its statement would be."""
    return ScriptConn([
        (lambda sql: sql.startswith("WITH RECURSIVE chain"), [(7, 7), (9, 8)]),
        (lambda sql: "from properties_public" in sql, list(props.values())[::-1]),   # never in the asked order
        (lambda sql: "from property_sources_public" in sql, ADS),
        (lambda sql: "from listings_public" in sql, MEASURES),
        (lambda sql: "lag(price_czk)" in sql, POINTS),
        (lambda sql: "from estimation_runs" in sql, runs),
        (lambda sql: "FROM listing_broker_public" in sql, [dict(b) for b in brokers]),
        (lambda sql: "FROM property_pipeline pp" in sql, STATES),
        (lambda sql: "FROM property_notes n WHERE" in sql, notes),
        (lambda sql: "FROM collections c ORDER BY" in sql, COLLECTIONS),
        (lambda sql: "FROM tags t ORDER BY" in sql, TAGS),
        (lambda sql: "FROM pipeline_stages WHERE" in sql, STAGES),
    ])


def run(name: str, args: object, conn: ScriptConn | None = None) -> dict[str, Any]:
    """`name` through the one call path, either tenancy reading `conn`."""
    conn = conn or script()

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        assert (act_as, method) == (None, "GET")
        yield conn
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry.tenant_pool, "tenant_transaction", own)
        return registry.call(Caller(market=conn, claims={"sub": "a"}), name, args)


def _broker(listing_id: int, broker_id: int) -> dict[str, Any]:
    return {"sreality_id": None, "listing_id": listing_id, "broker_id": broker_id,
            "broker_display_name": f"B{broker_id}", "broker_firm_label": "RK", "primary_email": f"b{broker_id}@rk.cz",
            "primary_phone": f"+420 777 000 {broker_id:03d}"}


# --- the market evidence ----------------------------------------------------------------------------------------

POINT = {"point": {"lat": 50.08, "lng": 14.42}, "category_main": "byt", "category_type": "prodej"}
SEEN = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)


def _proxy_row(pid: int, ppm2: float | None = 100_000.0, basis: str | None = SALE_CAPITAL_CZK_M2) -> tuple[Any, ...]:
    facts = {"property_id": pid, "category_main": "byt", "category_type": "prodej", "price_czk": 6_000_000,
             "area_m2": Decimal("60"), "price_per_m2": None if ppm2 is None else Decimal(str(ppm2)),
             "price_per_m2_basis": basis, "tom_days": 30, "last_seen_at": SEEN, "disposition": "2+kk",
             "condition": "dobry", "building_type": "cihla"}
    return (*(facts[c] for c in me.PROXY_COLUMNS), 120.5)


def _sale(n: int, area: float | None = 60.0, ppm2: float | None = 90_000.0, kind: str = "byt",
          basis: str | None = SALE_CAPITAL_CZK_M2) -> tuple[Any, ...]:
    facts = {"source": "reas", "source_record_id": f"r{n}", "sold_at": date(2026, 8, 1), "category_main": kind,
             "price_czk": 5_000_000, "area_m2": None if area is None else Decimal(str(area)),
             "price_per_m2": None if ppm2 is None else Decimal(str(ppm2)), "price_per_m2_basis": basis,
             "disposition": "2+kk", "address_text": "Nusle", "distance_m": 10.0 * n, "sold_age_days": 90,
             "fetched_at": SEEN}
    return tuple(facts[c] for c in me.REGISTERED_COLUMNS)


TOTAL = {"keys": [], "col": None, "n": 6, "mean": 98_000.0, "median": 97_000.0, "p10": 80_000.0, "p25": 90_000.0,
         "p75": 105_000.0, "p90": 110_000.0, "small": None}
PAYLOAD = {"spec": {"rows": [], "cols": [], "measure": "ppm2"}, "filtered": 7, "sample": 6, "excluded": 1,
           "category_main": "byt", "category_type": "prodej", "status": "inactive", "basis": SALE_CAPITAL_CZK_M2,
           "min_n": 5, "refused": None, "places": [], "cells": [TOTAL]}
COVERAGE = (554782, "Praha", SEEN, 120, 400, False, SEEN, "ok")
PROPERTY = tuple({"property_id": 7, "lat": 50.07, "lng": 14.43, "category_main": "dum",
                  "category_type": "prodej"}.get(c) for c in pr.PROPERTY_COLS)


def evidence_script(proxy: list[tuple[Any, ...]] | None = None, cohort: list[int] = (11, 12, 13, 14, 15, 16, 17),
            sales: list[tuple[Any, ...]] | None = None, payload: dict[str, Any] = PAYLOAD,
            prop: tuple[Any, ...] = PROPERTY) -> ScriptConn:
    return ScriptConn([
        (lambda sql: sql.startswith("SELECT property_id FROM listings_public"), [(7,)]),
        (lambda sql: sql.startswith("WITH RECURSIVE chain"), [(7, 7)]),
        (lambda sql: "from properties_public" in sql, [prop]),
        (lambda sql: sql.startswith("SELECT l.property_id, l.category_main"),
         [_proxy_row(i) for i in (11, 12)] if proxy is None else proxy),
        (lambda sql: sql.startswith("SELECT l.property_id FROM browse_list"), [(i,) for i in cohort]),
        (lambda sql: sql.startswith("SELECT public.browse_stats_properties("), [(payload,)]),
        (lambda sql: "FROM public.sold_comparables(" in sql, [_sale(i) for i in range(6)] if sales is None else sales),
        (lambda sql: "FROM public.sold_coverage(" in sql, [COVERAGE]),
    ])


def evidence_run(args: dict[str, Any], conn: ScriptConn | None = None) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    conn = conn or evidence_script()

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        assert (act_as, method) == (None, "GET")
        yield conn
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry.tenant_pool, "tenant_transaction", own)
        out = registry.call(Caller(market=ScriptConn([]), claims={"sub": "a"}), "market_evidence", args)
    return out, conn.executed

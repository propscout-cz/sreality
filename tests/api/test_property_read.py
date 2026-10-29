"""A property as its page reads it (#2063): the dossier and broker tools over the page's own relations and columns.

What they read under a user's login is pinned against the replayed schema in tests/test_capabilities_live.py; here, the
columns (the page's), the statements, the rows they assemble and the broker cap.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from api import property_read as pr
from api.capabilities import registry, truncate
from api.capabilities.entry import Caller
from api.capabilities.errors import ToolError, ToolErrorCode
from api.tenant_pool import READ_BOUND
from tests.api._script_conn import ScriptConn
from tests.test_property_page_read_contract import _select_cols
from toolkit.measures import RENT_MONTHLY_CZK_M2, SALE_CAPITAL_CZK_M2

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


def script(props: dict[int, tuple[Any, ...]] = PROPS, runs: list[tuple[Any, ...]] = RUNS,
           brokers: list[dict[str, Any]] = ()) -> ScriptConn:
    """The page's relations for the seeds above, every read answered as its statement would be."""
    return ScriptConn([
        (lambda sql: sql.startswith("WITH RECURSIVE chain"), [(7, 7), (9, 8)]),
        (lambda sql: "from properties_public" in sql, list(props.values())[::-1]),   # never in the asked order
        (lambda sql: "from property_sources_public" in sql, ADS),
        (lambda sql: "from listings_public" in sql, MEASURES),
        (lambda sql: "lag(price_czk)" in sql, POINTS),
        (lambda sql: "from estimation_runs" in sql, runs),
        (lambda sql: "FROM listing_broker_public" in sql, [dict(b) for b in brokers]),
    ])


def run(name: str, args: object, conn: ScriptConn | None = None, record: Any = None) -> dict[str, Any]:
    """`name` through the one call path, either tenancy reading `conn`."""
    conn = conn or script()

    @contextlib.contextmanager
    def own(claims: dict[str, Any], *, act_as: Any, method: str) -> Any:
        assert (act_as, method) == (None, "GET")
        yield conn
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(registry.tenant_pool, "tenant_transaction", own)
        return registry.call(Caller(market=conn, claims={"sub": "a"}, record=record), name, args)


# --- the page's columns -----------------------------------------------------------------------------------------

def test_the_columns_are_the_pages_own() -> None:
    """The page's property row but the MF breakdown (its per-m² parts carry no basis; compute_reference_rent stamps
    one) and the legacy sreality_id; the page's ad rows; its deal-type and per-m² read; its chart's snapshot columns."""
    assert set(_select_cols("PROPERTY_COLS")) - set(pr.PROPERTY_COLS) == {"mf_reference_rent", "sreality_id"}
    assert set(pr.PROPERTY_COLS) <= set(_select_cols("PROPERTY_COLS"))
    assert pr.SOURCE_COLS == tuple(_select_cols("PROPERTY_SOURCE_COLS"))
    assert set(pr.AD_MEASURE_COLS) <= set(_select_cols("DETAIL_COLS"))
    spa = (Path(__file__).resolve().parents[2] / "frontend/src/lib/queries.ts").read_text(encoding="utf-8")
    assert f".select('{','.join(pr.SNAPSHOT_COLS)}', {{ count: 'exact' }})" in spa


# --- the dossier ------------------------------------------------------------------------------------------------

def test_one_row_per_property_in_the_order_asked_a_merged_id_followed_an_unknown_named() -> None:
    out = run("get_property_dossier", {"property_ids": [7, 9, 404, 7]})
    assert [r["property_id"] for r in out["data"]] == [7, 8]
    meta = out["metadata"]
    assert (meta["tool"], meta["result_count"], meta["resolved"], meta["missing"]) == (
        "get_property_dossier", 2, {9: 8}, [404])
    assert meta["filters_used"] == {"property_ids": [7, 9, 404, 7]}
    assert meta["data_freshness"] == _at(40).isoformat()


def test_each_row_carries_its_facts_its_ads_and_the_runs_on_its_ads() -> None:
    seven, eight = run("get_property_dossier", {"property_ids": [7, 9]})["data"]
    assert set(seven) == {*pr.PROPERTY_COLS, "ads", "estimates"}
    assert (seven["listing_id"], seven["price_per_m2"], seven["total_price_change_pct"]) == (71, 100000.5, -5.0)
    assert seven["last_seen_at"] == _at(30).isoformat()
    canonical, ended = seven["ads"]
    assert canonical == {
        "listing_id": 71, "canonical": True, "source": "sreality", "source_url": None, "source_id_native": None,
        "ad_reference": None, "is_active": True, "price_czk": 6_000_000, "first_seen_at": _at(0).isoformat(),
        "last_seen_at": None, "category_type": "prodej", "price_per_m2": 100000.5,
        "price_per_m2_basis": SALE_CAPITAL_CZK_M2,
        "prices": [{"scraped_at": _at(0).isoformat(), "price_czk": 6_300_000},
                   {"scraped_at": _at(20).isoformat(), "price_czk": 6_000_000}]}
    # An ad of another deal type keeps its own unit; one with no snapshot has no price points.
    assert (ended["listing_id"], ended["canonical"], ended["price_per_m2_basis"], ended["prices"]) == (
        72, False, RENT_MONTHLY_CZK_M2, [])
    assert [r["id"] for r in seven["estimates"]] == [2, 1] and [r["id"] for r in eight["estimates"]] == [3]
    assert set(seven["estimates"][0]) == set(pr.RUN_COLS)
    assert eight["estimates"][0]["gross_yield_pct"] == 4.2


def test_every_read_is_one_statement_over_the_survivors_or_their_ads() -> None:
    conn = script()
    run("get_property_dossier", {"property_ids": [7, 9, 404]}, conn)
    reads = [(sql.split(" from ")[1].split()[0] if " from " in sql else sql.split()[0], params)
             for sql, params in conn.executed if not sql.startswith("SET ")]
    assert reads == [("WITH", {"ids": [7, 9, 404]}), ("properties_public", ([7, 8],)),
                     ("property_sources_public", ([7, 8],)), ("listings_public", ([71, 81, 72],)),
                     ("(select", ([71, 81, 72],)), ("estimation_runs", ([71, 81, 72],))]
    points = next(sql for sql, _ in conn.executed if "lag(price_czk)" in sql)
    assert "partition by listing_id order by scraped_at, id" in points and "description" not in points
    runs = next(sql for sql, _ in conn.executed if "estimation_runs" in sql)
    # The page's [own, SYSTEM] (deps.account_scope): never the admin-only runs of a deleted account.
    assert "and account_id is not null order by created_at desc, id desc" in runs


def _many_runs(n: int) -> list[tuple[Any, ...]]:
    return [_row(pr.RUN_COLS, id=i, created_at=_at(-i), input_listing_id=71) for i in range(1, n + 1)]


def test_a_property_shows_the_newest_runs_the_page_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """The page's cap, with room for a hundred runs (production holds at most five)."""
    monkeypatch.setattr(truncate, "RESULT_BUDGET", 10**6)
    args = registry.validate("get_property_dossier", {"property_ids": [7]})[1]
    seven = pr.dossier(script(runs=_many_runs(130)), args)["data"][0]
    assert [r["id"] for r in seven["estimates"]] == list(range(1, 101)) and "estimates_cut" not in seven


def test_a_property_too_large_alone_keeps_its_newest_runs_that_fit_and_says_so() -> None:
    """Dropped whole, it could never be read: asked again, it would come back empty again."""
    out = run("get_property_dossier", {"property_ids": [7, 9]}, script(runs=_many_runs(130)))
    seven = out["data"][0]
    kept = seven["estimates_cut"]["kept"]
    assert 0 < kept < 100 and seven["estimates_cut"] == {"kept": kept, "total": 100}
    assert [r["id"] for r in seven["estimates"]] == list(range(1, kept + 1))
    assert len(truncate.encode(out)) <= truncate.RESULT_BUDGET
    # Left out whole beside the trimmed 7, 8 is named to ask again for; alone, it fits as it is.
    assert out["metadata"]["truncated"] == {"data": {"kept": 1, "total": 2}}
    eight = run("get_property_dossier", {"property_ids": [9]}, script(runs=_many_runs(130)))["data"][0]
    assert "estimates_cut" not in eight


def test_a_property_too_large_without_its_runs_is_refused_by_name() -> None:
    huge = {7: _row(pr.PROPERTY_COLS, property_id=7, listing_id=71, description="x" * truncate.RESULT_BUDGET)}
    with pytest.raises(ToolError) as info:
        run("get_property_dossier", {"property_ids": [7]}, script(props=huge))
    assert info.value.code is ToolErrorCode.RESULT_TOO_LARGE and "property 7 " in info.value.message


def test_nothing_found_reads_no_relation_past_the_survivors() -> None:
    conn = ScriptConn([])
    out = run("get_property_dossier", {"property_ids": [404]}, conn)
    assert out["data"] == [] and out["metadata"]["missing"] == [404]
    assert [sql.split()[0] for sql, _ in conn.executed if not sql.startswith("SET ")] == ["WITH"]


def test_an_oversized_dossier_drops_trailing_properties_whole() -> None:
    wide = {i: _row(pr.PROPERTY_COLS, property_id=i, listing_id=i * 10, description="x" * 3_000)
            for i in range(1, 21)}
    conn = script(props=wide)
    conn.script[0] = (lambda sql: sql.startswith("WITH RECURSIVE chain"), [(i, i) for i in wide])
    out = run("get_property_dossier", {"property_ids": list(wide)}, conn)
    kept = [r["property_id"] for r in out["data"]]
    assert kept == list(range(1, len(kept) + 1)) and 0 < len(kept) < 20
    assert out["metadata"]["truncated"] == {"data": {"kept": len(kept), "total": 20}}
    assert len(truncate.encode(out)) <= truncate.RESULT_BUDGET


@pytest.mark.parametrize("args", [{"property_ids": []}, {"property_ids": list(range(1, 22))},
                                  {"property_ids": [0]}, {"property_ids": [2**63]},
                                  {"property_ids": [7], "listing_ids": [71]}])
@pytest.mark.parametrize("name", ["get_property_dossier", "get_property_brokers"])
def test_up_to_twenty_property_ids_and_nothing_else_or_no_database_is_touched(name: str, args: dict[str, Any]) -> None:
    conn, records = ScriptConn([]), []
    with pytest.raises(ToolError) as info:
        run(name, args, conn, record=lambda *r: records.append(r))
    assert info.value.code is ToolErrorCode.INVALID_ARGUMENTS
    assert not conn.executed and records == [(name, args, info.value.payload())]


# --- the brokers ------------------------------------------------------------------------------------------------

def _broker(listing_id: int, broker_id: int) -> dict[str, Any]:
    return {"sreality_id": None, "listing_id": listing_id, "broker_id": broker_id,
            "broker_display_name": f"B{broker_id}", "broker_firm_label": "RK", "primary_email": f"b{broker_id}@rk.cz",
            "primary_phone": f"+420 777 000 {broker_id:03d}"}


def test_brokers_are_the_pages_the_canonical_ads_first_contacts_shown_and_the_call_recorded() -> None:
    records: list[tuple[Any, ...]] = []
    out = run("get_property_brokers", {"property_ids": [9, 7]}, script(brokers=[
        _broker(71, 1), _broker(72, 2), _broker(81, 3)]), record=lambda *r: records.append(r))
    assert out["data"] == [
        {"property_id": 8, "broker_id": 3, "broker_display_name": "B3", "broker_firm_label": "RK",
         "primary_phone": "+420 777 000 003", "primary_email": "b3@rk.cz", "from_inactive": False,
         "ads": [{"listing_id": 81, "source": "idnes", "ad_reference": "N-1", "is_active": True}]},
        # 72 has ended, so its broker is not 7's.
        {"property_id": 7, "broker_id": 1, "broker_display_name": "B1", "broker_firm_label": "RK",
         "primary_phone": "+420 777 000 001", "primary_email": "b1@rk.cz", "from_inactive": False,
         "ads": [{"listing_id": 71, "source": "sreality", "ad_reference": None, "is_active": True}]},
    ]
    meta = out["metadata"]
    assert (meta["tool"], meta["pii_masked"], meta["result_count"], meta["brokers"], meta["unlisted_property_ids"],
            meta["resolved"]) == ("get_property_brokers", False, 2, {"kept": 2, "total": 2}, [], {9: 8})
    assert records == [("get_property_brokers", {"property_ids": [9, 7]}, out)]


def test_a_call_stops_at_the_first_row_of_the_twenty_first_person() -> None:
    """7's one live ad and 8's carry 21 people between them once 8 has twenty: the call lists 7's, then 8's first 19,
    and names 8 as the property to ask for again."""
    ads = [_row(pr.SOURCE_COLS, id=71, property_id=7, source="sreality", is_active=True)] + [
        _row(pr.SOURCE_COLS, id=800 + n, property_id=8, source="idnes", is_active=True) for n in range(20)]
    conn = script(brokers=[_broker(71, 1)] + [_broker(800 + n, 100 + n) for n in range(20)])
    conn.script[2] = (lambda sql: "from property_sources_public" in sql, ads)
    out = run("get_property_brokers", {"property_ids": [7, 9]}, conn)
    assert [(r["property_id"], r["broker_id"]) for r in out["data"]] == [(7, 1)] + [(8, 100 + n) for n in range(19)]
    meta = out["metadata"]
    assert (meta["result_count"], meta["brokers"], meta["unlisted_property_ids"]) == (
        21, {"kept": pr.BROKERS_MAX, "total": 21}, [8])


def test_the_brokers_are_read_on_the_market_connection_bounded_like_a_page_read() -> None:
    conn = script(brokers=[_broker(71, 1)])
    run("get_property_brokers", {"property_ids": [7]}, conn)
    assert tuple(sql for sql, _ in conn.executed[:2]) == READ_BOUND
    assert conn.tx == ["commit"]
    assert next(p for sql, p in conn.executed if "listing_broker_public" in sql) == ([71, 72, 81],)

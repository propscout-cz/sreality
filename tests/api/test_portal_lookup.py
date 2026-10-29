"""Tests for POST /listings/lookup — the Chrome extension's batch (source,
native id) → MF rent/yield + the property's header facts + latest estimate.

The logic tests drive `lookup_portal_listings` against a tiny fake cursor that
returns dict rows (the real query uses psycopg's dict_row factory); the route
tests exercise the bearer gate, request validation, and delegation.

Since W3 the read is RLS-only: it takes no account argument and its SQL carries
no account predicate, so RLS is the single definition of membership shared with
the SPA. The property's state is `curation.property_state`, the read an agent's
own context makes too (#2063).
"""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from api import curation
from api import dependencies as deps
from api import main as api_main
from api import portal_lookup as pl
from api import schemas as s
from api import tenant_pool
from tests.api._script_conn import ScriptConn
from tests.test_sql_placeholders import _invalid_placeholder
from toolkit.measures import plot_area_sql

_USER = uuid.uuid4()


# ----------------------------------------------------------------------
# Logic: lookup_portal_listings against a fake dict-row cursor.
# ----------------------------------------------------------------------

# The panel header's property facts (MS11), off the same `pp` row as MF.
_PROPERTY_FACTS = (
    "canonical_listing_id", "property_is_active", "tom_days", "floor",
    "total_floors", "condition", "ownership", "property_subtype", "plot_area_m2",
)


def _mk_market_row(source: str, source_id: str, found: bool, **cols: Any) -> dict[str, Any]:
    """One market-query dict row (service-role conn) keyed by SELECT aliases."""
    row: dict[str, Any] = {
        "source": source, "source_id": source_id, "found": found,
        "listing_id": None, "property_id": None,
        "category_main": None, "category_type": None,
        "area_m2": None, "area_basis": None,
        "price_czk": None, "price_per_m2": None, "price_per_m2_basis": None,
        "disposition": None, "display_label": None, "is_active": None,
        "mf_reference_rent_czk": None,
        "mf_gross_yield_pct": None, "mf_reference_rent": None,
        **dict.fromkeys(_PROPERTY_FACTS),
    }
    row.update(cols)
    return row


def _mk_estimate_row(listing_id: int, **cols: Any) -> dict[str, Any]:
    """One estimation dict row (tenant conn, RLS-scoped) per found listing with a successful run."""
    return {"listing_id": listing_id, "estimation_id": None, "estimation_kind": None,
            "estimation_yield": None, **cols}


def _mk_state_row(property_id: int, **cols: Any) -> tuple[Any, ...]:
    """One curation.property_state row (tenant conn, RLS-scoped) per found property."""
    # The SQL coalesces the id lists to an empty array (never NULL); the Python
    # layer nulls them for no-property rows.
    row = {"property_id": property_id, "collection_ids": [], "tag_ids": [], "dismissed": False, **cols}
    return tuple(row.get(c) for c in curation.STATE_COLS)


def _tenant(estimates: list[dict[str, Any]] = (), states: list[tuple[Any, ...]] = ()) -> ScriptConn:
    """The tenant connection: each found ad's latest estimate, then its property's state."""
    return ScriptConn([
        (lambda q: "FROM estimation_runs" in q, list(estimates)),
        (lambda q: "FROM property_pipeline" in q, list(states)),
    ])


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: tuple[str, list[Any]] | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: list[Any]) -> None:
        self.executed = (sql, params)

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.cur = _FakeCursor(rows)

    def cursor(self, **_kwargs: Any) -> _FakeCursor:  # accepts row_factory=dict_row
        return self.cur


_TS = datetime(2026, 6, 1, 12, 0, 0)


def _items(*pairs: tuple[str, str]) -> list[s.PortalLookupItem]:
    return [s.PortalLookupItem(source=src, source_id=sid) for src, sid in pairs]


def test_lookup_maps_rows_with_mf_and_estimation() -> None:
    market_rows = [
        # sreality: found, has MF
        _mk_market_row("sreality", "1184977484", True,
                       listing_id=9001, property_id=501,
                       category_main="byt", category_type="prodej",
                       area_m2=Decimal("65.0"),
                       price_czk=4_800_000, disposition="2+kk",
                       display_label="Plzeňská 12, Praha", is_active=True,
                       mf_reference_rent_czk=21_840,
                       mf_gross_yield_pct=Decimal("5.46")),
        # bazos: found, no MF; has a property but is NOT in the pipeline
        _mk_market_row("bazos", "220291221", True,
                       listing_id=9002, property_id=777,
                       category_main="komercni", category_type="prodej",
                       price_czk=7_700_000, display_label="Pardubice", is_active=True),
        # idnes: not found
        _mk_market_row("idnes", "deadbeef", False),
    ]
    # sreality listing: successful estimation + bookmarked + in collections
    tenant = _tenant(
        [_mk_estimate_row(9001, estimation_id=99, estimation_kind="rent",
                          estimation_yield=Decimal("5.46"))],
        [_mk_state_row(501, stage_id=3, stage_key="interested", stage_label="Zájem",
                       entered_stage_at=_TS, collection_ids=[7, 9], tag_ids=[3]),
         # bazos listing: nothing account-side but a dismissal
         _mk_state_row(777, dismissed=True)],
    )
    out = pl.lookup_portal_listings(
        _FakeConn(market_rows), tenant,
        _items(("sreality", "1184977484"), ("bazos", "220291221"),
               ("idnes", "deadbeef")),
    )
    data = out["data"]
    assert [d["source"] for d in data] == ["sreality", "bazos", "idnes"]

    sr = data[0]
    # The extension's contract, key for key and in order.
    assert list(sr) == ["source", "source_id", "found", *pl._LISTING_COLS, "fond_per_m2_czk_default",
                        "latest_estimation", "pipeline", "collection_ids", "tag_ids", "dismissed"]
    assert sr["found"] is True
    assert sr["property_id"] == 501
    assert sr["area_m2"] == 65.0  # Decimal → float
    assert sr["mf_gross_yield_pct"] == 5.46
    assert sr["disposition"] == "2+kk"
    assert sr["latest_estimation"] == {
        "estimation_id": 99, "estimate_kind": "rent", "gross_yield_pct": 5.46,
    }
    assert sr["pipeline"] == {  # bookmarked → entry-stage membership
        "in_pipeline": True, "stage_id": 3,
        "stage_key": "interested", "stage_label": "Zájem",
        # Badge + accent (migration 377) so the panel's funnel matches the SPA's.
        "stage_code": None, "stage_color": None,
        # The panel's "ve fázi od": the card's stage stamp, datetime → iso8601.
        "entered_stage_at": _TS.isoformat(),
    }
    assert sr["collection_ids"] == [7, 9]  # property-grain memberships
    assert sr["tag_ids"] == [3]  # property-grain, like collections
    assert sr["dismissed"] is False

    bz = data[1]
    assert bz["found"] is True
    assert bz["latest_estimation"] is None
    assert bz["property_id"] == 777
    # has a property but no card → in_pipeline false (still toggle-able)
    assert bz["pipeline"] == {
        "in_pipeline": False, "stage_id": None,
        "stage_key": None, "stage_label": None,
        "stage_code": None, "stage_color": None, "entered_stage_at": None,
    }
    assert bz["collection_ids"] == []  # has a property, in no collection
    assert bz["tag_ids"] == []
    assert bz["dismissed"] is True  # property-grain, like the two above

    idn = data[2]
    assert idn["found"] is False
    assert idn["listing_id"] is None
    assert idn["latest_estimation"] is None
    assert idn["property_id"] is None
    assert idn["pipeline"] is None  # no property → nothing to bookmark
    assert idn["collection_ids"] is None  # no property → null, not []
    assert idn["tag_ids"] is None
    assert idn["dismissed"] is None  # no property → nothing to dismiss


# Today's stored breakdown (no `status` key): the panel renders it as a value.
_MF_DETAIL = {
    "vk": 3, "area_m2": 75, "base_per_m2": 184, "total_per_m2": 236,
    "territory": {"kraj": "Kraj Vysočina", "name": "Moravské Budějovice",
                  "level": "ku", "ruian_code": 698903},
    "adjustments": [{"attribute": "balcony", "czk_per_m2": 5}],
    "is_novostavba": False, "source_revision": 2, "monthly_rent_czk": 17_700,
    "adjustments_sum_per_m2": 52,
}


def test_lookup_serves_the_property_mf_result_and_none_before_attach() -> None:
    """The attached advert carries its PROPERTY's MF result whole — the jsonb
    the panel renders by shape; the pre-attach one (property_id NULL) has no
    property, so no MF at all: no coalesce to a listing-grain value, no per-m²
    quotient of the client's own."""
    market_rows = [
        _mk_market_row("bezrealitky", "attached", True, listing_id=31,
                       property_id=900, category_main="byt", category_type="prodej",
                       mf_reference_rent_czk=17_700,
                       mf_gross_yield_pct=Decimal("5.25"),
                       mf_reference_rent=_MF_DETAIL),
        _mk_market_row("bezrealitky", "fresh", True, listing_id=32,
                       property_id=None, category_main="byt", category_type="prodej"),
    ]
    out = pl.lookup_portal_listings(
        _FakeConn(market_rows), _tenant(),
        _items(("bezrealitky", "attached"), ("bezrealitky", "fresh")),
    )
    attached, fresh = out["data"]
    assert attached["mf_reference_rent"] == _MF_DETAIL
    assert attached["mf_reference_rent_czk"] == 17_700
    assert attached["mf_gross_yield_pct"] == 5.25
    for key in ("mf_reference_rent", "mf_reference_rent_czk", "mf_gross_yield_pct"):
        assert fresh[key] is None, key
    for entry in (attached, fresh):
        assert "mf_reference_rent_per_m2_czk" not in entry


def test_lookup_reads_mf_from_properties_public_by_property_id() -> None:
    """ONE source for MF: the property row every other surface reads. The
    listing-grain fallback and the per-m² re-derivation are gone."""
    code = "\n".join(line.split("--")[0] for line in pl._MARKET_SQL.splitlines())
    assert "LEFT JOIN properties_public pp ON pp.property_id = l.property_id" in code
    assert "JOIN properties " not in code
    assert "l.mf_" not in code
    # The one COALESCE is the label's (ruling R9): an advert not yet attached keeps its own.
    assert code.lower().count("coalesce(") == 1
    assert "COALESCE(pp.display_label," in code
    assert "'pronajem'" not in code
    for col in ("mf_reference_rent_czk", "mf_gross_yield_pct", "mf_reference_rent"):
        assert f"pp.{col}" in code
        assert col in pl._LISTING_COLS
    assert "mf_reference_rent_per_m2_czk" not in pl._LISTING_COLS


def test_lookup_serves_the_property_facts_beside_the_adverts_own_status() -> None:
    """A merged house seen on its delisted non-canonical advert: the header facts (status
    included) are the PROPERTY's, the advert's own `is_active` keeps its meaning, and an
    advert not yet attached has no property facts (no coalesce to its own)."""
    market_rows = [
        _mk_market_row("idnes", "merged", True, listing_id=41, property_id=901,
                       category_main="dum", category_type="prodej",
                       is_active=False, property_is_active=True,
                       canonical_listing_id=40, tom_days=94,
                       total_floors=2, condition="velmi_dobry", ownership="osobni",
                       property_subtype="rodinny_dum", plot_area_m2=Decimal("905.0")),
        _mk_market_row("idnes", "fresh", True, listing_id=42, property_id=None,
                       category_main="dum", category_type="prodej"),
    ]
    merged, fresh = pl.lookup_portal_listings(
        _FakeConn(market_rows), _tenant(), _items(("idnes", "merged"), ("idnes", "fresh")),
    )["data"]
    assert merged["canonical_listing_id"] == 40
    assert (merged["is_active"], merged["property_is_active"]) == (False, True)
    assert (merged["tom_days"], merged["floor"], merged["total_floors"]) == (94, None, 2)
    assert (merged["condition"], merged["ownership"]) == ("velmi_dobry", "osobni")
    assert merged["plot_area_m2"] == 905.0 and type(merged["plot_area_m2"]) is float
    assert merged["property_subtype"] == "rodinny_dum"
    for key in _PROPERTY_FACTS:
        assert fresh[key] is None, key


def test_lookup_reads_the_property_facts_off_pp_and_the_plot_measure() -> None:
    code = "\n".join(line.split("--")[0] for line in pl._MARKET_SQL.splitlines())
    assert f"{plot_area_sql('pp')} AS plot_area_m2" in code
    assert "estate_area" not in code.replace(plot_area_sql("pp"), "")
    assert "pp.listing_id AS canonical_listing_id" in code
    assert "pp.is_active AS property_is_active" in code
    assert "pp.subtype AS property_subtype" in code
    for col in ("tom_days", "floor", "total_floors", "condition", "ownership"):
        assert f"pp.{col}" in code and f"l.{col}" not in code, col
    assert set(_PROPERTY_FACTS) <= set(pl._LISTING_COLS)
    # An f-string constant leaves the offline `%` sweep (tests/sql_corpus.py reads literals only).
    assert _invalid_placeholder(pl._MARKET_SQL) is None


def test_lookup_binds_one_value_pair_per_item() -> None:
    market_conn, tenant_conn = _FakeConn([]), _tenant()
    pl.lookup_portal_listings(
        market_conn, tenant_conn, _items(("sreality", "1"), ("idnes", "abc")),
    )
    sql, params = market_conn.cur.executed
    # two (%s::text, %s::text) tuples → 4 bound params, in request order
    assert params == ["sreality", "1", "idnes", "abc"]
    assert sql.count("%s") == 4
    # no found rows → the tenant conn is never queried at all
    assert tenant_conn.executed == []


def test_lookup_tenant_reads_target_only_found_ads_and_bind_no_account() -> None:
    market_rows = [
        _mk_market_row("sreality", "a", True, listing_id=11, property_id=100,
                       category_main="byt", category_type="prodej", is_active=True),
        # A second ad of the same property, and one not yet attached to any.
        _mk_market_row("idnes", "b", True, listing_id=13, property_id=100,
                       category_main="byt", category_type="prodej", is_active=True),
        _mk_market_row("bazos", "c", True, listing_id=12,
                       category_main="byt", category_type="prodej", is_active=True),
        _mk_market_row("idnes", "miss", False),
    ]
    tenant = _tenant()
    pl.lookup_portal_listings(
        _FakeConn(market_rows), tenant,
        _items(("sreality", "a"), ("idnes", "b"), ("bazos", "c"), ("idnes", "miss")),
    )
    # Each found ad's estimate, each held property's state once — the miss
    # excluded — and NOTHING else: no account is bound, because none is taken.
    (est_sql, est_params), (state_sql, state_params) = tenant.executed
    assert (est_params, state_params) == (([11, 13, 12],), ([100],))
    assert est_sql.count("%s") == state_sql.count("%s") == 1  # nothing else CAN be bound
    # RLS-only by construction. Not "the predicate binds the right value" but
    # "there is no predicate left to bind wrong" — the shape #917 broke is gone.
    # `account_id` survives ONLY as the LATERAL's deterministic tiebreak.
    for sql in (pl._ESTIMATION_SQL, curation._PROPERTY_STATE_SQL):
        code = "\n".join(line.split("--")[0] for line in sql.splitlines())
        assert "IS NOT DISTINCT FROM" not in code
        assert "account_id" not in code.replace("ORDER BY pp.updated_at DESC, pp.account_id", "")


def test_lookup_preserves_request_order_even_if_db_reorders() -> None:
    rows = [
        _mk_market_row("idnes", "b", True, listing_id=22,
                       category_main="byt", category_type="prodej", is_active=True),
        _mk_market_row("sreality", "a", True, listing_id=21,
                       category_main="byt", category_type="prodej", is_active=True),
    ]
    out = pl.lookup_portal_listings(
        _FakeConn(rows), _tenant(), _items(("sreality", "a"), ("idnes", "b")),
    )
    assert [d["source_id"] for d in out["data"]] == ["a", "b"]


def test_fond_default_is_withheld_wherever_the_denominator_is_a_parcel() -> None:
    """The fond rate is multiplied by `area_m2`, so it inherits that basis.

    A monthly Kč/m² service charge levied on 905 m² of PLOT manufactures
    ~9 050 Kč/month of cost that a yield then subtracts from the rent — enough
    to turn the figure negative. Both stamps that name the denominator are
    consulted, because `measure_price_per_m2_basis` cannot do this job: it
    resolves RENT-FIRST, so the plot-to-let row below labels
    `rent_monthly_czk_m2` and would slip a land-basis test.
    """
    rows = [
        # A flat for sale: floor area, a fond genuinely applies.
        _mk_market_row("sreality", "flat", True, listing_id=1,
                       category_main="byt", category_type="prodej",
                       area_m2=Decimal("65.0"), area_basis="usable"),
        # A plot for sale: area_m2 IS the parcel.
        _mk_market_row("sreality", "plot", True, listing_id=2,
                       category_main="pozemek", category_type="prodej",
                       area_m2=Decimal("905.0"), area_basis="plot"),
        # A plot TO LET — the rent-first trap: no area_basis stamp at all, so
        # only `category_main` says the denominator is a parcel.
        _mk_market_row("sreality", "plot-rent", True, listing_id=3,
                       category_main="pozemek", category_type="pronajem",
                       area_m2=Decimal("905.0"), area_basis=None),
        # Not in our DB: nothing is known that says a fond doesn't apply.
        _mk_market_row("idnes", "unknown", False),
    ]
    out = pl.lookup_portal_listings(
        _FakeConn(rows), _tenant(),
        _items(("sreality", "flat"), ("sreality", "plot"),
               ("sreality", "plot-rent"), ("idnes", "unknown")),
    )
    served = [d["fond_per_m2_czk_default"] for d in out["data"]]
    assert served == [s.DEFAULT_FOND_CZK_PER_M2, None, None,
                      s.DEFAULT_FOND_CZK_PER_M2]


def test_fond_default_is_served_for_an_item_with_no_row_at_all() -> None:
    """The panel seeds its fond field from this key alone (it holds no literal),
    so the defensive no-row shape must carry it too or the field goes blank."""
    out = pl.lookup_portal_listings(
        _FakeConn([]), _tenant(), _items(("sreality", "nothing")),
    )
    assert out["data"][0]["fond_per_m2_czk_default"] == s.DEFAULT_FOND_CZK_PER_M2


def test_the_card_stage_stamp_rides_the_pipeline_lateral() -> None:
    """The panel's "ve fázi od" reads the caller's card row, the same LATERAL that
    picks the stage (a stamp off another account's card would date the wrong move)."""
    head, lateral = curation._PROPERTY_STATE_SQL.split("LEFT JOIN LATERAL")
    assert "pp.entered_stage_at" in lateral
    assert "pc.stage_id, pc.key, pc.label, pc.code, pc.color, pc.entered_stage_at" in head
    assert curation.STATE_COLS[1:7] == ("stage_id", "stage_key", "stage_label", "stage_code",
                                        "stage_color", "entered_stage_at")


def test_lookup_takes_no_account_argument() -> None:
    """The assertable form of "this read has no second definition of membership":
    there is no parameter an account could be passed to, so none can be dropped."""
    assert "account_id" not in inspect.signature(pl.lookup_portal_listings).parameters
    assert "account_id" not in inspect.signature(curation.property_state).parameters


def test_lookup_never_multiplies_rows_for_a_multi_membership_caller() -> None:
    """RLS is plural — two accounts' cards on one property are two rows to the
    tenant query. The SQL's LATERAL collapses them; this pins that the Python
    shaping cannot undo that (one output row per REQUESTED item, always)."""
    market_rows = [
        _mk_market_row("sreality", "a", True, listing_id=11,
                       property_id=100, category_main="byt",
                       category_type="prodej", is_active=True),
    ]
    # Two rows for property 100 — what an un-collapsed plural join would return.
    states = [
        _mk_state_row(100, stage_id=3, stage_key="interested", stage_label="Zájem"),
        _mk_state_row(100, stage_id=8, stage_key="offer", stage_label="Nabídka"),
    ]
    items = _items(("sreality", "a"))
    out = pl.lookup_portal_listings(
        _FakeConn(market_rows), _tenant(states=states), items,
    )
    assert len(out["data"]) == len(items)
    assert out["data"][0]["pipeline"]["in_pipeline"] is True


# ----------------------------------------------------------------------
# Route: POST /listings/lookup
# ----------------------------------------------------------------------

@pytest.fixture()
def client(monkeypatch) -> Any:
    # The route runs on the tenant pool since Phase 1 — stub the whole
    # tenant_conn chain (auth included) for delegation/validation tests; the
    # auth gate itself is exercised by test_route_fails_closed_without_token.
    # resolve_account_id RAISES: the route is RLS-only, so reaching account
    # resolution at all is the regression (a resolved account is a second
    # definition of membership, and #917 proved it can silently bind NULL).
    def _no_account(conn: Any, claims: dict) -> None:
        raise AssertionError("route must not resolve an account")

    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    api_main.app.dependency_overrides[tenant_pool.tenant_conn] = lambda: object()
    api_main.app.dependency_overrides[deps.verify_jwt] = lambda: {"sub": str(_USER)}
    monkeypatch.setattr(tenant_pool, "resolve_account_id", _no_account)
    yield TestClient(api_main.app)
    api_main.app.dependency_overrides.clear()


def test_route_delegates_and_returns_data(client, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_lookup(market_conn, tenant_conn, items):
        captured["items"] = items
        return {"data": [{"source": "sreality", "source_id": "1", "found": True}]}

    monkeypatch.setattr(api_main, "lookup_portal_listings", fake_lookup)
    res = client.post(
        "/listings/lookup",
        json={"items": [{"source": "sreality", "source_id": "1"}]},
    )
    # 200 with the fixture's raising resolve_account_id in place: the route
    # never asked who the caller is — RLS on the tenant connection did.
    assert res.status_code == 200
    assert res.json()["data"][0]["source"] == "sreality"
    assert [(i.source, i.source_id) for i in captured["items"]] == [("sreality", "1")]


def test_route_rejects_empty_items(client) -> None:
    res = client.post("/listings/lookup", json={"items": []})
    assert res.status_code == 422


def test_route_rejects_over_50_items(client) -> None:
    items = [{"source": "sreality", "source_id": str(i)} for i in range(51)]
    res = client.post("/listings/lookup", json={"items": items})
    assert res.status_code == 422


def test_route_fails_closed_without_token(monkeypatch) -> None:
    """No tenant_conn override here — the real verify_jwt chain must 401 a
    missing bearer (fail-closed, Phase 1)."""
    api_main.app.dependency_overrides[deps.get_db_conn] = lambda: object()
    try:
        res = TestClient(api_main.app).post(
            "/listings/lookup",
            json={"items": [{"source": "sreality", "source_id": "1"}]},
        )
        assert res.status_code == 401
    finally:
        api_main.app.dependency_overrides.clear()

"""The building load's writes, against a real schema: a changed building gets a new row, a
vanished one is closed, and an address point's building code follows the file. Gated on
TEST_DATABASE_URL (the schema-replay job); everything rolls back.
"""

from __future__ import annotations

import dataclasses
import datetime
import os

import pytest

_DB_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not _DB_URL, reason="TEST_DATABASE_URL not set")

from location_data import ruian_buildings as rb  # noqa: E402
from location_data import ruian_vfr  # noqa: E402

DAY = datetime.date(2026, 10, 31)
OBEC, CAST, ADM, OTHER_ADM = 990001, 990002, 99000001, 99000002

BUILDING = ruian_vfr.BuildingRow(
    code=77000001, plati_od=datetime.date(2023, 12, 18), typ_so=1, usage_code=6, iskn_building_id=896183101,
    identifying_parcel_id=2099603101, cast_obce_kod=CAST, momc_kod=None, house_numbers=(727, 728),
    completed_on=datetime.date(1945, 12, 31), construction_code=7, floors=8, dwelling_count=75,
    built_up_area_m2=2270, floor_area_m2=None, enclosed_volume_m3=None, lift_code=1, water_code=1,
    sewer_code=1, gas_code=1, heating_code=2, protection_codes=(16, 18))


@pytest.fixture
def conn():
    import psycopg

    with psycopg.connect(_DB_URL) as c:
        try:
            yield c
        finally:
            c.rollback()


@pytest.fixture
def world(conn):
    with conn.cursor() as cur:
        cur.execute(
            "insert into registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline) "
            "values ('test:buildings', 'baseline', %s, '{}', 'p', 'q') returning id", (DAY,))
        version = rb.Version(int(cur.fetchone()[0]), DAY)
        units = {}
        for level, code in (("obec", OBEC), ("cast_obce", CAST)):
            cur.execute(
                "insert into ruian_admin_units (level, code, name, name_norm, path, valid_from, "
                "first_version_id, last_version_id) values (%s::ruian_level, %s, %s, %s, %s::ltree, %s, %s, %s) "
                "returning id", (level, code, str(code), str(code), f"t{code}", DAY, version.id, version.id))
            units[code] = int(cur.fetchone()[0])
        for kod in (ADM, OTHER_ADM):
            cur.execute(
                "insert into ruian_address_points (kod_adm, obec_unit_id, obec_kod, psc, plati_od, "
                "first_version_id, last_version_id) values (%s, %s, %s, '11000', %s, %s, %s)",
                (kod, units[OBEC], OBEC, DAY, version.id, version.id))
    return version, units


def _current(conn, code: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "select valid_from, floors, typ_so, house_numbers, protection_codes, cast_obce_unit_id, "
            "definition_point, valid_to from ruian_building_objects "
            "where code = %s order by valid_from", (code,))
        return cur.fetchall()


def _link(conn, kod: int) -> int | None:
    with conn.cursor() as cur:
        cur.execute("select stavebni_objekt_code from ruian_address_points where kod_adm = %s", (kod,))
        return cur.fetchone()[0]


def _load(conn, world, rows, obec_kod=OBEC, sha="a"):
    version, units = world
    return rb.write_obec(conn, version, units[OBEC], obec_kod, rows, sha)


def test_a_building_lands_with_what_the_register_states(conn, world):
    assert _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)]) == (1, 1)
    assert _current(conn, BUILDING.code) == [
        (BUILDING.plati_od, 8, "č.p.", [727, 728], [16, 18], world[1][CAST], None, None)]
    assert (_link(conn, ADM), _link(conn, OTHER_ADM)) == (BUILDING.code, None)


def _property(conn, door: int, *categories: str) -> int:
    """A live property at `door` holding one ad per category; the first is the ad it stands at and shows."""
    with conn.cursor() as cur:
        ads = []
        for category in categories:
            cur.execute(
                "insert into listings (source, source_id_native, raw_json, category_main, category_type) "
                "values ('bazos', %s, '{}'::jsonb, %s, 'prodej') returning id", (f"scope-{door}-{len(ads)}", category))
            ads.append(int(cur.fetchone()[0]))
        cur.execute(
            "insert into listing_location (listing_id, match_confidence, granularity, uncertainty_radius_m, "
            "country_status, resolver_version, claim_set_hash, registry_version, kraj_kod, okres_kod, obec_kod, "
            "ruian_adm_kod) values (%s, 'exact', 'address_point', 5, 'cz', 'test', '\\x00'::bytea, 'test', 99, 9991, "
            "%s, %s)", (ads[0], OBEC, door))
        cur.execute("insert into properties (status, location_listing_ref_id, repr_listing_ref_id) "
                    "values ('active', %s, %s) returning id", (ads[0], ads[0]))
        property_id = int(cur.fetchone()[0])
        cur.execute("update listings set property_id = %s where id = any(%s)", (property_id, ads))
    return property_id


@pytest.mark.parametrize(("categories", "served"), [
    (("byt",), True), (("dum",), True), (("komercni",), True), (("byt", "komercni"), True),
    (("pozemek",), False), (("ostatni",), False),
    (("dum", "pozemek"), False),  # one plot ad is enough, although the property shows the house
])
def test_the_building_reads_serve_no_property_that_holds_a_plot_or_other_ad(conn, world, categories, served):
    from api import cadastre_prices
    from api.property_building import load_building
    from api.property_building_sales import load_building_sales

    _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)])
    with conn.cursor() as cur:
        cur.execute(
            "insert into sold_transactions (source, source_record_id, sold_at, price_czk, category_main, "
            "category_type, geom, raw) values ('reas', %s, '2025-01-01', 1000000, 'byt', 'prodej', "
            "ST_SetSRID(ST_MakePoint(14.4, 50.1), 4326), '{}')", (f"1_building_{BUILDING.code}",))
    property_id = _property(conn, ADM, *categories)
    building = load_building(conn, property_id)
    assert (building is not None, len(load_building_sales(conn, property_id))) == (served, int(served))
    if served:  # the purchase asks the same question before it orders anything
        assert cadastre_prices.building_of(conn, property_id).code == BUILDING.code
    else:
        with pytest.raises(cadastre_prices.NoBuilding):
            cadastre_prices.building_of(conn, property_id)


def test_every_ad_of_the_property_is_compared_with_the_record(conn, world):
    from api.property_building import load_building

    _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)])  # 8 floors, with a lift
    property_id = _property(conn, ADM, "byt", "byt")
    with conn.cursor() as cur:
        cur.execute("select id from listings where property_id = %s order by id", (property_id,))
        shown, other = (int(r[0]) for r in cur.fetchall())
        cur.execute("update listings set has_lift = false, floor = 8, total_floors = 8 where id = %s", (shown,))
        cur.execute("update listings set has_lift = true, total_floors = 7 where id = %s", (other,))
    assert load_building(conn, property_id)["ads"] == {
        str(shown): {"lift": {"differs": True, "ad": False},
                     "floors": {"differs": True, "ad": {"total_floors": 8, "floor": 8}}, "construction": None},
        str(other): {"lift": {"differs": False, "ad": True},
                     "floors": {"differs": False, "ad": {"total_floors": 7, "floor": None}}, "construction": None},
    }


def test_a_changed_building_gets_a_new_row_and_the_old_one_is_closed(conn, world):
    _load(conn, world, [BUILDING])
    changed = dataclasses.replace(BUILDING, plati_od=datetime.date(2026, 10, 1), floors=9)
    _load(conn, world, [changed])
    rows = _current(conn, BUILDING.code)
    assert [(r[0], r[1], r[7]) for r in rows] == [
        (BUILDING.plati_od, 8, changed.plati_od), (changed.plati_od, 9, None)]


def test_a_building_gone_from_the_file_is_closed_and_its_link_cleared(conn, world):
    other = dataclasses.replace(BUILDING, code=77000002)
    _load(conn, world, [BUILDING, other, ruian_vfr.AddressLink(ADM, BUILDING.code)])
    _load(conn, world, [other])
    assert _current(conn, BUILDING.code)[0][7] == DAY
    assert _current(conn, other.code)[0][7] is None
    assert _link(conn, ADM) is None


def test_reading_the_same_file_again_changes_nothing(conn, world):
    rows = [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)]
    _load(conn, world, rows)
    before = _current(conn, BUILDING.code)
    _load(conn, world, rows)
    assert _current(conn, BUILDING.code) == before


def test_an_obec_already_read_is_not_pending(conn, world):
    version, _ = world
    assert OBEC in [code for _, code in rb.pending_obce(conn, version)]
    rb.write_obec(conn, version, world[1][OBEC], OBEC, [], "a")
    assert OBEC not in [code for _, code in rb.pending_obce(conn, version)]


def test_the_completion_period_reads_a_placeholder_as_a_period(conn):
    with conn.cursor() as cur:
        cur.execute(
            "select ruian_completion_period('1945-12-31')::text, ruian_completion_period('1919-12-31')::text, "
            "ruian_completion_period('2012-06-30')::text, ruian_completion_period('2012-12-31')::text, "
            "ruian_completion_period(null)")
        assert cur.fetchone() == ("[1920,1946)", "(,1920)", "[2012,2013)", "[2012,2013)", None)


def _versions(conn, code: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute("select first_version_id, last_version_id, valid_to from ruian_building_objects "
                    "where code = %s order by valid_from", (code,))
        return cur.fetchall()


def _next_version(conn, world):
    with conn.cursor() as cur:
        cur.execute(
            "insert into registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline) "
            "values ('test:buildings:2', 'baseline', %s, '{}', 'p', 'q') returning id", (DAY,))
        return (rb.Version(int(cur.fetchone()[0]), DAY), world[1])


def test_a_new_version_does_not_write_an_unchanged_building(conn, world):
    _load(conn, world, [BUILDING])
    later = _next_version(conn, world)
    _load(conn, later, [BUILDING])
    assert _versions(conn, BUILDING.code) == [(world[0].id, world[0].id, None)]


def test_a_new_version_writes_a_building_whose_facts_changed_in_place(conn, world):
    _load(conn, world, [BUILDING])
    later = _next_version(conn, world)
    _load(conn, later, [dataclasses.replace(BUILDING, floors=9)])
    assert _versions(conn, BUILDING.code) == [(world[0].id, later[0].id, None)]
    assert _current(conn, BUILDING.code)[0][1] == 9


def test_a_building_that_reappears_is_opened_again(conn, world):
    _load(conn, world, [BUILDING])
    _load(conn, world, [])
    later = _next_version(conn, world)
    _load(conn, later, [BUILDING])
    assert _versions(conn, BUILDING.code) == [(world[0].id, later[0].id, None)]


def test_a_reversioned_obec_unit_never_leaves_two_current_rows(conn, world):
    """ruian_load gives an obec a new unit row on a rename or a parent's re-version (M2)."""
    version, units = world
    _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)])
    with conn.cursor() as cur:
        cur.execute("update ruian_admin_units set valid_to = %s where id = %s", (DAY, units[OBEC]))
        cur.execute(
            "insert into ruian_admin_units (level, code, name, name_norm, path, valid_from, "
            "first_version_id, last_version_id) values ('obec', %s, 'x', 'x', 't990001b'::ltree, %s, %s, %s) "
            "returning id", (OBEC, DAY + datetime.timedelta(days=1), version.id, version.id))
        renamed = int(cur.fetchone()[0])
    changed = dataclasses.replace(BUILDING, plati_od=datetime.date(2026, 10, 1), floors=9)
    rb.write_obec(conn, version, renamed, OBEC, [changed], "b")
    rows = _current(conn, BUILDING.code)
    assert [(r[1], r[7]) for r in rows] == [(8, changed.plati_od), (9, None)]
    assert _link(conn, ADM) is None, "the old unit row's door is still this obec's door"


def test_a_building_that_moves_obec_is_one_current_row_in_its_new_obec(conn, world):
    version, units = world
    _load(conn, world, [BUILDING])
    with conn.cursor() as cur:
        cur.execute(
            "insert into ruian_admin_units (level, code, name, name_norm, path, valid_from, "
            "first_version_id, last_version_id) values ('obec', 990009, 'y', 'y', 't990009'::ltree, %s, %s, %s) "
            "returning id", (DAY, version.id, version.id))
        other = int(cur.fetchone()[0])
    moved = dataclasses.replace(BUILDING, plati_od=datetime.date(2026, 10, 2))
    rb.write_obec(conn, version, other, 990009, [moved], "c")
    with conn.cursor() as cur:
        cur.execute("select obec_unit_id from ruian_building_objects where code = %s and valid_to is null",
                    (BUILDING.code,))
        assert cur.fetchall() == [(other,)]
    _load(conn, world, [])
    with conn.cursor() as cur:
        cur.execute("select count(*) from ruian_building_objects where code = %s and valid_to is null",
                    (BUILDING.code,))
        assert cur.fetchone()[0] == 1


def test_a_failed_obec_keeps_its_error_and_stays_pending(conn, world):
    version, _ = world
    rb.record_failure(conn, version, OBEC, "Unavailable: HTTP 404")
    assert OBEC in [code for _, code in rb.pending_obce(conn, version)]
    rb.write_obec(conn, version, world[1][OBEC], OBEC, [], "a")
    with conn.cursor() as cur:
        cur.execute("select error from ruian_building_loads where version_id = %s and obec_kod = %s",
                    (version.id, OBEC))
        assert cur.fetchone() == (None,)
    assert OBEC not in [code for _, code in rb.pending_obce(conn, version)]


def test_an_obec_the_served_version_no_longer_holds_is_not_pending(conn, world):
    version, units = world
    later = _next_version(conn, world)[0]
    assert OBEC not in [code for _, code in rb.pending_obce(conn, later)]
    with conn.cursor() as cur:
        cur.execute("update ruian_admin_units set last_version_id = %s where id = %s", (later.id, units[OBEC]))
    assert OBEC in [code for _, code in rb.pending_obce(conn, later)]


def _many(n: int) -> list:
    return [dataclasses.replace(BUILDING, code=78000000 + i) for i in range(n)]


def test_a_file_that_would_empty_an_obec_is_refused_and_writes_nothing(conn, world):
    held = _many(30)
    _load(conn, world, [*held, ruian_vfr.AddressLink(ADM, held[0].code)])
    for rows in ([], held[:10]):
        with pytest.raises(rb.ShrinkRefused):
            _load(conn, world, rows)
    with conn.cursor() as cur:
        cur.execute("select count(*) from ruian_building_objects where code >= 78000000 and valid_to is null")
        assert cur.fetchone()[0] == 30
    assert _link(conn, ADM) == held[0].code


def test_an_obec_may_lose_a_minority_of_its_buildings(conn, world):
    held = _many(30)
    _load(conn, world, held)
    _load(conn, world, held[:20])
    with conn.cursor() as cur:
        cur.execute("select count(*) from ruian_building_objects where code >= 78000000 and valid_to is null")
        assert cur.fetchone()[0] == 20


def test_a_judged_shrink_is_applied_by_hand(conn, world):
    held = _many(30)
    _load(conn, world, [*held, ruian_vfr.AddressLink(ADM, held[0].code)])
    version, units = world
    with pytest.raises(rb.ShrinkRefused, match="holds 0 buildings and would drop 30 of 30"):
        rb.write_obec(conn, version, units[OBEC], OBEC, [], "a")
    rb.write_obec(conn, version, units[OBEC], OBEC, [], "a", accept_shrink=True)
    with conn.cursor() as cur:
        cur.execute("select count(*) from ruian_building_objects where code >= 78000000 and valid_to is null")
        assert cur.fetchone()[0] == 0
    assert _link(conn, ADM) is None


def test_a_file_that_would_clear_most_of_an_obecs_door_links_is_refused(conn, world):
    version, units = world
    doors = list(range(99100000, 99100030))
    with conn.cursor() as cur:
        for kod in doors:
            cur.execute(
                "insert into ruian_address_points (kod_adm, obec_unit_id, obec_kod, psc, plati_od, "
                "first_version_id, last_version_id) values (%s, %s, %s, '11000', %s, %s, %s)",
                (kod, units[OBEC], OBEC, DAY, version.id, version.id))
    links = [ruian_vfr.AddressLink(kod, BUILDING.code) for kod in doors]
    _load(conn, world, [BUILDING, *links])
    with pytest.raises(rb.ShrinkRefused, match="holds 0 address links and would drop 30 of 30"):
        _load(conn, world, [BUILDING])
    assert _link(conn, doors[0]) == BUILDING.code
    _load(conn, world, [BUILDING, *links[:20]])
    assert (_link(conn, doors[0]), _link(conn, doors[29])) == (BUILDING.code, None)


def test_a_vanished_building_under_a_superseded_unit_row_is_still_closed(conn, world):
    """The gone-close names the obec by CODE: a regression to `obec_unit_id = <the new unit row>` fails here."""
    version, units = world
    other = dataclasses.replace(BUILDING, code=77000002)
    _load(conn, world, [BUILDING, other])
    with conn.cursor() as cur:
        cur.execute("update ruian_admin_units set valid_to = %s where id = %s", (DAY, units[OBEC]))
        cur.execute(
            "insert into ruian_admin_units (level, code, name, name_norm, path, valid_from, "
            "first_version_id, last_version_id) values ('obec', %s, 'x', 'x', 't990001c'::ltree, %s, %s, %s) "
            "returning id", (OBEC, DAY + datetime.timedelta(days=1), version.id, version.id))
        renamed = int(cur.fetchone()[0])
    rb.write_obec(conn, version, renamed, OBEC, [other], "b")
    assert _current(conn, BUILDING.code)[0][7] == DAY
    with conn.cursor() as cur:
        cur.execute("select obec_unit_id from ruian_building_objects where code = %s and valid_to is null",
                    (other.code,))
        assert cur.fetchall() == [(renamed,)]


def test_the_card_read_names_each_protection_and_keeps_a_code_the_list_does_not_hold(conn, world):
    from api.property_building import load_building

    listed = dataclasses.replace(BUILDING, protection_codes=(16, 18, 999))
    plain = dataclasses.replace(BUILDING, code=77000002, protection_codes=())
    _load(conn, world, [listed, plain, ruian_vfr.AddressLink(ADM, listed.code),
                        ruian_vfr.AddressLink(OTHER_ADM, plain.code)])
    properties = []
    with conn.cursor() as cur:
        for native, door in (("766-a", ADM), ("766-b", OTHER_ADM)):
            cur.execute(
                "insert into listings (source, source_id_native, raw_json, category_main, category_type) "
                "values ('bazos', %s, '{}'::jsonb, 'byt', 'prodej') returning id", (native,))
            listing = int(cur.fetchone()[0])
            cur.execute(
                "insert into listing_location (listing_id, match_confidence, granularity, uncertainty_radius_m, "
                "country_status, resolver_version, claim_set_hash, registry_version, kraj_kod, okres_kod, obec_kod, "
                "ruian_adm_kod) values (%s, 'exact', 'building', 5, 'cz', 'test', '\\x00'::bytea, 'test', 99, 9991, "
                "%s, %s)", (listing, OBEC, door))
            cur.execute("insert into properties (status, location_listing_ref_id, repr_listing_ref_id) "
                        "values ('active', %s, %s) returning id", (listing, listing))
            properties.append(int(cur.fetchone()[0]))
    assert load_building(conn, properties[0])["protection"] == [
        {"code": 16, "label": "pam. rezervace - budova, pozemek v památkové rezervaci"},
        {"code": 18, "label": "nemovitá kulturní památka"},
        {"code": 999, "label": None}]
    assert load_building(conn, properties[1])["protection"] == []


def _ad(conn, door: int, native: str, price: int, area: float, *, asked: tuple[tuple[str, int], ...] | None = None,
        category: str = "byt", property_id: int | None = None, status: str = "active") -> int:
    """One sale ad at `door`, now asking `price`; `asked` = its snapshots (day, price), by default the price it
    asks now, seen in January. Its own property unless `property_id` names one."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into listings (source, source_id_native, raw_json, category_main, category_type, price_czk, "
            "area_m2, disposition, first_seen_at) values ('bazos', %s, '{}'::jsonb, %s, 'prodej', %s, %s, '2+kk', "
            "'2026-01-10') returning id", (native, category, price, area))
        ad = int(cur.fetchone()[0])
        for n, (day, was) in enumerate(asked if asked is not None else (("2026-01-10", price),)):
            cur.execute("insert into listing_snapshots (listing_id, scraped_at, price_czk, content_hash, raw_json) "
                        "values (%s, %s, %s, %s, '{}'::jsonb)", (ad, day, was, f"h{n}"))
        cur.execute(
            "insert into listing_location (listing_id, match_confidence, granularity, uncertainty_radius_m, "
            "country_status, resolver_version, claim_set_hash, registry_version, kraj_kod, okres_kod, obec_kod, "
            "ruian_adm_kod) values (%s, 'exact', 'address_point', 5, 'cz', 'test', '\\x00'::bytea, 'test', 99, 9991, "
            "%s, %s)", (ad, OBEC, door))
        if property_id is None:
            cur.execute("insert into properties (status, location_listing_ref_id, repr_listing_ref_id) "
                        "values (%s, %s, %s) returning id", (status, ad, ad))
            property_id = int(cur.fetchone()[0])
        cur.execute("update listings set property_id = %s where id = %s", (property_id, ad))
    return property_id


def test_a_bought_deed_over_one_unit_borrows_what_we_hold_of_the_same_sale(conn, world, monkeypatch):
    from psycopg.types.json import Jsonb

    from api import cadastre_prices, cuzk_login

    _load(conn, world, [BUILDING, ruian_vfr.AddressLink(ADM, BUILDING.code)])

    def unit(*labels: str, also: str | None = None) -> list[dict]:
        units = [{"kind": "jednotka", "iskn_id": n, "label": label} for n, label in enumerate(labels)]
        return units + ([{"kind": also, "iskn_id": 99, "label": "x"}] if also else [])

    deeds = {  # proceeding -> (price, properties[, currency]); every deed signed 2026-05-02, entered 2026-06-01
        "held": (4000000, unit("727/1")),
        "advertised": (3500000, unit("727/2")),
        "advertised-once": (3300000, unit("727/3")),
        "with-a-plot-share": (3700000, unit("727/20", also="parcela")),
        "two-properties-ask-it": (5000000, unit("727/4")),
        "one-property-two-areas": (5100000, unit("727/13")),
        "one-property-two-categories": (5200000, unit("727/19")),
        "two-units": (6000000, unit("727/5", "727/6")),
        "unit-and-building": (6100000, unit("727/14", also="stavba")),
        "in-euros": (6200000, unit("727/15"), "EUR"),
        "asked-only-after-signing": (2000000, unit("727/7")),
        "same-price-a": (2500000, unit("727/8")),
        "same-price-b": (2500000, unit("727/9")),
        "nothing-held": (1234567, unit("727/10")),
        "plot-ad": (900000, unit("727/11")),
        "merged-away-property": (950000, unit("727/16")),
        "resold-at-the-same-price": (3000000, unit("727/12")),
        "one-sale-two-deeds-a": (7000000, unit("727/17")),
        "one-sale-two-deeds-b": (7000000, unit("727/17")),
        "two-sales-one-deed": (7100000, unit("727/18")),
    }
    sales = [  # (record, sold_at, price)
        ("1_unit_{b}-727-1", "2026-06-01", 4000000),
        ("2_unit_{b}-727-12", "2016-06-01", 3000000),  # the same unit at the same price ten years earlier
        ("3_unit_{b}-727-17", "2026-05-20", 7000000),
        ("4_unit_{b}-727-18", "2026-05-20", 7100000), ("5_unit_{b}-727-18", "2026-05-21", 7100000),
    ]
    with conn.cursor() as cur:
        cur.execute("insert into accounts (name) values ('deed matches') returning id")
        account = cur.fetchone()[0]
        cur.execute(
            "insert into cadastre_price_purchases (account_id, building_code, iskn_building_id, object_count, status, "
            "report_id) values (%s, %s, 1, 1, 'done', 1) returning id", (account, BUILDING.code))
        purchase = cur.fetchone()[0]
        for proceeding, (price, properties, *currency) in deeds.items():
            cur.execute(
                "insert into cadastre_deeds (purchase_id, account_id, proceeding, kind, price, currency, signed_on, "
                "entered_on, properties) values (%s, %s, %s, 'Smlouva kupní', %s, %s, '2026-05-02', '2026-06-01', %s)",
                (purchase, account, proceeding, price, currency[0] if currency else "CZK", Jsonb(properties)))
        for record, sold_at, price in sales:
            cur.execute(
                "insert into sold_transactions (source, source_record_id, sold_at, price_czk, category_main, "
                "category_type, geom, raw, area_m2) values ('reas', %s, %s, %s, 'byt', 'prodej', "
                "ST_SetSRID(ST_MakePoint(14.4, 50.1), 4326), '{}', 50)", (record.format(b=BUILDING.code), sold_at, price))
    _ad(conn, ADM, "m-held", 4000000, 50)  # the held sale wins over an ad at the same price
    asked = _ad(conn, ADM, "m-asked", 3500000, 35)
    once = _ad(conn, ADM, "m-once", 3100000, 33, asked=(("2026-01-10", 3300000), ("2026-03-01", 3100000)))
    shared = _ad(conn, ADM, "m-share", 3700000, 37)
    _ad(conn, ADM, "m-two-a", 5000000, 40)
    _ad(conn, ADM, "m-two-b", 5000000, 40)  # the same area at another property: still two candidates
    twice = _ad(conn, ADM, "m-areas-a", 5100000, 51)
    _ad(conn, ADM, "m-areas-b", 5100000, 52, property_id=twice)
    mixed = _ad(conn, ADM, "m-cat-a", 5200000, 53)
    _ad(conn, ADM, "m-cat-b", 5200000, 53, property_id=mixed, category="komercni")
    _ad(conn, ADM, "m-units", 6000000, 60)
    _ad(conn, ADM, "m-building", 6100000, 61)
    _ad(conn, ADM, "m-euros", 6200000, 62)
    _ad(conn, ADM, "m-after", 2000000, 20, asked=(("2026-01-10", 2100000), ("2026-05-03", 2000000)))
    _ad(conn, ADM, "m-same", 2500000, 25)
    _ad(conn, ADM, "m-plot", 900000, 300, category="pozemek")
    _ad(conn, ADM, "m-merged", 950000, 30, status="merged_away")
    _ad(conn, OTHER_ADM, "m-other-door", 1234567, 12)  # a door of no building

    def shown() -> dict:
        return {d["proceeding"]: d["match"] for d in cadastre_prices.state(conn, account, BUILDING.code)["deeds"]}

    got = shown()
    matched = {k: (v["source"], v["property_id"], v["area_m2"]) for k, v in got.items() if v}
    assert matched == {"held": ("reas", None, None), "advertised": ("ad", asked, 35.0),
                       "advertised-once": ("ad", once, 33.0), "with-a-plot-share": ("ad", shared, 37.0)}
    assert set(got) == set(deeds)
    assert got["held"]["source_record_id"] == f"1_unit_{BUILDING.code}-727-1"
    assert (got["advertised"]["price_per_m2"], got["advertised"]["price_per_m2_basis"],
            got["advertised"]["disposition"]) == (100000.0, "sale_capital_czk_m2", "2+kk")

    monkeypatch.setattr(cuzk_login, "run_live", lambda *_: True)  # the card polls while a run is live: no match then
    assert not any(shown().values())

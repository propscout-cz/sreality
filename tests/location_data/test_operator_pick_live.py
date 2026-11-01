"""Location W3b, EXECUTED against the replayed schema (CI's migrations lane, `TEST_DATABASE_URL`;
locally `scripts/local_pg.sh test`): the address picker's pick on every ad of a property,
resolved by the drain over a two-town mini mirror seeded in the transaction, replaced by a
re-pick, undone by a clear, and left behind by an ad the real detach splits off (R1). Every test
rolls back.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest

from location_data import claims_intake as ci
from location_data import claims_store, contracts
from location_data import operator_corrections as oc
from location_data.claims_common import Claim
from location_data.name_index import normalize_name, normalize_street_name
from location_data.resolver import drain
from toolkit.property_identity import detach_listings

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL and os.environ.get("DB_RAILS_REQUIRED") != "1",
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job")

T0 = datetime(2026, 10, 1, tzinfo=UTC)
DOOR, LIPOVA, SADOVA = 21000001, 9002, 9001


@pytest.fixture()
def conn() -> Any:
    connection = psycopg.connect(_DB_URL, autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        contracts.project(connection, next(
            c for c in contracts.load_all() if c.source == "bazos"), git_ref="ci")
        yield connection


def _mirror(conn: Any) -> None:
    """Two towns in two okresy of one kraj: Alfov with Sadová, Betov with Lipová and door 12."""
    conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    version = conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version,"
        " proj_pipeline, is_current) VALUES ('test:w3b-picker', 'baseline', '2026-10-01', '{}',"
        " 'p', 'q', true) RETURNING id").fetchone()[0]
    units: dict[str, int] = {}
    for key, level, code, name, parent, path in (
        ("cz", "stat", 1, "Česká republika", None, "s1"),
        ("kraj", "kraj", 27, "Středočeský kraj", "cz", "k27"),
        ("oa", "okres", 3201, "Alfovsko", "kraj", "k27.o3201"),
        ("a", "obec", 500001, "Alfov", "oa", "k27.o3201.b500001"),
        ("ob", "okres", 3202, "Betovsko", "kraj", "k27.o3202"),
        ("b", "obec", 500002, "Betov", "ob", "k27.o3202.b500002"),
    ):
        units[key] = conn.execute(
            "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path,"
            " display_path, valid_from, first_version_id, last_version_id) VALUES"
            " (%s::ruian_level, %s, %s, %s, %s, %s::ltree, %s, '2020-01-01', %s, %s) RETURNING id",
            (level, code, name, normalize_name(name), units.get(parent or ""), path, name,
             version, version)).fetchone()[0]
        conn.execute(
            "INSERT INTO ruian_name_index (entity_kind, entity_id, registry_version_id, name,"
            " name_norm, name_kind) VALUES (%s::ruian_level, %s, %s, %s, %s, 'official')",
            (level, units[key], version, name, normalize_name(name)))
    streets = {}
    for code, name, town in ((SADOVA, "Sadová", "a"), (LIPOVA, "Lipová", "b")):
        streets[code] = conn.execute(
            "INSERT INTO ruian_streets (code, name, name_norm, obec_unit_id, first_version_id,"
            " last_version_id, valid_from) VALUES (%s, %s, %s, %s, %s, %s, '2020-01-01')"
            " RETURNING id", (code, name, normalize_street_name(name), units[town], version,
                              version)).fetchone()[0]
    conn.execute(
        "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, street_id, ulice_kod,"
        " typ_so, cislo_domovni, psc, geom, plati_od, first_version_id, last_version_id)"
        " VALUES (%s, %s, 500002, %s, %s, 'č.p.', 12, '25001',"
        " ST_SetSRID(ST_MakePoint(14.5, 50.1), 4326), %s, %s, %s)",
        (DOOR, units["b"], streets[LIPOVA], LIPOVA, date(2020, 1, 1), version, version))


def _property(conn: Any) -> tuple[int, list[int]]:
    """A property of two bazos ads, each stating Sadová in Alfov; the first is the location ad."""
    entries = {e.entry_id: e for e in ci.load_entries(conn)["bazos"]}
    ads = [int(conn.execute(
        "INSERT INTO listings (source, source_id_native, raw_json, category_main, category_type,"
        " price_czk, area_m2, is_active, last_seen_at) VALUES ('bazos', %s, '{}'::jsonb, 'byt',"
        " 'prodej', 5000000, 60, true, now()) RETURNING id", (f"w3b-{uuid.uuid4()}",)
    ).fetchone()[0]) for _ in range(2)]
    prop = conn.execute("INSERT INTO properties (repr_listing_ref_id, location_listing_ref_id)"
                        " VALUES (%s, %s) RETURNING id", (ads[0], ads[0])).fetchone()[0]
    conn.execute("UPDATE listings SET property_id = %s WHERE id = ANY(%s)", (prop, ads))
    with conn.cursor() as cur:
        claims_store.write_claims(cur, [
            Claim(listing_id=ad, source="bazos", source_id_native="n", claim_type=e.claim_type,
                  surface=e.surface, page_kind=e.page_kind, extraction_method=e.extraction_method,
                  extractor_id="ci", extractor_version="ci", contract_entry_id=e.id,
                  first_observed_at=T0, blur_evidence=e.default_blur_evidence,
                  licence_class=e.default_licence_class, value_text=value)
            for ad in ads for e, value in ((entries["bzs.txt.town"], "Alfov"),
                                           (entries["bzs.txt.street"], "Sadová"))])
    return prop, ads


def _operator(conn: Any, ad: int) -> dict[str, str]:
    return dict(conn.execute(
        "SELECT claim_type::text, value_text FROM location_claims WHERE listing_id = %s"
        " AND licence_class = 'operator'", (ad,)).fetchall())


def _resolved(conn: Any, ad: int) -> tuple[Any, ...]:
    drain.run(conn, only_listing_id=ad, batch_size=1)
    return conn.execute(
        "SELECT granularity::text, ruian_adm_kod, ulice_kod, obec_kod, encode(claim_set_hash,"
        " 'hex') FROM listing_location WHERE listing_id = %s", (ad,)).fetchone()


def test_a_pick_lands_on_every_ad_a_re_pick_replaces_it_and_a_clear_restores_the_words(
    conn: Any,
) -> None:
    _mirror(conn)
    prop, ads = _property(conn)
    before = {ad: _resolved(conn, ad) for ad in ads}
    assert {row[2:4] for row in before.values()} == {(SADOVA, 500001)}   # the ads' own words
    conn.execute("DELETE FROM dirty_locations WHERE listing_id = ANY(%s)", (ads,))

    result = oc.submit_pick(conn, property_id=prop, pick=("adresni_misto", DOOR), by="ci")
    assert (result["written"], result["enqueued"]) == (2, 2)
    assert [a["location_ad"] for a in result["ads"]] == [True, False]
    # The location ad resolved before the answer; the other waits in the queue for the drain.
    assert (result["resolved"], result["held"], result["location"]["granularity"]) == (
        True, True, "address_point")
    assert conn.execute("SELECT reason FROM dirty_locations WHERE listing_id = %s",
                        (ads[1],)).fetchone() == ("operator_edit",)
    for ad in ads:
        assert _operator(conn, ad) == {"address_point_id": str(DOOR)}
        assert _resolved(conn, ad)[:4] == ("address_point", DOOR, LIPOVA, 500002)
    # The picker's preview (`/maps/resolve`'s door label) is the label the row now reads.
    maps = pytest.importorskip("api.maps")
    assert result["location"]["label"] == "Lipová 12, Betov" == conn.execute(
        maps._DOOR_LABEL_SQL, (DOOR,)).fetchone()[0]
    assert conn.execute("SELECT note FROM location_claim_batches WHERE lane ="
                        " 'operator_correction' ORDER BY id DESC LIMIT 1").fetchone() == (
        f"property {prop}: adresni_misto {DOOR} by ci",)

    oc.submit_pick(conn, property_id=prop, pick=("ulice", LIPOVA), by="ci")
    for ad in ads:
        assert _operator(conn, ad) == {"street_name": "Lipová", "obec_name": "Betov",
                                       "okres_name": "Betovsko"}
        assert _resolved(conn, ad)[:4] == ("street", None, LIPOVA, 500002)

    oc.submit_pick(conn, property_id=prop, pick=None, by="ci")
    for ad in ads:
        assert _operator(conn, ad) == {}
        assert _resolved(conn, ad) == before[ad]


def test_a_detached_ad_goes_back_to_its_own_words(conn: Any) -> None:
    """Ruling R1 (b), executed: the REAL detach (rule 15's chokepoint) splits the second ad off to
    a record of its own and, in its transaction, clears the operator's pick on it; the ad that
    stays keeps it. The offline suites see the call only through the ledger fake."""
    _mirror(conn)
    prop, ads = _property(conn)
    oc.submit_pick(conn, property_id=prop, pick=("obec", 500002), by="ci")
    (moved,) = detach_listings(conn, [ads[1]], decided_by="ci")["data"]["adverts"]
    assert moved["detached"] and moved["left_property_id"] == prop
    assert conn.execute("SELECT property_id FROM listings WHERE id = %s",
                        (ads[1],)).fetchone()[0] not in (None, prop)
    assert _operator(conn, ads[1]) == {}
    assert _operator(conn, ads[0]) == {"obec_name": "Betov", "okres_name": "Betovsko"}
    assert conn.execute("SELECT note FROM location_claim_batches WHERE lane ="
                        " 'operator_correction' ORDER BY id DESC LIMIT 1").fetchone() == (
        f"listings {ads[1]}: detached",)

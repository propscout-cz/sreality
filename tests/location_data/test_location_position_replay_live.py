"""W6-c, EXECUTED against the replayed schema (CI's migrations lane, `TEST_DATABASE_URL`; locally
`scripts/local_pg.sh test`): the position audit's kind (location_steps.position_kind_sql, inside
location_position_mv, migration 733) is the resolver's own `Position.origin` for every fixture.

The mini mirror (tests/location_data/mini_mirror.py) is loaded into the RÚIAN tables; each fixture is resolved in
memory against the same mirror with its final `Position` recorded (`bind.place`, then `fill.position`, whichever the
resolver called last); its answer row goes in through the drain's own upsert and its pin through the one claim write;
the matview refreshes through the chokepoint. Then the scorecard's `position` rows and the admin list read the same
kinds. Every test rolls back.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest

from location_data import claims_intake as ci
from location_data import claims_store, contracts
from location_data import location_steps as ls
from location_data.resolver import bind, core, fill, projection, resolve_db
from location_data.resolver.types import Claim, Position
from location_data.resolver.version import RESOLVER_VERSION
from tests.location_data import mini_mirror as mm

_DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not _DB_URL and os.environ.get("DB_RAILS_REQUIRED") != "1",
    reason="TEST_DATABASE_URL not set — schema-replay test runs only in the CI DB job")

T0 = datetime(2026, 10, 1, tzinfo=UTC)
REGISTRY = "test:w6c-position"
c = mm.claim

# Every origin the resolver publishes, each from the default mirror's own regression material.
FIXTURES: dict[str, list[Claim]] = {
    # Nad Bořislavkou 487/40: the address point, alone and with an exact pin on the door (the door wins the tie).
    "door": [c(1, "obec_name", value_text="Praha"), c(2, "street_name", value_text="Nad Bořislavkou 487/40"),
             c(3, "psc", value_text="160 00")],
    "door_on_its_pin": [c(1, "obec_name", value_text="Praha"),
                        c(2, "street_name", value_text="Nad Bořislavkou 487/40"), c(3, "psc", value_text="160 00"),
                        c(4, "coordinate", lat=50.10100, lon=14.34800, declared_precision_label="gps")],
    # A street with doors and no house number: the centroid of its doors.
    "street_point": [c(1, "obec_name", value_text="Bílovec"), c(2, "street_name", value_text="Slunečná")],
    # An exact pin inside the town the words bound.
    "own_pin": [c(1, "obec_name", value_text="Praha"),
                c(4, "coordinate", lat=50.08, lon=14.42, declared_precision_label="gps")],
    # A pin with no words at all, abroad: it names nothing and stays.
    "own_pin_abroad": [c(4, "coordinate", lat=52.52, lon=13.40, declared_precision_label="gps")],
    # A část with doors: the centroid of its doors.
    "part_point": [c(1, "obec_name", value_text="Praha"), c(5, "cast_obce_name", value_text="Vokovice")],
    # A town, and a doorless část that walks up to its town.
    "town_point": [c(1, "obec_name", value_text="Bílovec")],
    "town_point_doorless_part": [c(1, "obec_name", value_text="Kladno"),
                                 c(5, "cast_obce_name", value_text="Dubí")],
    # An okres or a kraj alone: the unit's own point.
    "unit_point_okres": [c(1, "okres_name", value_text="Nový Jičín")],
    "unit_point_kraj": [c(1, "kraj_name", value_text="Liberecký kraj")],
    # Abroad by its words, no pin: no point at all.
    "foreign": [c(1, "country", value_text="Rakousko")],
    # Nothing to go on.
    "none": [],
}
EXPECTED_KIND = {
    "door": "door", "door_on_its_pin": "door", "street_point": "street_point", "own_pin": "own_pin",
    "own_pin_abroad": "own_pin", "part_point": "part_point", "town_point": "town_point",
    "town_point_doorless_part": "town_point", "unit_point_okres": "unit_point", "unit_point_kraj": "unit_point",
    "foreign": "foreign", "none": "none",
}


@pytest.fixture()
def conn() -> Iterator[Any]:
    connection = psycopg.connect(_DB_URL, autocommit=True, options="-c statement_timeout=20000")
    with connection, connection.transaction(force_rollback=True):
        contracts.project(connection, next(
            x for x in contracts.load_all() if x.source == "bazos"), git_ref="ci")
        yield connection


def _load_mirror(conn: Any, mirror: mm.MiniMirror) -> None:
    """The mirror's units, unit points (an authoritative row per unit that has one), streets and doors, at a new
    current registry version."""
    conn.execute("UPDATE registry_versions SET is_current = false WHERE is_current")
    version = conn.execute(
        "INSERT INTO registry_versions (label, kind, source_date, artifact_urls, proj_version, proj_pipeline,"
        " is_current) VALUES (%s, 'baseline', '2026-10-01', '{}', 'p', 'q', true) RETURNING id",
        (REGISTRY,)).fetchone()[0]
    ids: dict[int, int] = {}
    for unit in sorted(mirror.units, key=lambda u: u.path.count(".")):
        ids[unit.unit_id] = conn.execute(
            "INSERT INTO ruian_admin_units (level, code, name, name_norm, parent_id, path, valid_from,"
            " first_version_id, last_version_id) VALUES (%s::ruian_level, %s, %s, %s, %s, %s::ltree,"
            " '2020-01-01', %s, %s) RETURNING id",
            (unit.level, unit.code, unit.name, unit.name_norm, ids.get(unit.parent_id or 0), unit.path,
             version, version)).fetchone()[0]
        if unit.lat is not None and unit.lon is not None:
            conn.execute(
                "INSERT INTO ruian_admin_unit_geometries (unit_id, registry_version_id, purpose,"
                " generalization_tolerance_m, geom, representative_point, inscribed_radius_m, centroid_point,"
                " containment_radius_m) SELECT %s, %s, 'authoritative', 0,"
                " ST_Multi(ST_Buffer(pt, 0.01)), pt, 0, pt, 0"
                " FROM (SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326) AS pt) x",
                (ids[unit.unit_id], version, unit.lon, unit.lat))
    obec_unit = {u.code: ids[u.unit_id] for u in mirror.units if u.level == "obec"}
    streets: dict[int, int] = {}
    for street in mirror.streets:
        streets[street.code] = conn.execute(
            "INSERT INTO ruian_streets (code, name, name_norm, obec_unit_id, first_version_id, last_version_id,"
            " valid_from) VALUES (%s, %s, %s, %s, %s, %s, '2020-01-01') RETURNING id",
            (street.code, street.name, street.name_norm, obec_unit[street.obec_kod], version,
             version)).fetchone()[0]
    for door in mirror.points:
        conn.execute(
            "INSERT INTO ruian_address_points (kod_adm, obec_unit_id, obec_kod, cast_obce_unit_id, cast_obce_kod,"
            " street_id, ulice_kod, typ_so, cislo_domovni, cislo_orientacni, psc, geom, plati_od,"
            " first_version_id, last_version_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " ST_SetSRID(ST_MakePoint(%s, %s), 4326), '2020-01-01', %s, %s)",
            (door.kod_adm, ids[door.obec_unit_id], door.obec_kod,
             ids.get(door.cast_obce_unit_id or 0), door.cast_obce_kod, streets.get(door.ulice_kod or 0),
             door.ulice_kod, door.typ_so, door.cislo_domovni, door.cislo_orientacni, door.psc, door.lon,
             door.lat, version, version))


def _resolved(claims: list[Claim], monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Position]:
    """The resolution, and the Position it published: the last one `place` or `fill.position` returned."""
    seen: list[Position] = []
    place, position = bind.place, fill.position
    monkeypatch.setattr(bind, "place", lambda *a, **k: seen.append(place(*a, **k)) or seen[-1])
    monkeypatch.setattr(fill, "position", lambda *a, **k: seen.append(position(*a, **k)) or seen[-1])
    resolution = core.resolve(claims, mm.context(), resolver_version=RESOLVER_VERSION, registry_version=REGISTRY,
                              listing_id=1, source="bazos")
    monkeypatch.undo()
    assert (seen[-1].lat, seen[-1].lon) == (resolution.lat, resolution.lon), "the recorded Position is the row's"
    return resolution, seen[-1]


def _listing(conn: Any) -> int:
    return int(conn.execute(
        "INSERT INTO listings (source, source_id_native, raw_json, category_main, category_type, price_czk,"
        " area_m2, is_active, first_seen_at, last_seen_at) VALUES ('bazos', %s, '{}'::jsonb, 'byt', 'prodej',"
        " 5000000, 60, true, now() - interval '3 days', now()) RETURNING id",
        (f"w6c-{uuid.uuid4()}",)).fetchone()[0])


def _write(conn: Any, claims: list[Claim], resolution: Any) -> int:
    """A listing with this answer row (the drain's own upsert) and its pin (the one claim write)."""
    listing = _listing(conn)
    row = projection.build_listing_row(resolution)
    resolve_db.upsert_listing_locations_bulk(conn, [{**row, "listing_id": listing}])
    pins = [x for x in claims if x.claim_type == "coordinate"]
    if pins:
        entry = {e.entry_id: e for e in ci.load_entries(conn)["bazos"]}["bzs.det.link_pin"]
        with conn.cursor() as cur:
            claims_store.write_claims(cur, [
                ci._base(entry, ci.ListingRow(listing, "bazos", "n", {}, T0),
                         value_geom_wkt=f"POINT({pin.lon} {pin.lat})") for pin in pins])
    return listing


@pytest.fixture()
def written(conn: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, tuple[int, Position]]:
    _load_mirror(conn, mm.default_mirror())
    out = {}
    for name, claims in FIXTURES.items():
        resolution, position = _resolved(claims, monkeypatch)
        out[name] = (_write(conn, claims, resolution), position)
    conn.execute("SELECT public.refresh_matview('location_position_mv')")
    return out


def _kinds(conn: Any, listings: list[int]) -> dict[int, str]:
    return dict(conn.execute("SELECT listing_id, position_kind FROM location_position_mv"
                             " WHERE listing_id = ANY(%s)", (listings,)).fetchall())


def test_every_fixture_reads_the_kind_of_the_origin_the_resolver_published(
    conn: Any, written: dict[str, tuple[int, Position]],
) -> None:
    kinds = _kinds(conn, [listing for listing, _ in written.values()])
    got = {name: kinds[listing] for name, (listing, _) in written.items()}
    assert got == EXPECTED_KIND
    for name, (listing, position) in written.items():
        assert ls.POSITION_ORIGIN[kinds[listing]] == position.origin, name
    assert set(EXPECTED_KIND.values()) == set(ls.POSITION_KINDS) - {"unexplained"}, "every kind has a fixture"


def test_a_point_the_resolver_could_not_have_used_is_unexplained(
    conn: Any, written: dict[str, tuple[int, Position]],
) -> None:
    listing = written["town_point"][0]
    conn.execute("UPDATE listing_location SET geom = ST_SetSRID(ST_MakePoint(18.02, 49.76), 4326)"
                 " WHERE listing_id = %s", (listing,))
    conn.execute("SELECT public.refresh_matview('location_position_mv')")
    assert _kinds(conn, [listing]) == {listing: "unexplained"}


def test_the_scorecard_counts_the_kinds_by_live_grain_and_a_re_resolve_reads_unclassified(
    conn: Any, written: dict[str, tuple[int, Position]],
) -> None:
    """The view's `position` rows over the listing cohort: every fixture once, kind by the matview, grain by the live
    row (the scorecard's classes first); a row re-resolved since the refresh is `unclassified` until the next one."""
    def classes() -> dict[str, int]:
        return dict(conn.execute(
            "SELECT class, n FROM location_quality_by_source"
            " WHERE cohort = 'listing' AND metric = 'position' AND source = 'bazos'").fetchall())

    assert classes() == {
        "door/address": 2, "street_point/street": 1, "own_pin/obec": 1, "own_pin/unknown": 1,
        "part_point/quarter": 1, "town_point/obec": 1, "town_point/quarter": 1, "unit_point/okres": 1,
        "unit_point/kraj": 1, "foreign/country": 1, "none/unknown": 1}
    conn.execute("UPDATE listing_location SET resolved_at = resolved_at + interval '1 second'"
                 " WHERE listing_id = %s", (written["street_point"][0],))
    assert classes()["unclassified/street"] == 1 and "street_point/street" not in classes()


def test_the_admin_list_serves_the_live_property_and_the_none_extras(
    conn: Any, written: dict[str, tuple[int, Position]],
) -> None:
    none, door = written["none"][0], written["door"][0]
    prop = conn.execute("INSERT INTO properties (repr_listing_ref_id, location_listing_ref_id) VALUES (%s, %s)"
                        " RETURNING id", (door, door)).fetchone()[0]
    conn.execute("UPDATE listings SET property_id = %s WHERE id = ANY(%s)", (prop, [none, door]))
    conn.execute("SELECT public.refresh_matview('location_position_mv')")
    rows = {r[0]: r[1:] for r in conn.execute(
        "SELECT listing_id, property_id, position_kind, grain, town_evidence, sibling_placed, queued, display_label"
        " FROM location_position_audit WHERE listing_id = ANY(%s)", ([none, door],)).fetchall()}
    assert rows[none][:6] == (prop, "none", "unknown", False, True, False)
    assert rows[door][:6] == (prop, "door", "address", None, None, None)
    assert rows[door][6], "the one place string"

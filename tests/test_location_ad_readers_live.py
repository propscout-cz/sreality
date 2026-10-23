"""Migration 636, executed (ONE PIN W4): a property is placed at its location ad in browse_projection, properties_public
and city proximity, and the rollup clears the proximity stamp exactly when the location ad changes; and migration 669:
browse_projection serves it only once published, properties_public always. Runs in CI's migrations job
(`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests._live_property import REQUIRED_DB, _advert, _property, _recompute, cur  # noqa: F401 -- `cur` is the fixture
from toolkit.publication import CAP_SQL

pytestmark = REQUIRED_DB

PRAHA = 554782
STAMP = "2026-02-01 00:00+00"
# Property 15100 on 2026-10-19: its canonical ad a ±300 m street ring, an inactive ad on the address point.
RING = (14.4625, 50.0612)
POINT = (14.46709, 50.05995)
LABEL = "Pod Sychrovem II 850/43, Praha"


def _placed(cur: Any, listing_id: int, at: tuple[float, float], granularity: str, radius: int, *,
            street: str | None = "Pod Sychrovem II", cp: str | None = None, co: str | None = None,
            town: str = "Praha", town_code: int = PRAHA) -> None:
    """One resolved `listing_location` row: a point, a rung, a radius and the label's parts."""
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version, country_code, obec_name, obec_kod, "
        "  street_name, house_number_cp, house_number_co) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'exact', %s::location_granularity, %s, "
        "  'cz'::country_status, 'test', '\\x00'::bytea, 'test', 'CZ', %s, %s, %s, %s, %s)",
        (listing_id, *at, granularity, radius, town, town_code, street, cp, co))


def _pointers(cur: Any, pid: int) -> tuple[Any, ...]:
    cur.execute("SELECT repr_listing_ref_id, location_listing_ref_id, city_proximity_computed_at "
                "FROM properties WHERE id = %s", (pid,))
    return cur.fetchone()


def _stamped(cur: Any, pid: int) -> Any:
    """A proximity stamp the hourly job wrote; source_count 1, so only the pointer comparison can clear it."""
    cur.execute("UPDATE properties SET city_proximity_computed_at = %s, source_count = 1 WHERE id = %s "
                "RETURNING city_proximity_computed_at", (STAMP, pid))
    return cur.fetchone()[0]


def _town(cur: Any, code: int, name: str, centre: tuple[float, float], population: int) -> None:
    """An obec: a 0.1° square around `centre`."""
    lng, lat = centre
    cur.execute(
        "INSERT INTO admin_boundaries (id, level, name, geom, population) VALUES (%s, 'obec', %s, "
        "ST_Multi(ST_MakeEnvelope(%s, %s, %s, %s, 4326))::geography, %s)",
        (code, name, lng - 0.05, lat - 0.05, lng + 0.05, lat + 0.05, population))


def _ring_and_point(cur: Any) -> tuple[int, int, int]:
    """15100's shape: the active canonical ad on the street ring, a cheaper inactive ad on the address point."""
    pid = _property(cur)
    ring = _advert(cur, pid, source="idnes", price=8_000_000)
    point = _advert(cur, pid, source="idnes", price=7_000_000, active=False)
    _placed(cur, ring, RING, "street", 300)
    _placed(cur, point, POINT, "address_point", 10, cp="850", co="43")
    _recompute(cur, pid)
    assert _pointers(cur, pid)[:2] == (ring, point)
    return pid, ring, point


def test_browse_projection_places_the_property_at_its_address_point_and_prices_it_at_its_canonical_ad(
        cur: Any) -> None:
    pid, ring, _ = _ring_and_point(cur)
    cur.execute("SELECT lat, lng, display_label, granularity_rank, uncertainty_radius_m, obec_id, listing_id, "
                "price_czk FROM browse_projection WHERE property_id = %s", (pid,))
    lat, lng, label, rank, radius, town, listing_id, price = cur.fetchone()
    assert (lng, lat) == pytest.approx(POINT)
    assert (label, rank, float(radius), town) == (LABEL, 100, 10.0, PRAHA)
    assert (listing_id, price) == (ring, 8_000_000), "the link and the price voice stay the canonical ad's (R3)"


def test_properties_public_places_the_property_at_its_address_point_and_names_its_location_ad(cur: Any) -> None:
    pid, ring, point = _ring_and_point(cur)
    cur.execute("SELECT lat, lng, display_label, obec_id, location_listing_ref_id, listing_id, price_czk "
                "FROM properties_public WHERE property_id = %s", (pid,))
    lat, lng, label, town, location_ad, listing_id, price = cur.fetchone()
    assert (lng, lat) == pytest.approx(POINT)
    assert (label, town, location_ad) == (LABEL, PRAHA, point)
    assert (listing_id, price) == (ring, 8_000_000)


def test_a_property_is_served_by_its_location_ad_when_its_canonical_ad_has_no_place(cur: Any) -> None:
    """Rule 25's served test follows the place: before 636 this property was invisible to Browse."""
    pid = _property(cur)
    unplaced = _advert(cur, pid, source="idnes")
    placed = _advert(cur, pid, source="idnes", active=False)
    _placed(cur, placed, POINT, "address_point", 10, cp="850", co="43")
    _recompute(cur, pid)
    assert _pointers(cur, pid)[:2] == (unplaced, placed)
    cur.execute("SELECT listing_id, display_label FROM browse_projection WHERE property_id = %s", (pid,))
    assert cur.fetchone() == (unplaced, LABEL)


def test_browse_serves_a_property_once_settled_or_past_the_cap_and_its_page_always(cur: Any) -> None:
    """Rule 16 (operator ruling 2026-10-23): the stamp publishes at once, the cap without one; the property page, by
    id, never waits. now() is the transaction's, so `first_seen_at = now()` is a property first seen this instant."""
    pid, _, _ = _ring_and_point(cur)

    def served(settled_at: str, first_seen_at: str) -> tuple[bool, bool]:
        cur.execute(f"UPDATE properties SET settled_at = {settled_at}, first_seen_at = {first_seen_at} WHERE id = %s",
                    (pid,))
        cur.execute("SELECT EXISTS (SELECT 1 FROM browse_projection WHERE property_id = %(p)s), "
                    "EXISTS (SELECT 1 FROM properties_public WHERE property_id = %(p)s)", {"p": pid})
        return cur.fetchone()

    assert served("NULL", "now()") == (False, True), "a fresh unsettled property is in Browse, or its page is gone"
    assert served("now()", "now()") == (True, True), "a settled property waits for the cap"
    assert served("NULL", f"now() - {CAP_SQL} - interval '1 hour'") == (True, True), "the cap did not publish it"


def test_city_proximity_is_computed_at_the_location_ads_point(cur: Any) -> None:
    """The canonical ad stands in Brno, the location ad in Praha: the home town is Praha's."""
    _town(cur, 9_990_001, "Brno (test)", (16.60, 49.19), 380_000)
    _town(cur, 9_990_002, "Praha (test)", (14.46, 50.05), 1_300_000)
    pid = _property(cur)
    ring = _advert(cur, pid, source="idnes")
    point = _advert(cur, pid, source="idnes", active=False)
    _placed(cur, ring, (16.605, 49.195), "street", 300, town="Brno", town_code=582786)
    _placed(cur, point, POINT, "address_point", 10, cp="850", co="43")
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (ring, point, None)
    cur.execute("SELECT recompute_city_proximity(false)")
    cur.execute("SELECT home_obec_pop, near_pop_5km, near_pop_15km, city_proximity_computed_at IS NOT NULL "
                "FROM properties WHERE id = %s", (pid,))
    assert cur.fetchone() == (1_300_000, 1_300_000, 1_300_000, True)


def test_the_rollup_clears_the_proximity_stamp_when_a_property_first_gains_a_location_ad(cur: Any) -> None:
    """From a NULL pointer: `<>` reads NULL and keeps the stale stamp; IS DISTINCT FROM clears it."""
    pid = _property(cur)
    assert _pointers(cur, pid) == (None, None, None)
    _stamped(cur, pid)
    lid = _advert(cur, pid, source="idnes")
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (lid, lid, None)
    stamp = _stamped(cur, pid)
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (lid, lid, stamp), "an unchanged location ad keeps its proximity"


def test_the_stamp_follows_the_location_ad_never_the_canonical_ad(cur: Any) -> None:
    """A finer place clears it; a new canonical ad at the same place keeps it (repr_since restarts, the place not)."""
    pid = _property(cur)
    ring = _advert(cur, pid, source="idnes")
    _placed(cur, ring, RING, "street", 300)
    _recompute(cur, pid)
    _stamped(cur, pid)
    point = _advert(cur, pid, source="idnes", active=False)
    _placed(cur, point, POINT, "address_point", 10, cp="850", co="43")
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (ring, point, None)
    stamp = _stamped(cur, pid)
    newer = _advert(cur, pid, source="idnes")
    cur.execute("UPDATE listings SET is_active = false WHERE id = %s", (ring,))
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (newer, point, stamp)

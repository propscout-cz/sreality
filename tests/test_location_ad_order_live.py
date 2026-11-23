"""Migration 635, executed (ONE PIN W3, ruling R1): a property's location ad is rank 1 of the location order (a map
point, then determined foreign, then the finest rung, then the canonical order), the canonical order is 588's ad for
ad, and the birth, the rollup and the no-ads reset set both pointers. Runs in CI's migrations job
(`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests._live_property import REQUIRED_DB, _advert, _property, _recompute

pytestmark = REQUIRED_DB

_588 = (Path(__file__).resolve().parents[1] / "migrations"
        / "588_canonical_order_and_portal_dates.sql").read_text(encoding="utf-8")
_AT = _588.index("$$", _588.index("create or replace function public.property_canonical_listings(")) + 2
# 588's body run inline: the canonical order as it stood before 635 added a window beside it.
CANONICAL_588 = _588[_AT: _588.index("$$", _AT)].replace("p_property_id", "%(pid)s")


def _placed(cur: Any, listing_id: int, granularity: str, *, point: bool = True, country: str = "cz") -> None:
    """One `listing_location` row: a point or none, a rung, a country determination."""
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version) "
        "VALUES (%s, CASE WHEN %s THEN ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326) END, 'exact', "
        "  %s::location_granularity, 5, %s::country_status, 'test', '\\x00'::bytea, 'test')",
        (listing_id, point, granularity, country))


def _orders(cur: Any, pid: int) -> tuple[list[int], list[int]]:
    """The canonical and the location order; the canonical one must be 588's, rank for rank."""
    cur.execute("SELECT listing_id, canonical_rank, location_rank FROM property_canonical_listings(%s)", (pid,))
    rows = cur.fetchall()
    cur.execute(CANONICAL_588, {"pid": pid})
    assert sorted((r[0], r[1]) for r in rows) == sorted(cur.fetchall()), "635 moved the canonical order"
    assert sorted(r[2] for r in rows) == list(range(1, len(rows) + 1))
    return [r[0] for r in sorted(rows, key=lambda r: r[1])], [r[0] for r in sorted(rows, key=lambda r: r[2])]


def _pointers(cur: Any, pid: int) -> tuple[int | None, int | None]:
    cur.execute("SELECT repr_listing_ref_id, location_listing_ref_id FROM properties WHERE id = %s", (pid,))
    return cur.fetchone()


def test_an_inactive_address_point_beats_an_active_street_centroid(cur: Any) -> None:
    """R1: the location is a fact about the building, so the exact point outlives its ad; the price voice stays."""
    pid = _property(cur)
    street = _advert(cur, pid, source="idnes")
    exact = _advert(cur, pid, source="idnes", active=False)
    _placed(cur, street, "street")
    _placed(cur, exact, "address_point")
    canonical, location = _orders(cur, pid)
    assert (canonical[0], location[0]) == (street, exact)
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (street, exact)


def test_among_equal_rungs_the_canonical_order_puts_the_active_ad_first(cur: Any) -> None:
    """The id alone would pick the closed ad; activity decides only through canonical_rank."""
    pid = _property(cur)
    gone = _advert(cur, pid, source="idnes", active=False)
    live = _advert(cur, pid, source="idnes")
    for lid in (gone, live):
        _placed(cur, lid, "building")
    canonical, location = _orders(cur, pid)
    assert location == canonical == [live, gone]
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (live, live)


def test_a_point_beats_foreign_beats_unlocated_whatever_the_rung(cur: Any) -> None:
    """The served tier first: a coarse point over a finer foreign row over a finer unserved one; no row is last."""
    pid = _property(cur)
    nowhere = _advert(cur, pid, source="idnes")
    unserved = _advert(cur, pid, source="idnes")
    abroad = _advert(cur, pid, source="idnes")
    pointed = _advert(cur, pid, source="idnes", active=False)
    _placed(cur, unserved, "street", point=False)
    _placed(cur, abroad, "obec", point=False, country="foreign")
    _placed(cur, pointed, "kraj")
    canonical, location = _orders(cur, pid)
    assert canonical == [nowhere, unserved, abroad, pointed]
    assert location == [pointed, abroad, unserved, nowhere]
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (nowhere, pointed)


def test_a_single_ad_is_born_and_rolled_up_as_both_ads(cur: Any) -> None:
    from scraper.db import NEW_SINGLETONS_SQL

    lid = _advert(cur, None, source="idnes")  # type: ignore[arg-type]  # an unlinked ad, as the straggler pass finds it
    cur.execute(NEW_SINGLETONS_SQL, {"ids": [lid]})
    pid = int(cur.fetchone()[0])
    assert _pointers(cur, pid) == (lid, lid)
    assert _orders(cur, pid) == ([lid], [lid])
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (lid, lid)


def test_a_property_left_with_no_ads_nulls_both_pointers(cur: Any) -> None:
    from scripts.recompute_property_stats import _RECONCILE_CHILDLESS_SQL

    pid, home = _property(cur), _property(cur)
    lid = _advert(cur, pid, source="idnes")
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (lid, lid)
    cur.execute("UPDATE listings SET property_id = %s WHERE id = %s", (home, lid))
    cur.execute(_RECONCILE_CHILDLESS_SQL)
    assert _pointers(cur, pid) == (None, None)


def test_a_later_location_change_moves_only_the_location_pointer(cur: Any) -> None:
    """The location ad moves while the canonical ad stays: repr_since is kept and the city-proximity stamp is cleared (W4, migration 636)."""
    pid = _property(cur)
    street = _advert(cur, pid, source="idnes")
    closed = _advert(cur, pid, source="idnes", active=False)
    _placed(cur, street, "street")
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (street, street)
    cur.execute("UPDATE properties SET repr_since = '2026-01-01 00:00+00', "
                "city_proximity_computed_at = '2026-02-01 00:00+00' WHERE id = %s "
                "RETURNING repr_since, city_proximity_computed_at", (pid,))
    stamps = cur.fetchone()
    _placed(cur, closed, "address_point")
    _recompute(cur, pid)
    assert _pointers(cur, pid) == (street, closed)
    cur.execute("SELECT repr_since, city_proximity_computed_at FROM properties WHERE id = %s", (pid,))
    assert cur.fetchone() == (stamps[0], None)  # repr_since stays with the canonical ad; the location ad moved


def test_the_function_still_inlines_into_a_lateral_call(cur: Any) -> None:
    """The rollup's CROSS JOIN LATERAL: a Function Scan would mean one call per property."""
    cur.execute("EXPLAIN (VERBOSE, COSTS OFF) "
                "SELECT o.* FROM properties b CROSS JOIN LATERAL property_canonical_listings(b.id) o")
    plan = "\n".join(r[0] for r in cur.fetchall())
    assert "Function Scan" not in plan, plan

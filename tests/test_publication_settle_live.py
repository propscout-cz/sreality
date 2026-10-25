"""Migration 668's stamp and its merge carry, executed. `_settle_fresh` (scripts/recompute_property_stats.py) stamps
`properties.settled_at` on an active, unsettled property first seen inside the cap once its first photo is stored or
none of its ads has one, the engine has an `rt_fp` row for every ad of it in its generation, holds no pair of them for
evidence and keeps none in a group with an ad of another property or of none; a stamp publishes the property to
browse_projection at once (migration 669). `_CARRY_SETTLED_SQL` (toolkit/property_identity.py) hands a retired
property's stamp to an unsettled survivor only while it is earlier than the survivor's cap. Every hold seeds a ready
property beside it, so a stamper that stamps nothing fails instead of passing. Runs in CI's migrations lane
(`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from autodedup.incremental import EVIDENCE_HOLD_REASON, GENERATION
from scripts.recompute_property_stats import _settle_fresh
from tests._live_property import REQUIRED_DB, _advert, _recompute, cur  # noqa: F401 -- `cur` is the fixture
from toolkit.property_identity import _CARRY_SETTLED_SQL

pytestmark = REQUIRED_DB

PRAHA = 554782
POINT = (14.46709, 50.05995)


def _fresh(cur: Any, hours_ago: float = 1) -> int:
    """An active, unsettled property first seen `hours_ago` before the transaction's now()."""
    cur.execute("INSERT INTO properties (first_seen_at) VALUES (now() - make_interval(secs => %s)) RETURNING id",
                (hours_ago * 3600,))
    return int(cur.fetchone()[0])


def _read(cur: Any, listing_id: int) -> None:
    """The engine has read this ad in its live generation."""
    cur.execute("INSERT INTO autodedup.rt_fp (generation, listing_id) VALUES (%s, %s)", (GENERATION, listing_id))


def _ad(cur: Any, pid: int, *, photo: bool | None = True, read: bool = True) -> int:
    """An active ad of `pid`. photo: True stored, False waiting for its download, None none at the source."""
    lid = _advert(cur, pid, source="idnes")
    if photo is not None:
        cur.execute("INSERT INTO images (listing_id, sreality_url, sequence, storage_path) VALUES (%s, %s, 1, %s)",
                    (lid, f"https://cdn.test/settle/{lid}/1.jpg", f"settle/{lid}/1.jpg" if photo else None))
    if read:
        _read(cur, lid)
    return lid


def _loose(cur: Any) -> int:
    """An ad no property holds yet (straggler-attach has not reached it)."""
    cur.execute("INSERT INTO listings (source, source_id_native, raw_json) VALUES ('idnes', %s, '{}'::jsonb) "
                "RETURNING id", (f"settle-{uuid.uuid4()}",))
    return int(cur.fetchone()[0])


def _ready(cur: Any, hours_ago: float = 1) -> int:
    """One ad, its photo stored, read by the engine, held by nothing."""
    pid = _fresh(cur, hours_ago)
    _ad(cur, pid)
    return pid


def _pair(cur: Any, a: int, b: int, *, generation: str = GENERATION) -> None:
    """A merge the engine holds in the band until the photographs arrive."""
    lo, hi = sorted((a, b))
    cur.execute("INSERT INTO autodedup.pairs (generation, listing_lo, listing_hi, probes, zone, decision) "
                "VALUES (%s, %s, %s, '{test}'::text[], 'band', %s)", (generation, lo, hi, EVIDENCE_HOLD_REASON))


def _group(cur: Any, *listing_ids: int) -> None:
    """One engine group of these ads, keyed on the first of them (a listing id no other group of the test uses)."""
    for lid in listing_ids:
        cur.execute("INSERT INTO autodedup.cluster_members (generation, cluster_key, listing_id) VALUES (%s, %s, %s)",
                    (GENERATION, listing_ids[0], lid))


def _settle(cur: Any) -> int:
    """One stamper pass as the maintenance pass runs it; inside the test's open transaction it takes savepoints."""
    return _settle_fresh(cur.connection, renew=lambda: None)


def _settled(cur: Any, *pids: int) -> dict[int, bool]:
    cur.execute("SELECT id, settled_at IS NOT NULL FROM properties WHERE id = ANY(%s)", (list(pids),))
    return dict(cur.fetchall())


def _settled_at(cur: Any, pid: int) -> Any:
    cur.execute("SELECT settled_at FROM properties WHERE id = %s", (pid,))
    return cur.fetchone()[0]


def _browsed(cur: Any, pid: int) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM browse_projection WHERE property_id = %s)", (pid,))
    return bool(cur.fetchone()[0])


def test_a_stored_photo_an_engine_read_and_no_hold_settle_a_fresh_property(cur: Any) -> None:
    pid = _ready(cur)
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_a_property_without_photos_at_the_source_waits_for_none(cur: Any) -> None:
    pid = _fresh(cur)
    _ad(cur, pid, photo=None)
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_a_photo_not_yet_stored_holds_the_property(cur: Any) -> None:
    ready, pid = _ready(cur), _fresh(cur)
    _ad(cur, pid, photo=False)
    _settle(cur)
    assert _settled(cur, ready, pid) == {ready: True, pid: False}


def test_one_ad_the_engine_has_not_read_holds_the_property(cur: Any) -> None:
    ready, pid = _ready(cur), _fresh(cur)
    _ad(cur, pid)
    _ad(cur, pid, read=False)
    _settle(cur)
    assert _settled(cur, ready, pid) == {ready: True, pid: False}


@pytest.mark.parametrize("side", ["listing_lo", "listing_hi"])
def test_a_pair_held_for_evidence_holds_the_property_on_either_side(cur: Any, side: str) -> None:
    ready, pid = _ready(cur), _fresh(cur)
    if side == "listing_lo":
        ad = _ad(cur, pid)
        other = _loose(cur)
    else:
        other = _loose(cur)
        ad = _ad(cur, pid)
    assert (ad < other) == (side == "listing_lo")
    _pair(cur, ad, other)
    _settle(cur)
    assert _settled(cur, ready, pid) == {ready: True, pid: False}


def test_a_held_pair_of_another_generation_holds_nothing(cur: Any) -> None:
    pid = _fresh(cur)
    _pair(cur, _ad(cur, pid), _loose(cur), generation="legacy")
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_a_group_with_an_ad_of_another_property_holds_both(cur: Any) -> None:
    """The merge that would retire one of them has not landed."""
    ready, pid, other = _ready(cur), _fresh(cur), _fresh(cur)
    _group(cur, _ad(cur, pid), _ad(cur, other))
    _settle(cur)
    assert _settled(cur, ready, pid, other) == {ready: True, pid: False, other: False}


def test_a_group_with_an_ad_no_property_holds_yet_holds_the_property(cur: Any) -> None:
    ready, pid = _ready(cur), _fresh(cur)
    _group(cur, _ad(cur, pid), _loose(cur))
    _settle(cur)
    assert _settled(cur, ready, pid) == {ready: True, pid: False}


def test_a_group_entirely_within_the_property_holds_nothing(cur: Any) -> None:
    pid = _fresh(cur)
    _group(cur, _ad(cur, pid), _ad(cur, pid))
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_a_property_past_the_cap_is_no_candidate(cur: Any) -> None:
    """The cap has published it already, so a stamp would change nothing."""
    ready, old = _ready(cur), _ready(cur, hours_ago=7)
    _settle(cur)
    assert _settled(cur, ready, old) == {ready: True, old: False}


def test_a_merge_hands_the_retired_stamp_to_a_survivor_inside_its_cap(cur: Any) -> None:
    retired, survivor = _fresh(cur), _fresh(cur, hours_ago=2)
    cur.execute("UPDATE properties SET settled_at = now() WHERE id = %s RETURNING settled_at", (retired,))
    stamp = cur.fetchone()[0]
    cur.execute(_CARRY_SETTLED_SQL, (survivor, retired))
    assert _settled_at(cur, survivor) == stamp


def test_a_merge_leaves_a_survivor_the_cap_published_unstamped(cur: Any) -> None:
    """A stamp later than the survivor's cap would move the moment it became visible past the moment it did."""
    retired, survivor = _fresh(cur), _fresh(cur, hours_ago=10)
    cur.execute("UPDATE properties SET settled_at = now() WHERE id = %s", (retired,))
    cur.execute(_CARRY_SETTLED_SQL, (survivor, retired))
    assert _settled_at(cur, survivor) is None


def test_a_stamp_publishes_the_property_to_browse_at_once(cur: Any) -> None:
    pid = _fresh(cur)
    ad = _ad(cur, pid)
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version, country_code, obec_name, obec_kod, "
        "  street_name, house_number_cp, house_number_co) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'exact', 'address_point'::location_granularity, 10, "
        "  'cz'::country_status, 'test', '\\x00'::bytea, 'test', 'CZ', 'Praha', %s, 'Pod Sychrovem II', '850', '43')",
        (ad, *POINT, PRAHA))
    _recompute(cur, pid)
    cur.execute("UPDATE properties SET first_seen_at = now() - interval '1 hour' WHERE id = %s "
                "RETURNING status, repr_listing_ref_id, location_listing_ref_id, settled_at", (pid,))
    assert cur.fetchone() == ("active", ad, ad, None)
    assert not _browsed(cur, pid), "an unsettled property inside the cap is in Browse already"
    assert _settle(cur) >= 1, "the pass stamped nothing (its MAINTENANCE warning is in the captured log)"
    assert _settled(cur, pid) == {pid: True}
    assert _browsed(cur, pid)

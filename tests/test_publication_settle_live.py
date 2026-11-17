"""The publication stamp and its merge carry, executed (migrations 668/747). `_settle_fresh`
(scripts/recompute_property_stats.py) stamps `properties.settled_at`, which IS publication (migration 748): never
while an ad of the property awaits the engine's read (no `rt_fp` row in its generation, and its place undetermined or
inside the engine's scope), otherwise once the property is settled (first photo stored or none, every ad read, no pair
held for evidence, no group with an ad of another property or of none) or 6 h old. `_CARRY_SETTLED_SQL`
(toolkit/property_identity.py) hands a retired property's stamp to a survivor not yet published. Every hold seeds a
ready property beside it, so a stamper that stamps nothing fails instead of passing. Runs in CI's migrations lane
(`TEST_DATABASE_URL`); every test rolls back.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from autodedup.incremental import EVIDENCE_HOLD_REASON, GENERATION
from autodedup.incremental_lane import scope_setting_key
from scripts.recompute_property_stats import _drain_dirty, _enqueue_published, _settle_fresh
from tests._live_property import REQUIRED_DB, _advert, _recompute
from toolkit.property_identity import _CARRY_SETTLED_SQL

pytestmark = REQUIRED_DB

PRAHA = 554782
LIBEREC = 563889
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
    return _settle_fresh(cur.connection)


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


def _scope(cur: Any, value: Any) -> None:
    """The engine's scope row, as its seed writes it: a JSON list of `{grain, code}`."""
    cur.execute("INSERT INTO autodedup.settings (key, value) VALUES (%s, %s::jsonb) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (scope_setting_key(GENERATION), value))


_PRAHA_SCOPE = '[{"grain": "obec", "code": %d}]' % PRAHA
_LIBEREC_SCOPE = '[{"grain": "obec", "code": %d}]' % LIBEREC


def _unread(cur: Any, hours_ago: float, *, located: bool = True, photo: bool | None = True) -> int:
    """A property of one ad the engine has not read, its place Praha unless `located` is False."""
    pid = _fresh(cur, hours_ago)
    ad = _ad(cur, pid, photo=photo, read=False)
    if located:
        _locate(cur, ad)
    return pid


def test_the_cap_ends_the_wait_for_a_photo(cur: Any) -> None:
    waiting, old = _fresh(cur), _fresh(cur, hours_ago=7)
    _ad(cur, waiting, photo=False)
    _ad(cur, old, photo=False)
    _settle(cur)
    assert _settled(cur, waiting, old) == {waiting: False, old: True}


def test_the_cap_ends_the_wait_for_an_engine_decision(cur: Any) -> None:
    pid = _fresh(cur, hours_ago=7)
    _pair(cur, _ad(cur, pid), _loose(cur))
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_no_clock_publishes_an_ad_the_engine_will_read_and_has_not(cur: Any) -> None:
    """2026-11-17: a re-list of a property held for weeks was told as new at its 6 h mark, the engine braked. An
    unread ad inside the engine's scope holds its property however old it is; the engine's read releases it."""
    _scope(cur, _PRAHA_SCOPE)
    ready, pid = _ready(cur), _unread(cur, hours_ago=30)
    _settle(cur)
    assert _settled(cur, ready, pid) == {ready: True, pid: False}
    cur.execute("SELECT id FROM listings WHERE property_id = %s", (pid,))
    _read(cur, int(cur.fetchone()[0]))
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_an_ad_outside_the_engines_scope_is_never_waited_for_past_the_cap(cur: Any) -> None:
    """The engine never reads it, so only the cap applies: no special case there, and no stamp before it."""
    _scope(cur, _LIBEREC_SCOPE)
    young, old = _unread(cur, hours_ago=1), _unread(cur, hours_ago=7)
    _settle(cur)
    assert _settled(cur, young, old) == {young: False, old: True}


def test_an_unread_ad_whose_place_is_undetermined_holds_the_property(cur: Any) -> None:
    """Its place may still resolve into the scope; until it resolves no surface serves it anyway (rule 25)."""
    _scope(cur, _LIBEREC_SCOPE)
    ready, pid = _ready(cur), _unread(cur, hours_ago=30, located=False)
    _settle(cur)
    assert _settled(cur, ready, pid) == {ready: True, pid: False}


def test_a_whole_corpus_scope_waits_for_every_unread_ad(cur: Any) -> None:
    _scope(cur, '"all"')
    pid = _unread(cur, hours_ago=30)
    _settle(cur)
    assert _settled(cur, pid) == {pid: False}


def test_without_a_scope_row_the_engine_reads_nothing_and_the_cap_publishes(cur: Any) -> None:
    cur.execute("DELETE FROM autodedup.settings WHERE key = %s", (scope_setting_key(GENERATION),))
    pid = _unread(cur, hours_ago=7)
    _settle(cur)
    assert _settled(cur, pid) == {pid: True}


def test_a_scope_that_cannot_be_read_stamps_nothing(cur: Any, caplog: Any) -> None:
    """Never a guess: publishing under an unknown scope could tell a re-list as new."""
    _scope(cur, '[]')
    ready = _ready(cur)
    with caplog.at_level("WARNING"):
        assert _settle(cur) == 0
    assert _settled(cur, ready) == {ready: False} and "MAINTENANCE settle failed" in caplog.text


def _place(cur: Any, ad: int, *, obec: int | None, cast_obce: int | None, okres: int | None) -> None:
    """A served place carrying these RÚIAN codes."""
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version, country_code, obec_name, obec_kod, "
        "  cast_obce_kod, okres_kod) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'exact', 'obec'::location_granularity, 10, "
        "  'cz'::country_status, 'test', '\\x00'::bytea, 'test', 'CZ', 'Test', %s, %s, %s)",
        (ad, *POINT, obec, cast_obce, okres))


@pytest.mark.parametrize("scope", [
    '[{"grain": "obec", "code": 1001}]', '[{"grain": "cast_obce", "code": 2002}]',
    '[{"grain": "okres", "code": 3003}]', '"all"',
    '[{"grain": "obec", "code": 1001}, {"grain": "okres", "code": 3003}]'])
def test_the_stamper_holds_exactly_the_ads_the_lanes_own_feed_would_claim(cur: Any, scope: str) -> None:
    """THE PIN on the one predicate spelled twice: `_SETTLE_SQL` renders the engine's scope by hand, the lane's
    arrival feed (RT_NEW_LISTINGS_SQL) is its owner. Over one world of unread, placed, cap-old ads the properties
    the stamper holds are exactly those whose ad the feed returns, under every grain and under `all`."""
    from autodedup.incremental_lane import parse_scope
    from autodedup.incremental_sql import RT_NEW_LISTINGS_SQL

    _scope(cur, scope)
    cur.execute("SELECT coalesce(max(id), 0) FROM listings")
    floor = int(cur.fetchone()[0])
    world = {"obec": (1001, None, None), "cast_obce": (9, 2002, None), "okres": (9, None, 3003),
             "elsewhere": (9, 9, 9), "no codes": (None, None, None)}
    ads: dict[int, int] = {}
    for obec, cast_obce, okres in world.values():
        pid = _fresh(cur, hours_ago=30)
        ad = _ad(cur, pid, read=False)
        _place(cur, ad, obec=obec, cast_obce=cast_obce, okres=okres)
        ads[ad] = pid
    cur.execute(RT_NEW_LISTINGS_SQL, {"after_id": floor, "lag": 0, "window": 1000,
                                      **parse_scope(__import__("json").loads(scope)).params()})
    claimed = {int(i) for i in cur.fetchone()[2]} & set(ads)
    _settle(cur)
    held = {ad for ad, pid in ads.items() if not _settled(cur, pid)[pid]}
    assert held == claimed
    assert held or scope != '"all"'


def test_a_merge_hands_the_retired_stamp_to_a_survivor_not_yet_published(cur: Any) -> None:
    """Publication never regresses on a merge, whatever the survivor's age."""
    retired, survivor = _fresh(cur), _fresh(cur, hours_ago=30)
    cur.execute("UPDATE properties SET settled_at = now() WHERE id = %s RETURNING settled_at", (retired,))
    stamp = cur.fetchone()[0]
    cur.execute(_CARRY_SETTLED_SQL, (survivor, retired))
    assert _settled_at(cur, survivor) == stamp


def test_a_merge_leaves_a_published_survivors_stamp_alone(cur: Any) -> None:
    retired, survivor = _fresh(cur), _fresh(cur, hours_ago=10)
    cur.execute("UPDATE properties SET settled_at = now() - interval '4 hours' WHERE id = %s "
                "RETURNING settled_at", (survivor,))
    stamp = cur.fetchone()[0]
    cur.execute("UPDATE properties SET settled_at = now() WHERE id = %s", (retired,))
    cur.execute(_CARRY_SETTLED_SQL, (survivor, retired))
    assert _settled_at(cur, survivor) == stamp


def _locate(cur: Any, ad: int) -> None:
    """A served place for the ad: an address point in Praha."""
    cur.execute(
        "INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, uncertainty_radius_m, "
        "  country_status, resolver_version, claim_set_hash, registry_version, country_code, obec_name, obec_kod, "
        "  street_name, house_number_cp, house_number_co) "
        "VALUES (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'exact', 'address_point'::location_granularity, 10, "
        "  'cz'::country_status, 'test', '\\x00'::bytea, 'test', 'CZ', 'Praha', %s, 'Pod Sychrovem II', '850', '43')",
        (ad, *POINT, PRAHA))


def test_a_stamp_publishes_the_property_to_browse_at_once(cur: Any) -> None:
    pid = _fresh(cur)
    ad = _ad(cur, pid)
    _locate(cur, ad)
    _recompute(cur, pid)
    cur.execute("UPDATE properties SET first_seen_at = now() - interval '1 hour' WHERE id = %s "
                "RETURNING status, repr_listing_ref_id, location_listing_ref_id, settled_at", (pid,))
    assert cur.fetchone() == ("active", ad, ad, None)
    assert not _browsed(cur, pid), "an unstamped property is in Browse already"
    assert _settle(cur) >= 1, "the pass stamped nothing (its MAINTENANCE warning is in the captured log)"
    assert _settled(cur, pid) == {pid: True}
    assert _browsed(cur, pid)


def _aged(cur: Any, hours_ago: float, *, located: bool = True, published: bool = True) -> int:
    """A property of one ad first seen `hours_ago` ago, published unless `published` is False, its place served
    unless `located` is False; the ad carries the age, so a recompute keeps it."""
    pid = _fresh(cur)
    ad = _ad(cur, pid)
    cur.execute("UPDATE listings SET first_seen_at = now() - make_interval(secs => %s) WHERE id = %s",
                (hours_ago * 3600, ad))
    if located:
        _locate(cur, ad)
    _recompute(cur, pid)
    if published:
        cur.execute("UPDATE properties SET settled_at = now() WHERE id = %s", (pid,))
    return pid


def _in(cur: Any, table: str, pids: list[int]) -> set[int]:
    cur.execute(f"SELECT property_id FROM {table} WHERE property_id = ANY(%s)", (pids,))
    return {int(r[0]) for r in cur.fetchall()}


def test_the_pass_queues_what_is_published_but_not_in_browse_and_its_drain_brings_it_in(cur: Any) -> None:
    """A place resolved after the stamp, or a patch a rebuild superseded, writes nothing to the dirty set, so the
    pass's enqueue is what tells the drain: exactly the published, served properties browse_list lacks, first seen
    within the cap plus two hours (older ones are the hourly rebuild's)."""
    crossed, held, unplaced, old, listed = (_aged(cur, 6.1), _aged(cur, 5.9, published=False),
                                            _aged(cur, 6.1, located=False), _aged(cur, 8.5), _aged(cur, 6.1))
    cur.execute("INSERT INTO browse_list SELECT * FROM browse_projection WHERE property_id = %s", (listed,))
    pids = [crossed, held, unplaced, old, listed]
    assert _in(cur, "browse_list", pids) == {listed} and not _in(cur, "dirty_properties", pids)

    _enqueue_published(cur.connection)
    assert _in(cur, "dirty_properties", pids) == {crossed}
    cur.execute("SELECT now()")
    _drain_dirty(cur.connection, 500, cur.fetchone()[0])
    assert _in(cur, "browse_list", pids) == {crossed, listed}
    assert not _in(cur, "dirty_properties", pids)
    _enqueue_published(cur.connection)
    assert not _in(cur, "dirty_properties", pids), "a property its patch brought in is queued again"

"""Migration 735, executed: a watchdog keeps the kinds it tells and its slice. The CHECK refuses what the API refuses,
a kinds edit restarts the watchdog, and the producers tell a watchdog exactly the kinds it is set to. A watchdog
telling New match takes its slice silently at a start, tells each later entry once with its reason (a price cut, a
return, a change, or "now matches"), never a Price drop for the change that brought a property in, nothing for a
return within the day, nothing for an entry our own data alone caused, and nothing for a merge survivor of a member.
A watched collection likewise keeps its kinds. Runs in CI's migrations job (`TEST_DATABASE_URL`); every test rolls
back (a producer's per-statement transaction is a savepoint inside the test's), so `now()` is one instant
throughout: a test moves the slice's clock back by hand to stand for a later pass."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest

from tests._live_property import REQUIRED_DB, _account, _advert, _property, _recompute, _snapshot

pytestmark = REQUIRED_DB

_AGED = "2026-01-01 00:00:00+00"
_SPEC = '{"category_type": "prodej"}'


def _watchdog(cur: Any, acc: Any, kinds: list[str] | None, spec: str = _SPEC) -> Any:
    """A live watchdog over every byt for sale, started long ago (an UPDATE of active_since alone fires nothing)."""
    cols, vals = ("account_id, name, filter_spec", "%s, 'w', %s::jsonb")
    params: list[Any] = [acc, spec]
    if kinds is not None:
        cols, vals = cols + ", notify_kinds", vals + ", %s"
        params.append(kinds)
    cur.execute(f"INSERT INTO notification_subscriptions ({cols}) VALUES ({vals}) RETURNING id", params)  # noqa: S608
    sid = cur.fetchone()[0]
    cur.execute("UPDATE notification_subscriptions SET active_since = %s WHERE id = %s", (_AGED, sid))
    return sid


def _served(cur: Any, pid: int, lid: int, resolved_at: Any = None) -> None:
    """The ad has a resolved place (the consumer rule, rule 25), then the rollup names it canonical and location ad."""
    cur.execute("INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, "
                "  uncertainty_radius_m, country_status, resolver_version, claim_set_hash, registry_version, "
                "  resolved_at) "
                "VALUES (%s, ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326), 'exact', 'building', 5, 'cz', 'test', "
                "  '\\x00'::bytea, 'test', coalesce(%s::timestamptz, now()))", (lid, resolved_at))
    _recompute(cur, pid)


def _old(cur: Any, price: int = 5_000_000, *, served: bool = True) -> tuple[int, int]:
    """A property on the market for weeks with nothing new about it: first seen, placed and published 20 days ago."""
    long_ago = datetime.now(timezone.utc) - timedelta(days=20)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", price=price, first_seen_at=long_ago)
    if served:
        _served(cur, pid, lid, long_ago)
    else:
        _recompute(cur, pid)
    cur.execute("UPDATE properties SET settled_at = %s, repr_since = '-infinity' WHERE id = %s", (long_ago, pid))
    return pid, lid


def _later_pass(cur: Any, sid: Any) -> None:
    """Stand for a pass an hour after the last one: the slice's clock goes back, the members' with it."""
    cur.execute("UPDATE watchdog_slices SET sliced_at = now() - interval '1 hour' WHERE subscription_id = %s", (sid,))


def _members(cur: Any, sid: Any, *pids: int) -> dict[int, bool]:
    """Of `pids`, property -> whether it is in the slice now (False: it left, and is remembered for the day)."""
    cur.execute("SELECT property_id, left_at IS NULL FROM watchdog_slice_members "
                "WHERE subscription_id = %s AND property_id = ANY(%s)", (sid, list(pids)))
    return dict(cur.fetchall())


def _told(cur: Any, pid: int) -> list[tuple[Any, str, str | None]]:
    cur.execute("SELECT subscription_id, change_kind, event_ref FROM notification_dispatches "
                "WHERE property_id = %s ORDER BY dispatched_at, change_kind", (pid,))
    return cur.fetchall()


def _new_matches(cur: Any, sid: Any, *pids: int) -> list[tuple[int, str | None, int | None, int | None]]:
    cur.execute("SELECT property_id, match_reason, trigger_price_czk, prev_price_czk FROM notification_dispatches "
                "WHERE subscription_id = %s AND change_kind = 'new' AND property_id = ANY(%s) ORDER BY property_id",
                (sid, list(pids)))
    return cur.fetchall()


def test_the_check_refuses_what_the_api_refuses(cur: Any) -> None:
    sid = _watchdog(cur, _account(cur), None)
    cur.execute("SELECT notify_kinds FROM notification_subscriptions WHERE id = %s", (sid,))
    assert cur.fetchone()[0] == ["new", "price_drop"], "the default is what every watchdog told before 735"
    for bad in ([], ["sold"], ["reactivated"], ["inactive", "reactivated"]):
        cur.execute("SAVEPOINT kinds")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s", (bad, sid))
        cur.execute("ROLLBACK TO SAVEPOINT kinds")
    cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s",
                (["inactive", "new_source"], sid))


def test_a_kinds_edit_restarts_the_watchdog_and_the_same_set_does_not(cur: Any) -> None:
    sid = _watchdog(cur, _account(cur), None)
    for kinds, restarts in ((["new", "price_drop"], False), (["new", "price_drop", "inactive"], True)):
        cur.execute("UPDATE notification_subscriptions SET active_since = %s WHERE id = %s", (_AGED, sid))
        cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s", (kinds, sid))
        cur.execute("SELECT active_since = now() FROM notification_subscriptions WHERE id = %s", (sid,))
        assert cur.fetchone()[0] is restarts, kinds


def test_a_match_reason_rides_only_on_a_new_match(cur: Any) -> None:
    sid = _watchdog(cur, _account(cur), None)
    pid, lid = _old(cur)
    for kind, reason, ok in (("new", "price", True), ("price_drop", "price", False), ("new", "sold", False)):
        cur.execute("SAVEPOINT reason")
        insert = ("INSERT INTO notification_dispatches (subscription_id, source_kind, property_id, listing_id, "
                  "change_kind, event_ref, match_reason) VALUES (%s, 'watchdog', %s, %s, %s, %s, %s)")
        if ok:
            cur.execute(insert, (sid, pid, lid, kind, "1", reason))
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                cur.execute(insert, (sid, pid, lid, kind, "2", reason))
        cur.execute("ROLLBACK TO SAVEPOINT reason")


def test_a_return_is_stamped_and_a_detach_is_not(cur: Any) -> None:
    pid = _property(cur)
    cur.execute("UPDATE properties SET is_active = false WHERE id = %s", (pid,))
    cur.execute("UPDATE properties SET is_active = true WHERE id = %s RETURNING back_on_market_at = now()", (pid,))
    assert cur.fetchone()[0] is True
    other = _property(cur)
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s, is_active = false WHERE id = %s",
                (pid, other))
    cur.execute("UPDATE properties SET status = 'active', merged_into = NULL, is_active = true WHERE id = %s "
                "RETURNING back_on_market_at", (other,))
    assert cur.fetchone()[0] is None, "a detach's reactivation is no return to the market"


def test_a_start_takes_the_slice_silently_and_tells_only_what_was_published_after_it(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    sid = _watchdog(cur, acc, None)
    old, _ = _old(cur)
    fresh = _property(cur)
    _served(cur, fresh, _advert(cur, fresh, source="bezrealitky"))
    told_before = _property(cur)
    tlid = _advert(cur, told_before, source="bezrealitky")
    _served(cur, told_before, tlid)
    # The matcher before the slice told it already, with no event_ref.
    cur.execute("INSERT INTO notification_dispatches (subscription_id, source_kind, property_id, listing_id, "
                "change_kind) VALUES (%s, 'watchdog', %s, %s, 'new')", (sid, told_before, tlid))

    stats = nf.match_once(cur.connection)
    assert stats["matches_inserted"] == 1 and stats["subscriptions_failed"] == 0
    assert set(_members(cur, sid, old, fresh, told_before)) == {old, fresh, told_before}
    assert [row[:2] for row in _new_matches(cur, sid, old, fresh, told_before)] == [
        (fresh, "listed"), (told_before, None)]

    # An edit starts it afresh: the slice is taken again, silently.
    cur.execute("UPDATE notification_subscriptions SET filter_spec = %s::jsonb WHERE id = %s",
                ('{"category_type": "prodej", "max_price_czk": 4000000}', sid))
    nf.match_once(cur.connection)
    assert _members(cur, sid, old, fresh, told_before) == {}
    cur.execute("SELECT started_at = active_since FROM watchdog_slices JOIN notification_subscriptions s "
                "ON s.id = subscription_id WHERE subscription_id = %s", (sid,))
    assert cur.fetchone()[0] is True


def test_an_entry_is_one_new_match_naming_its_price_cut_and_no_price_drop_follows(cur: Any) -> None:
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None, '{"category_type": "prodej", "max_price_czk": 5000000}')
    pid, lid = _old(cur, 5_200_000)
    nf.match_once(cur.connection)
    assert _members(cur, sid, pid) == {}

    _later_pass(cur, sid)
    _snapshot(cur, lid, 5_200_000, hours_ago=30)
    _snapshot(cur, lid, 4_900_000, hours_ago=0.5)
    cur.execute("UPDATE listings SET price_czk = 4900000 WHERE id = %s", (lid,))
    _recompute(cur, pid)
    nf.match_once(cur.connection)
    assert _members(cur, sid, pid) == {pid: True}
    assert _new_matches(cur, sid, pid) == [(pid, "price", 4_900_000, 5_200_000)]

    nf.match_changes_once(cur.connection)
    assert [kind for _, kind, _ in _told(cur, pid)] == ["new"], "the cut that brought it in is told once"


def test_a_member_is_told_a_price_drop_after_it_entered(cur: Any) -> None:
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None)
    pid, lid = _old(cur, 5_200_000)
    nf.match_once(cur.connection)
    cur.execute("UPDATE watchdog_slice_members SET entered_at = now() - interval '2 days' "
                "WHERE subscription_id = %s AND property_id = %s", (sid, pid))
    _snapshot(cur, lid, 5_200_000, hours_ago=30)
    down = _snapshot(cur, lid, 4_900_000, hours_ago=1)

    nf.match_changes_once(cur.connection)
    assert _told(cur, pid) == [(sid, "price_drop", str(down))]


def test_a_return_within_the_day_is_silent_and_one_after_it_is_a_new_match(cur: Any) -> None:
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None)
    pid, lid = _old(cur)
    nf.match_once(cur.connection)

    def flip(active: bool) -> None:
        cur.execute("UPDATE listings SET is_active = %s, inactive_at = CASE WHEN %s THEN NULL ELSE now() END "
                    "WHERE id = %s", (active, active, lid))
        _recompute(cur, pid)
        _later_pass(cur, sid)
        nf.match_once(cur.connection)

    flip(False)
    assert _members(cur, sid, pid) == {pid: False}, "it left the slice and is remembered"
    flip(True)
    assert _members(cur, sid, pid) == {pid: True} and _new_matches(cur, sid, pid) == [], "a blink re-enters silently"

    flip(False)
    cur.execute("UPDATE watchdog_slice_members SET left_at = now() - interval '2 days' "
                "WHERE subscription_id = %s AND property_id = %s", (sid, pid))
    _later_pass(cur, sid)
    nf.match_once(cur.connection)
    assert _members(cur, sid, pid) == {}, "gone for more than a day: forgotten"
    flip(True)
    assert _new_matches(cur, sid, pid) == [(pid, "back", 5_000_000, None)]


def test_an_entry_nothing_about_the_property_explains_is_told_unless_our_own_data_could_have(cur: Any,
                                                                                              monkeypatch: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    market = _watchdog(cur, acc, None)
    ours = _watchdog(cur, acc, None, '{"category_type": "prodej", "min_area_m2": 10}')
    # Stand for a filter on our own data (a city index, the rent map) on the second watchdog's spec.
    monkeypatch.setattr(nf, "_always_told", lambda spec: spec.min_area_m2 is None)
    pid, lid = _old(cur, served=False)
    nf.match_once(cur.connection)
    for sid in (market, ours):
        assert _members(cur, sid, pid) == {}, "unplaced: not served"
        _later_pass(cur, sid)
    # Placed long ago (a row a backfill writes): no change of the property's is newer than the last pass.
    _served(cur, pid, lid, datetime.now(timezone.utc) - timedelta(days=20))
    nf.match_once(cur.connection)
    assert _members(cur, market, pid) == {pid: True} == _members(cur, ours, pid)
    assert _new_matches(cur, market, pid) == [(pid, "other", 5_000_000, None)]
    assert _new_matches(cur, ours, pid) == []


def test_a_merge_survivor_of_a_member_takes_its_place_silently(cur: Any) -> None:
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None)
    retired, lid = _old(cur)
    survivor = _property(cur)
    nf.match_once(cur.connection)
    assert _members(cur, sid, retired, survivor) == {retired: True}

    cur.execute("UPDATE listings SET property_id = %s WHERE id = %s", (survivor, lid))
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s, merged_at = now(), "
                "is_active = false WHERE id = %s", (survivor, retired))
    _recompute(cur, survivor)
    cur.execute("UPDATE properties SET settled_at = now() - interval '20 days' WHERE id = %s", (survivor,))
    _later_pass(cur, sid)
    nf.match_once(cur.connection)
    assert _members(cur, sid, retired, survivor) == {retired: False, survivor: True}
    assert _new_matches(cur, sid, retired, survivor) == []


def test_a_watchdog_set_to_left_the_market_is_told_it_and_a_default_one_not(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    telling, silent = _watchdog(cur, acc, ["inactive"]), _watchdog(cur, acc, None)
    gone_at = datetime.now(timezone.utc) - timedelta(days=2)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", active=False, inactive_at=gone_at)
    _served(cur, pid, lid)

    stats = nf.match_lifecycle_once(cur.connection)
    assert stats["subscriptions_evaluated"] == 1, "a watchdog telling no lifecycle kind is not read"
    assert _told(cur, pid) == [(telling, "inactive", str(int(gone_at.timestamp())))]
    assert silent not in {row[0] for row in _told(cur, pid)}


def test_a_watchdog_set_to_price_rises_is_told_a_rise_and_not_a_drop(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    rises = _watchdog(cur, acc, ["price_rise"])
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", price=5_200_000)
    _served(cur, pid, lid)
    cur.execute("UPDATE properties SET repr_since = '-infinity' WHERE id = %s", (pid,))
    _snapshot(cur, lid, 5_000_000, hours_ago=30)
    up = _snapshot(cur, lid, 5_200_000, hours_ago=2)
    _snapshot(cur, lid, 5_100_000, hours_ago=1)

    nf.match_changes_once(cur.connection)
    assert _told(cur, pid) == [(rises, "price_rise", str(up))]
    cur.execute("SELECT trigger_price_czk, prev_price_czk FROM notification_dispatches WHERE property_id = %s",
                (pid,))
    assert cur.fetchone() == (5_200_000, 5_000_000)


def test_a_watchdog_set_to_new_sources_is_told_the_portal_that_joined(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    sources = _watchdog(cur, acc, ["new_source"])
    now = datetime.now(timezone.utc)
    pid = _property(cur)
    first = _advert(cur, pid, source="sreality", first_seen_at=now - timedelta(days=20))
    joined = _advert(cur, pid, source="bezrealitky", first_seen_at=now - timedelta(days=1))
    _advert(cur, pid, source="bezrealitky", first_seen_at=now - timedelta(hours=3))  # the portal's second ad
    _served(cur, pid, first)

    nf.match_lifecycle_once(cur.connection)
    assert _told(cur, pid) == [(sources, "new_source", str(joined))]


def test_a_new_portal_is_told_to_a_slice_watchdog_only_after_the_property_entered(cur: Any) -> None:
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), ["new", "new_source"])
    pid, _ = _old(cur)
    nf.match_once(cur.connection)
    joined = _advert(cur, pid, source="sreality", first_seen_at=datetime.now(timezone.utc) - timedelta(days=1))
    nf.match_lifecycle_once(cur.connection)
    assert _told(cur, pid) == [], "the portal joined before the property entered the slice"
    cur.execute("UPDATE watchdog_slice_members SET entered_at = now() - interval '2 days' "
                "WHERE subscription_id = %s AND property_id = %s", (sid, pid))
    nf.match_lifecycle_once(cur.connection)
    assert _told(cur, pid) == [(sid, "new_source", str(joined))]


def _watched(cur: Any, acc: Any, pid: int, kinds: list[str] | None) -> int:
    """A watched collection holding `pid`, watching and holding it since long ago."""
    from tests._live_property import _collection

    cid = _collection(cur, acc)
    cur.execute("UPDATE collections SET monitoring_enabled = true, notify_kinds = coalesce(%s::text[], notify_kinds) "
                "WHERE id = %s", (kinds, cid))
    cur.execute("UPDATE collections SET monitoring_enabled_at = %s WHERE id = %s", (_AGED, cid))
    cur.execute("INSERT INTO collection_properties (collection_id, property_id, added_at) VALUES (%s, %s, %s)",
                (cid, pid, _AGED))
    return cid


def test_a_collection_checks_its_kinds_and_an_edit_while_watching_restarts_it(cur: Any) -> None:
    from tests._live_property import _collection

    cid = _collection(cur, _account(cur))
    cur.execute("SELECT notify_kinds FROM collections WHERE id = %s", (cid,))
    assert cur.fetchone()[0] == ["price_drop", "price_rise", "inactive", "reactivated", "new_source"], \
        "the default is what every watched collection told before 735"
    for bad in ([], ["new"], ["sold"], ["reactivated"], ["price_drop", "reactivated"]):
        cur.execute("SAVEPOINT kinds")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE collections SET notify_kinds = %s WHERE id = %s", (bad, cid))
        cur.execute("ROLLBACK TO SAVEPOINT kinds")
    # Not watching: an edit stamps nothing, so switching monitoring on later is still the start.
    cur.execute("UPDATE collections SET notify_kinds = %s WHERE id = %s RETURNING monitoring_enabled_at",
                (["price_drop"], cid))
    assert cur.fetchone()[0] is None
    cur.execute("UPDATE collections SET monitoring_enabled = true WHERE id = %s", (cid,))
    for kinds, restarts in ((["price_drop"], False), (["price_drop", "inactive"], True)):
        cur.execute("UPDATE collections SET monitoring_enabled_at = %s WHERE id = %s", (_AGED, cid))
        cur.execute("UPDATE collections SET notify_kinds = %s WHERE id = %s", (kinds, cid))
        cur.execute("SELECT monitoring_enabled_at = now() FROM collections WHERE id = %s", (cid,))
        assert cur.fetchone()[0] is restarts, kinds


def test_a_collection_set_to_left_and_back_on_the_market_is_told_both(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    gone_at = datetime.now(timezone.utc) - timedelta(days=2)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", active=False, inactive_at=gone_at)
    _served(cur, pid, lid)
    cid = _watched(cur, acc, pid, ["inactive", "reactivated"])

    nf.match_lifecycle_once(cur.connection)
    cur.execute("SELECT dispatched_at FROM notification_dispatches WHERE collection_id = %s", (cid,))
    told_at = cur.fetchone()[0]
    cur.execute("UPDATE listings SET is_active = true, inactive_at = NULL, "
                "last_seen_at = clock_timestamp() + interval '1 minute' WHERE id = %s", (lid,))
    _recompute(cur, pid)
    nf.match_lifecycle_once(cur.connection)
    cur.execute("SELECT change_kind, event_ref FROM notification_dispatches WHERE collection_id = %s "
                "ORDER BY change_kind", (cid,))
    assert cur.fetchall() == [("inactive", str(int(gone_at.timestamp()))),
                              ("reactivated", str(int(told_at.timestamp())))]


def test_a_collection_set_to_price_rises_is_told_a_rise_and_not_a_drop(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", price=5_100_000)
    _served(cur, pid, lid)
    cur.execute("UPDATE properties SET repr_since = '-infinity' WHERE id = %s", (pid,))
    rises, every = _watched(cur, acc, pid, ["price_rise"]), _watched(cur, acc, pid, None)
    _snapshot(cur, lid, 5_000_000, hours_ago=30)
    up = _snapshot(cur, lid, 5_200_000, hours_ago=2)
    down = _snapshot(cur, lid, 5_100_000, hours_ago=1)

    nf.match_changes_once(cur.connection)
    cur.execute("SELECT collection_id, change_kind, event_ref FROM notification_dispatches "
                "WHERE property_id = %s ORDER BY collection_id, event_ref", (pid,))
    assert sorted(cur.fetchall()) == sorted([(rises, "price_rise", str(up)), (every, "price_rise", str(up)),
                                             (every, "price_drop", str(down))])


def test_a_collection_that_left_out_new_portal_is_not_told_one(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    now = datetime.now(timezone.utc)
    pid = _property(cur)
    first = _advert(cur, pid, source="sreality", first_seen_at=now - timedelta(days=20))
    joined = _advert(cur, pid, source="bezrealitky", first_seen_at=now - timedelta(days=1))
    _served(cur, pid, first)
    quiet, every = _watched(cur, acc, pid, ["price_drop"]), _watched(cur, acc, pid, None)

    stats = nf.match_lifecycle_once(cur.connection)
    assert stats["monitored_collections"] >= 2
    cur.execute("SELECT collection_id, change_kind, event_ref FROM notification_dispatches WHERE property_id = %s",
                (pid,))
    assert cur.fetchall() == [(every, "new_source", str(joined))], f"collection {quiet} tells price drops only"

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
    assert cur.fetchone()[0] == ["new", "price_drop", "reactivated"], \
        "the default is what every watchdog told before 735: a return was a New match until 754"
    for bad in ([], ["sold"]):
        cur.execute("SAVEPOINT kinds")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s", (bad, sid))
        cur.execute("ROLLBACK TO SAVEPOINT kinds")
    cur.execute("UPDATE notification_subscriptions SET notify_kinds = %s WHERE id = %s",
                (["inactive", "reactivated", "new_source"], sid))


def test_a_kinds_edit_restarts_the_watchdog_and_the_same_set_does_not(cur: Any) -> None:
    sid = _watchdog(cur, _account(cur), None)
    for kinds, restarts in ((["new", "price_drop", "reactivated"], False),
                            (["new", "price_drop", "inactive"], True)):
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


def test_a_return_is_stamped_after_a_day_away_and_a_blink_or_a_detach_is_not(cur: Any) -> None:
    """Migration 754: leaving stamps left_market_at; a return clears it and stamps back_on_market_at only when
    the property was away a day or more (one that left before 754, left_market_at NULL, counts as away)."""
    def flip(pid: int, active: bool) -> tuple[Any, ...]:
        cur.execute("UPDATE properties SET is_active = %s WHERE id = %s "
                    "RETURNING left_market_at = now(), left_market_at IS NULL, back_on_market_at = now()",
                    (active, pid))
        return cur.fetchone()

    pid = _property(cur)
    assert flip(pid, False)[0] is True, "leaving is stamped"
    assert flip(pid, True) == (None, True, None), "a blink: cleared, and no return"
    flip(pid, False)
    cur.execute("UPDATE properties SET left_market_at = now() - interval '25 hours' WHERE id = %s", (pid,))
    assert flip(pid, True) == (None, True, True), "away a day or more: a return"
    cur.execute("UPDATE properties SET is_active = false, back_on_market_at = NULL WHERE id = %s", (pid,))
    cur.execute("UPDATE properties SET left_market_at = NULL WHERE id = %s", (pid,))
    assert flip(pid, True) == (None, True, True), "left before 754: away long enough"
    other = _property(cur)
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s, is_active = false WHERE id = %s",
                (pid, other))
    cur.execute("UPDATE properties SET status = 'active', merged_into = NULL, is_active = true WHERE id = %s "
                "RETURNING back_on_market_at, left_market_at", (other,))
    assert cur.fetchone() == (None, None), "a detach's reactivation is no return to the market"


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


@pytest.mark.parametrize("published", [True, False])
def test_a_merge_that_brings_a_live_property_into_one_long_gone_is_its_return(cur: Any, published: bool) -> None:
    """#2583 (migration 777): a survivor long off the market flips active when a live property is merged into it,
    published or not: the trigger stamps the return, and who is told is decided per follower in the producers."""
    survivor, retired = _property(cur), _property(cur)
    cur.execute("UPDATE properties SET is_active = false WHERE id = %s", (survivor,))
    cur.execute("UPDATE properties SET left_market_at = now() - interval '90 days' WHERE id = %s", (survivor,))
    cur.execute("UPDATE properties SET status = 'merged_away', merged_into = %s, merged_at = now(), is_active = false, "
                "settled_at = CASE WHEN %s THEN now() - interval '3 days' END WHERE id = %s",
                (survivor, published, retired))
    cur.execute("UPDATE properties SET is_active = true WHERE id = %s "
                "RETURNING back_on_market_at = now(), left_market_at", (survivor,))
    assert cur.fetchone() == (True, None)


def test_a_property_born_off_the_market_is_stamped_as_left(cur: Any) -> None:
    """A record born with no active ad (a split's, an ad first fetched gone) left now, so its first active ad
    within the day is no return."""
    cur.execute("INSERT INTO properties (is_active) VALUES (false) RETURNING id, left_market_at = now()")
    pid, stamped = cur.fetchone()
    assert stamped is True
    cur.execute("UPDATE properties SET is_active = true WHERE id = %s RETURNING back_on_market_at", (pid,))
    assert cur.fetchone() == (None,)
    cur.execute("INSERT INTO properties (is_active) VALUES (true) RETURNING left_market_at")
    assert cur.fetchone() == (None,)


def test_a_watchdog_with_no_slice_is_told_a_return_on_its_spec(cur: Any) -> None:
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), ["price_drop", "reactivated"])
    pid, lid = _old(cur)
    cur.execute("UPDATE listings SET is_active = false, inactive_at = now() WHERE id = %s", (lid,))
    _recompute(cur, pid)
    cur.execute("UPDATE properties SET left_market_at = now() - interval '2 days' WHERE id = %s", (pid,))
    cur.execute("UPDATE listings SET is_active = true, inactive_at = NULL WHERE id = %s", (lid,))
    _recompute(cur, pid)
    cur.execute("UPDATE properties SET back_on_market_at = now() + interval '1 second' WHERE id = %s "
                "RETURNING floor(extract(epoch FROM back_on_market_at))::bigint::text", (pid,))
    back = cur.fetchone()[0]
    nf.match_once(cur.connection)
    nf.match_lifecycle_once(cur.connection)
    nf.match_lifecycle_once(cur.connection)
    assert _told(cur, pid) == [(sid, "reactivated", back)]


def test_a_return_within_the_day_is_silent_and_one_after_it_is_back_on_the_market_never_a_new_match(
        cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    sid = _watchdog(cur, acc, ["new", "price_drop", "reactivated"])
    quiet = _watchdog(cur, acc, ["new", "price_drop"])
    pid, lid = _old(cur)
    nf.match_once(cur.connection)

    def flip(active: bool) -> None:
        cur.execute("UPDATE listings SET is_active = %s, inactive_at = CASE WHEN %s THEN NULL ELSE now() END "
                    "WHERE id = %s", (active, active, lid))
        _recompute(cur, pid)
        for watchdog in (sid, quiet):
            _later_pass(cur, watchdog)
        nf.match_once(cur.connection)
        nf.match_lifecycle_once(cur.connection)

    flip(False)
    assert _members(cur, sid, pid) == {pid: False}, "it left the slice and is remembered"
    flip(True)
    assert _members(cur, sid, pid) == {pid: True} and _told(cur, pid) == [], "a blink re-enters silently"

    flip(False)
    cur.execute("UPDATE watchdog_slice_members SET left_at = now() - interval '2 days' WHERE property_id = %s",
                (pid,))
    cur.execute("UPDATE properties SET left_market_at = now() - interval '2 days' WHERE id = %s", (pid,))
    for watchdog in (sid, quiet):
        _later_pass(cur, watchdog)
    nf.match_once(cur.connection)
    assert _members(cur, sid, pid) == {}, "gone for more than a day: forgotten"
    flip(True)
    cur.execute("SELECT floor(extract(epoch FROM back_on_market_at))::bigint::text FROM properties WHERE id = %s",
                (pid,))
    back = cur.fetchone()[0]
    assert back is not None and _told(cur, pid) == [(sid, "reactivated", back)], \
        "one alert, its own kind, from the watchdog set to tell it; the other takes it back silently"
    assert _members(cur, quiet, pid) == {pid: True}
    nf.match_lifecycle_once(cur.connection)
    assert len(_told(cur, pid)) == 1, "a re-run tells nothing more"


def test_an_entry_nothing_about_the_property_explains_is_told_only_by_a_velocity_spec(cur: Any,
                                                                                       monkeypatch: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    market = _watchdog(cur, acc, None)
    velocity = _watchdog(cur, acc, None, '{"category_type": "prodej", "min_area_m2": 10}')
    # Stand for a Velocity filter on the second watchdog's spec.
    monkeypatch.setattr(nf, "_always_told", lambda spec: spec.min_area_m2 is not None)
    pid, lid = _old(cur, served=False)
    nf.match_once(cur.connection)
    for sid in (market, velocity):
        assert _members(cur, sid, pid) == {}, "unplaced: not served"
        _later_pass(cur, sid)
    # Placed long ago (a row a backfill writes): no change of the property's is newer than the last pass.
    _served(cur, pid, lid, datetime.now(timezone.utc) - timedelta(days=20))
    nf.match_once(cur.connection)
    assert _members(cur, market, pid) == {pid: True} == _members(cur, velocity, pid)
    assert _new_matches(cur, market, pid) == [], "no market event: a member, never told"
    assert _new_matches(cur, velocity, pid) == [(pid, "other", 5_000_000, None)]


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
    # The survivor was long gone and the merge is its return (migration 777): still silent, the member was told.
    cur.execute("UPDATE properties SET settled_at = now() - interval '20 days', back_on_market_at = now() "
                "WHERE id = %s", (survivor,))
    _later_pass(cur, sid)
    nf.match_once(cur.connection)
    assert _members(cur, sid, retired, survivor) == {retired: False, survivor: True}
    assert _new_matches(cur, sid, retired, survivor) == []
    assert _told(cur, survivor) == _told(cur, retired) == []


def test_a_price_bouncing_across_the_cap_inside_the_day_sends_nothing(cur: Any) -> None:
    """Out on a rise, back on a cut, inside SLICE_BLINK: no New match, and neither step is told afterwards."""
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), ["new", "price_drop", "price_rise"],
                    '{"category_type": "prodej", "max_price_czk": 5000000}')
    pid, lid = _old(cur, 4_900_000)
    nf.match_once(cur.connection)
    cur.execute("UPDATE watchdog_slice_members SET entered_at = now() - interval '2 days' "
                "WHERE subscription_id = %s AND property_id = %s", (sid, pid))
    _snapshot(cur, lid, 4_900_000, hours_ago=30)
    for price, hours_ago in ((5_200_000, 0.8), (4_900_000, 0.5)):
        _snapshot(cur, lid, price, hours_ago=hours_ago)
        cur.execute("UPDATE listings SET price_czk = %s WHERE id = %s", (price, lid))
        _recompute(cur, pid)
        _later_pass(cur, sid)
        nf.match_once(cur.connection)
        nf.match_changes_once(cur.connection)
    assert _members(cur, sid, pid) == {pid: True}
    assert _told(cur, pid) == [], "a blink sends nothing"


def test_a_property_split_off_a_member_enters_silently(cur: Any) -> None:
    """A detach (an operator split, an engine undo) closes the ad's ledger row: the slice already showed the ad."""
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None)
    parent, lid = _old(cur)
    long_ago = datetime.now(timezone.utc) - timedelta(days=20)
    moved = _advert(cur, parent, source="sreality", first_seen_at=long_ago)
    cur.execute("INSERT INTO listing_location (listing_id, geom, match_confidence, granularity, "
                "  uncertainty_radius_m, country_status, resolver_version, claim_set_hash, registry_version, "
                "  resolved_at) "
                "VALUES (%s, ST_SetSRID(ST_MakePoint(14.42, 50.08), 4326), 'exact', 'building', 5, 'cz', 'test', "
                "  '\\x00'::bytea, 'test', %s)", (moved, long_ago))
    _recompute(cur, parent)
    nf.match_once(cur.connection)

    split = _property(cur)
    cur.execute("UPDATE listings SET property_id = %s WHERE id = %s", (split, moved))
    cur.execute("INSERT INTO property_merge_events (merge_group_id, survivor_property_id, retired_property_id, "
                "  listing_ref_id, prev_property_id, reason, source, undone_at, undone_by) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'split_new', 'operator', now(), 'test')",
                (parent, split, moved, split))
    _recompute(cur, parent)
    _recompute(cur, split)
    cur.execute("UPDATE properties SET settled_at = %s WHERE id = %s", (long_ago, split))
    _later_pass(cur, sid)
    nf.match_once(cur.connection)
    assert _members(cur, sid, parent, split) == {parent: True, split: True}
    assert _new_matches(cur, sid, parent, split) == []


def test_a_property_first_seen_within_the_day_enters_as_newly_listed_whatever_let_it_in(cur: Any,
                                                                                         monkeypatch: Any) -> None:
    """Published hours ago but let in only now (its place or a proximity filled in late): still newly listed, so a
    watchdog on our own data tells it too."""
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None)
    monkeypatch.setattr(nf, "_always_told", lambda spec: False)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", first_seen_at=datetime.now(timezone.utc) - timedelta(hours=8))
    _recompute(cur, pid)
    cur.execute("UPDATE properties SET settled_at = now() - interval '7 hours' WHERE id = %s", (pid,))
    nf.match_once(cur.connection)
    _later_pass(cur, sid)
    _served(cur, pid, lid, datetime.now(timezone.utc) - timedelta(hours=7))
    nf.match_once(cur.connection)
    assert _new_matches(cur, sid, pid) == [(pid, "listed", 5_000_000, None)]


def test_two_new_matches_of_one_property_inside_the_day_are_merge_twins_and_the_later_is_withdrawn(cur: Any) -> None:
    """Each entry carries its own event_ref, so a merge marks no twin; VISIBLE withdraws the later one, unless the
    earlier is itself withdrawn (newly listed about a property first seen long before)."""
    from api import notifications as nf

    sid = _watchdog(cur, _account(cur), None)
    pid, lid = _old(cur)
    recent, old = _property(cur), _old(cur)[0]
    cur.execute("UPDATE properties SET first_seen_at = now() - interval '3 hours' WHERE id = %s", (recent,))

    def told(prop: int, hours_ago: float, reason: str) -> None:
        cur.execute("INSERT INTO notification_dispatches (subscription_id, source_kind, property_id, listing_id, "
                    "  change_kind, event_ref, match_reason, dispatched_at) "
                    "VALUES (%s, 'watchdog', %s, %s, 'new', %s, %s, now() - make_interval(secs => %s))",
                    (sid, prop, lid, str(hours_ago), reason, hours_ago * 3600))

    def visible(prop: int) -> list[str]:
        cur.execute(f"SELECT d.event_ref FROM notification_dispatches d WHERE d.property_id = %s AND {nf.VISIBLE_SQL} "
                    "ORDER BY d.dispatched_at", (prop,))
        return [row[0] for row in cur.fetchall()]

    told(recent, 2, "listed")
    told(recent, 1, "listed")
    assert visible(recent) == ["2"], "the twin told an hour later is withdrawn"
    told(old, 2, "listed")  # first seen 20 days before it: withdrawn itself (ruling 5)
    told(old, 1, "price")
    assert visible(old) == ["1"], "a withdrawn earlier one withdraws nothing"
    told(pid, 30, "price")
    told(pid, 1, "back")
    assert visible(pid) == ["30", "1"], "a day apart: two entries"


def test_a_watchdog_set_to_left_the_market_is_told_it_and_a_default_one_not(cur: Any) -> None:
    from api import notifications as nf

    acc = _account(cur)
    telling, silent = _watchdog(cur, acc, ["inactive"]), _watchdog(cur, acc, ["new", "price_drop"])
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
    for bad in ([], ["new"], ["sold"]):
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


@pytest.mark.parametrize("kinds", [["inactive", "reactivated"], ["reactivated"]])
def test_a_collection_is_told_a_return_with_or_without_left_the_market(cur: Any, kinds: list[str]) -> None:
    """The return is the property's fact (migration 754), so Back on the market needs no Left the market alert."""
    from api import notifications as nf

    acc = _account(cur)
    gone_at = datetime.now(timezone.utc) - timedelta(days=2)
    pid = _property(cur)
    lid = _advert(cur, pid, source="bezrealitky", active=False, inactive_at=gone_at)
    _served(cur, pid, lid)
    cid = _watched(cur, acc, pid, kinds)

    nf.match_lifecycle_once(cur.connection)
    cur.execute("UPDATE properties SET left_market_at = %s WHERE id = %s", (gone_at, pid))
    cur.execute("UPDATE listings SET is_active = true, inactive_at = NULL, "
                "last_seen_at = clock_timestamp() + interval '1 minute' WHERE id = %s", (lid,))
    _recompute(cur, pid)
    nf.match_lifecycle_once(cur.connection)
    nf.match_lifecycle_once(cur.connection)
    cur.execute("SELECT floor(extract(epoch FROM back_on_market_at))::bigint::text FROM properties WHERE id = %s",
                (pid,))
    back = cur.fetchone()[0]
    cur.execute("SELECT change_kind, event_ref FROM notification_dispatches WHERE collection_id = %s "
                "ORDER BY change_kind", (cid,))
    left = [("inactive", str(int(gone_at.timestamp())))] if "inactive" in kinds else []
    assert cur.fetchall() == [*left, ("reactivated", back)]


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


def test_a_source_with_members_reads_only_theirs_and_writes_what_the_market_read_wrote(cur: Any) -> None:
    """#2504: the monitor and a slice read only their members' delistings and new portals. Rendered over the market
    (`within` cleared, the shape before), each statement writes the very same rows: every window and group there is
    one property's, so a member's row cannot change, and a stranger's event never had anyone to tell. A property in
    two collections is told once per collection (the narrowing is a semi-join)."""
    from dataclasses import replace

    from api import notifications as nf

    acc = _account(cur)
    now = datetime.now(timezone.utc)
    gone_at = now - timedelta(days=2)
    sid = _watchdog(cur, acc, ["new", "new_source"])
    sliced, _ = _old(cur)
    nf.match_once(cur.connection)
    slice_joined = _advert(cur, sliced, source="sreality", first_seen_at=now - timedelta(days=1))
    cur.execute("UPDATE watchdog_slice_members SET entered_at = now() - interval '2 days' "
                "WHERE subscription_id = %s AND property_id = %s", (sid, sliced))

    def left() -> int:
        pid = _property(cur)
        _served(cur, pid, _advert(cur, pid, source="bezrealitky", active=False, inactive_at=gone_at))
        return pid

    def joined() -> tuple[int, int]:
        pid = _property(cur)
        first = _advert(cur, pid, source="sreality", first_seen_at=now - timedelta(days=20))
        lid = _advert(cur, pid, source="bezrealitky", first_seen_at=now - timedelta(days=1))
        _served(cur, pid, first)
        return pid, lid

    # Strangers: a delisting and a new portal in no collection and no slice, on the market read only.
    watched_left, _stranger_left = left(), left()
    (watched, watched_lid), _stranger = joined(), joined()
    one, two = _watched(cur, acc, watched_left, None), _watched(cur, acc, watched_left, None)
    cur.execute("INSERT INTO collection_properties (collection_id, property_id, added_at) "
                "VALUES (%s, %s, %s), (%s, %s, %s)", (one, watched, _AGED, two, watched, _AGED))
    cur.execute("SELECT active_since FROM notification_subscriptions WHERE id = %s", (sid,))
    monitor = {"monitor_window": nf.MONITOR_WINDOW, "inactive_dwell": nf.INACTIVE_DWELL}
    sliced_params = {**monitor, "subscription_id": str(sid), "active_since": cur.fetchone()[0],
                     "kinds": ["new", "new_source"]}

    def written(sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        cur.execute("SAVEPOINT shape")
        cur.execute(sql + " RETURNING collection_id, subscription_id, property_id, change_kind, event_ref", params)
        rows = sorted(cur.fetchall(), key=str)
        cur.execute("ROLLBACK TO SAVEPOINT shape")
        return rows

    epoch = str(int(gone_at.timestamp()))
    market_monitor, market_slice = replace(nf._MONITOR, within=""), replace(nf._WATCHDOG_SLICE, within="")
    for narrowed, market, params, told in (
        (nf._MONITOR_INACTIVE_SQL, nf._inactive_sql(market_monitor), monitor,
         [(one, None, watched_left, "inactive", epoch), (two, None, watched_left, "inactive", epoch)]),
        (nf._MONITOR_NEW_SOURCE_SQL, nf._new_source_sql(market_monitor), monitor,
         [(one, None, watched, "new_source", str(watched_lid)), (two, None, watched, "new_source", str(watched_lid))]),
        (nf._WATCHDOG_SLICE_NEW_SOURCE_SQL, nf._new_source_sql(market_slice), sliced_params,
         [(None, sid, sliced, "new_source", str(slice_joined))]),
    ):
        assert narrowed != market
        assert written(narrowed, params) == written(market, params) == sorted(told, key=str), narrowed

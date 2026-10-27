"""E976 — a build reads the scope's LIVE adverts first.

The operator, 2026-10-22: Praha's visible duplicates merged as fast as possible. The widening to
the whole of Praha left 37,910 adverts unread, 11,864 of them live, and the build's feed took them
in listing-id order after a cursor: the live ones (median id 18.7M against 0.5M overall) came
last, ~3 days in. During a build the feed now orders live before inactive, ids ascending within
each, under no id cursor (the `rt_fp` anti-join advances it); outside a build nothing changes.

E979, the operator 2026-10-25: a newly widened block first, in a category order they choose —
Liberec, flats for rent, houses for sale, flats for sale, the rest — read out of
`rt_build_priority:<generation>`, so the order changes without a deploy; no row is E978's order.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from autodedup import incremental_lane
from autodedup import incremental_sql as S
from autodedup.incremental import WorkItem, bootstrap_key
from autodedup.incremental_lane import (
    CURSOR_ENTER,
    SqlWork,
    build_priority_key,
    parse_build_priority,
    pass_rate_key,
    run_incremental,
)
from autodedup.incremental_scope import Scope, ScopeBlock
from tests.autodedup.fake_pg import FakePg
from tests.autodedup.test_rt_widening import NEW_IDS, OTHER_TOWN, TOWN, _built_town, _seed

GEN = "rt"
BLOCK = Scope((ScopeBlock("obec", TOWN),))
# id -> is_active; None = no `listings` row at all.
WORLD: dict[int, bool | None] = {1: False, 2: True, 3: False, 4: True, 5: None, 6: True}


def _world(world: dict[int, bool | None] = WORLD) -> FakePg:
    db = FakePg()
    for listing_id, live in world.items():
        db.locations[listing_id] = {"obec_kod": TOWN, "cast_obce_kod": None,
                                    "resolved_at": db.now - timedelta(days=1)}
        if live is not None:
            db.listings[listing_id] = {"id": listing_id, "is_active": live,
                                       "first_seen_at": db.now - timedelta(days=30)}
    # The forward feed is past them: these are a widening's held history, not arrivals.
    db.cursors["rt_new"] = {"last_listing_id": max(world), "last_snapshot_id": None,
                            "watermark": None}
    return db


def _work(db: FakePg, **kw: Any) -> SqlWork:
    params: dict[str, Any] = {"straggler_window": 0, "revive_slice": 0, "drift_slice": 0,
                              "evidence_slice": 0, "lag": 0, "enter_slice": 1000}
    params.update(kw)
    return SqlWork(db, BLOCK, GEN, **params)


def _entered(work: SqlWork, limit: int = 50) -> list[int]:
    return [item.listing_id for item in work.claim(limit) if item.feed == "entered"]


def _decide(db: FakePg, work: SqlWork, ids: list[int]) -> dict[str, Any]:
    """What a committed pass leaves: a fingerprint row each, the feed's cursor written."""
    for listing_id in ids:
        db.rt_fp[(GEN, listing_id)] = {"is_active": True}
    return work.commit([WorkItem(listing_id, "entered", None, None) for listing_id in ids])


def test_a_build_reads_live_adverts_first_and_ids_ascending_within_each() -> None:
    db = _world()
    assert _entered(_work(db, bootstrap=True)) == [2, 4, 6, 1, 3, 5], (
        "live, then inactive, then the advert with no listings row")
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL in db.statements
    assert S.RT_SCOPE_ENTRANTS_SQL not in db.statements


def test_a_short_build_claim_takes_the_live_adverts_first() -> None:
    db = _world()
    assert _entered(_work(db, bootstrap=True), limit=2) == [2, 4]


def test_the_build_feed_is_advanced_by_the_fingerprints_not_by_an_id_cursor() -> None:
    """Two keys are no keyset: the id half of the cursor stays 0 and what a pass decided leaves
    the feed because it now has an `rt_fp` row, so the next pass takes the next live advert."""
    db = _world()
    first = _work(db, bootstrap=True)
    taken = _entered(first, limit=2)
    assert taken == [2, 4]
    out = _decide(db, first, taken)
    assert out[CURSOR_ENTER][0] == 0, "no id cursor during a build"
    assert db.cursors[CURSOR_ENTER]["last_listing_id"] == 0

    second = _work(db, bootstrap=True)
    assert _entered(second, limit=2) == [6, 1], "the last live advert, then the inactive"


def test_a_cursor_left_by_the_id_ordered_feed_skips_nothing_in_a_build() -> None:
    """A build that was running when E976 deployed holds a cursor deep in the id range: the build
    feed reads no cursor, so the live adverts below it are still read first."""
    db = _world()
    db.cursors[CURSOR_ENTER] = {"last_listing_id": 5, "last_snapshot_id": 0, "watermark": None}
    assert _entered(_work(db, bootstrap=True))[:3] == [2, 4, 6]


def test_the_build_feed_keeps_the_settle_lag() -> None:
    db = _world()
    db.locations[2]["resolved_at"] = db.now - timedelta(seconds=5)
    assert 2 not in _entered(_work(db, bootstrap=True, lag=300))
    db.now += timedelta(minutes=10)
    assert _entered(_work(db, bootstrap=True, lag=300))[:3] == [2, 4, 6]


def test_outside_a_build_the_feed_is_the_id_cursor_it_always_was() -> None:
    """Steady state: the same statement, the same id order after the cursor, the cursor moved
    to the last id taken (or wrapped on a short claim)."""
    db = _world()
    ordinary = _work(db)
    taken = _entered(ordinary, limit=10)
    assert taken == [1, 2], "a fifth of the claim, in id order"
    assert S.RT_SCOPE_ENTRANTS_SQL in db.statements
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL not in db.statements
    assert _decide(db, ordinary, taken)[CURSOR_ENTER][0] == 2

    later = _work(db)
    assert _entered(later, limit=10) == [3, 4], "after the cursor, by id, live or not"
    db.cursors[CURSOR_ENTER] = {"last_listing_id": 4, "last_snapshot_id": 0, "watermark": None}
    db.statements.clear()
    assert _entered(_work(db), limit=50) == [5, 6]
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL not in db.statements


def test_the_build_statement_reads_is_active_by_primary_key_and_takes_no_cursor() -> None:
    flat = " ".join(S.RT_SCOPE_BUILD_ENTRANTS_SQL.split())
    assert "%(after_id)s" not in flat
    # E979's two keys lead; E978's visibility key follows them unchanged.
    assert ("order by (s.block_key = any(%(priority_blocks)s::text[])) desc, case when "
            "s.block_key = any(%(priority_blocks)s::text[]) then coalesce((select "
            "array_position(%(priority_cats)s::text[], l.category_main || '/' || "
            "l.category_type) from public.listings l where l.id = s.listing_id), "
            "cardinality(%(priority_cats)s::text[]) + 1) else 0 end, "
            "(select l.is_active or exists (select 1 from public.listings o where ") in flat
    assert ("(select l.is_active from public.listings l where l.id = s.listing_id) "
            "desc nulls last, s.listing_id limit %(limit)s") in flat
    assert ("not exists (select 1 from autodedup.rt_fp f where f.generation = "
            "%(generation)s::text and f.listing_id = s.listing_id)") in flat
    assert "make_interval(secs => %(lag)s)" in flat
    # The steady-state statement is untouched: no `public` table, the id keyset.
    steady = " ".join(S.RT_SCOPE_ENTRANTS_SQL.split())
    assert "public." not in steady
    assert "s.listing_id > %(after_id)s::bigint" in steady and "order by s.listing_id" in steady


def test_a_widening_build_reads_the_new_blocks_live_adverts_first(tmp_path: Path) -> None:
    """The live path end to end: a widening opens the build, the pass reads three adverts, and
    the three it reads are the new block's live ones although their ids are the highest."""
    conn = _built_town(tmp_path)
    for listing_id in list(NEW_IDS)[:3]:
        conn.listings[listing_id]["is_active"] = False
    _seed(conn, tmp_path, f"obec:{TOWN} obec:{OTHER_TOWN}")
    assert conn.settings[bootstrap_key(GEN)] is True
    conn.settings[pass_rate_key(GEN)] = 3.5 / 787.5          # three adverts a build pass

    out = run_incremental(lambda: conn)

    assert not out["aborted"] and out["feeds"].get("entered") == 3
    read = {i for (g, i) in conn.rt_fp if g == GEN} & set(NEW_IDS)
    live = set(list(NEW_IDS)[3:])
    assert live <= read, "the live adverts, before the lower inactive ids"
    # The straggler sweep (the last rows of `listings`, by id) is not the build's feed and keeps
    # its own fifth; nothing else reads an inactive advert of the new block.
    assert len(read - live) <= out["feeds"].get("straggler", 0)
    assert conn.cursors[CURSOR_ENTER]["last_listing_id"] == 0


def test_e978_an_inactive_advert_on_a_live_property_or_a_live_group_comes_before_the_rest() -> None:
    """E978: after the live adverts, the build reads the inactive ones a VISIBLE merge waits for —
    on a property holding a live advert, or on a property whose read adverts sit in a group with
    a live advert — before every other inactive advert; ids ascending within each half."""
    world: dict[int, bool | None] = {10: False, 11: False, 12: False, 13: False, 14: True, 15: True}
    db = _world(world)
    # 11 shares property 500 with live 14; 12 is on property 600, whose read advert 16 sits in a
    # group with live 15; 10 and 13 are inactive on properties nobody live touches.
    for listing_id, prop in ((10, 100), (11, 500), (12, 600), (13, 300), (14, 500), (15, 700)):
        db.listings[listing_id]["property_id"] = prop
    db.listings[16] = {"id": 16, "is_active": False, "property_id": 600,
                       "first_seen_at": db.now - timedelta(days=30)}
    db.rt_fp[(GEN, 16)] = {"is_active": False}
    db.cluster_members.update({(GEN, 9001, 16), (GEN, 9001, 15)})
    assert _entered(_work(db, bootstrap=True), limit=50) == [14, 15, 11, 12, 10, 13]
    assert _entered(_work(db, bootstrap=True), limit=3) == [14, 15, 11]


# --- E979: the operator's blocks first, in their category order ---------------------------------

LIBEREC_LIKE = OTHER_TOWN
PRIORITY = {"blocks": [f"obec:{LIBEREC_LIKE}"],
            "categories": [["byt", "pronajem"], ["dum", "prodej"], ["byt", "prodej"]]}
TWO_BLOCKS = Scope((ScopeBlock("obec", TOWN), ScopeBlock("obec", LIBEREC_LIKE)))
# id -> (obec, is_active, category, property); is_active None = no `listings` row at all.
TWO_WORLD: dict[int, tuple[int, bool | None, tuple[str, str] | None, int | None]] = {
    1: (TOWN, False, ("byt", "prodej"), None),
    2: (TOWN, True, ("dum", "prodej"), None),
    3: (TOWN, True, ("byt", "pronajem"), None),            # a listed category, outside the block
    10: (LIBEREC_LIKE, True, ("byt", "prodej"), None),
    11: (LIBEREC_LIKE, False, ("dum", "prodej"), None),
    12: (LIBEREC_LIKE, False, ("byt", "pronajem"), None),
    13: (LIBEREC_LIKE, True, ("byt", "pronajem"), 800),
    14: (LIBEREC_LIKE, True, ("komercni", "prodej"), None),  # unlisted: after the listed ones
    15: (LIBEREC_LIKE, True, ("dum", "prodej"), None),
    16: (LIBEREC_LIKE, False, ("byt", "prodej"), None),
    17: (LIBEREC_LIKE, None, None, None),                   # no listings row: last of the block
    18: (LIBEREC_LIKE, False, ("byt", "pronajem"), 800),     # inactive, beside live 13: visible
}


def _two_blocks() -> FakePg:
    db = FakePg()
    for listing_id, (obec, live, category, prop) in TWO_WORLD.items():
        db.locations[listing_id] = {"obec_kod": obec, "cast_obce_kod": None,
                                    "resolved_at": db.now - timedelta(days=1)}
        if live is not None:
            main, kind = category or (None, None)
            db.listings[listing_id] = {"id": listing_id, "is_active": live, "property_id": prop,
                                       "category_main": main, "category_type": kind,
                                       "first_seen_at": db.now - timedelta(days=30)}
    db.cursors["rt_new"] = {"last_listing_id": max(TWO_WORLD), "last_snapshot_id": None,
                            "watermark": None}
    return db


def _two_work(db: FakePg, priority: Any = None, **kw: Any) -> SqlWork:
    params: dict[str, Any] = {"straggler_window": 0, "revive_slice": 0, "drift_slice": 0,
                              "evidence_slice": 0, "lag": 0, "enter_slice": 1000,
                              "bootstrap": True}
    if priority is not None:
        params["build_priority"] = parse_build_priority(priority)
    params.update(kw)
    return SqlWork(db, TWO_BLOCKS, GEN, **params)


def test_e979_the_priority_block_is_read_first_in_the_operators_category_order() -> None:
    """Inside the named block: flats for rent, houses for sale, flats for sale, then the rest;
    within a category E978's order (visible, then live, then id). Then every other block in
    E978's order, its listed categories promoting nothing."""
    db = _two_blocks()
    assert _entered(_two_work(db, PRIORITY)) == [
        13, 18, 12,            # byt/pronajem: live, visible inactive, inactive
        15, 11,                # dum/prodej
        10, 16,                # byt/prodej
        14, 17,                # an unlisted category, then no listings row
        2, 3, 1,               # the other block: live first, ids ascending
    ]
    assert _entered(_two_work(db, PRIORITY), limit=4) == [13, 18, 12, 15]


def test_e979_no_priority_is_exactly_e978s_order() -> None:
    db = _two_blocks()
    e978 = [2, 3, 10, 13, 14, 15, 18, 1, 11, 12, 16, 17]
    assert _entered(_two_work(db)) == e978
    assert _entered(_two_work(db, {"garbage": True})) == e978
    assert _entered(_two_work(db, {"blocks": [], "categories": PRIORITY["categories"]})) == e978


def test_e979_a_priority_block_without_categories_comes_first_in_e978s_order() -> None:
    db = _two_blocks()
    assert _entered(_two_work(db, {"blocks": PRIORITY["blocks"]})) == [
        10, 13, 14, 15, 18, 11, 12, 16, 17, 2, 3, 1]


def test_e979_the_priority_rides_only_the_build_feed() -> None:
    """Outside a build the id cursor's statement runs, priority or not."""
    db = _two_blocks()
    assert _entered(_two_work(db, PRIORITY, bootstrap=False), limit=10) == [1, 2]
    assert S.RT_SCOPE_BUILD_ENTRANTS_SQL not in db.statements


def test_e979_parse_build_priority_reads_the_operators_row() -> None:
    want = ([f"obec:{LIBEREC_LIKE}"], ["byt/pronajem", "dum/prodej", "byt/prodej"])
    assert parse_build_priority(PRIORITY) == want
    assert parse_build_priority(json.dumps(PRIORITY)) == want, "a row stored as a JSON string"
    assert parse_build_priority({"blocks": ["obec:563889", "cast_obce:490245", "okres:3202"]}) \
        == (["obec:563889", "cast_obce:490245", "okres:3202"], [])
    assert parse_build_priority({"blocks": ["obec:0563889"]}) == (["obec:563889"], [])
    assert parse_build_priority({"blocks": ["obec:1", "obec:1"],
                                 "categories": [["byt", "prodej"], ["byt", "prodej"]]}) \
        == (["obec:1"], ["byt/prodej"]), "repeats collapse to their first place"


@pytest.mark.parametrize("value", [
    None, "", "not json", "[]", 42, True, [], ["obec:563889"], {},
    {"categories": [["byt", "prodej"]]},
    {"blocks": "obec:563889"},
    {"blocks": []},
    {"blocks": [563889]},
    {"blocks": ["563889"]},
    {"blocks": ["Liberec"]},
    {"blocks": ["obec:563889"], "categories": "byt/prodej"},
    {"blocks": ["obec:563889"], "categories": [["byt"]]},
    {"blocks": ["obec:563889"], "categories": [["byt", "prodej", "x"]]},
    {"blocks": ["obec:563889"], "categories": ["byt/prodej"]},
    {"blocks": ["obec:563889"], "categories": [["byt", None]]},
    {"blocks": ["obec:563889"], "categories": [["byt/x", "prodej"]]},
])
def test_e979_a_malformed_priority_is_no_priority(value: Any,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=incremental_lane.LOG.name):
        assert parse_build_priority(value) == ([], [])
    if value is not None:
        assert "rt_build_priority ignored" in caplog.text


def _spy_build_claims(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], list[tuple]]]:
    """Every build-feed claim's parameters and the rows it returned."""
    seen: list[tuple[dict[str, Any], list[tuple]]] = []
    rows = incremental_lane._rows

    def spy(conn: Any, sql: str, params: Any = None) -> list[tuple]:
        out = rows(conn, sql, params)
        if sql == S.RT_SCOPE_BUILD_ENTRANTS_SQL:
            seen.append((dict(params), list(out)))
        return out

    monkeypatch.setattr(incremental_lane, "_rows", spy)
    return seen


def test_e979_the_setting_reaches_the_build_claim_end_to_end(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The live path: the row in `autodedup.settings` is read by the pass, handed to the claim's
    statement, and the pass reads the named category of the widened block before lower ids."""
    conn = _built_town(tmp_path)
    for listing_id in list(NEW_IDS)[3:]:
        conn.listings[listing_id]["category_main"] = "dum"
    _seed(conn, tmp_path, f"obec:{TOWN} obec:{OTHER_TOWN}")
    assert conn.settings[bootstrap_key(GEN)] is True
    conn.settings[pass_rate_key(GEN)] = 3.5 / 787.5          # three adverts a build pass
    conn.settings[build_priority_key(GEN)] = {"blocks": [f"obec:{OTHER_TOWN}"],
                                              "categories": [["dum", "prodej"]]}
    seen = _spy_build_claims(monkeypatch)

    out = run_incremental(lambda: conn)

    assert not out["aborted"] and out["feeds"].get("entered") == 3
    assert len(seen) == 1
    params, claimed = seen[0]
    assert params["priority_blocks"] == [f"obec:{OTHER_TOWN}"]
    assert params["priority_cats"] == ["dum/prodej"]
    assert [int(row[0]) for row in claimed] == list(NEW_IDS)[3:], (
        "the houses, although the flats' ids are lower")


def test_e979_without_the_row_the_claim_gets_empty_arrays(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _built_town(tmp_path)
    _seed(conn, tmp_path, f"obec:{TOWN} obec:{OTHER_TOWN}")
    conn.settings[pass_rate_key(GEN)] = 3.5 / 787.5
    seen = _spy_build_claims(monkeypatch)

    out = run_incremental(lambda: conn)

    assert not out["aborted"] and seen
    assert all(p["priority_blocks"] == [] and p["priority_cats"] == [] for p, _rows in seen)
    assert [int(row[0]) for row in seen[0][1]] == list(NEW_IDS)[:3], "E976's id order"

"""C4c: a candidate only an area exemption admits never displaces a base candidate.

C4b lets retrieval pass every pair whose areas sit 8-20 % apart (the decision settles it on the
two galleries). On the 2026-11-01 replay of eight towns those candidates, filling the 60-a-listing
cap in posting order, pushed 147 merge-zone pairs out of repost series (146 read SAME); with the
sub-quota the replay kept 182 gains and lost 3, all three to the realitymix basis exemption, which
widens retrieval the same way. So a candidate that passes the veto only through an exemption (the
basis exemption or the C4b window) is held aside and seated after the fill, only in the slots no
base candidate (one the plain 8 % bar passes) took, and at most `exemption_window_quota` of them.
The cohort pass (`BlockIndex.candidates`) and the lane (`incremental.retrieve`,
`retrieve_posted`) seat the same.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

import pytest

from autodedup import guards, harness, incremental
from autodedup.blocking import BlockIndex
from autodedup.dataset import Dataset, Meta
from autodedup.guards import (
    EXEMPTION_WINDOW_CAP_SHARE,
    exemption_window_quota,
    in_exemption_window,
    pair_veto,
)
from autodedup.incremental import GuardRow, Keyer, _fill, guard_row, retrieve, retrieve_posted
from autodedup.incremental_store import MemoryStore
from autodedup.settings import Settings
from tests.autodedup.test_incremental import _listing

SUBJECT = 1
WINDOW_FIRST = 2
BASE_FIRST = 100
DIAL = {"byt": 0.20}
BASIS = {"realitymix": 0.15}
FLOOR = {"source": "realitymix", "attrs": {"area_basis": "floor"}}


def cfg(cap: int, dial: dict[str, float] = DIAL,
        basis: dict[str, float] | None = None) -> Settings:
    return replace(Settings(), area_veto_gallery_exempt=dict(dial),
                   area_veto_basis_floor_exempt=dict(basis or {}),
                   max_candidates_per_listing=cap)


def world(base: int, window: int, realitymix: int = 0) -> Dataset:
    """One 60 m2 flat at one address with `base` 60 m2 flats (ids from 100), `window` 70 m2
    ones (14 % apart, the C4b window; ids from 2) and after them `realitymix` 68 m2 realitymix
    ones stating the floor area (12 % apart, the basis exemption): every window id posts before
    every base id, so a fill in posting order would seat the window first."""
    ids = ([(SUBJECT, 60.0, {})] + [(WINDOW_FIRST + n, 70.0, {}) for n in range(window)]
           + [(WINDOW_FIRST + window + n, 68.0, FLOOR) for n in range(realitymix)]
           + [(BASE_FIRST + n, 60.0, {}) for n in range(base)])
    listings = {listing_id: _listing(listing_id, area_m2=area, **extra)
                for listing_id, area, extra in ids}
    return Dataset(meta=Meta(), listings=listings, images_by_listing={})


def window_ids(count: int, first: int = WINDOW_FIRST) -> set[int]:
    return set(range(first, first + count))


def base_ids(count: int) -> set[int]:
    return set(range(BASE_FIRST, BASE_FIRST + count))


def seated(ds: Dataset, settings: Settings) -> dict[int, dict[int, set[str]]]:
    """Every listing's candidates, asserted equal across the cohort pass and both lane reads."""
    fps, calibration = harness.calibrated(ds, settings)
    index = BlockIndex(settings)
    for listing_id in sorted(fps):
        index.add(fps[listing_id])
    index.finalize()
    keyer = Keyer(settings, calibration)
    store = MemoryStore()
    for listing_id, fp in fps.items():
        store.put_listing(listing_id, incremental.FpRow(guard_row(fp), "", "", "", True),
                          sorted(set(keyer.index_keys(fp))))
    rows = {i: guard_row(fp) for i, fp in fps.items()}
    out: dict[int, dict[int, set[str]]] = {}
    for listing_id in sorted(fps):
        expected = index.candidates(fps[listing_id])
        assert retrieve(fps[listing_id], keyer, store, rows, settings, {}) == expected
        assert retrieve_posted(rows[listing_id], keyer.index_keys(fps[listing_id]), keyer,
                               store, rows, settings, {}) == expected
        out[listing_id] = expected
    return out


def test_the_world_s_two_classes_are_what_they_say() -> None:
    subject = GuardRow(SUBJECT, "byt", "prodej", 60.0, "2+kk", 3)
    wide = GuardRow(WINDOW_FIRST, "byt", "prodej", 70.0, "2+kk", 3)
    near = GuardRow(BASE_FIRST, "byt", "prodej", 60.0, "2+kk", 3)
    assert pair_veto(subject, wide, cfg(60)) is None
    assert in_exemption_window(subject, wide, cfg(60))
    assert pair_veto(subject, near, cfg(60)) is None
    assert not in_exemption_window(subject, near, cfg(60))
    assert pair_veto(subject, wide, cfg(60, {})) == "area"
    assert not in_exemption_window(subject, wide, cfg(60, {}))


def test_a_realitymix_floor_area_twelve_percent_apart_is_a_window_candidate() -> None:
    subject = GuardRow(SUBJECT, "byt", "prodej", 60.0, "2+kk", 3, "sreality", None)
    floor = GuardRow(WINDOW_FIRST, "byt", "prodej", 68.0, "2+kk", 3, "realitymix", "floor")
    near = GuardRow(BASE_FIRST, "byt", "prodej", 60.0, "2+kk", 3, "realitymix", "floor")
    on = cfg(60, {}, BASIS)
    assert pair_veto(subject, floor, on) is None and in_exemption_window(subject, floor, on)
    assert in_exemption_window(floor, subject, on)
    assert pair_veto(subject, near, on) is None and not in_exemption_window(subject, near, on)
    assert pair_veto(subject, floor, cfg(60, {})) == "area"
    assert not in_exemption_window(subject, floor, cfg(60, {}))


def test_the_share_is_half_the_cap() -> None:
    assert EXEMPTION_WINDOW_CAP_SHARE == 0.5
    assert exemption_window_quota(60) == 30


def test_a_cap_full_of_base_candidates_seats_no_window_candidate() -> None:
    found = seated(world(base=6, window=4), cfg(4))[SUBJECT]
    assert set(found) == base_ids(4)


def test_a_few_base_candidates_leave_the_rest_of_the_cap_to_the_window() -> None:
    found = seated(world(base=3, window=3), cfg(4))[SUBJECT]
    assert set(found) == base_ids(3) | window_ids(1)
    found = seated(world(base=3, window=3), cfg(10))[SUBJECT]
    assert set(found) == base_ids(3) | window_ids(3)


def test_the_window_reaches_by_every_probe_that_meets_it() -> None:
    found = seated(world(base=1, window=1), cfg(10))[SUBJECT]
    assert {"addr", "text", "attr_dispo"} <= found[WINDOW_FIRST]
    assert {"addr", "text", "attr_dispo"} <= found[BASE_FIRST]


def test_the_share_bounds_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    found = seated(world(base=0, window=8), cfg(10))[SUBJECT]
    assert set(found) == window_ids(exemption_window_quota(10)) == window_ids(5)
    monkeypatch.setattr(guards, "EXEMPTION_WINDOW_CAP_SHARE", 0.2)
    found = seated(world(base=1, window=8), cfg(10))[SUBJECT]
    assert set(found) == base_ids(1) | window_ids(2)


def test_with_the_dial_empty_the_window_never_opens() -> None:
    off = cfg(4, {})
    found = seated(world(base=2, window=4), off)
    assert set(found[SUBJECT]) == base_ids(2)
    fps, _calibration = harness.calibrated(world(base=2, window=4), off)
    index = BlockIndex(off)
    for listing_id in sorted(fps):
        index.add(fps[listing_id])
    index.finalize()
    index.candidates(fps[SUBJECT])
    assert index.veto_counts == {"area": 4}


@pytest.mark.parametrize("cap", [2, 4, 60])
def test_without_a_window_pair_the_dial_retrieves_as_the_eight_percent_bar(cap: int) -> None:
    ds = world(base=6, window=0)
    assert seated(ds, cfg(cap)) == seated(ds, cfg(cap, {}))
    assert seated(ds, cfg(cap, DIAL, BASIS)) == seated(ds, cfg(cap, {}))


# ------------------------------------------------- the realitymix basis exemption


def test_a_realitymix_window_candidate_cannot_displace_a_base_candidate() -> None:
    found = seated(world(base=6, window=0, realitymix=4), cfg(4, {}, BASIS))[SUBJECT]
    assert set(found) == base_ids(4)


def test_a_realitymix_window_candidate_takes_the_leftover_slots() -> None:
    found = seated(world(base=3, window=0, realitymix=3), cfg(4, {}, BASIS))[SUBJECT]
    assert set(found) == base_ids(3) | window_ids(1)
    found = seated(world(base=3, window=0, realitymix=3), cfg(10, {}, BASIS))[SUBJECT]
    assert set(found) == base_ids(3) | window_ids(3)


def test_both_exemptions_share_one_quota_in_fill_order() -> None:
    found = seated(world(base=0, window=4, realitymix=4), cfg(10, DIAL, BASIS))[SUBJECT]
    assert set(found) == window_ids(4) | window_ids(1, WINDOW_FIRST + 4)


def test_with_the_basis_dial_empty_the_realitymix_gap_is_vetoed() -> None:
    off = cfg(4, {})
    found = seated(world(base=2, window=0, realitymix=4), off)
    assert set(found[SUBJECT]) == base_ids(2)


# ------------------------------------------------------- a loose phash probe (E960)


class _Index:
    def __init__(self, probes: Sequence[str]) -> None:
        self.probes = tuple(probes)


class _Keyer:
    def __init__(self, probes: Sequence[str]) -> None:
        self.index = _Index(probes)

    def is_exploded(self, probe: str, token: str) -> bool:
        return False


class _Store:
    def __init__(self, postings: dict[tuple[str, str], list[int]]) -> None:
        self.postings = postings

    def lookup_many(self, keys: Sequence[tuple[str, str]]) -> dict[tuple[str, str], list[int]]:
        return {key: list(self.postings[key]) for key in keys if key in self.postings}


def row(listing_id: int, area: float) -> GuardRow:
    return GuardRow(listing_id, "byt", "prodej", area, "2+kk", 3)


def fill(keys: list[tuple[str, str]], postings: dict[tuple[str, str], list[int]],
         areas: dict[int, float], cap: int, loose: str | None) -> Any:
    rows = {SUBJECT: row(SUBJECT, 60.0), **{i: row(i, area) for i, area in areas.items()}}
    probes = list(dict.fromkeys(probe for probe, _token in keys))
    found = _fill(SUBJECT, rows[SUBJECT], keys, _Keyer(probes), _Store(postings), rows,
                  cfg(cap), {}, loose=loose)
    return None if found is None else set(found)


PHASH = {("phash", "a"): [201, 202], ("phash", "b"): [301, 302]}
WIDE = {201: 70.0, 202: 70.0, 301: 70.0, 302: 70.0}
A_FIRST = [("phash", "a"), ("phash", "b")]
B_FIRST = [("phash", "b"), ("phash", "a")]


def test_window_seats_that_end_inside_a_loose_probe_read_the_listing() -> None:
    assert fill(A_FIRST, PHASH, WIDE, 2, None) == {201}
    assert fill(B_FIRST, PHASH, WIDE, 2, None) == {301}
    assert fill(A_FIRST, PHASH, WIDE, 2, "phash") is None
    assert fill(B_FIRST, PHASH, WIDE, 2, "phash") is None


def test_window_seats_that_hold_the_whole_loose_probe_are_order_free() -> None:
    for keys in (A_FIRST, B_FIRST):
        assert fill(keys, PHASH, WIDE, 10, "phash") == {201, 202, 301, 302}


def test_window_seats_spent_before_a_loose_probe_are_order_free() -> None:
    postings = {("addr", "x"): [50], **PHASH}
    for keys in (A_FIRST, B_FIRST):
        assert fill([("addr", "x"), *keys], postings, {50: 70.0, **WIDE}, 2, "phash") == {50}


def test_a_window_candidate_the_full_cap_turns_away_is_order_free() -> None:
    postings = {("phash", "a"): [201], ("phash", "b"): [301]}
    areas = {201: 60.0, 301: 70.0}
    for keys in (A_FIRST, B_FIRST):
        assert fill(keys, postings, areas, 1, "phash") == {201}

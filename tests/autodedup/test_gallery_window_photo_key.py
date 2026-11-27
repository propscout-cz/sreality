"""E1000: a candidate only the gallery exemption admits is seated only when a photo-hash key
also reaches it (`gallery_window_photo_key`).

Under w34 `area_veto_gallery_exempt` let through retrieval every pair of one category whose
areas sit 8-20 % apart; the real test (one price, one gallery) ran at decision time. On nine
blocks that was 288,275 more decided pairs than w31 for 858 more merge pairs, 281,799 of them
`guard:area` vetoes, and it stalled the switch of 2026-11-25. One gallery implies a shared
photo-hash key, which retrieval already holds. The basis exemption is not gated.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from autodedup.dataset import Dataset, Image, Meta
from autodedup import harness
from autodedup.blocking import BlockIndex, probe_priority
from autodedup.guards import (
    PHOTO_PROBE,
    gallery_window_only,
    in_exemption_window,
    photo_key_still_possible,
)
from autodedup.incremental import GuardRow
from autodedup.incremental_lane import SETTINGS_KEYS_SINCE_W31
from autodedup.settings import Settings
from tests.autodedup.test_c4c_window_sub_quota import (
    BASE_FIRST,
    BASIS,
    DIAL,
    SUBJECT,
    WINDOW_FIRST,
    base_ids,
    cfg,
    seated,
    window_ids,
    world,
)
from tests.autodedup.test_incremental import _listing

ROOT = Path(__file__).resolve().parents[2]
GALLERY = [0x1234_5678_9ABC_0001 + 7919 * k for k in range(4)]


def gated(cap: int, basis: dict[str, float] | None = None) -> Settings:
    return replace(cfg(cap, DIAL, basis), gallery_window_photo_key=True)


def with_gallery(ds: Dataset, *ids: int) -> Dataset:
    """`ds` with one gallery of four photographs on each of `ids`."""
    for listing_id in ids:
        ds.images_by_listing[listing_id] = [
            Image(listing_id=listing_id, image_id=listing_id * 10 + seq, seq=seq, phash=phash,
                  pop=1) for seq, phash in enumerate(GALLERY)]
    return ds


def test_w36_is_w34_with_the_dial_and_no_other_row_turns_it_on() -> None:
    rows = {path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "autodedup/settings").glob("*.json")}
    w34, w36 = rows["w34"], rows["w36"]
    assert {k for k in set(w34) | set(w36) if w34.get(k) != w36.get(k)} == {
        "gallery_window_photo_key"}
    assert w36["gallery_window_photo_key"] is True
    assert [name for name, row in rows.items() if row.get("gallery_window_photo_key")] == ["w36"]
    assert Settings().gallery_window_photo_key is False
    assert "gallery_window_photo_key" in SETTINGS_KEYS_SINCE_W31


def test_only_the_gallery_limb_is_gated() -> None:
    subject = GuardRow(SUBJECT, "byt", "prodej", 60.0, "2+kk", 3, "sreality", None)
    wide = GuardRow(WINDOW_FIRST, "byt", "prodej", 70.0, "2+kk", 3, "idnes", None)
    floor = GuardRow(WINDOW_FIRST + 1, "byt", "prodej", 68.0, "2+kk", 3, "realitymix", "floor")
    on, off = gated(60, BASIS), cfg(60, DIAL, BASIS)
    assert in_exemption_window(subject, wide, on) and in_exemption_window(subject, floor, on)
    assert gallery_window_only(subject, wide, on) and gallery_window_only(wide, subject, on)
    assert not gallery_window_only(subject, floor, on), "the basis exemption is not gated"
    assert not gallery_window_only(subject, wide, off), "off: w34"


def test_off_the_window_seats_as_w34_does() -> None:
    ds = world(base=3, window=4)
    found = seated(ds, cfg(60))
    assert set(found[SUBJECT]) == base_ids(3) | window_ids(4)


def test_a_window_candidate_no_photo_key_reaches_is_not_seated() -> None:
    """No advert of this world holds a photograph: under the dial the window is empty, the
    base candidates are untouched, and the cohort pass and both lane reads agree."""
    ds = world(base=3, window=4)
    found = seated(ds, gated(60))
    assert set(found[SUBJECT]) == base_ids(3)
    for other in window_ids(4):
        assert SUBJECT not in found[other]


def test_a_window_candidate_sharing_a_photo_key_is_seated() -> None:
    ds = with_gallery(world(base=3, window=4), SUBJECT, WINDOW_FIRST, WINDOW_FIRST + 1)
    found = seated(ds, gated(60))
    assert set(found[SUBJECT]) == base_ids(3) | {WINDOW_FIRST, WINDOW_FIRST + 1}
    assert PHOTO_PROBE in found[SUBJECT][WINDOW_FIRST]
    assert SUBJECT in found[WINDOW_FIRST] and SUBJECT not in found[WINDOW_FIRST + 2]


def test_a_basis_exempt_candidate_is_seated_without_a_photo_key() -> None:
    ds = world(base=2, window=2, realitymix=2)
    found = seated(ds, gated(60, BASIS))
    realitymix = window_ids(2, first=WINDOW_FIRST + 2)
    assert set(found[SUBJECT]) == base_ids(2) | realitymix, (
        "the realitymix floor-area candidates stay; the gallery ones wait for a photo key")


def test_candidates_no_photo_key_can_reach_take_no_place_in_the_quota() -> None:
    """Three seats (half the cap of six) and six window candidates in posting order, the two
    photo-keyed ones LAST: the others are first met after the photo probe, so they take no
    place in the quota and the two are seated. (Filtered only at the seating, the first three
    would have filled the quota and turned the photo-keyed ones away.)"""
    ds = with_gallery(world(base=1, window=6), SUBJECT, WINDOW_FIRST + 4, WINDOW_FIRST + 5)
    settings = gated(6)
    fps, _calibration = harness.calibrated(ds, settings)
    index = BlockIndex(settings)
    for listing_id in sorted(fps):
        index.add(fps[listing_id])
    index.finalize()
    assert set(index.candidates(fps[SUBJECT])) == base_ids(1) | {WINDOW_FIRST + 4,
                                                               WINDOW_FIRST + 5}
    found = seated(ds, settings)
    assert set(found[SUBJECT]) == base_ids(1) | {WINDOW_FIRST + 4, WINDOW_FIRST + 5}


def test_the_photo_probe_fills_before_the_probes_that_bring_the_window() -> None:
    """What the rule above rests on: every probe after the photo probe is one a photo key can
    no longer follow, and only the address probe fills before it."""
    for settings in (Settings(), replace(Settings(), attr_probe_town_grain=True)):
        probes = list(probe_priority(settings))
        assert probes.index(PHOTO_PROBE) == 1 and probes[0] == "addr"
        assert [photo_key_still_possible(probe, probes) for probe in probes] == (
            [True, True] + [False] * (len(probes) - 2))


def test_a_gallery_pair_inside_the_plain_bar_is_no_window_pair() -> None:
    """The dial reads only pairs past `area_reject_pct`: two 60 m2 flats are base candidates
    whatever their photographs."""
    listings = {SUBJECT: _listing(SUBJECT, area_m2=60.0),
                BASE_FIRST: _listing(BASE_FIRST, area_m2=62.0)}
    found = seated(Dataset(meta=Meta(), listings=listings, images_by_listing={}), gated(60))
    assert set(found[SUBJECT]) == {BASE_FIRST}

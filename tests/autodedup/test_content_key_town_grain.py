"""B: the text and phash posting keys keyed per town (`content_key_town_grain`).

Over the live scope (344k ads) a location-free content key fills with the whole scope's ads and
explodes, and an exploded key retrieves nothing: 95 of the 119 operator-SAME pairs in reach were
lost that way (research experiments/absent_attribution.md section 4). Keyed per town, 30 of them
fall back under `max_block_size`. Off, every key is w31's to the byte.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from autodedup import harness
from autodedup.blocking import BlockIndex, content_town, posting_keys
from autodedup.dataset import Dataset, Image, Listing, Location
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.incremental import Keyer, band_of, guard_row, key_token, retrieve, retrieve_posted
from autodedup.settings import Settings
from tests.autodedup.test_incremental import _dataset, _drain, arrival_order

ROOT = Path(__file__).resolve().parents[2]
W31 = Settings.from_json(ROOT / "autodedup/settings/w31.json")
W34 = Settings.from_json(ROOT / "autodedup/settings/w34.json")
TOWN = replace(W31, content_key_town_grain=True)
CONTENT = ("text", "phash")
PRAHA, TURNOV = 554782, 577626
BODY = ("Prodej bytu 3+kk v Turnove s vyhledem do zahrady, plocha 68 m2, 4. patro, "
        "cena 5 990 000 Kc, sklep, balkon, vytah, nizke naklady. ") * 4


def fp(listing_id: int, settings: Settings, *, obec: int | None = None,
       cast: int | None = None, phashes: tuple[int, ...] = (0x1F2E3D4C5B6A7980,),
       **kwargs: Any) -> Fingerprint:
    kwargs.setdefault("category_main", "byt")
    kwargs.setdefault("category_type", "prodej")
    kwargs.setdefault("disposition", "3+kk")
    kwargs.setdefault("area_m2", 68.0)
    kwargs.setdefault("description", BODY)
    listing = Listing(id=listing_id, block="b",
                      location=Location(obec_kod=obec, cast_obce_kod=cast), **kwargs)
    images = [Image(listing_id=listing_id, image_id=listing_id * 10 + seq, seq=seq,
                    phash=phash, pop=1) for seq, phash in enumerate(phashes)]
    return build_fingerprint(listing, images, settings)


def content(keys: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [(probe, key) for probe, key in keys if probe in CONTENT]


def test_w34_turns_the_dial_on_and_w31_leaves_it_off() -> None:
    assert W31.content_key_town_grain is False and Settings().content_key_town_grain is False
    assert W34.content_key_town_grain is True
    assert {k: v for k, v in W34.to_dict().items() if W31.to_dict()[k] != v} == {
        "area_reject_pct_by_category": {"byt": 0.2, "dum": 0.2, "pozemek": 0.2},
        "area_veto_basis_floor_exempt": {"realitymix": 0.15},
        "content_key_town_grain": True}


def test_dial_off_the_content_keys_are_the_bare_bands() -> None:
    advert = fp(1, W31, obec=TURNOV)
    keys = posting_keys(advert, W31, None)
    assert content(keys) == (
        [("phash", (band, value)) for band, value, _phash in advert.anchor_bands]
        + [("text", (band, value)) for band, value in advert.desc_bands])
    assert len(content(keys)) == 8
    assert content_town(advert, W31) == ()


def test_dial_on_prefixes_the_content_keys_with_the_town_and_nothing_else() -> None:
    off = fp(1, W31, obec=TURNOV, broker_key="b1")
    on = fp(1, TOWN, obec=TURNOV, broker_key="b1")
    keys_off, keys_on = posting_keys(off, W31, 3), posting_keys(on, TOWN, 3)
    assert content(keys_on) == [(probe, (TURNOV, *key)) for probe, key in content(keys_off)]
    assert ([k for k in keys_on if k[0] not in CONTENT]
            == [k for k in keys_off if k[0] not in CONTENT])


def test_a_quarter_of_a_split_city_is_keyed_by_its_town() -> None:
    """An unknown-quarter Praha ad still meets every quarter on content, as on the town probe."""
    known = fp(1, TOWN, obec=PRAHA, cast=490245)
    unknown = fp(2, TOWN, obec=PRAHA)
    assert content(posting_keys(known, TOWN, None)) == content(posting_keys(unknown, TOWN, None))
    assert all(key[0] == PRAHA for _probe, key in content(posting_keys(known, TOWN, None)))


def test_an_ad_with_no_town_keeps_the_bare_content_key() -> None:
    homeless = fp(1, TOWN)
    assert content_town(homeless, TOWN) == ()
    assert content(posting_keys(homeless, TOWN, None)) == content(
        posting_keys(fp(1, W31), W31, None))


def test_the_band_number_reads_the_same_from_either_shape() -> None:
    assert band_of(key_token((3, 18237))) == 3
    assert band_of(key_token((PRAHA, 3, 18237))) == 3


def bare(listing_id: int, settings: Settings, obec: int) -> Fingerprint:
    """A flat that states no area and no disposition: its only keys are its body and photo."""
    return fp(listing_id, settings, obec=obec, area_m2=None, disposition=None)


def _town_block(settings: Settings) -> BlockIndex:
    """Six flats in each of two towns, one body and one photograph: every content key holds
    twelve postings scope-wide and six per town, against a limit of eight."""
    index = BlockIndex(replace(settings, max_block_size=8))
    for listing_id in range(1, 13):
        index.add(bare(listing_id, settings, TURNOV if listing_id <= 6 else PRAHA))
    return index.finalize()


def test_town_keys_stop_a_shared_body_and_photo_exploding() -> None:
    off, on = _town_block(W31), _town_block(TOWN)
    assert {probe: len(off.exploded[probe]) for probe in CONTENT} == {"text": 4, "phash": 4}
    assert {probe: len(on.exploded[probe]) for probe in CONTENT} == {"text": 0, "phash": 0}
    assert off.candidates(off.fingerprints[1]) == {}
    found = on.candidates(on.fingerprints[1])
    assert set(found) == {2, 3, 4, 5, 6}
    assert all(probes == {"phash", "text"} for probes in found.values())


def test_a_content_match_across_two_towns_is_not_retrieved_under_the_dial() -> None:
    for settings, expected in ((W31, {2: {"phash", "text"}}), (TOWN, {})):
        index = BlockIndex(settings)
        index.add(bare(1, settings, TURNOV))
        index.add(bare(2, settings, PRAHA))
        index.finalize()
        assert index.candidates(index.fingerprints[1]) == expected, settings is TOWN


def _two_towns() -> Dataset:
    """`test_incremental._dataset` with every other flat moved to Turnov: one body and four
    photographs shared across the two towns, a broker key that still crosses them."""
    ds = _dataset(24)
    for step, listing_id in enumerate(sorted(ds.listings)):
        if step % 2:
            ds.listings[listing_id].location = Location(obec_kod=TURNOV, street_key="nadrazni",
                                                        house_number_cp=str(step))
    return ds


def test_the_lane_retrieves_what_the_cohort_pass_does_under_the_dial() -> None:
    ds, settings = _two_towns(), replace(Settings(), content_key_town_grain=True)
    fps, calibration = harness.calibrated(ds, settings)
    store, _passes = _drain(ds, settings, calibration, arrival_order(ds))
    index = BlockIndex(settings)
    for listing_id in sorted(fps):
        index.add(fps[listing_id])
    index.finalize()
    keyer = Keyer(settings, calibration)
    guards = {i: guard_row(advert) for i, advert in fps.items()}
    towns = {i: advert.obec_kod for i, advert in fps.items()}
    crossing = 0
    for listing_id in sorted(fps):
        expected = index.candidates(fps[listing_id])
        assert retrieve(fps[listing_id], keyer, store, guards, settings, {}) == expected
        posted = keyer.index_keys(fps[listing_id])
        truth, read = keyer.probe_keys(fps[listing_id]), keyer.probe_keys_of(posted)
        assert [k for k in read if k[0] == "text"] == [k for k in truth if k[0] == "text"]
        assert (sorted(k for k in read if k[0] == "phash")
                == sorted(set(k for k in truth if k[0] == "phash")))
        assert retrieve_posted(guards[listing_id], posted, keyer, store, guards, settings,
                               {}) == expected
        for other, probes in expected.items():
            if towns[other] != towns[listing_id]:
                crossing += 1
                assert not probes & set(CONTENT), (listing_id, other)
    assert crossing, "the broker key still crosses the towns"

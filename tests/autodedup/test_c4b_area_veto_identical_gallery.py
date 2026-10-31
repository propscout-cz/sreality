"""C4b: the pair veto's area bar rises from 8 % to 20 % only for two adverts of one gallery (w34).

On the operator's cleaned truth (2026-10-31) the plain 8 -> 20 % raise (C4) releases 144 SAME
against 118 DIFFERENT pairs, while the identical-gallery shape releases 68 SAME and no DIFFERENT
pair (research experiments/cleaned_truth_remeasure.md, levers 1b and 4). Retrieval reads only
the guard columns, so a gap inside the bar passes there and the decision, which holds both
galleries and both prices, vetoes it unless the photographs prove one unit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autodedup import census, settings as settings_module
from autodedup.dataset import Image, Listing, Location
from autodedup.decide import Decision, decide_pair
from autodedup.features import ABSENT, FEATURE_ORDER
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.guards import (
    GALLERY_EXEMPT_MIN_AREA_M2,
    GALLERY_EXEMPT_MIN_COVERAGE,
    GALLERY_EXEMPT_MIN_PHOTOS,
    GALLERY_EXEMPT_PRICE_TOL,
    gallery_coverage,
    identical_gallery,
    pair_veto,
)
from autodedup.incremental import GuardRow
from autodedup.settings import Settings
from autodedup.store_score import storable

SETTINGS = Path(__file__).resolve().parents[2] / "autodedup/settings"
W31 = Settings.from_json(SETTINGS / "w31.json")
W34 = Settings.from_json(SETTINGS / "w34.json")
PRICE = 5_000_000.0


def frame(seed: int) -> int:
    """A pHash every other `frame` sits at least 8 bits from."""
    return int.from_bytes(bytes([seed % 256]) * 8, "big")


GALLERY = [frame(seed) for seed in (11, 22, 33, 144, 155)]
OTHER = [frame(seed) for seed in (101, 102, 103, 104, 105)]


class FixedModel:
    def predict_proba(self, feats: Any) -> float:
        return 0.5


def fp(listing_id: int, settings: Settings = W34, *, category: str | None = "byt",
       area: float | None = 100.0, price: float | None = PRICE,
       frames: list[int] = GALLERY) -> Fingerprint:
    listing = Listing(id=listing_id, block="b", location=Location(), category_type="prodej",
                      category_main=category, area_m2=area, price=price)
    images = [Image(listing_id=listing_id, image_id=listing_id * 100 + seq, seq=seq,
                    storage_path=f"{listing_id}/{seq}", phash=value, pop=1)
              for seq, value in enumerate(frames)]
    return build_fingerprint(listing, images, settings)


def decide(a: Fingerprint, b: Fingerprint, settings: Settings = W34) -> Decision:
    la = Listing(id=a.listing_id, block="b", location=Location(), category_main=a.category_main,
                 category_type=a.category_type, area_m2=a.area_m2, price=a.price)
    lb = Listing(id=b.listing_id, block="b", location=Location(), category_main=b.category_main,
                 category_type=b.category_type, area_m2=b.area_m2, price=b.price)
    feats = {name: ABSENT for name in FEATURE_ORDER}
    return decide_pair(a, b, la, lb, feats, {"phash"}, FixedModel(), settings)


def vetoed(a: Fingerprint, b: Fingerprint, settings: Settings = W34) -> str | None:
    """The decision's veto, asserted symmetric and recorded as every guard veto is."""
    decision, mirror = decide(a, b, settings), decide(b, a, settings)
    assert (decision.zone, decision.veto, decision.reason) == (
        mirror.zone, mirror.veto, mirror.reason)
    if decision.veto is None:
        assert decision.zone != "veto"
        return None
    assert (decision.zone, decision.reason) == ("veto", f"guard:{decision.veto}")
    return decision.veto


def test_w34_is_w31_plus_its_three_dials() -> None:
    w31 = json.loads((SETTINGS / "w31.json").read_text(encoding="utf-8"))
    row = json.loads((SETTINGS / "w34.json").read_text(encoding="utf-8"))
    assert row.pop("area_veto_gallery_exempt") == {"byt": 0.2, "dum": 0.2, "pozemek": 0.2}
    assert row.pop("area_veto_basis_floor_exempt") == {"realitymix": 0.15}
    assert row.pop("content_key_town_grain") is True
    assert row == w31
    assert W31.area_veto_gallery_exempt == {}
    assert W34.area_reject_pct == W31.area_reject_pct == 0.08
    assert W34.phash_tight == 6


def test_the_rule_s_thresholds_are_the_ruled_ones() -> None:
    assert (GALLERY_EXEMPT_MIN_COVERAGE, GALLERY_EXEMPT_MIN_PHOTOS, GALLERY_EXEMPT_PRICE_TOL,
            GALLERY_EXEMPT_MIN_AREA_M2) == (0.80, 3, 0.02, 20.0)


# ------------------------------------------------------------------ the decision (both galleries)


@pytest.mark.parametrize("cat", ["byt", "dum", "pozemek"])
def test_fifteen_percent_with_one_gallery_at_one_price_is_scored(cat: str) -> None:
    assert vetoed(fp(1, category=cat, area=85.0), fp(2, category=cat)) is None
    assert vetoed(fp(1, W31, category=cat, area=85.0), fp(2, W31, category=cat), W31) == "area"


def test_fifteen_percent_with_another_gallery_is_vetoed() -> None:
    assert vetoed(fp(1, area=85.0), fp(2, frames=OTHER)) == "area"
    assert vetoed(fp(1, area=85.0, frames=[]), fp(2, frames=[])) == "area"


def test_fifteen_percent_with_one_gallery_five_percent_apart_in_price_is_vetoed() -> None:
    assert vetoed(fp(1, area=85.0), fp(2, price=PRICE * 1.05)) == "area"
    assert vetoed(fp(1, area=85.0), fp(2, price=PRICE * 1.02)) is None, "2 % is inclusive"
    assert vetoed(fp(1, area=85.0, price=None), fp(2)) == "area"


def test_twenty_five_percent_with_one_gallery_is_vetoed() -> None:
    assert vetoed(fp(1, area=75.0), fp(2)) == "area"
    assert vetoed(fp(1, area=80.0), fp(2)) is None, "20 % is inclusive"


def test_a_komercni_pair_with_one_gallery_keeps_the_eight_percent_bar() -> None:
    assert vetoed(fp(1, category="komercni", area=85.0), fp(2, category="komercni")) == "area"
    assert vetoed(fp(1, category="komercni", area=93.0), fp(2, category="komercni")) is None


def test_a_side_under_twenty_square_metres_keeps_the_eight_percent_bar() -> None:
    assert vetoed(fp(1, area=15.0), fp(2, area=17.6)) == "area"
    assert vetoed(fp(1, area=20.0), fp(2, area=23.5)) is None


@pytest.mark.parametrize(("cat_a", "cat_b"), [
    ("byt", "komercni"), ("dum", "pozemek"), ("dum", None), ("ostatni", "ostatni")])
def test_a_mixed_or_unlisted_category_keeps_the_eight_percent_bar(
        cat_a: str, cat_b: str | None) -> None:
    assert vetoed(fp(1, category=cat_a, area=85.0), fp(2, category=cat_b)) == "area"


def test_the_veto_is_unstored_like_any_other_reject_below_the_floor() -> None:
    decision = decide(fp(1, area=85.0), fp(2, frames=OTHER))
    assert not storable({"zone": "reject", "score": decision.score,
                         "evidence": decision.evidence}, W34.store_floor)


# ------------------------------------------------------------------ retrieval (guard columns only)


@pytest.mark.parametrize("cat", ["byt", "dum", "pozemek", "komercni", "ostatni"])
@pytest.mark.parametrize("area", [100.0, 95.0, 92.0, 91.9, 85.0, 70.0])
def test_an_empty_dial_is_the_w31_veto_at_both_steps(cat: str, area: float) -> None:
    named = Settings.from_dict({**W31.to_dict(), "area_veto_gallery_exempt": {}})
    a, b = fp(1, named, category=cat, area=area), fp(2, named, category=cat)
    expected = "area" if area < 92.0 else None
    assert pair_veto(a, b, named) == pair_veto(a, b, W31) == expected
    assert pair_veto(a, b, named, lambda: True) == expected


def test_retrieval_lets_the_window_through_for_the_decision_to_settle() -> None:
    assert pair_veto(fp(1, area=85.0), fp(2, frames=OTHER), W34) is None
    assert pair_veto(fp(1, area=79.0), fp(2), W34) == "area"
    assert pair_veto(fp(1, category="komercni", area=85.0), fp(2, category="komercni"),
                     W34) == "area"
    assert pair_veto(fp(1, area=15.0), fp(2, area=17.6), W34) == "area"


def test_the_rt_lane_s_served_rows_read_the_same_floor() -> None:
    a = GuardRow(1, "byt", "prodej", 85.0, None, None)
    b = GuardRow(2, "byt", "prodej", 100.0, None, None)
    assert pair_veto(a, b, W31) == "area"
    assert pair_veto(a, b, W34) is None and pair_veto(b, a, W34) is None
    office = GuardRow(3, "komercni", "pronajem", 91.0, None, None)
    suite = GuardRow(4, "komercni", "pronajem", 100.0, None, None)
    assert pair_veto(office, suite, W34) == "area"
    small = GuardRow(5, "byt", "prodej", 17.0, None, None)
    smaller = GuardRow(6, "byt", "prodej", 19.9, None, None)
    assert pair_veto(small, smaller, W34) == "area"


def test_another_guard_still_vetoes_inside_the_window() -> None:
    a = GuardRow(1, "byt", "prodej", 85.0, "2+kk", None)
    b = GuardRow(2, "byt", "prodej", 100.0, "3+kk", None)
    assert pair_veto(a, b, W34) == "disposition"


# ------------------------------------------------------------------ one gallery


def test_coverage_is_the_smaller_gallery_s_share() -> None:
    assert gallery_coverage(set(GALLERY), set(GALLERY), 6) == 1.0
    assert gallery_coverage(set(GALLERY[:4]) | {OTHER[0]}, set(GALLERY), 6) == 0.8
    assert gallery_coverage(set(GALLERY[:3]) | set(OTHER[:2]), set(GALLERY), 6) == 0.6
    assert gallery_coverage(set(GALLERY), set(GALLERY) | set(OTHER), 6) == 1.0
    assert gallery_coverage(set(), set(GALLERY), 6) == 0.0


def test_a_twin_counts_within_phash_tight_bits_only() -> None:
    six = {value ^ 0b111111 for value in GALLERY}
    seven = {value ^ 0b1111111 for value in GALLERY}
    assert gallery_coverage(six, set(GALLERY), 6) == 1.0
    assert gallery_coverage(seven, set(GALLERY), 6) == 0.0


def test_signed_bigint_hashes_compare_as_64_bits() -> None:
    signed = [value - (1 << 64) if value >= 1 << 63 else value for value in GALLERY]
    assert any(value < 0 for value in signed)
    assert identical_gallery(fp(1, frames=signed), fp(2), W34)


def test_eighty_percent_of_the_smaller_gallery_is_enough() -> None:
    four_of_five = GALLERY[:4] + OTHER[:1]
    assert identical_gallery(fp(1, frames=four_of_five), fp(2), W34)
    assert vetoed(fp(1, area=85.0, frames=four_of_five), fp(2)) is None
    three_of_five = GALLERY[:3] + OTHER[:2]
    assert not identical_gallery(fp(1, frames=three_of_five), fp(2), W34)
    assert vetoed(fp(1, area=85.0, frames=three_of_five), fp(2)) == "area"
    # A short gallery inside a long one is covered whole.
    assert identical_gallery(fp(1, frames=GALLERY[:3]), fp(2, frames=GALLERY + OTHER), W34)


def test_each_gallery_holds_at_least_three_photos() -> None:
    assert not identical_gallery(fp(1, frames=GALLERY[:2]), fp(2, frames=GALLERY[:2]), W34)
    assert vetoed(fp(1, area=85.0, frames=GALLERY[:2]), fp(2, frames=GALLERY[:2])) == "area"
    assert identical_gallery(fp(1, frames=GALLERY[:3]), fp(2, frames=GALLERY[:3]), W34)


# ------------------------------------------------------------------ the dial


@pytest.mark.parametrize("dial", [{"dům": 0.2}, {"flat": 0.2}, {"byt": 0.08}, {"byt": 0.05},
                                  {"byt": 1.0}])
def test_the_dial_is_validated(dial: dict[str, float]) -> None:
    raw = json.loads((SETTINGS / "w31.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="area_veto_gallery_exempt"):
        Settings.from_dict({**raw, "area_veto_gallery_exempt": dial})


def test_the_dial_s_vocabulary_is_the_census_s() -> None:
    assert settings_module.CATEGORY_MAIN == census.CATEGORY_MAIN

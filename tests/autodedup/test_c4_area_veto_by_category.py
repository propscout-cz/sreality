"""C4: the pair veto's area bar per category, behind a dial that is empty by default.

The census of every pair the 8 % -> 20 % lift newly merges (reading2/RESULT.md: 322 of 326
same) put its only wrong merges on komerční suites of one office building. So the lift is read
per category_main: a pair takes its category's bar only where BOTH sides state that one
category and it has an entry; a mixed pair (byt x komerční, dům x pozemek) and any category
without an entry keep `area_reject_pct`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autodedup import census, settings as settings_module
from autodedup.dataset import Listing, Location
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.guards import area_veto_pct, pair_veto
from autodedup.incremental import GuardRow
from autodedup.settings import Settings

SETTINGS = Path(__file__).resolve().parents[2] / "autodedup/settings"
W31 = Settings.from_json(SETTINGS / "w31.json")
C4 = Settings.from_json(SETTINGS / "c4.json")
C4_FLATS = Settings.from_json(SETTINGS / "c4_flats.json")


def fp(listing_id: int, settings: Settings, **kwargs: Any) -> Fingerprint:
    listing = Listing(id=listing_id, block="b", location=Location(), category_type="prodej",
                      **kwargs)
    return build_fingerprint(listing, [], settings)


def pair(settings: Settings, cat_a: str, area_a: float, cat_b: str | None = None,
         area_b: float = 100.0) -> str | None:
    a = fp(1, settings, category_main=cat_a, area_m2=area_a)
    b = fp(2, settings, category_main=cat_b or cat_a, area_m2=area_b)
    return pair_veto(a, b, settings)


def test_c4_rows_are_w31_plus_the_dial() -> None:
    w31 = json.loads((SETTINGS / "w31.json").read_text(encoding="utf-8"))
    for name, dial in (("c4", {"byt": 0.2, "dum": 0.2, "pozemek": 0.2}),
                       ("c4_flats", {"byt": 0.2})):
        row = json.loads((SETTINGS / f"{name}.json").read_text(encoding="utf-8"))
        assert row.pop("area_reject_pct_by_category") == dial
        assert row == w31
    assert W31.area_reject_pct_by_category == {}
    assert C4.area_reject_pct == W31.area_reject_pct == 0.08


@pytest.mark.parametrize("cat", ["byt", "dum", "pozemek", "komercni", "ostatni"])
@pytest.mark.parametrize("area", [100.0, 95.0, 92.0, 91.9, 85.0, 70.0])
def test_an_empty_dial_is_the_w31_veto(cat: str, area: float) -> None:
    named = Settings.from_dict({**json.loads((SETTINGS / "w31.json").read_text(
        encoding="utf-8")), "area_reject_pct_by_category": {}})
    assert pair(named, cat, area) == pair(W31, cat, area)
    assert pair(W31, cat, area) == ("area" if area < 92.0 else None)


@pytest.mark.parametrize("cat", ["byt", "dum", "pozemek"])
def test_a_same_category_pair_at_fifteen_percent_passes_under_c4_only(cat: str) -> None:
    assert pair(W31, cat, 85.0) == "area"
    assert pair(C4, cat, 85.0) is None
    # 20 % is the new bar, not no bar.
    assert pair(C4, cat, 80.0) is None
    assert pair(C4, cat, 79.0) == "area"


def test_a_komercni_pair_at_fifteen_percent_is_vetoed_under_both() -> None:
    assert pair(W31, "komercni", 85.0) == "area"
    assert pair(C4, "komercni", 85.0) == "area"
    assert pair(C4, "komercni", 93.0) is None


@pytest.mark.parametrize(("cat_a", "cat_b"), [
    ("byt", "komercni"), ("dum", "komercni"), ("dum", "pozemek"), ("pozemek", "komercni")])
def test_a_mixed_category_pair_keeps_the_default_bar(cat_a: str, cat_b: str) -> None:
    for first, second in ((cat_a, cat_b), (cat_b, cat_a)):
        assert pair(C4, first, 85.0, second) == "area"
        assert pair(C4, first, 93.0, second) is None
        assert area_veto_pct(fp(1, C4, category_main=first), fp(2, C4, category_main=second),
                             C4) == 0.08


def test_an_unstated_category_keeps_the_default_bar() -> None:
    a = fp(1, C4, category_main="byt", area_m2=85.0)
    b = fp(2, C4, area_m2=100.0)
    assert pair_veto(a, b, C4) == "area"
    assert pair_veto(b, a, C4) == "area"


def test_the_flats_row_lifts_flats_only() -> None:
    assert pair(C4_FLATS, "byt", 85.0) is None
    for cat in ("dum", "pozemek", "komercni"):
        assert pair(C4_FLATS, cat, 85.0) == "area"


def test_the_rt_lane_s_served_rows_read_the_same_bar() -> None:
    # `incremental.retrieve` vetoes on `GuardRow`s from `listing_fp`, not fingerprints.
    a = GuardRow(1, "byt", "prodej", 85.0, None, None)
    b = GuardRow(2, "byt", "prodej", 100.0, None, None)
    assert pair_veto(a, b, W31) == "area"
    assert pair_veto(a, b, C4) is None
    office = GuardRow(3, "komercni", "pronajem", 91.0, None, None)
    suite = GuardRow(4, "komercni", "pronajem", 100.0, None, None)
    assert pair_veto(office, suite, C4) == "area"


@pytest.mark.parametrize("dial", [{"dům": 0.2}, {"flat": 0.2}, {"byt": 0.02}, {"byt": 1.0}])
def test_the_dial_is_validated(dial: dict[str, float]) -> None:
    raw = json.loads((SETTINGS / "w31.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="area_reject_pct_by_category"):
        Settings.from_dict({**raw, "area_reject_pct_by_category": dial})


def test_the_dial_s_vocabulary_is_the_census_s() -> None:
    assert settings_module.CATEGORY_MAIN == census.CATEGORY_MAIN

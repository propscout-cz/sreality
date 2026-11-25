"""K-R reads the advertiser's own offer number, `listings.ad_reference` (operator ruling 2026-11-23).

Measured on operator truth the engine did not merge: byt 297 same / 1 different, dum 131 / 0,
pozemek 79 / 0, komerční 38 / 13 (two offices of one building share a code). So the column
certifies only between two adverts of byt, dum or pozemek of one deal type, behind the same two
rails as a body-printed code (RARE, PURE), judged over every carrier of the code. The dial is
`Settings.ref_code_structured_categories`: empty in w31 and before, so nothing they decide moves.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from autodedup import dataset as ds
from autodedup import export
from autodedup.dataset import Image, Listing, Location
from autodedup.decide import certificate_of
from autodedup.features import ABSENT, FeatureContext, pair_features
from autodedup.fingerprint import build_fingerprint
from autodedup.incremental import (
    Calibration,
    FpRow,
    carried_codes,
    context_for,
    crowded_codes,
    decision_digest,
    guard_row,
)
from autodedup.incremental_store import MemoryStore
from autodedup.settings import Settings
from autodedup.text_facts import MAX_CODE_POPULATION, structured_code

SETTINGS = Path(__file__).resolve().parents[2] / "autodedup/settings"
W31 = Settings.from_json(SETTINGS / "w31.json")
W34 = Settings.from_json(SETTINGS / "w34.json")
GEN = "gtest"


def ad(listing_id: int, *, code: str | None = None, category: str | None = "byt",
       deal: str | None = "prodej", body: str | None = None, dispo: str | None = "2+kk",
       area: float | None = 61.0, source: str = "sreality") -> Listing:
    return Listing(id=listing_id, block="b", source=source, location=Location(),
                   category_main=category, category_type=deal, disposition=dispo, area_m2=area,
                   price=5_000_000.0, description=body, ad_reference=code)


def certificate(settings: Settings, a: Listing, b: Listing, *others: Listing) -> str | None:
    """The pair's certificate on BOTH paths — the batch context over the cohort, and the lane's
    context under a calibration cut from it — which must agree."""
    ads = {listing.id: listing for listing in (a, b, *others)}
    fps = {i: build_fingerprint(listing, [], settings) for i, listing in ads.items()}
    batch = FeatureContext.build(fps, settings, None)
    batch.index_reference_codes(ads)
    lane = context_for(Calibration.build(fps, ads, settings, GEN), settings, fps, ads)
    out = []
    for ctx in (batch, lane):
        feats = pair_features(fps[a.id], fps[b.id], a, b, [], [], ctx, settings)
        assert (feats["ref_code_shared"] != ABSENT) == bool(ctx.shared_codes(a, b))
        out.append(certificate_of(feats, a, b, settings))
    assert out[0] == out[1], "the batch and the lane read one code"
    return out[0]


# ------------------------------------------------------------------------- the one spelling


def test_idnes_prefix_and_the_bare_number_are_one_code() -> None:
    assert structured_code("IDNES-123456") == structured_code("123456") == "123456"
    assert structured_code("realitymix-N 115 423") == structured_code(" n115423 ") == "N115423"
    assert structured_code("ID 4521") == structured_code("idnes-4521") == "4521"
    assert certificate(W34, ad(1, code="IDNES-123456", source="idnes"), ad(2, code="123456")) == "K-R"


@pytest.mark.parametrize("value", [None, "", "   ", "7", "12", "123", "IDNES-123", "ID 12", "a-1"])
def test_an_empty_or_short_value_or_a_one_to_three_digit_number_is_no_code(
        value: str | None) -> None:
    assert structured_code(value) is None
    assert certificate(W34, ad(1, code=value), ad(2, code=value)) is None


def test_four_characters_are_a_code() -> None:
    assert structured_code("1166") == "1166" and structured_code("IDNES-1166") == "1166"


# ------------------------------------------------------------------------- the category and deal


@pytest.mark.parametrize("category", ["byt", "dum", "pozemek"])
def test_a_pair_sharing_only_the_structured_code_certifies_under_w34_and_not_under_w31(
        category: str) -> None:
    a, b = ad(1, code="IDNES-778899", category=category), ad(2, code="778899", category=category)
    assert certificate(W34, a, b) == "K-R"
    assert certificate(W31, a, b) is None
    assert certificate(Settings(), a, b) is None


@pytest.mark.parametrize("category", ["komercni", "ostatni", None])
def test_a_komercni_pair_sharing_only_the_structured_code_does_not_certify(
        category: str | None) -> None:
    assert certificate(W34, ad(1, code="778899", category=category, dispo=None),
                       ad(2, code="778899", category=category, dispo=None)) is None


def test_a_byt_and_a_komercni_advert_sharing_the_structured_code_do_not_certify() -> None:
    assert certificate(W34, ad(1, code="778899"), ad(2, code="778899", category="komercni")) is None


@pytest.mark.parametrize(("deal_a", "deal_b"), [("prodej", "pronajem"), ("prodej", None),
                                                 (None, None)])
def test_different_or_unknown_deal_types_do_not_certify(deal_a: str | None,
                                                        deal_b: str | None) -> None:
    assert certificate(W34, ad(1, code="778899", deal=deal_a),
                       ad(2, code="778899", deal=deal_b)) is None


# ------------------------------------------------------------------------- the two rails


def test_a_structured_code_carried_by_more_than_max_code_population_adverts_certifies_nothing(
        ) -> None:
    crowd = [ad(10 + i, code="778899") for i in range(MAX_CODE_POPULATION - 1)]
    assert certificate(W34, ad(1, code="778899"), ad(2, code="778899"), *crowd[:-1]) == "K-R"
    assert len(crowd) + 2 == MAX_CODE_POPULATION + 1
    assert certificate(W34, ad(1, code="778899"), ad(2, code="778899"), *crowd) is None


def test_a_carrier_the_column_is_not_read_for_still_counts_in_the_population() -> None:
    """A code one building's offices share is the building's code, whoever else carries it."""
    offices = [ad(10 + i, code="778899", category="komercni", dispo=None)
               for i in range(MAX_CODE_POPULATION - 1)]
    assert certificate(W34, ad(1, code="778899"), ad(2, code="778899"), *offices) is None


@pytest.mark.parametrize("other", [{"dispo": "3+1"}, {"area": 70.0}])
def test_a_structured_code_whose_carriers_disagree_on_disposition_or_area_certifies_nothing(
        other: dict) -> None:
    assert certificate(W34, ad(1, code="778899"), ad(2, code="778899"),
                       ad(3, code="778899", **other)) is None
    assert certificate(W34, ad(1, code="778899"), ad(2, code="778899"),
                       ad(3, code="778899", area=62.0)) == "K-R", "within CODE_AREA_TOL"


# ------------------------------------------------------------------------- body and column


def test_a_body_code_and_the_same_structured_code_are_one_code() -> None:
    printed = ad(1, body="Pěkný byt, evidenční číslo zakázky N115423.")
    assert certificate(W34, printed, ad(2, code="N115423")) == "K-R"
    assert certificate(W31, printed, ad(2, code="N115423")) is None


def test_a_body_code_keeps_its_verdict_whatever_the_column_carries() -> None:
    body = "Pěkný byt, evidenční číslo zakázky N115423."
    column = [ad(10 + i, code="N115423") for i in range(MAX_CODE_POPULATION)]
    a, b = ad(1, body=body), ad(2, body=body, category="komercni", dispo=None)
    for settings in (W31, W34):
        assert certificate(settings, a, b, *column) == "K-R", settings
    assert certificate(W34, a, column[0], b, *column[1:]) is None, "the union is a crowd"


# ------------------------------------------------------------------------- what moves


def _digest_before_the_column(listing: Listing, images: list[Image]) -> str:
    """`decision_digest` as it was spelled before the engine read `ad_reference`."""
    facts = asdict(listing)
    facts.pop("ad_reference")
    payload = json.dumps(
        [facts, sorted((img.image_id, img.seq, img.phash, img.pop, img.tags, img.clip_tagged_at)
                       for img in images)], sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def test_the_decision_digest_moves_only_for_an_advert_whose_code_is_read() -> None:
    images = [Image(listing_id=1, image_id=100, seq=0, phash=7, pop=1)]
    read = ad(1, code="IDNES-778899")
    unread = [read, replace(read, ad_reference=None), replace(read, ad_reference="123"),
              replace(read, category_main="komercni"), replace(read, ad_reference="   ")]
    for listing in unread[1:]:
        assert decision_digest(listing, images, W34) == _digest_before_the_column(listing, images)
    for listing in unread:
        assert decision_digest(listing, images, W31) == _digest_before_the_column(listing, images)
    assert decision_digest(read, images, W34) != _digest_before_the_column(read, images)
    assert (decision_digest(read, images, W34)
            == decision_digest(replace(read, ad_reference="778899"), images, W34)), "one code"


def test_a_calibration_cut_without_the_column_keeps_its_bytes_and_its_digest() -> None:
    a, b = ad(1, code="778899", body="evidenční číslo zakázky N115423"), ad(2, code="778899")
    fps = {i: build_fingerprint(x, [], W31) for i, x in ((1, a), (2, b))}
    cut = Calibration.build(fps, {1: a, 2: b}, W31, GEN)
    assert cut.certifying_codes == ["N115423"] and cut.certifying_structured_codes == []
    assert "certifying_structured_codes" not in cut.to_json()
    assert Calibration.from_json(cut.to_json()).digest() == cut.digest()
    under = Calibration.build(fps, {1: a, 2: b}, W34, GEN)
    assert under.certifying_structured_codes == ["778899", "N115423"]
    assert Calibration.from_json(under.to_json()).digest() == under.digest() != cut.digest()


def test_the_lane_reads_no_structured_code_under_a_calibration_cut_without_it() -> None:
    a, b = ad(1, code="778899"), ad(2, code="778899")
    ads = {1: a, 2: b}
    fps = {i: build_fingerprint(x, [], W34) for i, x in ads.items()}
    stale = context_for(Calibration.build(fps, ads, W31, GEN), W34, fps, ads)
    assert stale.shared_codes(a, b) == []
    fresh = context_for(Calibration.build(fps, ads, W34, GEN), W34, fps, ads)
    assert fresh.shared_codes(a, b) == ["778899"]


# ------------------------------------------------------------------------- the artifact


def test_the_export_record_carries_the_column_as_stored() -> None:
    record = export.build_listing_record({"id": 5, "ad_reference": "IDNES-1166"}, block="b",
                                         location=None)
    assert record["ad_reference"] == "IDNES-1166" and "ad_reference" not in record["attrs"]
    assert Listing.from_json(record).ad_reference == "IDNES-1166"


def test_a_cohort_file_without_the_key_still_loads(tmp_path: Path) -> None:
    record = export.build_listing_record({"id": 5, "description": "byt"}, block="b",
                                         location=None)
    record.pop("ad_reference")
    path = tmp_path / "old.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"t": "meta"}) + "\n" + json.dumps(record) + "\n")
    assert ds.load(path).listings[5].ad_reference is None


def test_settings_name_only_category_main_values() -> None:
    assert Settings.from_dict({"ref_code_structured_categories": ["byt"]}
                              ).ref_code_structured_categories == ("byt",)
    with pytest.raises(ValueError, match="ref_code_structured_categories"):
        Settings(ref_code_structured_categories=("flat",))


# ------------------------------------------ what is no code at all (#2056 review, finding 1)


@pytest.mark.parametrize("value", ["Novák", "SaleMaso", "IDNES-Beroun", "VK_VYS", "0012", "00003",
                                   "IDNES-00035", "00075-1", "REALITYMIX-0179"])
def test_a_name_or_a_zero_padded_short_number_is_no_code(value: str) -> None:
    assert structured_code(value) is None
    assert certificate(W34, ad(1, code=value), ad(2, code=value)) is None


@pytest.mark.parametrize(("value", "code"), [("01607", "01607"), ("N00930", "N00930"),
                                             ("IDNES-1166", "1166"), ("84015", "84015")])
def test_four_significant_characters_with_a_digit_are_a_code(value: str, code: str) -> None:
    assert structured_code(value) == code


# ------------------------------- a code that became a crowd AFTER the cut (finding 2)


def _lane_certificate(store: MemoryStore, calibration: Calibration, a: Listing, b: Listing,
                      *others: Listing) -> str | None:
    ads = {listing.id: listing for listing in (a, b, *others)}
    fps = {i: build_fingerprint(listing, [], W34) for i, listing in ads.items()}
    for i, listing in ads.items():
        store.put_listing(i, FpRow(guard_row(fps[i]), "d", "cell", "g", True,
                                   ref_codes=carried_codes(listing, W34)), [])
    pair = {a.id: a, b.id: b}
    ctx = context_for(calibration, W34, {i: fps[i] for i in pair}, pair,
                      crowded_codes(store, W34, pair))
    feats = pair_features(fps[a.id], fps[b.id], a, b, [], [], ctx, W34)
    return certificate_of(feats, a, b, W34)


def _cut(*ads_at_cut: Listing) -> Calibration:
    ads = {listing.id: listing for listing in ads_at_cut}
    fps = {i: build_fingerprint(listing, [], W34) for i, listing in ads.items()}
    return Calibration.build(fps, ads, W34, GEN)


def test_a_carrier_added_after_the_cut_certifies_while_the_code_stays_rare_and_pure() -> None:
    first = ad(1, code="556677")
    assert _lane_certificate(MemoryStore(), _cut(first), first, ad(2, code="IDNES-556677")) == "K-R"


def test_a_code_that_became_a_crowd_after_the_cut_stops_certifying() -> None:
    first = ad(1, code="556677")
    crowd = [ad(i, code="556677") for i in range(3, 3 + MAX_CODE_POPULATION)]
    assert _lane_certificate(MemoryStore(), _cut(first), first, ad(2, code="556677"),
                             *crowd) is None


def test_a_code_whose_later_carriers_disagree_on_the_unit_stops_certifying() -> None:
    first = ad(1, code="556677")
    assert _lane_certificate(MemoryStore(), _cut(first), first, ad(2, code="556677"),
                             ad(3, code="556677", dispo="3+kk")) is None
    assert _lane_certificate(MemoryStore(), _cut(first), first, ad(2, code="556677"),
                             ad(3, code="556677", area=80.0)) is None


def test_under_settings_that_read_no_column_no_code_is_stored_or_counted() -> None:
    assert carried_codes(ad(1, code="556677"), W31) == ()
    assert crowded_codes(MemoryStore(), W31, {1: ad(1, code="556677")}) == frozenset()

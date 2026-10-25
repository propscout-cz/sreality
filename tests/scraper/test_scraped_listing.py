"""Contract tests for scraper.scraped_listing (the multi-portal row shape)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from scraper.hashing import digest
from scraper.scraped_listing import _HASH_FIELDS, ScrapedListing


def _h(listing: ScrapedListing) -> str:
    return digest(listing.hash_doc())


def _listing(**overrides) -> ScrapedListing:
    base = dict(
        source="bazos",
        source_id_native="219122924",
        source_url="https://reality.bazos.cz/inzerat/219122924/x.php",
        category_main="byt",
        category_type="prodej",
        price_czk=5_499_000,
        disposition="2+kk",
        locality="Letovice",
        street="Husova 12",
    )
    base.update(overrides)
    return ScrapedListing(**base)


def test_the_place_reading_never_reaches_a_column():
    """W4-c dropped every place column from `listings`, so `listing_columns` emits none.
    The parser still READS the page's locality/street/pin — that reading is the
    claim `location_data` resolves into `listing_location`, and it rides
    raw_json, not a column."""
    listing = _listing()
    row = listing.listing_columns()
    assert listing.street == "Husova 12" and listing.locality == "Letovice"
    for key in ("locality", "district", "street", "house_number", "zip", "lat", "lon"):
        assert key not in row, f"listing_columns still emits {key!r}"


def test_street_is_not_hashed():
    # street is derived/extracted (like lat/lon): a backfill or extraction
    # refinement must never churn snapshots.
    assert "street" not in _HASH_FIELDS
    a = _listing(street=None)
    b = replace(a, street="Husova 12")
    assert _h(a) == _h(b)


def test_content_hash_still_sees_real_content():
    a = _listing()
    b = replace(a, locality="Brno")
    assert _h(a) != _h(b)


def test_published_at_lands_in_the_columns():
    row = _listing(published_at=date(2026, 7, 2)).listing_columns()
    assert row["published_at"] == date(2026, 7, 2)


def test_published_at_is_not_hashed():
    # Portal lifecycle metadata: bazos re-stamps the date on every bump / TOP
    # renewal, and backfills from stored raw must stay snapshot-free — a
    # published_at change must NEVER change the content hash.
    assert "published_at" not in _HASH_FIELDS
    a = _listing(published_at=None)
    b = replace(a, published_at=date(2026, 7, 2))
    c = replace(a, published_at=date(2026, 7, 3))
    assert _h(a) == _h(b) == _h(c)


def test_area_basis_lands_in_the_columns_and_in_the_column_list():
    from scraper import db

    row = _listing(area_m2=70.0, area_basis="usable").listing_columns()
    assert row["area_basis"] == "usable"
    assert "area_basis" in db.LISTING_COLUMNS


def test_area_basis_is_not_hashed():
    # It is a PROVENANCE observation of a value that does not change, so stamping
    # it (now, or by a later backfill) must not append a snapshot row — rule 2.
    assert "area_basis" not in _HASH_FIELDS
    a = _listing(area_m2=70.0, area_basis=None)
    b = replace(a, area_basis="usable")
    assert _h(a) == _h(b)


def test_source_url_lands_in_the_columns_and_is_required():
    # Identity, not content: in the row (so it rides LISTING_COLUMNS), never hashed, and
    # refused when empty — the write is preserve-if-null, so a None would keep a stale URL.
    row = _listing().listing_columns()
    assert row["source_url"] == "https://reality.bazos.cz/inzerat/219122924/x.php"
    assert "source_url" not in _HASH_FIELDS
    import pytest
    with pytest.raises(ValueError):
        _listing(source_url="")


# Every hash field set, no price_native. Its digest was computed on 8de51b414, before
# price_native existed: a formula change must leave every stored Kč-priced hash valid, or
# the next fetch of every ad re-snapshots once.
_GOLDEN = ScrapedListing(
    source="idnes", source_id_native="6aa540b9cdd78699cd0ec464",
    source_url="https://reality.idnes.cz/detail/prodej/byt/praha/6aa540b9cdd78699cd0ec464/",
    category_main="byt", category_type="prodej", price_czk=7_229_480,
    price_unit="za nemovitost", area_m2=42.0, disposition="2+kk",
    locality="Praha 5 - Hlubočepy", district=None, floor=3, total_floors=5,
    has_balcony=True, has_parking=False, has_lift=True, building_type="cihla",
    condition="velmi_dobry", energy_rating="G", estate_area=None, usable_area=42.0,
    garden_area=None, category_sub_cb=None, subtype="byt", furnished="ano",
    terrace=True, cellar=None, garage=False, parking_lots=1, ownership="osobni",
    description="Útulný byt 2+kk.",
)


def test_a_kc_priced_hash_is_byte_identical_to_before_price_native():
    assert set(_GOLDEN.hash_doc()) == set(_HASH_FIELDS)
    assert _h(_GOLDEN) == "9512b08a9a5529f583275e2411b3bdfe2fd51e9c94907903c99e3e3c6ea00c3e"


def test_a_stated_foreign_price_stands_in_for_its_daily_kc_conversion():
    # idnes re-converts "Cena v EUR 298 000 €" to Kč every day: the rate moving is not the ad
    # changing (rule 2), the stated price moving is.
    eur = _listing(price_native="298000 EUR")
    assert "price_czk" not in eur.hash_doc()
    assert _h(eur) == _h(replace(eur, price_czk=7_164_210))
    assert _h(eur) != _h(replace(eur, price_native="289000 EUR"))
    assert _h(eur) != _h(replace(eur, price_native="298000 USD"))
    assert _h(_listing()) != _h(replace(_listing(), price_czk=5_399_000))


def test_price_native_is_never_a_column():
    assert "price_native" not in _listing(price_native="298000 EUR").listing_columns()


def test_a_plot_that_is_the_floor_area_is_declined_before_the_content_hash() -> None:
    """The ONE call site of `scraper.area.stated_plot` for every portal that crosses this
    contract (rule 21): a komerční "plot" equal to the usable measure, and ANY plot on a
    byt, is absence — absence in the HASH too, so the row and its newest snapshot agree
    (rules 2/8) instead of a NULL landing after the hash at the write boundary."""
    echo = _listing(category_main="komercni", area_m2=10.0, area_basis="usable",
                    usable_area=10.0, estate_area=10.0)
    assert echo.estate_area is None
    assert echo.listing_columns()["estate_area"] is None
    assert echo.hash_doc()["estate_area"] is None
    assert _h(echo) == _h(replace(echo, estate_area=None))
    assert _listing(category_main="byt", area_m2=59.0, area_basis="usable",
                    usable_area=59.0, estate_area=64.0).estate_area is None
    parcel = _listing(category_main="komercni", area_m2=852.0, area_basis="total",
                      estate_area=6841.0)
    assert parcel.estate_area == 6841.0
    assert _h(parcel) != _h(replace(parcel, estate_area=None))
    house = _listing(category_main="dum", area_m2=210.0, area_basis="usable",
                     usable_area=210.0, estate_area=80.0)
    assert house.estate_area == 80.0


def test_the_plot_rule_reads_the_description_before_the_content_hash() -> None:
    """Equality alone is not evidence (W22, 2026-10-25): the same call site hands the rule
    the advert's description, so a petrol station whose text states the parcel it typed
    into both boxes keeps its plot — in the column AND the hash — while a unit whose text
    names a different parcel still hashes the echo as absence."""
    station = _listing(category_main="komercni", area_m2=994.0, area_basis="usable",
                       usable_area=994.0, estate_area=994.0,
                       description="Čerpací stanice LPG. Celková výměra pozemku činí 994m².")
    assert station.estate_area == 994.0
    assert station.hash_doc()["estate_area"] == 994.0
    assert replace(station).estate_area == 994.0
    unit = _listing(category_main="komercni", area_m2=495.0, area_basis="usable",
                    usable_area=495.0, estate_area=495.0,
                    description="Objekt s užitnou plochou 495 m² na pozemku o výměře 1 484 m².")
    assert unit.estate_area is None
    assert unit.hash_doc()["estate_area"] is None

"""A stated price is a number plus its basis, and ONE conversion turns it into the stored total (#2355)."""

from __future__ import annotations

import logging
from decimal import Decimal

import pytest

from scraper import price_reading
from scraper.price_reading import (
    EUR_CZK_RATE,
    PER_M2,
    PER_M2_MONTH,
    PER_M2_YEAR,
    TOTAL,
    PriceReading,
    read_price_text,
    resolve,
)


def _r(amount: str, currency: str = "CZK", basis: str = TOTAL) -> PriceReading:
    return PriceReading(Decimal(amount), currency, basis)


# --- the reader: every cell shape the three portals render ------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    # remax index card `data-price`, tags already stripped (fetched live 2026-11-05)
    ("11\xa0500\xa0Kč\n\t\t\t\n\t\t\t\t(\t\t\t\tza měsíc)\n\t\t\t", _r("11500")),
    ("109\xa0Kč\n\t\t\t\n\t\t\t\t(\t\t\t\tm²/měsíc)", _r("109", basis=PER_M2_MONTH)),
    ("7\xa0500\xa0Kč (bez DPH,\t\t\t\tza měsíc)", _r("7500")),
    # the VAT prefix the old anchored test missed (108 of 522 stored per-area cards)
    ("2\xa0500\xa0Kč (bez DPH, za m²)", _r("2500", basis=PER_M2)),
    ("2\xa0500\xa0Kč (vč. DPH, za m²)", _r("2500", basis=PER_M2)),
    ("9\xa0962\xa0000\xa0Kč (za nemovitost)", _r("9962000")),
    # remax detail spec cell
    ("2 500 CZK / za m2", _r("2500", basis=PER_M2)),
    ("100 CZK / m2/měsíc", _r("100", basis=PER_M2_MONTH)),
    ("15,5 EUR / m2/měsíc", _r("15.5", "EUR", PER_M2_MONTH)),
    ("10 000 000 EUR / za nemovitost", _r("10000000", "EUR")),
    ("7 928,17 EUR / za měsíc", _r("7928.17", "EUR")),
    ("3 347,37 CZK / za m2", _r("3347.37", basis=PER_M2)),
    # the decimal total remax inflated 100x by concatenating every digit (18 rows)
    ("9 990 000,39 CZK / za nemovitost", _r("9990000.39")),
    ("7\xa0759\n\t\tCZK/ za m2", _r("7759", basis=PER_M2)),
    # realitymix detail cell and index card (card fixture: tests/fixtures/portal_html)
    ("6 490 000 Kč / (za m 2 ) Nabídněte cenu", _r("6490000", basis=PER_M2)),
    ("1 100 Kč / (za m 2 /měsíc)", _r("1100", basis=PER_M2_MONTH)),
    ("1,00 € / (za m 2 /měsíc)", _r("1.00", "EUR", PER_M2_MONTH)),
    ("266,30 Kč / (za m 2 /měsíc)", _r("266.30", basis=PER_M2_MONTH)),
    ("3 350 Kč (za m 2 )", _r("3350", basis=PER_M2)),
    ("5 800 Kč (za měsíc)", _r("5800")),
    ("5 290 000 Kč", _r("5290000")),
    # two amounts never concatenate into one (the integer column overflowed); the first is read
    ("12 000 000 Kč 5 290 000 Kč", _r("12000000")),
    # a total with a per-m² NOTE beside it is still a total
    ("4 990 000 Kč (4 008 Kč za m²)", _r("4990000")),
    # the unit BEFORE the amount; the "2" of "m2" must not become the amount
    ("Cena za m2: 7 759 Kč", _r("7759", basis=PER_M2)),
    # a yearly rent rate (operator ruling 2026-11-07: stored as the monthly rent)
    ("1 000 Kč / (za m 2 /rok)", _r("1000", basis=PER_M2_YEAR)),
    ("1 100 CZK / m2/rok", _r("1100", basis=PER_M2_YEAR)),
])
def test_reads_amount_currency_and_basis(text: str, expected: PriceReading) -> None:
    assert read_price_text(text) == expected


@pytest.mark.parametrize("text", [
    None, "", "Cena na vyžádání", "Dohodou", "Prodáno",
    # a currency we do not convert
    "250 000 USD",
])
def test_reads_nothing_it_cannot_store(text: str | None) -> None:
    assert read_price_text(text) is None


# --- the conversion -----------------------------------------------------------------------

def test_a_total_in_czk_is_itself_with_no_record() -> None:
    out = resolve(_r("4977000"), area_m2=1422.0, category_main="pozemek",
                  category_type="prodej")
    assert (out.total_czk, out.record, out.native) == (4_977_000, None, None)


def test_per_m2_multiplies_by_the_measure_area() -> None:
    """remax 447268 (Kostomlaty): 2,500 CZK per m² of a 1,422 m² plot."""
    out = resolve(_r("2500", basis=PER_M2), area_m2=1422.0, category_main="pozemek",
                  category_type="prodej")
    assert out.total_czk == 3_555_000
    assert out.record == {"amount": 2500.0, "currency": "CZK", "basis": PER_M2}


def test_per_m2_per_month_on_a_rent_is_the_monthly_total() -> None:
    out = resolve(_r("109", basis=PER_M2_MONTH), area_m2=250.0, category_main="komercni",
                  category_type="pronajem")
    assert out.total_czk == 27_250


def test_the_area_is_taken_at_the_column_scale() -> None:
    # the detail parses 86.19 m², the column stores 86.2 and the card multiplies by that
    for area in (86.19, 86.2):
        out = resolve(_r("100", basis=PER_M2_MONTH), area_m2=area, category_main="byt",
                      category_type="pronajem")
        assert out.total_czk == 8_620


def test_per_m2_per_month_on_a_sale_stores_nothing() -> None:
    out = resolve(_r("109", basis=PER_M2_MONTH), area_m2=250.0, category_main="komercni",
                  category_type="prodej")
    assert out.total_czk is None


def test_per_m2_with_no_area_stores_nothing() -> None:
    out = resolve(_r("2500", basis=PER_M2), area_m2=None, category_main="pozemek",
                  category_type="prodej")
    assert out.total_czk is None


def test_eur_converts_at_the_fixed_rate_and_records_it() -> None:
    assert EUR_CZK_RATE == Decimal("24.5")
    out = resolve(_r("635000.08", "EUR"), area_m2=120.0, category_main="dum",
                  category_type="prodej")
    assert out.total_czk == 15_557_501
    assert out.record == {
        "amount": 635000.08, "currency": "EUR", "basis": TOTAL,
        "czk_per_eur": 24.5, "rate_set": price_reading.EUR_CZK_RATE_SET,
    }
    # the content hash reads the stated amount, so a later rate review snapshots nothing
    assert out.native == "635000.08 EUR"


def test_eur_per_m2_per_month() -> None:
    out = resolve(_r("15.5", "EUR", PER_M2_MONTH), area_m2=100.0, category_main="komercni",
                  category_type="pronajem")
    assert out.total_czk == 37_975
    assert out.native == "15.5 EUR per_m2_month"


def test_drops_the_fraction_like_the_parsers_always_did() -> None:
    # a stored "6 990 000,5" total stays 6,990,000, so it never moves by a crown
    out = resolve(_r("6990000.5"), area_m2=None, category_main="pozemek",
                  category_type="prodej")
    assert out.total_czk == 6_990_000


def test_nothing_resolves_to_nothing() -> None:
    out = resolve(None, area_m2=10.0, category_main="byt", category_type="prodej")
    assert (out.total_czk, out.record, out.native) == (None, None, None)


# --- the mislabel override: only when one reading is impossible and the other normal ------

def test_realitymix_8557677_a_total_labelled_per_m2_is_the_total(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="scraper.price_reading")
    out = resolve(_r("6490000", basis=PER_M2), area_m2=4087.0, category_main="pozemek",
                  category_type="prodej", label="realitymix/8557677")
    assert out.total_czk == 6_490_000
    assert out.record == {"amount": 6490000.0, "currency": "CZK", "basis": PER_M2,
                          "read_as": TOTAL}
    assert [r.getMessage() for r in caplog.records] == [
        "PRICE basis_override realitymix/8557677 stated=per_m2 used=total "
        "amount_czk=6490000 area_m2=4087.0 category=pozemek/prodej"]


def test_realitymix_8557727_the_same() -> None:
    out = resolve(_r("3190000", basis=PER_M2), area_m2=1914.0, category_main="pozemek",
                  category_type="prodej")
    assert out.total_czk == 3_190_000


def test_an_impossible_total_that_is_a_normal_per_m2_is_the_per_m2() -> None:
    """realitymix 8539137: "24 Kč" for a 51,563 m² forest is 24 Kč per m²."""
    out = resolve(_r("24"), area_m2=51563.0, category_main="pozemek", category_type="prodej")
    assert out.total_czk == 1_237_512
    assert out.record == {"amount": 24.0, "currency": "CZK", "basis": TOTAL, "read_as": PER_M2}


def test_both_readings_plausible_trusts_the_label() -> None:
    # 25,000 per m² of a 1,000 m² plot is a dear plot; 25 per m² would be a cheap field
    out = resolve(_r("25000", basis=PER_M2), area_m2=1000.0, category_main="pozemek",
                  category_type="prodej")
    assert out.total_czk == 25_000_000
    # 180,000 for a 50 m² flat (remax 417736, Kladno) is cheap, never 180,000 per m²
    out = resolve(_r("180000"), area_m2=50.0, category_main="byt", category_type="prodej")
    assert out.total_czk == 180_000


def test_an_impossible_total_whose_per_m2_is_not_normal_stays_the_total() -> None:
    # 254,000 for a "1,687 m²" house: as a per-m² figure it would be over the 99th percentile
    out = resolve(_r("254000"), area_m2=1687.0, category_main="dum", category_type="prodej")
    assert out.total_czk == 254_000


def test_an_impossible_per_m2_whose_total_is_not_normal_stores_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # realitymix 7055474: 8,575 per m² per month is impossible, and so is 14 per m² as a total
    caplog.set_level(logging.INFO, logger="scraper.price_reading")
    out = resolve(_r("8575", basis=PER_M2_MONTH), area_m2=614.0, category_main="komercni",
                  category_type="pronajem", label="realitymix/7055474")
    assert out.total_czk is None
    assert [r.getMessage() for r in caplog.records] == [
        "PRICE basis_override realitymix/7055474 stated=per_m2_month used=none "
        "amount_czk=8575 area_m2=614.0 category=komercni/pronajem"]


def test_a_land_rate_stored_as_a_total_is_read_per_m2() -> None:
    """realitymix 8615319: "1 567 Kč" for a 1,659 m² building plot. The bands read from every
    source put land's p0.1 at 0.15 Kč/m², so this was never caught; sreality's is 10.24."""
    out = resolve(_r("1567"), area_m2=1659.0, category_main="pozemek", category_type="prodej")
    assert out.total_czk == 2_599_653
    assert out.record == {"amount": 1567.0, "currency": "CZK", "basis": TOTAL, "read_as": PER_M2}


def test_an_impossible_rent_total_is_read_per_m2_per_month() -> None:
    """remax 441508: "120 Kč" a month for 274 m² of offices is 120 Kč per m² per month."""
    out = resolve(_r("120"), area_m2=274.0, category_main="komercni", category_type="pronajem")
    assert out.total_czk == 32_880
    assert out.record == {"amount": 120.0, "currency": "CZK", "basis": TOTAL,
                          "read_as": PER_M2_MONTH}


def test_a_rent_rate_that_names_no_period_stores_nothing() -> None:
    # per m² on a rent could be monthly or yearly (12x apart): only "per m² per month" is stored
    out = resolve(_r("150", basis=PER_M2), area_m2=200.0, category_main="komercni",
                  category_type="pronajem")
    assert out.total_czk is None


def test_a_yearly_rent_rate_is_stored_as_the_monthly_rent() -> None:
    out = resolve(_r("1200", basis=PER_M2_YEAR), area_m2=100.0, category_main="komercni",
                  category_type="pronajem")
    assert out.total_czk == 10_000
    assert out.record == {"amount": 1200.0, "currency": "CZK", "basis": PER_M2_YEAR}


def test_a_yearly_rate_on_a_sale_stores_nothing() -> None:
    out = resolve(_r("1200", basis=PER_M2_YEAR), area_m2=100.0, category_main="komercni",
                  category_type="prodej")
    assert out.total_czk is None


def test_an_unlabelled_resolve_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    # the walk resolves every card on every sighting; only the detail parse, which writes the
    # row, logs an override
    caplog.set_level(logging.INFO, logger="scraper.price_reading")
    out = resolve(_r("6490000", basis=PER_M2), area_m2=4087.0, category_main="pozemek",
                  category_type="prodej")
    assert out.total_czk == 6_490_000
    assert caplog.records == []


def test_no_band_for_the_category_trusts_the_label() -> None:
    out = resolve(_r("6490000", basis=PER_M2), area_m2=10.0, category_main="pozemek",
                  category_type="drazba")
    assert out.total_czk == 64_900_000


def test_every_band_is_ordered() -> None:
    for key, band in price_reading.PPM2_BANDS.items():
        assert 0 < band.impossible_low < band.p01 < band.p99 < band.impossible_high, key

"""A per-m² figure must never masquerade as `listings.price_czk`.

`price_czk` is a TOTAL (or a monthly rent) on all nine portals — production
carries only `za nemovitost` / `za mesic` / `celkem` / `měsíc`, none per-area. A
unit price stored there reads as a total in every downstream consumer (Kč/m²
stats, estimation comparables, Browse sort, price-drop watchdogs), which is
strictly worse than the missing value it replaces.

The masquerade is ONGOING, not historical: ceskereality, realitymix and bazos
carry ~310 m² commercial units whose stored price_czk has a median of 136 / 176 /
379 Kč, with rows first seen the day this was written. All three are TEXT-price
portals, so the rail is the shared parse-time refusal
(`scraper.price_text.is_per_area_price`) — this is the ONLY rail, which is why
every portal's own cell shape has to reach it here.

A second rail was tried at the write boundary (a per-agenda price floor) and
withdrawn: measured against production it would have NULLed 3,025 active rows,
2,501 of them `pronajem`/`komercni`, after the content hash — so the deletion
would append no snapshot (rule 8) and each row would then read as "price
changed" on every index walk forever. See the W1 review notes.

Since #2355 remax and realitymix no longer refuse a rate: `scraper.price_reading` reads it
as a rate and stores rate x area, so their cells are held to the reader below instead.
"""

from __future__ import annotations

import pytest

from scraper.bazos_parser import _parse_price as bazos_price
from scraper.ceskereality_parser import _parse_price as ceskereality_price
from scraper.idnes_parser import _parse_price as idnes_price
from scraper.maxima_parser import _parse_price as maxima_price
from scraper.price_reading import PER_M2, PER_M2_MONTH, TOTAL, read_price_text
from scraper.price_text import is_per_area_price

# Every portal whose price arrives as TEXT and still refuses a rate. One shared parse rail,
# so the same corpus must hold for all four (rule 21 — no per-portal branches).
TEXT_PRICE_PARSERS = [
    pytest.param(bazos_price, id="bazos"),
    pytest.param(ceskereality_price, id="ceskereality"),
    pytest.param(idnes_price, id="idnes"),
    pytest.param(maxima_price, id="maxima"),
]

# Seeded from test_idnes_parser's six positives (incl. the spaced "18 500 Kč / m²")
# and test_remax_parser's "7 759 CZK/ za m2", which the naive digit scrape used to
# turn into 77592 by swallowing the "2" out of "m2".
PER_AREA_CELLS = [
    "18 500 Kč/m²",
    # realitymix's real cell, which W1 shipped without matching: it BRACKETS the
    # marker (`45 Kč / (za m²)`), and an anchor that cannot open a bracket walks
    # straight past 1,147 confirmed per-m² rows. Measured 2026-08-24.
    "45 Kč / (za m 2 )",
    "850 Kč /(za m2)",
    # EUR-denominated, both real cells. ceskereality stages the string verbatim
    # (336 priced rows); realitymix's is a DECIMAL, and every portal's amount
    # scanner is an integer run, so the slice reaching the rail starts `,00 € …`
    # — the anchor has to survive both the fraction and a non-Kč currency or
    # 611 confirmed per-m² rows are reported clean. Measured 2026-08-25.
    "16 EUR za m²/měsíc",
    "12,00 € / (za m 2 /měsíc)",
    "18 500 Kč / m²",
    "2 500 Kč za m²",
    "1 200 Kč/m²/rok",
    "150 Kč/m2/měsíc",
    "7\xa0759\n\t\tCZK/ za m2",
]

# The unit BEFORE the amount. The anchored test cannot see it, and remax's digit
# scrape would fold the "2" out of "m2" into the number (7 759 -> 27759), so a
# fabricated total is what a missing guard produces here — not a missing value.
PER_AREA_PREFIX_CELLS = [
    "Cena za m2: 7 759 Kč",
    "Cena/m2 3 500 Kč",
    "Cena za metr: 45 000 Kč",
]

# The two negatives are the point of the anchoring: a monthly rent is not a unit
# price, and a total with a per-m² NOTE beside it is still a total.
TOTAL_CELLS = [
    ("14 160 Kč/měsíc", 14_160),
    ("4 990 000 Kč (4 008 Kč/m² )", 4_990_000),
    ("3 190 000 Kč", 3_190_000),
]

# The after-amount slices the widened anchor must still REFUSE. `EUR za měsíc`
# is a real ceskereality shape (46 priced rows) and a CURRENCY bug, not a unit
# masquerade — convicting it would NULL a real price.
NON_PER_AREA_SLICES = [
    " EUR za měsíc",
    " EUR Spočítat hypotéku",
    " Kč (4 008 Kč/m²)",
    ",- Kč",
    "€ za měsíc",
]


@pytest.mark.parametrize("parse", TEXT_PRICE_PARSERS)
@pytest.mark.parametrize("cell", PER_AREA_CELLS)
def test_per_area_cell_yields_null(parse, cell: str) -> None:
    assert parse(cell, "prodej")[0] is None


@pytest.mark.parametrize("cell", PER_AREA_CELLS + PER_AREA_PREFIX_CELLS)
def test_the_reader_reads_every_per_area_cell_as_a_rate_never_a_total(cell: str) -> None:
    # remax and realitymix (#2355): a rate is read as a rate, or not at all ("/rok")
    reading = read_price_text(cell)
    assert reading is None or reading.basis in (PER_M2, PER_M2_MONTH)
    if "rok" not in cell:
        assert reading is not None


@pytest.mark.parametrize("parse", TEXT_PRICE_PARSERS)
@pytest.mark.parametrize("cell,expected", TOTAL_CELLS)
def test_total_cell_survives(parse, cell: str, expected: int) -> None:
    assert parse(cell, "prodej")[0] == expected


@pytest.mark.parametrize("cell,expected", TOTAL_CELLS)
def test_the_reader_keeps_a_total_a_total(cell: str, expected: int) -> None:
    reading = read_price_text(cell)
    assert reading is not None
    assert (reading.amount, reading.basis) == (expected, TOTAL)


@pytest.mark.parametrize("cell", PER_AREA_CELLS)
def test_the_unit_is_never_mistaken_for_an_agenda(cell: str) -> None:
    # price_unit stays the agenda's own label; it is a duplicate of category_type
    # and must never be pressed into service as an area unit (migration 423).
    assert idnes_price(cell, "pronajem")[1] == "za mesic"
    assert idnes_price(cell, "prodej")[1] == "za nemovitost"


@pytest.mark.parametrize("slice_", NON_PER_AREA_SLICES)
def test_the_anchor_still_refuses_a_non_per_area_slice(slice_: str) -> None:
    assert is_per_area_price(slice_) is False

"""scraper.text_grammar: an advert read from its words alone — a title, prose and labelled
facts — the way a web page's fact table and a document's text lines both reach it. No HTML."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scraper import vocabulary
from scraper.text_grammar import TextFacts, facts_from_lines, parse_text, split_fact, text_price

SALE = """Prodej bytu 3+1, 78 m², Brno - Žabovřesky
Cena: 8 950 000 Kč
Dispozice: 3+1
Užitná plocha: 78 m²
Podlaží: 4. NP
Počet podlaží: 6
Vlastnictví: Osobní
Stav objektu: Po rekonstrukci
Konstrukce: Panelová
Výtah: Ano
Balkon: 4 m²
Energetická náročnost: C
Nabízíme světlý byt 3+1 ve čtvrtém patře panelového domu. Původní cena 9 990 000 Kč byla snížena.
Volejte makléři na +420 602 123 456.
E-mail: jan.novak@example.cz
"""

RENT = """Pronájem bytu 2+kk, Praha 7 - Holešovice
Nájemné: 14 500 Kč/měsíc
Poplatky: 3 500 Kč/měsíc
Kauce: 29 000 Kč
Plocha: 54 m²
Patro: 3. patro
Vybavení: Částečně vybaveno
Parkování: Garáž
Pronajmeme částečně zařízený byt 2+kk v klidné ulici, volný od prosince. Volejte 777 12 34 56.
"""

HOUSE = """Prodej rodinného domu 5+1, Líbeznice
Cena: 12 490 000 Kč
Typ nemovitosti: Rodinný dům
Užitná plocha: 180 m²
Plocha pozemku: 812 m²
Zahrada: 650 m²
Počet podlaží: 2
Garáž: Ano
Sklep: Ne
Stav: Novostavba
Prodáváme novostavbu rodinného domu 5+1 se zahradou a garáží, prohlídky na +420 731 000 111.
E-mail: Pavel@RK-Example.cz
"""


def _read(text: str) -> TextFacts:
    title, _, _ = text.partition("\n")
    return parse_text(title, text, facts_from_lines(text), portal="web")


def test_a_flat_for_sale_with_a_labelled_price() -> None:
    got = _read(SALE)
    assert (got.price_czk, got.price_unit) == (8_950_000, "za nemovitost")  # the label beats the prose
    assert (got.category_main, got.category_type, got.disposition) == ("byt", "prodej", "3+1")
    assert (got.area_m2, got.area_basis, got.usable_area) == (78.0, "usable", 78.0)
    assert (got.floor, got.total_floors) == (3, 6)
    assert (got.ownership, got.condition, got.building_type) == ("osobni", "po_rekonstrukci", "panel")
    assert (got.has_lift, got.has_balcony, got.energy_rating) == (True, True, "C")
    assert (got.phone, got.email) == ("+420602123456", "jan.novak@example.cz")


def test_a_flat_for_rent() -> None:
    vocabulary.take_unmapped()
    got = _read(RENT)
    assert (got.price_czk, got.price_unit) == (14_500, "za mesic")
    assert (got.category_main, got.category_type, got.disposition) == ("byt", "pronajem", "2+kk")
    assert (got.area_m2, got.area_basis, got.floor) == (54.0, "floor", 3)
    assert (got.furnished, got.has_parking) == ("castecne", True)
    assert (got.phone, got.email) == ("+420777123456", None)
    assert vocabulary.take_unmapped() == []  # fees and the deposit are known labels, not unknown ones


def test_a_house_with_a_plot() -> None:
    got = _read(HOUSE)
    assert (got.price_czk, got.price_unit) == (12_490_000, "za nemovitost")
    assert (got.category_main, got.category_type, got.disposition) == ("dum", "prodej", "5+1")
    assert (got.area_m2, got.area_basis) == (180.0, "usable")
    assert (got.estate_area, got.garden_area) == (812.0, 650.0)
    assert (got.floor, got.total_floors) == (None, 2)
    assert (got.garage, got.has_parking, got.cellar, got.condition) == (True, True, False, "novostavba")
    assert (got.phone, got.email) == ("+420731000111", "pavel@rk-example.cz")


def test_an_unknown_label_is_counted_and_never_mapped_by_likeness() -> None:
    vocabulary.take_unmapped()
    got = parse_text("Prodej bytu 2+kk", None, [("Užitná plocha bytu", "67 m²"), ("Orientace", "jih"),
                                                 ("Stav", "Výborný"), ("Adresa", "Máchova")],
                     portal="web")
    assert got.usable_area is None and got.condition is None
    assert sorted(vocabulary.take_unmapped()) == [
        ("condition/web/vyborny", 1), ("label/web/orientace", 1), ("label/web/uzitna_plocha_bytu", 1)]


def test_a_labelled_per_m2_figure_is_no_price_and_the_prose_still_speaks() -> None:
    got = parse_text("Prodej pozemku", "Pozemek o výměře 900 m² za 2 700 000 Kč.",
                     [("Cena", "3 000 Kč/m²")], portal="web")
    assert (got.price_czk, got.category_main, got.area_m2, got.area_basis) == (
        2_700_000, "pozemek", 900.0, "plot")


def test_a_bare_labelled_storey_number_stays_unread_and_the_prose_decides() -> None:
    got = parse_text("Prodej bytu 2+kk", "Byt se nachází v 1. podzemním podlaží.",
                     [("Podlaží", "-1")], portal="web")
    assert got.floor == -1
    assert parse_text("Byt", None, [("Podlaží", "3")], portal="web").floor is None


def test_structured_figures_the_caller_read_lead() -> None:
    got = parse_text("Prodej bytu 3+1", None, [("Cena", "5 000 000 Kč"), ("Plocha", "70 m²")],
                     portal="web", stated_price=6_490_000, stated_floor_area=78.0)
    assert (got.price_czk, got.area_m2, got.area_basis) == (6_490_000, 78.0, "floor")


@pytest.mark.parametrize(("line", "fact"), [
    ("Cena: 8 950 000 Kč", ("Cena", "8 950 000 Kč")),
    ("  Tel.:  +420 602 123 456 ", ("Tel.", "+420 602 123 456")),
    ("Plocha (m²): 54", ("Plocha (m²)", "54")),
    ("Prohlídky v 10:00", None),
    ("https://rk.example/byt/1", None),
    ("Toto je velmi dlouhá věta, která není žádný štítek: nic", None),
    ("Bez dvojtečky", None),
])
def test_a_line_is_a_fact_only_when_it_reads_label_colon_value(line: str, fact: tuple | None) -> None:
    assert split_fact(line) == fact


def test_dotted_thousands_read_as_one_amount() -> None:
    assert text_price("8.950.000 Kč") == 8_950_000
    assert text_price("Cena 1.250.000,- Kč") == 1_250_000


def test_the_grammar_imports_without_an_html_parser() -> None:
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-c", "import sys, scraper.text_grammar; print('selectolax' in sys.modules)"],
        cwd=root, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"

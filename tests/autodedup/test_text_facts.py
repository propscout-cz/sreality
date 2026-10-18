"""The printed-fact parsers the benchmark and the rule floor now share (E60/E61, W8)."""

from __future__ import annotations

import pytest

from autodedup.dataset import Listing, Location, live_end_stamp
from autodedup.text_facts import (
    address_block_key,
    compass_points,
    mask_codes,
    orientations,
    printed_floors,
    printed_floors_by_form,
    printed_unit_codes,
    reference_codes,
    stated_areas,
    unit_designators,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Pro komunikaci uvádějte evidenční číslo zakázky N115423.", {"N115423"}),
        ("Ev. číslo: 657349", {"657349"}),
        ("evidenční číslo / ID zakázky: R2256", {"R2256"}),
        ("[ID 84553] Prodej bytu", {"84553"}),
        ("Cena 4 500 000 Kč, postaveno 1998", set()),
        ("Ev. č. 123", set()),  # shorter than MIN_CODE_LEN: a per-broker sequence number
    ],
)
def test_reference_codes(text: str, expected: set[str]) -> None:
    assert reference_codes(text) == expected


def test_mask_codes_hides_the_code_in_the_original_text() -> None:
    masked = mask_codes("Nabídka, ev. číslo: 657349, volejte")
    assert "657349" not in (masked or "")
    assert "[KOD]" in (masked or "")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("byt (č.3) v 1.NP", {"3"}),
        ("jednotka č. 12", {"12"}),
        ("s označením B36", {"B36"}),
        ("apartmán č. A311", {"A311"}),
        ("prodej jednotku 4+kk", set()),  # a disposition is never a unit designator
    ],
)
def test_unit_designators(text: str, expected: set[str]) -> None:
    assert unit_designators(text) == expected


# --- stated areas -------------------------------------------------------------------------


def test_stated_areas_reads_the_text_not_the_column() -> None:
    assert stated_areas("byt 3+1 o velikosti 72m2 se zahrádkou 20,2 m²") == {72.0, 20.2}


def test_a_building_total_is_not_the_unit_area() -> None:
    """W7 wrong label 83275 x 412654: the sreality body prints only Polygon's 10 500 m²."""
    sreality = "Polygon je moderní budova o velikosti 10.500 m2 pronajímatelné plochy."
    ceskereality = (
        "Nabízíme k pronájmu moderní kancelářské prostory o velikosti 883 m2 v budově. "
        "Objekt disponuje celkovou plochou cca 10 500 m2."
    )
    assert stated_areas(sreality, 883.0) == set()
    assert stated_areas(ceskereality, 883.0) == {883.0}


def test_a_unit_area_beside_the_word_budova_survives() -> None:
    """Listing 464483: `cihlové budovy. S užitnou plochou 80 m2` names the UNIT, not the house."""
    body = "Byt ve 13. patře cihlové budovy. S užitnou plochou 80 m2 nabízí příjemné bydlení."
    assert stated_areas(body, 80.0) == {80.0}


def test_a_size_range_is_a_menu_not_a_fact() -> None:
    body = "Nabízíme pronájem obchodních prostor/ kanceláří od 55m2 do 900m2 v budově."
    assert stated_areas(body, 65.0) == set()


def test_an_enumeration_of_sizes_is_a_menu() -> None:
    body = "Nabídka coworkingových prostor – 10 m2, 20 m2, 30 m2, 50 m2, 60 m2, 75 m2."
    assert stated_areas(body, 25.0) == set()


def test_spaced_thousands_are_one_number_not_their_tail() -> None:
    """`10 500 m2` must never read as 500 — the truncation four portal parsers still carry."""
    assert stated_areas("celková plocha haly je 10 500 m2") == {10500.0}


def test_an_area_far_from_the_stored_one_is_not_this_unit() -> None:
    assert stated_areas("hala o výměře 900 m2, kancelář 65 m2", 65.0) == {65.0}
    assert stated_areas("sklep 4 m2, byt 60 m2", 60.0) == {60.0}


def test_stated_areas_without_a_stored_area_keeps_the_absolute_window() -> None:
    assert stated_areas("byt 60 m2 se sklepem 3 m2") == {60.0}


# --- place and window ---------------------------------------------------------------------


def _listing(**kwargs: object) -> Listing:
    base = dict(id=1, block="b")
    base.update(kwargs)
    return Listing(**base)  # type: ignore[arg-type]


def test_address_block_key_falls_back_through_the_grains() -> None:
    assert address_block_key(_listing(location=Location(ruian_adm_kod=555))) == "ruian:555"
    loose = _listing(location=Location(obec_kod=9, street_key="hartigova", house_number="1116/83"))
    assert address_block_key(loose) == "addr:9:hartigova:1116/83"
    assert address_block_key(_listing(location=Location(obec_kod=9))) == "obec:9"


def test_live_end_stamp_prefers_the_sighting_over_the_detection_stamp() -> None:
    gone = _listing(last_seen_at="2026-08-12T00:00:00+00:00",
                    inactive_at="2026-09-08T00:00:00+00:00", is_active=False)
    assert live_end_stamp(gone) == "2026-08-12T00:00:00+00:00"
    stampless = _listing(last_seen_at=None, inactive_at="2026-09-08T00:00:00+00:00",
                         is_active=False)
    assert live_end_stamp(stampless) == "2026-09-08T00:00:00+00:00"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Orientace obou pokojů je na jihovýchod a na oknech jsou žaluzie.", {"jihovychod"}),
        ("Orientace je na východ, na oknech s trojskly jsou žaluzie.", {"vychod"}),
        # A through-flat names two; the clause is read whole so neither wins.
        ("Byt je orientovaný na jih a západ, díky čemuž je prosvětlený.", {"jih", "zapad"}),
        # "na dvě světové strany" names no direction at all.
        ("Díky orientaci na dvě světové strany byt přirozeně větrá.", set()),
        # An abbreviation is a coin flip; the parser abstains.
        ("Nový byt 2+kk, orientovaný na J/Z, vybavený kuchyňskou linkou.", set()),
        # No keyword, no fact: a body names the direction of the park too.
        ("Sever (ložnice) je vaše oáza klidu, Jih patří obývacímu pokoji.", set()),
        ("", set()),
    ],
)
def test_orientations_reads_only_what_the_body_states_as_this_unit_s(
    text: str, expected: set[str]
) -> None:
    assert orientations(text) == expected


def test_orientations_prefers_the_longer_compass_stem() -> None:
    """`jihovýchod` contains `východ`; reading the short stem would merge two flats."""
    assert orientations("Byt je orientován na jihovýchod.") == {"jihovychod"}
    assert orientations("Byt je orientován na severozápad.") == {"severozapad"}


# E968: the direction written as an ADJECTIVE in front of the noun. Rezidence Medkova 16900 and
# 113982 are one template apart by that word; U Stromečku 62766 writes its south that way.
MEDKOVA_SW = ("Byt se nachází v 1. nadzemním podlaží, má jihozápadní orientaci a bude připraven "
              "k nastěhování cca v 6/2027.")
MEDKOVA_NE = ("Byt se nachází v 1. nadzemním podlaží, má severovýchodní orientaci a bude "
              "připraven k nastěhování cca v 6/2027.")
STROMECEK_SOUTH = ("a samostatnou ložnicí o ploše 12,1 m². Jižní orientace a velká okna zajišťují "
                   "dostatek denního světla po celý den.")


@pytest.mark.parametrize(
    "text, expected",
    [
        (MEDKOVA_SW, {"jihozapad"}),
        (MEDKOVA_NE, {"severovychod"}),
        # `jizni` starts with no stem; the one normaliser reads it as `jih`.
        (STROMECEK_SOUTH, {"jih"}),
        # Two adjectives are a through-flat, read as two so neither wins.
        ("Byt má jihozápadní a severovýchodní orientaci.", {"jihozapad", "severovychod"}),
        # An adjective that qualifies another noun is not the unit's orientation.
        ("Dům stojí na jihozápadní straně ulice, byt má orientaci do klidného dvora.", set()),
    ],
)
def test_orientations_reads_the_adjective_in_front_of_the_noun(
    text: str, expected: set[str]
) -> None:
    assert orientations(text) == expected


def test_compass_points_split_a_compound_direction() -> None:
    assert compass_points({"jihozapad"}) == {"jih", "zapad"}
    assert compass_points({"sever", "zapad"}) == {"sever", "zapad"}
    assert compass_points(set()) == frozenset()


# --- E966: a co-living room's code ---------------------------------------------------------
# Za Pohořelcem, one template of 10 m² rooms at 13,500 Kč: sreality 11944 and idnes 144190 let
# room 303-2, sreality 19252047 room 301-3, sreality 12273170 rooms 403-1 and 303-1.
ROOM_303_2 = ("Nabízíme k dlouhodobému nebo krátkodobému pronájmu zařízený pokoj č. 303-2 ve "
              "sdíleném bytu 3+1 ve skvělé lokalitě Prahy 6 Pohořelec.")
ROOM_301_3 = ("Nabízíme k dlouhodobému nebo střednědobému pronájmu (201-3, 301-3) zařízený pokoj "
              "ve sdíleném bytu 3+1. Nabízený pokoj (cca 10 m2): Jednolůžková postel s matrací. "
              "Dostupnost: Pokoj 301-3 (2. patro) Volný ihned.")
ROOMS_403_303 = ("Neplatíte provizi. Pokoj 403-1 volný od 24.8.2026. Pokoj 303-1 volný od "
                 "1.9.2026. Nabízíme k dlouhodobému nebo střednědobému pronájmu zařízený pokoj "
                 "(č. 403-1) ve sdíleném bytu 3+1.")


@pytest.mark.parametrize(
    "text, expected",
    [
        (ROOM_303_2, {"303-2"}),
        # The menu in front of the noun is not read; the room the advert names is.
        (ROOM_301_3, {"301-3"}),
        (ROOMS_403_303, {"403-1", "303-1"}),
        ("pronájmu pokoj 301-2 ve sdíleném bytu 3+1, který se nachází ve 2 patře", {"301-2"}),
    ],
)
def test_printed_unit_codes_reads_a_co_living_room_code(text: str, expected: set[str]) -> None:
    assert printed_unit_codes(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # After a dwelling noun two bare numbers joined by a hyphen are a range.
        "Hledáme byt 2-3 pokoje v Praze 6.",
        # A room list's size is not a code, and a single room number is a room inside one flat.
        "Dispozice: pokoj 1 - 12,5 m2, pokoj 2 - 14 m2, kuchyň 9 m2.",
        "Pronajmu pokoj č. 2 v bytě 3+1.",
    ],
)
def test_printed_unit_codes_reads_no_range_and_no_room_number(text: str) -> None:
    assert printed_unit_codes(text) == frozenset()


# --- E967: the NP noun is the explicit one ---------------------------------------------------


def test_the_np_noun_is_read_numbered_and_worded() -> None:
    """náměstí Jiřího z Poděbrad: sreality 14936 in May against idnes 19241736 in October."""
    may = "nabídli nezařízený byt, který se nachází ve 3 nadzemním podlaží secesního domu"
    october = "moderní stylový byt 3kk, který se nachází ve čtvrtém nadzemním podlaží secesního domu"
    assert printed_floors_by_form(may, True)["np"] == {3}
    assert printed_floors_by_form(october, True)["np"] == {4}


def test_a_bare_podlazi_is_not_the_np_noun() -> None:
    """`v prvním podlaží` is written for the ground floor and for the one above it."""
    assert printed_floors("bydlení je situováno v prvním podlaží", True) == frozenset()
    assert printed_floors("byt ve třetím podlaží domu", True) == frozenset()

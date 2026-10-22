"""D43's predicate: every fact it can read, and every way it must abstain."""

from __future__ import annotations

from pathlib import Path

import pytest

from autodedup.dataset import Listing, Location
from autodedup.indistinguishable import (
    CLUSTER,
    FACT_NAMES,
    PROMOTE,
    areas_disjoint,
    distinguishing_facts,
    indistinguishable,
)
from autodedup.settings import Settings

# The settings the real-time generation runs, for the limbs the default leaves off.
W31 = Settings.from_json(Path(__file__).resolve().parents[2] / "autodedup/settings/w31.json")


def listing(listing_id: int, **kwargs: object) -> Listing:
    fields: dict[str, object] = {
        "source": "sreality",
        "category_main": "byt",
        "category_type": "prodej",
        "disposition": "2+kk",
        "area_m2": 70.0,
        "floor": 3,
        "price": 5_000_000.0,
    }
    fields.update(kwargs)
    return Listing(id=listing_id, block="b", **fields)  # type: ignore[arg-type]


def names(a: Listing, b: Listing, feats: object = None) -> list[str]:
    return [fact.name for fact in distinguishing_facts(a, b, feats)]  # type: ignore[arg-type]


def stated(a: Listing, b: Listing, mode: str = CLUSTER) -> list[str]:
    return [fact.name for fact in distinguishing_facts(a, b, None, W31, mode)]


def test_identical_adverts_are_indistinguishable() -> None:
    assert indistinguishable(listing(1), listing(2))
    assert names(listing(1), listing(2)) == []


def test_areas_disjoint_tolerates_one_flat_measured_twice() -> None:
    assert not areas_disjoint({47.6}, {46.6})
    assert areas_disjoint({39.3}, {43.2})
    assert not areas_disjoint({50.7, 20.2}, {49.8, 20.2})
    assert not areas_disjoint(set(), {50.0})


def test_predicate_is_symmetric() -> None:
    a, b = listing(1, area_m2=70.0), listing(2, area_m2=80.0, source="bazos")
    assert names(a, b) == names(b, a)


# --- area ----------------------------------------------------------------------------------


def test_one_tape_measure_read_twice_is_not_a_difference() -> None:
    """70 vs 72 m² across two portals is one flat (W7, pair 469862 x 15290511)."""
    a = listing(1, area_m2=70.0)
    b = listing(2, area_m2=72.0, source="bazos")
    assert "area" not in names(a, b)


def test_area_beyond_the_band_is_a_difference() -> None:
    assert "area" in names(listing(1, area_m2=70.0), listing(2, area_m2=80.0))


def test_area_missing_on_one_side_is_not_a_difference() -> None:
    assert "area" not in names(listing(1, area_m2=None), listing(2, area_m2=80.0))


def test_stated_areas_that_cannot_be_the_same_unit() -> None:
    a = listing(1, area_m2=None, description="Prodej bytu o vymere 47 m2 v centru.")
    b = listing(2, area_m2=None, description="Nabizime byt 95 m2 v centru.")
    assert "stated_area" in names(a, b)


def test_stated_areas_within_the_printing_tolerance_agree() -> None:
    a = listing(1, area_m2=None, description="Byt o vymere 47,6 m2.")
    b = listing(2, area_m2=None, description="Byt o vymere 46,6 m2.")
    assert "stated_area" not in names(a, b)


# --- disposition, floors -------------------------------------------------------------------


def test_disposition_difference() -> None:
    assert "disposition" in names(listing(1), listing(2, disposition="3+kk"))


def test_land_has_no_disposition_fact() -> None:
    a = listing(1, category_main="pozemek", disposition="2+kk", floor=None)
    b = listing(2, category_main="pozemek", disposition="3+kk", floor=None)
    assert "disposition" not in names(a, b)


def test_two_floors_apart_is_a_difference_on_either_portal() -> None:
    assert "floor" in names(listing(1, floor=3), listing(2, floor=5, source="bazos"))


def test_one_floor_apart_counts_only_within_one_portal() -> None:
    same = names(listing(1, floor=3), listing(2, floor=4))
    cross = names(listing(1, floor=3), listing(2, floor=4, source="bazos"))
    assert "floor" in same
    assert "floor" not in cross


def test_total_floors_difference() -> None:
    a = listing(1, total_floors=8)
    b = listing(2, total_floors=6)
    assert "total_floors" in names(a, b)
    assert "total_floors" not in names(listing(1, total_floors=None), b)


# --- plot ----------------------------------------------------------------------------------


def test_plot_area_difference_beyond_two_percent() -> None:
    a = listing(1, category_main="dum", attrs={"estate_area": 1000.0})
    b = listing(2, category_main="dum", attrs={"estate_area": 1400.0})
    assert "plot_area" in names(a, b)


def test_plot_area_within_two_percent_agrees() -> None:
    a = listing(1, category_main="dum", attrs={"estate_area": 1000.0})
    b = listing(2, category_main="dum", attrs={"estate_area": 1010.0})
    assert "plot_area" not in names(a, b)


# --- price ---------------------------------------------------------------------------------


def test_a_modest_price_gap_counts_across_portals_but_not_within_one() -> None:
    cross = names(listing(1, price=5_000_000.0),
                  listing(2, price=6_000_000.0, source="bazos"))
    same = names(listing(1, price=5_000_000.0), listing(2, price=6_000_000.0))
    assert "price" in cross
    assert "price" not in same


def test_a_gross_price_gap_counts_within_one_portal_too() -> None:
    """9.9M against 2.5M on bazos is not one asking price at two moments."""
    assert "price" in names(listing(1, price=9_900_000.0), listing(2, price=2_495_000.0))


def test_a_small_cross_portal_price_gap_is_not_a_difference() -> None:
    a = listing(1, price=5_000_000.0)
    b = listing(2, price=5_100_000.0, source="bazos")
    assert "price" not in names(a, b)


def test_missing_price_on_one_side_is_not_a_difference() -> None:
    a = listing(1, price=None)
    b = listing(2, price=6_000_000.0, source="bazos")
    assert "price" not in names(a, b)


# --- where the advert says the unit is ------------------------------------------------------


def _at(listing_id: int, obec: int | None = None, street: str | None = None,
        ruian: int | None = None, cp: str | None = None, **kwargs: object) -> Listing:
    row = listing(listing_id, **kwargs)
    row.location = Location(obec_kod=obec, street_key=street, ruian_adm_kod=ruian,
                            house_number=cp)
    return row


def test_two_municipalities_are_two_units() -> None:
    assert "obec" in names(_at(1, obec=554782), _at(2, obec=563510))


def test_two_streets_are_two_units() -> None:
    assert "street" in names(_at(1, obec=554782, street="jandova"),
                             _at(2, obec=554782, street="nepilova"))


def test_one_street_known_on_one_side_only_says_nothing() -> None:
    assert names(_at(1, obec=554782, street="jandova"), _at(2, obec=554782)) == []


def test_two_entrances_of_one_street_are_not_a_difference() -> None:
    """A RÚIAN code and a house number differ on 4.2 % / 3.7 % of known duplicates."""
    a = _at(1, obec=554782, street="k botici", ruian=22672176, cp="1453/6")
    b = _at(2, obec=554782, street="k botici", ruian=99999999, cp="1455/8")
    assert names(a, b) == []


# --- the orientation the body prints --------------------------------------------------------


def test_two_orientations_are_two_flats() -> None:
    a = listing(1, description="Orientace obou pokoju je na jihovychod, okna s trojskly.")
    b = listing(2, description="Orientace je na vychod, okna s trojskly.")
    assert "orientation" in names(a, b)


def test_a_through_flat_sharing_a_side_abstains() -> None:
    a = listing(1, description="Diky orientaci na jih i na sever byt prirozene vetra.")
    b = listing(2, description="Orientace je na jih.")
    assert "orientation" not in names(a, b)


def test_a_through_flat_sharing_no_side_is_two_flats() -> None:
    """E968: a flat facing north and south cannot be one facing only east."""
    a = listing(1, description="Diky orientaci na jih i na sever byt prirozene vetra.")
    b = listing(2, description="Orientace je na vychod.")
    assert "orientation" in names(a, b)


# E968: Rezidence Medkova, one developer template on sreality 16900 and idnes 113982 at 33 m²,
# 1+kk, floor 0, one exact pin, promoted to one property on the absence of a fact.
def test_two_adjective_orientations_are_two_flats() -> None:
    body = ("Byt se nachází v 1. nadzemním podlaží, má {side} orientaci a bude připraven k "
            "nastěhování cca v 6/2027.")
    a = listing(16900, disposition="1+kk", area_m2=33.0, floor=0, price=7_184_632.0,
                description=body.format(side="jihozápadní"))
    b = listing(113982, source="idnes", disposition="1+kk", area_m2=33.0, floor=0,
                price=7_400_170.0, description=body.format(side="severovýchodní"))
    assert "orientation" in names(a, b)
    assert "orientation" in stated(a, b, PROMOTE)


# E968: U Stromečku, sreality 18195 (`Byt 3A`, north and west) against 62766 (south), 44 m²
# 2+kk on floor 2 both, one broker, 49 days on sale together.
def test_north_and_west_against_south_is_two_flats() -> None:
    a = listing(18195, area_m2=44.0, floor=2, price=8_091_000.0,
                description="Byt 3A je dispozičně členěn na 2+kk má užitnou plochu (44 m2) a "
                            "je orientován na sever a západ, což zajišťuje vyvážené denní světlo.")
    b = listing(62766, area_m2=44.0, floor=2, price=None,
                description="a samostatnou ložnicí o ploše 12,1 m². Jižní orientace a velká "
                            "okna zajišťují dostatek denního světla po celý den.")
    assert "orientation" in names(a, b)
    assert "orientation" in stated(a, b, PROMOTE)


def test_a_room_adjective_does_not_hide_the_one_direction() -> None:
    """The adjective in front of `orientace` adds `jih` beside `jihovýchod`; the wider stem is
    the body's one direction, and it is not `východ`."""
    a = listing(1, description="Jižní orientace obýváku, ložnice orientována na jihovýchod.")
    b = listing(2, source="idnes", description="Byt je orientován na východ.")
    assert "orientation" in names(a, b)
    c = listing(3, source="idnes", description="Byt je orientován na jih.")
    assert "orientation" not in names(a, c)


@pytest.mark.parametrize(
    "body_a, body_b",
    [
        ("Byt je situován v jihozápadní části Prahy, nedaleko metra.",
         "Byt je orientován na sever a východ, je tedy příjemně světlý."),
        ("Byt je situován v západní části Brna.",
         "Díky orientaci na jih i na sever byt přirozeně větrá."),
        ("Byt je orientován do dvora, z obývacího pokoje je východ na balkon.",
         "Orientace bytu na jih a západ."),
        ("Orientační cena za pronájem, východ na terasu z obývacího pokoje.",
         "Orientace na západ a jih."),
        ("Obývací pokoj s kuchyní má západní orientaci.",
         "Ložnice má východní orientaci do klidného dvora."),
        ("Byt má severo-jižní orientaci.", "Byt je orientován na sever."),
    ],
)
def test_a_location_an_exit_or_a_room_is_no_orientation(body_a: str, body_b: str) -> None:
    a = listing(1, description=body_a)
    b = listing(2, source="idnes", description=body_b)
    assert "orientation" not in names(a, b)


def test_a_garden_or_a_plot_facing_is_no_orientation() -> None:
    house = listing(1, category_main="dum", disposition=None,
                    description="Rodinný dům, zahrada s jižní orientací.")
    other = listing(2, source="idnes", category_main="dum", disposition=None,
                    description="Rodinný dům je orientován na východ.")
    assert "orientation" not in names(house, other)
    plot = listing(3, category_main="pozemek", disposition=None,
                   description="Pozemek s jižní orientací, mírně svažitý.")
    sloped = listing(4, source="idnes", category_main="pozemek", disposition=None,
                     description="Pozemek je orientován na východ a sever.")
    assert "orientation" not in names(plot, sloped)


def test_an_abbreviated_orientation_is_not_read() -> None:
    a = listing(1, description="Novy byt 2+kk, orientovany na J/Z, vybaveny kuchynskou linkou.")
    b = listing(2, description="Byt s vyhledem orientovanym na jihozapad.")
    assert "orientation" not in names(a, b)


def test_the_same_orientation_is_not_a_difference() -> None:
    a = listing(1, description="Byt je orientovany na jihozapad.")
    b = listing(2, description="Orientace bytu je na jihozapad.")
    assert "orientation" not in names(a, b)


# --- unit designator (E61) -----------------------------------------------------------------


def _at_block(listing_id: int, text: str) -> Listing:
    row = listing(listing_id, description=text)
    row.location = Location(ruian_adm_kod=21778370)
    return row


def test_two_unit_numbers_at_one_address_block() -> None:
    a = _at_block(1, "Prodej bytu (c.3) v novostavbe.")
    b = _at_block(2, "Prodej bytu (c.5) v novostavbe.")
    assert "unit_designator" in names(a, b)


def test_unit_numbers_in_different_blocks_do_not_conflict() -> None:
    a = _at_block(1, "Prodej bytu (c.3).")
    b = listing(2, description="Prodej bytu (c.5).")
    b.location = Location(ruian_adm_kod=99999999)
    assert "unit_designator" not in names(a, b)


def test_a_price_list_of_several_units_conflicts_with_nothing() -> None:
    a = _at_block(1, "K dispozici jednotky c. 3, jednotky c. 5 a jednotky c. 7.")
    b = _at_block(2, "Prodej bytu (c.9).")
    assert "unit_designator" not in names(a, b)


# --- E966: a co-living room's code -----------------------------------------------------------
# Za Pohořelcem: 10 m² rooms of 3+1 flats in one house, one broker template at 13,500 Kč.
ROOM_303_2 = ("Nabízíme k dlouhodobému nebo krátkodobému pronájmu zařízený pokoj č. 303-2 ve "
              "sdíleném bytu 3+1 ve skvělé lokalitě Prahy 6 Pohořelec.")
ROOM_301_3 = ("Nabízíme k dlouhodobému nebo střednědobému pronájmu (201-3, 301-3) zařízený pokoj "
              "ve sdíleném bytu 3+1. Dostupnost: Pokoj 301-3 (2. patro) Volný ihned.")
ROOMS_403_303 = ("Pokoj 403-1 volný od 24.8.2026. Pokoj 303-1 volný od 1.9.2026. Nabízíme k "
                 "dlouhodobému nebo střednědobému pronájmu zařízený pokoj (č. 403-1) ve sdíleném "
                 "bytu 3+1.")


def room(listing_id: int, body: str, source: str = "sreality") -> Listing:
    return listing(listing_id, source=source, category_type="pronajem", disposition=None,
                   area_m2=10.0, floor=1, price=13_500.0, description=body)


def test_two_room_codes_are_two_rooms() -> None:
    """sreality 11944 (303-2) against 19252047 (301-3), promoted by D43 on agree:2."""
    for mode in (PROMOTE, CLUSTER):
        assert "unit_code" in stated(room(11944, ROOM_303_2), room(19252047, ROOM_301_3), mode)
    assert "unit_code" in stated(room(11944, ROOM_303_2), room(12273170, ROOMS_403_303))


def test_one_room_on_two_portals_is_one_room() -> None:
    """sreality 11944 and idnes 144190 both let room 303-2; 12273170 / 12298064 both list
    403-1 and 303-1."""
    assert "unit_code" not in stated(room(11944, ROOM_303_2),
                                     room(144190, ROOM_303_2, source="idnes"))
    assert "unit_code" not in stated(room(12273170, ROOMS_403_303),
                                     room(12298064, ROOMS_403_303, source="idnes"))


def test_a_room_price_list_reposted_at_new_prices_is_one_room() -> None:
    first = room(1, "Volné pokoje: Pokoj 1 - 12 000 Kč, Pokoj 2 - 11 500 Kč.")
    again = room(2, "Volné pokoje: Pokoj 1 - 11 000 Kč, Pokoj 2 - 10 500 Kč.", source="idnes")
    assert "unit_code" not in stated(first, again)


def test_a_subset_of_room_codes_is_not_a_conflict() -> None:
    only = room(2, "Dostupnost: Pokoj 403-1 (4. patro) od 24.8.2026")
    assert "unit_code" not in stated(room(12273170, ROOMS_403_303), only)


# --- E969: one storey apart across two portals is the vocabulary, in every noun ------------
# The operator's ruling of 2026-10-19, withdrawing E967: `2. NP` may be `3. NP` through data
# quality alone. náměstí Jiřího z Poděbrad: one agency's 93 m² 3+kk `ve 3 nadzemním podlaží`
# (sreality 14936, May) and `ve čtvrtém nadzemním podlaží` (idnes 19241736, October); every
# stored floor column reads 3. The prices are levelled so the storey is the only thing to read.
NP_3 = ("Rádi bychom Vám nabídli nezařízený byt, který se nachází ve 3 nadzemním podlaží "
        "secesního domu s výtahem.")
NP_4 = ("Nabízíme k pronájmu moderní stylový byt 3kk, který se nachází ve čtvrtém nadzemním "
        "podlaží secesního domu s výtahem.")
PATRO_3 = ("Nabízíme Vám k pronájmu velmi moderní a luxusní byt po rekonstrukci na náměstí Jiřího "
           "z Poděbrad, 3+kk, 93m2, ve 3. patře v domě s výtahem.")
PATRO_4_WORDED = ("Nabízíme k pronájmu byt 3+kk o užitné ploše 93 m² ve čtvrtém patře historického "
                  "činžovního domu s výtahem.")
STOREY_FACTS = {"prose_floor", "subject_floor"}
EVERY_STOREY_FACT = STOREY_FACTS | {"floor", "offered_storey", "storey_word"}


def flat(listing_id: int, body: str, source: str = "sreality", first: str = "2026-05-01",
         last: str = "2026-06-01", **fields: object) -> Listing:
    return listing(listing_id, source=source, category_type="pronajem", disposition="3+kk",
                   area_m2=93.0, floor=3, price=44_800.0, description=body,
                   first_seen_at=f"{first}T00:00:00+00:00", last_seen_at=f"{last}T00:00:00+00:00",
                   **fields)


def placed(listing_id: int, storey: str, source: str, **fields: object) -> Listing:
    return listing(listing_id, floor=None, source=source,
                   description=f"Byt se nachází {storey} cihlového domu.", **fields)


@pytest.mark.parametrize(
    "a, b",
    [
        # The NP noun on both sides, numbered against worded, then numbered on both.
        (flat(14936, NP_3, broker_key="phone-a"),
         flat(19241736, NP_4, source="idnes", first="2026-10-01", last="2026-10-18")),
        (placed(1, "ve 2. NP", "idnes"), placed(2, "ve 3. NP", "bezrealitky")),
        # The patro noun.
        (flat(19241633, PATRO_3, first="2026-10-01", last="2026-10-18"),
         flat(19250437, PATRO_4_WORDED, source="idnes", first="2026-10-01", last="2026-10-18")),
        # The ground floor in words against the first floor above it, numbered and worded.
        (placed(1, "v přízemí", "idnes"), placed(2, "v 1. patře", "bezrealitky")),
        (placed(1, "v přízemí", "idnes"), placed(2, "v prvním patře", "bezrealitky")),
    ],
)
def test_one_storey_apart_across_two_portals_is_no_fact(a: Listing, b: Listing) -> None:
    for mode in (PROMOTE, CLUSTER):
        assert not EVERY_STOREY_FACT & set(stated(a, b, mode))


def test_one_storey_apart_inside_one_portal_keeps_its_rule() -> None:
    """Not ruled on 2026-10-19: inside one portal (E180, scope `source`) or one feed (E145) a
    one-storey gap stays a fact for two adverts live together, two agencies included, and the
    sequential excuse still reads none for two that never were (14936 in May, 19241633 in
    October, `3. NP` against `3. patro`)."""
    live = {"first_seen_at": "2026-05-01T00:00:00+00:00",
            "last_seen_at": "2026-06-01T00:00:00+00:00"}
    for lower, upper in (("ve 2. NP", "ve 3. NP"), ("v přízemí", "v 1. patře")):
        a = placed(1, lower, "sreality", broker_key="k1", **live)
        b = placed(2, upper, "sreality", broker_key="k2", **live)
        for mode in (PROMOTE, CLUSTER):
            assert EVERY_STOREY_FACT & set(stated(a, b, mode))
    a = flat(14936, NP_3, broker_key="phone-a")
    b = flat(19241633, PATRO_3, first="2026-10-01", last="2026-10-18", broker_key="identity-b")
    for mode in (PROMOTE, CLUSTER):
        assert not EVERY_STOREY_FACT & set(stated(a, b, mode))


def test_the_ground_floor_against_a_storey_two_up_or_an_unnumbered_one_is_a_fact() -> None:
    for upper in ("ve 2. patře", "ve druhém patře", "v patře"):
        a, b = placed(1, "v přízemí", "idnes"), placed(2, upper, "bezrealitky")
        for mode in (PROMOTE, CLUSTER):
            assert "storey_word" in stated(a, b, mode)


def test_two_storeys_apart_in_the_np_noun_is_a_fact() -> None:
    a, b = placed(1, "ve 2. NP", "idnes"), placed(2, "ve 4. NP", "bezrealitky")
    for mode in (PROMOTE, CLUSTER):
        assert STOREY_FACTS <= set(stated(a, b, mode))


def test_a_maisonette_range_meets_its_entry_storey() -> None:
    """E967's range reader, kept: read for its last storey alone, `2.-3. NP` is one storey
    from `2. NP` inside one feed, which is a fact there."""
    for storeys in ("ve 2.-3. NP", "ve 2. a 3. NP"):
        a = placed(1, storeys, "sreality", broker_key="b1")
        b = placed(2, "ve 2. NP", "sreality", broker_key="b1")
        for mode in (PROMOTE, CLUSTER):
            assert not STOREY_FACTS & set(stated(a, b, mode))


def test_a_bare_podlazi_keeps_its_two_storey_reading() -> None:
    """A bare worded `podlaží` is read on the NP scale: two storeys apart it is a fact as it
    always was, one apart it is the vocabulary."""
    def pair(first: str, second: str) -> tuple[Listing, Listing]:
        return (listing(1, floor=None, description=f"Byt 2+kk se nachází {first} cihlového domu."),
                listing(2, floor=None, source="idnes",
                        description=f"Byt 2+kk se nachází {second} cihlového domu."))

    for mode in (PROMOTE, CLUSTER):
        assert STOREY_FACTS <= set(stated(*pair("ve třetím podlaží", "v prvním podlaží"), mode))
        assert STOREY_FACTS <= set(stated(*pair("ve druhém podlaží", "ve čtvrtém podlaží"), mode))
        assert STOREY_FACTS <= set(stated(*pair("ve třetím podlaží", "v 1. NP"), mode))
        assert not STOREY_FACTS & set(stated(*pair("v prvním podlaží", "ve druhém podlaží"), mode))
        assert not STOREY_FACTS & set(stated(*pair("ve druhém podlaží", "ve 3. NP"), mode))


# --- image facts, which come only from the engine's own feature row --------------------------


def _feats(**kwargs: float) -> dict[str, tuple[float, bool]]:
    return {name: (value, True) for name, value in kwargs.items()}


def test_no_feature_row_means_no_image_fact() -> None:
    assert names(listing(1), listing(2), None) == []


def test_a_floorplan_conflict_alone_is_refused() -> None:
    feats = _feats(floorplan_conflict=1.0, tag_room_clip_min2=0.99)
    assert "floorplan" not in names(listing(1), listing(2), feats)


def test_a_floorplan_conflict_with_a_weak_room_match_counts() -> None:
    feats = _feats(floorplan_conflict=1.0, tag_room_clip_min2=0.80)
    assert "floorplan" in names(listing(1), listing(2), feats)


def test_a_weak_room_match_alone_is_no_difference() -> None:
    """E929: a room tag is never a fact."""
    feats = _feats(tag_room_clip_min2=0.80)
    assert names(listing(1), listing(2), feats) == []


def test_an_absent_feature_slot_is_not_a_difference() -> None:
    feats = {"tag_room_clip_min2": (0.0, False), "floorplan_conflict": (1.0, False)}
    assert names(listing(1), listing(2), feats) == []


# --- the standing rulings -------------------------------------------------------------------


def test_deal_type_and_category_are_always_differences() -> None:
    a = listing(1, category_type="prodej")
    b = listing(2, category_type="pronajem")
    assert "category_type" in names(a, b)
    assert "category_main" in names(listing(1, category_main="byt"),
                                    listing(2, category_main="dum"))


def test_the_sanctioned_cross_type_is_not_a_difference() -> None:
    """dům <-> komerční is a rule #15 cross-type; the operator confirmed 8."""
    a = listing(1, category_main="dum", disposition=None, floor=None)
    b = listing(2, category_main="komercni", disposition=None, floor=None)
    assert "category_main" not in names(a, b)


def test_land_with_a_house_or_commercial_is_not_a_category_difference() -> None:
    """E935: pozemek <-> dům and pozemek <-> komerční are rule #15 cross-types too; a flat is
    not."""
    land = listing(1, category_main="pozemek", disposition=None, floor=None)
    for other in ("dum", "komercni"):
        assert "category_main" not in names(land, listing(2, category_main=other,
                                                          disposition=None, floor=None))
    assert "category_main" in names(land, listing(3, category_main="byt",
                                                  disposition=None, floor=None))


def test_a_flat_with_a_commercial_unit_is_not_a_category_difference_with_a_house_it_is() -> None:
    """E938: byt <-> komerční is a rule #15 pair; the relation is not transitive, so the
    commercial unit that meets both a flat and a house leaves the flat and the house apart."""
    flat = listing(1, category_main="byt", disposition=None, floor=None)
    commercial = listing(2, category_main="komercni", disposition=None, floor=None)
    house = listing(3, category_main="dum", disposition=None, floor=None)
    assert "category_main" not in names(flat, commercial)
    assert "category_main" not in names(commercial, house)
    assert "category_main" in names(flat, house)


# --- contract ---------------------------------------------------------------------------------


def test_every_returned_name_is_declared() -> None:
    a = listing(1, area_m2=70.0, floor=3, total_floors=8, price=5_000_000.0,
                category_type="prodej", category_main="byt", disposition="2+kk",
                description="Byt (c.3) o vymere 47 m2.", attrs={"estate_area": 1000.0})
    a.location = Location(ruian_adm_kod=21778370)
    b = listing(2, area_m2=95.0, floor=7, total_floors=6, price=9_000_000.0, source="bazos",
                category_type="pronajem", category_main="komercni", disposition="3+kk",
                description="Byt (c.9) o vymere 95 m2.", attrs={"estate_area": 2000.0})
    b.location = Location(ruian_adm_kod=21778370)
    feats = _feats(floorplan_conflict=1.0, tag_room_clip_min2=0.5)
    found = names(a, b, feats)
    assert set(found) <= set(FACT_NAMES)
    assert len(found) == len(set(found))
    assert not indistinguishable(a, b, feats)


@pytest.mark.parametrize("name", FACT_NAMES)
def test_fact_names_are_stable_strings(name: str) -> None:
    assert name and name.islower()

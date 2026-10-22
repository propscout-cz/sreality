"""W32w — witnessed columns: a portal column is a stated fact until the ad's own text
contradicts or composes it. Per limb a synthetic pair W31 refuses and W32w admits, read through
the pair veto AND the D43 limbs (gate and cluster), the stated-different pairs W32w still
refuses, each limb pinned to its own dial, and parity: with every dial off nothing moves.

W33m (the code under every W row): W1 and W2 are monotone and read two flats only. A reading
excuses a column difference and never creates one, so over random flat pairs the candidate's
vetoes and D43 facts are a subset of W31's, a house, commercial or land pair reads as W31, and
the four defect shapes the w32w_no5 replay lost read as W31."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from autodedup import text_facts
from autodedup.dataset import Listing, Location
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.guards import cluster_invariants_ok, pair_veto
from autodedup.incremental import FpRow, GuardRow, _GuardLookup, fp_digest, guard_row
from autodedup.incremental_lane import named_config, pass_config
from autodedup.incremental_store import MemoryStore
from autodedup.indistinguishable import CLUSTER, GATE, PROMOTE, distinguishing_facts
from autodedup.settings import Settings
from autodedup.text_facts import (
    printed_basement_storeys,
    printed_dispositions,
    unit_area_figures,
    worded_storeys,
)

SETTINGS_DIR = Path(__file__).resolve().parents[2] / "autodedup" / "settings"
W31 = Settings.from_json(SETTINGS_DIR / "w31.json")
W32W = Settings.from_json(SETTINGS_DIR / "w32w.json")
W33M = Settings.from_json(SETTINGS_DIR / "w33m.json")
WITNESS_ROWS = pytest.mark.parametrize("row", [W32W, W33M], ids=["w32w", "w33m"])
DIALS = ("witness_area", "witness_floor", "witness_total_floors", "witness_street",
         "witness_disposition")


def ad(listing_id: int, description: str | None = None, **kwargs: Any) -> Listing:
    fields: dict[str, Any] = {"source": "sreality", "category_main": "byt",
                              "category_type": "prodej"}
    fields.update(kwargs)
    return Listing(id=listing_id, block="b", description=description, **fields)


def fp(listing: Listing, settings: Settings) -> Fingerprint:
    return build_fingerprint(listing, [], settings)


def veto(a: Listing, b: Listing, settings: Settings) -> str | None:
    return pair_veto(fp(a, settings), fp(b, settings), settings)


def facts(a: Listing, b: Listing, settings: Settings, mode: str = GATE) -> list[str]:
    return [fact.name for fact in distinguishing_facts(a, b, None, settings, mode)]


def d43(a: Listing, b: Listing, settings: Settings) -> set[str]:
    """The facts D43 reads at the gate and at cluster grain, together."""
    return set(facts(a, b, settings, GATE)) | set(facts(a, b, settings, CLUSTER))


def only(dial: str) -> Settings:
    return replace(W31, **{dial: True})


# ------------------------------------------------------------------------------- the row


def test_w32w_is_w31_with_the_five_dials_on() -> None:
    raw31 = json.loads((SETTINGS_DIR / "w31.json").read_text(encoding="utf-8"))
    raw32 = json.loads((SETTINGS_DIR / "w32w.json").read_text(encoding="utf-8"))
    assert {key: value for key, value in raw32.items() if key not in DIALS} == raw31
    assert all(raw32[dial] is True for dial in DIALS)
    assert not any(getattr(Settings(), dial) for dial in DIALS)
    assert not any(getattr(W31, dial) for dial in DIALS)
    assert replace(W31, **{dial: True for dial in DIALS}) == W32W


BODY = ("Byt 2+kk o velikosti 60 m2, terasa o ploše 39,4 m2, v 6. NP, v suterénu sklep, "
        "ulice Kolbenova.")


def test_with_every_dial_off_the_fingerprint_reads_nothing_new() -> None:
    readers = [text_facts._unit_area_figures, text_facts._printed_basement_storeys,
               text_facts._worded_storeys, text_facts._printed_dispositions]
    for reader in readers:
        reader.cache_clear()
    row = fp(ad(1, BODY, area_m2=60.0, disposition="3+kk", floor=5), W31)
    assert all(reader.cache_info().currsize == 0 for reader in readers)
    assert (row.witnessed, row.printed_unit_area, row.printed_accessory_areas,
            row.printed_storeys, row.worded_storeys, row.printed_disposition) == (
        False, None, (), (), (), None)
    assert guard_row(row) == GuardRow(1, "byt", "prodej", 60.0, "3+kk", 5)
    on = fp(ad(1, BODY, area_m2=60.0, disposition="3+kk", floor=5), W32W)
    assert on.witnessed and on.printed_unit_area == 60.0
    assert on.printed_accessory_areas == (39.4,)
    assert on.printed_storeys == (("np", (6,)),)
    assert on.printed_disposition == "2+kk"


def test_the_digest_moves_only_with_a_reading() -> None:
    listing = ad(1, BODY, area_m2=60.0, disposition="3+kk", floor=5)
    silent = ad(2, "Byt k prodeji.", area_m2=60.0)
    assert fp_digest(fp(silent, W31), []) == fp_digest(fp(silent, W32W), [])
    assert fp_digest(fp(listing, W31), []) != fp_digest(fp(listing, W32W), [])


# ------------------------------------------------------------------------------- W1 area

TERRACE = "Byt 2+kk o velikosti 60 m2, terasa o ploše 39,4 m2, v prvním nadzemním podlaží."
NET = "Byt 2+1 s celkovou plochou 60 m², z toho jsou 4m² balkon a 2m² sklep."


@WITNESS_ROWS
def test_w1_a_column_that_adds_the_printed_terrace_is_one_area(row: Settings) -> None:
    """557627: realitymix files 100 = 60 + 39,4 terasa; both bodies print the 60."""
    a = ad(1, TERRACE, source="ceskereality", area_m2=60.0)
    b = ad(2, TERRACE, source="realitymix", area_m2=100.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, row) is None
    assert "area" not in d43(a, b, row)


@WITNESS_ROWS
def test_w1_a_column_net_of_its_own_printed_extras_composes(row: Settings) -> None:
    """71409: 54 = 60 - 4 balkon - 2 sklep, the 54 side's own body stating all three."""
    a = ad(1, NET, area_m2=54.0)
    b = ad(2, "Rohový byt 2+1 ve druhém patře, okna na západ.", source="idnes", area_m2=60.0)
    assert veto(a, b, W31) == "area"
    assert "area" in facts(a, b, W31)
    assert veto(a, b, row) is None
    assert "area" not in d43(a, b, row)


@WITNESS_ROWS
def test_w1_one_text_under_two_columns_is_read_by_its_figure(row: Settings) -> None:
    """71409 as it runs: both bodies print `60 m², z toho 4m² balkon a 2m² sklep`; the shared
    extras void the composition and the 54 side's figures hold the 60 its own text states."""
    a = ad(1, NET, area_m2=54.0)
    b = ad(2, NET, source="idnes", area_m2=60.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, row) is None
    assert "area" not in d43(a, b, row)


def test_w1_two_printed_areas_apart_stay_apart() -> None:
    a = ad(1, "Byt o užitné ploše 40 m2 ve 2. NP.", area_m2=40.0)
    b = ad(2, "Byt o užitné ploše 60 m2 ve 2. NP.", area_m2=60.0)
    assert veto(a, b, W32W) == "area"
    assert "area" in d43(a, b, W32W)


def test_w1_a_price_list_or_a_two_unit_template_witnesses_nothing() -> None:
    menu = ("Nabízíme byty v projektu. Byt 1+kk o ploše 40 m2 v přízemí domu. Dále byt 2+kk o "
            "ploše 55 m2 ve druhém patře. Také byt 3+kk o ploše 72 m2 v posledním patře.")
    a = ad(1, menu, area_m2=40.0)
    b = ad(2, "Byt 3+kk v novostavbě.", area_m2=72.0)
    assert fp(a, W32W).printed_unit_area is None
    assert veto(a, b, W32W) == "area"
    pair = ("Pronajmeme dva byty 2+kk v cihlovem dome v Plzni: mensi o velikosti 45 m2 a vetsi "
            "o velikosti 52 m2. Najem 15 000 Kc mesicne")
    c = ad(3, pair, category_type="pronajem", area_m2=45.0, disposition="2+kk")
    d = ad(4, pair, category_type="pronajem", area_m2=52.0, disposition="2+kk")
    assert veto(c, d, W32W) == "area"
    assert d43(c, d, W32W) == d43(c, d, W31)


def test_w1_a_house_template_of_three_units_witnesses_nothing() -> None:
    """A house body is not read (W33m): its columns are W31's in the veto and at D43."""
    body = ("Rodinný dům se třemi byty: v přízemí 68 m2, v patře 75 m2 a v podkroví 81 m2, "
            "cena za každý.")
    a = ad(1, body, category_main="dum", area_m2=68.0)
    b = ad(2, body, category_main="dum", area_m2=81.0)
    assert fp(a, W32W).printed_unit_area is None
    assert veto(a, b, W32W) == veto(a, b, W31) == "area"
    assert d43(a, b, W32W) == d43(a, b, W31)


def test_w1_an_extra_never_joins_one_column_to_the_other() -> None:
    """No body states the unit's area: 60 + the 20 m2 terrace hitting 80 is a coincidence."""
    body = "Byt 3+kk v cihlovém domě s terasou o velikosti 20 m2, sklep, výtah. Cena 6 500 000 Kč."
    a, b = ad(1, body, area_m2=60.0), ad(2, body, area_m2=80.0)
    assert veto(a, b, W32W) == "area"
    assert d43(a, b, W32W) == d43(a, b, W31)


def test_w1_two_bodies_stating_the_same_cellar_beside_two_areas_stay_apart() -> None:
    a = ad(1, "Byt, užitná plocha 40 m2, k bytu sklep 4 m2.", area_m2=40.0)
    b = ad(2, "Byt, užitná plocha 44 m2, k bytu sklep 4 m2.", area_m2=44.0)
    assert veto(a, b, W32W) == "area"
    c = ad(3, "Byt, užitná plocha 40 m2, k bytu sklep 4 m2.", area_m2=44.0)
    d = ad(4, "Byt, užitná plocha 44 m2, k bytu sklep 4 m2.", area_m2=48.0)
    assert veto(c, d, W32W) == "area"


@WITNESS_ROWS
def test_w1_e280_reads_as_w31(row: Settings) -> None:
    """W33m: E280 is W31's under every W row, so no printed-figure test adds an area fact."""
    body = "Byt 3+kk s terasou o ploše 20 m2 v klidné lokalitě."
    a, b = ad(1, body, area_m2=60.0), ad(2, body, area_m2=80.0)
    assert "area" not in facts(a, b, W31)
    for mode in (GATE, CLUSTER, PROMOTE):
        assert facts(a, b, row, mode) == facts(a, b, W31, mode)


def test_w1_reads_the_unit_figure_by_its_size_word() -> None:
    assert unit_area_figures("Byt o ploše 54 m2 s 12 m2 terasou.") == frozenset({(54.0, 0)})
    assert unit_area_figures("Byt 2+kk 54 m2, 25 m2 velký obývák") == frozenset()
    assert unit_area_figures("Hala o rozloze 500 m2 a kanceláře o velikosti 45 m2") == frozenset()
    assert unit_area_figures(NET) == frozenset({(60.0, 0)})


@WITNESS_ROWS
def test_w1_the_cluster_spread_reads_the_same_witnesses(row: Settings) -> None:
    """17972: the bezrealitky 44 is `plochu 40 m2 a cca 4 m2 sklep`."""
    a = ad(1, "Ateliér o celkové ploše 40 m2, klidný vnitroblok.", area_m2=40.0)
    b = ad(2, "Ateliér má plochu 40 m2 a cca 4 m2 sklep.", source="bezrealitky", area_m2=44.0)
    assert cluster_invariants_ok([fp(a, W31), fp(b, W31)], W31) == "area_spread"
    assert cluster_invariants_ok([fp(a, row), fp(b, row)], row) is None
    c = ad(3, "Ateliér o užitné ploše 60 m2.", area_m2=60.0)
    assert cluster_invariants_ok([fp(a, row), fp(c, row)], row) == "area_spread"


def test_w1_a_silent_column_composed_of_the_other_bodys_figure_and_extra() -> None:
    """17972 the other way: a silent 44 against a body stating `plochu 40 m2 a 4 m2 sklep`."""
    a = ad(1, "Ateliér v klidném vnitrobloku, výtah.", area_m2=44.0)
    b = ad(2, "Ateliér má plochu 40 m2 a cca 4 m2 sklep.", source="bezrealitky", area_m2=40.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, W33M) is None
    assert cluster_invariants_ok([fp(a, W33M), fp(b, W33M)], W33M) is None


# ------------------------------------------------------------------------------ W2 floor


@WITNESS_ROWS
def test_w2_two_bodies_printing_one_storey_lift_the_columns(row: Settings) -> None:
    """618930: both bodies `v 6. NP`, columns 5 and 0."""
    body = "Byt 4+1 o užitkové ploše 115,1 m, nacházející se v 6. NP, sklepní kóje."
    a = ad(1, body, floor=5)
    b = ad(2, body, source="bezrealitky", floor=0)
    assert veto(a, b, W31) == "floor"
    assert "floor" in facts(a, b, W31)
    assert veto(a, b, row) is None
    assert "floor" not in d43(a, b, row)


def test_w2_a_house_body_is_not_read() -> None:
    """W33m: storeys are read of a flat only; a house's printed storey lifts nothing."""
    body = "Rodinný dům, obytné místnosti v 6. NP."
    a = ad(1, body, category_main="dum", floor=5)
    assert fp(a, W33M).printed_storeys == ()
    assert fp(ad(2, body, floor=5), W33M).printed_storeys == (("np", (6,)),)


def test_w2_a_lone_printed_basement_reads_against_the_silent_column() -> None:
    """147111: idnes prints `(-1. PP)` under a column of 1; sreality is -1 `v polosuterénu`."""
    a = ad(1, "Byt 1+1 v klasickém cihlovém domě (-1. PP), samostatná kuchyň.", source="idnes",
           floor=1)
    b = ad(2, "Byt 1+1 v polosuterénu udržovaného činžovního domu.", floor=-1)
    assert veto(a, b, W31) == "floor"
    assert veto(a, b, W32W) is None
    assert "floor" not in d43(a, b, W32W)


def test_w2_two_bodies_stating_two_storeys_stay_apart() -> None:
    """40485: `v posledním podlaží` against `ve druhém podlaží`, columns 5 and 1."""
    a = ad(1, "Byt 2+kk v posledním podlaží pětipodlažního objektu.", floor=5)
    b = ad(2, "Byt 2+kk ve druhém podlaží pětipodlažního objektu.", floor=1)
    assert veto(a, b, W32W) == "floor"
    c = ad(3, "Byt 2+kk ve 2. NP.", floor=1)
    d = ad(4, "Byt 2+kk v 5. NP.", floor=4)
    assert veto(c, d, W32W) == "floor"


def test_w2_one_np_apart_in_one_noun_is_two_storeys() -> None:
    a = ad(1, "Byt 2+kk ve 3. NP.", floor=1)
    b = ad(2, "Byt 2+kk ve 4. NP.", floor=3)
    assert veto(a, b, W32W) == "floor"


def test_w2_a_body_naming_two_storeys_witnesses_none() -> None:
    a = ad(1, "Byt 2+kk ve 3. NP, sklep v 1. NP.", floor=2)
    b = ad(2, "Byt 2+kk v 1. NP s předzahrádkou.", floor=0)
    assert veto(a, b, W32W) == "floor"
    c = ad(3, "Byt 2+kk ve 2. NP, výtah do 5. NP.", floor=1)
    d = ad(4, "Byt 2+kk v 5. NP.", floor=4)
    assert veto(c, d, W32W) == "floor"


def test_w2_a_shared_basement_garage_does_not_join_two_storeys() -> None:
    a = ad(1, "Byt 2+kk ve 2. NP, garážové stání v 1. PP.", floor=1)
    b = ad(2, "Byt 2+kk v 5. NP, garážové stání v 1. PP.", floor=4)
    assert veto(a, b, W32W) == "floor"


def test_w2_a_cellar_or_a_garage_below_ground_is_not_the_unit() -> None:
    silent = ad(9, "Byt 1+1 k prodeji.", floor=-1)
    for body, floor in (("Byt 1+1, k bytu náleží sklep v suterénu domu.", 1),
                        ("Byt 1+1 ve 4. NP, garážové stání v 1. PP.", 3),
                        ("Byt 1+1, dům má 1 podzemní podlaží.", 1)):
        assert veto(ad(1, body, source="idnes", floor=floor), silent, W32W) == "floor"


def test_w2_a_worded_placement_or_an_unplaced_camp_keeps_the_veto() -> None:
    printer = ad(1, "Byt 2+kk ve 3. NP.", source="idnes", floor=5)
    assert veto(printer, ad(2, "Byt 2+kk na klidném místě.", floor=3), W32W) is None
    assert veto(printer, ad(3, "Byt 2+kk v přízemí.", floor=3), W32W) == "floor"
    assert veto(printer, ad(4, "Byt 2+kk na klidném místě.", source="bazos", floor=3),
                W32W) == "floor"


def test_w2_reads_storeys_below_ground_of_the_unit_only() -> None:
    assert printed_basement_storeys("Byt v cihlovém domě (-1. PP)") == frozenset({-1})
    assert printed_basement_storeys("Byt 2+kk ve 2. podzemním podlaží") == frozenset({-2})
    assert printed_basement_storeys("Byt v suterénu bytového domu") == frozenset({-1})
    assert printed_basement_storeys("garáž ve 2. podzemním podlaží domu") == frozenset()
    assert printed_basement_storeys("Byt v polosuterénu domu") == frozenset()
    assert printed_basement_storeys("Byt 3+kk ve 2. NP, dům (7 NP, 1 PP)") == frozenset()
    assert worded_storeys("Byt v polosuterénu domu") == frozenset({"semi"})
    assert worded_storeys("Byt ve zvýšeném přízemí") == frozenset({"raised"})
    assert worded_storeys("Byt v podkroví") == frozenset({"upper"})
    assert worded_storeys("Byt v patře rodinného domu") == frozenset({"upper"})


# ------------------------------------------------------------------------ W3 total_floors


def test_w3_a_one_storey_gap_between_silent_columns_is_not_a_storey_count() -> None:
    """512418: sreality 3/6 then 3/5, no body prints a height."""
    a = ad(1, "Byt 2+kk v projektu Atrium Petřiny, orientace na východ.", floor=3,
           total_floors=6)
    b = ad(2, "Byt 2+kk v projektu Atrium Petřiny, orientace na východ.", floor=3,
           total_floors=5)
    assert "total_floors" in facts(a, b, W31)
    assert "total_floors" not in d43(a, b, W32W)


def test_w3_two_storeys_between_silent_columns_stay_a_fact() -> None:
    body = ("Prodej bytu 2+kk 54 m2 ve 3. NP panelovém domě po revitalizaci, Praha 13 "
            "Stodůlky.")
    a = ad(1, body, floor=2, total_floors=4, area_m2=54.0)
    b = ad(2, body, source="idnes", floor=2, total_floors=12, area_m2=54.0)
    assert "total_floors" in facts(a, b, W32W)


def test_w3_a_printed_count_against_the_other_side_is_still_a_fact() -> None:
    a = ad(1, "Byt ve 3. podlaží ze 6, orientace na východ.", floor=2, total_floors=6)
    b = ad(2, "Byt v domě s pěti nadzemními podlažími.", floor=2, total_floors=5)
    assert "total_floors" in facts(a, b, W32W)
    c = ad(3, "Byt 2+1 ve 2. podlaží ze 4.", floor=1, total_floors=4)
    d = ad(4, "Byt 2+1 ve zvýšeném přízemí.", floor=1, total_floors=5)
    assert "total_floors" in facts(c, d, W32W)


# ------------------------------------------------------------------------------ W4 street


def _on(listing: Listing, street: str) -> Listing:
    listing.location = Location(obec_kod=554782, street_key=street)
    return listing


REPOST = ("Nabízíme k prodeji prostorný byt 4+1 o výměře 112 m² s rohovou terasou o velikosti "
          "29,5 m² v oblíbeném komplexu z roku 2002 v klidné části Vysočan u parku. Byt tvoří "
          "obývací pokoj s kuchyňským koutem, tři samostatné pokoje, koupelna s rohovou vanou a "
          "samostatné WC. K bytu náleží sklep a samostatná garáž ve druhém podzemním podlaží. "
          "Výborná dostupnost do centra metrem i tramvají, v okolí veškerá občanská vybavenost, "
          "školy, obchody a sportoviště. Provizi nehradíte. Doporučujeme.")


def test_w4_one_text_of_one_source_that_names_no_street_is_not_stated() -> None:
    """335094: one sreality text twice, no street printed, two resolver streets."""
    a = _on(ad(1, REPOST, area_m2=112.0, floor=3), "rubesova")
    b = _on(ad(2, REPOST, area_m2=112.0, floor=3), "kolbenova")
    assert "street" in facts(a, b, W31)
    assert "street" not in d43(a, b, W32W)


def test_w4_two_portals_or_a_printed_street_keep_the_resolver() -> None:
    a = _on(ad(1, REPOST, area_m2=112.0), "rubesova")
    b = _on(ad(2, REPOST, source="idnes", area_m2=112.0), "kolbenova")
    assert "street" in facts(a, b, W32W)
    named = REPOST + " Adresa: ulice Kolbenova."
    c = _on(ad(3, named, area_m2=112.0), "rubesova")
    d = _on(ad(4, named, area_m2=112.0), "kolbenova")
    assert "street" in facts(c, d, W32W)


def test_w4_agreeing_prose_never_excuses_the_resolver() -> None:
    a = _on(ad(1, "Byt 2+kk 54 m2 v ulici Pod Lesem, Praha 4.", area_m2=54.0), "pod lesem")
    b = _on(ad(2, "Byt 2+kk 54 m2 v ulici Pod Lipami, Praha 4.", area_m2=54.0), "pod lipami")
    assert "street" in facts(a, b, W32W)
    c = _on(ad(3, "Byt v ulici Vinohradská, kousek od náměstí Míru.", area_m2=54.0),
            "vinohradska")
    d = _on(ad(4, "Byt v ulici Korunní, kousek od náměstí Míru.", area_m2=54.0), "korunni")
    assert "street" in facts(c, d, W32W)
    e = _on(ad(5, "Byt 2+kk v ulici Vinohradská, Praha 2.", area_m2=54.0), "vinohradska")
    f = _on(ad(6, "Byt 2+kk k prodeji, Praha 2.", area_m2=54.0), "korunni")
    assert "street" in facts(e, f, W32W)


def test_w4_two_printed_streets_apart_stay_apart() -> None:
    a = _on(ad(1, "Byt 2+kk v ulici Litovelská, Olomouc.", area_m2=50.0), "litovelska")
    b = _on(ad(2, "Byt 2+kk v ulici Kolbenova, Olomouc.", area_m2=50.0), "kolbenova")
    found = facts(a, b, W32W)
    assert "street" in found and "street_prose" in found


# ------------------------------------------------------------------------- W5 disposition


def test_w5_a_body_contradicting_its_column_is_read_by_its_print() -> None:
    """39679: idnes files 5+kk, its own body sells `byt 5+1`."""
    a = ad(1, "Prostorný byt 5+1 v rezidenci Areál Hvězda.", source="idnes", disposition="5+kk")
    b = ad(2, "Byt 5+1 s balkonem v Areálu Hvězda.", disposition="5+1")
    assert veto(a, b, W31) == "disposition"
    assert "disposition" in facts(a, b, W31)
    assert veto(a, b, W32W) is None
    assert "disposition" not in d43(a, b, W32W)


def test_w5_two_printed_dispositions_apart_stay_apart() -> None:
    a = ad(1, "Byt 2+kk s balkonem.", disposition="2+kk")
    b = ad(2, "Byt 3+kk s balkonem.", disposition="3+kk")
    assert veto(a, b, W32W) == "disposition"
    c = ad(3, "Prodej bytu 5+1, případně bytu 3+kk.", disposition="5+kk")
    assert printed_dispositions(c.description) == frozenset({"5+1", "3+kk"})
    assert veto(c, ad(4, "Byt 5+1.", disposition="5+1"), W32W) == "disposition"


def test_w5_reads_the_offered_disposition_only() -> None:
    assert printed_dispositions(
        "Byt 3+kk ve 3. NP. Ve stejném domě nabízíme také byt 2+kk.") == frozenset({"3+kk"})
    assert printed_dispositions("Krásný byt v centru, 3 pokoje. Kauce 2+1 nájem.") == frozenset()
    a = ad(1, "Krásný byt v centru, 3 pokoje, ve 3. NP. Kauce 2+1 nájem.", disposition="3+kk")
    b = ad(2, "Byt 3+kk ve 3. NP.", disposition="3+kk")
    assert veto(a, b, W32W) is None


def test_w5_a_body_offering_another_disposition_adds_its_veto() -> None:
    a = ad(1, "Byt 3+kk s balkonem.", disposition="2+kk")
    b = ad(2, "Světlý byt s balkonem.", disposition="2+kk")
    assert veto(a, b, W31) is None
    assert veto(a, b, W32W) == "disposition"
    assert "disposition" in facts(a, b, W32W)


# ------------------------------------------------------------- each limb on its own dial

VETO_CASES: dict[str, tuple[Listing, Listing, str]] = {
    "witness_area": (ad(1, TERRACE, source="ceskereality", area_m2=60.0),
                     ad(2, TERRACE, source="realitymix", area_m2=100.0), "area"),
    "witness_floor": (ad(1, "Byt 4+1 nacházející se v 6. NP.", floor=5),
                      ad(2, "Byt 4+1 nacházející se v 6. NP.", source="bezrealitky", floor=0),
                      "floor"),
    "witness_disposition": (ad(1, "Prostorný byt 5+1.", source="idnes", disposition="5+kk"),
                            ad(2, "Byt 5+1 s balkonem.", disposition="5+1"), "disposition"),
}


@pytest.mark.parametrize("dial", sorted(VETO_CASES))
def test_each_veto_limb_answers_to_its_own_dial(dial: str) -> None:
    a, b, name = VETO_CASES[dial]
    assert veto(a, b, only(dial)) is None
    for other in DIALS:
        if other != dial:
            assert veto(a, b, only(other)) == name


def test_the_d43_limbs_answer_to_their_own_dials() -> None:
    a = ad(1, "Byt 2+kk v projektu Atrium Petřiny.", floor=3, total_floors=6)
    b = ad(2, "Byt 2+kk v projektu Atrium Petřiny.", floor=3, total_floors=5)
    assert "total_floors" not in facts(a, b, only("witness_total_floors"))
    c = _on(ad(3, REPOST, area_m2=112.0), "rubesova")
    d = _on(ad(4, REPOST, area_m2=112.0), "kolbenova")
    assert "street" not in facts(c, d, only("witness_street"))
    for other in DIALS:
        if other != "witness_total_floors":
            assert "total_floors" in facts(a, b, only(other))
        if other != "witness_street":
            assert "street" in facts(c, d, only(other))


@pytest.mark.parametrize("dial", sorted(VETO_CASES))
def test_readings_built_under_w32w_are_ignored_when_the_dials_are_off(dial: str) -> None:
    a, b, _name = VETO_CASES[dial]
    assert (pair_veto(fp(a, W32W), fp(b, W32W), W31)
            == pair_veto(fp(a, W31), fp(b, W31), W31))
    assert (cluster_invariants_ok([fp(a, W32W), fp(b, W32W)], W31)
            == cluster_invariants_ok([fp(a, W31), fp(b, W31)], W31))


# ---------------------------------------------------------------------- the retrieval veto


def test_the_witnesses_travel_with_the_guard_row_through_the_memory_store() -> None:
    """The retrieval veto reads a stored candidate's witnesses without its gallery."""
    dirty = fp(ad(1, TERRACE, source="realitymix", area_m2=100.0), W32W)
    stored = fp(ad(2, TERRACE, area_m2=60.0), W32W)
    store = MemoryStore()
    store.put_listing(2, FpRow(guard_row(stored), "digest", "o1", "byt", True), [])
    served = _GuardLookup(store, {}).get(2)
    assert served == guard_row(stored)
    assert pair_veto(dirty, served, W31) == "area"
    assert pair_veto(dirty, served, W32W) is None
    assert pair_veto(served, dirty, W32W) is None


def test_a_row_rt_fp_serves_is_unwitnessed_in_either_order() -> None:
    """`rt_fp` has no columns for the readings, so a row the SQL store serves is a `GuardRow`
    of the six columns: the veto reads the pair by its columns whichever side is dirty."""
    dirty = fp(ad(1, TERRACE, source="realitymix", area_m2=100.0), W32W)
    sql_row = GuardRow(2, "byt", "prodej", 60.0, "2+kk", 0)
    assert pair_veto(dirty, sql_row, W32W) == "area"
    assert pair_veto(sql_row, dirty, W32W) == "area"


def test_the_sql_lane_refuses_the_veto_dials() -> None:
    with pytest.raises(NotImplementedError):
        named_config({"settings": "w32w", "model": "w6_gold"}, what="rt_seed")
    with pytest.raises(NotImplementedError):
        pass_config(json.loads(json.dumps(W32W.to_dict())), "w6_gold", "rt")
    street_only = replace(W31, witness_street=True, witness_total_floors=True)
    settings, _ = pass_config(json.loads(json.dumps(street_only.to_dict())), "w6_gold", "rt")
    assert settings == street_only


# --------------------------------------------------------------- W33m: monotone, flats only


def test_w33m_is_w32w_without_the_disposition_witness() -> None:
    raw33 = json.loads((SETTINGS_DIR / "w33m.json").read_text(encoding="utf-8"))
    no5 = json.loads((SETTINGS_DIR / "w32w_no5.json").read_text(encoding="utf-8"))
    assert raw33 == no5
    assert W33M == replace(W32W, witness_disposition=False)


SOURCES = ("sreality", "idnes", "bezrealitky", "realitymix", "bazos")
DISPOSITIONS = ("1+kk", "2+kk", "2+1", "3+kk", "3+1")


def _figure(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _body(rng: random.Random, column: float | None, floor: int | None) -> str:
    """A flat's text that states sizes and storeys its own columns may contradict."""
    dispo = rng.choice(DISPOSITIONS)
    base = column if column is not None and rng.random() < 0.8 else float(rng.randint(30, 120))
    unit = round(base * rng.choice((0.8, 0.9, 0.95, 1.0, 1.06, 1.12, 1.3)), rng.choice((0, 1)))
    parts = [rng.choice((
        f"Byt {dispo} o užitné ploše {_figure(unit)} m2.",
        f"Prodej bytu {dispo} o velikosti {_figure(unit)} m².",
        f"Byt {dispo}, podlahová plocha {_figure(unit)} m2.",
        f"Byt {dispo}, celková plocha činí {_figure(unit + 4)} m², užitná plocha bytu je "
        f"{_figure(unit)} m².",
        f"Světlý byt {dispo} v klidné lokalitě.",
    ))]
    if rng.random() < 0.5:
        parts.append(f"K bytu náleží sklep {rng.choice((2, 3, 4, 6))} m2.")
    if rng.random() < 0.4:
        parts.append(f"Balkon o ploše {_figure(rng.choice((3.0, 4.0, 5.5)))} m2.")
    if rng.random() < 0.3:
        parts.append(f"Terasa o ploše {_figure(rng.choice((12.0, 20.0, 39.4)))} m2.")
    storey = (floor if floor is not None else 2) + rng.choice((-1, 0, 1, 2, 3))
    parts.append(rng.choice((f"Byt se nachází v {max(storey, 1)}. NP.",
                             f"Byt je ve {max(storey, 1)}. patře.", "Byt v přízemí.",
                             "Byt v cihlovém domě (-1. PP).", "Byt v posledním podlaží.", "")))
    return " ".join(part for part in parts if part)


def _random_pair(rng: random.Random, index: int, category: str = "byt"
                 ) -> tuple[Listing, Listing]:
    area = float(rng.randint(30, 120)) if rng.random() < 0.9 else None
    other = (round(area * rng.choice((0.85, 0.9, 0.93, 0.96, 1.0, 1.05, 1.1, 1.2)))
             if area is not None and rng.random() < 0.9 else None)
    floor = rng.randint(-1, 6) if rng.random() < 0.85 else None
    other_floor = (floor + rng.choice((-3, -2, 0, 1, 2, 4))
                   if floor is not None and rng.random() < 0.85 else None)
    body = _body(rng, area, floor)
    other_body = body if rng.random() < 0.35 else _body(rng, other, other_floor)
    kind = rng.choice(("prodej", "pronajem"))
    a = ad(2 * index + 1, body, source=rng.choice(SOURCES), category_main=category,
           category_type=kind, area_m2=area, floor=floor)
    b = ad(2 * index + 2, other_body, source=rng.choice(SOURCES), category_main=category,
           category_type=kind, area_m2=float(other) if other is not None else None,
           floor=other_floor)
    return a, b


def _area_floor(a: Listing, b: Listing, settings: Settings, mode: str) -> set[str]:
    return set(facts(a, b, settings, mode)) & {"area", "floor"}


@pytest.mark.parametrize("row", [W32W, W33M], ids=["w32w", "w33m"])
def test_a_witnessed_reading_never_adds_a_veto_or_a_fact(row: Settings) -> None:
    """(a) Parity of vetoes: over 30 random flat pairs whose bodies print figures and storeys
    their columns contradict, every pair the candidate vetoes W31 vetoes, every area or floor
    fact it reads in any D43 mode W31 reads, and no two-member group it refuses W31 admits.
    No pair files a disposition column, so W5 (a dial, off in w33m) is not read here."""
    rng = random.Random(33)
    lifted_vetoes = lifted_facts = 0
    for index in range(30):
        a, b = _random_pair(rng, index)
        fa31, fb31, fa, fb = fp(a, W31), fp(b, W31), fp(a, row), fp(b, row)
        base, cand = pair_veto(fa31, fb31, W31), pair_veto(fa, fb, row)
        assert cand is None or base is not None, (a.description, b.description)
        lifted_vetoes += base is not None and cand is None
        for mode in (GATE, CLUSTER, PROMOTE):
            base_facts, cand_facts = _area_floor(a, b, W31, mode), _area_floor(a, b, row, mode)
            assert cand_facts <= base_facts, (mode, a.description, b.description)
            lifted_facts += len(base_facts - cand_facts)
        if cluster_invariants_ok([fa31, fb31], W31) is None:
            assert cluster_invariants_ok([fa, fb], row) is None
    assert lifted_vetoes and lifted_facts, "the property is read on pairs W1 or W2 lifts"


@pytest.mark.parametrize("category", ["dum", "komercni", "pozemek", "ostatni"])
def test_a_house_commercial_or_land_pair_reads_as_w31(category: str) -> None:
    """W33m: area and storeys are read of two flats only, so any other pair is W31's."""
    rng = random.Random(category)
    for index in range(10):
        a, b = _random_pair(rng, index, category)
        assert fp(a, W33M).printed_unit_area is None and fp(a, W33M).printed_storeys == ()
        assert pair_veto(fp(a, W33M), fp(b, W33M), W33M) == pair_veto(fp(a, W31), fp(b, W31), W31)
        for mode in (GATE, CLUSTER, PROMOTE):
            assert _area_floor(a, b, W33M, mode) == _area_floor(a, b, W31, mode)
        assert (cluster_invariants_ok([fp(a, W33M), fp(b, W33M)], W33M)
                == cluster_invariants_ok([fp(a, W31), fp(b, W31)], W31))


def _reads_as_w31(members: list[Listing]) -> None:
    """Every pair of `members` vetoes and reads D43 as W31, and the group's invariant too."""
    for index, a in enumerate(members):
        for b in members[index + 1:]:
            assert veto(a, b, W33M) == veto(a, b, W31)
            for mode in (GATE, CLUSTER, PROMOTE):
                assert facts(a, b, W33M, mode) == facts(a, b, W31, mode), (a.id, b.id, mode)
    assert (cluster_invariants_ok([fp(m, W33M) for m in members], W33M)
            == cluster_invariants_ok([fp(m, W31) for m in members], W31))


PARDUBICE_HOUSE = ("Dvojpodlažní dům z roku 2022 má dispozici 4+kk a podlahovou plochu 101 m2. "
                   "Nabízíme novostavbu s nadstandardním pozemkem o velikosti bezmála 5000 m2.")


def test_defect_houses_whose_body_prints_another_area_read_as_w31() -> None:
    """(b) Pardubice 378422 / 369318 x 496420: two houses filed at 141 m2 whose bodies print
    `podlahovou plochu 101 m2` and a 5000 m2 plot. A house body is not read."""
    members = [ad(369318, PARDUBICE_HOUSE, category_main="dum", area_m2=141.0, floor=2),
               ad(378422, PARDUBICE_HOUSE, source="idnes", category_main="dum", area_m2=141.0),
               ad(496420, PARDUBICE_HOUSE + " Stavba určená k rekreaci.", source="ceskereality",
                  category_main="dum", area_m2=141.0),
               ad(1, PARDUBICE_HOUSE, source="bazos", category_main="dum", area_m2=101.0)]
    assert all(fp(m, W33M).printed_unit_area is None for m in members)
    _reads_as_w31(members)


def test_defect_two_equal_flat_columns_one_body_contradicting_its_own_read_as_w31() -> None:
    """(b) Hradec 456651 x 13181930: two flats at 36 m2; a body stating another figure than its
    own column moves nothing where the columns agree."""
    idnes = ("Pronájem bytu 1+kk 36 m², Hradec Králové. Nabízíme k pronájmu světlý byt 1+kk o "
             "velikosti 36 m² v pečlivě udržovaném bytovém domě.")
    bazos = ("Ev.č. N09578 Nabízíme k pronájmu světlý byt 1+kk o velikosti 45 m² v pečlivě "
             "udržovaném bytovém domě. En. náročnost b.: C (97 kWh/m2 za rok).")
    members = [ad(456651, idnes, source="idnes", category_type="pronajem", area_m2=36.0,
                  disposition="1+kk", floor=2),
               ad(13181930, bazos, source="bazos", category_type="pronajem", area_m2=36.0,
                  disposition="1+kk"),
               ad(10620319, idnes, category_type="pronajem", area_m2=37.0, disposition="1+kk")]
    assert fp(members[1], W33M).printed_unit_area == 45.0
    _reads_as_w31(members)


def test_defect_two_halls_whose_body_prints_two_figures_read_as_w31() -> None:
    """(b) Jablonec 43634 x 456906: industrial halls filed 750 and 725 m2 under one text
    printing `každý o výměře 375m2 (750m2 celkem)`; E280 keeps W31's promotion."""
    body = ("Nabízíme pronájem v komerčním objektu výrobních a skladovacích prostorů. Dva "
            "nezávislé prostory ve 2 a 3 patře, každý o výměře 375m2 (750m2 celkem) s nákladním "
            "výtahem, kancelářemi a sociálním zařízením.")
    a = ad(43634, body, category_main="komercni", category_type="pronajem", area_m2=750.0)
    b = ad(456906, body, source="realitymix", category_main="komercni",
           category_type="pronajem", area_m2=725.0)
    assert "area" not in facts(a, b, W31, PROMOTE)
    _reads_as_w31([a, b])


def test_defect_a_flat_printing_its_gross_and_net_area_reads_as_w31() -> None:
    """(b) Pardubice 55774 x 106890: flats filed 61 and 65 m2 under one text printing
    `celková plocha činí 65 m², užitná plocha bytu je 61 m²` - two unit figures, so the body
    witnesses nothing, and E280 promotes as in W31 (the 3 % check never fails where W31
    passes)."""
    body = ("Byt 3+1 v osobním vlastnictví, Pichlova ulice. Byt se nachází ve 4. patře "
            "cihlového domu a jeho celková plocha činí 65 m², užitná plocha bytu je 61 m².")
    a = ad(55774, body, area_m2=61.0, disposition="3+1", floor=3)
    b = ad(106890, body, source="bazos", area_m2=65.0, disposition="3+1")
    assert fp(a, W33M).printed_unit_area is None
    assert "area" not in facts(a, b, W31, PROMOTE)
    _reads_as_w31([a, b])

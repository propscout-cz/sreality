"""W32w — witnessed columns: a portal column is a stated fact until the ad's own text
contradicts or composes it. Per limb a synthetic pair W31 refuses and W32w admits, read through
the pair veto AND the D43 limbs (gate and cluster), the stated-different pairs W32w still
refuses, each limb pinned to its own dial, and parity: with every dial off nothing moves."""

from __future__ import annotations

import json
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
from autodedup.indistinguishable import CLUSTER, GATE, distinguishing_facts
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


def test_w1_a_column_that_adds_the_printed_terrace_is_one_area() -> None:
    """557627: realitymix files 100 = 60 + 39,4 terasa; both bodies print the 60."""
    a = ad(1, TERRACE, source="ceskereality", area_m2=60.0)
    b = ad(2, TERRACE, source="realitymix", area_m2=100.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, W32W) is None
    assert "area" not in d43(a, b, W32W)


def test_w1_a_column_net_of_its_own_printed_extras_composes() -> None:
    """71409: 54 = 60 - 4 balkon - 2 sklep, the 54 side's own body stating all three."""
    a = ad(1, NET, area_m2=54.0)
    b = ad(2, "Rohový byt 2+1 ve druhém patře, okna na západ.", source="idnes", area_m2=60.0)
    assert veto(a, b, W31) == "area"
    assert "area" in facts(a, b, W31)
    assert veto(a, b, W32W) is None
    assert "area" not in d43(a, b, W32W)


def test_w1_one_text_under_two_columns_is_read_by_its_figure() -> None:
    """71409 as it runs: both bodies print `60 m², z toho 4m² balkon a 2m² sklep`; the shared
    extras void the composition and the 54 side is read by the 60 its own text states."""
    a = ad(1, NET, area_m2=54.0)
    b = ad(2, NET, source="idnes", area_m2=60.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, W32W) is None
    assert "area" not in d43(a, b, W32W)


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
    assert "area" in d43(c, d, W32W)


def test_w1_a_house_template_of_three_units_witnesses_nothing() -> None:
    body = ("Rodinný dům se třemi byty: v přízemí 68 m2, v patře 75 m2 a v podkroví 81 m2, "
            "cena za každý.")
    a = ad(1, body, category_main="dum", area_m2=68.0)
    b = ad(2, body, category_main="dum", area_m2=81.0)
    assert veto(a, b, W32W) == "area"
    assert "area" in d43(a, b, W32W)


def test_w1_an_extra_never_joins_one_column_to_the_other() -> None:
    """No body states the unit's area: 60 + the 20 m2 terrace hitting 80 is a coincidence."""
    body = "Byt 3+kk v cihlovém domě s terasou o velikosti 20 m2, sklep, výtah. Cena 6 500 000 Kč."
    a, b = ad(1, body, area_m2=60.0), ad(2, body, area_m2=80.0)
    assert veto(a, b, W32W) == "area"
    assert "area" in d43(a, b, W32W)


def test_w1_two_bodies_stating_the_same_cellar_beside_two_areas_stay_apart() -> None:
    a = ad(1, "Byt, užitná plocha 40 m2, k bytu sklep 4 m2.", area_m2=40.0)
    b = ad(2, "Byt, užitná plocha 44 m2, k bytu sklep 4 m2.", area_m2=44.0)
    assert veto(a, b, W32W) == "area"
    c = ad(3, "Byt, užitná plocha 40 m2, k bytu sklep 4 m2.", area_m2=44.0)
    d = ad(4, "Byt, užitná plocha 44 m2, k bytu sklep 4 m2.", area_m2=48.0)
    assert veto(c, d, W32W) == "area"


def test_w1_e280_needs_a_unit_figure_in_its_equal_set() -> None:
    body = "Byt 3+kk s terasou o ploše 20 m2 v klidné lokalitě."
    a, b = ad(1, body, area_m2=60.0), ad(2, body, area_m2=80.0)
    assert "area" not in facts(a, b, W31)
    assert "area" in facts(a, b, W32W)


def test_w1_reads_the_unit_figure_by_its_size_word() -> None:
    assert unit_area_figures("Byt o ploše 54 m2 s 12 m2 terasou.") == frozenset({(54.0, 0)})
    assert unit_area_figures("Byt 2+kk 54 m2, 25 m2 velký obývák") == frozenset()
    assert unit_area_figures("Hala o rozloze 500 m2 a kanceláře o velikosti 45 m2") == frozenset()
    assert unit_area_figures(NET) == frozenset({(60.0, 0)})


def test_w1_the_cluster_spread_reads_the_same_witnesses() -> None:
    """17972: the bezrealitky 44 is `plochu 40 m2 a cca 4 m2 sklep`."""
    a = ad(1, "Ateliér o celkové ploše 40 m2, klidný vnitroblok.", area_m2=40.0)
    b = ad(2, "Ateliér má plochu 40 m2 a cca 4 m2 sklep.", source="bezrealitky", area_m2=44.0)
    assert cluster_invariants_ok([fp(a, W31), fp(b, W31)], W31) == "area_spread"
    assert cluster_invariants_ok([fp(a, W32W), fp(b, W32W)], W32W) is None
    c = ad(3, "Ateliér o užitné ploše 60 m2.", area_m2=60.0)
    assert cluster_invariants_ok([fp(a, W32W), fp(c, W32W)], W32W) == "area_spread"


# ------------------------------------------------------------------------------ W2 floor


def test_w2_two_bodies_printing_one_storey_lift_the_columns() -> None:
    """618930: both bodies `v 6. NP`, columns 5 and 0."""
    body = "Byt 4+1 o užitkové ploše 115,1 m, nacházející se v 6. NP, sklepní kóje."
    a = ad(1, body, floor=5)
    b = ad(2, body, source="bezrealitky", floor=0)
    assert veto(a, b, W31) == "floor"
    assert "floor" in facts(a, b, W31)
    assert veto(a, b, W32W) is None
    assert "floor" not in d43(a, b, W32W)


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

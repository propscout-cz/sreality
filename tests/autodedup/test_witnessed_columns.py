"""W32w — witnessed columns: a portal column is a stated fact until the ad's own text
contradicts or composes it. Per limb one synthetic pair W31 refuses and W32w admits, and one
stated-different pair W32w still refuses; with every dial off nothing moves."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from autodedup.dataset import Listing, Location
from autodedup.fingerprint import Fingerprint, build_fingerprint
from autodedup.guards import cluster_invariants_ok, pair_veto
from autodedup.incremental import FpRow, GuardRow, _GuardLookup, guard_row
from autodedup.incremental_store import MemoryStore
from autodedup.indistinguishable import CLUSTER, GATE, distinguishing_facts
from autodedup.settings import Settings
from autodedup.text_facts import printed_basement_storeys, printed_dispositions

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


# ------------------------------------------------------------------------------- the row


def test_w32w_is_w31_with_the_five_dials_on() -> None:
    raw31 = json.loads((SETTINGS_DIR / "w31.json").read_text(encoding="utf-8"))
    raw32 = json.loads((SETTINGS_DIR / "w32w.json").read_text(encoding="utf-8"))
    assert {key: value for key, value in raw32.items() if key not in DIALS} == raw31
    assert all(raw32[dial] is True for dial in DIALS)
    assert not any(getattr(Settings(), dial) for dial in DIALS)
    assert not any(getattr(W31, dial) for dial in DIALS)
    assert replace(W31, **{dial: True for dial in DIALS}) == W32W


def test_with_every_dial_off_the_fingerprint_reads_nothing_new() -> None:
    body = ("Byt 2+kk o velikosti 60 m2, terasa o ploše 39,4 m2, v 6. NP, v suterénu sklep, "
            "ulice Kolbenova.")
    row = fp(ad(1, body, area_m2=60.0, disposition="3+kk", floor=5), W31)
    assert (row.printed_unit_areas, row.printed_accessory_areas, row.printed_storeys,
            row.worded_storey, row.printed_disposition) == ((), (), (), False, None)
    on = fp(ad(1, body, area_m2=60.0, disposition="3+kk", floor=5), W32W)
    assert on.printed_unit_areas == (60.0,) and on.printed_accessory_areas == (39.4,)
    assert on.printed_storeys == (("np", (6,)), ("pp", (-1,)))
    assert on.printed_disposition == "2+kk"


# ------------------------------------------------------------------------------- W1 area

TERRACE = "Byt 2+kk o velikosti 60 m2, terasa o ploše 39,4 m2, v prvním nadzemním podlaží."


def test_w1_a_column_that_adds_the_printed_terrace_is_one_area() -> None:
    """557627: realitymix files 100 = 60 + 39,4 terasa; both bodies print the 60."""
    a = ad(1, TERRACE, source="ceskereality", area_m2=60.0)
    b = ad(2, TERRACE, source="realitymix", area_m2=100.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, W32W) is None


def test_w1_a_column_net_of_its_printed_extras_composes() -> None:
    """71409: 60 = 54 + 4 balkon + 2 sklep, the 54 side's own body stating the extras."""
    a = ad(1, "Byt 2+1 s celkovou plochou 60 m², z toho jsou 4m² balkon a 2m² sklep.",
           area_m2=54.0)
    b = ad(2, "Rohový byt 2+1 ve druhém patře, okna na západ.", source="idnes", area_m2=60.0)
    assert veto(a, b, W31) == "area"
    assert veto(a, b, W32W) is None


def test_w1_two_printed_areas_apart_stay_apart() -> None:
    a = ad(1, "Byt o užitné ploše 40 m2 ve 2. NP.", area_m2=40.0)
    b = ad(2, "Byt o užitné ploše 60 m2 ve 2. NP.", area_m2=60.0)
    assert veto(a, b, W32W) == "area"


def test_w1_a_price_list_witnesses_nothing() -> None:
    menu = ("Nabízíme byty v projektu. Byt 1+kk o ploše 40 m2 v přízemí domu. Dále byt 2+kk o "
            "ploše 55 m2 ve druhém patře. Také byt 3+kk o ploše 72 m2 v posledním patře a k "
            "tomu ještě byt 4+kk o ploše 95 m2 s terasou.")
    a = ad(1, menu, area_m2=40.0)
    b = ad(2, "Byt 3+kk v novostavbě.", area_m2=72.0)
    assert fp(a, W32W).printed_unit_areas == ()
    assert veto(a, b, W32W) == "area"


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
    assert veto(a, b, W32W) is None


def test_w2_a_lone_printed_basement_reads_against_the_silent_column() -> None:
    """147111: idnes prints `(-1. PP)` under a column of 1; sreality is -1 and prints none."""
    a = ad(1, "Byt 1+1 v klasickém cihlovém domě (-1. PP), samostatná kuchyň.", source="idnes",
           floor=1)
    b = ad(2, "Byt 1+1 v polosuterénu udržovaného činžovního domu.", floor=-1)
    assert veto(a, b, W31) == "floor"
    assert veto(a, b, W32W) is None


def test_w2_two_bodies_stating_two_storeys_stay_apart() -> None:
    """40485: `v posledním podlaží` against `ve druhém podlaží`, columns 5 and 1."""
    a = ad(1, "Byt 2+kk v posledním podlaží pětipodlažního objektu.", floor=5)
    b = ad(2, "Byt 2+kk ve druhém podlaží pětipodlažního objektu.", floor=1)
    assert veto(a, b, W32W) == "floor"
    c = ad(3, "Byt 2+kk ve 2. NP.", floor=1)
    d = ad(4, "Byt 2+kk v 5. NP.", floor=4)
    assert veto(c, d, W32W) == "floor"


def test_w2_a_shared_basement_garage_does_not_join_two_storeys() -> None:
    a = ad(1, "Byt 2+kk ve 2. NP, garážové stání v 1. PP.", floor=1)
    b = ad(2, "Byt 2+kk v 5. NP, garážové stání v 1. PP.", floor=4)
    assert veto(a, b, W32W) == "floor"


def test_w2_reads_storeys_below_ground() -> None:
    assert printed_basement_storeys("v cihlovém domě (-1. PP)") == frozenset({-1})
    assert printed_basement_storeys("garáž ve 2. podzemním podlaží domu") == frozenset({-2})
    assert printed_basement_storeys("Byt v suterénu bytového domu") == frozenset({-1})
    assert printed_basement_storeys("Byt v polosuterénu domu") == frozenset()
    assert printed_basement_storeys("Byt 3+kk ve 2. NP, 21 pp") == frozenset()


# ------------------------------------------------------------------------ W3 total_floors


def test_w3_two_silent_columns_are_not_a_storey_count() -> None:
    """512418: sreality 3/6 then 3/5, no body prints a height."""
    a = ad(1, "Byt 2+kk v projektu Atrium Petřiny, orientace na východ.", floor=3,
           total_floors=6)
    b = ad(2, "Byt 2+kk v projektu Atrium Petřiny, orientace na východ.", floor=3,
           total_floors=5)
    assert "total_floors" in facts(a, b, W31)
    assert "total_floors" not in facts(a, b, W32W)
    assert "total_floors" not in facts(a, b, W32W, CLUSTER)


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


def test_w4_the_resolver_street_alone_is_not_stated() -> None:
    """335094: one sreality text twice, no street printed, two resolver streets."""
    body = "Byt 4+1 o výměře 112 m² s rohovou terasou, komplex z roku 2002."
    a = _on(ad(1, body, area_m2=112.0, floor=3), "rubesova")
    b = _on(ad(2, body, area_m2=112.0, floor=3), "kolbenova")
    assert "street" in facts(a, b, W31)
    assert "street" not in facts(a, b, W32W)


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
    assert veto(a, b, W32W) is None


def test_w5_two_printed_dispositions_apart_stay_apart() -> None:
    a = ad(1, "Byt 2+kk s balkonem.", disposition="2+kk")
    b = ad(2, "Byt 3+kk s balkonem.", disposition="3+kk")
    assert veto(a, b, W32W) == "disposition"
    c = ad(3, "Byt 5+1, k prodeji je i vedlejší byt 3+kk.", disposition="5+kk")
    assert printed_dispositions(c.description) == frozenset({"5+1", "3+kk"})
    assert veto(c, ad(4, "Byt 5+1.", disposition="5+1"), W32W) == "disposition"


# ---------------------------------------------------------------------- the retrieval veto


def test_the_witnesses_travel_with_the_guard_row_through_the_memory_store() -> None:
    """The retrieval veto reads a stored candidate's witnesses without its gallery: the dirty
    side prints nothing, the stored side's body composes its 100."""
    dirty = fp(ad(1, "Byt 2+kk v novostavbě.", source="realitymix", area_m2=100.0), W32W)
    stored = fp(ad(2, TERRACE, area_m2=60.0), W32W)
    store = MemoryStore()
    store.put_listing(2, FpRow(guard_row(stored), "digest", "o1", "byt", True), [])
    served = _GuardLookup(store, {}).get(2)
    assert served == guard_row(stored)
    assert pair_veto(dirty, served, W31) == "area"
    assert pair_veto(dirty, served, W32W) is None
    assert pair_veto(dirty, stored, W32W) is None


def test_a_row_rt_fp_serves_carries_no_witness_and_is_read_by_its_columns() -> None:
    """The deploy implication: `rt_fp` has no columns for the readings, so a row the SQL store
    serves is a `GuardRow` of the six columns alone, and the same pair the MemoryStore admits
    is refused at retrieval until those columns exist (or the rows are re-seeded)."""
    dirty = fp(ad(1, "Byt 2+kk v novostavbě.", source="realitymix", area_m2=100.0), W32W)
    sql_row = GuardRow(2, "byt", "prodej", 60.0, "2+kk", 0)
    assert pair_veto(dirty, sql_row, W32W) == "area"

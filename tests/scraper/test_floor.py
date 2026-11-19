import pytest

from scraper.attribute_contract import CONTRACT, floor_convention
from scraper.floor import (
    floor_from_portal,
    floor_from_text,
    is_plausible_floor,
    normalize_floor,
    storey_label,
)


def test_is_plausible_floor():
    assert is_plausible_floor(3, None) is True
    assert is_plausible_floor(0, 6) is True
    assert is_plausible_floor(-1, None) is True       # suterén
    assert is_plausible_floor(None, 5) is False
    assert is_plausible_floor(8, 5) is False           # above the building total
    assert is_plausible_floor(99, None) is False       # out of band
    assert is_plausible_floor(-5, None) is False       # too deep
    # total_floors counts PODLAŽÍ including the ground storey, so under ground=0 the
    # top storey is total-1 and the count itself is one storey too high.
    assert is_plausible_floor(5, 6) is True
    assert is_plausible_floor(6, 6) is False
    assert is_plausible_floor(0, 1) is True


def test_normalize_floor_grammar():
    assert normalize_floor("přízemí") == 0
    assert normalize_floor("prizemi") == 0
    assert normalize_floor("suterén") == -1
    # NP is 1-indexed from ground: 1.NP = ground = 0.
    assert normalize_floor("1. NP") == 0
    assert normalize_floor("6. NP") == 5
    assert normalize_floor("4. nadzemní podlaží") == 3
    assert normalize_floor("6. nadzemním podlaží") == 5
    # PP is below ground.
    assert normalize_floor("1. PP") == -1
    assert normalize_floor("2. podzemní podlaží") == -2


def test_normalize_floor_rejects_unknown_and_bare_int():
    # A bare integer carries no convention -> never guessed.
    assert normalize_floor("4") is None
    assert normalize_floor("") is None
    assert normalize_floor(None) is None
    assert normalize_floor("mezonet") is None
    # "patro" names no scale: half the brokers mean the podlaží ordinal (#2552).
    assert normalize_floor("3. patro") is None
    assert normalize_floor("ve 4. patře") is None
    # idnes prints both, and its NP is the one read.
    assert normalize_floor("2. patro (3. NP)") == 2
    # The building ADJECTIVE must not read as a floor.
    assert normalize_floor("šestipodlažní") is None
    assert normalize_floor("6podlažní dům") is None


def test_floor_from_text_high_precision_cases():
    # A patro wording alone is no storey; the NP beside it is.
    assert floor_from_text("Byt se nachází ve 4. patře bytového domu.") == (None, None)
    assert floor_from_text("Byt ve 4. patře (5. NP) bytového domu.") == (4, None)
    # NP form.
    assert floor_from_text("situovaný ve 4. nadzemním podlaží") == (3, None)
    # RK 'Podlaží:' label carrying an NP form.
    f, t = floor_from_text("Užitná plocha: 42 m2 Podlaží: 4. NP Parkování: vyhrazené")
    assert (f, t) == (3, None)
    # přízemí + a digit building total.
    assert floor_from_text("byt v přízemí 6podlažního domu") == (0, 6)
    # 'Podlaží celkem' total alongside a unit floor.
    assert floor_from_text("Podlaží: 3. NP Podlaží celkem: 6") == (2, 6)


def test_floor_from_text_building_total_trap():
    # Unit floor (digit ordinal noun) captured; building total (digit adjectival)
    # read only as total_floors — never as the floor.
    assert floor_from_text("v 6. NP šestipodlažního domu") == (5, None)
    # A patra-worded total is as unread as a patra-worded storey.
    assert floor_from_text("ve 3. NP (z celkových 10 pater)") == (2, None)
    assert floor_from_text("byt ve 2. NP 6patrového domu") == (1, None)
    # The unit floor exceeding the stated building total is dropped (we grabbed a
    # building number), the total is kept.
    f, t = floor_from_text("Podlaží: 8. NP Podlaží celkem: 5")
    assert f is None and t == 5


def test_floor_from_text_defers_ambiguous_tail():
    # Spelled-out ordinals: read for a flat, still deferred on the legacy (non-flat) miner.
    assert floor_from_text("v devátém podlaží udržovaného domu") == (8, None)
    assert floor_from_text("v devátém podlaží udržovaného domu", category_main="dum") == (None, None)
    # 'pater' (building total, no unit cue) yields only the total.
    assert floor_from_text("panelový dům o deseti patrech") == (None, None)
    # mezonet / no floor info.
    assert floor_from_text("mezonetový byt s galerií") == (None, None)
    assert floor_from_text("hezký byt 3+1 po rekonstrukci") == (None, None)
    # bare 'Podlaží: 7' (no NP/patro keyword) -> convention unknown -> deferred.
    assert floor_from_text("Podlaží: 7 Parkování: ne")[0] is None


def test_floor_from_text_empty():
    assert floor_from_text(None) == (None, None)
    assert floor_from_text("") == (None, None)


# --- the per-portal converter ------------------------------------------------


def test_floor_from_portal_ground1_shifts_only_positive_storeys():
    assert floor_from_portal("ground1", 1) == 0
    assert floor_from_portal("ground1", 3) == 2
    # sreality emits BOTH 0 and 1 for the ground storey ('zvýšené přízemí'), so a
    # blanket decrement would invent basements; -1 is suterén under either reading.
    assert floor_from_portal("ground1", 0) == 0
    assert floor_from_portal("ground1", -1) == -1
    assert floor_from_portal("ground1", "3") == 2
    assert floor_from_portal("ground1", None) is None


def test_floor_from_portal_ground0_is_a_passthrough():
    assert floor_from_portal("ground0", "2.") == 2
    assert floor_from_portal("ground0", 0) == 0
    assert floor_from_portal("ground0", "-1.") == -1
    # ceskereality's 126 is a keyed typo, not a storey: out of band is absence on either
    # scale (the stored ones are NULLed by migration 573, backed up first).
    assert floor_from_portal("ground0", "126.") is None


def test_the_word_wins_over_the_keys_convention():
    # A value that spells the storey out is the portal speaking about THAT advert.
    assert floor_from_portal("ground1", "přízemí") == 0
    assert floor_from_portal("ground0", "přízemí") == 0
    # 'zvýšené přízemí' is still the ground storey — the reading sreality's own
    # 4,597 floor=0 rows carry, and the reason those rows must not be decremented.
    assert floor_from_portal("ground1", "zvýšené přízemí") == 0
    assert floor_from_portal("ground1", "suterén") == -1
    assert floor_from_portal("word", "2. patro (3. NP)") == 2
    assert floor_from_portal("word", "-1. patro, suterén (1. PP)") == -1
    # 'snížené přízemí' is the ground storey the advert names, not the 1. PP its
    # parenthetical files it as.
    assert floor_from_portal("word", "snížené přízemí (1. PP)") == 0
    # A word portal that states no word states nothing: a bare int is not guessed.
    assert floor_from_portal("word", "7") is None


def test_a_bare_int_with_no_declared_convention_is_refused():
    with pytest.raises(ValueError):
        floor_from_portal(None, 3)


def test_the_conversion_is_idempotent_because_it_reads_the_source_not_the_column():
    # The heal re-derives from the stored payload; running it twice on the same
    # payload gives the same storey. `floor = floor - 1` would move on every pass.
    payload = 3
    once = floor_from_portal("ground1", payload)
    assert floor_from_portal("ground1", payload) == once == 2
    # And re-deriving is NOT re-applying: feeding the already-converted column back
    # would be the double-decrement bug, which no call site can express.
    assert floor_from_portal("ground1", once) == 1


def test_every_portal_declares_its_floor_convention():
    """The refusal above only binds if no cell can reach a parser without one."""
    missing = [p for p in CONTRACT if floor_convention(p) is None]
    assert missing == []
    assert floor_convention("ceskereality") == "ground0"
    assert floor_convention("idnes") == "word"
    assert {floor_convention(p) for p in
            ("sreality", "realitymix", "mmreality", "remax", "bezrealitky", "maxima")
            } == {"ground1"}


def test_a_storey_range_is_not_a_basement():
    # The hyphen between two ordinals is a separator, never a sign.
    assert normalize_floor("1.-2. NP") == 1
    assert floor_from_text("Podlaží: 1.-2. patro") == (None, None)
    # přízemí beside it decides, and it never reads as suterén (-1).
    assert floor_from_text("Byt je umístěn v přízemí (0. podlaží z celkem 3 podlaží)",
                           category_main="byt") == (0, None)
    assert floor_from_text("0. podlaží", category_main="byt") == (None, None)


def test_a_layout_digit_is_not_a_storey():
    assert floor_from_text("Byt 3+1. Podlaží 4.", category_main="byt") == (None, None)
    assert floor_from_text("Prodám byt 2+1. Patro: přízemí.") == (0, None)
    assert floor_from_text("Prodám byt 2+1. Patro: přízemí.", category_main="dum") == (0, None)


def test_the_pardubice_train_reads_its_sixth_storey_not_the_cellar():
    text = ("Nabízíme byt 3+kk, který se nachází v šestém podlaží bytového domu. "
            "K bytu náleží balkon a sklep v přízemí domu.")
    assert floor_from_text(text, category_main="byt") == (5, None)


def test_spelled_out_ordinals_follow_the_noun_convention():
    assert floor_from_text("čtvrté patro, výtah") == (None, None)
    assert floor_from_text("byt v přízemí, ve druhém patře bydlí majitel") == (None, None)
    assert floor_from_text("ve druhém nadzemním podlaží") == (1, None)
    assert floor_from_text("v prvním podzemním podlaží") == (-1, None)
    assert floor_from_text("v desátém podlaží") == (9, None)
    # Only a locative adjective may sit between: 'na druhé straně patra' is no storey.
    assert floor_from_text("byt 2+kk, na druhé straně patra je výtah") == (None, None)
    # The building total is not an ordinal: 'o deseti patrech' still reads nothing.
    assert floor_from_text("panelový dům o deseti patrech, sklep v přízemí") == (None, None)


def test_prizemi_tied_to_the_flat_is_ground():
    assert floor_from_text("Prodám přízemní byt 2+1") == (0, None)
    assert floor_from_text("Prodám byt v přízemí cihlového domu") == (0, None)
    assert floor_from_text("byt ve zvýšeném přízemí") == (0, None)
    assert floor_from_text("Nabízím 2+1, který se nachází v přízemí") == (0, None)
    assert floor_from_text("Patro: přízemí") == (0, None)


def test_prizemi_never_beats_a_storey_the_text_names():
    # A storey cue the readers cannot convert still forbids the ground-storey guess.
    assert floor_from_text("byt v 6 podlaží, sklep v přízemí") == (None, None)
    assert floor_from_text("byt ve 3. poschodí, kočárkárna v přízemí") == (None, None)


def test_a_storey_cue_binds_the_unit_word_before_a_later_prizemi():
    assert floor_from_text("byt v 6. podlaží, v přízemí domu je prádelna") == (5, None)


def test_a_flat_on_ground_that_names_another_storey_reads_none():
    assert floor_from_text("byt v přízemí, ve druhém patře bydlí majitel") == (None, None)
    assert floor_from_text("Prodám přízemní byt 2+1, sklep v 1. PP") == (None, None)


def test_a_flat_text_with_no_storey_reads_none():
    assert floor_from_text("Prodej bytu 2+kk, Pardubice, po rekonstrukci.",
                           category_main="byt") == (None, None)


def test_a_house_keeps_the_legacy_miner():
    text = "Rodinný dům 5+1, v přízemí kuchyň a obývák, ve druhém patře tři ložnice."
    assert floor_from_text(text, category_main="dum") == (0, None)
    assert floor_from_text("Dům ve 2. NP má terasu", category_main="dum") == (1, None)
    assert floor_from_text("v 6. podlaží", category_main="dum") == (None, None)


def test_storey_label_reads_the_fixture_the_spa_formatter_reads() -> None:
    import json
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / "fixtures" / "storey_labels.json").read_text())
    for case in cases:
        assert storey_label(case["floor"], case["total"]) == case["label"], case

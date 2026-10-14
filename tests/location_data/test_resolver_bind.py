"""BIND — step 1 of 4: the three NAMED homonym regressions, and the ambiguity contract.

Czech obec names repeat heavily. The old system's answer was to refuse text→hierarchy
entirely and route everything through "geocode the text, then PIP the coordinate" — which
converts a naming problem into a precision problem, because the geocode of an ambiguous town
name IS the town centroid. BIND reverses it: resolve names locally and hierarchically inside
the constraining parent.

**The W2-a contract flip**: BIND returns ONE `Binding`, not a stored candidate set, and
`ambiguous` is a CONFIDENCE (`low`) rather than a first-class status routed to an operator
queue that does not exist. The 2026-09-11 audit measured what the queue cost: 24,601 bazos
rows were served an arbitrary id-ordered winner while the row was labelled `ambiguous`.
"""

from __future__ import annotations

import pytest

from location_data.resolver import bind as step_bind
from location_data.resolver import core, normalize
from location_data.resolver.types import AddressPoint
from location_data.resolver.version import RESOLVER_VERSION
from tests.location_data import mini_mirror as mm


def _bind(claims, *, mirror=None, pin_is_precise=False):
    ctx = mm.context(mirror)
    binding, _ = step_bind.bind(
        claims, normalize.normalize_all(claims), ctx, pin_is_precise=pin_is_precise
    )
    return binding


def _resolve(claims, mirror=None):
    return core.resolve(
        claims, mm.context(mirror), resolver_version=RESOLVER_VERSION,
        registry_version="ruian:2026-07-31",
    )


# ---------------------------------------------------- regression 1: Krásný Les (maxima)


def test_krasny_les_resolves_via_the_okres_claim():
    """maxima f60012522: the description states 'obec Krásný Les, okres Liberec' while the
    stored row carried obec Petrovice / okres Ústí nad Labem — the OTHER Krásný Les, ~100 km
    west. Five stored fields wrong at once. An undeclared pin 1.5 km from the other one must
    not pull the row there (v5.5): it refuses no named town, and the okres narrows first."""
    for pin in ([], [mm.claim(4, "coordinate", lat=50.7700, lon=13.9500, source="maxima")]):
        binding = _bind([
            mm.claim(1, "obec_name", value_text="Krásný Les", source="maxima"),
            mm.claim(3, "okres_name", value_text="Liberec", source="maxima",
                     extraction_method="regex_text"), *pin,
        ])
        assert binding.admin_unit_id == 3  # Krásný Les, okres Liberec
        assert binding.ambiguous is False


def test_an_ambiguous_bind_is_served_at_low_confidence_never_queued():
    claims = [mm.claim(1, "obec_name", value_text="Krásný Les", source="maxima")]
    assert _bind(claims).ambiguous is True
    resolution = _resolve(claims)
    assert resolution.match_confidence == "low"
    assert resolution.obec_kod in (563943, 567931)  # served, deterministically


def test_a_psc_narrows_the_homonym_decisively():
    binding = _bind([
        mm.claim(1, "obec_name", value_text="Krásný Les", source="maxima"),
        mm.claim(2, "psc", value_text="463 46", source="maxima"),
    ])
    assert binding.admin_unit_id == 3
    assert binding.ambiguous is False
    # The PSČ is an INDEPENDENT field agreeing with the name, so the pair grades `high`.
    assert set(binding.agreed) == {"obec", "psc"}


# ------------------------------------------------ regression 2: Bílovec (realitymix)


def test_bilovec_matches_through_the_deaccented_form():
    """realitymix 8375963/8375983: `locality_text: 'Bilovec'` — the diacritics-stripped form.
    The CZ municipality lookup missed it and a fuzzy STREET-level fallback landed in western
    Slovakia, ~180 km off, with obec/okres/region/ku_id all NULL."""
    binding = _bind([mm.claim(1, "obec_name", value_text="Bilovec", source="realitymix")])
    assert binding.admin_unit_id == 10
    assert binding.granularity == "obec"


def test_a_fuzzy_street_match_can_never_become_an_obec_resolution():
    """The Bílovec failure mode: a street-level fuzzy hit used as a municipality answer. The
    fuzzy rung produces a STREET binding — never an admin-unit one."""
    binding = _bind([
        mm.claim(1, "obec_name", value_text="Bilovec", source="realitymix"),
        mm.claim(2, "street_name", value_text="Slunecna", source="realitymix"),
    ])
    assert binding.rung in ("R2", "R3")
    assert binding.target_kind == "street"
    assert binding.granularity == "street"
    assert binding.obec_kod == 599212


# --------------------------------------------- regression 3: Bořislav 40 (GeocodeSOE)


def test_borislav_40_never_resolves_to_the_prague_street_that_contains_the_substring():
    """GeocodeSOE ranks `Nad Bořislavkou 487/40, Vokovice, Praha 6` FIRST for
    `SingleLine=Borislav 40` — a Prague street ~120 km from the correct village, every
    candidate scoring exactly 100. The obec constraint decides instead."""
    binding = _bind([
        mm.claim(1, "obec_name", value_text="Bořislav", source="maxima"),
        mm.claim(2, "house_number_cp", value_text="40", source="maxima"),
    ])
    assert binding.admin_unit_id == 15  # the village, not Praha
    assert binding.ruian_adm_kod != 21690278  # the Vokovice address point
    assert binding.ulice_kod != 101  # 'Nad Bořislavkou'


# ------------------------------------------- regression 4: a PSČ shared by several obce


def _psc_mirror() -> mm.MiniMirror:
    """Kraj Vysočina / okres Třebíč: Třebíč and Kožichovice both carry PSČ 674 01 (as do
    four more obce in production). Kožichovice's circle sits inside Třebíč's 5 km circle and
    has the LOWER obec code, so a code-ordered tie-break picks it first."""
    mirror = mm.default_mirror()
    mirror.units.extend([
        mm._unit(20, "kraj", 63, "Kraj Vysočina", "kraj vysocina", "k63"),
        mm._unit(21, "okres", 3710, "Třebíč", "trebic", "k63.o3710", parent=20),
        mm._unit(22, "obec", 590266, "Třebíč", "trebic", "k63.o3710.b590266", parent=21,
                 lat=49.2149, lon=15.8817, psc_set=("67401",)),
        mm._unit(23, "obec", 545309, "Kožichovice", "kozichovice", "k63.o3710.b545309",
                 parent=21, lat=49.1980, lon=15.9250, psc_set=("67401",)),
    ])
    mirror.points.extend([
        AddressPoint(kod_adm=44000001, obec_unit_id=22, obec_kod=590266, psc="67401",
                     lat=49.2149, lon=15.8817),
        AddressPoint(kod_adm=44000002, obec_unit_id=23, obec_kod=545309, psc="67401",
                     lat=49.1980, lon=15.9250),
    ])
    mirror.obec_polygons.update({
        590266: (49.2149, 15.8817, 5000.0),
        545309: (49.1980, 15.9250, 1200.0),
    })
    return mirror


def test_a_shared_psc_is_broken_by_the_pins_containing_obec_at_low_confidence():
    """bazos 18798695: 'Lokalita 674 01 Třebíč' and a link pin inside Třebíč. Six obce share
    the PSČ; before 2026-09-11 the PSČ branch skipped the qualifier ladder, the candidates
    tied at one score and the lowest admin_unit_id — Kožichovice — was served. Measured on
    24,601 bazos rows."""
    claims = [
        mm.claim(1, "psc", value_text="67401", source="bazos"),
        mm.claim(2, "coordinate", lat=49.2149, lon=15.8817, source="bazos"),
    ]
    binding = _bind(claims, mirror=_psc_mirror())
    assert binding.ambiguous is False
    assert binding.admin_unit_id == 22
    assert "coordinate_tiebreak_imprecise" in binding.relaxations
    resolution = _resolve(claims, _psc_mirror())
    assert resolution.obec_kod == 590266
    assert resolution.match_confidence == "low"  # a tie-break decided it, not a field


def test_the_pin_side_of_the_tie_wins_whichever_obec_it_lands_in():
    """v5.5 step 3: the pin REPLACES the lowest-`admin_unit_id` pick (Třebíč, unit 22). Inside
    Kožichovice, or 4 km from it and inside neither circle, it takes Kožichovice — nearest
    being a guess, at `low`."""
    for pin in ((49.1980, 15.9250), (49.1700, 15.9600)):
        binding = _bind([
            mm.claim(1, "psc", value_text="67401", source="bazos"),
            mm.claim(2, "coordinate", lat=pin[0], lon=pin[1], source="bazos"),
        ], mirror=_psc_mirror())
        assert binding.ambiguous is False
        assert binding.admin_unit_id == 23


def test_a_shared_psc_with_nothing_to_break_the_tie_is_ambiguous_and_still_answered():
    claims = [mm.claim(1, "psc", value_text="67401", source="bazos")]
    binding = _bind(claims, mirror=_psc_mirror())
    assert binding.ambiguous is True
    resolution = _resolve(claims, _psc_mirror())
    assert resolution.obec_kod in (590266, 545309)
    assert resolution.match_confidence == "low"


# ------------------------------------------------------------- the v5.5 town rule (D4)


def _town_mirror(*, with_zlate_hory_ku: bool = True) -> mm.MiniMirror:
    """The W2 cases as the live register holds them (2026-09-30): Černotín is an obec near
    Přerov AND a část/KÚ of Dnešice; Hory an obec AND a část/KÚ of Oloví; "Zlaté Hory v
    Jeseníkách" a KÚ of Zlaté Hory, 329 km from that Hory."""
    mirror = mm.default_mirror()
    towns = {557668: (60, "Dnešice", 49.6056, 13.2655, ("33401", "33443")),
             513067: (63, "Černotín", 49.5271, 17.7717, ("75368",)),
             560588: (64, "Oloví", 50.2436, 12.5595, ("35709", "35707")),
             551651: (67, "Hory", 50.2100, 12.7800, ("36001",)),
             597996: (68, "Zlaté Hory", 50.2639, 17.3958, ("79376",))}
    for code, (uid, name, lat, lon, pscs) in towns.items():
        mirror.units.append(mm._unit(uid, "obec", code, name, normalize.normalize_match_key(name),
                                     f"b{code}", lat=lat, lon=lon, psc_set=pscs))
        mirror.obec_polygons[code] = (lat, lon, 4000.0)
        mirror.points.append(AddressPoint(kod_adm=uid, obec_unit_id=uid, obec_kod=code,
                                          psc=pscs[-1], lat=lat, lon=lon))
    parts = [(61, "cast_obce", 26778, "cernotin", 557668), (62, "katastralni_uzemi", 626775,
             "cernotin", 557668), (65, "cast_obce", 111007, "hory", 560588),
             (66, "katastralni_uzemi", 711004, "hory", 560588)]
    if with_zlate_hory_ku:
        parts.append((69, "katastralni_uzemi", 793191, "zlate hory v jesenikach", 597996))
    for uid, level, code, key, town in parts:
        mirror.units.append(mm._unit(uid, level, code, key, key, f"b{town}.x{code}",
                                     parent=towns[town][0]))
    return mirror


def _bazos(town: str, psc: str, pin: tuple[float, float]):
    return [mm.claim(1, "obec_name", value_text=town, source="bazos"),
            mm.claim(2, "psc", value_text=psc, source="bazos"),
            mm.claim(3, "coordinate", lat=pin[0], lon=pin[1], source="bazos")]


def test_a_village_the_register_holds_as_a_part_or_ku_binds_the_town_its_psc_serves():
    """As a část obce / KÚ, Černotín and Hory climb to Dnešice and Oloví, whose PSČ the advert
    carries: those bind from the name, the PSČ that discriminated is an agreeing field, and
    since v5.7 the village itself is the part (1a)."""
    for town, psc, obec_kod, part in (("Černotín", "33443", 557668, 61),
                                      ("Hory", "35707", 560588, 65)):
        claims = _bazos(town, psc, (49.61, 13.27))
        binding = _bind(claims, mirror=_town_mirror())
        assert (binding.obec_kod, binding.rung, binding.admin_unit_id) == (obec_kod, "R4", part)
        assert "climbed_from_town_name" in binding.relaxations
        towns = step_bind.constraining_towns(step_bind.collect_constraints(
            claims, normalize.normalize_all(claims)), _town_mirror(), pin_is_precise=False)
        assert (towns.units[0].code, "psc" in towns.applied) == (obec_kod, True)


def test_18841980_heals_through_the_ku_and_its_composite_anchor_is_refused():
    """The slug "zlaté-hory-v-jeseníkách" matches no obec and split to a "Hory" 329 km away.
    It IS the KÚ of Zlaté Hory, whose obec carries PSČ 793 76 (R4). Without that KÚ the line's
    anchor is neither in the PSČ nor within 40 km of the pin, and the PSČ answers (R6)."""
    claims = _bazos("zlaté-hory-v-jeseníkách", "79376", (50.24194, 17.34158))
    for with_ku, rung in ((True, "R4"), (False, "R6")):
        binding = _bind(claims, mirror=_town_mirror(with_zlate_hory_ku=with_ku))
        assert (binding.obec_kod, binding.rung) == (597996, rung)


def test_only_a_psc_or_a_precise_pin_refuses_a_named_town_out_of_reach():
    """No PSČ: a name 280 km from a PRECISE pin is a namesake — the pin's town answers (R7).
    An undeclared pin is often a placeholder, so the name stands (CHECK flags it), as it does
    within 40 km or with no pin at all."""
    praha = mm.claim(1, "obec_name", value_text="Praha")
    pin = mm.claim(2, "coordinate", lat=49.7573, lon=18.0158)
    far = _bind([praha, pin], pin_is_precise=True)
    assert (far.obec_kod, far.rung) == (599212, "R7")
    assert _bind([praha, pin]).obec_kod == _bind([praha]).obec_kod == 554782
    near = _bind([mm.claim(1, "obec_name", value_text="Bílovec"), pin], pin_is_precise=True)
    assert (near.obec_kod, near.rung) == (599212, "R4")


def test_a_name_that_binds_nothing_falls_through_to_the_part_then_the_psc():
    """The `elif` is open (step 4): an unknown town name no longer leaves a PSČ unread."""
    unknown = mm.claim(1, "obec_name", value_text="Neexistující")
    binding = _bind([unknown, mm.claim(2, "psc", value_text="74301")])
    assert (binding.obec_kod, binding.rung) == (599212, "R6")
    resolution = _resolve([unknown, mm.claim(2, "cast_obce_name", value_text="Vokovice"),
                           mm.claim(3, "psc", value_text="160 00")])
    assert (resolution.obec_kod, resolution.cast_obce_kod) == (554782, 490067)


# ------------------------------------------------ the pin BIND uses is the pin it publishes


def test_the_town_is_reverse_geocoded_from_the_pin_the_row_actually_publishes():
    """Two coordinate claims — a blurred one that arrived first and a precise one that did
    not. The town used to come from `collect_constraints`' FIRST coordinate by id while
    `geom` came from `elect_pin`'s best-DECLARED one, so the row shipped a town in Bílovec
    and a pin in Praha, 300 km apart. R7/R8 are pin-derived, so CHECK skips the containment
    test by design and nothing downstream could have caught it."""
    claims = [
        mm.claim(2, "coordinate", lat=49.7573, lon=18.0158,
                 declared_precision_label="municipality"),
        mm.claim(3, "coordinate", lat=50.0755, lon=14.4378, declared_precision_label="gps"),
    ]
    assert step_bind.elect_pin(claims).id == 3
    resolution = _resolve(claims)
    assert (resolution.lat, resolution.lon) == (50.0755, 14.4378)
    assert resolution.obec_kod == 554782  # Praha, the town the PUBLISHED pin is in
    assert resolution.disputed is None


def test_the_psc_tie_break_uses_the_elected_pin_too():
    """Same defect, the other branch that reads `constraints.pin`: with a shared PSČ the
    tie-break must consult the pin the row publishes, not whichever coordinate sorted first."""
    claims = [
        mm.claim(1, "psc", value_text="67401", source="bazos"),
        mm.claim(2, "coordinate", lat=49.1980, lon=15.9250, source="bazos",
                 declared_precision_label="municipality"),
        mm.claim(3, "coordinate", lat=49.2149, lon=15.8817, source="bazos",
                 declared_precision_label="gps"),
    ]
    resolution = _resolve(claims, _psc_mirror())
    assert (resolution.lat, resolution.lon) == (49.2149, 15.8817)
    assert resolution.obec_kod == 590266  # Třebíč, where the elected pin is


# --------------------------------------------------- a PSČ is one fact, not two agreeing ones


def test_a_psc_only_bind_is_low_confidence():
    """The obec was not named, it was LOOKED UP. Seeding the qualifier list with "psc" and
    then counting "obec" + "psc" as two independent fields was one fact counted twice, and it
    graded a bare postcode `high` — the same grade a town named and corroborated gets."""
    for psc, obec_kod in (("743 01", 599212), ("160 00", 554782)):
        resolution = _resolve([mm.claim(1, "psc", value_text=psc)])
        assert resolution.obec_kod == obec_kod, psc
        assert resolution.match_confidence == "low", psc
        assert resolution.granularity == "obec", psc


def test_a_named_town_corroborated_by_its_psc_still_grades_high():
    """The counterpart: two INDEPENDENT fields — the name and the postcode — agreeing on one
    obec is exactly what `high` is for."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Krásný Les", source="maxima"),
        mm.claim(2, "psc", value_text="463 46", source="maxima"),
    ])
    assert resolution.obec_kod == 563943
    assert resolution.match_confidence == "high"


# ------------------------------------------- the margin compares like with like


def test_a_quarter_is_not_ambiguous_against_the_town_that_contains_it():
    """Reproduced: a town qualified by one field scored 45 + 5, tying the quarter below it at
    45 + 5, and the zero gap graded the row `low`. One answer containing another is not two
    answers, so the margin compares like with like."""
    resolution = _resolve([mm.claim(1, "obec_name", value_text="Praha"),
                           mm.claim(2, "cast_obce_name", value_text="Vokovice")])
    assert resolution.cast_obce_kod == 490067
    assert resolution.match_confidence == "high"


# ------------------------------------------------------- the region is the chain's last word


def test_an_okres_name_alone_binds_the_okres():
    """maxima and mmreality emit an okres name with no town. "We know the okres" is a real
    answer at a real rung, and strictly better than the `undetermined` an unbound region used
    to collapse to — the hierarchy above it fills by the ordinary chain read."""
    resolution = _resolve([mm.claim(1, "okres_name", value_text="Liberec", source="maxima")])
    assert (resolution.okres_kod, resolution.okres_name) == (3506, "Liberec")
    assert resolution.kraj_name == "Liberecký kraj"
    assert resolution.obec_kod is None
    assert resolution.granularity == "okres"
    assert resolution.match_confidence == "low"
    # And it is a COUNTRY determination: the gazetteer it came out of is the Czech one.
    assert (resolution.country_status, resolution.country_code) == ("cz", "CZ")


def test_a_kraj_name_alone_binds_the_kraj():
    resolution = _resolve([mm.claim(1, "kraj_name", value_text="Ústecký kraj")])
    assert (resolution.kraj_kod, resolution.kraj_name) == (42, "Ústecký kraj")
    assert resolution.granularity == "kraj"
    assert resolution.country_status == "cz"


def test_a_town_still_outranks_the_region_that_contains_it():
    """The region rung is the LAST one: it fires only when nothing finer bound."""
    resolution = _resolve([
        mm.claim(1, "okres_name", value_text="Liberec"),
        mm.claim(2, "obec_name", value_text="Krásný Les"),
    ])
    assert resolution.obec_kod == 563943
    assert resolution.granularity == "obec"


# ------------------------------------------- v5.7: the operator's W4 verdicts (2026-10-14)
#
# Every row below is a production listing the operator judged, on `mm.w4_mirror()` — the
# register rows behind it, read 2026-10-14. The two regression rows of 1a and the "stored
# correctly" rows of 1c are the ones W4 must not move.

_APPROXIMATE = {"declared_precision_label": "approximate_location", "blur_evidence": "declared"}


def _w4(listing: int, *claimed: tuple[str, str], pin: tuple[float, float] | None = None):
    """A bazos row as production holds it: its claims, and the post-office pin it is blurred to."""
    claims = [mm.claim(i, t, value_text=v, source="bazos", listing_id=listing)
              for i, (t, v) in enumerate(claimed, start=1)]
    if pin:
        claims += [mm.claim(90, "coordinate", lat=pin[0], lon=pin[1], source="bazos",
                            listing_id=listing),
                   mm.claim(91, "precision_declaration", value_text="approximate_location",
                            source="bazos", listing_id=listing, **_APPROXIMATE)]
    return _resolve(claims, mm.w4_mirror())


@pytest.mark.parametrize("row", mm.W4_VILLAGES, ids=lambda row: str(row[0]))
def test_1a_the_village_a_town_name_climbed_from_is_the_part(row):
    """Fifteen "stored incompletely" rows named a village that is a část of the town their PSČ
    serves; it is the part now — through the KÚ's base name where only a KÚ matched (Podolí u
    Mohelnice). 13824760 and 18827725 name the town's OWN KÚ: its eponymous část is no part."""
    listing, town, psc, (_, obec_kod, _), _, namesake, part = row
    resolution = _w4(listing, ("obec_name", town), ("psc", psc))
    assert (resolution.obec_kod, resolution.cast_obce_kod) == (obec_kod, part)
    assert resolution.granularity == ("cast_obce_or_quarter" if part else "obec")
    assert namesake is None or resolution.obec_kod != namesake[1]


def test_1a_a_climbed_village_against_a_claimed_part_publishes_no_part():
    """Fail closed, as two streets do: the advert's town names one part and its part claim
    another."""
    resolution = _w4(18772969, ("obec_name", "Hracholusky"), ("cast_obce_name", "Úlice"),
                     ("psc", "33033"))
    assert (resolution.obec_kod, resolution.cast_obce_kod) == (559555, None)


def test_1b_boudy_u_jesenice_is_boudy_in_the_jesenice_its_qualifier_names():
    """19009300: no register unit is called "Boudy u Jesenice". Stripped, "Boudy" climbs to
    three obce and one of them IS Jesenice; the PSČ agrees and Boudy is its část. The pin is
    the post office's, in Sedlčany, so CHECK says so — the names are the advert's."""
    pin = (49.641845, 14.417496)
    resolution = _w4(19009300, ("obec_name", "Boudy u Jesenice"), ("psc", "26401"), pin=pin)
    assert (resolution.obec_kod, resolution.cast_obce_kod) == (540391, 403407)
    assert resolution.disputed == "pin_outside_obec"
    alone = _w4(19009300, ("obec_name", "Boudy u Jesenice"))
    assert (alone.obec_kod, alone.cast_obce_kod) == (540391, 403407)
    towns = step_bind.constraining_towns(
        step_bind.collect_constraints(*_claims_of("Boudy u Jesenice")), mm.w4_mirror(),
        pin_is_precise=False)
    assert "qualifier_stripped" in towns.applied
    # "nad", "pod", "v" belong to real names and are never stripped: the PSČ decides.
    other = _w4(19009300, ("obec_name", "Boudy nad Jesenice"), ("psc", "26401"), pin=pin)
    assert (other.obec_kod, other.cast_obce_kod) == (541281, None)


def _claims_of(town: str):
    claims = [mm.claim(1, "obec_name", value_text=town, source="bazos")]
    return claims, normalize.normalize_all(claims)


def test_1c_a_street_line_naming_a_part_of_the_town_is_that_part():
    """18798279: "Nový Svět" is no street of Harrachov but its část, and č.p. 701 is unique in
    it (D7). 19009248: "Dlouhé" is a část of Nový Hrádek and the advert's part claim says Rzy —
    the operator ruled Rzy wrong, so neither is published."""
    harrachov = _w4(18798279, ("obec_name", "Harrachov"), ("street_name", "Nový Svět"),
                    ("house_number_cp", "701"), ("psc", "51246"), pin=(50.772076, 15.436134))
    assert (harrachov.granularity, harrachov.ruian_adm_kod) == ("address_point", 81547374)
    assert (harrachov.cast_obce_kod, harrachov.house_number_cp, harrachov.street_name) == (
        410845, "701", None)
    conflict = _w4(19009248, ("obec_name", "Nový Hrádek"), ("cast_obce_name", "Rzy"),
                   ("street_name", "Dlouhé"), ("psc", "54901"), pin=(50.354114, 16.149216))
    assert (conflict.obec_kod, conflict.cast_obce_kod, conflict.granularity) == (
        574287, None, "obec")


def test_1c_leaves_the_rows_the_operator_judged_correct_where_they_were():
    """19083768's street line IS its claimed part (Smolná 28, an address point); 17936637's
    "Janová" is a village of another obec, so Polanka nad Odrou stays its part."""
    smolna = _w4(19083768, ("obec_name", "Bělá u Jevíčka"), ("cast_obce_name", "Smolná"),
                 ("street_name", "Smolná"), ("house_number_cp", "28"), ("psc", "56943"))
    assert (smolna.ruian_adm_kod, smolna.cast_obce_kod) == (81000028, 1813)
    janova = _w4(17936637, ("obec_name", "Ostrava"), ("cast_obce_name", "Polanka nad Odrou"),
                 ("street_name", "Janová"), ("psc", "72525"))
    assert (janova.obec_kod, janova.cast_obce_kod, janova.street_name) == (
        554821, 414557, None)


def test_1d_hanusova_binds_the_street_spelled_so_and_a_part_breaks_a_genuine_tie():
    """18625515 bound no street because `name_norm` is accent-free: Praha's Hanusova (Michle)
    and Hanušova (Kbely) share the key `hanusova` — the census's "1 row with several". The
    claim is SPELLED like one of them. Liškova/Lísková is the tie spelling cannot break
    ("Liskova"): the street with a door in the advert's one part wins it."""
    hanusova = _w4(18625515, ("obec_name", "Praha"), ("cast_obce_name", "Michle"),
                   ("street_name", "Hanusova"), ("psc", "14000"), pin=(50.041266, 14.442902))
    assert (hanusova.ulice_kod, hanusova.cast_obce_kod, hanusova.granularity) == (
        447340, 490130, "street")
    for spelled, code in (("Hanusova", 447340), ("Hanušova", 447358)):
        assert _w4(1, ("obec_name", "Praha"), ("street_name", spelled)).ulice_kod == code
    claims = [mm.claim(1, "obec_name", value_text="Praha"),
              mm.claim(2, "cast_obce_name", value_text="Hloubětín"),
              mm.claim(3, "street_name", value_text="Liskova")]
    tied = step_bind.bind(claims, normalize.normalize_all(claims), mm.context(mm.w4_mirror()))[0]
    assert (tied.ulice_kod, tied.relaxations) == (485926, ("street_part_tiebreak",))
    assert _w4(1, ("obec_name", "Praha"), ("street_name", "Liskova")).ulice_kod is None


def test_1e_csa_is_the_registers_cs_armady():
    """19013478: the advert writes "ČSA"; Moravská Třebová's street is "Čs. armády"."""
    resolution = _w4(19013478, ("obec_name", "Moravská Třebová"), ("street_name", "ČSA"),
                     ("psc", "57101"), pin=(49.757538, 16.676216))
    assert (resolution.ulice_kod, resolution.street_name) == (561517, "Čs. armády")


def test_1f_a_shared_psc_yields_to_the_one_town_holding_the_street():
    """19120281: PSČ 68601 serves five obce and the post-office pin sits in Staré Město; "Na
    Splávku" is a street of Uherské Hradiště only (Prostějov's is outside the PSČ). The pin
    still disagrees, and CHECK says so; without a street the pin decides, as before."""
    pin = (49.076565, 17.427063)
    resolution = _w4(19120281, ("street_name", "Na Splávku"), ("psc", "68601"), pin=pin)
    assert (resolution.obec_kod, resolution.ulice_kod) == (592005, 611034)
    assert resolution.disputed == "pin_outside_obec"
    assert _w4(19120281, ("psc", "68601"), pin=pin).obec_kod == 550752


def _from_reading(listing: int, payload, text: str, *page: tuple[str, str],
                  pin: tuple[float, float]):
    """The reading through bazos' reading entries (V1–V4), then the resolver with the page."""
    from tests.location_data.test_text_reading import _claims

    read = sorted(_claims(payload, text).items())
    return read, _w4(listing, *read, *page, pin=pin)


def test_2a_2b_2c_the_reader_rows_resolve_where_the_operator_put_them():
    """19009239: the town's quote "v městyse Koloveč" is not verbatim, its VALUE is (2a).
    18714030: "Č.B." carries České Budějovice's initials (2b). 18907132: a not_property advert
    keeps its town and nothing else (2c). 18726990 is 2d: a bar to take over is an offer once
    the corpus is re-read; until then its town alone."""
    from scraper.bazos_parser import ad_haystack
    from tests.location_data.test_text_reading import _payload

    read, kolovec = _from_reading(19009239, _payload(
        "offer", town=("Koloveč", "v městyse Koloveč"), street=("Růžová", "Růžová, Koloveč")),
        ad_haystack("Prodej rodinného domu 186 m2 Růžová, Koloveč",
                    "V klidné části městyse Koloveč, přibližně 10 km od Staňkova."),
        ("psc", "34543"), pin=(49.469463, 13.104286))
    assert dict(read)["obec_name"] == "Koloveč"
    assert (kolovec.obec_kod, kolovec.ulice_kod) == (553816, 100005)
    read, budejovice = _from_reading(18714030, _payload(
        "offer", town=("České Budějovice", "Č.B."),
        street=("Pabláskova", "ulice Pabláskova pod nemocnicí Č.B.")),
        ad_haystack("Pronájem 2+1 u nemocnice v Č.B.",
                    "Byt 2+1 v bytovém domě, ulice Pabláskova pod nemocnicí Č.B."),
        ("psc", "37001"), pin=(48.951930, 14.441528))
    assert dict(read)["obec_name"] == "České Budějovice"
    assert (budejovice.obec_kod, budejovice.ulice_kod) == (544256, 75817)
    read, sadova = _from_reading(18907132, _payload(
        "not_property", town=("Sadová", "503 15 Sadová"),
        street=("Sadová", "adresa: Sadová 130, 503 15 Sadová"),
        house_number_cp=("130", "Sadová 130")),
        ad_haystack("ABI Phoenix 30 Dny otevřených dveří: 21 MOBILNÍCH DOMŮ n",
                    "* adresa: Sadová 130, 503 15 Sadová"),
        ("psc", "50315"), pin=(50.256644, 15.637665))
    assert read == [("obec_name", "Sadová")]
    assert (sadova.obec_kod, sadova.ulice_kod) == (573191, None)
    bar = ad_haystack("Přenechání kompletně vybaveného baru – Masarykovo náměstí",
                      "Bar přímo v centru Hradce Králové, na Masarykově náměstí.")
    cells = {"town": ("Hradec Králové", "přímo v centru Hradce Králové"),
             "street": ("Masarykovo náměstí", "na Masarykově náměstí")}
    for kind, street in (("not_property", None), ("offer", 128899)):
        _, resolution = _from_reading(18726990, _payload(kind, **cells), bar,
                                      ("psc", "50002"), pin=(50.196572, 15.806580))
        assert (resolution.obec_kod, resolution.ulice_kod) == (569810, street), kind

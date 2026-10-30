"""CHECK — step 4 of 4: which country this is, and whether the row disagrees with itself.

Two halves, and they were two separate stages before W2-a. The country determination was S2
(310 lines, a four-signal ladder with a method and a confidence of its own); the
self-consistency comparison was S9, the reconciler — eight rules, a contradiction ledger, a
disposition table and an auto-close engine. They collapse into one function and one nullable
`disputed` column whose value IS the reason.

Every rejection tested here is a verified corpus trap that would otherwise flag EVERY
listing on at least one portal: `Zahraniční nemovitosti` is site nav on 100 % of mmreality
and ceskereality pages, the REMAX footer lists twelve countries, EUR is standard practice on
CZ commercial rent, and the Regus boilerplate advertises a "global network of thousands of
branches" on two Karlín/Nusle listings.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from location_data.resolver import check as step_check
from location_data.resolver import core
from location_data.resolver.version import RESOLVER_VERSION
from tests.location_data import mini_mirror as mm


def _resolve(claims, mirror=None):
    return core.resolve(
        claims, mm.context(mirror), resolver_version=RESOLVER_VERSION,
        registry_version="ruian:2026-07-31",
    )


# --------------------------------------------------------------- mandatory rejections


@pytest.mark.parametrize(
    "text, trap",
    [
        ("Zahraniční nemovitosti", "site_nav_zahranicni_nemovitosti"),
        ("Prodej bytu, cena 250 000 EUR", "eur_denomination"),
        ("Cena 4 500 €/měsíc", "eur_denomination"),
        ("Regus — globální síť tisíců poboček", "regus_boilerplate"),
        (
            "Austria Belgium Bulgaria Croatia Czechia France Germany Italy Poland Spain",
            "country_list_footer",
        ),
    ],
)
def test_the_four_named_traps_are_rejected_by_content(text, trap):
    assert step_check.is_rejected_country_evidence(text) == trap


def test_a_nav_country_claim_never_reaches_a_determination():
    claims = [
        mm.claim(1, "country", value_text="Zahraniční nemovitosti", source="mmreality",
                 extraction_method="html_selector_parse"),
    ]
    assert _resolve(claims).country_status == "undetermined"


def test_a_subject_scoped_false_claim_is_inadmissible_by_construction():
    claims = [
        mm.claim(1, "country", value_text="Španělsko", subject_scoped=False,
                 extraction_method="regex_text"),
    ]
    assert _resolve(claims).country_status == "undetermined"


def test_a_eur_price_sentence_never_makes_a_listing_foreign():
    claims = [
        mm.claim(1, "address_line_verbatim",
                 value_text="Kancelář Praha 8 - Karlín, nájem 18 EUR/m2, Španělsko",
                 extraction_method="regex_text"),
    ]
    assert _resolve(claims).country_status != "foreign"


def test_a_country_named_mid_sentence_is_not_an_address_tail():
    assert step_check.country_from_text("byt jako ve Španělsko u moře, Praha 6") is None


# ------------------------------------------------------------------- the determination


def test_a_trailing_country_token_is_foreign_and_nothing_else():
    """idnes ships the country in Czech, in a stored text column: 22 545 active rows. A
    foreign row carries the code and NO Czech hierarchy — there is none to carry."""
    resolution = _resolve([
        mm.claim(1, "address_line_verbatim", value_text="Benahavís, Španělsko",
                 source="idnes", extraction_method="portal_structured_field"),
        mm.claim(2, "coordinate", lat=36.5090, lon=-4.8856, source="idnes"),
    ])
    assert (resolution.country_status, resolution.country_code) == ("foreign", "ES")
    assert resolution.obec_kod is None and resolution.obec_name is None
    assert resolution.granularity == "country"
    # The pin is KEPT — it is geographically correct, it is simply not in Czechia.
    assert (resolution.lat, resolution.lon) == (36.5090, -4.8856)


def test_the_bazos_psc_buckets_are_country_signals_not_postcodes():
    resolution = _resolve([mm.claim(1, "psc", value_text="987 65", source="bazos")])
    assert (resolution.country_status, resolution.country_code) == ("foreign", "SK")


def test_the_zahranici_bucket_is_foreign_with_no_code():
    resolution = _resolve([mm.claim(1, "obec_name", value_text="Zahraničí", source="bazos")])
    assert (resolution.country_status, resolution.country_code) == ("foreign", None)
    assert resolution.granularity == "unknown"


def test_the_bbox_never_determines_foreign():
    """The Wisła hotel sits 0.008° outside the box and the Italian row's coordinates are
    geographically correct for Scalea: those pins are *uncountried, not wrong*. Outside the
    bbox with no other signal is `undetermined`, never `foreign`."""
    mirror = mm.default_mirror()
    mirror.cz_polygon = None
    mirror.obec_polygons = {}
    resolution = _resolve([mm.claim(1, "coordinate", lat=49.65, lon=19.20)], mirror)
    assert resolution.country_status == "undetermined"
    assert resolution.granularity == "unknown"


def test_inside_the_bbox_with_no_boundary_pack_is_still_czech():
    """Degraded mode only: with the state polygon unloaded the bbox is the last signal
    standing, and it may say CZ — it may never say foreign."""
    mirror = mm.default_mirror()
    mirror.cz_polygon = None
    mirror.obec_polygons = {}
    resolution = _resolve([mm.claim(1, "coordinate", lat=50.0755, lon=14.4378)], mirror)
    assert resolution.country_status == "cz"


def test_registry_containment_is_authoritative_for_cz():
    resolution = _resolve([mm.claim(1, "coordinate", lat=50.0755, lon=14.4378)])
    assert (resolution.country_status, resolution.country_code) == ("cz", "CZ")


def test_a_listing_with_no_claims_at_all_is_undetermined_never_foreign():
    """Rule 25: foreign is a determination the resolver MAKES. The row exists and says
    `undetermined`, which is how coverage counts it."""
    resolution = core.resolve(
        [], mm.context(), resolver_version=RESOLVER_VERSION,
        registry_version="ruian:2026-07-31", listing_id=4242, source="remax",
    )
    assert resolution.country_status == "undetermined"
    assert resolution.granularity == "unknown"
    assert resolution.listing_id == 4242 and resolution.source == "remax"


# -------------------------------------------------------------------- the three reasons


def test_a_clean_row_is_not_disputed():
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "coordinate", lat=50.0755, lon=14.4378),
    ])
    assert resolution.disputed is None


def test_a_pin_outside_the_resolved_obec_keeps_the_pin_and_drops_to_the_admin_level():
    """The pin says Bílovec, the name says Praha. Both are kept — the pin because it is the
    only position there is, the town because the name is the stronger claim — and the row
    says so in one word.

    NO street here, deliberately. W18 gave a bound street a point of its own, and a row that
    has one no longer has "the pin because it is the only position there is" — it takes the
    register's point and disputes `pin_off_street` instead (the test below). This rail is
    about the rows that still have nothing else. No PSČ and an undeclared pin (v5.5): the town
    rule keeps the name — 56 realitymix pins sit on the centre of Czechia — and this flags it.

    UNDECLARED pin, deliberately: since v5.9 a pin the portal itself calls approximate yields
    the position to the words (the tests below); a pin nobody called approximate is kept, as
    which half is wrong there is not ruled."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "coordinate", lat=49.7573, lon=18.0158),
    ])
    assert resolution.disputed == "pin_outside_obec"
    assert resolution.obec_kod == 554782
    assert (resolution.lat, resolution.lon) == (49.7573, 18.0158)
    assert resolution.granularity == "obec"


# ------------------------------------------------ v5.9: the words decide (ruling 2026-10-30)

_APPROXIMATE = {"value_text": "approximate_location",
                "declared_precision_label": "approximate_location", "blur_evidence": "declared"}


def test_a_declared_blurred_pin_outside_the_named_town_yields_the_position_to_the_words():
    """Operator ruling 2026-10-30 ("the words"). bazos stamps every pin "Přibližná lokalita"
    and places it on a POSTAL-AREA point: one Kolín pin carried 143 active ads from 34 obce,
    and listing 19509113 said Jestřabí Lhota while its row drew Kolín, 8.8 km off at r = 1 km
    — 12,543 active bazos rows in that state. A pin the portal itself calls approximate, in a
    town the ad does not name, is the half that yields: FILL stands the row at the town's own
    point, the pin stays EVIDENCE (CHECK still says `pin_outside_obec`), the grain is the
    bind's and the blurred pin caps the confidence at `medium`, exactly as before."""
    claims = [
        mm.claim(1, "obec_name", source="bazos", value_text="Praha"),
        mm.claim(2, "psc", source="bazos", value_text="16000"),
        mm.claim(3, "coordinate", source="bazos", lat=49.7573, lon=18.0158),
        mm.claim(4, "precision_declaration", source="bazos", **_APPROXIMATE),
    ]
    resolution = _resolve(claims)
    assert resolution.disputed == "pin_outside_obec"
    assert resolution.obec_kod == 554782
    assert (resolution.lat, resolution.lon) == (50.0755, 14.4378)   # Praha's own point
    assert resolution.granularity == "obec"
    assert resolution.match_confidence == "medium"
    assert resolution.uncertainty_radius_m == 1000.0
    # v5.8's floor applies to this placement as to every FILL placement: the town's reach,
    # not the level constant, is the radius a reader sees.
    mirror = mm.default_mirror()
    mirror.units[:] = [replace(u, extent_m=17_600.0) if u.unit_id == 13 else u
                       for u in mirror.units]
    assert _resolve(claims, mirror).uncertainty_radius_m == 17_600.0


def test_a_declared_blurred_pin_outside_the_town_bound_to_a_part_stands_at_the_parts_doors():
    """The words name the quarter too: the row stands where v5.8 puts a pinless část — the
    centroid of its own doors — at the quarter grain, and the flag still records the pin."""
    resolution = _resolve([
        mm.claim(1, "obec_name", source="bazos", value_text="Praha"),
        mm.claim(2, "cast_obce_name", source="bazos", value_text="Vokovice"),
        mm.claim(3, "coordinate", source="bazos", lat=49.7573, lon=18.0158),
        mm.claim(4, "precision_declaration", source="bazos", **_APPROXIMATE),
    ])
    assert resolution.disputed == "pin_outside_obec"
    assert (resolution.obec_kod, resolution.cast_obce_kod) == (554782, 490067)
    assert resolution.granularity == "cast_obce_or_quarter"
    assert 50.1010 < resolution.lat < 50.1012 and 14.3480 < resolution.lon < 14.3482
    assert resolution.uncertainty_radius_m == 750.0


def test_a_declared_blurred_pin_inside_the_named_town_stays_the_position():
    """Nothing moves where nothing disagrees: a blurred pin inside the town it names is the
    finer of two true answers and keeps the position, at the obec grain and `medium`."""
    resolution = _resolve([
        mm.claim(1, "obec_name", source="bazos", value_text="Praha"),
        mm.claim(2, "coordinate", source="bazos", lat=50.0900, lon=14.4500),
        mm.claim(3, "precision_declaration", source="bazos", **_APPROXIMATE),
    ])
    assert resolution.disputed is None
    assert (resolution.lat, resolution.lon) == (50.0900, 14.4500)
    assert resolution.granularity == "obec"
    assert resolution.match_confidence == "medium"


def test_a_town_the_pin_itself_established_never_yields_its_own_pin():
    """BIND's pin-derived rungs: the town CAME from the pin, so the containment question is
    circular and is not asked — a reverse-geocoded row (R7) and a sliver row 100 m outside
    its polygon (R8) both keep the pin, with no dispute."""
    reverse = _resolve([
        mm.claim(1, "coordinate", source="bazos", lat=49.7573, lon=18.0158),
        mm.claim(2, "precision_declaration", source="bazos", **_APPROXIMATE),
    ])
    assert (reverse.obec_kod, reverse.disputed) == (599212, None)
    assert (reverse.lat, reverse.lon) == (49.7573, 18.0158)
    sliver = _resolve([
        mm.claim(1, "coordinate", source="bazos", lat=49.79413, lon=18.0158),
        mm.claim(2, "precision_declaration", source="bazos", **_APPROXIMATE),
    ])
    assert (sliver.obec_kod, sliver.disputed) == (599212, None)
    assert (sliver.lat, sliver.lon) == (49.79413, 18.0158)


def test_a_pin_that_cannot_be_on_the_named_street_loses_to_the_street():
    """W18. The same disagreement with a street on the row: the street BINDS to the register
    and the pin cannot be anywhere near it, so the register places the listing and the pin is
    the half that is called out. The granularity does NOT drop — the address identity is the
    half that bound; the coordinate is the half that lost.

    The pin is INSIDE Praha, deliberately: a pin in the wrong town is a coarser disagreement
    and is reported instead (the test below)."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "street_name", value_text="Nad Bořislavkou"),
        mm.claim(3, "coordinate", lat=50.0600, lon=14.4200,
                 declared_precision_label="gps"),
    ])
    assert resolution.disputed == "pin_off_street"
    assert resolution.obec_kod == 554782
    assert resolution.ulice_kod == 101
    assert (resolution.lat, resolution.lon) != (50.0600, 14.4200)
    assert resolution.granularity == "street"
    # A disagreement is never served above `medium`, however many fields agreed.
    assert resolution.match_confidence == "medium"


def test_a_blurred_pin_outside_the_town_is_still_flagged_once_the_street_places_the_row():
    """W18's own regression. bazos declares EVERY pin approximate, so once a street bound, the
    pin stopped being the position on every bazos row — and the `pin_outside_obec` flag went
    with it on 3,421 production rows, exactly where it says the obec or the bind is wrong.

    CHECK reads the elected COORDINATE CLAIM, not the published position: a pin that lost the
    position is still evidence about which town this is. The row keeps the street's own point
    and the street grain — the address identity is the half that bound — and says so."""
    resolution = _resolve([
        mm.claim(1, "obec_name", source="bazos", value_text="Praha"),
        mm.claim(2, "street_name", source="bazos", value_text="Nad Bořislavkou"),
        mm.claim(3, "coordinate", source="bazos", lat=49.7573, lon=18.0158),
        mm.claim(4, "precision_declaration", source="bazos",
                 value_text="approximate_location",
                 declared_precision_label="approximate_location", blur_evidence="declared"),
    ])
    assert resolution.disputed == "pin_outside_obec"
    assert resolution.granularity == "street"
    assert resolution.ulice_kod == 101
    assert (resolution.lat, resolution.lon) != (49.7573, 18.0158)   # the street's centroid
    assert 50.09 < resolution.lat < 50.11 and 14.34 < resolution.lon < 14.35


def test_an_exact_pin_outside_the_town_outranks_the_off_street_reason():
    """The precedence, stated: both are true of this row and the coarser one is reported. A
    pin in the wrong TOWN says something about the row's identity; a pin merely off its street
    says something about the coordinate alone."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "street_name", value_text="Nad Bořislavkou"),
        mm.claim(3, "coordinate", lat=49.7573, lon=18.0158,
                 declared_precision_label="gps"),
        mm.claim(4, "psc", value_text="160 00"),
    ])
    assert resolution.disputed == "pin_outside_obec"
    assert resolution.granularity == "street"


def test_a_pin_outside_czechia_with_a_czech_town_is_disputed_not_foreign():
    """Almost always a geocoder artifact; the town is the trustworthy half, so the row stays
    Czech and carries the reason."""
    mirror = mm.default_mirror()
    mirror.cz_polygon = (49.8, 15.5, 1000.0)  # a state polygon that covers almost nothing
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "coordinate", lat=50.0755, lon=14.4378),
    ], mirror)
    assert resolution.country_status == "cz"
    assert resolution.disputed == "pin_outside_cz"


def test_text_that_says_foreign_over_a_czech_town_is_a_conflict_never_a_silent_flip():
    """remax 442804 is genuinely in Poland and the portal files it as Opava; three of the
    five corpus `foreign_suspect` rows are the inverse — pure geocoder artifacts. Trusting
    either side unconditionally is wrong in both directions."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "foreign_indicator", value_text="Polsko", extraction_method="llm_text",
                 source="remax"),
    ])
    assert resolution.country_status == "cz"
    assert resolution.disputed == "country_conflict"


def test_the_containment_test_asks_about_the_pin_and_never_about_the_published_point():
    """A registry point is inside its own town by construction, so it is never what the
    containment test asks about — the ad's own PIN is, whether or not that pin became the
    position (W18). Here the row keeps the address point it bound, keeps its grain, and still
    reports that the ad's coordinate is 400 km from the town it names."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "street_name", value_text="Nad Bořislavkou 487/40"),
        mm.claim(3, "coordinate", lat=49.7573, lon=18.0158),
    ])
    assert resolution.ruian_adm_kod == 21690278
    assert (resolution.lat, resolution.lon) == (50.101, 14.348)
    assert resolution.granularity == "address_point"
    assert resolution.disputed == "pin_outside_obec"


def test_a_row_with_no_pin_at_all_disputes_nothing():
    """The other side of the same read: no coordinate claim, no containment question."""
    resolution = _resolve([
        mm.claim(1, "obec_name", value_text="Praha"),
        mm.claim(2, "street_name", value_text="Nad Bořislavkou 487/40"),
    ])
    assert resolution.ruian_adm_kod == 21690278
    assert resolution.disputed is None

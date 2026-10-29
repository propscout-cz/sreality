"""Argument models generated from toolkit/filter_registry (#2062): one declaration per filter."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.filter_args import Args, Box, Chip, agenda_ids, as_str_list, filter_model, filter_values
from api.location_filter import DistrictChip, MapBounds
from toolkit import ComparableFilters
from toolkit import filter_registry as fr
from toolkit.neighborhoods import describe_neighborhood

COMPARABLES = agenda_ids(fr.Agenda.COMPARABLES)
Cohort = filter_model("Cohort", COMPARABLES, ComparableFilters)


def test_agenda_ids_are_the_registry_order_of_the_agendas_filters() -> None:
    assert COMPARABLES == tuple(f.id for f in fr.REGISTRY.values() if fr.Agenda.COMPARABLES in f.agendas)
    assert {"min_price_per_m2", "max_garden_area", "last_seen_min_days"} <= set(COMPARABLES)


def test_the_fields_are_exactly_the_ids() -> None:
    assert tuple(Cohort.model_fields) == COMPARABLES


def test_defaults_come_from_the_consumer_never_from_the_browse_ui_default() -> None:
    m = Cohort()
    assert (m.radius_m, m.area_band_pct, m.disposition_match, m.include_unreliable) == (1000, 0.2, "exact", False)
    # The registry's own defaults are Browse's (byt, pronajem): a cohort tool never inherits them.
    assert m.category_main is None and m.category_type is None
    assert filter_values(m) == {f: getattr(ComparableFilters(), f) for f in COMPARABLES}


def test_a_field_is_nullable_exactly_when_its_consumer_default_is_none() -> None:
    assert Cohort(category_main=None, floor_band=None).floor_band is None
    for fid in ("radius_m", "area_band_pct", "disposition_match", "include_unreliable"):
        with pytest.raises(ValidationError):
            Cohort(**{fid: None})


def test_required_means_present_but_nullable() -> None:
    Required = filter_model("Required", COMPARABLES, ComparableFilters, required={"category_main"})
    with pytest.raises(ValidationError, match="category_main"):
        Required()
    assert Required(category_main=None).category_main is None


def test_vocabularies_are_enforced_and_bare_strings_become_lists() -> None:
    with pytest.raises(ValidationError):
        Cohort(category_main="byty")
    with pytest.raises(ValidationError):
        Cohort(portals=["sreality.cz"])
    assert Cohort(furnished="ano").furnished == ["ano"]
    assert Cohort(portals=["realitymix", "web"], condition_match=["__unknown__"]).portals == ["realitymix", "web"]
    assert as_str_list("x") == ["x"] and as_str_list(None) is None and as_str_list(["x"]) == ["x"]


def test_the_registry_min_is_a_floor_and_its_slider_max_is_never_a_bound() -> None:
    with pytest.raises(ValidationError):
        Cohort(radius_m=50)
    with pytest.raises(ValidationError):
        Cohort(min_price_czk=-1)
    # The registry's max is a slider axis (price ≤ 100 000): a sale band must pass.
    assert Cohort(max_price_czk=5_000_000, max_estate_area=20_000.0).max_price_czk == 5_000_000
    schema = Cohort.model_json_schema()["properties"]
    # The only maximum on an integer filter is its int4 column type, never the slider's 10 000 m.
    assert schema["radius_m"]["minimum"] == 100 and schema["radius_m"]["maximum"] == 2**31 - 1
    with pytest.raises(ValidationError):
        Cohort(radius_m=2**31)


def test_unknown_keys_are_refused_and_strings_parse() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden|Extra inputs"):
        Cohort(price_min=1)
    assert Cohort(has_balcony="false").has_balcony is False
    assert issubclass(Cohort, Args)


def test_descriptions_are_the_registrys_with_the_unit_and_basis_they_point_at() -> None:
    for fid, field in Cohort.model_fields.items():
        assert field.description.startswith(fr.REGISTRY[fid].description)
    ppm2 = Cohort.model_fields["min_price_per_m2"].description
    assert ppm2.endswith("Unit: CZK/m²; basis: depends_on_category.")
    assert Cohort.model_fields["has_lift"].description == fr.REGISTRY["has_lift"].description


def test_a_consumer_whose_semantics_differ_restates_the_description() -> None:
    Neighbourhood = filter_model("N", ("max_age_days",), describe_neighborhood,
                                 descriptions={"max_age_days": "Drop ads last seen more than N days ago."})
    assert Neighbourhood.model_fields["max_age_days"].description == "Drop ads last seen more than N days ago. Unit: days."


def test_recency_without_the_active_gate_is_refused_because_the_cohort_would_drop_it() -> None:
    with pytest.raises(ValidationError, match="lifecycle='active'"):
        Cohort(max_age_days=30)
    with pytest.raises(ValidationError, match="lifecycle='active'"):
        Cohort(max_age_days=30, lifecycle="all")
    assert Cohort(max_age_days=30, lifecycle="active").max_age_days == 30
    # A consumer that applies recency without a lifecycle gate takes it alone.
    assert filter_model("N", ("max_age_days",), describe_neighborhood)(max_age_days=30).max_age_days == 30


def test_a_filter_the_consumer_cannot_take_cannot_be_generated() -> None:
    with pytest.raises(KeyError, match="has_lift"):
        filter_model("Neighbourhood", ("radius_m", "has_lift"), describe_neighborhood)


def test_an_unsupported_filter_type_raises() -> None:
    def consumer(location: object = None) -> None: ...
    with pytest.raises(KeyError):
        filter_model("Bad", ("location",), consumer)


def test_filter_values_leaves_out_fields_that_are_no_filter() -> None:
    class WithOption(Cohort):
        min_results: int = 5
    assert "min_results" not in filter_values(WithOption())


# --- a mapping consumer, integer vocabularies and the composites (#2063) ------------------------------------------

Browse = filter_model("Browse", tuple(i for i in agenda_ids(fr.Agenda.BROWSE) if i != "location"), None)


def test_a_mapping_consumer_takes_the_registrys_own_defaults() -> None:
    m = Browse()
    assert (m.status, m.show_dismissed, m.category_type, m.tags) == ("any", False, "pronajem", None)


def test_an_integer_vocabulary_is_enforced_so_the_compiler_never_meets_another_value() -> None:
    for fid, bad in (("price_change_window_days", 45), ("recently_added_days", 2), ("recently_changed_days", 0)):
        with pytest.raises(ValidationError, match=fid):
            Browse(**{fid: bad})
    assert Browse(price_change_window_days=90, recently_added_days=7).price_change_window_days == 90
    schema = Browse.model_json_schema()["properties"]["recently_added_days"]["anyOf"][0]
    assert schema == {"enum": [1, 3, 7, 14, 30], "type": "integer"}


def test_the_composites_are_strict_here_while_the_stored_watchdog_blobs_stay_lenient() -> None:
    chip = {"name": "Brno", "level": "obec", "id": 582786, "legacy": True}
    with pytest.raises(ValidationError, match="legacy"):
        Browse(districts=[chip])
    assert DistrictChip.model_validate(chip).id == 582786 and issubclass(Chip, DistrictChip)
    box = {"west": 14.0, "south": 50.0, "east": 15.0, "north": 51.0, "zoom": 9}
    with pytest.raises(ValidationError, match="zoom"):
        Box.model_validate(box)
    assert MapBounds.model_validate(box).north == 51.0
    with pytest.raises(ValidationError, match="op"):
        Browse(city_index_rules=[{"index_name": "bezpecnost", "op": "~", "value": 6}])
    with pytest.raises(ValidationError, match="all"):
        Browse(tags={"set": "in", "all": [1]})


def test_filter_values_hands_a_composite_on_as_its_wire_json() -> None:
    m = Browse(tags={"in": [3]}, city_index_rules=[{"index_name": "bezpecnost", "op": ">=", "value": 6}],
               districts=[{"name": "Brno", "level": "obec", "id": 582786}])
    values = filter_values(m)
    assert values["tags"] == {"set": None, "in": [3], "out": []}
    assert values["city_index_rules"] == [{"index_name": "bezpecnost", "op": ">=", "value": 6.0}]
    assert values["districts"][0] == {"name": "Brno", "context": None, "excluded": False, "level": "obec",
                                      "id": 582786}

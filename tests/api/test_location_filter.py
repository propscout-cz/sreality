"""The ONE place predicate, API side (`api.location_filter`).

W3 S3 replaced five chip predicates — obec/okres/region equality, a `locality`
pair of obec-equality AND a `place_search_text` ILIKE, and a legacy name
fallback ILIKE-ing across four text columns AND-ed with a `context` narrow —
with `<level>_id = any(codes)` over five code columns. These tests pin the
compiled shape and the model that makes a chip without a (level, code)
impossible to construct.

The cross-surface table (this predicate compiles identically in the SPA, the API
and the two RPC bodies) lives in `tests/test_one_place_predicate.py`.
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from api.location_filter import (
    LEVEL_COLUMN,
    LEVEL_ORDER,
    DistrictChip,
    district_where,
)


def test_no_chips_is_a_no_op() -> None:
    where, params = district_where(None, alias="l")
    assert where == []
    assert params == {}
    where, params = district_where([], alias="l")
    assert where == []
    assert params == {}


def test_one_resolved_chip_is_one_equality_on_its_level() -> None:
    where, params = district_where(
        [DistrictChip(name="Jihlava", level="obec", id=586846)], alias="l",
    )
    assert where == ["(l.obec_id = ANY(%(district_codes_obec)s))"]
    assert params == {"district_codes_obec": [586846]}


def test_chips_at_one_level_collapse_into_one_array() -> None:
    where, params = district_where(
        [
            DistrictChip(name="Jihlava", level="obec", id=586846),
            DistrictChip(name="Brno", level="obec", id=582786),
        ],
        alias="l",
    )
    assert where == ["(l.obec_id = ANY(%(district_codes_obec)s))"]
    assert params == {"district_codes_obec": [586846, 582786]}


def test_levels_or_together_coarsest_first() -> None:
    where, params = district_where(
        [
            DistrictChip(name="Žižkov", level="cast_obce", id=490067),
            DistrictChip(name="Jihlava", level="okres", id=3707),
            DistrictChip(name="Vysočina", level="kraj", id=108),
        ],
        alias="l",
    )
    assert where == [
        "(l.region_id = ANY(%(district_codes_kraj)s) "
        "OR l.okres_id = ANY(%(district_codes_okres)s) "
        "OR l.cast_obce_id = ANY(%(district_codes_cast_obce)s))"
    ]
    assert params == {
        "district_codes_kraj": [108],
        "district_codes_okres": [3707],
        "district_codes_cast_obce": [490067],
    }


def test_a_street_chip_filters_at_its_street_never_its_town() -> None:
    """The defect of 2026-10-02: a street pick filtered its whole obec. It is the
    fifth level now — strict equality on the RÚIAN street code."""
    where, params = district_where(
        [DistrictChip(name="Na Rybníčkách", context="Pardubice", level="ulice", id=666742)],
        alias="l",
    )
    assert where == ["(l.ulice_id = ANY(%(district_codes_ulice)s))"]
    assert params == {"district_codes_ulice": [666742]}


def test_five_levels_one_list_finest_last() -> None:
    assert LEVEL_ORDER == ("kraj", "okres", "obec", "cast_obce", "ulice")
    assert tuple(LEVEL_COLUMN) == LEVEL_ORDER
    assert LEVEL_COLUMN["ulice"] == "ulice_id"


@pytest.mark.parametrize("chip", [
    {"name": "Brno"},
    {"name": "Brno", "context": "Jihomoravský kraj"},
    {"name": "Brno", "level": "obec"},
    {"name": "Brno", "level": "obec", "id": None},
    {"name": "Brno", "id": 582786},
    {"name": "Lipová", "context": "Praha", "level": "locality", "id": 554782},
    {"name": "Praha", "level": "obec", "id": -1},
    {"name": "Praha", "level": "obec", "id": 0},
], ids=["name only", "name+context", "level no id", "id null", "id no level",
        "stale locality", "the old sentinel", "zero"])
def test_a_chip_without_a_level_and_a_positive_code_cannot_exist(chip) -> None:
    """A code-less chip used to compile to a no-match sentinel; now it is refused
    where it enters (a 422 on every write), so no compiler needs a branch for it."""
    with pytest.raises(ValidationError):
        DistrictChip(**chip)


def test_excluded_chips_are_negated_separately() -> None:
    where, params = district_where(
        [
            DistrictChip(name="Praha", level="obec", id=554782),
            DistrictChip(name="Modřany", level="cast_obce", id=490017, excluded=True),
        ],
        alias="l",
    )
    assert where == [
        "(l.obec_id = ANY(%(district_codes_obec)s))",
        "NOT COALESCE(l.cast_obce_id = ANY(%(district_codes_excl_cast_obce)s), false)",
    ]
    assert params == {
        "district_codes_obec": [554782],
        "district_codes_excl_cast_obce": [490017],
    }


def test_exclude_only_filter_keeps_the_whole_cohort_minus_the_chips() -> None:
    where, _params = district_where(
        [DistrictChip(name="Praha", level="obec", id=554782, excluded=True)], alias="l",
    )
    assert where == [
        "NOT COALESCE(l.obec_id = ANY(%(district_codes_excl_obec)s), false)"
    ]


def test_an_exclude_subtracts_only_what_it_matches() -> None:
    """A row with NO code at the excluded level survives the exclude.

    `NOT (col = ANY(...))` is NULL for a NULL col, and a NULL WHERE drops the
    row — so the bare spelling made "not Prague" also mean "and located", while
    the RPC's `not exists(... case ...)` and the SPA's in-memory `!hits` both
    kept such a row. COALESCE is what makes the four renderings one predicate."""
    where, _ = district_where(
        [DistrictChip(name="Praha", level="obec", id=554782, excluded=True)], alias="l",
    )
    assert where[0].startswith("NOT COALESCE(")
    assert where[0].endswith(", false)")


def test_no_text_column_is_read_any_more() -> None:
    chips = [
        DistrictChip(name="Praha", level="obec", id=554782),
        DistrictChip(name="Žižkov", context="Praha", level="cast_obce", id=490067),
        DistrictChip(name="Ostrava", level="okres", id=3811, excluded=True),
    ]
    where, _ = district_where(chips, alias="l")
    blob = " ".join(where)
    for gone in ("l.district", "place_search_text", "l.okres ", "l.region ", "ILIKE"):
        assert gone not in blob, f"{gone!r} is back in the place predicate"


def test_empty_alias_raises() -> None:
    with pytest.raises(ValueError):
        district_where([DistrictChip(name="Praha", level="obec", id=554782)], alias="")

"""A tool's subject: a property or an ad we hold, or a point on the map with the attributes the caller states."""

from __future__ import annotations

from typing import Any, Literal, Self

import psycopg
from pydantic import Field, model_validator

from api.capabilities.errors import ToolError, ToolErrorCode
from api.filter_args import Args, filter_values
from toolkit import TargetSpec
from toolkit.filter_registry import COLUMN_CANONICAL_VALUES
from toolkit.property_identity import canonical_advert
from toolkit.subject import match_listing, subject_mf_facts

_SUBJECT_FIELDS = ("property_id", "listing_id", "point")
_CATEGORIES = ("category_main", "category_type")


class Point(Args):
    lat: float = Field(ge=-90, le=90, description="Latitude (WGS84).")
    lng: float = Field(ge=-180, le=180, description="Longitude (WGS84).")
    area_m2: float | None = Field(
        None, gt=0, description="Floor area in m²; with it, comparables are held to an area band around it.")
    disposition: Literal[COLUMN_CANONICAL_VALUES["disposition"]] | None = Field(
        None, description="Layout such as 2+kk; with it, disposition_match applies.")
    floor: int | None = Field(None, description="Floor number; with it, floor_band applies.")


class HeldSubject(Args):
    property_id: int | None = Field(
        None, ge=1, description="A property we hold; it stands for its canonical ad. A merged-away id follows its "
        "survivor.")
    listing_id: int | None = Field(None, ge=1, description="An ad we hold (its listing id).")

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> Self:
        declared = [f for f in _SUBJECT_FIELDS if f in type(self).model_fields]
        named = [f for f in declared if getattr(self, f) is not None]
        if len(named) != 1:
            raise ValueError(f"name exactly one subject: {' or '.join(declared)}")
        unstated = [c for c in _CATEGORIES if c in type(self).model_fields and c not in self.model_fields_set]
        if named == ["point"] and unstated:
            raise ValueError(f"a point has no category of its own: state {' and '.join(unstated)} "
                             "(null means every category)")
        return self


class Subject(HeldSubject):
    point: Point | None = Field(
        None, description="A place on the map with the attributes you state, when the subject is no ad we hold.")


def held_ad(conn: psycopg.Connection, subject: HeldSubject) -> int:
    """The ad a held subject stands for: the ad itself, or its property's canonical ad."""
    if subject.listing_id is not None:
        return subject.listing_id
    ad = canonical_advert(conn, subject.property_id)
    if ad is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"property {subject.property_id} is not in our database or has no ad")
    return ad


def held_facts(conn: psycopg.Connection, ad: int) -> dict[str, Any]:
    """The held ad's subject facts (property-canonical), or NOT_FOUND."""
    matched = match_listing(conn, ad)
    if matched is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"listing {ad} is not in our database")
    return matched


def place(conn: psycopg.Connection, args: Subject) -> tuple[TargetSpec, dict[str, Any]]:
    """The cohort target and the filter values for `args`.

    A held subject stands at its property's location ad and leaves every ad of its property out of the cohort
    (Decision 13); any category filter the call left unset takes the property's own category, so a house is never
    compared with flats by omission. An explicit null still means every category.
    """
    values = filter_values(args)
    if args.point is not None:
        return TargetSpec(**args.point.model_dump()), values
    ad = held_ad(conn, args)
    spec = held_facts(conn, ad)["spec"]
    if spec["lat"] is None:
        raise ToolError(ToolErrorCode.REFUSED, f"listing {ad} has no location yet")
    unset = [c for c in _CATEGORIES if c in values and c not in args.model_fields_set]
    if unset:
        facts = subject_mf_facts(conn, ad)   # the ad was just matched, and no ad is ever deleted (rule 3)
        values.update({c: facts[c] for c in unset})
    return TargetSpec(**spec, exclude_listing_ids=[ad]), values

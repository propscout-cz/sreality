"""A tool's subject: a property or an ad we hold, or a point on the map with the attributes the caller states."""

from __future__ import annotations

from typing import Any, Literal, Self

import psycopg
from pydantic import BaseModel, Field, model_validator

from api.capabilities.errors import ToolError, ToolErrorCode
from api.filter_args import Args, filter_values
from toolkit import TargetSpec
from toolkit.filter_registry import COLUMN_CANONICAL_VALUES
from toolkit.property_identity import canonical_advert
from toolkit.subject import match_listing, subject_mf_facts

CATEGORIES = ("category_main", "category_type")
_SUBJECT_FIELDS = ("property_id", "listing_id", "point")
# Each band is drawn around the subject's own value; the cohort skips a band whose anchor is missing.
_BANDS = (("area_band_pct", "area_m2"), ("disposition_match", "disposition"), ("floor_band", "floor"))


class Place(Args):
    lat: float = Field(ge=-90, le=90, description="Latitude (WGS84).")
    lng: float = Field(ge=-180, le=180, description="Longitude (WGS84).")


class Point(Place):
    area_m2: float | None = Field(
        None, ge=1, description="Floor area in m²; with it, comparables are held to an area band around it.")
    disposition: Literal[COLUMN_CANONICAL_VALUES["disposition"]] | None = Field(
        None, description="Layout such as 2+kk; with it, disposition_match applies.")
    floor: int | None = Field(None, description="Floor number; with it, floor_band applies.")


class HeldSubject(Args):
    property_id: int | None = Field(
        None, ge=1, description="A property we hold; it stands for its canonical ad. A merged-away id follows its "
        "survivor.")
    listing_id: int | None = Field(None, ge=1, description="An ad we hold (its listing id).")

    # Reads the model's declared fields: `point` comes from a subclass, the categories from the filter mixins.
    @model_validator(mode="after")
    def _exactly_one_subject(self) -> Self:
        declared = [f for f in _SUBJECT_FIELDS if f in type(self).model_fields]
        named = [f for f in declared if getattr(self, f) is not None]
        if len(named) != 1:
            raise ValueError(f"name exactly one subject: {' or '.join(declared)}")
        unstated = [c for c in CATEGORIES if c in type(self).model_fields and c not in self.model_fields_set]
        if named == ["point"] and unstated:
            raise ValueError(f"a point has no category of its own: state {' and '.join(unstated)} "
                             "(null means every category)")
        return self


class Subject(HeldSubject):
    point: Point | None = Field(
        None, description="A place on the map with the attributes you state, when the subject is no ad we hold.")


class PlaceSubject(HeldSubject):
    point: Place | None = Field(None, description="A place on the map, when the subject is no ad we hold.")


def sentence(args: type[Args]) -> str:
    """The subject sentence of a tool's description, naming only the categories `args` declares."""
    declared = [c for c in CATEGORIES if c in args.model_fields]
    return ("The subject is exactly one of property_id, listing_id or point. For a property or ad we hold, an unset "
            f"{' or '.join(declared)} takes the subject's own value (null means every category); a point must state "
            f"{' and '.join(declared)}.")


def held_ad(conn: psycopg.Connection, subject: HeldSubject) -> int:
    """The ad a held subject stands for: the ad itself, or its property's canonical ad."""
    if subject.listing_id is not None:
        return subject.listing_id
    ad = canonical_advert(conn, subject.property_id)
    if ad is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"property {subject.property_id} is not in our database or has no ad")
    return ad


def _named(subject: HeldSubject, ad: int) -> str:
    return f"ad {ad}" if subject.listing_id is not None else f"property {subject.property_id} (its canonical ad {ad})"


def held_spec(conn: psycopg.Connection, subject: HeldSubject) -> tuple[int, dict[str, Any]]:
    """The held subject's ad and its place and attributes (property-canonical), or NOT_FOUND / REFUSED."""
    ad = held_ad(conn, subject)
    matched = match_listing(conn, ad)
    if matched is None:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"{_named(subject, ad)} is not in our database")
    if matched["spec"]["lat"] is None:
        raise ToolError(ToolErrorCode.REFUSED, f"{_named(subject, ad)} has no location yet")
    return ad, matched["spec"]


def held_mf_facts(conn: psycopg.Connection, ad: int) -> dict[str, Any]:
    """A held ad's golden-record facts (its property's, else its own) and stored location codes."""
    return subject_mf_facts(conn, ad)   # the ad was just matched, and no ad is ever deleted (rule 3)


def unanchored(args: BaseModel, target: Any) -> list[str]:
    """Each band `args` asks for whose anchor `target` lacks, as "band needs the subject's anchor"."""
    return [f"{band} needs the subject's {anchor}" for band, anchor in _BANDS
            if band in args.model_fields_set and getattr(args, band) not in (None, "any")
            and getattr(target, anchor) is None]


def _anchored(args: Subject | PlaceSubject, target: TargetSpec, named: str) -> TargetSpec:
    """`target`, once every band the caller asked for has its anchor; a band without one is refused, not dropped."""
    if missing := unanchored(args, target):
        raise ToolError(ToolErrorCode.REFUSED, f"{'; '.join(missing)}, and {named} has none")
    return target


def place(conn: psycopg.Connection, args: Subject | PlaceSubject) -> tuple[TargetSpec, dict[str, Any]]:
    """The cohort target and the filter values for `args`."""
    values = filter_values(args)
    if args.point is not None:
        return _anchored(args, TargetSpec(**args.point.model_dump()), "the point"), values
    ad, spec = held_spec(conn, args)
    # A category left unset takes the property's own, so a house is never compared with flats by omission; an
    # explicit null still means every category.
    unset = [c for c in CATEGORIES if c in values and c not in args.model_fields_set]
    if unset:
        facts = held_mf_facts(conn, ad)
        values.update({c: facts[c] for c in unset})
    # Decision 13: every ad of the subject's property is left out of its cohort.
    return _anchored(args, TargetSpec(**spec, exclude_listing_ids=[ad]), _named(args, ad)), values

"""The ONE place predicate — a chip is a level plus a RÚIAN code (W3 S3).

Every surface that lets the operator narrow by place — Browse's list
(`districtsFilterClause`), Browse's Stats and map (`browse_stats_properties` /
`browse_map_cells`), the pipeline board's in-memory predicate, the Watchdog
matcher and the broker board (Browse's own cohort) — tests a chip the same way:

    <level>_id = any(<codes at that level>)

plain equality, per level, nothing else, over FIVE code columns: `region_id`,
`okres_id`, `obec_id`, `cast_obce_id` (migration 503) and `ulice_id`, the RÚIAN
street code (584). `listing_location` answers every listing with those codes,
and `admin_boundaries.id` IS the RÚIAN code (migrations 083/141), so a chip and a
listing come from one register.

WHAT YOU PICK IS WHAT YOU FILTER. A chip is exactly one unit at the level the
operator picked; a street chip filters at its street, never its town. A chip
without a (level, code) cannot exist: `DistrictChip` refuses one (a 422 on every
write), the typeahead never makes one, and migration 606 deleted the last stored
rows that held one. Nothing re-reads a chip by name — a name can mean several
units ("Jihlava" is an obec AND an okres), so a re-guess could only widen.

THE MAP AREA is the other spatial shape (rule 16): a box (`MapBounds`, Browse's
viewport) or a circle (`MapCircle`, Browse's centre + radius), never both. `area_where`
renders either as one call of `public.in_map_area` (migration 694), the predicate
Browse's list source and both stats RPCs test, so a watchdog saved from Browse watches
the rows Browse showed. The circle is true (metres on the WGS84 spheroid), not the
square around it. A row with no point falls out of either, as it does there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

from toolkit.filter_registry import REGISTRY

ChipLevel = Literal["kraj", "okres", "obec", "cast_obce", "ulice"]

# Coarsest first, and DETERMINISTIC: the compiled predicate must be byte-stable
# so the SPA, the API and the RPC bodies can be diffed.
LEVEL_ORDER: tuple[ChipLevel, ...] = get_args(ChipLevel)
# level -> the code column on every chip-filtered relation: `browse_projection`
# (materialized as `browse_list`), `properties_public` and `pipeline_board_public`.
LEVEL_COLUMN: dict[ChipLevel, str] = dict(zip(
    LEVEL_ORDER, ("region_id", "okres_id", "obec_id", "cast_obce_id", "ulice_id"), strict=True,
))


class DistrictChip(BaseModel):
    """One location-filter entry: the SPA's `DistrictChip` and its `districts*` URL params."""

    name: str = Field(description="The unit's name, for display only; never a predicate.")
    # Display only: the obec a část obce or a street sits in. Never a predicate.
    context: str | None = Field(None, description="The town a district or street sits in, for display only.")
    excluded: bool = Field(False, description="True subtracts this unit from the result instead of adding it.")
    level: ChipLevel = Field(description="The level the unit was picked at; a street filters at its street.")
    id: int = Field(ge=1, description="The unit's RÚIAN code at that level.")


class MapBounds(BaseModel):
    """The map box: WGS84 degrees, west < east and south < north (the SPA's `parseBounds` rule)."""

    west: float = Field(ge=-180, le=180, description="Western edge, longitude (WGS84).")
    south: float = Field(ge=-90, le=90, description="Southern edge, latitude (WGS84).")
    east: float = Field(ge=-180, le=180, description="Eastern edge, longitude (WGS84).")
    north: float = Field(ge=-90, le=90, description="Northern edge, latitude (WGS84).")

    @model_validator(mode="after")
    def _ordered(self) -> "MapBounds":
        if not (self.west < self.east and self.south < self.north):
            raise ValueError("bounds need west < east and south < north")
        return self


# Browse's radius domain is the registry's (`radius_m`): one definition for the slider and every reader.
_RADIUS = REGISTRY["radius_m"].constraints or {}


class MapCircle(BaseModel):
    """The map circle: a centre in WGS84 degrees and a radius in metres (Browse's centre + radius)."""

    lat: float = Field(ge=-90, le=90, description="The centre's latitude (WGS84).")
    lng: float = Field(ge=-180, le=180, description="The centre's longitude (WGS84).")
    radius_m: float = Field(ge=_RADIUS["min"], le=_RADIUS["max"],
                            description="The radius in metres, measured on the WGS84 spheroid.")


def one_area(bounds: MapBounds | None, circle: MapCircle | None) -> None:
    """A map area is a box or a circle, never both (Browse's one spatial value)."""
    if bounds is not None and circle is not None:
        raise ValueError("a map area is a box (bounds) or a circle, never both")


@dataclass(frozen=True)
class DistrictCodePlan:
    """Codes per level, include and exclude — what the SPA's `districtCodePlan` returns too."""

    include: dict[str, list[int]] = field(default_factory=dict)
    exclude: dict[str, list[int]] = field(default_factory=dict)


def district_code_plan(chips: Sequence[DistrictChip] | None) -> DistrictCodePlan:
    plan = DistrictCodePlan()
    for chip in chips or []:
        codes = (plan.exclude if chip.excluded else plan.include).setdefault(chip.level, [])
        if chip.id not in codes:
            codes.append(chip.id)
    return plan


def _arms(plan_side: dict[str, list[int]], alias: str, prefix: str,
          params: dict[str, Any]) -> str:
    arms: list[str] = []
    for level in LEVEL_ORDER:
        codes = plan_side.get(level)
        if not codes:
            continue
        key = f"{prefix}_{level}"
        params[key] = list(codes)
        arms.append(f"{alias}.{LEVEL_COLUMN[level]} = ANY(%({key})s)")
    return " OR ".join(arms)


def district_where(
    chips: Sequence[DistrictChip] | None,
    alias: str,
) -> tuple[list[str], dict[str, Any]]:
    """Render `chips` as parameterised WHERE fragments (AND them together with
    the rest of the caller's WHERE) plus their params dict.

    `alias` names the relation each chip is tested against — `"l"` for the
    Watchdog's `properties_public` alias. It must expose the five
    `LEVEL_COLUMN` columns; no text column is read."""
    if not chips:
        return [], {}
    if not alias:
        raise ValueError("district_where requires an alias")
    plan = district_code_plan(chips)
    params: dict[str, Any] = {}
    where: list[str] = []
    inc = _arms(plan.include, alias, "district_codes", params)
    if inc:
        where.append(f"({inc})")
    exc = _arms(plan.exclude, alias, "district_codes_excl", params)
    if exc:
        # COALESCE, because an EXCLUDE must subtract only what it MATCHES. A row
        # whose code at that level is NULL — an unresolved listing, a listing with
        # no street code — makes the comparison NULL, and a bare `NOT (NULL)` is
        # NULL, i.e. drops the row. That would make "not Prague" quietly mean "not
        # Prague AND located"; the RPC's `not exists(... case ...)` and the SPA's
        # in-memory `!hits` both KEEP such a row, and this makes SQL keep it too.
        where.append(f"NOT COALESCE({exc}, false)")
    return where, params


def area_where(
    bounds: MapBounds | None, circle: MapCircle | None, alias: str,
) -> tuple[list[str], dict[str, Any]]:
    """Render the map area as the one `in_map_area` call (migration 694); `alias` must expose `lat` + `lng`."""
    if bounds is None and circle is None:
        return [], {}
    if not alias:
        raise ValueError("area_where requires an alias")
    one_area(bounds, circle)
    box = (bounds.west, bounds.south, bounds.east, bounds.north) if bounds else (None,) * 4
    disc = (circle.lat, circle.lng, circle.radius_m) if circle else (None,) * 3
    params: dict[str, Any] = dict(zip(
        ("bbox_west", "bbox_south", "bbox_east", "bbox_north", "circle_lat", "circle_lng", "circle_radius_m"),
        (*box, *disc), strict=True,
    ))
    args = ", ".join(f"%({k})s::float8" for k in params)
    return [f"public.in_map_area({alias}.lat, {alias}.lng, {args})"], params

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

THE MAP BOX is the other spatial shape, and the only one (rule 16): `MapBounds` is
Browse's `MapBounds` (`lib/filters.ts`), the box Browse filters on — its viewport, or
the square around a centre+radius pick (`effectiveBbox`) — and `bounds_where` renders
it as the lat/lng band Browse's list (`queries.ts applyFilters`) and both stats RPCs
(`bbox_west` … `bbox_north`) apply, so a watchdog saved from Browse watches the rows
Browse showed. A row with no point falls out of the band, as it does there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

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


def bounds_where(bounds: MapBounds | None, alias: str) -> tuple[list[str], dict[str, Any]]:
    """Render the map box as the lat/lng band Browse applies; `alias` must expose `lat` + `lng`."""
    if bounds is None:
        return [], {}
    if not alias:
        raise ValueError("bounds_where requires an alias")
    return (
        [
            f"{alias}.lng >= %(bbox_west)s", f"{alias}.lng <= %(bbox_east)s",
            f"{alias}.lat >= %(bbox_south)s", f"{alias}.lat <= %(bbox_north)s",
        ],
        {
            "bbox_west": bounds.west, "bbox_east": bounds.east,
            "bbox_south": bounds.south, "bbox_north": bounds.north,
        },
    )

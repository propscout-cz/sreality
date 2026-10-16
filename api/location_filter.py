"""The ONE place predicate — a chip is a level plus a RÚIAN code (W3 S3).

Every surface that lets the operator narrow by place — Browse's list
(`districtsFilterClause`), Browse's Stats and map (`browse_stats_properties` /
`browse_map_cells`), the pipeline board's in-memory predicate, the Watchdog
matcher and the broker leaderboard (`broker_leaderboard`, migration 607: Brokers
and Outreach) — tests a chip the same way:

    <level>_id = any(<codes at that level>)

plain equality, per level, nothing else, over FIVE code columns: `region_id`,
`okres_id`, `obec_id`, `cast_obce_id` (migration 503) and `ulice_id`, the RÚIAN
street code (584). `listing_location` answers every listing with those codes,
and `admin_boundaries.id` IS the RÚIAN code (migrations 083/141), so a chip and
a listing come from one register.

WHAT YOU PICK IS WHAT YOU FILTER. A chip is exactly one unit at the level the
operator picked; a street chip filters at its street, never its town. A chip
without a (level, code) cannot exist: `DistrictChip` refuses one (a 422 on every
write), the typeahead never makes one, and migration 606 deleted the last stored
rows that held one. Nothing re-reads a chip by name — a name can mean several
units ("Jihlava" is an obec AND an okres), so a re-guess could only widen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

ChipLevel = Literal["kraj", "okres", "obec", "cast_obce", "ulice"]

# Coarsest first, and DETERMINISTIC: the compiled predicate must be byte-stable
# so the SPA, the API and the RPC bodies can be diffed.
LEVEL_ORDER: tuple[ChipLevel, ...] = get_args(ChipLevel)
# level -> the code column on every chip-filtered relation: `browse_projection`
# (materialized as `browse_list` and `properties_map_mv`), `properties_public`,
# `pipeline_board_public` and `listing_feed_public`.
LEVEL_COLUMN: dict[ChipLevel, str] = dict(zip(
    LEVEL_ORDER, ("region_id", "okres_id", "obec_id", "cast_obce_id", "ulice_id"), strict=True,
))


class DistrictChip(BaseModel):
    """One location-filter entry: the SPA's `DistrictChip` and its `districts*` URL params."""

    name: str
    # Display only: the obec a část obce or a street sits in. Never a predicate.
    context: str | None = None
    excluded: bool = False
    level: ChipLevel
    id: int = Field(gt=0)


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


def district_arrays(
    chips: Sequence[DistrictChip] | None,
) -> tuple[list[str] | None, list[bool] | None, list[str] | None, list[int] | None]:
    """The four parallel chip arguments of a chip-taking RPC — names (which drive
    its unnest), excluded flags, levels, codes — one entry per chip, all NULL for
    none: what the SPA's `buildBrowseStatsArgs` sends Browse's two RPCs."""
    if not chips:
        return None, None, None, None
    return ([c.name for c in chips], [c.excluded for c in chips],
            [c.level for c in chips], [c.id for c in chips])


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

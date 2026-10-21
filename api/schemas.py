"""Pydantic request bodies for the FastAPI service.

Responses are returned as plain dicts (the toolkit envelope) so we
don't have to re-encode every field in a Pydantic response model.
FastAPI's jsonable_encoder handles datetimes and Decimals on the way
out.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import HTTPException
from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, Field, TypeAdapter, field_validator, model_validator,
)

from api.location_filter import DistrictChip
from toolkit import stats_registry as sr


def _as_str_list(v: Any) -> Any:
    """Coerce a bare string into a single-element list (backward-compat for
    callers that still send `furnished="ano"` now that the filter is
    multi-select). None and lists pass through unchanged."""
    if isinstance(v, str):
        return [v]
    return v


# Multi-select enum filter that also accepts a single bare string.
EnumListFilter = Annotated[list[str] | None, BeforeValidator(_as_str_list)]


class TargetIn(BaseModel):
    lat: float
    lng: float
    area_m2: float | None = None
    disposition: str | None = None
    floor: int | None = None
    exclude_listing_ids: list[int] = Field(default_factory=list)


class FindComparablesIn(BaseModel):
    target: TargetIn
    radius_m: int = 1000
    area_band_pct: float = 0.20
    disposition_match: Literal["exact", "loose", "any"] = "exact"
    # No implicit freshness gate. Pass max_age_days + lifecycle='active'
    # explicitly when you want only fresh active listings; lifecycle
    # also unlocks 'delisted' / 'all' cohorts.
    max_age_days: int | None = None
    lifecycle: Literal["active", "delisted", "all"] | None = None
    floor_band: int | None = None
    portals: list[str] | None = None
    condition_match: list[str] | None = None
    building_type_match: list[str] | None = None
    energy_rating_match: list[str] | None = None
    has_balcony: bool | None = None
    has_lift: bool | None = None
    has_parking: bool | None = None
    min_price_czk: int | None = None
    max_price_czk: int | None = None
    # Required, no silent default. A missing category used to mean
    # "apartments for rent", which made house/commercial cohorts
    # impossible to drive cleanly. Pass `null` explicitly to search every
    # category. See ComparableFilters in toolkit/comparables.py.
    category_main: str | None = Field(...)
    category_type: str | None = Field(...)
    category_sub_cb: int | None = None
    include_unreliable: bool = False
    furnished: EnumListFilter = None
    terrace: bool | None = None
    cellar: bool | None = None
    garage: bool | None = None
    ownership: EnumListFilter = None
    min_estate_area: float | None = None
    max_estate_area: float | None = None
    min_usable_area: float | None = None
    max_usable_area: float | None = None
    min_parking_lots: int | None = None
    building_condition_level_min: int | None = None
    building_condition_level_max: int | None = None
    apartment_condition_level_min: int | None = None
    apartment_condition_level_max: int | None = None
    tom_days_min: int | None = None
    tom_days_max: int | None = None
    tom_observed_only: bool | None = None
    last_seen_min_days: int | None = None
    last_seen_max_days: int | None = None
    first_seen_min_days: int | None = None
    first_seen_max_days: int | None = None


class AnalyzeDistributionIn(BaseModel):
    listings: list[dict[str, Any]]
    field: Literal["price_czk", "price_per_m2", "area_m2"] = "price_per_m2"


class ClusterComparablesIn(BaseModel):
    listings: list[dict[str, Any]]
    axes: list[
        Literal["price_per_m2", "price_czk", "area_m2", "distance_m"]
    ] = Field(default_factory=lambda: ["price_per_m2"])
    n_clusters: int = Field(default=3, ge=2, le=8)
    seed: int = 42
    n_restarts: int = Field(default=5, ge=1, le=20)


class FindComparablesRelaxedIn(FindComparablesIn):
    min_results: int = Field(default=5, ge=1, le=50)
    relaxation_ladder: list[str] | None = None


class FindComparablesAlongAxisIn(FindComparablesIn):
    transport_types: list[Literal["tram", "subway", "bus"]] | None = None
    anchor_radius_m: int = Field(default=800, ge=50, le=5000)
    corridor_m: int = Field(default=300, ge=50, le=2000)
    cache_ttl_days: int = Field(default=30, ge=1, le=365)


class VerifyFreshnessIn(BaseModel):
    sreality_id: int
    max_age_hours: int = 24


class CompareSnapshotsIn(BaseModel):
    sreality_id: int | None = None
    since_days: int | None = None
    listing_id: int | None = None


class DescribeNeighborhoodIn(BaseModel):
    lat: float
    lng: float
    radius_m: int = 1000
    # No implicit freshness gate; pass explicitly when needed.
    max_age_days: int | None = None
    # Required, no silent apartment-rental default. Pass `null` to span
    # every category.
    category_main: str | None = Field(...)
    category_type: str | None = Field(...)


class FindDistributionOutliersIn(BaseModel):
    listings: list[dict[str, Any]]
    field: Literal["price_per_m2", "price_czk"] = "price_per_m2"
    iqr_multiplier: float = 1.5
    investigate_history: bool = True


class ComputeMarketVelocityIn(BaseModel):
    target: TargetIn
    radius_m: int = 1000
    area_band_pct: float = 0.20
    disposition_match: Literal["exact", "loose", "any"] = "exact"
    floor_band: int | None = None
    portals: list[str] | None = None
    condition_match: list[str] | None = None
    building_type_match: list[str] | None = None
    energy_rating_match: list[str] | None = None
    has_balcony: bool | None = None
    has_lift: bool | None = None
    has_parking: bool | None = None
    min_price_czk: int | None = None
    max_price_czk: int | None = None
    # Required, no silent apartment-rental default. Pass `null` to span
    # every category.
    category_main: str | None = Field(...)
    category_type: str | None = Field(...)
    category_sub_cb: int | None = None
    include_unreliable: bool = False
    furnished: EnumListFilter = None
    terrace: bool | None = None
    cellar: bool | None = None
    garage: bool | None = None
    ownership: EnumListFilter = None
    min_estate_area: float | None = None
    max_estate_area: float | None = None
    min_usable_area: float | None = None
    max_usable_area: float | None = None
    min_parking_lots: int | None = None
    building_condition_level_min: int | None = None
    building_condition_level_max: int | None = None
    apartment_condition_level_min: int | None = None
    apartment_condition_level_max: int | None = None
    tom_days_min: int | None = None
    tom_days_max: int | None = None
    tom_observed_only: bool | None = None
    last_seen_min_days: int | None = None
    last_seen_max_days: int | None = None
    first_seen_min_days: int | None = None
    first_seen_max_days: int | None = None
    lifecycle: Literal["active", "delisted", "all"] = "all"
    trend_split_days: int = 7


class ComputeListingVelocityIn(BaseModel):
    sreality_id: int
    radius_m: int = 1000
    disposition_match: Literal["exact", "loose", "any"] = "exact"
    lifecycle: Literal["active", "delisted", "all"] = "all"


class FindAnchorAmenitiesIn(BaseModel):
    lat: float
    lng: float
    radius_m: int = 1000
    categories: list[str] | None = None
    cache_ttl_days: int = 30


class ComputeWalkabilityIn(BaseModel):
    lat: float
    lng: float
    radius_m: int = 1000
    categories: list[str] | None = None
    weights: dict[str, float] | None = None
    cache_ttl_days: int = 30


class ComputeAmenitySupplyIn(BaseModel):
    lat: float
    lng: float
    radius_m: int = 1000
    categories: list[str] | None = None
    target_counts: dict[str, int] | None = None
    cache_ttl_days: int = 30


class SummarizeListingIn(BaseModel):
    sreality_id: int
    snapshot_id: int | None = None
    force_refresh: bool = False


class SummarizeListingsBatchItem(BaseModel):
    sreality_id: int
    snapshot_id: int | None = None


class SummarizeListingsBatchIn(BaseModel):
    """Batch wrapper around summarize_listing for the Estimate page.

    Returns one row per requested item — `{summary, snapshot_id}` on
    success or `{error}` on failure. Cache hits skip the LLM, so
    repeat calls for the same pairs are effectively free.
    """
    items: list[SummarizeListingsBatchItem] = Field(default_factory=list, max_length=50)


class CompareListingImagesIn(BaseModel):
    sreality_id_a: int
    sreality_id_b: int
    n_images: int = Field(default=6, ge=1, le=20)
    force_refresh: bool = False


class CreateEstimationIn(BaseModel):
    """POST /estimations request body.

    Exactly one of `listing_id`, `sreality_id` or `spec` must be set. An
    estimation is of an ad we hold: `listing_id` names it, and the legacy
    `sreality_id` (the Browse cards' on-card estimate) resolves to the same
    ad; its target is built from the stored row, `spec_overrides` (a partial
    dict) merged on top. A `spec` is used verbatim. A pasted link becomes an
    ad through POST /listings/ingest first.

    `estimate_kind` selects rent vs sale. When omitted, defaults to
    'rent' for backwards compatibility with callers built before
    migration 029. The default `category_type` follows the kind
    (`pronajem` for rent, `prodej` for sale) unless the caller
    explicitly overrides it.

    The comparable-search knobs (radius_m, area_band_pct,
    disposition_match, max_age_days) were removed from this schema:
    agent-mode runs choose them per-iteration, and deterministic runs
    use the built-in defaults in `api.estimation_runs._build_filters`.
    `lifecycle` is the one cohort knob kept here (optional override of
    `default_lifecycle`). UI callers default to
    `mode='agent'`; direct API callers may still pass
    `mode='deterministic'` for a single-shot estimate with the
    built-in filter defaults.
    """
    source: Literal["ui", "api", "clickup", "extension"] = "api"
    mode: Literal["deterministic", "agent"] = "deterministic"

    # Agent-mode only; ignored when mode == 'deterministic'.
    provider: Literal["anthropic", "gemini"] = "anthropic"
    skill: str = "rental_estimator_full_v1"

    estimate_kind: Literal["rent", "sale"] = "rent"

    # Cohort lifecycle. None (default) inherits `default_lifecycle`
    # ("active and recently-seen"); "delisted" restricts to
    # is_active=false (the advertisement ended, not a sale); "all"
    # applies no filter.
    lifecycle: Literal["active", "delisted", "all"] | None = None

    listing_id: int | None = None
    sreality_id: int | None = None
    spec: TargetIn | None = None
    spec_overrides: dict[str, Any] | None = None

    purchase_price_czk: int | None = None
    expected_monthly_rent_czk: int | None = None

    floor_band: int | None = None
    portals: list[str] | None = None
    condition_match: list[str] | None = None
    building_type_match: list[str] | None = None
    energy_rating_match: list[str] | None = None
    has_balcony: bool | None = None
    has_lift: bool | None = None
    has_parking: bool | None = None
    min_price_czk: int | None = None
    max_price_czk: int | None = None
    category_main: str | None = "byt"
    category_type: str | None = None
    category_sub_cb: int | None = None
    include_unreliable: bool = False
    furnished: EnumListFilter = None
    terrace: bool | None = None
    cellar: bool | None = None
    garage: bool | None = None
    ownership: EnumListFilter = None
    min_estate_area: float | None = None
    max_estate_area: float | None = None
    min_usable_area: float | None = None
    max_usable_area: float | None = None
    min_parking_lots: int | None = None
    building_condition_level_min: int | None = None
    building_condition_level_max: int | None = None
    apartment_condition_level_min: int | None = None
    apartment_condition_level_max: int | None = None
    tom_days_min: int | None = None
    tom_days_max: int | None = None
    tom_observed_only: bool | None = None
    last_seen_min_days: int | None = None
    last_seen_max_days: int | None = None
    first_seen_min_days: int | None = None
    first_seen_max_days: int | None = None

    parent_run_id: int | None = None
    rerun_reason: str | None = None

    special_instructions: str | None = Field(default=None, max_length=10_000)
    contextual_text: str | None = Field(default=None, max_length=20_000)

    # Pydantic drops unknown keys, so without this a pre-2026-10-20 extension's
    # {url, listing_id} would run with its url silently ignored.
    @model_validator(mode="before")
    @classmethod
    def _refuse_url(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("url") is not None:
            raise ValueError(
                "POST /estimations no longer takes a url: add the ad through "
                "POST /listings/ingest (update the extension), then estimate its listing_id"
            )
        return data

    @model_validator(mode="after")
    def _apply_kind_defaults(self) -> "CreateEstimationIn":
        targets = (self.listing_id, self.sreality_id, self.spec)
        if sum(t is not None for t in targets) != 1:
            raise ValueError(
                "Provide exactly one of 'listing_id', 'sreality_id' or 'spec'"
            )
        if self.category_type is None:
            self.category_type = (
                "pronajem" if self.estimate_kind == "rent" else "prodej"
            )
        return self


# The default service charge (fond oprav + SVJ) a yield panel offers, in CZK
# per m² of a DWELLING's floor area per MONTH. ONE definition, server-side: the
# Chrome extension reads it per subject off POST /listings/lookup
# (`fond_per_m2_czk_default`) instead of carrying its own copy of the number.
# It is a default for an operator INPUT, not a measure — but it is multiplied by
# `area_m2`, so it inherits that denominator's basis and must not be offered
# where the area is a parcel (see api/portal_lookup._fond_default_czk_per_m2).
DEFAULT_FOND_CZK_PER_M2 = 10.0


class ScenarioUpdateIn(BaseModel):
    """Operator-tunable yield scenario for an estimation_runs row.

    All numeric fields are optional. Send only the fields the operator
    actually overrode; missing keys remain at the default (estimated
    rent, `DEFAULT_FOND_CZK_PER_M2` per m², subject sale price, no
    renovation). Send a body with every field null to clear overrides
    and re-render defaults.

    `renovation_czk` is a flat one-off renovation budget added to the
    listing price to form the total acquisition cost (the yield
    denominator).
    """
    rent_czk: float | None = None
    fond_per_m2_czk: float | None = None
    price_czk: float | None = None
    renovation_czk: float | None = None


# Typed so a malformed entry is a 422 at the boundary, never a 500 in the binder.
class RegionalEntry(BaseModel):
    """One entry of a Mapy.cz suggestion's `regionalStructure` (other keys ignored)."""
    name: str | None = None
    type: str | None = None


class ResolveLocationIn(BaseModel):
    """A picked Mapy.cz suggestion, as `api.maps.resolve` binds it to one RÚIAN unit."""
    lat: float
    lng: float
    type: str
    name: str
    regional_structure: list[RegionalEntry] = Field(default_factory=list)


class EstimateYieldIn(BaseModel):
    target: TargetIn
    estimate_kind: Literal["rent", "sale"] = "rent"
    purchase_price_czk: int | None = None
    expected_monthly_rent_czk: int | None = None
    radius_m: int = 1000
    area_band_pct: float = 0.20
    disposition_match: Literal["exact", "loose", "any"] = "exact"
    max_age_days: int | None = None
    lifecycle: Literal["active", "delisted", "all"] | None = None
    floor_band: int | None = None
    portals: list[str] | None = None
    condition_match: list[str] | None = None
    building_type_match: list[str] | None = None
    energy_rating_match: list[str] | None = None
    has_balcony: bool | None = None
    has_lift: bool | None = None
    has_parking: bool | None = None
    min_price_czk: int | None = None
    max_price_czk: int | None = None
    # category_main is required (no silent apartment default). category_type
    # follows estimate_kind when omitted — same smart pattern as
    # CreateEstimationIn (rent -> pronajem, sale -> prodej). Pass it
    # explicitly to override.
    category_main: str | None = Field(...)
    category_type: str | None = None
    category_sub_cb: int | None = None
    include_unreliable: bool = False
    furnished: EnumListFilter = None
    terrace: bool | None = None
    cellar: bool | None = None
    garage: bool | None = None
    ownership: EnumListFilter = None
    min_estate_area: float | None = None
    max_estate_area: float | None = None
    min_usable_area: float | None = None
    max_usable_area: float | None = None
    min_parking_lots: int | None = None
    building_condition_level_min: int | None = None
    building_condition_level_max: int | None = None
    apartment_condition_level_min: int | None = None
    apartment_condition_level_max: int | None = None
    tom_days_min: int | None = None
    tom_days_max: int | None = None
    tom_observed_only: bool | None = None
    last_seen_min_days: int | None = None
    last_seen_max_days: int | None = None
    first_seen_min_days: int | None = None
    first_seen_max_days: int | None = None

    @model_validator(mode="after")
    def _apply_kind_defaults(self) -> "EstimateYieldIn":
        if self.category_type is None:
            self.category_type = (
                "pronajem" if self.estimate_kind == "rent" else "prodej"
            )
        # No implicit freshness gate. Callers that want only fresh active
        # listings now pass max_age_days + lifecycle='active' explicitly.
        return self


# --- curation -------------------------------------------------------------
# Collections (named lists of listings), free-text journal notes, and
# free-form coloured tags. The colour palette is mirrored in the
# 024_listing_tags.sql CHECK constraint and in the frontend's
# tag-palette tokens.

TagColor = Literal[
    "copper", "sage", "brick", "ochre",
    "slate",  "plum", "teal",  "sand",
]


class CreateCollectionIn(BaseModel):
    name:               str = Field(min_length=1, max_length=200)
    description:        str | None = None
    monitoring_enabled: bool = False
    notify_channels:    list[str] = Field(default_factory=list)


class UpdateCollectionIn(BaseModel):
    name:               str | None = Field(default=None, min_length=1, max_length=200)
    description:        str | None = None
    monitoring_enabled: bool | None = None
    notify_channels:    list[str] | None = None


class AddPropertiesToCollectionIn(BaseModel):
    property_ids: list[int] = Field(min_length=1, max_length=500)


class CreateNoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    # The advert's surrogate id the operator was viewing when they wrote the note —
    # display provenance ("written while viewing this advert") and a split's routing
    # key, never a grouping key. The legacy origin_listing_id column is derived from it.
    origin_listing_ref_id: int | None = None
    # The author's consent mark (migration 610): the parties of a share holding this property may read it.
    shared: bool = False


ShareMode = Literal["read", "edit"]
ShareSide = Literal["shared_with", "shared_with_me"]


class ShareIn(BaseModel):
    """The one email rule for every share kind (migrations 610, 641, 647)."""

    email: str

    @field_validator("email", mode="before")
    @classmethod
    def _one_address(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip().lower()
            if not 3 <= len(v) <= 320 or v.count("@") != 1:
                raise ValueError("must be one email address")
        return v


ShareCollectionIn = ShareIn


class SharePipelineIn(ShareIn):
    """Share my board (migration 641); re-sharing changes the mode."""

    mode: ShareMode


class SetArtifactLinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url", mode="before")
    @classmethod
    def _http_url(cls, v: Any) -> Any:
        # The DB CHECK (migration 599) says the same; this turns it into a 422 the SPA can show.
        if isinstance(v, str):
            v = v.strip()
            if not re.fullmatch(r"https?://\S+", v, re.IGNORECASE):
                raise ValueError("must be an http(s) URL with no spaces")
        return v


class CreateTagIn(BaseModel):
    name:  str = Field(min_length=1, max_length=50)
    color: TagColor


class UpdateNoteIn(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    shared: bool | None = None


class UpdateTagIn(BaseModel):
    name:  str | None = Field(default=None, min_length=1, max_length=50)
    color: TagColor | None = None


class AttachTagIn(BaseModel):
    tag_id: int


# --- deal pipeline (migration 205) ----------------------------------------
# A property is "bookmarked / interested" iff it has a property_pipeline row
# (starting at the entry stage). Single-valued; property grain.

class AddPipelineCardIn(BaseModel):
    property_id: int


class DismissPropertyIn(BaseModel):
    property_id: int


class MoveCardIn(BaseModel):
    # Move to another stage and/or reorder within a stage. Both optional; an
    # empty body is a no-op that returns the current card.
    stage_id: int | None = None
    board_position: float | None = None


# Stage management (operator-curated columns; the curated-index precedent). The
# `key` is derived server-side from the label — operators name a column, not a slug.
PIPELINE_STAGE_COLORS = (
    "copper", "sage", "brick", "ochre", "slate", "plum", "teal", "sand",
)


class CreateStageIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    color: str | None = None
    is_terminal: bool = False
    # Short badge shown inside the funnel glyph on every surface (migration 377).
    # Deliberately not unique and deliberately nullable — NULL means "use the
    # stage's ordinal", which is what a freshly created stage gets.
    code: str | None = Field(default=None, min_length=1, max_length=4)


class UpdateStageIn(BaseModel):
    # All optional; an absent field is left unchanged. `is_entry` may only be
    # set True (move the entry crown to this stage) — you re-home the entry by
    # crowning another, never by un-crowning the only one.
    label: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = None
    is_terminal: bool | None = None
    is_entry: bool | None = None
    # Sent explicitly as null to clear the badge back to the ordinal fallback,
    # so this one is read via model_fields_set, not `is not None`.
    code: str | None = Field(default=None, min_length=1, max_length=4)


class ReorderStagesIn(BaseModel):
    # The complete set of non-archived stage ids in their new left-to-right order.
    ordered_ids: list[int]


# --- saved sets: filter presets + Browse > Stats dashboards (api/saved_sets.py) ---
# Both instances' bodies (migrations 151, 626, 647). Each blob carries its one validator in
# its type: PresetSpec (every place chip coded), Charts (toolkit.stats_registry; a refusal is
# a 422 naming the chart and the reason).

_CHIPS = TypeAdapter(list[DistrictChip])


# The blob is stored VERBATIM, so its chips are checked strictly (a "582786" or a
# `true` id would be stored as written), where the SPA reads them (`readPresetSpec`:
# `spec.filters`, else a legacy bare filter set). `strict=` at validation time: a
# `ConfigDict(strict=True)` on the adapter does not reach the nested model.
def _coded_chips(spec: dict[str, Any]) -> dict[str, Any]:
    """Every place chip in the blob is a level plus a RÚIAN code, or the write is a 422."""
    filters = spec["filters"] if spec.get("filters") is not None else spec
    if isinstance(filters, dict) and filters.get("districts") is not None:
        _CHIPS.validate_python(filters["districts"], strict=True)
    return spec


def _valid_board(charts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The board as the stats registry normalises it: the only specs the pivot accepts."""
    try:
        return sr.validate_board(charts)
    except sr.SpecError as exc:
        # Not a ValueError: pydantic re-raises an HTTPException unwrapped, so the 422 names the chart.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


PresetSpec = Annotated[dict[str, Any], AfterValidator(_coded_chips)]
Charts = Annotated[list[dict[str, Any]], AfterValidator(_valid_board)]


class CreatePresetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    filter_spec: PresetSpec
    # Shared tag palette (TagColor); None = neutral default chip.
    color: TagColor | None = None


class UpdatePresetIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    filter_spec: PresetSpec | None = None
    color: TagColor | None = None


class StatsDashboardIn(BaseModel):
    """The account's working board, stored whole (PUT /stats/dashboard)."""
    charts: Charts


class CreateStatsDashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    charts: Charts
    # The preset blob, embedded (ruling 2, never a linked preset); None = the dashboard carries no filter.
    filter_spec: PresetSpec | None = None
    color: TagColor | None = None


class UpdateStatsDashboardIn(BaseModel):
    # An absent field keeps its value; an explicit null clears `filter_spec` or `color`, nothing else.
    name: str | None = Field(default=None, min_length=1, max_length=200)
    charts: Charts | None = None
    filter_spec: PresetSpec | None = None
    color: TagColor | None = None


ShareDashboardIn = ShareIn


class ValuoRunIn(BaseModel):
    """A Valuo valuation (api/valuo.py): parameter -> the user's value, or null to send none."""
    overrides: dict[str, Any] = Field(default_factory=dict)


# --- buildings ------------------------------------------------------------
# Phase B0 of the building-decomposition track. See ROADMAP.md
# "Building decomposition track" and CLAUDE.md architectural rule #13.
# B0 ships schemas + read endpoints only; B1 adds URL ingest + the
# extractor; B2 fans out per-unit estimations; B3 layers the
# business case.

BuildingStatus = Literal[
    "pending", "extracting", "awaiting_input",
    "estimating", "success", "failed",
]

BuildingUnitSource = Literal[
    "description", "floor_plan", "both", "user_added",
]


class BuildingUnit(BaseModel):
    """One apartment unit within a building.

    Lives inside `building_runs.units_proposal` (agent output) and
    `building_runs.units` (operator-confirmed) as a JSONB array entry.
    `unit_id` is a stable string ('u1', 'u2', ...) assigned by the
    extractor (B1) and preserved across edits so child estimations
    stay linked to the same conceptual unit.
    """
    unit_id: str = Field(min_length=1, max_length=50)
    label: str | None = None
    floor: str | None = None
    area_m2: float | None = None
    disposition: str | None = None
    condition: str | None = None
    is_potential: bool = False
    source: BuildingUnitSource | None = None
    notes: str | None = None


class CreateBuildingIn(BaseModel):
    """Minimal building-row creation: inserts a `status='pending'` shell."""
    source: Literal["ui", "api", "clickup"] = "api"
    input_url: str | None = None


class UpdateBuildingInputsIn(BaseModel):
    """Patch operator-supplied text on a building_runs row.

    Allowed only while the row is editable (status in
    pending / extracting / awaiting_input). Once estimation starts the
    inputs are frozen for audit. Use only to correct text inputs;
    attachments have their own endpoints.
    """
    special_instructions: str | None = Field(default=None, max_length=10_000)
    contextual_text: str | None = Field(default=None, max_length=20_000)


class BuildingAttachmentOut(BaseModel):
    """One operator-supplied attachment row on a building_run."""
    id: int
    building_run_id: int
    filename: str
    mime_type: str
    byte_size: int
    width_px: int | None = None
    height_px: int | None = None
    storage_key: str
    sha256_hex: str
    uploaded_by: str | None = None
    created_at: str


class ConfirmBuildingUnitsIn(BaseModel):
    """Operator-confirmed unit list. Transitions awaiting_input -> estimating."""
    units: list[BuildingUnit] = Field(min_length=1, max_length=30)


# --- Feedback (Phase AI slice B) ------------------------------------------

class CreateFeedbackIn(BaseModel):
    """Operator note on one estimation run.

    `kick_off_refinement` flips on the slice C refiner the moment the
    feedback lands, so the operator gets a same-session prompt
    proposal. Set false to stash feedback without spending LLM credit.
    """
    feedback_text:        str = Field(min_length=1, max_length=4000)
    kick_off_refinement:  bool = True


# --- Skill refinements (Phase AI slice C) ---------------------------------

FeedbackStatus = Literal[
    "submitted", "refining", "proposed", "applied", "dismissed", "failed",
]

RefinementStatus = Literal["proposed", "applied", "dismissed"]


class RefinementDecisionIn(BaseModel):
    """Operator decides what to do with a proposed skill refinement."""
    decision: Literal["apply", "dismiss"]


# --- Price-stats datasets (ceny-nemovitosti) ------------------------------

class PriceStatDatasetIn(BaseModel):
    """Create a price-stats dataset (one named filter set)."""
    slug:               str = Field(min_length=1, max_length=120)
    name:               str = Field(min_length=1, max_length=200)
    description:        str | None = None
    category_main_cb:   int = 1
    building_condition: str | None = None
    building_type:      str | None = None
    ownership:          str | None = None
    usable_area_from:   int | None = Field(default=None, ge=0)
    usable_area_to:     int | None = Field(default=None, ge=0)
    distance:           int = Field(default=0, ge=0)
    # scrape window (YYYY-MM) + city selection
    start_ym:           str | None = None
    end_ym:             str | None = None
    obec_ids:           list[int] | None = None
    min_population:     int | None = Field(default=None, ge=0)
    max_population:     int | None = Field(default=None, ge=0)


class PriceStatDatasetUpdateIn(BaseModel):
    """Patch a dataset; every field optional."""
    name:               str | None = Field(default=None, min_length=1, max_length=200)
    description:        str | None = None
    category_main_cb:   int | None = None
    building_condition: str | None = None
    building_type:      str | None = None
    ownership:          str | None = None
    usable_area_from:   int | None = Field(default=None, ge=0)
    usable_area_to:     int | None = Field(default=None, ge=0)
    distance:           int | None = Field(default=None, ge=0)
    is_active:          bool | None = None
    start_ym:           str | None = None
    end_ym:             str | None = None
    obec_ids:           list[int] | None = None
    min_population:     int | None = Field(default=None, ge=0)
    max_population:     int | None = Field(default=None, ge=0)


class PortalLookupItem(BaseModel):
    """One portal listing keyed by its native id. For sreality, source_id is the
    numeric id as a string (matches listings.source_id_native)."""
    source: str = Field(min_length=1, max_length=40)
    source_id: str = Field(min_length=1, max_length=512)


class PortalLookupIn(BaseModel):
    """Batch lookup for the Chrome extension's detail panel + index-card overlay."""
    items: list[PortalLookupItem] = Field(min_length=1, max_length=50)


class IngestIn(BaseModel):
    """POST /listings/ingest: pasted text or a URL; the first link in it is the ad."""
    url: str = Field(max_length=4096)


class IngestQueue(BaseModel):
    attempts: int
    claimed_at: datetime | None
    given_up: bool


class IngestState(BaseModel):
    """The ONE answer of POST /listings/ingest and GET /listings/ingest/{source}/{native_id}.
    Market data only: no per-account field may ever join it."""
    state: Literal["refused", "held", "queued", "gone", "failed"]
    message_cs: str | None
    source: str | None
    source_id_native: str | None
    listing_id: int | None
    is_active: bool | None
    property_id: int | None
    queue: IngestQueue | None

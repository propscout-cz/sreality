"""POST /listings/lookup — batch (source, native id) → MF + property header facts + latest estimate.

The Chrome extension overlays the Browse-card 'Výnos MF' yield
(`mf_gross_yield_pct`) and the MF reference rent (`mf_reference_rent`) on
portal detail + index pages, across every scraped portal, and fills its listing
sheet's header from the same row. A card knows only its own `(source, native
id)` from its href, so the lookup resolves uniformly on `(source,
source_id_native)` (the migration-091 unique key; for sreality
`source_id_native` is the numeric id as text) and joins any latest successful
estimation. Read-only; bearer-gated like every other non-/health route.

It also returns the listing's `property_id` + its **deal-pipeline membership**
(rule #22) so the panel's "Přidat do pipeline" toggle knows its state in the
one call it already makes — the toggle then writes through the existing
bearer-gated `POST/DELETE /pipeline/cards` (the same path the SPA uses). The
pipeline is property-grain, so membership is read off `l.property_id`. It
likewise returns the property's **collection memberships** (`collection_ids`,
rule #18) so the panel's save-to-collection control knows which collections the
property is already in, writing through the existing bearer-gated
`POST/DELETE /collections/{id}/properties`. And whether the caller
has **dismissed** the property (`dismissed`, migration 536), for the panel's
hide toggle, which writes through `POST/DELETE /dismissals`. And the
property's **tags** (`tag_ids`, rule #18), which the search-page cards print
over the photo (names and colours from `GET /tags`).

Rows come back keyed by column name (dict_row) — no positional index math, so
projecting one more column is a one-line change that can't silently misalign.

TWO connections since the extension's own JWT went live (Wave 1): the shared
market facts (`listings` + `properties_public`) are read on the SERVICE-ROLE
connection — those tables are RLS-enabled-with-zero-policies by design (they
carry broker PII inline, so a blanket `authenticated` read policy is the exact
leak A6 walled off; see the A5 correction in
docs/design/phase-1-multitenancy-foundations.md), which means a tenant
connection sees zero rows and every lookup would report found=false. The
per-account joins (pipeline membership, collection memberships, latest
estimation) run on the TENANT connection so RLS scopes them to the caller —
including the SYSTEM-account arm of estimation_runs_tenant_read (migration
291), which is what lets the platform's pre-multi-tenancy golden estimates
still surface. Same trusted-server split POST /estimations already uses.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row

from api import curation
from api.schemas import DEFAULT_FOND_CZK_PER_M2
from toolkit.measures import LAND_CATEGORY_MAIN, plot_area_sql

if TYPE_CHECKING:
    import psycopg

    from api import schemas as s

# Listing columns projected onto each entry (dict keys === SELECT aliases).
# `listing_id` is the surrogate (always present for a found listing) and is what
# a write should carry.
_LISTING_COLS: tuple[str, ...] = (
    "listing_id", "property_id",
    "category_main", "category_type", "area_m2", "area_basis",
    "price_czk", "price_per_m2", "price_per_m2_basis",
    "disposition", "display_label", "is_active",
    "mf_reference_rent_czk", "mf_gross_yield_pct", "mf_reference_rent",
    "canonical_listing_id", "property_is_active", "tom_days", "floor",
    "total_floors", "condition", "ownership", "property_subtype", "plot_area_m2",
)

_MARKET_SQL = f"""
WITH req(source, source_id) AS (VALUES {{values}})
SELECT
    req.source,
    req.source_id,
    (l.source_id_native IS NOT NULL) AS found,
    l.id AS listing_id, l.property_id,
    l.category_main, l.category_type, l.area_m2, l.price_czk, l.disposition,
    -- ONE place string (migration 503), the same one Browse, the map and the
    -- notifications print. The panel used to fall back `district ?? locality`
    -- while the SPA fell back the other way, so one listing could be labelled
    -- two different ways on two surfaces. It is the PROPERTY's, its location ad's
    -- (ruling R9), so it names the place the MF below is priced at; the advert's
    -- own label only for an advert not yet attached, which has no MF.
    COALESCE(pp.display_label,
             location_display_label(ll.street_name, ll.house_number_cp, ll.house_number_co,
                                    ll.obec_name, ll.cast_obce_name, ll.country_code,
                                    ll.country_status)) AS display_label,
    l.is_active,
    -- THE measure and its label (migration 425), listing-grain: the panel is
    -- overlaid on ONE portal advert, so the number must be that advert's.
    -- `area_basis` (migration 423) rides along because the denominator is
    -- polymorphic — 905 m² of plot and 905 m² of floor are not the same
    -- denominator, and only this stamp says which one was used.
    measure_price_per_m2(l.price_czk::numeric, l.area_m2::numeric,
                         l.category_main, l.category_type) AS price_per_m2,
    measure_price_per_m2_basis(l.category_main, l.category_type) AS price_per_m2_basis,
    l.area_basis,
    -- MF is PROPERTY-grain: every portal's advert of one flat shows the SAME
    -- number, read off the same `properties_public` row the listing page, the
    -- kanban and the Watchdog matcher read. `mf_reference_rent` is the whole
    -- result (a value's breakdown, a range with its note, or a note alone); the
    -- panel renders it by shape and derives nothing. A pre-attach advert
    -- (property_id NULL) has no property, so no MF.
    pp.mf_reference_rent_czk,
    pp.mf_gross_yield_pct,
    pp.mf_reference_rent,
    -- What the property IS, each field at its MS11 grain (docs/design/merge-sprint/PROGRAM.md):
    -- the panel header's facts, off the same row as MF and, like it, NULL before attach.
    -- `pp.listing_id` is the canonical ad (MS5), the first track of the price history;
    -- `is_active` is aliased because `l.is_active` above is this advert's own.
    pp.listing_id AS canonical_listing_id, pp.is_active AS property_is_active,
    pp.tom_days, pp.floor, pp.total_floors, pp.condition, pp.ownership,
    pp.subtype AS property_subtype,
    {plot_area_sql('pp')} AS plot_area_m2
FROM req
LEFT JOIN listings l
    ON l.source = req.source AND l.source_id_native = req.source_id
LEFT JOIN properties_public pp ON pp.property_id = l.property_id
LEFT JOIN listing_location ll ON ll.listing_id = l.id
"""

# Each ad's latest successful estimate as RLS shows the caller (estimation_runs_tenant_read: their own runs and the
# SYSTEM account's); the property's curation is curation.property_state's. No account is bound in either.
_ESTIMATION_SQL = """
SELECT t.listing_id,
    e.id AS estimation_id, e.estimate_kind AS estimation_kind,
    e.gross_yield_pct AS estimation_yield
FROM unnest(%s::bigint[]) AS t(listing_id)
JOIN LATERAL (
    SELECT er.id, er.estimate_kind, er.gross_yield_pct
    FROM estimation_runs er
    WHERE er.status = 'success'
      -- Match on the SURROGATE for every source (R2): every run of an ad is
      -- stamped with its listings.id at submit.
      AND er.input_listing_id = t.listing_id
    ORDER BY er.created_at DESC
    LIMIT 1
) e ON true
"""


# The `listings.area_basis` stamp (migration 423) that says `area_m2` IS the
# parcel — the one denominator a monthly per-m² service charge cannot be levied
# on.
_PLOT_AREA_BASIS = "plot"


def _fond_default_czk_per_m2(row: dict[str, Any]) -> float | None:
    """The default service charge to offer for THIS subject; None where none can apply.

    A fond (fond oprav + SVJ) is a monthly CZK per m² of a dwelling's FLOOR
    area. `area_m2` is polymorphic (migration 423): on `pozemek` it IS the
    parcel, so offering a rate there lets a panel multiply 10 CZK/m² by 905 m²
    of land and subtract ~9 050 CZK/month from the rent in a yield numerator —
    enough to drive a computed yield negative.

    Deliberately NOT gated on `measure_price_per_m2_basis`: that resolves
    RENT-FIRST, so a plot to let (pozemek + pronajem) labels
    `rent_monthly_czk_m2` and would slip through a land-basis test. The two
    stamps that actually name the DENOMINATOR are `area_basis` and
    `category_main`, and either one saying "parcel" is enough.

    A listing we don't have (`found=false`) gets the plain default: nothing is
    known that would say a fond doesn't apply, and withholding it would only
    blank a field the operator then retypes.
    """
    if not row["found"]:
        return DEFAULT_FOND_CZK_PER_M2
    if row["area_basis"] == _PLOT_AREA_BASIS:
        return None
    if row["category_main"] == LAND_CATEGORY_MAIN:
        return None
    return DEFAULT_FOND_CZK_PER_M2


def _clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def lookup_portal_listings(
    market_conn: "psycopg.Connection",
    tenant_conn: "psycopg.Connection",
    items: "list[s.PortalLookupItem]",
) -> dict[str, Any]:
    """Resolve each (source, source_id) to its MF + property header facts + latest estimate.

    One row per requested item, in request order; `found=false` (and null
    fields) when we have no listing for that pair. Market facts read on
    `market_conn` (service-role — see module docstring); estimation and the
    property's state on `tenant_conn`, which takes NO account argument: RLS
    is the only membership definition (`_ESTIMATION_SQL`,
    `curation.property_state`).
    """
    values_sql = ", ".join(["(%s::text, %s::text)"] * len(items))
    params: list[str] = []
    for it in items:
        params.extend([it.source, it.source_id])

    with market_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MARKET_SQL.format(values=values_sql), params)
        rows = cur.fetchall()

    found_rows = [r for r in rows if r["found"] and r["listing_id"] is not None]
    estimate_by_listing: dict[int, dict[str, Any]] = {}
    state_by_property: dict[int, dict[str, Any]] = {}
    if found_rows:
        with tenant_conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_ESTIMATION_SQL, ([r["listing_id"] for r in found_rows],))
            estimate_by_listing = {e["listing_id"]: e for e in cur.fetchall()}
        held = dict.fromkeys(r["property_id"] for r in found_rows if r["property_id"] is not None)
        state_by_property = {st["property_id"]: st for st in curation.property_state(tenant_conn, list(held))}

    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        entry: dict[str, Any] = {
            "source": row["source"],
            "source_id": row["source_id"],
            "found": bool(row["found"]),
        }
        entry.update({col: _clean(row[col]) for col in _LISTING_COLS})
        # Derived, not projected: the panel's default service charge, resolved
        # from this subject's denominator (see `_fond_default_czk_per_m2`).
        # Served so the extension holds no copy of the number.
        entry["fond_per_m2_czk_default"] = _fond_default_czk_per_m2(row)
        est = estimate_by_listing.get(row["listing_id"], {})
        est_id = est.get("estimation_id")
        entry["latest_estimation"] = (
            {
                "estimation_id": est_id,
                "estimate_kind": est.get("estimation_kind"),
                "gross_yield_pct": _clean(est.get("estimation_yield")),
            }
            if est_id is not None
            else None
        )
        # Deal-pipeline membership is property-grain (rule #22): only present
        # once the listing is attached to a property (a freshly-scraped row is
        # property_id NULL for ~5 min — the panel hides the toggle until then).
        state = state_by_property.get(row["property_id"], {})
        entry["pipeline"] = (
            {
                "in_pipeline": state.get("stage_id") is not None,
                "stage_id": state.get("stage_id"),
                "stage_key": state.get("stage_key"),
                "stage_label": state.get("stage_label"),
                # Badge + colour (migration 377) so the panel's funnel renders
                # the same mark as the SPA's, from one server-side source.
                "stage_code": state.get("stage_code"),
                "stage_color": state.get("stage_color"),
                # Since when the card sits at this stage (the PATCH stamps it): the
                # panel's "ve fázi od".
                "entered_stage_at": _clean(state.get("entered_stage_at")),
            }
            if row["property_id"] is not None
            else None
        )
        # Collection memberships are property-grain (rule #18) — same NULL-until-
        # attached posture as pipeline above; the panel's save-to-collection control reads it.
        entry["collection_ids"] = (
            list(state.get("collection_ids") or []) if row["property_id"] is not None else None
        )
        entry["tag_ids"] = (
            list(state.get("tag_ids") or []) if row["property_id"] is not None else None
        )
        # Dismissal (migration 536) is property-grain too: the panel's hide toggle.
        entry["dismissed"] = (
            bool(state.get("dismissed")) if row["property_id"] is not None else None
        )
        by_key[(row["source"], row["source_id"])] = entry

    fallback = lambda it: {  # noqa: E731 — tiny shape for the (rare) missing row
        "source": it.source, "source_id": it.source_id, "found": False,
        "latest_estimation": None, "pipeline": None, "collection_ids": None,
        "tag_ids": None, "dismissed": None,
        "fond_per_m2_czk_default": DEFAULT_FOND_CZK_PER_M2,
    }
    return {"data": [by_key.get((it.source, it.source_id), fallback(it)) for it in items]}

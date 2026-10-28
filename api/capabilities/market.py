"""Tools over market data, on the service connection (ADR-0002's one exception): the toolkit's analytics.

Each handler places its subject, hands the toolkit function its filters by registry id and returns the toolkit's
own envelope unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.capabilities.subject import HeldSubject, Subject, held_ad, held_facts, place
from api.estimate_yield import estimate_yield
from api.filter_args import agenda_ids, filter_model, filter_values
from toolkit import (
    ComparableFilters,
    _now_iso,
    analyze_distribution,
    compute_listing_velocity,
    compute_market_velocity,
    describe_neighborhood,
    find_comparables,
    find_comparables_relaxed,
    find_distribution_outliers,
)
from toolkit.filter_registry import Agenda
from toolkit.measures import RENT_MONTHLY_CZK_M2
from toolkit.rent_map import compute_reference_rent
from toolkit.subject import subject_mf_facts

_COMPARABLES = agenda_ids(Agenda.COMPARABLES)
CohortFilters = filter_model("CohortFilters", _COMPARABLES, ComparableFilters)
# The deal type follows estimate_kind, so a rent estimate never scales a sale cohort.
YieldFilters = filter_model("YieldFilters", tuple(i for i in _COMPARABLES if i != "category_type"), ComparableFilters)
# Velocity gates lifecycle itself and never applies a recency window (time on market needs each ad's whole stay),
# so the agenda's max_age_days would be dropped; lifecycle is velocity's own argument, defaulted as it defaults it.
VelocityFilters = filter_model(
    "VelocityFilters",
    tuple(i for i in agenda_ids(Agenda.VELOCITY) if i not in ("max_age_days", "lifecycle")), ComparableFilters,
)
VelocityLifecycle = filter_model("VelocityLifecycle", ("lifecycle",), compute_market_velocity)
# describe_neighborhood reads these four; the NEIGHBORHOOD agenda also lists attribute filters it never applies.
NeighbourhoodFilters = filter_model(
    "NeighbourhoodFilters", ("radius_m", "max_age_days", "category_main", "category_type"), describe_neighborhood,
)
ListingVelocityFilters = filter_model(
    "ListingVelocityFilters", ("radius_m", "disposition_match", "lifecycle"), compute_listing_velocity,
)

_PRICE_FIELD = "price_per_m2 is THE per-m² measure (its unit is the cohort's price_per_m2_basis)"


class ComparablesArgs(Subject, CohortFilters):
    min_results: int = Field(
        5, ge=1, le=50, description="Widen the cohort step by step until it holds at least this many; every "
        "widening is listed in data.relaxation_trace.")


class DistributionArgs(Subject, CohortFilters):
    field: Literal["price_per_m2", "price_czk", "area_m2"] = Field(
        "price_per_m2", description=f"The value to describe; {_PRICE_FIELD}.")


class OutliersArgs(Subject, CohortFilters):
    field: Literal["price_per_m2", "price_czk"] = Field(
        "price_per_m2", description=f"The value to test; {_PRICE_FIELD}.")
    iqr_multiplier: float = Field(
        1.5, ge=0.5, le=5.0, description="Tukey fence width: an outlier lies beyond Q1/Q3 by this many IQRs.")
    investigate_history: bool = Field(True, description="Read each outlier's price history as well.")


class MarketVelocityArgs(Subject, VelocityFilters, VelocityLifecycle):
    trend_split_days: int = Field(
        7, ge=1, le=90, description="The trend compares ads first seen in the last N days with those before.")


class ListingVelocityArgs(ListingVelocityFilters):
    listing_id: int = Field(ge=1, description="The ad whose time on market to measure (its listing id).")


class YieldArgs(Subject, YieldFilters):
    estimate_kind: Literal["rent", "sale"] = Field(
        "rent", description="Estimate a monthly rent (over rentals) or a sale price (over sales).")
    purchase_price_czk: int | None = Field(
        None, ge=0, description="The price paid or asked, in Kč; with a rent estimate it yields a gross yield.")
    expected_monthly_rent_czk: int | None = Field(
        None, ge=0, description="The monthly rent expected, in Kč; with a sale estimate it yields a gross yield.")


class ReferenceRentArgs(HeldSubject):
    pass  # a held ad only: the reference reads its stored location codes and its property's facts


class NeighbourhoodArgs(Subject, NeighbourhoodFilters):
    pass  # the subject and the four filters describe_neighborhood reads


def _comparables(ctx: Context, a: ComparablesArgs) -> dict[str, Any]:
    target, values = place(ctx.conn, a)
    return find_comparables_relaxed(ctx.conn, target, ComparableFilters(**values), min_results=a.min_results)


def _cohort(ctx: Context, a: Subject) -> list[dict[str, Any]]:
    target, values = place(ctx.conn, a)
    return find_comparables(ctx.conn, target, ComparableFilters(**values))["data"]["listings"]


def _distribution(ctx: Context, a: DistributionArgs) -> dict[str, Any]:
    return analyze_distribution(_cohort(ctx, a), field=a.field)


def _outliers(ctx: Context, a: OutliersArgs) -> dict[str, Any]:
    return find_distribution_outliers(ctx.conn, _cohort(ctx, a), field=a.field, iqr_multiplier=a.iqr_multiplier,
                                      investigate_history=a.investigate_history)


def _market_velocity(ctx: Context, a: MarketVelocityArgs) -> dict[str, Any]:
    target, values = place(ctx.conn, a)
    lifecycle = values.pop("lifecycle")
    return compute_market_velocity(ctx.conn, target, ComparableFilters(**values), lifecycle=lifecycle,
                                   trend_split_days=a.trend_split_days)


def _listing_velocity(ctx: Context, a: ListingVelocityArgs) -> dict[str, Any]:
    return compute_listing_velocity(ctx.conn, listing_id=a.listing_id, **filter_values(a))


def _yield(ctx: Context, a: YieldArgs) -> dict[str, Any]:
    target, values = place(ctx.conn, a)
    deal = "pronajem" if a.estimate_kind == "rent" else "prodej"
    return estimate_yield(ctx.conn, target, ComparableFilters(**values, category_type=deal), a.purchase_price_czk,
                          estimate_kind=a.estimate_kind, expected_monthly_rent_czk=a.expected_monthly_rent_czk)


def _reference_rent(ctx: Context, a: ReferenceRentArgs) -> dict[str, Any]:
    ad = held_ad(ctx.conn, a)
    spec = held_facts(ctx.conn, ad)["spec"]
    inputs = {"disposition": spec["disposition"], "area_m2": spec["area_m2"], **subject_mf_facts(ctx.conn, ad)}
    reference = compute_reference_rent(ctx.conn, **inputs)
    return {
        "data": reference,
        "metadata": {
            "tool": "compute_reference_rent",
            "filters_used": {"listing_id": ad, **inputs},
            "result_count": 0 if reference is None else 1,
            "queried_at": _now_iso(),
            "data_freshness": None,
            # base_per_m2 / total_per_m2 are monthly rent per m² (rule 23: a per-m² figure names its unit).
            "price_per_m2_basis": RENT_MONTHLY_CZK_M2,
        },
    }


def _neighbourhood(ctx: Context, a: NeighbourhoodArgs) -> dict[str, Any]:
    target, values = place(ctx.conn, a)
    return describe_neighborhood(ctx.conn, lat=target.lat, lng=target.lng, **values)


_SUBJECT = ("The subject is exactly one of property_id, listing_id or point. A property or ad we hold fills any "
            "category filter you leave unset with its own category (null means every category); a point must state "
            "category_main and category_type.")
_COHORT = ("The cohort is the comparables these filters select, at most 500 nearest, each property once; a "
           "price_per_m2_basis of 'mixed' or 'unknown' means it has no single per-m² unit. Omitting lifecycle "
           "means every ad, live or delisted.")

ENTRIES = tuple(
    Entry(name=name, description=description, args=args, handler=handler, tenancy=Tenancy.MARKET, effect=Effect.READ)
    for name, description, args, handler in (
        ("find_comparables_relaxed",
         f"Comparable ads around a subject, widened step by step until at least min_results are found. {_SUBJECT} "
         f"{_COHORT}", ComparablesArgs, _comparables),
        ("analyze_distribution",
         f"n, min, max, mean, median, p10 to p90, standard deviation and IQR of one value over the comparables "
         f"cohort these arguments select. {_SUBJECT} {_COHORT}", DistributionArgs, _distribution),
        ("find_distribution_outliers",
         f"The comparables whose value lies beyond the Tukey fences of their cohort. {_SUBJECT} {_COHORT}",
         OutliersArgs, _outliers),
        ("compute_market_velocity",
         f"Time on market around a subject: statistics over live and delisted ads, how many of each, and the trend "
         f"between ads first seen recently and before. A live ad's time so far is still running, and a delisting "
         f"is an ad ending, not a sale. {_SUBJECT} Lifecycle defaults to every ad.",
         MarketVelocityArgs, _market_velocity),
        ("compute_listing_velocity",
         "One ad's time on market and its percentile rank among the ads around it.", ListingVelocityArgs,
         _listing_velocity),
        ("estimate_yield",
         f"A rent or sale estimate from the comparables' per-m² distribution, and a gross yield when the other "
         f"price is given. The deal type follows estimate_kind. {_SUBJECT} {_COHORT}", YieldArgs, _yield),
        ("compute_reference_rent",
         "The Ministry of Finance rent-map reference (Cenová mapa nájemného) for a flat we hold: the monthly rent "
         "per m² its territory and facts give, or null where the map has none. The subject is property_id or "
         "listing_id.", ReferenceRentArgs, _reference_rent),
        ("describe_neighborhood",
         f"The market within the radius of a subject: how many ads are live, their disposition, building-type and "
         f"condition mix, price statistics by disposition, the trend, and how old the data is. {_SUBJECT}",
         NeighbourhoodArgs, _neighbourhood),
    )
)

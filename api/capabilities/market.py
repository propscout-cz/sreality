"""Tools over market data on the service connection (ADR-0002's one exception), each returning its toolkit envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from api.capabilities.entry import Context, Effect, Entry, Tenancy
from api.capabilities.subject import (
    CATEGORIES,
    HeldSubject,
    PlaceSubject,
    Subject,
    held_mf_facts,
    held_spec,
    place,
)
from api.estimate_yield import estimate_yield
from api.filter_args import Args, agenda_ids, filter_model, filter_values
from api.property_read import BROKERS_MAX, PropertyIds, property_brokers
from toolkit import (
    ComparableFilters,
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
# describe_neighborhood reads these four filters and applies no area, layout or floor. Its recency window needs no
# lifecycle, unlike a cohort's.
NeighbourhoodFilters = filter_model(
    "NeighbourhoodFilters", ("radius_m", "max_age_days", "category_main", "category_type"), describe_neighborhood,
    descriptions={"max_age_days": "Leave out ads last seen more than N days ago."},
)
ListingVelocityFilters = filter_model(
    "ListingVelocityFilters", ("radius_m", "disposition_match", "lifecycle"), compute_listing_velocity,
)


class ComparablesArgs(Subject, CohortFilters):
    min_results: int = Field(
        5, ge=1, le=50, description="Widen the cohort step by step until it holds at least this many; every "
        "widening is listed in data.relaxation_trace.")


_PER_M2 = "price_per_m2 is THE per-m² measure, in the unit the result's basis names"


class DistributionArgs(Subject, CohortFilters):
    field: Literal["price_per_m2", "price_czk", "area_m2"] = Field(
        "price_per_m2", description=f"The value to describe; {_PER_M2}.")


class OutliersArgs(Subject, CohortFilters):
    field: Literal["price_per_m2", "price_czk"] = Field("price_per_m2", description=f"The value to test; {_PER_M2}.")
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


class NeighbourhoodArgs(PlaceSubject, NeighbourhoodFilters):
    pass  # a place and the four filters describe_neighborhood reads; it applies no area, layout or floor


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
    from toolkit import _now_iso

    ad, spec = held_spec(ctx.conn, a)
    inputs = {"disposition": spec["disposition"], "area_m2": spec["area_m2"], **held_mf_facts(ctx.conn, ad)}
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


def _brokers(ctx: Context, a: PropertyIds) -> dict[str, Any]:
    return property_brokers(ctx.conn, a)


def _subject(args: type[Args]) -> str:
    """The subject sentence, naming only the categories `args` declares."""
    declared = [c for c in CATEGORIES if c in args.model_fields]
    return ("The subject is exactly one of property_id, listing_id or point. For a property or ad we hold, an unset "
            f"{' or '.join(declared)} takes the subject's own value (null means every category); a point must state "
            f"{' and '.join(declared)}.")


_COHORT = ("The cohort is the comparables these filters select, at most 500 nearest, each property once. Omitting "
           "lifecycle means every ad, live or delisted; max_age_days needs lifecycle='active'.")
_NO_UNIT = "'mixed' or 'unknown' means the cohort has no single per-m² unit."


def _read(name: str, description: str, args: type[Args], handler: Any) -> Entry:
    return Entry(name=name, description=description, args=args, handler=handler,
                 tenancy=Tenancy.MARKET, effect=Effect.READ)


ENTRIES = (
    _read("find_comparables_relaxed",
          f"Comparable ads around a subject, widened step by step until at least min_results are found. "
          f"{_subject(ComparablesArgs)} {_COHORT} A price_per_m2_basis of {_NO_UNIT}",
          ComparablesArgs, _comparables),
    _read("analyze_distribution",
          f"n, min, max, mean, median, p10 to p90, standard deviation and IQR of one value over the comparables "
          f"cohort these arguments select. {_subject(DistributionArgs)} {_COHORT} A basis of {_NO_UNIT}",
          DistributionArgs, _distribution),
    _read("find_distribution_outliers",
          f"The comparables whose value lies beyond the Tukey fences of their cohort. Only sreality ads are tested "
          f"today, and the ids returned are sreality ids. {_subject(OutliersArgs)} {_COHORT} A basis of {_NO_UNIT}",
          OutliersArgs, _outliers),
    _read("compute_market_velocity",
          f"Time on market around a subject: statistics over live and delisted ads, how many of each, and the trend "
          f"between ads first seen recently and before. A live ad's time so far is still running, and a delisting "
          f"is an ad ending, not a sale. {_subject(MarketVelocityArgs)} Lifecycle defaults to every ad.",
          MarketVelocityArgs, _market_velocity),
    _read("compute_listing_velocity",
          "One ad's time on market and its percentile rank among the ads around it.",
          ListingVelocityArgs, _listing_velocity),
    _read("estimate_yield",
          f"A rent or sale estimate from the comparables' per-m² distribution, and a gross yield when the other "
          f"price is given. The deal type always follows estimate_kind. {_subject(YieldArgs)} {_COHORT} A "
          f"price_per_m2_basis of {_NO_UNIT}", YieldArgs, _yield),
    _read("compute_reference_rent",
          "The Ministry of Finance rent-map reference (Cenová mapa nájemného) for a flat we hold: the monthly rent "
          "per m² its territory and facts give, or null where the map has none. The subject is property_id or "
          "listing_id.", ReferenceRentArgs, _reference_rent),
    _read("describe_neighborhood",
          f"The market within the radius of a subject: how many ads are live, their disposition, building-type and "
          f"condition mix, price statistics by disposition, the trend, and how old the data is. "
          f"{_subject(NeighbourhoodArgs)} A point is a place only (lat and lng).",
          NeighbourhoodArgs, _neighbourhood),
    # The page's broker read (POST /brokers/by-listings): the broker relations are the API's alone (A6), never the
    # user's login, so this is the market's connection, unmasked as the page shows every signed-in user.
    _read("get_property_brokers",
          f"Each property's brokers as its page lists them, in the order asked: the brokers of its live ads (of all "
          f"its ads when none is live, then from_inactive is true), one row per person, its canonical ad's broker "
          f"first, with name, agency, phone, e-mail and the ads they advertise there. A call lists at most "
          f"{BROKERS_MAX} people and stops at the next: metadata.brokers says how many of how many, and "
          f"metadata.unlisted_property_ids the properties to ask for again. A merged-away id follows its survivor.",
          PropertyIds, _brokers),
)

"""Prices achieved around a subject, for an agent (#2064), under the reader's own login: two arms, never mixed.

The PROXY arm is the Stats tab's sold-market lens (frontend/src/lib/stats.ts soldMarketFilters) over a circle round the
subject, Browse's own cohort over `browse_list`: properties that left the market after a time on market inside the
window, each at its last asking price, summed up by the dashboard's own statistic (api/stats_pivot). The REGISTERED
arm is the property page's registered sales (`public.sold_comparables`, migration 545; frontend/src/lib/soldComps.ts).
Every row and summary says which it is (`evidence`) and every per-m² figure sits beside its basis (rule 23).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Self

import psycopg
from pydantic import Field, model_validator

from api.capabilities.errors import ToolError, ToolErrorCode
from api.capabilities.subject import CATEGORIES, PlaceSubject
from api.filter_args import filter_model
from api.property_read import jsonable, properties
from api.property_search import cohort_where
from api.stats_pivot import AggregateArgs, payload_basis, pivot_args, run_pivot, whole_m2
from scraper.sold_db import MAX_READ_RADIUS_M
from toolkit import ComparableFilters, _max_last_seen, _now_iso
from toolkit import filter_registry as fr
from toolkit import stats_registry as sr
from toolkit.filter_compiler import SOLD_GRAIN, compile_filter_where
from toolkit.measures import BASIS_MIXED, BASIS_UNKNOWN, PPM2_BASIS_KEY, PPM2_VALUE_KEY, cohort_basis, measure_backed
from toolkit.property_identity import resolve_active_property_ids

PROXY, REGISTERED = "proxy", "registered"
ROWS_MAX = 25
# The registered-sales block's rules, twins of frontend/src/lib/queries.ts SOLD_COMPS_LIMIT and soldComps.ts
# SUMMARY_MIN_ROWS, SMALL_UNIT_M2 and COVERED_CATEGORIES (pinned by tests/api/test_market_evidence.py).
SOLD_COMPS_LIMIT = 200
SUMMARY_MIN_ROWS = 5
SMALL_UNIT_M2 = 30
COVERED_CATEGORIES = ("byt", "dum")
# A proxy row: Browse's published facts of a property that left the market, never a parked condition level (rule 14) or
# a city-quality column (rule 17); `price_czk` is its last asking price (its last-seen ad's).
PROXY_COLUMNS = (
    "property_id", "category_main", "category_type", "price_czk", "area_m2", "price_per_m2", "price_per_m2_basis",
    "tom_days", "last_seen_at", "disposition", "condition", "building_type",
)
# A registered sale as the page's block shows it (queries.ts SOLD_COMP_COLS) less photos, links and the asking price.
REGISTERED_COLUMNS = (
    "source", "source_record_id", "sold_at", "category_main", "price_czk", "area_m2", "price_per_m2",
    "price_per_m2_basis", "disposition", "address_text", "distance_m", "sold_age_days", "fetched_at",
)
_COVERAGE_COLUMNS = ("obec_kod", "obec_name", "fetched_at", "record_count", "source_total", "truncated",
                     "last_attempt_at", "last_attempt_status")
_SOLD_FILTERS = ("dispositions", "min_area_m2", "max_area_m2", "max_sold_age_days")
# What each arm cannot apply, so it is announced rather than dropped (registered sales have no time on market).
_NOT_REGISTERED = ("condition_match", "building_material", "last_seen_max_days")
_NOT_PROXY = ("max_sold_age_days",)
# Browse's circle takes the registry's radius domain as its bounds (api/location_filter.MapCircle).
_RADIUS_MAX = fr.REGISTRY["radius_m"].constraints["max"]
_DISTANCE = ("st_distance(st_makepoint(l.lng, l.lat)::geography, "
             "st_makepoint(%(subject_lng)s::float8, %(subject_lat)s::float8)::geography)")
_LO, _HI = sr.SOLD_MARKET_DAYS


def sold_lens(*, tom_days_min: int = _LO, tom_days_max: int = _HI, **cohort: Any) -> dict[str, Any]:
    """stats.ts soldMarketFilters over a Browse cohort: inactive, observed from its first ad, dismissed included, its
    time on market inside the window (the Stats tab's by default)."""
    return {**cohort, "status": "inactive", "tom_observed_only": True, "show_dismissed": True,
            "tom_days_min": tom_days_min, "tom_days_max": tom_days_max}


EvidenceScope = filter_model(
    "EvidenceScope", ("radius_m", "category_main", "category_type"), ComparableFilters,
    descriptions={
        "radius_m": f"The circle round the subject, in metres: the proxy arm reads all of it, the registered arm at "
        f"most {MAX_READ_RADIUS_M} (its sales are fetched no further round each town), the radius it read in "
        "metadata.registered_radius_m.",
        "category_main": "The property type, one value; null = every type. Registered sales are byt and dum only.",
        "category_type": "Deal type: pronajem = for rent, prodej = for sale, drazba = auction, podil = fractional "
        "ownership; null = every deal type. It decides the per-m² unit; registered sales are prodej only.",
    },
)
EvidenceFilters = filter_model(
    "EvidenceFilters",
    ("dispositions", "min_area_m2", "max_area_m2", "condition_match", "building_material", "last_seen_max_days",
     "max_sold_age_days"), None,
    descriptions={
        "last_seen_max_days": "Proxy arm: only properties last seen at most N days ago.",
        "max_sold_age_days": "Registered arm: only sales registered at most N days ago (the store keeps 24 months).",
    },
)
EvidenceWindow = filter_model(
    "EvidenceWindow", ("tom_days_min", "tom_days_max"), sold_lens,
    descriptions={
        "tom_days_min": f"Proxy arm: the shortest time on market, in days (inclusive), {_LO} by default; any window "
        f"but {_LO}-{_HI} needs window_reason.",
        "tom_days_max": f"Proxy arm: the longest time on market, in days (inclusive), {_HI} by default; any window "
        f"but {_LO}-{_HI} needs window_reason.",
    },
)


class EvidenceArgs(PlaceSubject, EvidenceScope, EvidenceFilters, EvidenceWindow):
    min_area_m2: int | None = whole_m2(EvidenceFilters, "min_area_m2")
    max_area_m2: int | None = whole_m2(EvidenceFilters, "max_area_m2")
    window_reason: str | None = Field(
        None, max_length=500, description=f"Why the window is not {_LO}-{_HI} days, wider or narrower (e.g. a luxury "
        "segment sells slowly); required for any other window and echoed in metadata.window.")
    limit: int = Field(10, ge=1, le=ROWS_MAX, description="Rows per arm, nearest first; the summaries read the whole "
                       f"cohort (registered: the nearest {SOLD_COMPS_LIMIT} sales).")

    @model_validator(mode="after")
    def _a_window_says_why(self) -> Self:
        if self.tom_days_min > self.tom_days_max:
            raise ValueError("tom_days_min is above tom_days_max")
        if (self.tom_days_min, self.tom_days_max) != sr.SOLD_MARKET_DAYS and not (self.window_reason or "").strip():
            raise ValueError(f"a window other than {_LO}-{_HI} days needs window_reason")
        if self.radius_m > _RADIUS_MAX:
            raise ValueError(f"radius_m is at most {_RADIUS_MAX}, as on Browse's map circle")
        return self


def summarize_sold(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """soldComps.ts summarizeSold, line for line: the median per-m² price of the sales in `rows`, units under
    SMALL_UNIT_M2 held out of it and counted; none under SUMMARY_MIN_ROWS kept; flats and houses together flagged
    (one median over both is about neither). A sale with no basis reads 'unknown' (the TS's null)."""
    kept = [r for r in rows if r["area_m2"] is None or r["area_m2"] >= SMALL_UNIT_M2]
    if len(kept) < SUMMARY_MIN_ROWS:
        return None
    tokens = {r[PPM2_BASIS_KEY] for r in kept}
    shape = {"n": len(kept), "held_out": len(rows) - len(kept),
             PPM2_BASIS_KEY: (next(iter(tokens)) or BASIS_UNKNOWN) if len(tokens) == 1 else BASIS_MIXED,
             "mixed_kinds": len({r["category_main"] for r in rows}) > 1}
    values = sorted(r[PPM2_VALUE_KEY] for r in kept if r[PPM2_VALUE_KEY] is not None)
    if not values:
        return {**shape, "median_price_per_m2": None}
    mid = len(values) // 2
    return {**shape, "n": len(values),
            "median_price_per_m2": values[mid] if len(values) % 2 == 1 else (values[mid - 1] + values[mid]) / 2}


def _subject(conn: psycopg.Connection, args: EvidenceArgs) -> tuple[int | None, float, float, Any, Any]:
    """The subject's property (none for a point), where it stands, and the two categories the arms take: an unset one
    is a held property's own, an explicit null every category. Read as the property page reads it."""
    if args.point is not None:
        return None, args.point.lat, args.point.lng, args.category_main, args.category_type
    pid, named = args.property_id, f"property {args.property_id}"
    if args.listing_id is not None:
        named = f"ad {args.listing_id}"
        with conn.cursor() as cur:
            cur.execute("SELECT property_id FROM listings_public WHERE id = %s", (args.listing_id,))
            row = cur.fetchone()
        if row is None or row[0] is None:
            raise ToolError(ToolErrorCode.NOT_FOUND, f"{named} is not in our database or belongs to no property yet")
        pid = int(row[0])
    survivor = resolve_active_property_ids(conn, [pid]).get(pid)
    found = properties(conn, [survivor]) if survivor is not None else []
    if not found:
        raise ToolError(ToolErrorCode.NOT_FOUND, f"{named} is not in our database")
    prop = found[0]
    if prop["lat"] is None:
        raise ToolError(ToolErrorCode.REFUSED, f"{named} has no location yet")
    cm, ct = (getattr(args, c) if c in args.model_fields_set else prop[c] for c in CATEGORIES)
    return survivor, prop["lat"], prop["lng"], cm, ct


def _proxy(conn: psycopg.Connection, lens: AggregateArgs, subject: int | None, at: dict[str, float],
           limit: int) -> tuple[list[dict[str, Any]], list[int]]:
    """The nearest `limit` properties of the lens's cohort, and every one's id; the subject's own property is never
    its own evidence (Decision 13)."""
    where, params = cohort_where(lens)
    if subject is not None:
        where.append("l.property_id <> %(subject_property_id)s")
        params["subject_property_id"] = subject
    source = f"FROM browse_list l WHERE {' AND '.join(where)}"
    columns = ", ".join(f"l.{c}" for c in PROXY_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(f"SELECT {columns}, {_DISTANCE} AS distance_m {source} "
                    "ORDER BY distance_m, l.last_seen_at DESC NULLS LAST, l.property_id LIMIT %(limit)s",
                    {**params, **at, "limit": limit})
        rows = [{"evidence": PROXY, **jsonable(dict(zip((*PROXY_COLUMNS, "distance_m"), r, strict=True)))}
                for r in cur.fetchall()]
        cur.execute(f"SELECT l.property_id {source}", params)
        return rows, [int(r[0]) for r in cur.fetchall()]


def _summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The pivot's cohort total (keys [] under the empty spec) with its ledger, refusal and basis."""
    total = next((c for c in payload.get("cells") or () if not c["keys"] and c["col"] is None), {})
    return {"evidence": PROXY, **{k: payload.get(k) for k in ("filtered", "sample", "excluded", "min_n", "refused")},
            **{k: total.get(k) for k in (*sr.STATS, "small")}, PPM2_BASIS_KEY: payload_basis(payload)}


def _covered(category_main: Any, category_type: Any) -> str | None:
    """Why the registered arm is empty for these categories, or None when it reads."""
    if category_type not in (None, "prodej") or category_main not in (None, *COVERED_CATEGORIES):
        return f"registered sales are {' and '.join(COVERED_CATEGORIES)} for sale (prodej) only"
    return None


def _registered(conn: psycopg.Connection, args: EvidenceArgs, category_main: Any, at: dict[str, float],
                radius_m: int) -> tuple[list[dict[str, Any]], bool, dict[str, Any] | None]:
    """The page's registered sales within `radius_m` of the subject, nearest first (queries.ts fetchSoldComparables),
    at most SOLD_COMPS_LIMIT, whether more exist, and the coverage of the centre's own town."""
    values = {"category_main_in": None if category_main is None else [category_main],
              **{f: getattr(args, f) for f in _SOLD_FILTERS}}
    where, params = compile_filter_where(values, SOLD_GRAIN)
    columns = ", ".join(f"l.{c}" for c in REGISTERED_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {columns} FROM public.sold_comparables(%(subject_lat)s::float8, %(subject_lng)s::float8, "
            f"%(radius_m)s::float8) l WHERE {' AND '.join(where) or 'true'} "
            "ORDER BY l.distance_m, l.sold_at DESC, l.source_record_id LIMIT %(cap)s",
            {**params, **at, "radius_m": radius_m, "cap": SOLD_COMPS_LIMIT + 1})
        sales = [{"evidence": REGISTERED, **jsonable(dict(zip(REGISTERED_COLUMNS, r, strict=True)))}
                 for r in cur.fetchall()]
        cur.execute(f"SELECT {', '.join(_COVERAGE_COLUMNS)} FROM public.sold_coverage(%(subject_lat)s::float8, "
                    "%(subject_lng)s::float8)", at)
        coverage = cur.fetchone()
    return (sales[:SOLD_COMPS_LIMIT], len(sales) > SOLD_COMPS_LIMIT,
            None if coverage is None else jsonable(dict(zip(_COVERAGE_COLUMNS, coverage, strict=True))))


def _set(args: EvidenceArgs, ids: tuple[str, ...]) -> list[str]:
    return [f for f in ids if getattr(args, f) not in (None, [])]


def market_evidence(conn: psycopg.Connection, args: EvidenceArgs) -> dict[str, Any]:
    """Both arms round the subject: rows nearest first, each arm's summary, and what each could not apply."""
    subject, lat, lng, category_main, category_type = _subject(conn, args)
    at = {"subject_lat": lat, "subject_lng": lng}
    lens = AggregateArgs.model_validate(sold_lens(
        tom_days_min=args.tom_days_min, tom_days_max=args.tom_days_max,
        category_main_in=None if category_main is None else [category_main], category_type=category_type,
        dispositions=args.dispositions, min_area_m2=args.min_area_m2, max_area_m2=args.max_area_m2,
        condition_match=args.condition_match, building_material=args.building_material,
        last_seen_max_days=args.last_seen_max_days, circle={"lat": lat, "lng": lng, "radius_m": args.radius_m}))
    proxy, cohort = _proxy(conn, lens, subject, at, args.limit)
    payload = run_pivot(conn, pivot_args(conn, lens, property_ids=cohort))
    refused = _covered(category_main, category_type)
    # Sales are fetched no further than MAX_READ_RADIUS_M round each town (scraper/sold_db.py), so a wider circle
    # would be covered by luck, not by construction: the arm reads at most that far, as the page's largest radius.
    reach = None if refused else min(args.radius_m, MAX_READ_RADIUS_M)
    sales, more, coverage = ([], False, None) if reach is None else _registered(conn, args, category_main, at, reach)
    registered = sales[:args.limit]
    summary = summarize_sold(sales) or {"n": None, "held_out": None, "mixed_kinds": None, "median_price_per_m2": None,
                                        PPM2_BASIS_KEY: cohort_basis(measure_backed(sales))}
    return {
        "data": {
            PROXY: proxy,
            REGISTERED: registered,
            "proxy_summary": _summary(payload),
            "registered_summary": {"evidence": REGISTERED, "sales": len(sales), "more": more,
                                   "min_n": SUMMARY_MIN_ROWS, **summary},
        },
        "metadata": {
            "tool": "market_evidence",
            "filters_used": {**args.model_dump(mode="json", by_alias=True, exclude_defaults=True),
                             "category_main": category_main, "category_type": category_type},
            "result_count": len(cohort) + len(sales),
            "queried_at": _now_iso(),
            "data_freshness": _max_last_seen(proxy),
            "subject": {"property_id": subject, "lat": lat, "lng": lng},
            "window": {"tom_days_min": args.tom_days_min, "tom_days_max": args.tom_days_max,
                       "default": (args.tom_days_min, args.tom_days_max) == sr.SOLD_MARKET_DAYS,
                       "reason": args.window_reason},
            "registered_radius_m": reach,
            "registered_coverage": coverage,
            "registered_refused": refused,
            "unapplied": {REGISTERED: _set(args, _NOT_REGISTERED), PROXY: _set(args, _NOT_PROXY)},
            # The rows' one per-m² unit (rule 23); 'mixed' or 'unknown' means they have none.
            "price_per_m2_basis": cohort_basis(measure_backed(proxy + registered)),
        },
    }

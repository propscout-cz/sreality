"""Parsing and data quality: are new ads still read the way they were, and what standing data
conditions does the stock carry?

`check_parsing` is an alarm over five arms, every subject emitted on every pass: fill and
vocabulary per (portal, field) against the blessed baseline (scraper.field_census) plus the key
census's age; measures of new ads and the per-m² median per (portal, category, deal) from
measure_plausibility_by_source; the payload shape of new ads through the predicates
location_data exports; and re-snapshot churn per portal against its own 28 days. The gauges below
the marker judge the standing conditions (data quality, location coverage, description reading).
The measure view is read once per health pass and shared through clock.PASS; portal names come
from location_data and are bound as parameters; every statement runs on the per-check clock and
the module imports neither the harness nor the worker.
"""

from __future__ import annotations

import datetime as _dt
import statistics
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any, NamedTuple

from location_data import location_steps
from location_data.claims_common import (
    PAYLOAD_SHAPE_PREDICATES,
    PAYLOAD_SHAPE_REMEDIES,
    SOURCES,
)
from location_data.resolver.version import RESOLVER_VERSION
from scraper import attribute_contract, field_census
from scripts.checks import clock
from scripts.checks.clock import fetchall, fetchone
from scripts.checks.common import (
    NEW_AD_H,
    ago,
    as_utc,
    cell,
    graded,
    iso,
    judged,
    now,
    oneline,
    result,
    with_trends,
)
from toolkit import description_extraction as text_lane
from toolkit.measures import unit_label, wilson_lower

_PARSING = "parsing"

# --- the measure view, read once per pass (parsing and data_quality) --------------------------

PLAUSIBILITY_COLUMNS = (
    "source", "category_main", "category_type", "price_per_m2_basis", "n_active",
    "median_area_m2", "median_usable_area", "median_price_per_m2",
    "n_floor_eligible", "floor_null_share", "n_floor_eligible_7d", "floor_null_share_7d",
    "n_area_pairs", "area_divergence_share", "n_area_pairs_7d", "area_divergence_share_7d",
    "n_area_valued", "n_ppm2_valued", "n_active_7d",
    "measure_input_gap_share", "measure_input_gap_share_7d",
    "n_estate_triples", "estate_sum_share",
)

# Active ads only (migration 534); about 3.5 s warm, 12 s cold. Admin-gated: a session the gate
# refuses reads zero rows, which is Not measured, never a clean bill.
PLAUSIBILITY_SQL = f"""
select {', '.join(PLAUSIBILITY_COLUMNS)}
  from measure_plausibility_by_source
 order by source, category_main, category_type
"""
_PLAUSIBILITY_MEMO = "measure_plausibility"


def _read_plausibility(conn: Any) -> tuple[list[dict[str, Any]], str | None]:
    """The view's rows, or none and why: the failure is the read's answer, memoised with it."""
    try:
        rows = fetchall(conn, PLAUSIBILITY_SQL)
    except Exception as exc:  # noqa: BLE001 — an unreadable view is Not measured, never a red
        return [], f"measure_plausibility_by_source is unreadable: {oneline(exc, 160)}"
    if not rows:
        return [], ("measure_plausibility_by_source returned no rows: no active ad, or its "
                    "is_platform_admin() gate refuses this session")
    return [{name: float(value) if isinstance(value, Decimal) else value
             for name, value in zip(PLAUSIBILITY_COLUMNS, row)} for row in rows], None


def _plausibility(conn: Any) -> tuple[list[dict[str, Any]], str | None]:
    """The view's rows by column (numeric as float), read once per pass, and why there are none."""
    return clock.PASS.memo(_PLAUSIBILITY_MEMO, lambda: _read_plausibility(conn))


def _grain(row: dict[str, Any]) -> str:
    """A view row's `<category>-<deal>`, '?' for a missing part: in subjects, never respelled."""
    return f"{row.get('category_main') or '?'}-{row.get('category_type') or '?'}"


# --- parsing (alarm) ------------------------------------------------------------------------

# Active ads first seen in the trailing 48 h: after a portal changes its payload the unreadable
# share climbs as elapsed / window, so the pass 6 h later already reads 12.5 %.
_PAYLOAD_WINDOW_H = 48
# A median is compared with this check's own reading of it 6-14 d earlier.
_MEDIAN_WINDOW_D = (6, 14)
_MEDIAN_ARM = "/median/"
_MEASURES_ARM = "/measures/"
# Churn: the last 24 h against each of the 28 whole 24-h windows before it.
_CHURN_DAYS = 28
_CENSUS = "census"

# This check's own medians 6-14 d before the reading, the newest per cell, from each median
# cell's facts whatever its status (a reading is a reading), and when the check first stored a
# result: a median is Not measured for the check's first 6 d.
_MEDIAN_BASELINE_SQL = """
with first as (
    select min(r.run_at) as at
      from pipeline_check_results r
     where r.check_key = %(key)s
), base as (
    select distinct on (c ->> 'subject')
           c ->> 'subject' as subject, r.run_at,
           (c -> 'facts' ->> 'median')::numeric as median,
           c -> 'facts' ->> 'price_per_m2_basis' as basis,
           case when jsonb_typeof(c -> 'facts' -> 'n') = 'number'
                then (c -> 'facts' ->> 'n')::numeric end as n
      from pipeline_check_results r
      cross join lateral jsonb_array_elements(
             case when jsonb_typeof(r.details -> 'cells') = 'array'
                  then r.details -> 'cells' else '[]'::jsonb end) c
     where r.check_key = %(key)s
       and r.run_at < now() - make_interval(days => %(young_d)s::int)
       and r.run_at > now() - make_interval(days => %(old_d)s::int)
       and strpos(c ->> 'subject', %(arm)s::text) > 0
       and jsonb_typeof(c -> 'facts' -> 'median') = 'number'
     order by c ->> 'subject', r.run_at desc
)
select f.at, b.subject, b.run_at, b.median, b.basis, b.n
  from first f
  left join base b on true
"""


def _payload_sql(predicates: Mapping[str, str]) -> str:
    """Every portal's readability predicate in one statement, each bound to its portal by name."""
    arms = "\n".join(f"                when %(shape_{i})s then coalesce({predicate}, false)"
                     for i, predicate in enumerate(predicates.values()))
    return f"""
select l.source, count(*)::bigint as ads,
       count(*) filter (where not (case l.source
{arms}
            end))::bigint as unreadable
  from listings l
 where l.source = any(%(portals)s::text[])
   and l.is_active
   and l.first_seen_at > now() - make_interval(hours => %(window_h)s::int)
 group by l.source
"""


# The portals whose contract reads `listings.raw_json`, in the order _PAYLOAD_SQL binds them.
_PAYLOAD_PORTALS = tuple(PAYLOAD_SHAPE_PREDICATES)
_PAYLOAD_SQL = _payload_sql(PAYLOAD_SHAPE_PREDICATES)

# Re-snapshots (an ad's second and later snapshots) per portal in each 24-h window back from
# now, 0 the reading and 1-28 its baseline, and each portal's active ads. One ad never gets two
# snapshots in one statement, so an earlier scraped_at is the whole test, and both sides are
# index-only probes of (listing_id, scraped_at): 2.8 s over 282,584 snapshots on 10-19.
_CHURN_SQL = """
with snaps as (
    select l.source,
           floor(extract(epoch from (now() - s.scraped_at)) / 86400)::int as days_back,
           count(*)::bigint as resnaps
      from listing_snapshots s
      join listings l on l.id = s.listing_id
     where s.scraped_at > now() - make_interval(days => %(days)s::int + 1)
       and exists (select 1
                     from listing_snapshots p
                    where p.listing_id = s.listing_id
                      and p.scraped_at < s.scraped_at)
     group by 1, 2
), active as (
    select l.source, count(*)::bigint as ads
      from listings l
     where l.is_active
     group by 1
)
select a.source, a.ads, s.days_back, s.resnaps
  from active a
  left join snaps s on s.source = a.source
"""


def _past(status: str, warn: str, fail: str) -> str:
    """The line a non-OK reading crossed, as the page says it."""
    return {"fail": f" (Problem past {fail})", "warn": f" (Watch past {warn})"}.get(status, "")


def _versus(excess: float) -> str:
    return (f"{excess * 100:.1f} pp above" if excess > 0 else
            f"{-excess * 100:.1f} pp below" if excess < 0 else "level with")


# --- fill and vocabulary --------------------------------------------------------------------


def _matrix_lines(thresholds: dict[str, Any]) -> dict[str, Any]:
    """The field matrix's lines from the harness, in scraper.field_census's argument names."""
    return {
        "fill_drop_warn_pp": float(thresholds["parsing_fill_drop_warn_pp"]),
        "fill_drop_fail_pp": float(thresholds["parsing_fill_drop_fail_pp"]),
        "fill_drop_min_baseline": float(thresholds["parsing_fill_drop_min_baseline"]),
        "fill_collapse_ratio": float(thresholds["parsing_fill_collapse_ratio"]),
        "fill_collapse_min_rows": int(thresholds["parsing_fill_collapse_min_rows"]),
        "off_canon_rise_warn_pp": float(thresholds["parsing_vocabulary_rise_warn_pp"]),
        "off_canon_rise_fail_pp": float(thresholds["parsing_vocabulary_rise_fail_pp"]),
    }


def _fill_cell(portal: str, field: str, measured: Mapping[str, Any] | None,
               blessed: Mapping[str, Any] | None, moved: list[tuple[str, str]], *,
               inert: bool, known_gap: str | None, zero: bool) -> dict[str, Any]:
    """One field's fill on one portal, against the fill it was blessed at."""
    subject = f"{portal}/{field_census.FILL}/{field}"
    facts: dict[str, Any] = {}
    if blessed is not None:
        facts.update(blessed_fill=blessed.get("fill"), blessed_filled=blessed.get("filled"))
    if measured is None:
        if not moved:
            return cell(subject, "unknown", "Not measured: absent from the live matrix",
                        unit="%", portal=portal, facts=facts)
        return judged(subject, moved, "absent from the live matrix", unit="%", portal=portal,
                      facts=facts)
    ads, filled = int(measured.get("n", 0)), int(measured.get("filled", 0))
    fill = float(measured.get("fill", 0.0))
    facts.update(ads=ads, filled=filled, fill=fill)
    if "true" in measured:
        facts.update(true=measured.get("true"), false=measured.get("false"))
    if inert:
        facts["inert"] = True
    if known_gap is not None:
        facts["known_gap"] = known_gap
    conditions = [(status, f"{reason}; once that is confirmed a fix, re-bless with "
                           f"`{field_census.BLESS_COMMAND}`") for status, reason in moved]
    if zero and known_gap is None and not inert:
        conditions.append(("warn", "no active ad carries it while the attribute contract names a "
                                   "producer for it (Watch, never Problem)"))
    said = f"fill {fill:.1%} of {ads:,} active ads"
    said += (f" (blessed {float(blessed.get('fill', 0.0)):.1%})" if blessed is not None else
             "; not in the blessed baseline yet: the next re-bless adds it")
    if inert:
        said += "; nothing can write it today, so a fall is expected"
    if zero and known_gap is not None:
        said += f"; a known gap ({known_gap})"
    return judged(subject, conditions, said, value=round(fill * 100, 2), unit="%", portal=portal,
                  facts=facts)


def _vocabulary_cell(portal: str, field: str, measured: Mapping[str, Any] | None,
                     blessed: Mapping[str, Any] | None,
                     moved: list[tuple[str, str]]) -> dict[str, Any]:
    """One canon field's share of values off the canon on one portal, against the blessed share."""
    subject = f"{portal}/{field_census.VOCABULARY}/{field}"
    if measured is None:
        return cell(subject, "unknown", "Not measured: absent from the live matrix (its fill "
                    "cell judges that)", unit="%", portal=portal)
    off, filled = float(measured.get("off_canon", 0.0)), int(measured.get("filled", 0))
    spellings = [str(value) for value in measured.get("off_canon_values") or []]
    facts: dict[str, Any] = {"filled": filled, "off_canon": off,
                             "off_canon_values": spellings[:8]}
    said = f"{off:.1%} of {filled:,} values off the canon"
    if spellings:
        said += f" ({', '.join(spellings[:4])})"
    if blessed is not None:
        facts["blessed_off_canon"] = float(blessed.get("off_canon", 0.0))
        said += f" (blessed {facts['blessed_off_canon']:.1%})"
    else:
        said += "; not in the blessed baseline yet: the next re-bless adds it"
    conditions = [(status, f"{reason}; map the spelling in scraper/vocabulary.py, or re-bless "
                           f"with `{field_census.BLESS_COMMAND}` once it is confirmed right")
                  for status, reason in moved]
    return judged(subject, conditions, said, value=round(off * 100, 2), unit="%", portal=portal,
                  facts=facts)


def _field_cells(conn: Any, thresholds: dict[str, Any],
                 at: _dt.datetime) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Per (portal, field) of the live and the blessed matrix: fill, and vocabulary on a canon."""
    live = field_census.reduce_matrix(fetchall(conn, field_census.FIELD_MATRIX_SQL),
                                      generated_at=at)
    try:
        baseline = field_census.load_baseline()
    except (OSError, ValueError) as exc:
        why = f"the blessed baseline is unreadable: {oneline(exc, 160)}"
        return [], {field_census.FILL: why, field_census.VOCABULARY: why}
    inert, gaps = attribute_contract.inert_cells(), attribute_contract.known_gaps()
    moved: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for found in field_census.compare_to_baseline(live, baseline, inert=inert,
                                                  **_matrix_lines(thresholds)):
        moved.setdefault((found.key, found.arm), []).append((found.status, found.reason))
    zero = set(field_census.zero_fill_cells(live))
    blessed_cells, live_cells = baseline.get("cells") or {}, live["cells"]
    cells = []
    for key in sorted(set(blessed_cells) | set(live_cells)):
        portal, field = key.split("/", 1)
        blessed = blessed_cells.get(key)
        blessed = blessed if isinstance(blessed, Mapping) else None
        measured = live_cells.get(key)
        known_gap = (gaps[key] or "the portal never states it") if key in gaps else None
        cells.append(_fill_cell(portal, field, measured, blessed,
                                moved.get((key, field_census.FILL), []), inert=key in inert,
                                known_gap=known_gap, zero=key in zero))
        if field_census.canonical_values(field) is not None:
            cells.append(_vocabulary_cell(portal, field, measured, blessed,
                                          moved.get((key, field_census.VOCABULARY), [])))
    return cells, {}


def _census_cell(thresholds: dict[str, Any], at: _dt.datetime) -> dict[str, Any]:
    """The portals' key censuses: Watch when one is older than the reviewed-diff cadence."""
    max_d = int(thresholds["parsing_census_stale_days"])
    try:
        censuses = field_census.load_censuses()
    except (OSError, ValueError) as exc:
        return cell(_CENSUS, "unknown", f"Not measured: a key census is unreadable: "
                    f"{oneline(exc, 160)}")
    if not censuses:
        return cell(_CENSUS, "unknown", "Not measured: no key census on this host")
    stale = field_census.stale_censuses(censuses, now=at, max_age_days=max_d)
    facts = {"censuses": len(censuses), "stale": stale, "max_age_d": max_d}
    conditions = []
    if stale:
        conditions.append(("warn", f"{'; '.join(stale)}: a portal may have renamed a key its "
                                   f"census still lists; re-run `{field_census.BLESS_COMMAND}` "
                                   f"(Watch past {max_d} d, never Problem)"))
    return judged(_CENSUS, conditions, f"all {len(censuses)} portal key censuses are at most "
                  f"{max_d} d old", value=len(stale), unit="portals", facts=facts)


# --- measures of new ads and the per-m² median ------------------------------------------------


def _measures_cells(rows: list[dict[str, Any]],
                    thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Per (portal, category, deal): new ads with no per-m² input, against the cell's own stock."""
    warn, fail = (float(thresholds["parsing_measures_warn"]),
                  float(thresholds["parsing_measures_fail"]))
    min_n = int(thresholds["parsing_measures_min_n"])
    cells = []
    for row in rows:
        basis = row["price_per_m2_basis"]
        if basis is None:
            continue  # no basis, no measure by specification: there is no gap to judge
        portal = row["source"]
        subject = f"{portal}{_MEASURES_ARM}{_grain(row)}"
        recent, ads = int(row["n_active_7d"] or 0), int(row["n_active"] or 0)
        gap_7d, gap = row["measure_input_gap_share_7d"], row["measure_input_gap_share"]
        facts: dict[str, Any] = {
            "price_per_m2_basis": basis, "ads_7d": recent, "ads": ads,
            "gap_7d": None if gap_7d is None else round(gap_7d, 4),
            "gap": None if gap is None else round(gap, 4)}
        if recent < min_n or gap_7d is None or gap is None:
            cells.append(cell(subject, "unknown", f"Not measured: {recent:,} ads first seen in "
                              f"7 d, under the {min_n} this arm needs", unit="pp", portal=portal,
                              facts=facts))
            continue
        missing = round(gap_7d * recent)
        lower = wilson_lower(missing, recent) or 0.0
        excess = lower - gap
        facts.update(missing_7d=missing, lower_bound=round(lower, 4), excess=round(excess, 4))
        status = graded(excess, warn, fail)
        said = (f"{missing:,} of the {recent:,} ads first seen in 7 d have no price or no positive "
                f"area ({gap_7d:.1%}, lower bound {lower:.1%}), {_versus(excess)} the {gap:.1%} of "
                f"all {ads:,} active ads"
                f"{_past(status, f'{warn * 100:g} pp', f'{fail * 100:g} pp')}")
        cells.append(cell(subject, status, said, value=round(excess * 100, 1) + 0.0, unit="pp",
                          portal=portal, facts=facts))
    return cells


def _median_baseline(conn: Any) -> tuple[_dt.datetime | None,
                                         dict[str, tuple[_dt.datetime, float, Any, int]]]:
    """When this check first stored a result, and each median cell's newest reading 6-14 d ago."""
    young, old = _MEDIAN_WINDOW_D
    rows = fetchall(conn, _MEDIAN_BASELINE_SQL, {"key": _PARSING, "young_d": young, "old_d": old,
                                                 "arm": _MEDIAN_ARM})
    first = as_utc(rows[0][0]) if rows else None
    then = {str(subject): (as_utc(run_at), float(median), basis, int(n or 0))
            for _, subject, run_at, median, basis, n in rows if subject is not None}
    return first, then


def _per_m2(value: float, unit: str) -> str:
    return f"{value:,.0f} {unit}"


def _median_cells(conn: Any, rows: list[dict[str, Any]], thresholds: dict[str, Any],
                  at: _dt.datetime) -> list[dict[str, Any]]:
    """Per (portal, category, deal): the active stock's per-m² median against itself 6-14 d ago."""
    young, old = _MEDIAN_WINDOW_D
    first, then = _median_baseline(conn)
    history_d = 0.0 if first is None else (at - first).total_seconds() / 86400
    warn, fail = float(thresholds["parsing_median_warn"]), float(thresholds["parsing_median_fail"])
    min_n = int(thresholds["parsing_median_min_n"])
    cells = []
    for row in rows:
        basis = row["price_per_m2_basis"]
        if basis is None:
            continue  # no basis, no per-m² measure by specification
        portal = row["source"]
        subject = f"{portal}{_MEDIAN_ARM}{_grain(row)}"
        median, carried = row["median_price_per_m2"], int(row["n_ppm2_valued"] or 0)
        facts: dict[str, Any] = {"median": None if median is None else round(median, 2),
                                 "price_per_m2_basis": basis, "n": carried,
                                 "ads": int(row["n_active"] or 0)}
        unknown = None
        before = then.get(subject)
        if history_d < young:
            unknown = (f"this check has {history_d:.1f} d of its own history, and a median is "
                       f"compared with its own reading {young}-{old} d earlier")
        elif before is None:
            unknown = f"no reading of this median {young}-{old} d ago"
        else:
            then_at, then_median, then_basis, then_n = before
            facts.update(median_then=round(then_median, 2), n_then=then_n,
                         baseline_at=iso(then_at))
            if then_basis != basis:
                unknown = f"its basis was {then_basis} {ago((at - then_at).total_seconds())} ago"
            elif median is None or carried < min_n or then_n < min_n:
                unknown = (f"{carried:,} ads carry a per-m² measure now and {then_n:,} then, "
                           f"under the {min_n} this arm needs in both")
            elif median <= 0 or then_median <= 0:
                # Land's per-m² price is rounded to 2 dp with no floor (migration 425), so a
                # median can read 0; a ratio needs both sides positive.
                when = ("is 0 now" if median <= 0 else
                        f"was 0 {ago((at - then_at).total_seconds())} ago")
                unknown = f"the median {when}, and a shift is the ratio of two positive medians"
        if unknown is not None:
            cells.append(cell(subject, "unknown", f"Not measured: {unknown}", unit="×",
                              portal=portal, facts=facts))
            continue
        ratio = max(median / then_median, then_median / median)
        facts["ratio"] = round(ratio, 3)
        status = graded(ratio, warn, fail)
        unit = unit_label(basis)
        age = ago((at - then_at).total_seconds())
        said = (f"median {_per_m2(median, unit)} against {_per_m2(then_median, unit)} {age} ago"
                if unit else f"the per-m² median (basis {basis}, which has no unit) against its "
                             f"reading {age} ago")
        said += f" ({ratio:.2f}×, over {carried:,} ads carrying it)"
        if status != "ok":
            said += (f"{_past(status, f'{warn:g}×', f'{fail:g}×')}: a parser change or a heal "
                     "moved it; confirm which before dismissing")
        cells.append(cell(subject, status, said, value=round(ratio, 3), unit="×", portal=portal,
                          facts=facts))
    return cells


# --- payload shape and churn ------------------------------------------------------------------


def _payload_cells(conn: Any,
                   thresholds: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Per portal with a readability predicate: new ads whose location payload it cannot read."""
    if not _PAYLOAD_PORTALS:
        return [], {"payload": "location_data exports no payload-shape predicate"}
    params: dict[str, Any] = {"portals": list(_PAYLOAD_PORTALS), "window_h": _PAYLOAD_WINDOW_H,
                              **{f"shape_{i}": p for i, p in enumerate(_PAYLOAD_PORTALS)}}
    read = {str(source): (int(ads or 0), int(bad or 0))
            for source, ads, bad in fetchall(conn, _PAYLOAD_SQL, params)}
    warn, fail = (float(thresholds["parsing_payload_warn"]),
                  float(thresholds["parsing_payload_fail"]))
    min_n = int(thresholds["parsing_payload_min_n"])
    cohort = f"active ads first seen in {_PAYLOAD_WINDOW_H} h"
    cells = []
    for portal in _PAYLOAD_PORTALS:
        ads, bad = read.get(portal, (0, 0))
        facts = {"ads": ads, "unreadable": bad, "window_h": _PAYLOAD_WINDOW_H}
        if ads < min_n:
            cells.append(cell(f"{portal}/payload", "unknown", f"Not measured: {ads:,} {cohort}, "
                              f"under the {min_n} this arm needs", unit="%", portal=portal,
                              facts=facts))
            continue
        share = bad / ads
        status = graded(share, warn, fail)
        said = (f"{bad:,} of {ads:,} {cohort} carry a location payload the contract cannot read "
                f"({share:.1%})")
        if status != "ok":
            said += _past(status, f"{warn:.0%}", f"{fail:.0%}")
            remedy = PAYLOAD_SHAPE_REMEDIES.get(portal)
            if remedy:
                said += f": {remedy}"
        cells.append(cell(f"{portal}/payload", status, said, value=round(share * 100, 2),
                          unit="%", portal=portal, facts=facts))
    return cells, {}


def _churn_cells(conn: Any, thresholds: dict[str, Any]) -> list[dict[str, Any]]:
    """Per portal: re-snapshots in 24 h per active ad, against its own 28-day median day."""
    active: dict[str, int] = {}
    by_day: dict[str, dict[int, int]] = {}
    for source, ads, days_back, resnaps in fetchall(conn, _CHURN_SQL, {"days": _CHURN_DAYS}):
        active[str(source)] = int(ads or 0)
        if days_back is not None:
            by_day.setdefault(str(source), {})[int(days_back)] = int(resnaps or 0)
    warn_x, fail_x = (float(thresholds["parsing_churn_warn_x"]),
                      float(thresholds["parsing_churn_fail_x"]))
    floor = int(thresholds["parsing_churn_min_snapshots"])
    cells = []
    for portal in sorted(SOURCES):
        subject, ads = f"{portal}/churn", active.get(portal, 0)
        if not ads:
            cells.append(cell(subject, "unknown", "Not measured: the portal has no active ad",
                              unit="×", portal=portal))
            continue
        days = by_day.get(portal, {})
        today = days.get(0, 0)
        median = statistics.median(days.get(k, 0) for k in range(1, _CHURN_DAYS + 1))
        multiple = today / median if median else None
        facts = {"resnaps_24h": today, "ads": ads, "ratio": round(today / ads, 4),
                 "median_resnaps_28d": median, "median_ratio_28d": round(median / ads, 4),
                 "multiple": None if multiple is None else round(multiple, 2)}
        said = (f"{today:,} re-snapshots of existing ads in 24 h ({today / ads:.4f} per active ad) "
                f"against a median day of {median:,g} ({median / ads:.4f}) over the last "
                f"{_CHURN_DAYS} days")
        if multiple is not None:
            said += f": {multiple:.2f}×"
        if today < floor:
            status = "ok"
            if today:
                said += f"; under the {floor:,} a storm needs"
        elif multiple is None:
            status = "fail"
            said += (f"; its median day wrote none (Problem past {fail_x:g}× at {floor:,} or "
                     "more): a hashed field may be re-snapshotting the stock")
        else:
            status = graded(multiple, warn_x, fail_x)
            if status != "ok":
                said += (f"{_past(status, f'{warn_x:g}×', f'{fail_x:g}×')}: a volatile hashed "
                         "field may be re-snapshotting the stock, or a heal's refetch wave ran")
        cells.append(cell(subject, status, said,
                          value=None if multiple is None else round(multiple, 2), unit="×",
                          portal=portal, facts=facts))
    return cells


def check_parsing(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Are newly scraped ads still read the way they were?"""
    at = now(conn)
    cells, unread = _field_cells(conn, thresholds, at)
    cells.append(_census_cell(thresholds, at))
    rows, why = _plausibility(conn)
    if why is None:
        cells += _measures_cells(rows, thresholds)
        cells += _median_cells(conn, rows, thresholds, at)
    else:
        unread.update(measures=why, median=why)
    payload, unread_payload = _payload_cells(conn, thresholds)
    cells += payload
    unread.update(unread_payload)
    cells += _churn_cells(conn, thresholds)
    return result(_PARSING, cells, unread)


# --- gauges (Q2) ---
#
# data_quality, location_town_coverage and text_extraction: standing conditions, every cell with
# its rolling 30-day trend (common.with_trends).

_DATA_QUALITY = "data_quality"
_LOCATION = "location_town_coverage"
_TEXT = "text_extraction"
_GAUGE_RANK = {"ok": 0, "warn": 1, "fail": 2}
# Land's headline area IS its plot (W17), so area against usable area says nothing there.
_LAND = "pozemek"


class _GaugeShare(NamedTuple):
    """One judged share: what it is of, its sentence, the share, its lower bound, the verdict."""

    key: str
    said: str
    share: float
    lower: float
    status: str


def _gauge_share(key: str, share: Any, n: Any, min_n: int,
                 said: Callable[[int, int, float, float], str], warn: float,
                 fail: float | None) -> _GaugeShare | None:
    """A share the view publishes beside its denominator, judged on its Wilson lower bound; None
    under min_n. The view gives the ratio, not the count, so the count is round(share x n)."""
    if share is None or n is None or int(n) < min_n:
        return None
    n, share = int(n), float(share)
    hits = round(share * n)
    low = wilson_lower(hits, n) or 0.0
    status = graded(low, warn, float("inf") if fail is None else fail)
    return _GaugeShare(key, said(hits, n, share, low), share, low, status)


def _gauge_share_cell(subject: str, portal: str | None, shares: list[_GaugeShare | None],
                      lines: tuple[float, float | None], remedy: str, unmeasured: str,
                      facts: dict[str, Any]) -> dict[str, Any]:
    """One cell over its judged shares: the worst decides and is the value, each non-OK share
    is a condition naming its line, and the remedy follows a non-OK message once."""
    judged_shares = [s for s in shares if s is not None]
    if not judged_shares:
        return cell(subject, "unknown", f"Not measured: {unmeasured}", unit="%", portal=portal,
                    facts=facts)
    worst = max(judged_shares, key=lambda s: (_GAUGE_RANK[s.status], s.lower))
    warn, fail = lines
    conditions = [(s.status, f"{s.said} ("
                   + (f"Problem above {fail:.0%})" if s.status == "fail" and fail is not None
                      else f"Watch above {warn:.0%})"))
                  for s in judged_shares if s.status != "ok"]
    facts = {**facts, "measured": len(judged_shares), "worst": worst.key,
             "share": round(worst.share, 4), "lower_bound": round(worst.lower, 4),
             "offenders": [s.key for s in judged_shares if s.status != "ok"]}
    ok = (worst.said if len(judged_shares) == 1
          else f"worst of {len(judged_shares)} measured: {worst.said}")
    out = judged(subject, conditions, ok, value=round(100 * worst.share, 2), unit="%",
                 portal=portal, facts=facts)
    if out["status"] != "ok":
        out["message"] += f" — {remedy}"
    return out


# --- data_quality (gauge): standing value-quality conditions ----------------------------------

# Active ads with no portal link, per portal; those first written in 7 d tell a new defect from
# an old gap. A count, never a share: a codebook miss moves a count within a day.
_URL_SQL = """
select source,
       count(*) as active_n,
       count(*) filter (where source_url is null) as no_url_n,
       count(*) filter (where source_url is null
                          and first_seen_at > now() - make_interval(days => 7)) as no_url_7d_n
  from listings
 where is_active
 group by source
"""


def _floor_cell(row: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    """The share of priced ads with an area whose per-m² price the basis floor NULLs: a unit
    price written as the price. The stock and the ads first written in 7 d, apart."""
    warn = float(thresholds["data_quality_floor_warn"])
    fail = float(thresholds["data_quality_floor_fail"])
    min_n = int(thresholds["data_quality_floor_min_n"])
    priced, priced_7d = int(row["n_floor_eligible"] or 0), int(row["n_floor_eligible_7d"] or 0)
    shares = [
        _gauge_share("stock", row["floor_null_share"], priced, min_n,
                     lambda h, n, p, lb: (f"{h:,} of {n:,} priced ads with an area lose their "
                                          f"per-m² price to the floor ({p:.1%}; lower bound "
                                          f"{lb:.1%})"), warn, fail),
        _gauge_share("7d", row["floor_null_share_7d"], priced_7d, min_n,
                     lambda h, n, p, lb: (f"{h:,} of {n:,} priced ads with an area first "
                                          f"written in 7 d lose their per-m² price to the floor "
                                          f"({p:.1%}; lower bound {lb:.1%})"), warn, fail)]
    return _gauge_share_cell(
        f"floor/{row['source']}/{_grain(row)}", row["source"], shares, (warn, fail),
        "a per-m² unit price is written as the price: check the portal's price parsing against "
        "scraper/price_text.is_per_area_price",
        f"{priced:,} priced ads with an area, {priced_7d:,} of them first written in 7 d: "
        f"under the {min_n} an arm needs",
        {"basis": row["price_per_m2_basis"], "priced": priced, "priced_7d": priced_7d})


def _divergence_cell(source: str, rows: list[dict[str, Any]],
                     thresholds: dict[str, Any]) -> dict[str, Any]:
    """Per portal, ads whose area and usable area are more than 10 % apart, per category and
    deal (land skipped), the stock and the ads first written in 7 d."""
    warn = float(thresholds["data_quality_divergence_warn"])
    fail = float(thresholds["data_quality_divergence_fail"])
    min_n = int(thresholds["data_quality_divergence_min_n"])
    shares = []
    for row in rows:
        grain = _grain(row)
        shares.append(_gauge_share(
            grain, row["area_divergence_share"], row["n_area_pairs"], min_n,
            lambda h, n, p, lb, g=grain: (f"{g}: {h:,} of {n:,} ads with both areas have them "
                                          f"more than 10% apart ({p:.1%}; lower bound "
                                          f"{lb:.1%})"), warn, fail))
        shares.append(_gauge_share(
            f"{grain}/7d", row["area_divergence_share_7d"], row["n_area_pairs_7d"], min_n,
            lambda h, n, p, lb, g=grain: (f"{g}: {h:,} of {n:,} ads with both areas first "
                                          f"written in 7 d have them more than 10% apart "
                                          f"({p:.1%}; lower bound {lb:.1%})"), warn, fail))
    return _gauge_share_cell(
        f"divergence/{source}", source, shares, (warn, fail),
        "the area column holds another area than the headline one (the plot): check the "
        "portal's parser against scraper/area.derive_headline_area",
        f"no category but land carries {min_n} ads with both areas", {})


def _estate_sum_cell(source: str, rows: list[dict[str, Any]],
                     thresholds: dict[str, Any]) -> dict[str, Any]:
    """Per portal, ads whose estate area IS area + usable area (within 1 %), a sum the page
    computed rather than a parcel, per category and deal (land skipped)."""
    warn = float(thresholds["data_quality_estate_sum_warn"])
    fail = float(thresholds["data_quality_estate_sum_fail"])
    min_n = int(thresholds["data_quality_estate_sum_min_n"])
    shares = []
    for row in rows:
        grain = _grain(row)
        shares.append(_gauge_share(
            grain, row["estate_sum_share"], row["n_estate_triples"], min_n,
            lambda h, n, p, lb, g=grain: (f"{g}: {h:,} of {n:,} ads with all three areas carry "
                                          f"their sum as the estate area ({p:.1%}; lower bound "
                                          f"{lb:.1%})"), warn, fail))
    return _gauge_share_cell(
        f"estate_sum/{source}", source, shares, (warn, fail),
        "the parser reads a total the page computed as the parcel: check its areas against the "
        "page's own parcel label",
        f"no category but land carries {min_n} ads with all three areas", {})


# The two per-portal families over the view's categories, and the builder of each.
_PER_PORTAL = (("divergence", _divergence_cell), ("estate_sum", _estate_sum_cell))


def _measure_gap_cell(row: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    """The stock's share of active ads with no price or no positive area, so no per-m² price: a
    standing condition, Watch only (a regression among new ads is the parsing alarm's)."""
    warn = float(thresholds["data_quality_measure_gap_warn"])
    min_n = int(thresholds["data_quality_measure_gap_min_n"])
    active = int(row["n_active"] or 0)
    share = _gauge_share(
        "stock", row["measure_input_gap_share"], active, min_n,
        lambda h, n, p, lb: (f"{h:,} of {n:,} active ads carry no price or no positive area, so "
                             f"no per-m² price ({p:.1%}; lower bound {lb:.1%})"), warn, None)
    return _gauge_share_cell(
        f"measure_gap/{row['source']}/{_grain(row)}", row["source"], [share], (warn, None),
        "a standing gap, never a Problem: check that the portal publishes a price and an area "
        "for this category",
        f"{active:,} active ads, under the {min_n} this arm needs",
        {"basis": row["price_per_m2_basis"], "active": active})


def _url_cell(source: str, counts: tuple[int, int, int],
              thresholds: dict[str, Any]) -> dict[str, Any]:
    """Per portal, the active ads with no portal link: a count, Problem past fail."""
    active, no_url, fresh = counts
    facts = {"active": active, "no_url": no_url, "no_url_7d": fresh}
    if not active:
        return cell(f"url/{source}", "unknown", "Not measured: no active ad", unit="ads",
                    portal=source, facts=facts)
    warn = int(thresholds["data_quality_url_warn"])
    fail = int(thresholds["data_quality_url_fail"])
    status = graded(no_url, warn, fail)
    said = (f"{no_url:,} of {active:,} active ads have no portal link ({fresh:,} of them first "
            "written in 7 d)")
    conditions = [] if status == "ok" else [(status, (
        f"{said} ({'Problem' if status == 'fail' else 'Watch'} above "
        f"{fail if status == 'fail' else warn}) — the parser stopped emitting the ad's link, or a "
        "link built from a codebook met a category the codebook lacks"))]
    ok = said if no_url else f"every one of {active:,} active ads has its portal link"
    return judged(f"url/{source}", conditions, ok, value=no_url, unit="ads", portal=source,
                  facts=facts)


def check_data_quality(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Which standing value-quality conditions does the active stock carry? The per-m² floor,
    area against usable area, estate sums, portal links and the stock's measure gap."""
    at = now(conn)
    rows, unread = _plausibility(conn)
    decidable = [r for r in rows if r.get("price_per_m2_basis") is not None]
    cells = [_floor_cell(r, thresholds) for r in decidable]
    for source in sorted(SOURCES):
        built = [r for r in rows if r["source"] == source and r.get("category_main") != _LAND]
        for family, build in _PER_PORTAL:
            cells.append(build(source, built, thresholds) if rows else
                         cell(f"{family}/{source}", "unknown", f"Not measured: {unread}",
                              unit="%", portal=source))
    counts = {s: (int(a or 0), int(n or 0), int(f or 0)) for s, a, n, f in fetchall(conn, _URL_SQL)}
    cells += [_url_cell(s, counts.get(s, (0, 0, 0)), thresholds) for s in sorted(SOURCES)]
    cells += [_measure_gap_cell(r, thresholds) for r in decidable]
    with_trends(conn, _DATA_QUALITY, cells, at)
    return result(_DATA_QUALITY, cells, {"measure view": unread} if unread else {})


# --- location_town_coverage (gauge): rule 25 over the active ads -----------------------------

_LOCATION_FLAGS = location_steps.step_flags_sql()

# The KÚ arm's one excuse (MF PR-B): an obec holding a KÚ the boundary loader recorded as
# `degenerate_boundary_geometry` at the current version has a hole no address point can be
# placed in, so its address-grain rows may lack a KÚ. `boundary_load_failed` is never one.
_DEGENERATE_KU_OBCE_SQL = """
    SELECT o.code
      FROM registry_load_discrepancies d
      JOIN ruian_admin_units k ON k.level = 'katastralni_uzemi' AND k.code = d.entity_code
                              AND k.valid_to IS NULL
      JOIN ruian_admin_units o ON o.id = k.parent_id
     WHERE d.entity_kind = 'katastralni_uzemi'
       AND d.discrepancy = 'degenerate_boundary_geometry'
       AND d.registry_version_id = (SELECT id FROM registry_versions WHERE is_current)"""

# Rule 25's invariant over ACTIVE ads first written more than NEW_AD_H hours ago (publishing's
# visible arm owns the younger ones): `town_missing_sql`, split into no answer row and a row that is
# not foreign with no town (undetermined and disputed count as Czech). `town_n` and
# `hidden_n` are the shared step flags; `hidden_n` (the consumer rule, negated) is a workload fact.
# The KÚ count reads rows WITH a town only: a town-less row is the town arm's, and `NULL NOT IN`
# would count it again whenever the degenerate set is empty. The inactive stock is not read:
# Location Quality owns it.
_LOCATION_TOWN_COVERAGE_SQL = f"""
    SELECT l.source,
           count(*)                                                   AS ads_n,
           count(*) FILTER (WHERE ll.listing_id IS NULL)              AS no_row_n,
           count(*) FILTER (WHERE ll.listing_id IS NOT NULL
                              AND {location_steps.town_missing_sql()}) AS cz_no_town_n,
           count(*) FILTER (WHERE {_LOCATION_FLAGS["has_town"]}) AS town_n,
           count(*) FILTER (WHERE NOT {_LOCATION_FLAGS["located"]})
                                                                      AS hidden_n,
           count(*) FILTER (WHERE ll.country_status = 'cz'
                              AND gr.is_address_grain
                              AND ll.resolver_version = %(resolver_version)s
                              AND ll.katastr_kod IS NULL
                              AND ll.obec_kod IS NOT NULL
                              AND ll.obec_kod NOT IN ({_DEGENERATE_KU_OBCE_SQL}))
                                                                      AS address_no_ku_n
      FROM listings l
      LEFT JOIN listing_location ll ON ll.listing_id = l.id
      LEFT JOIN location_granularity_rank gr ON gr.granularity = ll.granularity
     WHERE l.is_active
       AND l.first_seen_at < now() - make_interval(hours => %(new_ad_h)s::int)
     GROUP BY l.source
"""

# Every town above is a code of the CURRENT registry version, so a registry that stopped moving
# ages every answer silently; both of its failure shapes are invisible to the jobs check (a monthly
# run superseded by another pending run is cancelled, not failed; a load killed mid-way leaves an
# inert staged version). An older staged version a later vintage overtook is history, not a stall.
_REGISTRY_LIVENESS_SQL = """
    SELECT c.label AS current_label, current_date - c.source_date AS age_days,
           s.label AS staged_label,
           extract(epoch FROM now() - s.loaded_at) / 3600 AS staged_hours
      FROM registry_versions c
      LEFT JOIN LATERAL (
            SELECT n.label, n.loaded_at
              FROM registry_versions n
             WHERE NOT n.is_current AND n.source_date > c.source_date
             ORDER BY n.loaded_at
             LIMIT 1) s ON true
     WHERE c.is_current
"""


def _town_cell(source: str, counts: tuple[int, ...]) -> dict[str, Any]:
    """Per portal: Problem at one active ad past the edge with no town (red until zero), or one
    Czech address-grain ad with no KÚ; the value counts the ads with no town only."""
    ads, no_row, cz_no_town, town, hidden, no_ku = counts
    missing = no_row + cz_no_town
    facts = {"ads": ads, "no_row": no_row, "cz_no_town": cz_no_town, "town": town,
             "hidden": hidden, "address_no_ku": no_ku,
             "town_share": round(town / ads, 4) if ads else None}
    if not ads:
        return cell(f"{source}/town", "unknown", "Not measured: no active ad first written more "
                    f"than {NEW_AD_H} h ago", unit="ads", portal=source, facts=facts)
    cohort = f"active ads first written more than {NEW_AD_H} h ago"
    conditions = []
    if missing:
        conditions.append(("fail", f"{missing:,} of {ads:,} {cohort} have no town: {no_row:,} "
                                   f"with no location row, {cz_no_town:,} not foreign with no "
                                   "town (rule 25: red until zero)"))
    if no_ku:
        conditions.append(("fail", f"{no_ku:,} Czech ads at address grain have no cadastral area "
                                   f"(KÚ) at {RESOLVER_VERSION}: the registry's KÚ geometry is "
                                   "missing at its version"))
    return judged(f"{source}/town", conditions, f"every one of {ads:,} {cohort} has a town",
                  value=missing, unit="ads", portal=source, facts=facts)


def _registry_cell(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """The RÚIAN registry the towns are coded in: Problem with no current version, a current one
    past its age, or a newer one staged past its hours."""
    row = fetchone(conn, _REGISTRY_LIVENESS_SQL)
    if row is None:
        return cell("registry", "fail", "no current RÚIAN registry version: no town code is "
                    "current — run location_registry_load", unit="d", facts={"current": None})
    label, age_days, staged, staged_hours = row
    age = int(age_days)
    hours = None if staged_hours is None else round(float(staged_hours), 1)
    max_age = int(thresholds["location_registry_max_age_days"])
    max_staged = float(thresholds["location_registry_max_staged_hours"])
    conditions = []
    if age > max_age:
        conditions.append(("fail", f"the current registry {label} is {age} d old (Problem past "
                                   f"{max_age} d): the monthly load has not landed — re-dispatch "
                                   "location_registry_load"))
    if hours is not None and hours > max_staged:
        conditions.append(("fail", f"{staged} has sat staged {hours:.0f} h without becoming "
                                   f"current (Problem past {max_staged:g} h): a superseded run is "
                                   "cancelled, not failed — re-dispatch location_registry_load"))
    ok = (f"the current registry {label} is {age} d old"
          + (f"; {staged} staged {hours:.0f} h ago" if staged else ", nothing staged"))
    return judged("registry", conditions, ok, value=age, unit="d",
                  facts={"current": label, "age_days": age, "staged": staged,
                         "staged_hours": hours})


def check_location_town_coverage(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Has every active Czech ad first written more than 24 h ago a town (rule 25), and is the
    RÚIAN registry its town is coded in current? A cell per portal and one for the registry."""
    at = now(conn)
    read = {row[0]: tuple(int(x or 0) for x in row[1:])
            for row in fetchall(conn, _LOCATION_TOWN_COVERAGE_SQL,
                                {"resolver_version": RESOLVER_VERSION, "new_ad_h": NEW_AD_H})}
    cells = [_town_cell(s, read.get(s, (0, 0, 0, 0, 0, 0))) for s in sorted(SOURCES)]
    cells.append(_registry_cell(conn, thresholds))
    with_trends(conn, _LOCATION, cells, at)
    return result(_LOCATION, cells, {})


# --- text_extraction (gauge): are new ads' descriptions read promptly ------------------------

# The flow's arrivals: first written 1-24 h ago, an hour past the lane's 20-min SLO and its
# 5-min passes, so a burst of minutes-old ads does not count unread before the lane could read it.
_FLOW_WINDOW_H = (1, NEW_AD_H)


def _flow_cell(source: str, row: tuple[Any, ...] | None,
               thresholds: dict[str, Any]) -> dict[str, Any]:
    """Per portal in scope, new ads with a description and no reading yet, on the lower bound
    over at least `text_extraction_flow_min_n` of them (else Not measured)."""
    arrived, read, given_up, p95 = row or (0, 0, 0, None)
    arrived, read, given_up = int(arrived or 0), int(read or 0), int(given_up or 0)
    lo, hi = _FLOW_WINDOW_H
    unread = arrived - read
    facts: dict[str, Any] = {
        "state": "on", "arrived": arrived, "read": read, "unread": unread, "given_up": given_up,
        "p95_minutes": None if p95 is None else round(float(p95), 1),
        "slo_minutes": text_lane.SLO_MINUTES}
    if not arrived:
        return cell(f"flow/{source}", "unknown", "Not measured: no active ad with a description "
                    f"first written {lo}-{hi} h ago", unit="%", portal=source, facts=facts)
    min_n = int(thresholds["text_extraction_flow_min_n"])
    if arrived < min_n:
        return cell(f"flow/{source}", "unknown", f"Not measured: {arrived:,} active ads with a "
                    f"description first written {lo}-{hi} h ago, under the {min_n} this arm "
                    "needs", unit="%", portal=source, facts=facts)
    low = wilson_lower(unread, arrived) or 0.0
    facts["lower_bound"] = round(low, 4)
    said = (f"{unread:,} of {arrived:,} ads with a description first written {lo}-{hi} h ago "
            f"have no reading yet ({unread / arrived:.1%}; lower bound {low:.1%})")
    if given_up:
        said += (f", {given_up:,} given up after {text_lane.GIVE_UP_AFTER} failed readings at "
                 "this version")
    if p95 is not None:
        said += (f"; p95 {float(p95):.0f} min to the first reading (the lane's SLO is "
                 f"{text_lane.SLO_MINUTES} min)")
    warn = float(thresholds["text_extraction_flow_warn"])
    fail = float(thresholds["text_extraction_flow_fail"])
    status = graded(low, warn, fail)
    conditions = [] if status == "ok" else [(status, f"{said} ("
                                             + (f"Problem above {fail:.0%})" if status == "fail"
                                                else f"Watch above {warn:.0%})"))]
    return judged(f"flow/{source}", conditions, said, value=round(100 * unread / arrived, 2),
                  unit="%", portal=source, facts=facts)


def _stock_cell(row: tuple[Any, ...] | None, model: str, version: str,
                thresholds: dict[str, Any]) -> dict[str, Any]:
    """Every ad the lane holds waiting and the days to drain it at the last 24 h's pace (a lower
    bound): Problem when nothing was read in 24 h while active ads wait, Watch past the ETA."""
    waiting, active, readings = (int(x or 0) for x in (row or (0, 0, 0)))
    eta = waiting / readings if readings else (0.0 if not waiting else None)
    facts = {"state": "on", "model": model, "extractor_version": version, "waiting": waiting,
             "waiting_active": active, "waiting_inactive": waiting - active,
             "readings_24h": readings, "eta_d": None if eta is None else round(eta, 2)}
    if waiting:
        said = (f"{waiting:,} ads wait for a description reading ({active:,} active, "
                f"{waiting - active:,} inactive: the archive's read-once re-read); {readings:,} "
                "read in 24 h" + ("" if eta is None else f", done in at least {eta:.1f} d"))
    else:
        said = f"no ad waits for a description reading; {readings:,} read in 24 h"
    warn_d = float(thresholds["text_extraction_eta_warn_d"])
    conditions = []
    if active and not readings:
        conditions.append(("fail", f"{said} — nothing read in 24 h while active ads wait: the "
                                   "lane has stopped (jobs' lane/text_extract shows its passes, "
                                   "llm a provider refusal)"))
    elif waiting and (eta is None or eta > warn_d):
        conditions.append(("warn", f"{said} (Watch past {warn_d:g} d"
                                   + ("; nothing read in 24 h" if eta is None else "")
                                   + "; a model or prompt change re-opens the corpus)"))
    return judged("stock", conditions, said, value=None if eta is None else round(eta, 1),
                  unit="d", facts=facts)


def check_text_extraction(conn: Any, thresholds: dict[str, Any]) -> dict[str, Any]:
    """Are new ads' descriptions read promptly (a flow cell per portal in the lane's scope), and
    when will what waits be read (the stock)? Model and scope are the lane's own reads."""
    at = now(conn)
    model = text_lane.model_from(fetchone(conn, text_lane.MODEL_SQL, (text_lane.MODEL_SETTING,)))
    scope = sorted(text_lane.scope_from(
        row[0] for row in fetchall(conn, text_lane.LLM_TEXT_SOURCES_SQL)))
    if model is None or not scope:
        why = ("no portal is in its scope (no open gate, no llm_text contract entry)" if not scope
               else f"app_settings.{text_lane.MODEL_SETTING} names no model")
        cells = [cell(f"flow/{p}", "ok", f"the description lane is off: {why}", unit="%",
                      portal=p, facts={"state": "off"}) for p in scope]
        cells.append(cell("stock", "ok", f"the description lane is off: {why}; nothing is read "
                          "and nothing waits", unit="d", facts={"state": "off"}))
    else:
        version = text_lane.extractor_version(model)
        lo, hi = _FLOW_WINDOW_H
        flows = {row[0]: row[1:] for row in fetchall(conn, text_lane.EXTRACTION_LATENCY_SQL, {
            "version": version, "window_h": hi, "grace_h": lo})}
        cells = [_flow_cell(p, flows.get(p), thresholds) for p in scope]
        cells.append(_stock_cell(fetchone(conn, text_lane.ELIGIBLE_LAG_SQL, {"version": version}),
                                 model, version, thresholds))
    with_trends(conn, _TEXT, cells, at)
    return result(_TEXT, cells, {})

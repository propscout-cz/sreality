"""Pure mapping for the sreality `estate_prices` JSON.

No HTTP, no DB — just dict-in / dict-out, so it unit-tests offline. Two jobs:
build the `estate_prices` query params from a dataset filter spec, and fold the
monthly response arrays into per-month observation rows.
"""

from __future__ import annotations

from typing import Any


class PriceStatsParseError(Exception):
    """The estate_prices response wasn't the shape we expect."""


def build_estate_prices_params(
    dataset: dict[str, Any],
    *,
    entity_id: int,
    entity_type: str,
    category_type_cb: int,
    default_from: str,
    default_to: str,
) -> dict[str, Any]:
    """Query params for one estate_prices call.

    Only non-null filters are included — the API treats an absent param as "no
    filter", so omitting them widens the cohort (the safe default for sparse
    localities). `default_from`/`default_to` are `YYYY-MM` strings.
    """
    params: dict[str, Any] = {
        "category_main_cb": int(dataset["category_main_cb"]),
        "category_type_cb": int(category_type_cb),
        "entity_id": int(entity_id),
        "entity_type": entity_type,
        "distance": int(dataset.get("distance") or 0),
        "default_from": default_from,
        "default_to": default_to,
    }
    # building_condition / building_type / ownership are string codes.
    for field in ("building_condition", "building_type", "ownership"):
        val = dataset.get(field)
        if val not in (None, ""):
            params[field] = str(val)
    for field in ("usable_area_from", "usable_area_to"):
        val = dataset.get(field)
        if val not in (None, ""):
            params[field] = int(val)
    return params


def parse_estate_prices(payload: dict[str, Any]) -> dict[str, Any]:
    """Fold an estate_prices response into per-month rows + scalar aggregates.

    Returns ``{"months": [{year, month, price, active_count, new_count,
    deleted_count}], "aggregates": {advert_count, new_advert_count,
    avg_price_per_area, avg_published_days_overall, avg_views_per_day},
    "previous_range": ...}``. A month present in only one of the two source
    arrays still yields a row (the other metrics are None).
    """
    if not isinstance(payload, dict) or "result" not in payload:
        raise PriceStatsParseError("missing 'result' key")
    result = payload["result"] or {}

    by_key: dict[tuple[int, int], dict[str, Any]] = {}

    def slot(year: Any, month: Any) -> dict[str, Any]:
        key = (int(year), int(month))
        row = by_key.get(key)
        if row is None:
            row = {
                "year": key[0], "month": key[1], "price": None,
                "active_count": None, "new_count": None, "deleted_count": None,
            }
            by_key[key] = row
        return row

    for pt in result.get("dev_price_by_month") or []:
        if pt.get("year") is None or pt.get("month") is None:
            continue
        slot(pt["year"], pt["month"])["price"] = pt.get("price")
    for pt in result.get("dev_count_advert_by_month") or []:
        if pt.get("year") is None or pt.get("month") is None:
            continue
        row = slot(pt["year"], pt["month"])
        row["active_count"] = pt.get("active")
        row["new_count"] = pt.get("new")
        row["deleted_count"] = pt.get("deleted")

    months = [by_key[k] for k in sorted(by_key)]
    aggregates = {
        "advert_count": result.get("advert_count"),
        "new_advert_count": result.get("new_advert_count"),
        "avg_price_per_area": result.get("avg_price_per_area"),
        "avg_published_days_overall": result.get("avg_published_days_overall"),
        "avg_views_per_day": result.get("avg_views_per_day"),
    }
    return {
        "months": months,
        "aggregates": aggregates,
        "previous_range": result.get("previous_range"),
    }

"""The Browse > Stats dashboard's vocabulary — dimensions, measures, bands, the chart spec.

ONE definition (operator ruling 2026-10-19; design: docs/design/stats-dashboard.md). Three faces
read it and none restates it by hand:

- migrations/626_stats_dashboard.sql carries `dim_key_function_sql()`, `measure_value_sql()`,
  `spec_guard_sql()` and the two id lists (`single_status_measures_sql()`, `geo_ids_sql()`) byte
  for byte; tests/test_626_stats_dashboard.py pins the effective migration to this module.
- scripts/generate_filter_registry.py emits frontend/src/lib/statsRegistry.generated.ts from
  `registry_json()` (labels, order, availability, band steps, limits); CI `--check`s the drift.
- api/stats_dashboard.py validates every saved board through `validate_board`.

A dimension KEY is the raw value the SQL groups on: the canonical enum value, `ano`/`ne` for a
boolean, the RÚIAN code (as text) for a place, the band's lower bound as an integer string. Labels and
order live here, not in SQL, so the database never holds a second vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from toolkit import filter_registry as fr
from toolkit.measures import RENT_MONTHLY_CZK_M2

DimKind = Literal["enum", "band", "geo", "bool"]
MeasureUnit = Literal["days", "m2", "price", "ppm2"]

# Limits — the only numbers a chart cannot exceed (operator rulings 6, 7 of 2026-10-19).
MIN_N = 10          # a cell with fewer properties shows n and no statistics
MAX_ROWS = 6        # nested row levels
MAX_COLS = 1        # one column dimension (series)
MAX_CELLS = 2000    # above this the RPC refuses rather than ship a payload nobody can read
MAX_CHARTS = 50     # charts on one saved board (PUT /stats/dashboard); a size guard, not a ruling

STATS: tuple[str, ...] = ("n", "mean", "median", "p10", "p25", "p75", "p90")
CHART_STATS: tuple[str, ...] = ("mean", "median", "p10", "p25", "p75", "p90")  # one per line/bar
DEFAULT_STAT = "median"
VIEWS: tuple[str, ...] = ("table", "line", "bar", "box")
# The pivot RPC's refusals and the cohort status facts (migration 626), in the one place that names them.
REFUSALS: tuple[str, ...] = ("empty", "mixed_category", "mixed_status", "too_many_cells", "bad_spec")
STATUSES: tuple[str, ...] = ("active", "inactive", "mixed")


@dataclass(frozen=True)
class ViewLimits:
    min_rows: int
    max_rows: int
    max_cols: int


# Ruling 7: table = rows x one column; line / bar = ONE row dimension on x, the column as
# series; box = rows only. validate_chart enforces it and registry_json ships it to the SPA.
VIEW_LIMITS: dict[str, ViewLimits] = {
    "table": ViewLimits(0, MAX_ROWS, MAX_COLS),
    "line": ViewLimits(1, 1, MAX_COLS),
    "bar": ViewLimits(1, 1, MAX_COLS),
    "box": ViewLimits(0, MAX_ROWS, 0),
}

RENT_BASIS = RENT_MONTHLY_CZK_M2  # migration 425's token (rule 23); every other basis is a capital sum

BOOL_OPTIONS: tuple[fr.EnumOption, ...] = (
    fr.EnumOption("ano", "Ano", "Yes"),
    fr.EnumOption("ne", "Ne", "No"),
)
# The filter registry's furnished vocabulary minus its `__unknown__` filter sentinel: a chart
# cell is a stated value, and an unstated one is excluded (ruling 8), never a bucket.
FURNISHED_OPTIONS: tuple[fr.EnumOption, ...] = tuple(
    o for o in fr.FURNISHED_OPTIONS if o.value in fr.FURNISHED_CANONICAL
)


@dataclass(frozen=True)
class Dimension:
    id: str
    label_cs: str
    label_en: str
    kind: DimKind
    sql: str                                   # over the stats_dim_key argument names
    options: tuple[fr.EnumOption, ...] = ()    # enum / bool: the ordered vocabulary
    categories: tuple[str, ...] | None = None  # None = every category_main
    steps: dict[str, int] | None = None        # band: {sale, rent} or {"all": n}
    measure: str | None = None                 # band: the measure whose values it bands (its unit, its label)
    group: str = "Vlastnost"                   # the palette group, in registry order


def _band(expr: str, step_sql: str) -> str:
    return f"case when {expr} is null then null else (floor({expr} / {step_sql}) * {step_sql})::text end"


def _band_dim(
    dim_id: str, label_cs: str, label_en: str, column: str, steps: dict[str, int], measure: str,
    categories: tuple[str, ...] | None = None, group: str = "Cena",
) -> Dimension:
    """A band keyed on its lower bound: one step for every basis ({all}) or one per basis ({sale, rent})."""
    if set(steps) == {"all"}:
        step = str(steps["all"])
    else:
        step = f"(case when basis = '{RENT_BASIS}' then {steps['rent']} else {steps['sale']} end)"
    sql = _band(column, step)
    if categories:
        guard = " or ".join(f"category_main = '{c}'" for c in categories)
        sql = f"case when {guard} then {sql} end"
    return Dimension(dim_id, label_cs, label_en, "band", sql, categories=categories, steps=steps,
                     measure=measure, group=group)


def _bool_dim(dim_id: str, label_cs: str, label_en: str) -> Dimension:
    """A stated yes / no over the stats_dim_key argument of the same name; NULL stays NULL (ruling 8)."""
    return Dimension(dim_id, label_cs, label_en, "bool",
                     f"case {dim_id} when true then 'ano' when false then 'ne' end", BOOL_OPTIONS,
                     group="Vybavení")


# Band steps are ruling 5; `sale` covers every capital basis (sale + land).
DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("disposition", "Dispozice", "Layout", "enum", "disposition", fr.DISPOSITION_OPTIONS),
    _band_dim("area_band_5", "Plocha po 5 m²", "Floor area, 5 m² bands", "area_m2", {"all": 5}, "area",
              categories=("byt",), group="Vlastnost"),
    _band_dim("area_band_10", "Plocha po 10 m²", "Floor area, 10 m² bands", "area_m2", {"all": 10}, "area",
              categories=("byt",), group="Vlastnost"),
    Dimension("condition", "Stav", "Condition", "enum", "condition", fr.CONDITION_OPTIONS),
    _band_dim("ppm2_band_s", "Cena za m² po 5 tis. (nájem 25)", "Price per m², 5k bands (rent 25)",
              "price_per_m2", {"sale": 5_000, "rent": 25}, "ppm2"),
    _band_dim("ppm2_band_l", "Cena za m² po 10 tis. (nájem 50)", "Price per m², 10k bands (rent 50)",
              "price_per_m2", {"sale": 10_000, "rent": 50}, "ppm2"),
    _band_dim("price_band_s", "Cena po 250 tis. (nájem 1 tis.)", "Price, 250k bands (rent 1k)",
              "price_czk", {"sale": 250_000, "rent": 1_000}, "price"),
    _band_dim("price_band_m", "Cena po 500 tis. (nájem 2,5 tis.)", "Price, 500k bands (rent 2.5k)",
              "price_czk", {"sale": 500_000, "rent": 2_500}, "price"),
    _band_dim("price_band_l", "Cena po 1 mil. (nájem 5 tis.)", "Price, 1M bands (rent 5k)",
              "price_czk", {"sale": 1_000_000, "rent": 5_000}, "price"),
    # A place keys on its RUIAN CODE (rule 25: one code predicate), never its name: two towns or
    # streets that share a name stay two cells. The level is the dimension id, which is also the
    # `level` of public.ruian_place_names_public (migration 626), where the payload's `places`
    # list resolves each key to its name.
    Dimension("kraj", "Kraj", "Region", "geo", "region_id::text", group="Místo"),
    Dimension("okres", "Okres", "District", "geo", "okres_id::text", group="Místo"),
    Dimension("obec", "Město", "Town", "geo", "obec_id::text", group="Místo"),
    Dimension("cast_obce", "Městská čtvrť", "Quarter", "geo", "cast_obce_id::text", group="Místo"),
    Dimension("ulice", "Ulice", "Street", "geo", "ulice_id::text", group="Místo"),
    Dimension("building_type", "Materiál", "Material", "enum", "building_type", fr.BUILDING_TYPE_OPTIONS),
    Dimension("furnished", "Vybavení", "Furnished", "enum", "furnished", FURNISHED_OPTIONS, group="Vybavení"),
    _bool_dim("has_balcony", "Balkon", "Balcony"),
    _bool_dim("has_lift", "Výtah", "Lift"),
    _bool_dim("has_parking", "Parkování", "Parking"),
    _bool_dim("terrace", "Terasa", "Terrace"),
    _bool_dim("cellar", "Sklep", "Cellar"),
    _bool_dim("garage", "Garáž", "Garage"),
)
DIMENSION_BY_ID: dict[str, Dimension] = {d.id: d for d in DIMENSIONS}
GEO_IDS: frozenset[str] = frozenset(d.id for d in DIMENSIONS if d.kind == "geo")


@dataclass(frozen=True)
class Measure:
    id: str
    label_cs: str
    label_en: str
    unit: MeasureUnit
    sql: str                   # over browse_list's columns, alias `l`
    single_status: bool = False  # ruling 3: turnover needs one status in the sample


MEASURES: tuple[Measure, ...] = (
    Measure("tom_days", "Doba na trhu", "Time on market", "days", "l.tom_days::numeric", single_status=True),
    # area_m2 is already polymorphic (rule 23: the PLOT area of a pozemek, floor area otherwise).
    Measure("area", "Plocha", "Area", "m2", "l.area_m2::numeric"),
    Measure("price", "Cena", "Price", "price", "l.price_czk::numeric"),
    Measure("ppm2", "Cena za m²", "Price per m²", "ppm2", "l.price_per_m2"),
)
MEASURE_BY_ID: dict[str, Measure] = {m.id: m for m in MEASURES}


# --------------------------------------------------------------------------- SQL faces

# The scalar arguments of public.stats_dim_key, in signature order. Every dimension's `sql`
# is written over these names; a dimension that needs a new column adds it HERE first.
DIM_KEY_ARGS: tuple[tuple[str, str], ...] = (
    ("dim", "text"), ("category_main", "text"), ("basis", "text"),
    ("disposition", "text"), ("condition", "text"),
    ("area_m2", "numeric"), ("price_czk", "integer"), ("price_per_m2", "numeric"),
    ("building_type", "text"), ("furnished", "text"),
    ("has_balcony", "boolean"), ("has_lift", "boolean"), ("has_parking", "boolean"),
    ("terrace", "boolean"), ("cellar", "boolean"), ("garage", "boolean"),
    ("region_id", "bigint"), ("okres_id", "bigint"), ("obec_id", "bigint"),
    ("cast_obce_id", "bigint"), ("ulice_id", "bigint"),
)
DIM_KEY_SIGNATURE = ", ".join(t for _, t in DIM_KEY_ARGS)


def dim_key_function_sql() -> str:
    """The CREATE statement of public.stats_dim_key — one CASE arm per dimension, NULL otherwise.

    SQL-language and IMMUTABLE so the planner inlines each of the seven calls into the pivot's
    plan (a plpgsql body would run once per row per slot)."""
    args = ",\n  ".join(f"{n} {t}" for n, t in DIM_KEY_ARGS)
    arms = "\n".join(f"    when '{d.id}' then {d.sql}" for d in DIMENSIONS)
    return (
        "create or replace function public.stats_dim_key(\n"
        f"  {args}\n"
        ")\nreturns text\nlanguage sql\nimmutable\nparallel safe\nas $function$\n"
        "  select case dim\n"
        f"{arms}\n"
        "  end\n"
        "$function$;"
    )


def measure_value_sql(var: str = "v_measure") -> str:
    """The per-row measure value, a CASE on the plpgsql variable holding the spec's measure."""
    arms = " ".join(f"when '{m.id}' then {m.sql}" for m in MEASURES)
    return f"case {var} {arms} end"


def _sql_ids(ids: Iterable[str]) -> str:
    return ", ".join(f"'{i}'" for i in ids)


def single_status_measures_sql() -> str:
    """The `mixed_status` refusal's measure list (ruling 3)."""
    return _sql_ids(m.id for m in MEASURES if m.single_status)


def geo_ids_sql() -> str:
    """The place dimensions whose keys the payload's `places` names."""
    return _sql_ids(d.id for d in DIMENSIONS if d.kind == "geo")


def spec_guard_sql() -> str:
    """The pivot's `bad_spec` guard: validate_spec's rules over its plpgsql variables, one reason each."""
    dims = "unnest(v_rows || v_cols) d"
    return (
        "  if spec is null then v_bad := 'spec missing';\n"
        "  elsif jsonb_typeof(spec) <> 'object' then v_bad := 'spec must be an object';\n"
        "  elsif (spec ? 'rows' and jsonb_typeof(spec->'rows') <> 'array')\n"
        "     or (spec ? 'cols' and jsonb_typeof(spec->'cols') <> 'array') then v_bad := 'rows and cols must be arrays';\n"
        f"  elsif coalesce(array_length(v_rows, 1), 0) > {MAX_ROWS} then v_bad := 'at most {MAX_ROWS} row dimensions';\n"
        f"  elsif coalesce(array_length(v_cols, 1), 0) > {MAX_COLS} then v_bad := 'at most {MAX_COLS} column dimension';\n"
        f"  elsif v_measure is null or v_measure not in ({_sql_ids(m.id for m in MEASURES)}) then v_bad := 'unknown measure';\n"
        f"  elsif exists (select 1 from {dims} where d not in ({_sql_ids(d.id for d in DIMENSIONS)})) then v_bad := 'unknown dimension';\n"
        f"  elsif (select count(*) from {dims}) <> (select count(distinct d) from {dims}) then v_bad := 'a dimension may appear once';\n"
        "  end if;"
    )


# --------------------------------------------------------------------------- validation

class SpecError(ValueError):
    """A chart spec the registry refuses; the message is the reason, in English."""


def validate_spec(spec: Any) -> dict[str, Any]:
    """The pivot spec the RPC takes: {rows: [dim...], cols: [dim] | [], measure: id}."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    rows = spec.get("rows", [])
    cols = spec.get("cols", [])
    measure = spec.get("measure")
    if not isinstance(rows, list) or not isinstance(cols, list):
        raise SpecError("rows and cols must be arrays")
    if len(rows) > MAX_ROWS:
        raise SpecError(f"at most {MAX_ROWS} row dimensions")
    if len(cols) > MAX_COLS:
        raise SpecError(f"at most {MAX_COLS} column dimension")
    dims = [*rows, *cols]
    if not all(isinstance(d, str) for d in dims):
        raise SpecError("dimensions must be strings")
    for d in dims:
        if d not in DIMENSION_BY_ID:
            raise SpecError(f"unknown dimension {d!r}")
    if len(set(dims)) != len(dims):
        raise SpecError("a dimension may appear once")
    if not isinstance(measure, str) or measure not in MEASURE_BY_ID:
        raise SpecError(f"unknown measure {measure!r}")
    return {"rows": list(rows), "cols": list(cols), "measure": measure}


def validate_chart(chart: Any) -> dict[str, Any]:
    """One saved chart: {id, view, rows, cols, measure, stats}. `stats` (one or more chart statistics,
    in the order the user picked them: the first leads) is read by line, bar and a table with a column."""
    if not isinstance(chart, dict):
        raise SpecError("chart must be an object")
    cid = chart.get("id")
    if not isinstance(cid, str) or not (1 <= len(cid) <= 64):
        raise SpecError("chart id must be a short string")
    view = chart.get("view")
    if view not in VIEWS:
        raise SpecError(f"unknown view {view!r}")
    stats = chart.get("stats", [DEFAULT_STAT])
    if not isinstance(stats, list) or not stats:
        raise SpecError("stats must be a non-empty list")
    for st in stats:
        if st not in CHART_STATS:
            raise SpecError(f"unknown stat {st!r}")
    if len(set(stats)) != len(stats):
        raise SpecError("a statistic may appear once")
    spec = validate_spec(chart)
    limits = VIEW_LIMITS[view]
    if len(spec["cols"]) > limits.max_cols:
        raise SpecError(f"a {view} chart takes row dimensions only")
    if not limits.min_rows <= len(spec["rows"]) <= limits.max_rows:
        span = (f"exactly {limits.min_rows}" if limits.min_rows == limits.max_rows
                else f"{limits.min_rows} to {limits.max_rows}")
        raise SpecError(f"a {view} chart takes {span} row dimension{'' if limits.max_rows == 1 else 's'}")
    return {"id": cid, "view": view, "stats": list(stats), **spec}


def validate_board(charts: list[Any]) -> list[dict[str, Any]]:
    """A saved board: at most MAX_CHARTS charts, each valid, ids unique; a refusal names the chart."""
    if len(charts) > MAX_CHARTS:
        raise SpecError(f"at most {MAX_CHARTS} charts on a board")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, chart in enumerate(charts):
        try:
            valid = validate_chart(chart)
        except SpecError as exc:
            raise SpecError(f"chart {i}: {exc}") from exc
        if valid["id"] in seen:
            raise SpecError(f"chart {i}: duplicate id {valid['id']!r}")
        seen.add(valid["id"])
        out.append(valid)
    return out


def registry_json() -> dict[str, Any]:
    """What the generated TypeScript (and any client) needs to build and label a chart."""
    return {
        "min_n": MIN_N, "max_cells": MAX_CELLS,
        "max_charts": MAX_CHARTS,
        "chart_stats": list(CHART_STATS), "default_stat": DEFAULT_STAT, "views": list(VIEWS),
        "refusals": list(REFUSALS), "statuses": list(STATUSES),
        "view_limits": {
            v: {"min_rows": lim.min_rows, "max_rows": lim.max_rows, "max_cols": lim.max_cols}
            for v, lim in VIEW_LIMITS.items()
        },
        "dimensions": [
            {
                "id": d.id, "label_cs": d.label_cs, "label_en": d.label_en, "kind": d.kind,
                "categories": list(d.categories) if d.categories else None,
                "options": [fr.enum_to_json(o) for o in d.options] if d.options else None,
                "steps": d.steps, "measure": d.measure, "group": d.group,
            }
            for d in DIMENSIONS
        ],
        "measures": [
            {"id": m.id, "label_cs": m.label_cs, "label_en": m.label_en, "unit": m.unit,
             "single_status": m.single_status}
            for m in MEASURES
        ],
    }

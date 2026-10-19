"""The Browse > Stats dashboard's vocabulary — dimensions, measures, bands, the chart spec.

ONE definition (operator ruling 2026-10-19; design: docs/design/stats-dashboard.md). Three faces
read it and none restates it by hand:

- migrations/626_stats_dashboard.sql carries `dim_key_function_sql()` and `measure_value_sql()`
  byte for byte; tests/test_626_stats_dashboard.py pins the effective migration to this module.
- scripts/generate_filter_registry.py emits frontend/src/lib/statsRegistry.generated.ts from
  `registry_json()` (labels, order, availability, band steps, limits); CI `--check`s the drift.
- api/stats_dashboard.py validates every saved chart through `validate_chart`.

A dimension KEY is the raw value the SQL groups on: the canonical enum value, `ano`/`ne` for a
boolean, the RÚIAN code (as text) for a place, the band's lower bound as an integer string. Labels and
order live here, not in SQL, so the database never holds a second vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from toolkit import filter_registry as fr

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
VIEWS: tuple[str, ...] = ("table", "line", "bar", "box")


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

RENT_BASIS = "rent_monthly_czk_m2"  # migration 425's token; every other basis is a capital sum

# Band steps per basis (ruling 5). `sale` covers every capital basis (sale + land).
PPM2_STEPS: dict[str, dict[str, int]] = {
    "ppm2_band_s": {"sale": 5_000, "rent": 25},
    "ppm2_band_l": {"sale": 10_000, "rent": 50},
}
PRICE_STEPS: dict[str, dict[str, int]] = {
    "price_band_s": {"sale": 250_000, "rent": 1_000},
    "price_band_m": {"sale": 500_000, "rent": 2_500},
    "price_band_l": {"sale": 1_000_000, "rent": 5_000},
}
AREA_STEPS: dict[str, int] = {"area_band_5": 5, "area_band_10": 10}

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
    unit: MeasureUnit | None = None            # band: which unit the key is in


def _band(expr: str, step_sql: str) -> str:
    return f"case when {expr} is null then null else (floor({expr} / {step_sql}) * {step_sql})::text end"


def _basis_step(steps: dict[str, int]) -> str:
    return f"case when basis = '{RENT_BASIS}' then {steps['rent']} else {steps['sale']} end"


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("disposition", "Dispozice", "Layout", "enum", "disposition", fr.DISPOSITION_OPTIONS),
    Dimension("area_band_5", "Plocha po 5 m²", "Floor area, 5 m² bands", "band",
              f"case when category_main = 'byt' then {_band('area_m2', '5')} end",
              categories=("byt",), steps={"all": 5}, unit="m2"),
    Dimension("area_band_10", "Plocha po 10 m²", "Floor area, 10 m² bands", "band",
              f"case when category_main = 'byt' then {_band('area_m2', '10')} end",
              categories=("byt",), steps={"all": 10}, unit="m2"),
    Dimension("condition", "Stav", "Condition", "enum", "condition", fr.CONDITION_OPTIONS),
    Dimension("ppm2_band_s", "Cena za m² po 5 tis. (nájem 25)", "Price per m², 5k bands (rent 25)", "band",
              _band("price_per_m2", f"({_basis_step(PPM2_STEPS['ppm2_band_s'])})"),
              steps=PPM2_STEPS["ppm2_band_s"], unit="ppm2"),
    Dimension("ppm2_band_l", "Cena za m² po 10 tis. (nájem 50)", "Price per m², 10k bands (rent 50)", "band",
              _band("price_per_m2", f"({_basis_step(PPM2_STEPS['ppm2_band_l'])})"),
              steps=PPM2_STEPS["ppm2_band_l"], unit="ppm2"),
    Dimension("price_band_s", "Cena po 250 tis. (nájem 1 tis.)", "Price, 250k bands (rent 1k)", "band",
              _band("price_czk", f"({_basis_step(PRICE_STEPS['price_band_s'])})"),
              steps=PRICE_STEPS["price_band_s"], unit="price"),
    Dimension("price_band_m", "Cena po 500 tis. (nájem 2,5 tis.)", "Price, 500k bands (rent 2.5k)", "band",
              _band("price_czk", f"({_basis_step(PRICE_STEPS['price_band_m'])})"),
              steps=PRICE_STEPS["price_band_m"], unit="price"),
    Dimension("price_band_l", "Cena po 1 mil. (nájem 5 tis.)", "Price, 1M bands (rent 5k)", "band",
              _band("price_czk", f"({_basis_step(PRICE_STEPS['price_band_l'])})"),
              steps=PRICE_STEPS["price_band_l"], unit="price"),
    # A place keys on its RUIAN CODE (rule 25: one code predicate), never its name: two towns or
    # streets that share a name stay two cells. The level is the dimension id, which is also the
    # `level` of public.ruian_place_names_public (migration 626), where the payload's `places`
    # list resolves each key to its name.
    Dimension("kraj", "Kraj", "Region", "geo", "region_id::text"),
    Dimension("okres", "Okres", "District", "geo", "okres_id::text"),
    Dimension("obec", "Město", "Town", "geo", "obec_id::text"),
    Dimension("cast_obce", "Městská čtvrť", "Quarter", "geo", "cast_obce_id::text"),
    Dimension("ulice", "Ulice", "Street", "geo", "ulice_id::text"),
    Dimension("building_type", "Materiál", "Material", "enum", "building_type", fr.BUILDING_TYPE_OPTIONS),
    Dimension("furnished", "Vybavení", "Furnished", "enum", "furnished", FURNISHED_OPTIONS),
    Dimension("has_balcony", "Balkon", "Balcony", "bool", "case has_balcony when true then 'ano' when false then 'ne' end", BOOL_OPTIONS),
    Dimension("has_lift", "Výtah", "Lift", "bool", "case has_lift when true then 'ano' when false then 'ne' end", BOOL_OPTIONS),
    Dimension("has_parking", "Parkování", "Parking", "bool", "case has_parking when true then 'ano' when false then 'ne' end", BOOL_OPTIONS),
    Dimension("terrace", "Terasa", "Terrace", "bool", "case terrace when true then 'ano' when false then 'ne' end", BOOL_OPTIONS),
    Dimension("cellar", "Sklep", "Cellar", "bool", "case cellar when true then 'ano' when false then 'ne' end", BOOL_OPTIONS),
    Dimension("garage", "Garáž", "Garage", "bool", "case garage when true then 'ano' when false then 'ne' end", BOOL_OPTIONS),
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
    """One saved chart: {id, view, rows, cols, measure, stat}. `stat` matters to line/bar only."""
    if not isinstance(chart, dict):
        raise SpecError("chart must be an object")
    cid = chart.get("id")
    if not isinstance(cid, str) or not (1 <= len(cid) <= 64):
        raise SpecError("chart id must be a short string")
    view = chart.get("view")
    if view not in VIEWS:
        raise SpecError(f"unknown view {view!r}")
    stat = chart.get("stat", "median")
    if stat not in CHART_STATS:
        raise SpecError(f"unknown stat {stat!r}")
    spec = validate_spec({"rows": chart.get("rows", []), "cols": chart.get("cols", []),
                          "measure": chart.get("measure")})
    limits = VIEW_LIMITS[view]
    if len(spec["cols"]) > limits.max_cols:
        raise SpecError(f"a {view} chart takes row dimensions only")
    if not limits.min_rows <= len(spec["rows"]) <= limits.max_rows:
        span = (f"exactly {limits.min_rows}" if limits.min_rows == limits.max_rows
                else f"{limits.min_rows} to {limits.max_rows}")
        raise SpecError(f"a {view} chart takes {span} row dimension{'' if limits.max_rows == 1 else 's'}")
    return {"id": cid, "view": view, "stat": stat, **spec}


def registry_json() -> dict[str, Any]:
    """What the generated TypeScript (and any client) needs to build and label a chart."""
    def option(o: fr.EnumOption) -> dict[str, Any]:
        return {"value": o.value, "label_cs": o.label_cs, "label_en": o.label_en}
    return {
        "min_n": MIN_N, "max_rows": MAX_ROWS, "max_cols": MAX_COLS, "max_cells": MAX_CELLS,
        "max_charts": MAX_CHARTS,
        "stats": list(STATS), "chart_stats": list(CHART_STATS), "views": list(VIEWS),
        "view_limits": {
            v: {"min_rows": lim.min_rows, "max_rows": lim.max_rows, "max_cols": lim.max_cols}
            for v, lim in VIEW_LIMITS.items()
        },
        "rent_basis": RENT_BASIS,
        "dimensions": [
            {
                "id": d.id, "label_cs": d.label_cs, "label_en": d.label_en, "kind": d.kind,
                "categories": list(d.categories) if d.categories else None,
                "options": [option(o) for o in d.options] if d.options else None,
                "steps": d.steps, "unit": d.unit,
            }
            for d in DIMENSIONS
        ],
        "measures": [
            {"id": m.id, "label_cs": m.label_cs, "label_en": m.label_en, "unit": m.unit,
             "single_status": m.single_status}
            for m in MEASURES
        ],
    }

"""toolkit.stats_registry — the Stats dashboard's one vocabulary (docs/design/stats-dashboard.md).

The board validator gates `PUT /stats/dashboard`; the spec validator states the same rules
the pivot RPC refuses on; the SQL faces render one arm per dimension / measure from the same
tuples the generated TypeScript is emitted from. Migration 626 carrying those faces verbatim is
tests/test_626_stats_dashboard.py's subject.
"""

from __future__ import annotations

import json
import re

import pytest

from toolkit import filter_registry as fr
from toolkit import stats_registry as sr
from toolkit.measures import RENT_MONTHLY_CZK_M2

_OK_SPEC = {"rows": ["disposition", "condition"], "cols": ["kraj"], "measure": "ppm2"}
_OK_CHART = {"id": "c1", "view": "table", "rows": ["disposition"], "cols": ["condition"],
             "measure": "price", "stats": ["p25", "median"]}


# --- validate_spec --------------------------------------------------------------------


def test_a_spec_is_normalised_to_its_three_keys() -> None:
    assert sr.validate_spec({**_OK_SPEC, "extra": 1}) == _OK_SPEC
    assert sr.validate_spec({"measure": "tom_days"}) == {"rows": [], "cols": [], "measure": "tom_days"}


def test_six_rows_and_one_column_are_the_limits() -> None:
    six = [d.id for d in sr.DIMENSIONS[:6]]
    assert sr.validate_spec({"rows": six, "cols": [sr.DIMENSIONS[6].id], "measure": "area"})["rows"] == six


@pytest.mark.parametrize(
    "spec, reason",
    [
        ("rows", "spec must be an object"),
        ({"rows": "disposition", "measure": "price"}, "rows and cols must be arrays"),
        ({"cols": {"a": 1}, "measure": "price"}, "rows and cols must be arrays"),
        ({"rows": [d.id for d in sr.DIMENSIONS[:7]], "measure": "price"}, "at most 6 row dimensions"),
        ({"cols": ["kraj", "okres"], "measure": "price"}, "at most 1 column dimension"),
        ({"rows": ["pocet_pokoju"], "measure": "price"}, "unknown dimension 'pocet_pokoju'"),
        ({"rows": ["disposition"], "cols": ["disposition"], "measure": "price"}, "a dimension may appear once"),
        ({"rows": ["kraj", "kraj"], "measure": "price"}, "a dimension may appear once"),
        ({"rows": ["kraj"], "measure": "rent_yield"}, "unknown measure 'rent_yield'"),
        ({"rows": ["kraj"]}, "unknown measure None"),
        ({"rows": [["kraj"]], "measure": "price"}, "dimensions must be strings"),
        ({"rows": [{"a": 1}], "measure": "price"}, "dimensions must be strings"),
        ({"cols": [7], "measure": "price"}, "dimensions must be strings"),
        ({"rows": ["kraj"], "measure": ["price"]}, "unknown measure ['price']"),
        ({"rows": ["kraj"], "measure": {"id": "price"}}, "unknown measure {'id': 'price'}"),
    ],
)
def test_a_bad_spec_is_refused_with_its_reason(spec: object, reason: str) -> None:
    with pytest.raises(sr.SpecError, match=re.escape(reason)):
        sr.validate_spec(spec)


# --- validate_chart -------------------------------------------------------------------


def test_a_chart_is_normalised_and_a_title_is_never_stored() -> None:
    out = sr.validate_chart({**_OK_CHART, "title": "Ceny podle dispozice"})
    assert out == {"id": "c1", "view": "table", "stats": ["p25", "median"], "rows": ["disposition"],
                   "cols": ["condition"], "measure": "price"}


def test_the_stats_default_to_the_median_alone() -> None:
    chart = {k: v for k, v in _OK_CHART.items() if k != "stats"}
    assert sr.validate_chart(chart)["stats"] == ["median"]


def test_the_stats_keep_the_order_they_were_picked_in() -> None:
    """The first pick leads (the leading series is the copper one), so the order is part of the chart."""
    chart = {**_OK_CHART, "stats": ["p90", "median", "p10"]}
    assert sr.validate_chart(chart)["stats"] == ["p90", "median", "p10"]


def test_the_old_single_stat_key_is_ignored_not_read() -> None:
    """A client that still sends `stat` (the migration-628 shape) gets the default, never an error."""
    chart = {k: v for k, v in _OK_CHART.items() if k != "stats"} | {"stat": "p90"}
    out = sr.validate_chart(chart)
    assert out["stats"] == ["median"] and "stat" not in out


@pytest.mark.parametrize("view", sr.VIEWS)
def test_every_view_takes_rows_only(view: str) -> None:
    assert sr.validate_chart({**_OK_CHART, "view": view, "cols": []})["view"] == view


def test_the_view_limits_are_ruling_7() -> None:
    """table = rows x one column; line / bar = one row on x, the column as series; box = rows only."""
    assert set(sr.VIEW_LIMITS) == set(sr.VIEWS)
    as_tuples = {v: (lim.min_rows, lim.max_rows, lim.max_cols) for v, lim in sr.VIEW_LIMITS.items()}
    assert as_tuples == {"table": (0, 6, 1), "line": (1, 1, 1), "bar": (1, 1, 1), "box": (0, 6, 0)}


@pytest.mark.parametrize("view", ["table", "box"])
def test_a_table_or_box_takes_zero_to_six_rows(view: str) -> None:
    six = [d.id for d in sr.DIMENSIONS[:6]]
    for rows in ([], six):
        assert sr.validate_chart({**_OK_CHART, "view": view, "rows": rows, "cols": []})["rows"] == rows


@pytest.mark.parametrize("view", ["line", "bar"])
def test_a_line_or_bar_takes_one_row_and_a_series(view: str) -> None:
    out = sr.validate_chart({**_OK_CHART, "view": view, "rows": ["kraj"], "cols": ["condition"]})
    assert (out["rows"], out["cols"]) == (["kraj"], ["condition"])


@pytest.mark.parametrize(
    "patch, reason",
    [
        ({"id": ""}, "chart id must be a short string"),
        ({"id": "x" * 65}, "chart id must be a short string"),
        ({"id": 7}, "chart id must be a short string"),
        ({"view": "pie"}, "unknown view 'pie'"),
        ({"stats": ["n"]}, "unknown stat 'n'"),
        ({"stats": ["median", "max"]}, "unknown stat 'max'"),
        ({"stats": []}, "stats must be a non-empty list"),
        ({"stats": "median"}, "stats must be a non-empty list"),
        ({"stats": ["p10", "p10"]}, "a statistic may appear once"),
        ({"view": "box"}, "a box chart takes row dimensions only"),
        ({"view": "line", "rows": ["kraj", "obec"]}, "a line chart takes exactly 1 row dimension"),
        ({"view": "line", "rows": []}, "a line chart takes exactly 1 row dimension"),
        ({"view": "bar", "rows": ["disposition", "kraj", "obec"]}, "a bar chart takes exactly 1 row dimension"),
        ({"view": "bar", "rows": [], "cols": []}, "a bar chart takes exactly 1 row dimension"),
        ({"rows": [["kraj"]]}, "dimensions must be strings"),
        ({"measure": ["price"]}, "unknown measure ['price']"),
        ({"measure": "yield"}, "unknown measure 'yield'"),
        ({"rows": ["kraj"], "cols": ["kraj"]}, "a dimension may appear once"),
    ],
)
def test_a_bad_chart_is_refused_with_its_reason(patch: dict, reason: str) -> None:
    with pytest.raises(sr.SpecError, match=re.escape(reason)):
        sr.validate_chart({**_OK_CHART, **patch})


def test_a_chart_must_be_an_object() -> None:
    with pytest.raises(sr.SpecError, match="chart must be an object"):
        sr.validate_chart(["c1"])


# --- validate_board -------------------------------------------------------------------


def test_a_board_is_its_charts_normalised_in_order() -> None:
    board = [{**_OK_CHART, "id": "b"}, {**_OK_CHART, "id": "a", "view": "box", "cols": []}]
    assert [c["id"] for c in sr.validate_board(board)] == ["b", "a"]
    assert sr.validate_board([]) == []
    assert len(sr.validate_board([{**_OK_CHART, "id": f"c{i}"} for i in range(sr.MAX_CHARTS)])) == sr.MAX_CHARTS


@pytest.mark.parametrize(
    "board, reason",
    [
        ([{**_OK_CHART, "id": f"c{i}"} for i in range(sr.MAX_CHARTS + 1)],
         f"at most {sr.MAX_CHARTS} charts on a board"),
        ([_OK_CHART, dict(_OK_CHART)], "chart 1: duplicate id 'c1'"),
        ([_OK_CHART, {**_OK_CHART, "id": "c2", "view": "pie"}], "chart 1: unknown view 'pie'"),
        (["c1"], "chart 0: chart must be an object"),
    ],
)
def test_a_bad_board_is_refused_naming_the_chart(board: list, reason: str) -> None:
    with pytest.raises(sr.SpecError) as exc:
        sr.validate_board(board)
    assert str(exc.value) == reason


# --- the SQL faces ----------------------------------------------------------------------


def test_dim_key_has_one_arm_per_dimension_in_registry_order() -> None:
    sql = sr.dim_key_function_sql()
    arms = re.findall(r"^    when '([a-z0-9_]+)' then ", sql, flags=re.MULTILINE)
    assert arms == [d.id for d in sr.DIMENSIONS]
    assert len(set(arms)) == len(arms)
    assert sql.startswith("create or replace function public.stats_dim_key(\n  dim text,\n")
    assert "language sql\nimmutable\nparallel safe\n" in sql and "plpgsql" not in sql


def test_the_dim_key_signature_is_the_argument_tuple() -> None:
    sql = sr.dim_key_function_sql()
    head = sql[sql.index("(") + 1: sql.index(")\nreturns text")]
    assert [tuple(a.split()) for a in head.split(",\n")] == [(n, t) for n, t in sr.DIM_KEY_ARGS]
    assert sr.DIM_KEY_SIGNATURE == ", ".join(t for _, t in sr.DIM_KEY_ARGS)


_SQL_WORDS = {"case", "when", "then", "else", "end", "is", "null", "floor", "text", "true", "false"}


@pytest.mark.parametrize("dim", sr.DIMENSIONS, ids=[d.id for d in sr.DIMENSIONS])
def test_every_dimension_reads_only_the_key_functions_arguments(dim: sr.Dimension) -> None:
    """A dimension that needs a new column adds it to DIM_KEY_ARGS first."""
    bare = re.sub(r"'[^']*'", "", dim.sql)
    words = set(re.findall(r"[a-z_][a-z0-9_]*", bare)) - _SQL_WORDS
    assert words and words <= {n for n, _ in sr.DIM_KEY_ARGS[1:]}, words


def test_measure_value_has_one_arm_per_measure() -> None:
    sql = sr.measure_value_sql()
    assert sql.startswith("case v_measure ") and sql.endswith(" end")
    assert re.findall(r"when '([a-z0-9_]+)' then", sql) == [m.id for m in sr.MEASURES]
    assert sr.measure_value_sql("x").startswith("case x ")


# --- the vocabulary -------------------------------------------------------------------


def test_the_limits_are_the_rulings() -> None:
    assert (sr.MIN_N, sr.MAX_ROWS, sr.MAX_COLS, sr.MAX_CELLS) == (5, 6, 1, 2000)
    assert sr.STATS == ("n", "mean", "median", "p10", "p25", "p75", "p90")
    assert sr.CHART_STATS == sr.STATS[1:] and sr.DEFAULT_STAT in sr.CHART_STATS


def test_ids_are_unique() -> None:
    assert len(sr.DIMENSION_BY_ID) == len(sr.DIMENSIONS)
    assert len(sr.MEASURE_BY_ID) == len(sr.MEASURES)


@pytest.mark.parametrize("dim", [d for d in sr.DIMENSIONS if d.kind == "band"],
                         ids=[d.id for d in sr.DIMENSIONS if d.kind == "band"])
def test_every_band_has_steps_and_names_an_existing_measure(dim: sr.Dimension) -> None:
    assert dim.steps and dim.measure in sr.MEASURE_BY_ID
    assert all(isinstance(v, int) and v > 0 for v in dim.steps.values())
    if dim.measure == "area":
        assert set(dim.steps) == {"all"} and dim.categories == ("byt",)
        assert dim.sql.startswith("case when category_main = 'byt' then ")
    else:
        assert set(dim.steps) == {"sale", "rent"} and dim.categories is None
        assert f"'{sr.RENT_BASIS}' then {dim.steps['rent']} else {dim.steps['sale']}" in dim.sql


def test_only_bands_name_a_measure() -> None:
    assert all((d.measure is None) == (d.kind != "band") for d in sr.DIMENSIONS)


def test_the_band_steps_are_ruling_5() -> None:
    assert {d.id: d.steps for d in sr.DIMENSIONS if d.kind == "band"} == {
        "area_band_5": {"all": 5}, "area_band_10": {"all": 10},
        "ppm2_band_s": {"sale": 5_000, "rent": 25}, "ppm2_band_l": {"sale": 10_000, "rent": 50},
        "price_band_s": {"sale": 250_000, "rent": 1_000},
        "price_band_m": {"sale": 500_000, "rent": 2_500},
        "price_band_l": {"sale": 1_000_000, "rent": 5_000},
    }
    assert sr.RENT_BASIS == RENT_MONTHLY_CZK_M2


def test_enum_and_bool_dimensions_carry_their_order_and_places_none() -> None:
    for d in sr.DIMENSIONS:
        if d.kind in ("enum", "bool"):
            assert d.options, d.id
        else:
            assert not d.options, d.id
        if d.kind == "bool":
            assert [o.value for o in d.options] == ["ano", "ne"]
    assert sr.DIMENSION_BY_ID["condition"].options == fr.CONDITION_OPTIONS
    assert sr.GEO_IDS == {"kraj", "okres", "obec", "cast_obce", "ulice"}


def test_furnished_has_no_unknown_bucket() -> None:
    """Ruling 8: an unstated value is excluded and counted, never a bucket."""
    values = [o.value for o in sr.FURNISHED_OPTIONS]
    assert "__unknown__" not in values and set(values) == set(fr.FURNISHED_CANONICAL)


def test_only_turnover_needs_one_status() -> None:
    assert [m.id for m in sr.MEASURES if m.single_status] == ["tom_days"]


def test_registry_json_round_trips_and_names_everything() -> None:
    doc = sr.registry_json()
    assert json.loads(json.dumps(doc)) == doc
    assert set(doc) == {"min_n", "max_cells", "max_charts", "chart_stats", "default_stat", "views",
                        "refusals", "statuses", "view_limits", "dimensions", "measures"}
    assert [d["id"] for d in doc["dimensions"]] == [d.id for d in sr.DIMENSIONS]
    assert [m["id"] for m in doc["measures"]] == [m.id for m in sr.MEASURES]
    assert (doc["min_n"], doc["max_cells"], doc["max_charts"]) == (5, 2000, sr.MAX_CHARTS)
    assert doc["chart_stats"] == list(sr.CHART_STATS) and doc["default_stat"] in doc["chart_stats"]
    assert doc["refusals"] == list(sr.REFUSALS) and doc["statuses"] == list(sr.STATUSES)
    assert doc["view_limits"] == {
        v: {"min_rows": lim.min_rows, "max_rows": lim.max_rows, "max_cols": lim.max_cols}
        for v, lim in sr.VIEW_LIMITS.items()
    }
    assert list(doc["view_limits"]) == list(sr.VIEWS)
    for d in doc["dimensions"]:
        assert d["label_cs"] and d["label_en"] and d["group"]
        assert (d["steps"] is None) == (d["kind"] != "band")
        assert (d["measure"] in sr.MEASURE_BY_ID) == (d["kind"] == "band")


def test_no_dimension_label_spells_a_per_m2_unit() -> None:
    """Rule 23: one band dimension serves a sale (Kč/m²) and a rent (Kč/m²/měs) cohort, so its
    label names the quantity and the steps; the unit comes from the payload's basis."""
    for d in sr.DIMENSIONS:
        assert not re.search(r"(K[čc]|CZK)\s*/\s*m", d.label_cs + d.label_en), d.id


def test_the_area_measure_reads_the_polymorphic_area_column() -> None:
    """Rule 23: browse_list.area_m2 IS the plot for a pozemek (plot_area_m2 = area_m2 there)."""
    assert sr.MEASURE_BY_ID["area"].sql == "l.area_m2::numeric"

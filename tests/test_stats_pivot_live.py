"""The Stats pivot (migration 626), executed against the replayed schema.

`browse_stats_properties(<625's cohort>, spec)` is ONE rollup over the Browse cohort at
property grain (docs/design/stats-dashboard.md). What only executing it shows:

  * THE LEDGER. `filtered` is the cohort, `sample` the properties with the measure and a value
    on every chosen dimension, `excluded` the rest (ruling 8: a NULL is never a bucket).
  * THE LEVELS. A cell with `keys` of length L is the rollup at level L, 0 .. len(rows), one
    pass; statistics are NULL below `min_n` (ruling 6), the count never is.
  * THE REFUSALS. `empty`, `mixed_category` (ruling 4), `mixed_status` on turnover (ruling 3),
    `too_many_cells` (above MAX_CELLS) and `bad_spec` — refused for exactly the specs
    `toolkit.stats_registry.validate_spec` refuses.
  * THE KEYS. A band key is the band's lower bound on the payload's basis: Kc/m2 bands of 5 000
    on a sale, 25 on a rent (ruling 5); a place key is its RUIAN code as text, so two towns
    that share a name stay two cells, and the payload's `places` names each one from the
    register (null when the register has no live name for the code).
  * THE CALLER. The SPA calls it as `authenticated`; a place chart names its keys through the
    owner-rights view 626 adds (ruian_place_names_public), because the register mirror (381)
    is dark to browser roles and the pivot runs as its caller.

Rows go straight into `browse_list` (a real table since 590, `select *` of browse_projection),
with the per-m2 figure and its basis computed by migration 425's measure functions, so the
pivot reads exactly the shapes a rebuild publishes; a place code goes into the column
browse_projection fills from listing_location (region_id = kraj_kod, okres_id = okres_kod,
obec_id = obec_kod, cast_obce_id = cast_obce_kod, ulice_id = ulice_kod; 625). The register
rows the names resolve from are seeded beside them. Every test runs in a transaction that
always rolls back. The migrations lane sets DB_RAILS_REQUIRED=1, so a lost TEST_DATABASE_URL
goes red instead of skipping green.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from tests._live_property import REQUIRED_DB, cur  # noqa: F401 -- `cur` is the fixture
from toolkit import stats_registry as sr

pytestmark = REQUIRED_DB

_IDS = itertools.count(9_710_000_001)

# Synthetic RUIAN codes, nine digits (no real code at these levels is that long), so a register
# that already holds real rows can never answer for them.
_JMK, _BRNO_MESTO = 990_000_116, 990_003_702                  # kraj, okres
_BRNO, _KURIM = 990_582_786, 990_583_341                      # obec
_LHOTA_A, _LHOTA_B, _RETIRED = 990_500_001, 990_500_002, 990_500_003  # obec: one name twice; retired
_VEVERI, _NADRAZNI = 990_411_001, 990_700_001                 # cast_obce, ulice
_IN_BRNO = {"kraj": _JMK, "okres": _BRNO_MESTO, "obec": _BRNO}


def _add(cur: Any, *, cm: str = "byt", ct: str = "prodej", disposition: str | None = "2+kk",
         condition: str | None = "velmi_dobry", area: float | None = 50.0,
         price: int | None = 5_000_000, active: bool = True, tom: int | None = 30,
         balcony: bool | None = None, kraj: int | None = None, okres: int | None = None,
         obec: int | None = None, cast_obce: int | None = None, ulice: int | None = None,
         sources: list[str] | None = None, active_sources: list[str] | None = None) -> int:
    """`sources` / `active_sources`: the property's portals (default: sreality, active iff `active`)."""
    pid, lid = next(_IDS), next(_IDS)
    cur.execute(
        "insert into browse_list (property_id, listing_id, is_active, category_main, category_type, "
        "  disposition, condition, area_m2, price_czk, price_per_m2, price_per_m2_basis, tom_days, "
        "  has_balcony, region_id, okres_id, obec_id, cast_obce_id, ulice_id, "
        "  all_sources, active_sources) "
        "values (%(pid)s, %(lid)s, %(active)s::boolean, %(cm)s::text, %(ct)s::text, "
        "  %(disposition)s::text, %(condition)s::text, %(area)s::numeric, %(price)s::integer, "
        "  measure_price_per_m2(%(price)s::numeric, %(area)s::numeric, %(cm)s::text, %(ct)s::text), "
        "  measure_price_per_m2_basis(%(cm)s::text, %(ct)s::text), %(tom)s::integer, "
        "  %(balcony)s::boolean, %(kraj)s::bigint, %(okres)s::bigint, %(obec)s::bigint, "
        "  %(cast_obce)s::bigint, %(ulice)s::bigint, %(sources)s::text[], %(active_sources)s::text[])",
        {"pid": pid, "lid": lid, "active": active, "cm": cm, "ct": ct,
         "disposition": disposition, "condition": condition, "area": area, "price": price,
         "tom": tom, "balcony": balcony, "kraj": kraj, "okres": okres, "obec": obec,
         "cast_obce": cast_obce, "ulice": ulice,
         "sources": sources if sources is not None else ["sreality"],
         "active_sources": active_sources if active_sources is not None else (["sreality"] if active else [])},
    )
    return pid


def _register(cur: Any) -> None:
    """The register rows the place names resolve from (381's NOT NULL columns): one kraj, one
    okres, five obce (two named Lhota, one retired), one cast obce and one street."""
    cur.execute(
        "insert into registry_versions (label, kind, source_date, artifact_urls, proj_version, "
        "  proj_pipeline) "
        "values ('stats-pivot-test', 'baseline', '2026-10-01', '{}', 'test', 'test') returning id"
    )
    version = cur.fetchone()[0]

    def unit(level: str, code: int, name: str, retired: bool = False) -> int:
        cur.execute(
            "insert into ruian_admin_units (level, code, name, name_norm, path, display_path, "
            "  valid_from, first_version_id, last_version_id, retired_at) "
            "values (%s::ruian_level, %s, %s, lower(%s), %s::ltree, %s, '2020-01-01', %s, %s, "
            "  case when %s::boolean then now() end) returning id",
            (level, code, name, name, f"{level}_{code}", name, version, version, retired),
        )
        return cur.fetchone()[0]

    unit("kraj", _JMK, "Jihomoravský kraj")
    unit("okres", _BRNO_MESTO, "Brno-město")
    brno = unit("obec", _BRNO, "Brno")
    unit("obec", _KURIM, "Kuřim")
    unit("obec", _LHOTA_A, "Lhota")
    unit("obec", _LHOTA_B, "Lhota")
    unit("obec", _RETIRED, "Zaniklá", retired=True)
    unit("cast_obce", _VEVERI, "Veveří")
    cur.execute(
        "insert into ruian_streets (code, name, name_norm, obec_unit_id, first_version_id, "
        "  last_version_id, valid_from) "
        "values (%s, 'Nádražní', 'nadrazni', %s, %s, %s, '2020-01-01')",
        (_NADRAZNI, brno, version, version),
    )


def _pivot(cur: Any, pids: list[int], spec: Any, **args: Any) -> dict[str, Any]:
    """`args`: further cohort parameters by name (portal_filter, status_filter, ...)."""
    extra = "".join(f", {k} => %({k})s" for k in args)
    cur.execute(
        "select public.browse_stats_properties(property_ids_filter => %(pids)s::bigint[], "
        f"spec => %(spec)s::jsonb{extra})",
        {"pids": pids, "spec": None if spec is None else json.dumps(spec), **args},
    )
    return cur.fetchone()[0]


def _cells(payload: dict[str, Any]) -> dict[tuple, dict[str, Any]]:
    return {(tuple(c["keys"]), c["col"]): c for c in payload["cells"]}


# --- the ledger, the levels, min_n ---------------------------------------------------


def _two_dispositions_by_three_stavs(cur: Any) -> list[int]:
    """2+kk: 12 novostavba (priced 1..12 M), 3 dobry, 2 with no stav; 3+kk: 10 novostavba,
    1 dobry, 1 with no stav."""
    pids = [_add(cur, disposition="2+kk", condition="novostavba", price=i * 1_000_000)
            for i in range(1, 13)]
    pids += [_add(cur, disposition="2+kk", condition="dobry") for _ in range(3)]
    pids += [_add(cur, disposition="2+kk", condition=None) for _ in range(2)]
    pids += [_add(cur, disposition="3+kk", condition="novostavba") for _ in range(10)]
    pids += [_add(cur, disposition="3+kk", condition="dobry")]
    pids += [_add(cur, disposition="3+kk", condition=None)]
    return pids


def test_the_ledger_and_every_rollup_level(cur) -> None:
    pids = _two_dispositions_by_three_stavs(cur)
    spec = {"rows": ["disposition", "condition"], "cols": [], "measure": "price"}
    out = _pivot(cur, pids, spec)
    assert out["spec"] == spec
    assert (out["filtered"], out["sample"], out["excluded"]) == (29, 26, 3)
    assert (out["category_main"], out["category_type"], out["status"]) == ("byt", "prodej", "active")
    assert (out["basis"], out["min_n"], out["refused"]) == ("sale_capital_czk_m2", sr.MIN_N, None)
    cells = _cells(out)
    assert {k: c["n"] for k, c in cells.items()} == {
        ((), None): 26,
        (("2+kk",), None): 15, (("3+kk",), None): 11,
        (("2+kk", "novostavba"), None): 12, (("2+kk", "dobry"), None): 3,
        (("3+kk", "novostavba"), None): 10, (("3+kk", "dobry"), None): 1,
    }
    assert [len(c["keys"]) for c in out["cells"]] == sorted(len(c["keys"]) for c in out["cells"])
    big = cells[(("2+kk", "novostavba"), None)]
    assert (big["mean"], big["median"], big["p10"], big["p90"]) == (6_500_000, 6_500_000, 2_100_000, 10_900_000)
    assert big["p25"] < big["median"] < big["p75"]


def test_a_cell_under_min_n_ships_its_count_and_no_statistics(cur) -> None:
    out = _pivot(cur, _two_dispositions_by_three_stavs(cur),
                 {"rows": ["disposition", "condition"], "cols": [], "measure": "price"})
    cells = _cells(out)
    for key in ((("2+kk", "dobry"), None), (("3+kk", "dobry"), None)):
        small = cells[key]
        assert small["n"] < sr.MIN_N
        assert all(small[s] is None for s in sr.CHART_STATS), small
    assert cells[(("3+kk", "novostavba"), None)]["median"] == 5_000_000  # ten properties: over any floor here


def test_the_floor_is_inclusive_at_min_n_and_exclusive_just_below(cur) -> None:
    """The boundary itself, from the registry's floor: MIN_N properties show statistics, one fewer do not."""
    pids = [_add(cur, disposition="2+kk") for _ in range(sr.MIN_N)]
    pids += [_add(cur, disposition="3+kk") for _ in range(sr.MIN_N - 1)]
    out = _pivot(cur, pids, {"rows": ["disposition"], "cols": [], "measure": "price"})
    cells = _cells(out)
    at, below = cells[(("2+kk",), None)], cells[(("3+kk",), None)]
    assert out["min_n"] == sr.MIN_N
    assert at["n"] == sr.MIN_N and at["median"] == 5_000_000
    assert below["n"] == sr.MIN_N - 1 and all(below[st] is None for st in sr.CHART_STATS), below


def test_a_column_dimension_rolls_up_per_column_value(cur) -> None:
    pids = [_add(cur, disposition="2+kk", balcony=True) for _ in range(4)]
    pids += [_add(cur, disposition="3+kk", balcony=False) for _ in range(2)]
    pids += [_add(cur, disposition="3+kk", balcony=None)]
    out = _pivot(cur, pids, {"rows": ["disposition"], "cols": ["has_balcony"], "measure": "area"})
    assert (out["filtered"], out["sample"], out["excluded"]) == (7, 6, 1)
    assert {k: c["n"] for k, c in _cells(out).items()} == {
        ((), "ano"): 4, (("2+kk",), "ano"): 4, ((), "ne"): 2, (("3+kk",), "ne"): 2,
    }


def test_no_dimension_is_the_whole_sample_in_one_cell(cur) -> None:
    pids = [_add(cur, price=None)] + [_add(cur) for _ in range(10)]
    out = _pivot(cur, pids, {"rows": [], "cols": [], "measure": "price"})
    assert (out["filtered"], out["sample"], out["excluded"]) == (11, 10, 1)
    [cell] = out["cells"]
    assert (cell["keys"], cell["col"], cell["n"], cell["median"]) == ([], None, 10, 5_000_000)


# --- the refusals ----------------------------------------------------------------------


def test_an_empty_cohort_is_refused_with_its_counts(cur) -> None:
    out = _pivot(cur, [], {"rows": ["disposition"], "cols": [], "measure": "price"})
    assert (out["refused"], out["filtered"], out["sample"], out["status"], out["cells"]) == (
        "empty", 0, 0, None, [])


@pytest.mark.parametrize("other", [{"ct": "pronajem", "price": 15_000}, {"cm": "dum"}])
def test_more_than_one_category_is_refused(cur, other: dict[str, Any]) -> None:
    pids = [_add(cur) for _ in range(10)] + [_add(cur, **other) for _ in range(10)]
    out = _pivot(cur, pids, {"rows": ["disposition"], "cols": [], "measure": "price"})
    assert (out["refused"], out["filtered"], out["cells"]) == ("mixed_category", 20, [])
    assert None in (out["category_main"], out["category_type"])


def test_the_status_fact_is_the_propertys_under_a_portal_filter(cur) -> None:
    """Migration 657: the status is the property's on every surface. A property whose sreality ad
    ended but which is still live on bezrealitky is NOT in "Neaktivní on sreality" (it is live), so
    that cohort reads inactive and turnover draws over the five closed runs with nothing set aside;
    "any status on sreality" keeps it as the live row it is, and turnover refuses the mix."""
    gone = [_add(cur, active=False, tom=100 + i) for i in range(5)]
    elsewhere = _add(cur, active=True, tom=50, sources=["sreality", "bezrealitky"], active_sources=["bezrealitky"])
    cohort = gone + [elsewhere]
    inactive = _pivot(cur, cohort, {"rows": [], "cols": [], "measure": "tom_days"},
                      portal_filter=["sreality"], status_filter="inactive")
    assert (inactive["filtered"], inactive["sample"], inactive["excluded"], inactive["status"],
            inactive["refused"]) == (5, 5, 0, "inactive", None)
    assert inactive["cells"][0]["n"] == 5 and inactive["cells"][0]["median"] == 102
    active = _pivot(cur, cohort, {"rows": [], "cols": [], "measure": "price"},
                    portal_filter=["sreality"], status_filter="active")
    assert (active["filtered"], active["refused"]) == (0, "empty")
    any_status = _pivot(cur, cohort, {"rows": [], "cols": [], "measure": "tom_days"}, portal_filter=["sreality"])
    assert (any_status["filtered"], any_status["status"], any_status["refused"]) == (6, "mixed", "mixed_status")


def test_turnover_needs_one_status_and_price_does_not(cur) -> None:
    live = [_add(cur, tom=10 + i) for i in range(10)]
    gone = [_add(cur, active=False, tom=100 + i) for i in range(10)]
    mixed = _pivot(cur, live + gone, {"rows": [], "cols": [], "measure": "tom_days"})
    assert (mixed["refused"], mixed["status"], mixed["cells"]) == ("mixed_status", "mixed", [])
    assert mixed["filtered"] == 20
    price = _pivot(cur, live + gone, {"rows": [], "cols": [], "measure": "price"})
    assert (price["refused"], price["status"]) == (None, "mixed")
    delisted = _pivot(cur, gone, {"rows": [], "cols": [], "measure": "tom_days"})
    assert (delisted["refused"], delisted["status"]) == (None, "inactive")
    assert delisted["cells"][0]["median"] == 104.5


def test_turnover_judges_the_status_of_the_sample_not_the_cohort(cur) -> None:
    """Ruling 3 says ONE status in the SAMPLE: delisted rows with no stav are excluded from a
    chart on stav, so what is charted is live rows only; the cohort fact still reads mixed."""
    live = [_add(cur, condition="dobry", tom=10 + i) for i in range(10)]
    gone = [_add(cur, active=False, condition=None, tom=100 + i) for i in range(5)]
    out = _pivot(cur, live + gone, {"rows": ["condition"], "cols": [], "measure": "tom_days"})
    assert (out["refused"], out["status"]) == (None, "mixed")
    assert (out["filtered"], out["sample"], out["excluded"]) == (15, 10, 5)
    assert {k: c["n"] for k, c in _cells(out).items()} == {((), None): 10, (("dobry",), None): 10}
    both = _pivot(cur, live + gone, {"rows": [], "cols": [], "measure": "tom_days"})
    assert (both["refused"], both["status"]) == ("mixed_status", "mixed")


def test_too_many_cells_is_refused(cur) -> None:
    """2,001 distinct 250k price bands + the level-0 cell = 2,002 cells, over MAX_CELLS."""
    base = 9_720_000_000
    cur.execute(
        "insert into browse_list (property_id, listing_id, is_active, category_main, "
        "  category_type, area_m2, price_czk, price_per_m2_basis, all_sources, active_sources) "
        "select %s + g, %s + g, true, 'byt', 'prodej', 50, g * 250000, 'sale_capital_czk_m2', "
        "  array['sreality'], array['sreality'] from generate_series(1, 2001) g",
        (base, base),
    )
    pids = list(range(base + 1, base + 2002))
    out = _pivot(cur, pids, {"rows": ["price_band_s"], "cols": [], "measure": "area"})
    assert (out["refused"], out["filtered"], out["sample"], out["cells"]) == (
        "too_many_cells", 2001, 2001, [])
    coarse = _pivot(cur, pids, {"rows": ["price_band_l"], "cols": [], "measure": "area"})
    assert coarse["refused"] is None and len(coarse["cells"]) == 502


_SPECS: list[Any] = [
    None,
    "disposition",
    {"rows": ["disposition"], "cols": [], "measure": "price"},
    {"rows": [d.id for d in sr.DIMENSIONS[:6]], "cols": [sr.DIMENSIONS[6].id], "measure": "ppm2"},
    {"rows": [d.id for d in sr.DIMENSIONS[:7]], "cols": [], "measure": "ppm2"},
    {"rows": [], "cols": ["kraj", "okres"], "measure": "ppm2"},
    {"rows": ["pocet_pokoju"], "cols": [], "measure": "ppm2"},
    {"rows": ["kraj"], "cols": ["kraj"], "measure": "ppm2"},
    {"rows": ["kraj", "kraj"], "cols": [], "measure": "ppm2"},
    {"rows": ["kraj"], "cols": [], "measure": "yield"},
    {"rows": ["kraj"], "cols": []},
    {"rows": "kraj", "measure": "ppm2"},
    {"rows": [], "cols": None, "measure": "ppm2"},
    {"rows": [["kraj"]], "cols": [], "measure": "ppm2"},
    {"rows": [{"a": 1}], "cols": [], "measure": "ppm2"},
    {"rows": [], "cols": [7], "measure": "ppm2"},
    {"rows": ["kraj"], "cols": [], "measure": ["ppm2"]},
    {"rows": ["kraj"], "cols": [], "measure": {"id": "ppm2"}},
]


@pytest.mark.parametrize("spec", _SPECS, ids=[str(i) for i in range(len(_SPECS))])
def test_the_rpc_refuses_exactly_what_the_registry_refuses(cur, spec: Any) -> None:
    pids = [_add(cur, **_IN_BRNO)]
    try:
        sr.validate_spec(spec)
        refused_in_python = False
    except sr.SpecError:
        refused_in_python = True
    out = _pivot(cur, pids, spec)
    assert (out["refused"] == "bad_spec") is refused_in_python, out
    if refused_in_python:
        assert out["reason"] and out["cells"] == []


# --- the keys ----------------------------------------------------------------------------


def test_band_keys_follow_the_basis(cur) -> None:
    """91 500 Kc/m2 sale: 5 000 bands -> 90000; 319.50 Kc/m2/month rent: 25 bands -> 300."""
    sale = [_add(cur, price=4_575_000, area=50.0), _add(cur, price=4_575_000, area=57.5)]
    rent = [_add(cur, ct="pronajem", price=15_975, area=50.0)]
    spec = {"rows": ["ppm2_band_s", "price_band_s", "area_band_10"], "cols": [], "measure": "price"}
    s, r = _pivot(cur, sale[:1], spec), _pivot(cur, rent, spec)
    assert s["basis"] == "sale_capital_czk_m2" and r["basis"] == sr.RENT_BASIS
    assert [c["keys"] for c in s["cells"]][-1] == ["90000", "4500000", "50"]
    assert [c["keys"] for c in r["cells"]][-1] == ["300", "15000", "50"]
    both = _pivot(cur, sale, {"rows": ["area_band_10", "ppm2_band_l"], "cols": [], "measure": "price"})
    # 4 575 000 / 57.5 = 79 565.22 -> the 70 000 band of 10 000; 57.5 m2 -> the 50 band
    assert {tuple(c["keys"]) for c in both["cells"]} == {(), ("50",), ("50", "90000"), ("50", "70000")}


def test_area_bands_exist_for_flats_only(cur) -> None:
    pids = [_add(cur, cm="dum") for _ in range(3)]
    out = _pivot(cur, pids, {"rows": ["area_band_5"], "cols": [], "measure": "price"})
    assert (out["filtered"], out["sample"], out["excluded"], out["cells"]) == (3, 0, 3, [])


# --- the caller ----------------------------------------------------------------------------


def test_place_keys_are_codes_and_a_shared_name_stays_two_cells(cur) -> None:
    """Two towns named Lhota are two cells, each named; a code with no live register name
    (here a retired unit) keeps its cell and ships `name: null`. The column slot names too."""
    _register(cur)
    pids = [_add(cur, obec=_LHOTA_A) for _ in range(2)] + [_add(cur, obec=_LHOTA_B)]
    pids += [_add(cur, obec=_RETIRED)]
    out = _pivot(cur, pids, {"rows": ["obec"], "cols": [], "measure": "price"})
    a, b, gone = str(_LHOTA_A), str(_LHOTA_B), str(_RETIRED)
    assert {k: c["n"] for k, c in _cells(out).items()} == {
        ((), None): 4, ((a,), None): 2, ((b,), None): 1, ((gone,), None): 1,
    }
    named = [{"dim": "obec", "key": a, "name": "Lhota"}, {"dim": "obec", "key": b, "name": "Lhota"},
             {"dim": "obec", "key": gone, "name": None}]
    assert out["places"] == named
    by_col = _pivot(cur, pids, {"rows": ["disposition"], "cols": ["obec"], "measure": "price"})
    assert {c["col"] for c in by_col["cells"]} == {a, b, gone}
    assert by_col["places"] == named
    assert _pivot(cur, pids, {"rows": ["disposition"], "cols": [], "measure": "price"})["places"] == []


def test_a_signed_in_caller_runs_every_shape_including_places(cur) -> None:
    _register(cur)
    centre = {**_IN_BRNO, "cast_obce": _VEVERI, "ulice": _NADRAZNI}
    pids = [_add(cur, **centre) for _ in range(10)]
    pids += [_add(cur, kraj=_JMK, okres=_BRNO_MESTO, obec=_KURIM)]
    pids += [_add(cur)]  # no place codes: excluded from a place chart
    cur.execute("set local role authenticated")
    plain = _pivot(cur, pids, {"rows": ["disposition"], "cols": [], "measure": "ppm2"})
    assert (plain["refused"], plain["filtered"], plain["sample"], plain["places"]) == (None, 12, 12, [])
    places = _pivot(cur, pids, {"rows": ["kraj", "obec"], "cols": [], "measure": "ppm2"})
    assert (places["refused"], places["filtered"], places["sample"], places["excluded"]) == (None, 12, 11, 1)
    kraj, brno, kurim = str(_JMK), str(_BRNO), str(_KURIM)
    assert {k: c["n"] for k, c in _cells(places).items()} == {
        ((), None): 11, ((kraj,), None): 11, ((kraj, brno), None): 10, ((kraj, kurim), None): 1,
    }
    assert places["places"] == [
        {"dim": "kraj", "key": kraj, "name": "Jihomoravský kraj"},
        {"dim": "obec", "key": brno, "name": "Brno"},
        {"dim": "obec", "key": kurim, "name": "Kuřim"},
    ]
    every = _pivot(cur, pids, {"rows": ["kraj", "okres", "obec", "cast_obce", "ulice"], "cols": [],
                               "measure": "ppm2"})
    assert (every["refused"], every["sample"]) == (None, 10)
    assert {(p["dim"], p["key"]): p["name"] for p in every["places"]} == {
        ("kraj", kraj): "Jihomoravský kraj", ("okres", str(_BRNO_MESTO)): "Brno-město",
        ("obec", brno): "Brno", ("cast_obce", str(_VEVERI)): "Veveří", ("ulice", str(_NADRAZNI)): "Nádražní",
    }

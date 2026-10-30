"""Hermetic tests for the parsing alarm (scripts.checks.quality): states, legacy folds, rails."""

# No DB: the wave-3 fake connection answers each statement by the SQL constant it carries, through
# the per-check clock. At build time every new statement was EXPLAINed read-only against
# production (10-19): the churn read 2.8 s over 282,584 snapshots, the payload read 2.1 s over
# 2,031 new ads, the median baseline 29 ms over 38 stored results of 227 cells each.

from __future__ import annotations

import ast
import datetime as _dt
import inspect
from typing import Any

import pytest

from location_data import claims_common
from scraper import field_census
from scripts.checks import clock, common
from scripts.checks import quality as qu
from tests.scripts.test_checks_platform import _Conn
from tests.scripts.test_checks_rule21 import _PORTAL, _hits
from tests.test_measure_registry_census import ARM_DIVISION, ARM_UNIT, _scan, strip_python_comments
from toolkit import measures

_UTC = _dt.timezone.utc
_NOW = _dt.datetime(2026, 10, 19, 14, 0, tzinfo=_UTC)

# The parsing keys as the wave-6 contract names them (D6, D12); the check runs on these values,
# and a rail below pins the harness's THRESHOLDS to them.
_CONTRACT: dict[str, Any] = {
    "parsing_fill_drop_warn_pp": 0.10, "parsing_fill_drop_fail_pp": 0.20,
    "parsing_fill_drop_min_baseline": 0.10, "parsing_fill_collapse_ratio": 0.25,
    "parsing_fill_collapse_min_rows": 50, "parsing_vocabulary_rise_warn_pp": 0.05,
    "parsing_vocabulary_rise_fail_pp": 0.15, "parsing_census_stale_days": 30,
    "parsing_measures_warn": 0.10, "parsing_measures_fail": 0.25, "parsing_measures_min_n": 50,
    "parsing_median_warn": 2.0, "parsing_median_fail": 3.0, "parsing_median_min_n": 200,
    "parsing_payload_warn": 0.03, "parsing_payload_fail": 0.10, "parsing_payload_min_n": 30,
    "parsing_churn_warn_x": 3, "parsing_churn_fail_x": 6, "parsing_churn_min_snapshots": 500,
}
T: dict[str, Any] = dict(_CONTRACT)
_SALE, _RENT, _LAND = (measures.SALE_CAPITAL_CZK_M2, measures.RENT_MONTHLY_CZK_M2,
                       measures.LAND_CAPITAL_CZK_M2)
_MARKER = "# --- gauges (Q2) ---"


def _parsing_source() -> str:
    """The module above the gauges: the parsing alarm and the scaffold it shares."""
    return inspect.getsource(qu).split(_MARKER)[0]


# --- the world --------------------------------------------------------------------------------


def _c(n: int, filled: int, **kw: Any) -> dict[str, Any]:
    """A matrix cell: `filled` of `n` active ads, its fill at the matrix's four decimals."""
    return {"n": n, "filled": filled, "fill": round(filled / n, 4) if n else 0.0, **kw}


def _blessed(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"generated_at": "2026-10-19T13:53:29+00:00", "cohort": field_census.MATRIX_COHORT,
            "cells": cells}


# Two portals: a canon field each, a boolean, a field with no canon, and a known zero (remax never
# states a balcony; nothing can write it, so it is inert too).
_BLESSED = _blessed({
    "idnes/condition": _c(108_566, 73_401, off_canon=0.0),
    "idnes/has_balcony": _c(108_566, 14_980, true=14_980, false=0),
    "remax/condition": _c(9_464, 7_735, off_canon=0.0),
    "remax/floor": _c(9_464, 6_000),
    "remax/has_balcony": _c(9_464, 0, true=0, false=0),
})


def _matrix(blessed: dict[str, Any],
            moved: dict[str, dict[str, Any] | None] | None = None) -> list[tuple[Any, ...]]:
    """FIELD_MATRIX_SQL's rows reproducing `blessed`, each `moved` cell changed (None drops it)."""
    cells = {key: dict(cell) for key, cell in blessed["cells"].items()}
    for key, change in (moved or {}).items():
        if change is None:
            cells.pop(key, None)
        else:
            cells[key] = {**cells.get(key, {}), **change}
    rows = []
    for key, cell in sorted(cells.items()):
        source, field = key.split("/", 1)
        filled = int(cell.get("filled", 0))
        canon = field_census.canonical_values(field) is not None
        rows.append((source, int(cell.get("n", 0)), field, filled, None,
                     round(float(cell.get("off_canon", 0.0)) * filled) if canon else None,
                     int(cell.get("true", 0)), int(cell.get("false", 0)), None,
                     cell.get("off_canon_values")))
    return rows


def _view(source: str, main: str | None, deal: str | None, **kw: Any) -> dict[str, Any]:
    """A measure_plausibility_by_source row: a healthy sale cell unless `kw` says otherwise."""
    row: dict[str, Any] = dict.fromkeys(qu.PLAUSIBILITY_COLUMNS)
    row.update(source=source, category_main=main, category_type=deal, price_per_m2_basis=_SALE,
               n_active=40_000, n_active_7d=900, measure_input_gap_share=0.04,
               measure_input_gap_share_7d=0.04, median_price_per_m2=95_000.0,
               n_ppm2_valued=38_000)
    row.update(kw)
    return row


_VIEW = [
    _view("sreality", "byt", "prodej"),
    # 10-19's worst scored cell: 33.5 % of 1,945 new ads against 26.7 % of the stock.
    _view("bazos", "dum", "prodej", n_active=12_000, n_active_7d=1_945,
          measure_input_gap_share=0.267, measure_input_gap_share_7d=0.335,
          median_price_per_m2=41_000.0, n_ppm2_valued=8_000),
    _view("idnes", "byt", "pronajem", price_per_m2_basis=_RENT, median_price_per_m2=319.0,
          n_ppm2_valued=20_000),
    # No basis, no measure by specification: neither a measures nor a median cell.
    _view("bazos", "ostatni", None, price_per_m2_basis=None, measure_input_gap_share=None,
          measure_input_gap_share_7d=None, median_price_per_m2=None, n_ppm2_valued=0),
]


def _rows(view: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [tuple(row[column] for column in qu.PLAUSIBILITY_COLUMNS) for row in view]


def _baseline(view: list[dict[str, Any]], *, history_d: float | None = 30.0,
              days_ago: float = 7.0,
              then: dict[str, tuple[Any, Any, Any]] | None = None) -> list[tuple[Any, ...]]:
    """_MEDIAN_BASELINE_SQL's rows: each median cell's own reading `days_ago` d back (`then`
    overrides it by subject as (median, basis, n)), after `history_d` d of this check's results."""
    first = None if history_d is None else _NOW - _dt.timedelta(days=history_d)
    at = _NOW - _dt.timedelta(days=days_ago)
    rows = []
    for row in view:
        if row["price_per_m2_basis"] is None or row["median_price_per_m2"] is None:
            continue
        subject = f"{row['source']}/median/{qu._grain(row)}"
        median, basis, n = (then or {}).get(subject, (row["median_price_per_m2"],
                                                      row["price_per_m2_basis"],
                                                      row["n_ppm2_valued"]))
        rows.append((first, subject, at, median, basis, n))
    return rows or [(first, None, None, None, None, None)]


# 10-19 13:09-14:10Z: active ads first seen in 48 h and how many of them no predicate reads.
_PAYLOAD = [("sreality", 1_779, 0), ("bezrealitky", 170, 0), ("mmreality", 82, 0)]

# 10-19 14:06Z, per portal: active ads, re-snapshots in the last 24 h, and the median of the 28
# windows before it (live: every portal OK, 0.11-1.33x).
_CHURN_LIVE: dict[str, tuple[int, int, Any]] = {
    "bazos": (53_426, 204, 258), "bezrealitky": (5_793, 108, 81),
    "ceskereality": (49_543, 196, 320), "idnes": (108_569, 223, 710), "maxima": (277, 2, 3),
    "mmreality": (10_578, 8, 72), "realitymix": (49_476, 83, 256), "remax": (9_475, 42, 56),
    "sreality": (108_507, 898, 2_474), "web": (12, 0, 0),
}


def _churn(**portals: tuple[int, int, Any] | None) -> list[tuple[Any, ...]]:
    """_CHURN_SQL's rows: per portal (active ads, re-snapshots today, each earlier window's count
    or one count for all 28); None drops the portal (no active ad). Empty windows have no row."""
    live = {**_CHURN_LIVE, **portals}
    rows: list[tuple[Any, ...]] = []
    for portal, read in sorted(live.items()):
        if read is None:
            continue
        ads, today, before = read
        days = list(before) if isinstance(before, (list, tuple)) else [before] * qu._CHURN_DAYS
        counted = [(k, n) for k, n in enumerate([today, *days]) if n]
        rows += [(portal, ads, k, n) for k, n in counted] or [(portal, ads, None, None)]
    return rows


_CENSUSES = [{"portal": p, "generated_at": (_NOW - _dt.timedelta(days=1)).isoformat()}
             for p in sorted(claims_common.SOURCES)]

_DEFAULT: dict[str, Any] = {
    "blessed": _BLESSED, "moved": None, "censuses": _CENSUSES, "view": _VIEW, "then": None,
    "history_d": 30.0, "payload": _PAYLOAD, "churn": None,
}


def _answers(w: dict[str, Any]) -> dict[str, Any]:
    view = w["view"]
    unreadable = isinstance(view, BaseException)
    blessed = w["blessed"] if isinstance(w["blessed"], dict) else _BLESSED
    return {
        field_census.FIELD_MATRIX_SQL: _matrix(blessed, w["moved"]),
        qu.PLAUSIBILITY_SQL: view if unreadable else _rows(view),
        qu._MEDIAN_BASELINE_SQL: (w["then"] if w["then"] is not None else
                                  [] if unreadable else
                                  _baseline(view, history_d=w["history_d"])),
        qu._PAYLOAD_SQL: w["payload"],
        qu._CHURN_SQL: w["churn"] if w["churn"] is not None else _churn(),
    }


def _loader(value: Any) -> Any:
    def load() -> Any:
        if isinstance(value, BaseException):
            raise value
        return value
    return load


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> Any:
    """check_parsing over one world: a quiet 10-19 unless overridden."""
    def run(**overrides: Any) -> tuple[dict[str, Any], _Conn]:
        w = {**_DEFAULT, **overrides}
        monkeypatch.setattr(field_census, "load_baseline", _loader(w["blessed"]))
        monkeypatch.setattr(field_census, "load_censuses", _loader(w["censuses"]))
        conn = _Conn(_answers(w), now=_NOW)
        return qu.check_parsing(conn, T), conn
    return run


def _cells(out: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["subject"]: c for c in out["details"]["cells"]}


def _arm(out: dict[str, Any], arm: str) -> dict[str, dict[str, Any]]:
    return {s: c for s, c in _cells(out).items() if f"/{arm}" in s or s == arm}


# --- the quiet pass: every subject, every field ------------------------------------------------


def test_a_quiet_pass_reads_ok_with_every_subject_emitted(check: Any) -> None:
    out, _ = check()
    cells = _cells(out)
    assert out["check_key"] == "parsing" and out["status"] == "ok" and out["value"] == 0
    assert len(cells) == len(out["details"]["cells"]), "subjects are unique"
    fill = [s for s in cells if "/fill/" in s]
    vocabulary = [s for s in cells if "/vocabulary/" in s]
    assert sorted(fill) == ["idnes/fill/condition", "idnes/fill/has_balcony",
                            "remax/fill/condition", "remax/fill/floor", "remax/fill/has_balcony"]
    assert sorted(vocabulary) == ["idnes/vocabulary/condition", "remax/vocabulary/condition"]
    assert sorted(s for s in cells if "/measures/" in s) == [
        "bazos/measures/dum-prodej", "idnes/measures/byt-pronajem", "sreality/measures/byt-prodej"]
    assert sorted(s for s in cells if "/median/" in s) == [
        "bazos/median/dum-prodej", "idnes/median/byt-pronajem", "sreality/median/byt-prodej"]
    assert sorted(s for s in cells if s.endswith("/payload")) == [
        f"{p}/payload" for p in sorted(claims_common.PAYLOAD_SHAPE_PREDICATES)]
    assert sorted(s for s in cells if s.endswith("/churn")) == [
        f"{p}/churn" for p in sorted(claims_common.SOURCES)]
    assert "census" in cells
    assert {c["status"] for c in cells.values()} == {"ok"}
    for subject, c in cells.items():
        assert set(c) == {"subject", "portal", "status", "value", "unit", "message", "facts"}
        assert c["portal"] == (None if subject == "census" else subject.split("/", 1)[0])
        assert c["message"]


def test_the_blessed_baseline_replayed_live_is_green_and_names_its_known_gaps(
        check: Any) -> None:
    """The checked-in baseline (re-blessed in wave 6) as the live matrix is today's production: no
    fill or vocabulary cell moves, every blessed cell has its own, and the known zeros say so."""
    blessed = field_census.load_baseline()
    out, _ = check(blessed=blessed)
    cells = _cells(out)
    fill = {s: c for s, c in cells.items() if "/fill/" in s}
    assert len(fill) == len(blessed["cells"])
    assert len([s for s in cells if "/vocabulary/" in s]) == len(
        [key for key in blessed["cells"]
         if field_census.canonical_values(key.split("/", 1)[1]) is not None])
    assert {c["status"] for s, c in cells.items() if "/fill/" in s or "/vocabulary/" in s} == {"ok"}
    gap = fill["remax/fill/has_balcony"]
    assert gap["facts"]["known_gap"] == "the portal never states it" and gap["facts"]["inert"]
    assert "a known gap" in gap["message"]
    filled = fill["mmreality/fill/has_balcony"]
    assert filled["status"] == "ok" and "known_gap" not in filled["facts"]


def test_a_measured_result_carries_its_cells_on_every_status(check: Any) -> None:
    """Wave 6 deletes the ladder's implicit cell: a cell-less measured result reaches nothing."""
    quiet, _ = check()
    watch, _ = check(censuses=[{"portal": "remax", "generated_at": "2020-01-01T00:00:00+00:00"}])
    problem, _ = check(payload=[("sreality", 1_200, 1_150)])
    assert [r["status"] for r in (quiet, watch, problem)] == ["ok", "warn", "fail"]
    for out in (quiet, watch, problem):
        assert isinstance(out["details"]["cells"], list) and out["details"]["cells"]


# --- fill and vocabulary: the folds of field_fill_matrix and field_census -----------------------


def _one(cells: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    return _blessed({key: cells[key]})


def test_a_fill_collapse_is_problem_and_a_smaller_slip_watch(check: Any) -> None:
    """test_field_census: 88 % -> 60 % fails, 88 % -> 75 % warns (28 pp and 13 pp)."""
    blessed = _blessed({"idnes/condition": _c(1_000, 880, off_canon=0.0)})
    out, _ = check(blessed=blessed, moved={"idnes/condition": {"filled": 600}})
    cell = _cells(out)["idnes/fill/condition"]
    assert cell["status"] == "fail" and out["status"] == "fail"
    assert cell["message"].startswith("fill 88.0% -> 60.0%: -28.0 pp of 1,000 active ads, "
                                      "past 20 pp; once that is confirmed a fix, re-bless with "
                                      "`python -m scraper.field_census --bless`")
    assert cell["value"] == 60.0 and cell["unit"] == "%"
    assert cell["facts"]["blessed_fill"] == 0.88 and cell["facts"]["filled"] == 600
    out, _ = check(blessed=blessed, moved={"idnes/condition": {"filled": 750}})
    assert _cells(out)["idnes/fill/condition"]["status"] == "warn"
    assert _cells(out)["idnes/vocabulary/condition"]["status"] == "ok"


def test_a_field_the_parser_stopped_writing_is_one_problem_cell(check: Any) -> None:
    """field_fill_matrix's fold on the real baseline: idnes stops writing `condition` and the alarm
    reads Problem with that one cell; its vocabulary has nothing off the canon to judge."""
    blessed = field_census.load_baseline()
    out, _ = check(blessed=blessed, moved={"idnes/condition": {"filled": 0}})
    cells = _cells(out)
    assert out["status"] == "fail" and out["value"] == 1
    assert cells["idnes/fill/condition"]["status"] == "fail"
    assert cells["idnes/vocabulary/condition"]["status"] == "ok"
    assert out["message"].startswith("1 Problem — idnes/fill/condition: fill ")


def test_a_weeks_cohort_drift_does_not_flap(check: Any) -> None:
    """09-21's worst honest weekly drift, bezrealitky disposition 55.0 % -> 50.9 %."""
    blessed = _blessed({"bezrealitky/disposition": _c(5_724, 3_148, off_canon=0.0)})
    out, _ = check(blessed=blessed, moved={"bezrealitky/disposition": {"filled": 2_913}})
    assert _cells(out)["bezrealitky/fill/disposition"]["status"] == "ok"


def test_a_partial_break_under_the_absolute_floor_is_a_collapse(check: Any) -> None:
    """ceskereality's furnished key mismatch shape: 5 % -> 1 % is no 10 pp drop, but a collapse."""
    blessed = _blessed({"idnes/furnished": _c(1_000, 50, off_canon=0.0)})
    out, _ = check(blessed=blessed, moved={"idnes/furnished": {"filled": 10}})
    cell = _cells(out)["idnes/fill/furnished"]
    assert cell["status"] == "fail"
    assert cell["message"].startswith("filled 50 -> 10 of 1,000 active ads: under 25% of the "
                                      "blessed count")


def test_a_known_zero_cell_cannot_offend(check: Any) -> None:
    out, _ = check()
    cell = _cells(out)["remax/fill/has_balcony"]
    assert cell["status"] == "ok" and cell["value"] == 0.0
    assert cell["facts"]["known_gap"] == "the portal never states it"


def test_a_zero_cell_the_contract_says_is_written_reads_watch(check: Any) -> None:
    """The legacy check's third finding: a cell blessed and live at zero while the attribute
    contract names a producer for it (neither a known gap nor inert) — Watch, never Problem."""
    blessed = _blessed({"sreality/condition": _c(1_000, 0, off_canon=0.0)})
    out, _ = check(blessed=blessed)
    cell = _cells(out)["sreality/fill/condition"]
    assert cell["status"] == "warn"
    assert cell["message"] == ("no active ad carries it while the attribute contract names a "
                               "producer for it (Watch, never Problem)")


def test_an_off_canon_rise_rings_on_the_vocabulary_cell_and_names_the_spelling(
        check: Any) -> None:
    blessed = _blessed({"remax/condition": _c(1_000, 900, off_canon=0.01)})
    out, _ = check(blessed=blessed, moved={"remax/condition": {
        "off_canon": 0.2, "off_canon_values": ["zdena_kamenna"]}})
    cells = _cells(out)
    vocabulary = cells["remax/vocabulary/condition"]
    assert vocabulary["status"] == "fail" and cells["remax/fill/condition"]["status"] == "ok"
    assert vocabulary["message"].startswith(
        "off the canon 1.0% -> 20.0% (+19.0 pp: zdena_kamenna), past 15 pp; map the spelling in "
        "scraper/vocabulary.py")
    assert vocabulary["facts"]["off_canon_values"] == ["zdena_kamenna"]
    assert vocabulary["value"] == 20.0


def test_a_cell_the_baseline_has_never_seen_arrives_green(check: Any) -> None:
    out, _ = check(moved={"maxima/condition": _c(276, 200, off_canon=0.0)})
    cells = _cells(out)
    assert cells["maxima/fill/condition"]["status"] == "ok"
    assert cells["maxima/fill/condition"]["message"].endswith(
        "not in the blessed baseline yet: the next re-bless adds it")
    assert cells["maxima/vocabulary/condition"]["status"] == "ok"


def test_a_portal_that_leaves_the_matrix_is_problem_on_every_blessed_field(check: Any) -> None:
    """The denominator arm: a portal whose ads leave the measurement must not read as health. The
    vocabulary of a field with no live cell is Not measured; its fill cell judges the absence."""
    out, _ = check(moved={"remax/condition": None, "remax/floor": None,
                          "remax/has_balcony": None})
    cells = _cells(out)
    for field in ("condition", "floor", "has_balcony"):
        cell = cells[f"remax/fill/{field}"]
        assert cell["status"] == "fail" and cell["value"] is None
        assert "all 3 of remax's blessed fields" in cell["message"]
    assert cells["remax/vocabulary/condition"]["status"] == "unknown"
    assert cells["idnes/fill/condition"]["status"] == "ok"
    assert out["status"] == "fail" and out["value"] == 3


def test_one_blessed_field_gone_from_a_measured_portal_is_watch(check: Any) -> None:
    out, _ = check(moved={"remax/floor": None})
    cells = _cells(out)
    assert cells["remax/fill/floor"]["status"] == "warn"
    assert "1 of remax's blessed fields are" in cells["remax/fill/floor"]["message"]
    assert cells["remax/fill/condition"]["status"] == "ok"


def test_an_inert_cell_may_collapse_silently_but_not_go_off_canon(check: Any) -> None:
    """bazos condition: nothing can write it (its gate is closed), so its fall is the deleted
    lane's residue leaving; a new wrong spelling still rings whoever wrote it."""
    blessed = _blessed({"bazos/condition": _c(50_000, 133, off_canon=0.0)})
    out, _ = check(blessed=blessed, moved={"bazos/condition": {"filled": 30}})
    fill = _cells(out)["bazos/fill/condition"]
    assert fill["status"] == "ok" and fill["facts"]["inert"]
    assert "nothing can write it today" in fill["message"]
    out, _ = check(blessed=blessed, moved={"bazos/condition": {
        "filled": 30, "off_canon": 0.2, "off_canon_values": ["smisana"]}})
    assert _cells(out)["bazos/vocabulary/condition"]["status"] == "fail"


def test_the_idnes_balcony_narrowing_read_watch_before_the_rebless(check: Any) -> None:
    """Design :138, before wave 6's re-bless: 26.8 % -> 13.7 % is Watch (wave 4's definition
    change converging, not a parser regression)."""
    blessed = _blessed({"idnes/has_balcony": _c(108_820, 29_164, true=29_164, false=0)})
    out, _ = check(blessed=blessed, moved={"idnes/has_balcony": {"filled": 14_908,
                                                                 "true": 14_908}})
    cell = _cells(out)["idnes/fill/has_balcony"]
    assert cell["status"] == "warn" and cell["message"].startswith("fill 26.8% -> 13.7%")
    assert cell["facts"]["true"] == 14_908 and cell["facts"]["false"] == 0


def test_the_fill_and_vocabulary_lines_are_strict(check: Any) -> None:
    """common.graded's convention: a line is crossed when passed. field_fill_matrix used >=, so
    exactly 10 pp read Watch, exactly 20 pp Problem and exactly 5 pp of off-canon Watch."""
    blessed = _blessed({"idnes/condition": _c(1_000, 650, off_canon=0.01)})

    def status(**moved: Any) -> tuple[str, str]:
        cells = _cells(check(blessed=blessed, moved={"idnes/condition": moved})[0])
        return (cells["idnes/fill/condition"]["status"],
                cells["idnes/vocabulary/condition"]["status"])

    assert status(filled=550) == ("ok", "ok")
    assert status(filled=549) == ("warn", "ok")
    assert status(filled=450) == ("warn", "ok")
    assert status(filled=449) == ("fail", "ok")
    assert status(filled=650, off_canon=0.06) == ("ok", "ok")
    assert status(filled=650, off_canon=0.07) == ("ok", "warn")


def test_the_matrix_is_the_whole_active_stock_read_on_the_clock(check: Any) -> None:
    _, conn = check()
    sent = [(sql, armed) for sql, _, armed in conn.sent if sql is field_census.FIELD_MATRIX_SQL]
    assert sent == [(field_census.FIELD_MATRIX_SQL, True)]
    assert "where l.is_active" in field_census.FIELD_MATRIX_SQL
    assert "first_seen_at" not in field_census.FIELD_MATRIX_SQL


def test_an_unreadable_baseline_leaves_fill_and_vocabulary_not_read(check: Any) -> None:
    out, _ = check(blessed=OSError("data/field_capture/fill_baseline.json is missing"))
    assert not [s for s in _cells(out) if "/fill/" in s or "/vocabulary/" in s]
    assert out["details"]["not_measured"]["fill"].startswith("the blessed baseline is unreadable")
    assert out["status"] == "ok" and "Not read: fill: the blessed baseline" in out["message"]


# --- the key census -----------------------------------------------------------------------------


def test_a_stale_census_is_a_watch_census_cell_and_never_problem(check: Any) -> None:
    """field_fill_matrix's third arm: a census past 30 whole days, or with no readable stamp."""
    stale = [{"portal": "remax", "generated_at": "2026-09-17T19:20:00+00:00"},
             {"portal": "idnes",
              "generated_at": (_NOW - _dt.timedelta(days=30, hours=23)).isoformat()},
             {"portal": "bazos", "generated_at": "never"}]
    out, _ = check(censuses=stale)
    cell = _cells(out)["census"]
    assert cell["status"] == "warn" and out["status"] == "warn" and cell["value"] == 2
    assert cell["facts"]["stale"] == ["remax census is 31d old",
                                      "bazos has no readable generated_at"]
    assert cell["message"] == (
        "remax census is 31d old; bazos has no readable generated_at: a portal may have renamed a "
        "key its census still lists; re-run `python -m scraper.field_census --bless` (Watch past "
        "30 d, never Problem)")


def test_the_census_reads_ok_when_fresh_and_not_measured_when_absent(check: Any) -> None:
    out, _ = check()
    assert _cells(out)["census"]["message"] == (
        f"all {len(claims_common.SOURCES)} portal key censuses are at most 30 d old")
    out, _ = check(censuses=[])
    assert _cells(out)["census"]["status"] == "unknown"
    out, _ = check(censuses=ValueError("Expecting value: line 1 column 1"))
    assert _cells(out)["census"]["message"].startswith("Not measured: a key census is unreadable")


# --- measures of new ads ------------------------------------------------------------------------


def _measures(check: Any, *view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return _arm(check(view=list(view))[0], "measures")


def test_measures_a_portal_shipping_a_regression_is_problem(check: Any) -> None:
    """ppm2_measure_coverage's 'shipping' fold: every new mmreality land ad lost its inputs while
    the stock still has them (70 %)."""
    cells = _measures(check, _view("mmreality", "pozemek", "prodej", price_per_m2_basis=_LAND,
                                   n_active=4_190, n_active_7d=400, measure_input_gap_share=0.30,
                                   measure_input_gap_share_7d=1.0))
    cell = cells["mmreality/measures/pozemek-prodej"]
    assert cell["status"] == "fail" and cell["unit"] == "pp" and cell["value"] == 69.0
    assert cell["message"] == (
        "400 of the 400 ads first seen in 7 d have no price or no positive area (100.0%, lower "
        "bound 99.0%), 69.0 pp above the 30.0% of all 4,190 active ads (Problem past 25 pp)")
    assert cell["facts"]["price_per_m2_basis"] == _LAND


def test_measures_read_a_partial_regression_the_old_line_never_saw(check: Any) -> None:
    """remax's price defect (~44 % of new pages with no price until 08-05) sat under the old 90 %
    gap line for good; against its own 5 % stock it is Problem."""
    cell = _measures(check, _view("remax", "byt", "prodej", n_active=9_000, n_active_7d=480,
                                  measure_input_gap_share=0.05,
                                  measure_input_gap_share_7d=0.44))["remax/measures/byt-prodej"]
    assert cell["status"] == "fail"


def test_measures_watch_and_the_strict_lines(check: Any) -> None:
    def status(stock_gap: float) -> str:
        return _measures(check, _view("idnes", "byt", "prodej", n_active=50_000, n_active_7d=1_000,
                                      measure_input_gap_share=stock_gap,
                                      measure_input_gap_share_7d=0.30))[
            "idnes/measures/byt-prodej"]["status"]

    lower = measures.wilson_lower(300, 1_000)
    assert status(lower - 0.10 + 1e-6) == "ok"
    assert status(lower - 0.10 - 1e-6) == "warn"
    assert status(lower - 0.25 + 1e-6) == "warn"
    assert status(lower - 0.25 - 1e-6) == "fail"


def test_measures_tolerate_ordinary_incompleteness(check: Any) -> None:
    """Fold: bazos komercni/prodej 35.8 % of new ads against 35.8 % of its stock is no regression;
    a cell with 24 new ads is not judged (the old arm needed 200; this one 50)."""
    cells = _measures(check,
                      _view("bazos", "komercni", "prodej", n_active=1_472, n_active_7d=229,
                            measure_input_gap_share=0.358, measure_input_gap_share_7d=0.358),
                      _view("realitymix", "ostatni", "pronajem", n_active=292, n_active_7d=24,
                            measure_input_gap_share=0.894, measure_input_gap_share_7d=0.9))
    assert cells["bazos/measures/komercni-prodej"]["status"] == "ok"
    assert "below the 35.8% of all 1,472 active ads" in cells[
        "bazos/measures/komercni-prodej"]["message"]
    unread = cells["realitymix/measures/ostatni-pronajem"]
    assert unread["status"] == "unknown"
    assert unread["message"] == ("Not measured: 24 ads first seen in 7 d, under the 50 this arm "
                                 "needs")


def test_measures_judge_a_cell_from_fifty_new_ads(check: Any) -> None:
    cells = _measures(check,
                      _view("remax", "byt", "prodej", n_active_7d=49),
                      _view("remax", "dum", "prodej", n_active_7d=50))
    assert cells["remax/measures/byt-prodej"]["status"] == "unknown"
    assert cells["remax/measures/dum-prodej"]["status"] == "ok"


def test_measures_a_standing_gap_is_no_regression(check: Any) -> None:
    """The old stock arm (a land cell 100 % without inputs, Watch) moved to data_quality: here a
    standing gap cancels against itself, and a new-ads gap with nothing behind it reads OK."""
    cells = _measures(check,
                      _view("sreality", "pozemek", "prodej", price_per_m2_basis=_LAND,
                            n_active=20_484, n_active_7d=400, measure_input_gap_share=1.0,
                            measure_input_gap_share_7d=1.0),
                      _view("sreality", "pozemek", "podil", price_per_m2_basis=_LAND,
                            n_active=5_825, n_active_7d=16, measure_input_gap_share=1.0,
                            measure_input_gap_share_7d=1.0))
    assert cells["sreality/measures/pozemek-prodej"]["status"] == "ok"
    assert cells["sreality/measures/pozemek-podil"]["status"] == "unknown"


def test_measures_are_silent_where_the_basis_is_undecidable(check: Any) -> None:
    """No basis, no measure by specification: no cell at all, as the old arm skipped it."""
    out, _ = check(view=[_view("bazos", "ostatni", None, price_per_m2_basis=None,
                               measure_input_gap_share=None, measure_input_gap_share_7d=None,
                               median_price_per_m2=None, n_ppm2_valued=0)])
    assert not [s for s in _cells(out) if "/measures/" in s or "/median/" in s]


def test_the_measures_today_read_level_with_the_stock(check: Any) -> None:
    """10-19: the worst scored cell, bazos dum/prodej, sits 4.8 pp above its stock: OK."""
    cell = _arm(check()[0], "measures")["bazos/measures/dum-prodej"]
    assert cell["status"] == "ok" and 4.0 < cell["value"] < 5.0


# --- the per-m² median --------------------------------------------------------------------------


def _median(check: Any, row: dict[str, Any], **world: Any) -> dict[str, Any]:
    out, _ = check(view=[row], **world)
    return _cells(out)[f"{row['source']}/median/{qu._grain(row)}"]


def test_median_a_heal_reads_problem_in_the_bases_unit(check: Any) -> None:
    """The W2 mmreality heal (5,701 -> 42,500 per m², 7.455x): the arm must notice a step so big."""
    row = _view("mmreality", "dum", "prodej", n_active=3_601, median_price_per_m2=42_500.0,
                n_ppm2_valued=3_400)
    subject = "mmreality/median/dum-prodej"
    cell = _median(check, row, then=_baseline([row], then={subject: (5_701.0, _SALE, 3_400)}))
    assert cell["status"] == "fail" and cell["value"] == 7.455 and cell["unit"] == "×"
    assert cell["message"] == (
        "median 42,500 Kč/m² against 5,701 Kč/m² 7.0 d ago (7.45×, over 3,400 ads carrying it) "
        "(Problem past 3×): a parser change or a heal moved it; confirm which before dismissing")
    assert cell["facts"]["price_per_m2_basis"] == _SALE
    assert cell["facts"]["median_then"] == 5_701.0 and cell["facts"]["ratio"] == 7.455


def test_median_tolerates_the_noisiest_weekly_move(check: Any) -> None:
    row = _view("sreality", "byt", "prodej", median_price_per_m2=95_000.0)
    cell = _median(check, row, then=_baseline([row], then={
        "sreality/median/byt-prodej": (50_000.0, _SALE, 38_000)}))
    assert cell["status"] == "ok" and cell["value"] == 1.9


def test_median_is_not_measured_for_the_checks_first_six_days(check: Any) -> None:
    row = _view("sreality", "byt", "prodej")
    young = _median(check, row, then=_baseline([row], history_d=3.0))
    assert young["status"] == "unknown"
    assert young["message"] == ("Not measured: this check has 3.0 d of its own history, and a "
                                "median is compared with its own reading 6-14 d earlier")
    never = _median(check, row, then=[(None, None, None, None, None, None)])
    assert never["status"] == "unknown" and "0.0 d of its own history" in never["message"]
    assert young["facts"]["median"] == 95_000.0, "the reading is kept: it is next week's baseline"


def test_median_with_history_but_no_reading_6_to_14_days_ago_is_not_measured(check: Any) -> None:
    """The old arm read Watch here (20 d of history, no baseline); no unmeasured path does now."""
    row = _view("sreality", "byt", "prodej")
    cell = _median(check, row, then=[(_NOW - _dt.timedelta(days=20), None, None, None, None,
                                      None)])
    assert cell["status"] == "unknown"
    assert cell["message"] == "Not measured: no reading of this median 6-14 d ago"


def test_median_needs_its_support_in_both_weeks(check: Any) -> None:
    subject = "maxima/median/dum-prodej"
    small_now = _view("maxima", "dum", "prodej", n_active=4_000, n_ppm2_valued=83,
                      median_price_per_m2=5_000.0)
    cell = _median(check, small_now, then=_baseline([small_now], then={
        subject: (45_000.0, _SALE, 4_000)}))
    assert cell["status"] == "unknown"
    assert cell["message"] == ("Not measured: 83 ads carry a per-m² measure now and 4,000 then, "
                               "under the 200 this arm needs in both")
    big_now = dict(small_now, n_ppm2_valued=4_000)
    cell = _median(check, big_now, then=_baseline([big_now], then={subject: (45_000.0, _SALE,
                                                                              83)}))
    assert cell["status"] == "unknown"


def test_median_gates_on_its_support_not_on_the_cells_size(check: Any) -> None:
    """bezrealitky pozemek/prodej: 1,643 active ads of which nine carry a measure, a 17.6x spread.
    The old arm read Watch "compared NOTHING" here; with real support the move is Problem."""
    subject = "bezrealitky/median/pozemek-prodej"
    nine = _view("bezrealitky", "pozemek", "prodej", price_per_m2_basis=_LAND, n_active=1_643,
                 n_ppm2_valued=9, median_price_per_m2=17_573.27)
    cell = _median(check, nine, then=_baseline([nine], then={subject: (3_224.52, _LAND, 9)}))
    assert cell["status"] == "unknown" and cell["value"] is None
    supported = dict(nine, n_ppm2_valued=900)
    cell = _median(check, supported, then=_baseline([supported], then={
        subject: (3_224.52, _LAND, 900)}))
    assert cell["status"] == "fail" and cell["value"] == 5.45
    assert "17,573 Kč/m² pozemku against 3,225 Kč/m² pozemku" in cell["message"]


def test_median_lines_are_strict(check: Any) -> None:
    """ppm2_median_shift used >=: exactly 2x was Watch and exactly 3x Problem."""
    def status(now_median: float) -> str:
        row = _view("idnes", "byt", "prodej", median_price_per_m2=now_median)
        return _median(check, row, then=_baseline([row], then={
            "idnes/median/byt-prodej": (50_000.0, _SALE, 38_000)}))["status"]

    assert status(100_000.0) == "ok" and status(100_005.0) == "warn"
    assert status(150_000.0) == "warn" and status(150_005.0) == "fail"
    assert status(25_000.0) == "ok" and status(16_666.0) == "fail", "a fall counts as a move"


def test_median_a_zero_median_is_not_measured_and_every_other_cell_still_judges(
    check: Any,
) -> None:
    """Land's per-m² price is rounded to 2 dp with no floor, so a 1 Kč placeholder over 201 m²
    reads 0.00: a median of 0, now or then, has no ratio and reads Not measured like an empty
    baseline. A division by it would raise and leave all ~541 parsing cells Not measured."""
    subject = "bazos/median/pozemek-prodej"
    zero = _view("bazos", "pozemek", "prodej", price_per_m2_basis=_LAND, n_active=1_000,
                 median_price_per_m2=0.0, n_ppm2_valued=900)
    out, _ = check(view=[zero], then=_baseline([zero], then={subject: (1_240.0, _LAND, 900)}))
    cell = _cells(out)[subject]
    assert (cell["status"], cell["value"]) == ("unknown", None)
    assert cell["message"] == ("Not measured: the median is 0 now, and a shift is the ratio of two "
                               "positive medians")
    assert cell["facts"]["median"] == 0.0, "the reading is kept: it is next week's baseline"
    assert out["status"] == "ok" and _cells(out)["census"]["status"] == "ok"
    healed = dict(zero, median_price_per_m2=1_240.0)
    cell = _median(check, healed, then=_baseline([healed], then={subject: (0.0, _LAND, 900)}))
    assert (cell["status"], cell["message"]) == ("unknown", "Not measured: the median was 0 "
                                                 "7.0 d ago, and a shift is the ratio of two "
                                                 "positive medians")


def test_median_a_basis_change_is_not_a_shift(check: Any) -> None:
    row = _view("idnes", "byt", "prodej", median_price_per_m2=320.0, price_per_m2_basis=_RENT)
    cell = _median(check, row, then=_baseline([row], then={
        "idnes/median/byt-prodej": (95_000.0, _SALE, 38_000)}))
    assert cell["status"] == "unknown"
    assert cell["message"] == f"Not measured: its basis was {_SALE} 7.0 d ago"


def test_median_renders_each_basis_in_its_own_unit_and_none_without_one(check: Any) -> None:
    """Rule 23: no per-m² number without the basis that names its unit (unit_label)."""
    for basis, value, unit in ((_SALE, 91_535.0, "Kč/m²"), (_RENT, 319.0, "Kč/m²/měs"),
                               (_LAND, 1_240.0, "Kč/m² pozemku")):
        row = _view("idnes", "byt", "prodej", price_per_m2_basis=basis, median_price_per_m2=value)
        cell = _median(check, row)
        assert cell["message"].startswith(f"median {value:,.0f} {unit} against {value:,.0f} "
                                          f"{unit} 7.0 d ago")
        assert cell["facts"]["price_per_m2_basis"] == basis
    odd = _view("idnes", "byt", "prodej", price_per_m2_basis="mixed",
                median_price_per_m2=4_321.0)
    cell = _median(check, odd)
    assert "4,321" not in cell["message"]
    assert cell["message"].startswith("the per-m² median (basis mixed, which has no unit)")


def _stored(out: dict[str, Any], at: _dt.datetime) -> list[tuple[Any, ...]]:
    """What _MEDIAN_BASELINE_SQL returns from one stored result: its median cells with a numeric
    median, as (first, subject, run_at, median, basis, n)."""
    return [(at, c["subject"], at, c["facts"]["median"], c["facts"]["price_per_m2_basis"],
             c["facts"]["n"]) for c in out["details"]["cells"]
            if "/median/" in c["subject"] and isinstance(c["facts"].get("median"), float)]


def test_median_the_cell_a_run_writes_is_next_weeks_baseline(check: Any) -> None:
    """The baseline is this check's own cell facts: written whatever the verdict (a young check's
    first runs are Not measured), read back by subject a week later."""
    before = _view("mmreality", "dum", "prodej", n_active=3_601, median_price_per_m2=5_701.0,
                   n_ppm2_valued=3_400)
    first, _ = check(view=[before], then=_baseline([before], history_d=1.0))
    cell = _cells(first)["mmreality/median/dum-prodej"]
    assert cell["status"] == "unknown"
    assert cell["facts"] == {"median": 5_701.0, "price_per_m2_basis": _SALE, "n": 3_400,
                             "ads": 3_601}
    healed = dict(before, median_price_per_m2=42_500.0)
    week_on, _ = check(view=[healed], then=_stored(first, _NOW - _dt.timedelta(days=7)))
    assert _cells(week_on)["mmreality/median/dum-prodej"]["status"] == "fail"


def test_median_baseline_reads_this_checks_own_array_cells(check: Any) -> None:
    _, conn = check()
    sent = [(sql, params) for sql, params, _ in conn.sent if sql is qu._MEDIAN_BASELINE_SQL]
    assert sent == [(qu._MEDIAN_BASELINE_SQL, {"key": "parsing", "young_d": 6, "old_d": 14,
                                               "arm": "/median/"})]
    sql = qu._MEDIAN_BASELINE_SQL
    assert "jsonb_typeof(r.details -> 'cells') = 'array'" in sql
    assert "'object'" not in sql and "ppm2_median_shift" not in sql
    assert "jsonb_typeof(c -> 'facts' -> 'median') = 'number'" in sql
    assert "-> 'facts' ->> 'price_per_m2_basis'" in sql


# --- payload shape ------------------------------------------------------------------------------


def _payload(check: Any, rows: list[tuple[str, int, int]]) -> dict[str, dict[str, Any]]:
    return {s: c for s, c in _cells(check(payload=rows)[0]).items() if s.endswith("/payload")}


def test_payload_a_source_side_change_is_problem_with_its_remedy(check: Any) -> None:
    cells = _payload(check, [("sreality", 1_200, 1_150), ("bezrealitky", 80, 0)])
    cell = cells["sreality/payload"]
    assert cell["status"] == "fail" and cell["value"] == 95.83 and cell["unit"] == "%"
    assert cell["message"].startswith(
        "1,150 of 1,200 active ads first seen in 48 h carry a location payload the contract cannot "
        "read (95.8%) (Problem past 10%): ")
    assert cell["message"].endswith(claims_common.PAYLOAD_SHAPE_REMEDIES["sreality"])
    assert "sreality_payload_shape" in cell["message"]
    assert cells["bezrealitky/payload"]["status"] == "ok"
    assert cell["facts"] == {"ads": 1_200, "unreadable": 1_150, "window_h": 48}


def test_payload_rings_on_the_first_pass_after_a_cutover(check: Any) -> None:
    """The window is the latency: one 6-hourly pass into a 48 h window reads 12.5 %, past fail."""
    assert 6 / qu._PAYLOAD_WINDOW_H > T["parsing_payload_fail"]
    assert _payload(check, [("sreality", 800, 100)])["sreality/payload"]["status"] == "fail"


def test_payload_names_each_portals_own_remedy(check: Any) -> None:
    cells = _payload(check, [("sreality", 1_200, 0), ("bezrealitky", 90, 60),
                             ("mmreality", 80, 8)])
    assert cells["bezrealitky/payload"]["status"] == "fail"
    assert "_DETAIL_QUERY" in cells["bezrealitky/payload"]["message"]
    assert cells["mmreality/payload"]["status"] == "warn"
    assert cells["mmreality/payload"]["message"].endswith(
        claims_common.PAYLOAD_SHAPE_REMEDIES["mmreality"])


def test_payload_clean_new_ads_read_ok(check: Any) -> None:
    cells = _payload(check, [("sreality", 1_200, 3), ("bezrealitky", 80, 1), ("mmreality", 82, 0)])
    assert {c["status"] for c in cells.values()} == {"ok"}
    assert cells["sreality/payload"]["value"] == 0.25
    assert cells["bezrealitky/payload"]["value"] == 1.25
    assert "Problem" not in cells["sreality/payload"]["message"]


def test_payload_under_thirty_new_ads_is_not_measured_and_the_rest_still_judges(
        check: Any) -> None:
    out, _ = check(payload=[("sreality", 10, 10)])
    cells = _cells(out)
    assert cells["sreality/payload"]["status"] == "unknown"
    assert cells["sreality/payload"]["message"] == (
        "Not measured: 10 active ads first seen in 48 h, under the 30 this arm needs")
    assert cells["bezrealitky/payload"]["status"] == "unknown", "no row: no ad first seen in 48 h"
    assert out["status"] == "ok"


def test_payload_lines_are_strict(check: Any) -> None:
    """location_payload_shape_drift used >=: exactly 10 % was Problem and exactly 3 % Watch."""
    assert _payload(check, [("sreality", 1_000, 100)])["sreality/payload"]["status"] == "warn"
    assert _payload(check, [("sreality", 1_000, 101)])["sreality/payload"]["status"] == "fail"
    assert _payload(check, [("sreality", 1_000, 30)])["sreality/payload"]["status"] == "ok"
    assert _payload(check, [("sreality", 1_000, 31)])["sreality/payload"]["status"] == "warn"


def test_payload_binds_every_predicate_to_its_portal_by_parameter(check: Any) -> None:
    """Rule 21: the predicates come from location_data, the portal names are parameters."""
    _, conn = check()
    sent = [(sql, params) for sql, params, _ in conn.sent if sql is qu._PAYLOAD_SQL]
    assert len(sent) == 1
    params = sent[0][1]
    portals = list(claims_common.PAYLOAD_SHAPE_PREDICATES)
    assert params["portals"] == portals and params["window_h"] == 48
    assert [params[f"shape_{i}"] for i in range(len(portals))] == portals
    for i, predicate in enumerate(claims_common.PAYLOAD_SHAPE_PREDICATES.values()):
        assert f"when %(shape_{i})s then coalesce({predicate}, false)" in qu._PAYLOAD_SQL
    assert not _PORTAL.search(qu._PAYLOAD_SQL)
    assert "and l.is_active" in qu._PAYLOAD_SQL
    assert "make_interval(hours => %(window_h)s::int)" in qu._PAYLOAD_SQL


# --- churn --------------------------------------------------------------------------------------


def _churned(check: Any, **portals: Any) -> dict[str, dict[str, Any]]:
    return {s: c for s, c in _cells(check(churn=_churn(**portals))[0]).items()
            if s.endswith("/churn")}


def test_churn_today_reads_ok_everywhere(check: Any) -> None:
    cells = _churned(check)
    assert {c["status"] for c in cells.values()} == {"ok"}
    sreality = cells["sreality/churn"]
    assert sreality["value"] == 0.36 and sreality["unit"] == "×"
    assert sreality["facts"] == {"resnaps_24h": 898, "ads": 108_507, "ratio": 0.0083,
                                 "median_resnaps_28d": 2_474, "median_ratio_28d": 0.0228,
                                 "multiple": 0.36}
    assert sreality["message"] == (
        "898 re-snapshots of existing ads in 24 h (0.0083 per active ad) against a median day of "
        "2,474 (0.0228) over the last 28 days: 0.36×")
    assert cells["maxima/churn"]["message"].endswith("; under the 500 a storm needs")


def test_churn_a_storm_against_the_portals_own_median(check: Any) -> None:
    """10-01's idnes window: 8,868 re-snapshots against its 710 median day is 12.5x, Problem."""
    cell = _churned(check, idnes=(108_569, 8_868, 710))["idnes/churn"]
    assert cell["status"] == "fail" and cell["value"] == 12.49
    assert cell["message"].endswith(
        "(Problem past 6×): a volatile hashed field may be re-snapshotting the stock, or a heal's "
        "refetch wave ran")
    assert _churned(check, bazos=(53_426, 1_109, 258))["bazos/churn"]["status"] == "warn"


def test_churn_lines_are_strict_relative_and_need_five_hundred(check: Any) -> None:
    def status(today: int, median: int = 1_000) -> str:
        return _churned(check, idnes=(108_569, today, median))["idnes/churn"]["status"]

    assert status(3_000) == "ok" and status(3_001) == "warn"
    assert status(6_000) == "warn" and status(6_001) == "fail"
    assert status(499, median=10) == "ok", "under 500 re-snapshots is no storm at any multiple"
    assert status(500, median=10) == "fail"


def test_churn_a_portal_whose_median_day_is_quiet_rings_on_a_storm(check: Any) -> None:
    cell = _churned(check, remax=(9_475, 900, 0))["remax/churn"]
    assert cell["status"] == "fail" and cell["value"] is None
    assert "its median day wrote none" in cell["message"]
    quiet = _churned(check, remax=(9_475, 0, 0))["remax/churn"]
    assert quiet["status"] == "ok" and quiet["value"] is None


def test_churn_counts_an_empty_window_as_a_zero_day(check: Any) -> None:
    """ceskereality wrote no snapshot on 09-30 and 10-01: those windows have no row, and count."""
    days = [0] * 15 + [100] * 13
    cell = _churned(check, ceskereality=(49_543, 600, days))["ceskereality/churn"]
    assert cell["facts"]["median_resnaps_28d"] == 0 and cell["status"] == "fail"


def test_churn_a_portal_with_no_active_ad_is_not_measured(check: Any) -> None:
    cell = _churned(check, maxima=None)["maxima/churn"]
    assert cell["status"] == "unknown"
    assert cell["message"] == "Not measured: the portal has no active ad"


def test_churn_reads_re_snapshots_only_over_twenty_nine_windows(check: Any) -> None:
    _, conn = check()
    sent = [params for sql, params, _ in conn.sent if sql is qu._CHURN_SQL]
    assert sent == [{"days": 28}]
    assert "and p.scraped_at < s.scraped_at" in qu._CHURN_SQL
    assert "make_interval(days => %(days)s::int + 1)" in qu._CHURN_SQL
    assert "where l.is_active" in qu._CHURN_SQL


# --- the result, the statements and the pass memo -----------------------------------------------


def test_the_result_is_the_worst_cell_and_counts_the_cells_at_it(check: Any) -> None:
    out, _ = check(payload=[("sreality", 1_200, 1_150), ("bezrealitky", 90, 60),
                            ("mmreality", 82, 0)],
                   censuses=[{"portal": "remax", "generated_at": "2020-01-01T00:00:00+00:00"}])
    assert out["status"] == "fail" and out["value"] == 2
    assert out["message"].startswith("2 Problem — sreality/payload: ")
    assert "1 Watch — census: " in out["message"]


def _statements() -> set[str]:
    return {common.CLOCK_SQL, field_census.FIELD_MATRIX_SQL, qu.PLAUSIBILITY_SQL,
            qu._MEDIAN_BASELINE_SQL, qu._PAYLOAD_SQL, qu._CHURN_SQL}


def test_every_statement_runs_on_the_per_check_clock(check: Any) -> None:
    _, conn = check()
    assert {sql for sql, _, _ in conn.sent} == _statements()
    assert all(armed for _, _, armed in conn.sent)
    assert len(conn.sent) == len(_statements()), "each statement once"


def test_an_unreadable_view_is_not_measured_and_every_other_arm_still_judges(check: Any) -> None:
    out, conn = check(view=RuntimeError('relation "measure_plausibility_by_source" does not exist'))
    assert not [s for s in _cells(out) if "/measures/" in s or "/median/" in s]
    why = out["details"]["not_measured"]
    assert why["measures"] == why["median"] == (
        'measure_plausibility_by_source is unreadable: relation '
        '"measure_plausibility_by_source" does not exist')
    assert qu._MEDIAN_BASELINE_SQL not in {sql for sql, _, _ in conn.sent}
    assert out["status"] == "ok" and _cells(out)["census"]["status"] == "ok"


def test_an_empty_view_says_why(check: Any) -> None:
    out, _ = check(view=[])
    assert out["details"]["not_measured"]["measures"] == (
        "measure_plausibility_by_source returned no rows: no active ad, or its "
        "is_platform_admin() gate refuses this session")


def test_the_view_is_read_once_per_pass_and_its_failure_with_it() -> None:
    """data_quality runs after parsing in a pass and reads the memo parsing filled; two passes
    read twice, and outside a pass every call reads."""
    conn = _Conn({qu.PLAUSIBILITY_SQL: _rows(_VIEW)}, now=_NOW)
    clock.PASS.start()
    try:
        first, second = qu._plausibility(conn), qu._plausibility(conn)
    finally:
        clock.PASS.reset()
    assert first == second and first[1] is None and len(first[0]) == len(_VIEW)
    assert first[0][0]["source"] == "sreality" and first[0][0]["n_active"] == 40_000
    assert len(conn.sent) == 1
    clock.PASS.start()
    try:
        qu._plausibility(conn)
    finally:
        clock.PASS.reset()
    qu._plausibility(conn)
    assert len(conn.sent) == 3
    broken = _Conn({qu.PLAUSIBILITY_SQL: RuntimeError("canceling statement due to "
                                                      "statement timeout")}, now=_NOW)
    clock.PASS.start()
    try:
        failed = [qu._plausibility(broken) for _ in range(2)]
    finally:
        clock.PASS.reset()
    assert failed[0] == failed[1] == ([], "measure_plausibility_by_source is unreadable: "
                                          "canceling statement due to statement timeout")
    assert len(broken.sent) == 1


def test_parsing_fills_the_memo_its_pass_shares(check: Any) -> None:
    clock.PASS.start()
    try:
        _, conn = check()
        rows, why = qu._plausibility(conn)
    finally:
        clock.PASS.reset()
    assert why is None and len(rows) == len(_VIEW)
    assert sum(sql is qu.PLAUSIBILITY_SQL for sql, _, _ in conn.sent) == 1


def test_the_view_rows_convert_numerics_to_float_and_keep_counts() -> None:
    from decimal import Decimal

    row = dict(_view("sreality", "byt", "prodej"), measure_input_gap_share=Decimal("0.0400"),
               median_price_per_m2=Decimal("95000.5"))
    rows, why = qu._read_plausibility(_Conn({qu.PLAUSIBILITY_SQL: _rows([row])}, now=_NOW))
    assert why is None and rows[0]["measure_input_gap_share"] == 0.04
    assert rows[0]["median_price_per_m2"] == 95_000.5 and isinstance(rows[0]["n_active"], int)


def test_the_grain_keeps_a_missing_category_visible() -> None:
    assert qu._grain({"category_main": "byt", "category_type": "prodej"}) == "byt-prodej"
    assert qu._grain({"category_main": None, "category_type": "prodej"}) == "?-prodej"


# --- rails --------------------------------------------------------------------------------------


def test_the_module_never_imports_the_harness_or_the_worker() -> None:
    """The harness imports this check, so importing it back is a cycle; the worker is a lane."""
    tree = ast.parse(inspect.getsource(qu))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not imported & {"scripts.verify_pipeline", "scraper.realtime_worker",
                           "toolkit.system_alerts"}


def test_the_parsing_check_reads_exactly_the_thresholds_the_contract_names() -> None:
    import re

    read = set(re.findall(r"""thresholds\[["'](\w+)["']\]""", _parsing_source()))
    assert read == set(_CONTRACT)


def test_the_harness_declares_every_threshold_this_check_reads_with_the_contracts_value() -> None:
    from scripts.verify_pipeline import THRESHOLDS

    assert {key: THRESHOLDS.get(key) for key in _CONTRACT} == _CONTRACT


def test_parsing_is_registered_as_the_portals_alarm_on_the_worker() -> None:
    from scripts.verify_pipeline import CHECKS

    row = next(c for c in CHECKS if c.key == "parsing")
    assert row.fn is qu.check_parsing
    assert (row.domain, row.cls, row.host, row.cadence_s, row.label, row.unit, row.owner_path,
            row.budget_s) == ("portals", "alarm", "realtime-worker", 21_600, "Parsing", "cells",
                              "/scrapers", 180.0)


def test_no_portal_is_spelled_in_the_parsing_literals() -> None:
    """Rule 21: the portal lists come from location_data, names in SQL are bound parameters."""
    assert _hits(_parsing_source()) == []


def test_the_parsing_code_spells_no_per_m2_unit_and_divides_no_price_by_an_area() -> None:
    """Rule 23: the median's unit comes from toolkit.measures.unit_label, never a literal, and the
    figure from the measure view, never price / area (the census's own scanner, comments
    stripped)."""
    hits = _scan("scripts/checks/quality.py", strip_python_comments(_parsing_source()))
    assert [h for h in hits if h.arm in (ARM_UNIT, ARM_DIVISION)] == []
    assert "unit_label" in _parsing_source()


def test_imported_statements_are_sent_as_their_own_text(check: Any) -> None:
    """Imported, never copied: the matrix statement sent IS field_census's object."""
    _, conn = check()
    assert any(sql is field_census.FIELD_MATRIX_SQL for sql, _, _ in conn.sent)
    tree = ast.parse(inspect.getsource(qu))
    names = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
             for a in n.names}
    assert {("location_data.claims_common", "PAYLOAD_SHAPE_PREDICATES"),
            ("location_data.claims_common", "PAYLOAD_SHAPE_REMEDIES"),
            ("location_data.claims_common", "SOURCES"),
            ("toolkit.measures", "unit_label")} <= names

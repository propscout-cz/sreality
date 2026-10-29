"""The broker board (api/broker_board.py) through a scripted connection: its arguments, its refusals and the Python step
(MIN_N, the Wilson rank, the order, the hydration). What the statement selects is tests/test_broker_board_live.py's."""

from __future__ import annotations

from typing import Any

import pytest

from api import broker_board as bb
from api.broker_board import BoardArgs
from api.capabilities import registry
from api.capabilities.entry import Caller
from api.capabilities.schema import portable
from api.filter_args import agenda_ids
from tests.api._script_conn import ScriptConn
from tests.api.test_capabilities_census import _distinct
from toolkit import filter_registry as fr
from toolkit.measures import EXIT_CLEAN_SINCE, EXIT_MARGIN_DAYS, wilson_lower
from toolkit.stats_registry import MIN_N

# The board's own options, beside the registry filters (frontend/src/lib/brokerBoard.test.ts pins the same names).
OPTIONS = {"bounds", "horizon_days", "sort", "min_properties", "limit", "firm_ids", "broker_id"}


def is_board(sql: str) -> bool:
    return "FROM c JOIN broker_book b USING (property_id)" in sql


def head(**over: Any) -> dict[str, Any]:
    """The board statement's one row with no broker: the cohort's totals."""
    return {"cohort_properties": 0, "category_mains": 1, "category_types": 1, "data_freshness": None,
            "uncovered_portals": [], "attributed_properties": 0, "market_exited": 0, "market_not_exited": 0,
            "market_unknown": 0, "broker_id": None, "properties": None, "exited": None, "not_exited": None,
            "unknown": None, **over}


def _row(broker: int, properties: int, exited: int, not_exited: int, unknown: int = 0, **totals: Any) -> dict:
    return {**head(**totals), "broker_id": broker, "properties": properties, "exited": exited,
            "not_exited": not_exited, "unknown": unknown}


def _names(broker: int, name: str = "RK Alfa", **contact: Any) -> list[dict[str, Any]]:
    return [{"broker_id": broker, "display_name": name, "firm_id": None, "firm_name": None, "firm_domain": None,
             **contact}]


def _run(args: dict[str, Any], rows: list[dict[str, Any]] | None = None, *, contacts: bool = True,
         names: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], ScriptConn]:
    conn = ScriptConn([(is_board, rows or [head()]), (lambda sql: "FROM brokers_public" in sql, names or [])])
    board_args = BoardArgs.model_validate({"category_type": "prodej", **args})
    return bb.board(conn, board_args, contacts=contacts), conn


def test_the_board_takes_browses_filters_but_the_place_composite_and_the_lenses() -> None:
    """The SPA's request is held to the same registry (lib/brokerBoard.test.ts): one source for both languages."""
    curation = {f.id for f in fr.all_filters() if f.category == fr.CATEGORY_CURATION}
    assert curation == bb.LENSES == {"tags", "collections", "pipeline", "with_estimates", "show_dismissed"}
    assert set(BoardArgs.model_fields) == set(agenda_ids(fr.Agenda.BROWSE)) - {"location"} - curation | OPTIONS
    assert BoardArgs.model_fields["category_type"].is_required()


def test_every_board_filter_is_classified_for_speed() -> None:
    """A Browse filter the board takes with no stated bearing on speed could select on the outcome unnoticed."""
    assert not bb.OUTCOME & bb.NOT_OUTCOME
    assert bb.OUTCOME | bb.NOT_OUTCOME == set(bb.FILTERS)


@pytest.mark.parametrize("fid", sorted(bb.FILTERS))
def test_an_outcome_filter_refuses_speed_and_no_other_does(fid: str) -> None:
    """Each filter set to a value of its own schema's (the capability census's builder)."""
    current = "prodej" if fid == "category_type" else BoardArgs.model_fields[fid].default
    out, _ = _run({fid: _distinct(portable(BoardArgs)["properties"][fid], current)})
    outcome = fid in bb.OUTCOME
    assert (out["metadata"]["speed_refused"], out["metadata"]["speed_refused_by"]) == (
        (bb.OUTCOME_SELECTED, [fid]) if outcome else (None, []))


@pytest.mark.parametrize("args, totals, refused, by", [
    ({"tom_days_max": 30, "status": "active", "portals_excluded": ["idnes"], "portals_excluded_active_only": True},
     {}, bb.OUTCOME_SELECTED, ["status", "tom_days_max", "portals_excluded_active_only"]),
    ({"status": "active"}, {"category_types": 2}, bb.OUTCOME_SELECTED, ["status"]),
    ({}, {"category_mains": 2}, bb.MIXED_CATEGORY, ["category_main_in"]),
    ({}, {"category_mains": 3, "category_types": 2}, bb.MIXED_CATEGORY, ["category_type", "category_main_in"]),
    ({}, {"category_mains": 0, "category_types": 0}, None, []),
], ids=["every outcome filter set, in the agenda's order", "an outcome filter over a mixed cohort", "two property types",
        "two of each", "an empty cohort"])
def test_a_refusal_names_the_filters_to_change_and_volume_still_answers(
        args: dict[str, Any], totals: dict[str, Any], refused: str | None, by: list[str]) -> None:
    out, _ = _run(args, [_row(1, 9, 5, 1, **totals)])
    assert (out["metadata"]["speed_refused"], out["metadata"]["speed_refused_by"]) == (refused, by)
    assert (out["data"][0]["share"] is None) is (refused is not None)
    assert out["data"][0]["properties"] == 9


def test_speed_answers_from_min_n_followed_properties_and_ranks_by_the_wilson_lower_bound() -> None:
    totals = {"cohort_properties": 40, "attributed_properties": 30, "market_exited": 7, "market_not_exited": 5,
              "market_unknown": 2}
    out, _ = _run({}, [_row(1, 12, 4, 1, 2, **totals), _row(2, 9, 2, 2, **totals)])
    one, two = sorted(out["data"], key=lambda r: r["broker_id"])
    assert (one["n"], one["exited"], one["unknown"], one["share"], one["rank"]) == (5, 4, 2, 0.8, wilson_lower(4, 5))
    assert (two["n"], two["share"], two["rank"]) == (4, None, None)
    meta = out["metadata"]
    assert (meta["market"], meta["unknown"], meta["unattributed_share"]) == ({"n": 12, "exited": 7, "share": 7 / 12},
                                                                              2, 0.25)
    assert (meta["min_n"], meta["clean_exits_since"], meta["margin_days"], meta["horizon_days"]) == (
        MIN_N, EXIT_CLEAN_SINCE, EXIT_MARGIN_DAYS, 30)

    empty, conn = _run({})
    assert empty["data"] == [] and conn.executed[-1][1] == {"ids": []}
    assert empty["metadata"]["unattributed_share"] is None and empty["metadata"]["market"]["share"] is None


@pytest.mark.parametrize("sort, order", [("properties", [1, 3, 2, 5, 4]), ("speed", [2, 4, 1, 3, 5])])
def test_the_order_and_its_limit(sort: str, order: list[int]) -> None:
    """properties: most first, ties on broker_id; speed: the ranked first (highest rank first), then the rest by
    properties. Only the rows kept are hydrated."""
    rows = [_row(1, 20, 1, 3), _row(2, 10, 5, 0), _row(3, 15, 0, 0), _row(4, 5, 4, 1), _row(5, 10, 0, 0)]
    out, _ = _run({"sort": sort}, rows)
    assert [r["broker_id"] for r in out["data"]] == order
    limited, conn = _run({"sort": sort, "limit": 2}, rows)
    assert [r["broker_id"] for r in limited["data"]] == order[:2] and conn.executed[-1][1] == {"ids": order[:2]}


@pytest.mark.parametrize("contacts", [True, False])
def test_the_rows_carry_the_ledgers_broker_fields(contacts: bool) -> None:
    contact = {"primary_email": "a@alfa.cz", "primary_phone": "420700000000"} if contacts else {}
    out, conn = _run({}, [_row(1, 3, 0, 0), _row(2, 2, 0, 0)], contacts=contacts, names=_names(1, **contact))
    assert ("primary_email" in conn.executed[-1][0]) is contacts
    assert list(out["data"][0]) == ["broker_id", "display_name", "firm_id", "firm_name", "firm_domain", *contact,
                                    "properties", "n", "exited", "unknown", "share", "rank"]
    assert out["data"][0]["display_name"] == "RK Alfa" and out["data"][1]["display_name"] is None


@pytest.mark.parametrize("name", ["RK Alfa", "leak@rk.cz", "Kancelář Honzík <info@honzik.cz>", "+420 777 123 456"])
def test_rank_brokers_is_the_board_without_contacts_firms_or_a_broker_scope(name: str) -> None:
    """Masked as a non-admin's tab: two live brokers' display_name IS their e-mail (toolkit.brokers._redact_shaped)."""
    entry = registry.ENTRIES["rank_brokers"]
    assert set(entry.args.model_fields) == set(BoardArgs.model_fields) - {"firm_ids", "broker_id"}
    assert entry.input_schema["properties"]["limit"]["maximum"] == 50
    conn = ScriptConn([(is_board, [_row(1, 6, 5, 1)]), (lambda sql: "FROM brokers_public" in sql, _names(1, name))])
    out = registry.call(Caller(market=conn), "rank_brokers", {"category_type": "prodej", "sort": "speed"})
    assert "primary_email" not in conn.executed[-1][0]
    (row,) = out["data"]
    assert (row["properties"], row["n"], row["share"], row["rank"]) == (6, 6, 5 / 6, wilson_lower(5, 6))
    assert (row["display_name"] == name) is (name == "RK Alfa")
    assert "@" not in row["display_name"] and "777" not in row["display_name"]
    assert (out["metadata"]["tool"], out["metadata"]["pii_masked"]) == ("broker_board", True)

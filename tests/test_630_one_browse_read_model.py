"""Migration 630's rails (ONE PIN W1): the map reads browse_list and the swap re-patches identity changes, offline."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
PATH = MIGRATIONS / "630_one_browse_read_model.sql"
SQL = PATH.read_text(encoding="utf-8")

# The bodies 630 restates, md5 of pg_proc.prosrc read live 2026-10-19 15:32Z (W0).
LIVE = {"rebuild_browse_list": "be7b0ec2297d269f495a42fdcc474517",
        "browse_map_cells": "7406f8566504c33efc7fe4273b4f76cc"}
SOURCE_OF = {"rebuild_browse_list": "605_street_chip_level.sql",
             "browse_map_cells": "629_curated_set_scope.sql"}
MAP_INDEX = ("create index browse_list_next_map_idx on browse_list_next "
             "(category_main, category_type, lat, lng) include (property_id, is_active, price_czk, "
             "disposition, subtype, area_m2, last_seen_at, first_seen_at) "
             "where lat is not null and lng is not null")
MAP_RENAME = "alter index browse_list_next_map_idx rename to browse_list_map_idx"
_DROP = re.compile(r"drop table if exists (?:public\.)?browse_list\b")
_RENAME = re.compile(r"alter table (?:public\.)?browse_list_next rename to browse_list\b")


def _code(text: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", text).split()).lower()


def _statement(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _body(text: str, fn: str) -> str:
    return _statement(text, fn).split("$function$")[1]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _source(fn: str) -> str:
    return (MIGRATIONS / SOURCE_OF[fn]).read_text(encoding="utf-8")


def _missing_tokens(old: str, new: str) -> list[str]:
    """The tokens of `old` that `new` does not carry in order: a restatement may only add."""
    it = iter(new.split())
    return [t for t in old.split() if not any(t == u for u in it)][:5]


def test_the_repatch_runs_after_the_drop_and_before_the_rename() -> None:
    """RED by: reading the ledger before the DROP holds its lock (a merge in between is lost), or a second read."""
    rebuild = _code(_body(SQL, "rebuild_browse_list"))
    drop, rename = _DROP.search(rebuild), _RENAME.search(rebuild)
    assert drop and rename and drop.end() < rename.start()
    window = rebuild[drop.end(): rename.start()]
    for needle in ("delete from browse_list_next", "insert into browse_list_next",
                   "browse_projection", "property_merge_events"):
        assert needle in window, f"{needle!r} is not between the DROP and the rename"
    deletes = "delete from browse_list_next"
    assert rebuild.count(deletes) == window.count(deletes), "a re-patch DELETE sits outside the window"
    assert "property_merge_events" not in rebuild[: drop.start()], (
        "the id read runs once, after the DROP: a read before it adds a scan, never a complete set")
    assert rebuild.count("from property_merge_events") == 1, "the ledger is read once"
    assert "<> all(" not in rebuild, "a per-row array scan under the lock"
    assert "created_at" in rebuild and "undone_at" in rebuild, (
        "the window must name merges (created_at) and their undos/splits (undone_at)")


def test_the_map_index_is_built_and_renamed_like_its_siblings() -> None:
    """RED by: a key, INCLUDE or partial WHERE other than the planned one, or a rename before the table's."""
    rebuild = _code(_body(SQL, "rebuild_browse_list"))
    assert rebuild.count(MAP_INDEX) == 1 and rebuild.count(MAP_RENAME) == 1
    order = [rebuild.index(MAP_INDEX), rebuild.index("analyze browse_list_next"),
             _DROP.search(rebuild).start(), _RENAME.search(rebuild).start(),
             rebuild.index(MAP_RENAME)]
    assert order == sorted(order)


@pytest.mark.parametrize("fn", sorted(LIVE))
def test_each_restatement_only_adds_to_its_source(fn: str) -> None:
    """RED by: a restated body that loses or reorders any of its source's code, the map's one FROM aside."""
    old = _code(_body(_source(fn), fn)).replace("from properties_map_mv l", "from browse_list l")
    assert _missing_tokens(old, _code(_body(SQL, fn))) == []


def test_the_map_rpc_counts_mappable_browse_list_rows_only() -> None:
    """RED by: reading the matview, or dropping the lat/lng conjunct (~53k rows with no point, one NULL tile)."""
    body = _body(SQL, "browse_map_cells")
    code = _code(body)
    assert "properties_map_mv" not in code
    at = code.index("from browse_list l")
    where = code[at: code.index("group by 1, 2", at)]
    assert re.search(r"\bl\.lat is not null\b", where) and re.search(r"\bl\.lng is not null\b", where)
    assert "one index-only pass" not in body.lower(), "the comment still claims an index-only pass"


def test_the_preconditions_name_the_live_bodies_and_its_own() -> None:
    """RED by: a gate missing a source body (drift overwritten) or 630's own (a re-run refuses)."""
    pre = SQL[SQL.index("do $pre$"): SQL.index("$pre$;")]
    for fn, live in LIVE.items():
        assert live in pre, f"{fn}: the live body's md5 is not gated"
        assert _md5(_body(SQL, fn)) in pre, f"{fn}: a re-run would refuse its own body"


@pytest.mark.parametrize("fn", sorted(LIVE))
def test_the_gated_md5_is_the_source_files_body(fn: str) -> None:
    """The LIVE pins are the bodies of the files 630 restates, not a transcription."""
    assert _md5(_body(_source(fn), fn)) == LIVE[fn]


def test_a_revert_restores_605s_rebuild_and_629s_map_rpc() -> None:
    """The revert re-creates both source bodies byte for byte."""
    reverts = sorted((MIGRATIONS / "reverts").glob("630_*.sql"))
    assert len(reverts) == 1, f"expected one migrations/reverts/630_*.sql, found {reverts}"
    text = reverts[0].read_text(encoding="utf-8")
    for fn, live in LIVE.items():
        assert _md5(_body(text, fn)) == live, f"{reverts[0].name} does not restore {fn}"


def test_the_file_forces_no_rebuild() -> None:
    """The next */15 tick builds the index; a forced rebuild would hold browse_list for ~62 s at apply."""
    assert not re.search(r"(select|perform)\s+(public\.)?rebuild_browse_list\(\)", _code(SQL))

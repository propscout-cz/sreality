"""Migration 694: the map area is a box or a true circle, tested by ONE predicate. Offline, text only.

`public.in_map_area` is the spatial predicate; browse_list_visible (629) takes the box and the circle, and the two
stats RPCs (678, 662) take the circle. Each reader is its latest file's statement with exactly the named edits and
nothing else moved; what the predicate answers is tests/test_in_map_area_live.py's.
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS, latest_definition

NAME = "694_true_circle_map_area.sql"
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
SOURCES = {
    "browse_list_visible": ("629_curated_set_scope.sql", "$$"),
    "browse_stats_properties": ("678_stats_small_samples.sql", "$function$"),
    "browse_map_cells": ("662_status_filter_contract.sql", "$function$"),
}
# Each predecessor's own $post$ gate: the body it left live.
LIVE = {
    "browse_list_visible": "a19987a95f57dd9e54cb8100314f8b11",
    "browse_stats_properties": "7d506193953da1877f1acfbf5871d362",
    "browse_map_cells": "c4ac8e166b0a6521fcd48595c48fd067",
}
AREA_CALL = (
    "      -- migration 694: the map area, a box or a circle, through the one spatial predicate.\n"
    "      and public.in_map_area(l.lat, l.lng, bbox_west, bbox_south, bbox_east, bbox_north,\n"
    "                             circle_lat, circle_lng, circle_radius_m)\n"
)
BAND = (
    "      and (bbox_west  is null or l.lng >= bbox_west)\n"
    "      and (bbox_east  is null or l.lng <= bbox_east)\n"
    "      and (bbox_south is null or l.lat >= bbox_south)\n"
    "      and (bbox_north is null or l.lat <= bbox_north)\n"
)
CIRCLE_ARGS = (
    "  status_filter text default 'any'::text\n)\n",
    "  status_filter text default 'any'::text,\n"
    "  -- migration 694: the circle (centre + radius in metres), the other map area; never sent with a bbox.\n"
    "  circle_lat double precision default null::double precision,\n"
    "  circle_lng double precision default null::double precision,\n"
    "  circle_radius_m double precision default null::double precision\n"
    ")\n",
)
BLV_ARGS = (
    "  pipeline_out bigint[] default null::bigint[]\n)\n",
    "  pipeline_out bigint[] default null::bigint[],\n"
    "  -- migration 694: the map area, a box or a circle (never both), as the stats RPCs take it.\n"
    "  bbox_west double precision default null::double precision,\n"
    "  bbox_south double precision default null::double precision,\n"
    "  bbox_east double precision default null::double precision,\n"
    "  bbox_north double precision default null::double precision,\n"
    "  circle_lat double precision default null::double precision,\n"
    "  circle_lng double precision default null::double precision,\n"
    "  circle_radius_m double precision default null::double precision\n"
    ")\n",
)
BLV_END = "            where c.property_id = l.property_id and c.stage_id = any(pipeline_out)))\n$$;"


def _statement(text: str, fn: str) -> str:
    quote = SOURCES[fn][1] if fn in SOURCES else "$$"
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index(f"{quote};", at) + len(quote) + 1]


def _md5(statement: str, quote: str) -> str:
    """pg_proc.prosrc: the whole dollar-quoted string, its first and last newline included."""
    return hashlib.md5(statement.split(quote)[1].encode()).hexdigest()


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def _source(fn: str) -> str:
    return (MIGRATIONS / SOURCES[fn][0]).read_text(encoding="utf-8")


def test_it_is_the_latest_definition_of_the_helper_and_the_three_readers() -> None:
    for fn in (*SOURCES, "in_map_area"):
        assert latest_definition(fn).name == NAME, fn
        assert SQL.count(f"create or replace function public.{fn}(") == 1, fn
    assert CODE.count("create or replace function") == 4
    assert CODE.count("create ") == 4


def test_each_reader_is_its_latest_files_statement_with_exactly_the_named_edits() -> None:
    blv = _statement(_source("browse_list_visible"), "browse_list_visible")
    assert blv.count(BLV_ARGS[0]) == 1 and blv.count(BLV_END) == 1
    expected = blv.replace(*BLV_ARGS).replace(BLV_END, BLV_END[: -len("$$;")] + AREA_CALL + "$$;")
    assert _statement(SQL, "browse_list_visible") == expected
    for fn in ("browse_stats_properties", "browse_map_cells"):
        old = _statement(_source(fn), fn)
        assert old.count(CIRCLE_ARGS[0]) == 1 and old.count(BAND) == 1, fn
        assert _statement(SQL, fn) == old.replace(*CIRCLE_ARGS).replace(BAND, AREA_CALL), fn


def test_no_reader_keeps_an_inline_band() -> None:
    for fn in SOURCES:
        body = _statement(SQL, fn)
        assert "l.lng >= bbox_west" not in body, fn
        assert body.count("public.in_map_area(") == 1, fn


def test_the_helper_is_an_inlinable_invoker_predicate_with_a_true_circle() -> None:
    helper = _statement(SQL, "in_map_area")
    head = helper[: helper.index("as $$")]
    assert "language sql" in head and "immutable" in head
    assert "security definer" not in head and " set " not in head
    body = helper.split("$$")[1]
    # The box exactly as the readers applied it: each edge independently nullable.
    for edge in ("(bbox_west  is null or lng >= bbox_west)", "(bbox_east  is null or lng <= bbox_east)",
                 "(bbox_south is null or lat >= bbox_south)", "(bbox_north is null or lat <= bbox_north)"):
        assert edge in body, edge
    # The circle: a band no narrower than the circle (110 000 m per degree is under every WGS84 degree) as the
    # cheap prefilter, then the geodesic test on the spheroid.
    assert body.count("circle_radius_m / 110000.0") == 2
    assert body.count("circle_radius_m / (110000.0 * cos(radians(circle_lat)))") == 2
    assert "st_dwithin(st_makepoint(lng, lat)::geography, st_makepoint(circle_lng, circle_lat)::geography" in body
    assert "extensions." not in helper  # the CI replay keeps PostGIS in public, prod in extensions


def test_the_gates_pin_the_predecessors_and_this_files_bodies() -> None:
    pre, post = _block("pre"), _block("post")
    for fn, (_, quote) in SOURCES.items():
        assert _md5(_statement(_source(fn), fn), quote) == LIVE[fn], fn
        mine = _md5(_statement(SQL, fn), quote)
        assert f"not in ('{LIVE[fn]}', '{mine}')" in pre, fn
        assert f"is distinct from '{mine}'" in post, fn
    assert f"is distinct from '{_md5(_statement(SQL, 'in_map_area'), '$$')}'" in post


def test_old_signatures_are_dropped_and_every_function_is_executable_by_exactly_the_two_roles() -> None:
    drops = [ln for ln in SQL.splitlines() if ln.startswith("drop function if exists public.")]
    assert len(drops) == 3
    for fn in (*SOURCES, "in_map_area"):
        revoke = [ln for ln in SQL.splitlines() if ln.startswith(f"revoke all on function public.{fn}(")]
        grant = [ln for ln in SQL.splitlines() if ln.startswith(f"grant execute on function public.{fn}(")]
        assert len(revoke) == len(grant) == 1, fn
        assert revoke[0].endswith(") from public, anon;"), fn
        assert grant[0].endswith(") to authenticated, service_role;"), fn
        assert revoke[0][revoke[0].index("("):revoke[0].rindex(")")] == grant[0][grant[0].index("("):grant[0].rindex(")")]

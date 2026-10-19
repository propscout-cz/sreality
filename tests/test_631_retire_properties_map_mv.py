"""Migration 631's rails (ONE PIN W2): exactly the map matview and its four functions go, gated to the bodies the revert restores."""

from __future__ import annotations

import re

import pytest

from scripts.migration_objects import _dropped, parse_objects
from tests.test_630_one_browse_read_model import MIGRATIONS, _code, _md5
from tests.test_portal_exclusion_migration import _params, _types

SQL = (MIGRATIONS / "631_retire_properties_map_mv.sql").read_text(encoding="utf-8")
REVERTS = sorted((MIGRATIONS / "reverts").glob("631_*.sql"))
S522 = (MIGRATIONS / "522_location_w13_rebuild_budgets.sql").read_text(encoding="utf-8")
S629 = (MIGRATIONS / "629_curated_set_scope.sql").read_text(encoding="utf-8")

# md5(prosrc) of each function 631 drops, read live 2026-10-19 (each equal to its source file's body).
LIVE = {"properties_map_visible": "9c1351bbc86f7c1c7a0ec4a7d08db1df",
        "rebuild_properties_map_mv": "e18ea5161c8af9ceab5b07a62b6dc37d",
        "recompute_mf_gross_yields": "6c031b2362ed463369361c8320af30a0",
        "recompute_property_mf": "1ab31fca5867b88494692e322cac2d7c"}
SOURCE_OF = {"properties_map_visible": "629_curated_set_scope.sql",
             "rebuild_properties_map_mv": "522_location_w13_rebuild_budgets.sql",
             "recompute_mf_gross_yields": "507_location_w4a_readers.sql",
             "recompute_property_mf": "507_location_w4a_readers.sql"}
VISIBLE_SIG = "boolean, text, bigint[], bigint[], text, bigint[], bigint[], text, bigint[], bigint[]"
CRON_COMMAND = "set statement_timeout='1500s'; select public.rebuild_properties_map_mv();"


def _body(text: str, fn: str) -> str:
    """The dollar-quoted body of `fn`'s CREATE, i.e. what pg_proc.prosrc holds."""
    at = text.index(f"create or replace function public.{fn}(")
    quote = re.compile(r"\bas (\$[a-z_]*\$)").search(text, at).group(1)
    start = text.index(f"as {quote}", at) + len(f"as {quote}")
    return text[start: text.index(f"{quote};", start)]


def _revert() -> str:
    assert len(REVERTS) == 1, f"expected one migrations/reverts/631_*.sql, found {REVERTS}"
    return REVERTS[0].read_text(encoding="utf-8")


def _block(text: str, tag: str) -> str:
    at = text.index(f"do ${tag}$")
    return text[at: text.index(f"${tag}$;", at)]


@pytest.mark.parametrize("fn", sorted(LIVE))
def test_the_gated_md5_is_the_source_files_body(fn: str) -> None:
    """The $pre$ gate pins the body of the file the revert copies, not a transcription."""
    source = (MIGRATIONS / SOURCE_OF[fn]).read_text(encoding="utf-8")
    assert _md5(_body(source, fn)) == LIVE[fn]
    assert LIVE[fn] in _block(SQL, "pre"), f"{fn}: 631's $pre$ does not gate its live body"


@pytest.mark.parametrize("fn", sorted(LIVE))
def test_the_revert_restores_each_body_byte_for_byte(fn: str) -> None:
    """RED by: an edit to a spliced body or a constant; the revert's $post$ would refuse mid-incident."""
    text = _revert()
    assert _md5(_body(text, fn)) == LIVE[fn], f"{REVERTS[0].name} does not restore {fn}"
    assert LIVE[fn] in _block(text, "post"), f"{fn}: the revert's $post$ does not re-hash it"


def test_the_drop_signature_is_629s_parameter_list() -> None:
    assert _types(_params(S629, "properties_map_visible")) == VISIBLE_SIG
    assert f"drop function if exists public.properties_map_visible({VISIBLE_SIG});" in SQL
    assert f"to_regprocedure('public.properties_map_visible({VISIBLE_SIG})')" in _block(SQL, "pre")


def test_exactly_the_five_objects_go_and_nothing_cascades() -> None:
    assert _dropped(SQL) == {("function", "properties_map_visible"), ("function", "rebuild_properties_map_mv"),
                             ("function", "recompute_mf_gross_yields"), ("function", "recompute_property_mf"),
                             ("relation", "properties_map_mv")}
    assert parse_objects(SQL) == []
    assert "cascade" not in _code(SQL)
    for stmt in ("drop function if exists public.rebuild_properties_map_mv();",
                 "drop materialized view if exists public.properties_map_mv;",
                 "drop function if exists public.recompute_mf_gross_yields();",
                 "drop function if exists public.recompute_property_mf(bigint[]);"):
        assert SQL.count(stmt) == 1, stmt


def test_the_steps_run_in_order_inside_one_transaction() -> None:
    """RED by: a drop before the guard or the unschedule (a tick against a dropped function), or the caller after its callee."""
    code = _code(SQL)
    steps = ["begin;", "do $pre$", "cron.unschedule('browse-map-rebuild')",
             "drop function if exists public.properties_map_visible(",
             "drop function if exists public.rebuild_properties_map_mv()",
             "drop materialized view if exists public.properties_map_mv",
             "delete from public.derived_artifacts where name = 'properties_map_mv'",
             "drop function if exists public.recompute_mf_gross_yields()",
             "drop function if exists public.recompute_property_mf(bigint[])",
             "do $post$", "notify pgrst", "commit;"]
    for step in steps:
        assert code.count(step) == 1, step
    at = [code.index(step) for step in steps]
    assert at == sorted(at)


def test_the_unschedule_is_guarded_and_the_pre_takes_the_rebuilds_key() -> None:
    cron = _code(_block(SQL, "cron"))
    assert cron.index("to_regclass('cron.job') is null") < cron.index("cron.unschedule('browse-map-rebuild')")
    key = "pg_advisory_xact_lock(hashtext('rebuild_properties_map_mv'))"
    assert key in _code(_block(SQL, "pre"))
    assert "hashtext('rebuild_properties_map_mv')" in _body(S522, "rebuild_properties_map_mv"), (
        "522's rebuild no longer takes this key; the $pre$ would not block a tick")


def test_the_revert_lives_under_reverts_and_schedules_522s_command() -> None:
    assert REVERTS and REVERTS[0].parent.name == "reverts"
    assert not list(MIGRATIONS.glob("631_revert*.sql")), "a revert beside 631 would be replayed by CI"
    assert f"'browse-map-rebuild',\n    '7,37 * * * *',\n    $${CRON_COMMAND}$$" in S522
    assert f"'browse-map-rebuild',\n    '7,37 * * * *',\n    $${CRON_COMMAND}$$" in _block(_revert(), "cron")
    assert f"('7,37 * * * *', $c${CRON_COMMAND}$c$)" in _block(SQL, "pre")
    assert f"$c${CRON_COMMAND}$c$" in _block(_revert(), "post")

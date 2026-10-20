"""Migration 626's rails: the Stats dashboard's SQL is the registry's, offline.

What only the text can show (docs/design/stats-dashboard.md, "Rails"): the effective
`stats_dim_key` IS `toolkit.stats_registry.dim_key_function_sql()`; the pivot carries the
registry's measure expression, limits, spec guard, turnover and place-dimension lists verbatim; its
cohort WHERE is 625's byte for byte and its signature 625's 74 parameters plus `spec`; the old
signature is dropped by its exact types and the ACL restated in the same transaction; the
apply is gated on 625's live body or this file's own; the saved board is an RLS'd tenant table;
a place keys on its RUIAN code and its name reaches the INVOKER pivot through an owner-rights
view over the register. The generated TypeScript is the registry's too. Behaviour is
tests/test_stats_pivot_live.py's subject.
"""

from __future__ import annotations

import hashlib
import re

from tests.migration_defs import MIGRATIONS, latest_definition
from tests.test_portal_exclusion_migration import _params, _types
from toolkit import stats_registry as sr

NAME = "626_stats_dashboard.sql"
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
S625 = (MIGRATIONS / "625_tom_observed.sql").read_text(encoding="utf-8")
FN = "browse_stats_properties"
# The pivot is restated whole by every migration that touches it (629 added the curated scope, 632 moved the floor), so the pins
# about its BODY read the effective definition; the ones about what 626 itself creates read 626.
EFFECTIVE = latest_definition(FN).read_text(encoding="utf-8")
LIVE_625 = "6b102dd7960cced4ffbbf31127048428"  # pg_proc md5(prosrc), read live 2026-10-19


def _statement(text: str, fn: str) -> str:
    at = text.index(f"create or replace function public.{fn}(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _body(text: str, fn: str) -> str:
    """pg_proc.prosrc: the whole dollar-quoted string, its first and last newline included."""
    return _statement(text, fn).split("$function$")[1]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _where(text: str) -> str:
    stmt = _statement(text, FN)
    at = stmt.index("\n    where\n")
    return stmt[at: stmt.index("      and true\n", at) + len("      and true\n")]


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


# --- the registry, carried verbatim ---------------------------------------------------


def test_the_effective_dim_key_is_the_registrys() -> None:
    assert latest_definition("stats_dim_key").name == NAME
    assert _statement(SQL, "stats_dim_key") == sr.dim_key_function_sql()


def test_the_pivot_carries_the_registrys_measure_and_limits() -> None:
    body = _body(EFFECTIVE, FN)
    assert f"{sr.measure_value_sql()} as v" in body
    assert f"v_min_n     constant int := {sr.MIN_N};" in body
    assert f"v_max_cells constant int := {sr.MAX_CELLS};" in body
    assert sr.MIN_N == 5 and sr.MAX_CELLS == 2000
    # six fixed row slots and one column slot: the rollup is the registry's MAX_ROWS deep
    slots = ", ".join(f"k{i}" for i in range(1, sr.MAX_ROWS + 1))
    assert f"group by kc, rollup({slots})" in body


def test_the_pivot_guards_the_spec_with_the_registrys_rules() -> None:
    """validate_spec's limits, measures and dimensions, rendered once (`spec_guard_sql`)."""
    body = _body(EFFECTIVE, FN)
    at = body.index("  if spec is null then")
    assert body[at: body.index("  end if;", at) + len("  end if;")] == sr.spec_guard_sql()


def test_the_turnover_refusal_names_the_registrys_single_status_measures() -> None:
    """Ruling 3: ONE status in the SAMPLE; the cohort's `status` fact is not the gate."""
    single = [m.id for m in sr.MEASURES if m.single_status]
    assert single == ["tom_days"]
    body = _body(EFFECTIVE, FN)
    arm = re.search(r"when v_measure in \(([^)]*)\) and \(select bool_or\(is_active\) and not "
                    r"bool_and\(is_active\) from sample\) then 'mixed_status'", body)
    assert arm and arm.group(1) == sr.single_status_measures_sql()
    assert "c.any_active and not c.all_active" not in body


def test_every_slot_is_keyed_by_stats_dim_key_over_its_argument_list() -> None:
    body = _body(EFFECTIVE, FN)
    args = ", ".join(
        f"ll.{n}" if n.endswith("_name") else f"l.{'price_per_m2_basis' if n == 'basis' else n}"
        for n, _ in sr.DIM_KEY_ARGS[1:]
    )
    for slot, var in [*((f"k{i}", f"r{i}") for i in range(1, 7)), ("kc", "c1")]:
        assert f"public.stats_dim_key({var}, {args}) as {slot}," in body, slot


def test_the_generated_typescript_is_fresh() -> None:
    """The `--check` of scripts/generate_filter_registry.py, for the stats file."""
    from scripts.generate_filter_registry import generate_stats

    generated = MIGRATIONS.parent / "frontend" / "src" / "lib" / "statsRegistry.generated.ts"
    assert generated.read_text(encoding="utf-8") == generate_stats()


# --- the cohort is 625's, the signature 625's plus spec --------------------------------


def test_the_cohort_where_is_625s_byte_for_byte() -> None:
    assert len(_where(SQL)) > 5000
    assert _where(SQL) == _where(S625)
    # Every later restatement keeps 625's WHERE and may only ADD a block: 629 (curated-set scope) put its
    # clauses for the three curated sets right after the id denylist. Nothing of 625's may go, and the
    # one inserted block is that scope's and nothing else.
    base, later = _where(S625).split("\n"), _where(EFFECTIVE).split("\n")
    anchor = next(i for i, ln in enumerate(base) if "property_ids_excluded" in ln)
    # 640 dropped that one conjunct itself (598's denylist parameter left once 629 judged membership
    # in the database), so the effective WHERE is 625's minus that line, plus the block inserted
    # right after its predecessor (the property_ids_filter allowlist).
    base_now = base[:anchor] + base[anchor + 1:]
    start = later.index(base[anchor - 1]) + 1
    end = later.index(base[anchor + 1], start)
    assert later[:start] + later[end:] == base_now, "the effective WHERE is not 625's minus 640's drop plus one inserted block"
    block = " ".join(later[start:end])
    assert later[start].lstrip().startswith("-- migration 629: the curated-set scope")
    assert all(f"{c}_set" in block for c in ("tags", "collections", "pipeline")), block

def test_the_signature_is_625s_plus_spec() -> None:
    old, new = _params(S625, FN), _params(SQL, FN)
    assert len(old) == 74
    assert new == old + [("spec", "jsonb")]
    assert "  spec jsonb default null\n)\nreturns jsonb\nlanguage plpgsql\nstable\n" in _statement(SQL, FN)


def test_one_definition_dropped_by_625s_types_and_its_acl_restated() -> None:
    code = " ".join(CODE.split())
    assert SQL.count(f"create or replace function public.{FN}(") == 1
    assert f"drop function if exists public.{FN}({_types(_params(S625, FN))});" in code
    sig = _types(_params(SQL, FN))
    assert f"revoke execute on function public.{FN}({sig}) from public, anon;" in code
    assert f"grant execute on function public.{FN}({sig}) to authenticated, service_role;" in code
    assert "drop function if exists" in code and " cascade;" not in code.lower()
    at = CODE.index
    assert (at("do $pre$") < at("create table if not exists public.stats_dashboards") < at("begin;")
            < at("create or replace view public.ruian_place_names_public")
            < at("create or replace function public.stats_dim_key(")
            < at(f"drop function if exists public.{FN}(") < at(f"create or replace function public.{FN}(")
            < at("commit;") < at("do $post$") < at("select pg_notify('pgrst', 'reload schema');"))


def test_the_apply_is_gated_on_625s_live_body_or_this_files_own() -> None:
    """pg_proc.prosrc keeps the newline after the opening `$function$` and the one before the
    closing tag; an md5 taken without them never matches the catalog, so a re-run would refuse."""
    assert _md5(_body(S625, FN)) == LIVE_625
    pre = _block("pre")
    assert pre.index("into populated") < pre.index("return;") < pre.index("pg_proc")
    gate = re.search(r"not in \('(\w{32})', '(\w{32})'\)", pre)
    assert gate and gate.groups() == (LIVE_625, _md5(_body(SQL, FN)))


def test_the_post_conditions_name_the_new_shape() -> None:
    post = _block("post")
    assert "rollup(k1, k2, k3, k4, k5, k6)" in post and "tom_observed_only" in post
    assert "<> 75 then" in post
    assert "stats_dashboards_tenant_rw" in post
    assert "has_table_privilege('authenticated', 'public.ruian_place_names_public', 'select')" in post
    assert "or has_table_privilege('anon', 'public.ruian_place_names_public', 'select') then" in post
    assert (f"has_function_privilege('authenticated', 'public.stats_dim_key({sr.DIM_KEY_SIGNATURE})', "
            "'execute')") in post
    assert sr.DIM_KEY_SIGNATURE.endswith("bigint, bigint, bigint, bigint, bigint")


# --- the saved board and the place names -----------------------------------------------


def test_the_board_is_an_rls_tenant_table_written_by_the_api() -> None:
    from tests._tenant_registry import TENANT_TABLES

    code = " ".join(CODE.split())
    for clause in (
        "create table if not exists public.stats_dashboards ( account_id uuid primary key "
        "references public.accounts(id) on delete cascade, charts jsonb not null default '[]'::jsonb, "
        "updated_at timestamptz not null default now() );",
        "alter table public.stats_dashboards enable row level security;",
        "revoke all on public.stats_dashboards from public, anon, authenticated;",
        "grant select, insert, update, delete on public.stats_dashboards to authenticated;",
        "create policy stats_dashboards_tenant_rw on public.stats_dashboards for all to authenticated "
        "using (account_id in (select current_account_ids())) "
        "with check (account_id in (select current_account_ids()));",
    ):
        assert clause in code, clause
    assert "stats_dashboards" in TENANT_TABLES


def test_the_place_names_reach_the_invoker_pivot_through_an_owner_rights_view() -> None:
    """A place cell keys on the RUIAN code browse_list carries (rule 25's one code predicate),
    so two towns or streets sharing a name stay two cells and the pivot reads no listing
    location. The names come from the register mirror, which 381 revokes from browser roles;
    privileges are checked on every relation a statement names, so a direct join from the
    INVOKER pivot would fail every signed-in call. Hence one owner-rights view, read only on
    the <= MAX_CELLS result cells."""
    code = " ".join(CODE.split())
    admin = ", ".join(f"'{d.id}'" for d in sr.DIMENSIONS if d.kind == "geo" and d.id != "ulice")
    assert admin == "'kraj', 'okres', 'obec', 'cast_obce'"
    assert ("create or replace view public.ruian_place_names_public as select * from ( "
            "select distinct on (u.level, u.code) u.level::text as level, u.code, u.name "
            "from public.ruian_admin_units u "
            f"where u.level in ({admin}) and u.retired_at is null "
            "order by u.level, u.code, u.last_version_id desc nulls last ) a "
            "union all select * from ( "
            "select distinct on (s.code) 'ulice'::text as level, s.code, s.name "
            "from public.ruian_streets s "
            "order by s.code, s.last_version_id desc nulls last ) b;") in code
    assert "security_invoker" not in code
    assert "revoke all on public.ruian_place_names_public from public, anon, authenticated;" in code
    assert "grant select on public.ruian_place_names_public to authenticated, service_role;" in code

    # The keys: the five codes, last in stats_dim_key's signature; each place arm is its code as text.
    places = ("region_id", "okres_id", "obec_id", "cast_obce_id", "ulice_id")
    assert sr.DIM_KEY_ARGS[-5:] == tuple((n, "bigint") for n in places)
    assert {d.id: d.sql for d in sr.DIMENSIONS if d.kind == "geo"} == {
        "kraj": "region_id::text", "okres": "okres_id::text", "obec": "obec_id::text",
        "cast_obce": "cast_obce_id::text", "ulice": "ulice_id::text"}
    stmt = re.sub(r"--[^\n]*", "", _statement(EFFECTIVE, FN))
    tail = ", ".join(f"l.{n}" for n in places)
    for slot in [*(f"k{i}" for i in range(1, 7)), "kc"]:
        assert f"{tail}) as {slot}," in stmt, slot
    # 634 appended the two source arrays (the status lens reads them) after the five codes.
    assert f"l.garage, {tail}, l.all_sources, l.active_sources\n    from browse_list l\n" in stmt
    for gone in ("listing_location", "listing_place_names_public", "v_geo", "ruian_admin_units",
                 "ruian_streets", "kraj_name", "okres_name", "obec_name", "cast_obce_name", "street_name"):
        assert gone not in stmt, gone

    # The names: every place key among the result cells, resolved through the view.
    flat = " ".join(stmt.split())
    assert ("'places', case when x.refused is null then coalesce(( "
            "select jsonb_agg(jsonb_build_object('dim', p.dim, 'key', p.key, 'name', n.name) "
            "order by p.dim, p.key) from ( select distinct d.dim, d.key from used u "
            "cross join lateral (values (r1, u.k1), (r2, u.k2), (r3, u.k3), (r4, u.k4), (r5, u.k5), "
            "(r6, u.k6), (c1, u.kc)) d(dim, key) "
            f"where d.key is not null and d.dim in ({sr.geo_ids_sql()}) ) p "
            "left join public.ruian_place_names_public n on n.level = p.dim and n.code = p.key::bigint), "
            "'[]'::jsonb) else '[]'::jsonb end,") in flat
    assert flat.count("ruian_place_names_public") == 1
    assert "security definer" not in _statement(EFFECTIVE, FN).lower()


def test_the_receipt_probes_what_the_file_creates() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "relation:public.stats_dashboards", "relation:public.ruian_place_names_public",
        "function:public.stats_dim_key", "function:public.browse_stats_properties",
        "policy:stats_dashboards.stats_dashboards_tenant_rw"]


# --- 627: the annotations feature's storage goes ----------------------------------------


def test_627_drops_the_cache_and_the_two_settings_forward_only() -> None:
    sql = (MIGRATIONS / "627_region_annotations_drop.sql").read_text(encoding="utf-8")
    code = " ".join(re.sub(r"--[^\n]*", "", sql).split())
    assert "DESTRUCTIVE" in sql and "on the operator's word" in sql and "Forward only" in sql
    assert "drop table if exists public.region_disposition_annotations;" in code
    assert ("delete from public.app_settings where key in "
            "('llm_region_annotation_system_prompt', 'llm_region_annotation_model');") in code
    assert "cascade" not in code.lower()
    assert code.index("begin;") < code.index("drop table") < code.index("do $post$") < code.index("commit;")

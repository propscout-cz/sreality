"""Migration 647's rails: saved dashboards are one saved-set primitive, shared read-only, offline.

What only the text can show (docs/design/saved-dashboards.md, "Schema"): stats_dashboards is re-keyed on
`id` with the preset row's columns (the colour CHECK is 201's, verbatim) and keeps ONE unnamed working board
per account through a partial unique index the API's upsert infers; the share table is 610's shape (owner
stamped by trigger, which also refuses the working board; SELECT + DELETE, no browser INSERT; a party policy
that hides while acting); the shared-read arm is FOR SELECT only; share_party_emails() is 641's body plus one
arm; every statement re-runs as a no-op; the post-conditions name the new shape. Behaviour is
tests/test_stats_dashboard_shares_live.py's subject.
"""

from __future__ import annotations

import re
from typing import get_args

from tests.migration_defs import MIGRATIONS, latest_definition

NAME = "647_saved_dashboards.sql"
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
CODE = " ".join(re.sub(r"--[^\n]*", "", SQL).split())


def _flat(text: str) -> str:
    return " ".join(re.sub(r"--[^\n]*", "", text).split())


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return SQL[at: SQL.index(f"${tag}$;", at)]


def _function(text: str, name: str) -> str:
    flat = _flat(text)
    at = flat.index(f"function {name}()")
    return flat[at: flat.index("$$;", flat.index("$$", at) + 2) + 3]


# --- the shape of the file ------------------------------------------------------------


def test_one_transaction_behind_a_lock_timeout_then_the_schema_reload() -> None:
    at = CODE.index
    assert at("set lock_timeout = '5s';") < at("set statement_timeout = '60s';") < at("begin;")
    assert at("begin;") < at("alter table public.stats_dashboards add column") < at("do $post$")
    assert at("do $post$") < at("commit;") < at("select pg_notify('pgrst', 'reload schema');")
    assert CODE.endswith("reset statement_timeout; reset lock_timeout;")
    assert CODE.count("begin;") == 1 and CODE.count("commit;") == 1


def test_every_statement_re_runs_as_a_no_op() -> None:
    """The apply workflow retries the whole file on a lock timeout, and a second apply must change nothing."""
    for m in re.finditer(r"\b(create (?:unique )?(?:index|table)) (?!if not exists)", CODE):
        raise AssertionError(f"{m.group(1)} without `if not exists`")
    assert "add column " in CODE and "add column if not exists" in CODE
    assert CODE.count("add column ") == CODE.count("add column if not exists")
    for name, on in (("stats_dashboard_shares_party", "public.stats_dashboard_shares"),
                     ("stats_dashboards_shared_read", "public.stats_dashboards")):
        assert CODE.index(f"drop policy if exists {name} on {on};") < CODE.index(f"create policy {name} on {on}")
    assert (CODE.index("drop trigger if exists stats_dashboard_shares_account_id_bi")
            < CODE.index("create trigger stats_dashboard_shares_account_id_bi"))
    assert CODE.count("create function") == 0 and CODE.count("create or replace function") == 2
    # The key swap is the one statement pair that cannot re-run bare (the new FK depends on the new key).
    key = _flat(_block("key"))
    assert ("if exists (select 1 from pg_constraint k join pg_attribute a on a.attrelid = k.conrelid "
            "and a.attnum = any (k.conkey) where k.conrelid = 'public.stats_dashboards'::regclass "
            "and k.contype = 'p' and a.attname = 'account_id') then") in key
    assert ("alter table public.stats_dashboards drop constraint stats_dashboards_pkey; "
            "alter table public.stats_dashboards add constraint stats_dashboards_pkey primary key (id);") in key


# --- stats_dashboards: the saved-set row ----------------------------------------------


def test_the_board_becomes_a_saved_set_row_keyed_on_id() -> None:
    for clause in (
        "alter table public.stats_dashboards add column if not exists id uuid not null default gen_random_uuid();",
        "alter table public.stats_dashboards add column if not exists name text constraint "
        "stats_dashboards_name_check check (name is null or length(name) between 1 and 200);",
        "alter table public.stats_dashboards add column if not exists filter_spec jsonb;",
        "alter table public.stats_dashboards add column if not exists position integer not null default 0;",
        "alter table public.stats_dashboards add column if not exists created_at timestamptz not null default now();",
        "alter table public.stats_dashboards alter column account_id set not null;",
        "create index if not exists stats_dashboards_account_idx on public.stats_dashboards (account_id);",
        "create unique index if not exists stats_dashboards_one_working_per_account "
        "on public.stats_dashboards (account_id) where name is null;",
    ):
        assert clause in CODE, clause
    # The key swap comes before anything references the new key.
    assert CODE.index("add constraint stats_dashboards_pkey primary key (id)") < CODE.index(
        "references public.stats_dashboards(id)")


def test_the_colour_check_is_201s_palette_verbatim_and_the_apis() -> None:
    from api.schemas import TagColor

    s201 = _flat((MIGRATIONS / "201_filter_presets_color.sql").read_text(encoding="utf-8"))
    palette = s201[s201.index("check (color is null or color in ("): s201.index("));") + 2]
    assert ("alter table public.stats_dashboards add column if not exists color text "
            f"constraint stats_dashboards_color_check {palette};") in CODE
    assert re.findall(r"'(\w+)'", palette) == list(get_args(TagColor))


def test_the_working_board_upsert_infers_the_partial_index() -> None:
    from api.routes import stats_dashboards as sd

    assert "ON CONFLICT (account_id) WHERE name IS NULL DO UPDATE" in sd._SAVE_WORKING_BOARD_SQL
    assert sd._WORKING_BOARD_SQL.endswith("WHERE account_id = %s AND name IS NULL")
    assert sd.DASHBOARDS.row_scope == "name IS NOT NULL"


def test_the_tenant_policy_is_untouched_and_the_shared_arm_is_select_only() -> None:
    """626's FOR ALL policy keeps current_account_ids() (a delegate reads no dashboard of the owner); a
    recipient READS through one FOR SELECT arm, so every write stays the owner's."""
    assert "stats_dashboards_tenant_rw" not in _flat(SQL[: SQL.index("do $post$")])
    assert ("create policy stats_dashboards_shared_read on public.stats_dashboards for select to authenticated "
            "using (id in (select dashboard_id from public.stats_dashboard_shares));") in CODE
    assert "acting_account_ids" not in CODE


# --- the share row --------------------------------------------------------------------


def test_the_share_table_is_610s_shape() -> None:
    for clause in (
        "create table if not exists public.stats_dashboard_shares ( "
        "dashboard_id uuid not null references public.stats_dashboards(id) on delete cascade, "
        "account_id uuid not null references public.accounts(id) on delete cascade, "
        "recipient_account_id uuid not null references public.accounts(id) on delete cascade, "
        "created_at timestamptz not null default now(), "
        "primary key (dashboard_id, recipient_account_id), "
        "constraint stats_dashboard_shares_not_self check (recipient_account_id <> account_id) );",
        "create index if not exists stats_dashboard_shares_recipient_idx "
        "on public.stats_dashboard_shares (recipient_account_id);",
        "alter table public.stats_dashboard_shares enable row level security;",
        "revoke all on public.stats_dashboard_shares from public, anon, authenticated;",
        "grant select, delete on public.stats_dashboard_shares to authenticated;",
        "create policy stats_dashboard_shares_party on public.stats_dashboard_shares for all to authenticated "
        "using ((account_id in (select current_account_ids()) "
        "or recipient_account_id in (select current_account_ids())) and not acting()) "
        "with check (account_id in (select current_account_ids()));",
    ):
        assert clause in CODE, clause
    assert not re.search(r"grant [^;]*insert[^;]* on public\.stats_dashboard_shares", CODE)


def test_the_trigger_stamps_the_owner_and_refuses_the_working_board() -> None:
    fn = _function(SQL, "public.sync_account_id_from_dashboard")
    assert fn.startswith("function public.sync_account_id_from_dashboard() returns trigger "
                         "language plpgsql set search_path = '' as $$")
    assert ("select d.account_id, d.name into new.account_id, dashboard_name "
            "from public.stats_dashboards d where d.id = new.dashboard_id;") in fn
    assert ("if new.account_id is not null and dashboard_name is null then "
            "raise exception 'stats_dashboards %: the working board is never shared', new.dashboard_id;") in fn
    assert "security definer" not in fn
    assert ("revoke all on function public.sync_account_id_from_dashboard() "
            "from public, anon, authenticated;") in CODE
    assert ("create trigger stats_dashboard_shares_account_id_bi before insert on public.stats_dashboard_shares "
            "for each row execute function public.sync_account_id_from_dashboard();") in CODE


def test_the_label_door_is_641s_body_plus_the_dashboard_arm() -> None:
    assert latest_definition("share_party_emails").name == NAME
    s641 = (MIGRATIONS / "641_pipeline_shares_and_acting.sql").read_text(encoding="utf-8")
    old = _function(s641, "share_party_emails").replace("function share_party_emails()",
                                                         "function public.share_party_emails()")
    arm = ("select q.account_id, q.recipient_account_id from public.pipeline_shares q")
    new = old.replace(arm, f"{arm} union all select b.account_id, b.recipient_account_id "
                           "from public.stats_dashboard_shares b")
    assert new != old
    assert _function(SQL, "public.share_party_emails") == new
    assert "security definer set search_path = ''" in new
    assert "revoke all on function public.share_party_emails() from public, anon, authenticated;" in CODE
    assert "grant execute on function public.share_party_emails() to authenticated;" in CODE


# --- the post-conditions and the receipt ------------------------------------------------


def test_the_post_conditions_name_the_new_shape() -> None:
    post = _flat(_block("post"))
    for clause in (
        "'stats_dashboards.stats_dashboards_tenant_rw', 'ALL'",
        "'stats_dashboards.stats_dashboards_shared_read', 'SELECT'",
        "'stats_dashboard_shares.stats_dashboard_shares_party', 'ALL'",
        "and roles = array['authenticated']::name[]) <> 3 then",
        "where k.conrelid = 'public.stats_dashboards'::regclass and k.contype = 'p') "
        "is distinct from array['id'] then",
        "and a.attname = 'account_id') then raise exception",
        "pg_get_indexdef(i.indexrelid) like '%USING btree (account_id) WHERE (name IS NULL)'",
        "'stats_dashboards_name_check', 'stats_dashboards_color_check', 'stats_dashboard_shares_not_self'",
        "position('not acting()' in lower(qual)) > 0",
        "has_table_privilege('authenticated', 'public.stats_dashboard_shares', 'INSERT')",
        "or has_table_privilege('anon', 'public.stats_dashboard_shares', 'SELECT') then",
        "tgfoid = 'public.sync_account_id_from_dashboard()'::regprocedure",
        "position('public.stats_dashboard_shares' in (select p.prosrc from pg_proc p "
        "where p.oid = 'public.share_party_emails()'::regprocedure)) = 0",
        "or has_function_privilege('authenticated', 'public.sync_account_id_from_dashboard()', 'EXECUTE')",
    ):
        assert clause in post, clause
    assert CODE.index("do $post$") < CODE.index("commit;"), "a failed post-condition must roll the file back"


def test_the_receipt_probes_what_the_file_creates() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == [
        "relation:public.stats_dashboard_shares",
        "relation:stats_dashboards_account_idx",
        "relation:stats_dashboards_one_working_per_account",
        "relation:stats_dashboard_shares_recipient_idx",
        "function:public.sync_account_id_from_dashboard",
        "function:public.share_party_emails",
        "column:stats_dashboards.id",
        "column:stats_dashboards.name",
        "column:stats_dashboards.filter_spec",
        "column:stats_dashboards.position",
        "column:stats_dashboards.color",
        "column:stats_dashboards.created_at",
        "constraint:stats_dashboards.stats_dashboards_pkey",
        "policy:stats_dashboard_shares.stats_dashboard_shares_party",
        "policy:stats_dashboards.stats_dashboards_shared_read",
    ]

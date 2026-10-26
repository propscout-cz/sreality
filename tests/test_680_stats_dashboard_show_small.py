"""Migration 680: a dashboard saves its "Malé vzorky" switch (`stats_dashboards.show_small`). Offline, text only.

One additive column, boolean NOT NULL DEFAULT false (every existing dashboard reads off), described by a column
comment and proven by a post-condition; no data, no grant, no policy. What the API does with it is
tests/api/test_stats_dashboards.py's; that a row reads it back is tests/test_stats_dashboard_shares_live.py's.
"""

from __future__ import annotations

import re

from tests.migration_defs import MIGRATIONS

NAME = "680_stats_dashboard_show_small.sql"
SQL = (MIGRATIONS / NAME).read_text(encoding="utf-8")
CODE = " ".join(re.sub(r"--[^\n]*", "", SQL).split())


def _block(tag: str) -> str:
    at = SQL.index(f"do ${tag}$")
    return " ".join(SQL[at: SQL.index(f"${tag}$;", at)].split())


def test_one_transaction_behind_the_timeouts_then_the_schema_reload() -> None:
    at = CODE.index
    assert (at("set lock_timeout = '5s';") < at("set statement_timeout = '60s';") < at("begin;")
            < at("alter table public.stats_dashboards add column") < at("comment on column")
            < at("do $post$") < at("commit;") < at("select pg_notify('pgrst', 'reload schema');"))
    assert CODE.endswith("reset statement_timeout; reset lock_timeout;")
    assert CODE.count("begin;") == 1 and CODE.count("commit;") == 1


def test_the_column_is_an_off_by_default_boolean_and_re_runs_as_a_no_op() -> None:
    assert ("alter table public.stats_dashboards add column if not exists show_small boolean not null "
            "default false;") in CODE
    assert CODE.count("add column ") == CODE.count("add column if not exists") == 1


def test_it_is_additive_only() -> None:
    lowered = CODE.lower()
    for verb in ("drop ", "update ", "delete ", "truncate", "grant ", "revoke ", "policy", "rename"):
        assert verb not in lowered, verb


def test_the_comment_says_what_it_means_and_who_reads_it() -> None:
    comment = CODE[CODE.index("comment on column public.stats_dashboards.show_small is"): CODE.index("do $post$")]
    flat = comment.replace("' '", "")
    for phrase in ("min/mean/median/max", "\"Malé vzorky\"", "migration 678", "migration 680",
                   "\"Okolí dashboard\"", "The working board (name IS NULL) may carry it; nothing reads it there."):
        assert phrase in flat, phrase


def test_the_post_condition_names_the_column_type_nullability_and_default() -> None:
    post = _block("post")
    assert ("where table_schema = 'public' and table_name = 'stats_dashboards' and column_name = 'show_small' "
            "and data_type = 'boolean' and is_nullable = 'NO' and column_default = 'false'") in post
    assert ("raise exception '680 did not land: stats_dashboards.show_small is not boolean NOT NULL "
            "DEFAULT false'") in post


def test_the_receipt_probes_the_column() -> None:
    from scripts.migration_objects import parse_objects

    assert [str(o) for o in parse_objects(SQL)] == ["column:stats_dashboards.show_small"]

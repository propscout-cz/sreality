"""Migration 658: rebuild_browse_list() is 649's body less its dead `drop table if exists browse_list_next`.

That drop's NOTICE flushed the cron result early and blocked pg_cron's single launcher for the whole rebuild
(~68 s, four times an hour). Offline, text only: the replay applies the file against 649's catalog body.
"""

from __future__ import annotations

import hashlib

from tests.migration_defs import MIGRATIONS, latest_definition

SQL = (MIGRATIONS / "658_rebuild_browse_list_without_notice.sql").read_text(encoding="utf-8")
S649 = (MIGRATIONS / "649_web_portal.sql").read_text(encoding="utf-8")
DEAD = "  execute 'drop table if exists browse_list_next';\n"


def _statement(text: str) -> str:
    at = text.index("create or replace function public.rebuild_browse_list(")
    return text[at: text.index("$function$;", at) + len("$function$;")]


def _md5(statement: str) -> str:
    return hashlib.md5(statement.split("$function$")[1].encode()).hexdigest()


def test_the_body_is_649s_less_the_one_statement() -> None:
    """RED by: any other change to the function, or the drop surviving."""
    assert _statement(S649).count(DEAD) == 1
    assert _statement(SQL) == _statement(S649).replace(DEAD, "", 1)
    assert "drop table if exists browse_list_next" not in _statement(SQL)
    assert latest_definition("rebuild_browse_list").name == "658_rebuild_browse_list_without_notice.sql"


def test_the_apply_is_gated_on_649s_body_or_its_own_and_asserts_the_drop_gone() -> None:
    pre = SQL[SQL.index("do $pre$"): SQL.index("$pre$;")]
    assert _md5(_statement(S649)) in pre and _md5(_statement(SQL)) in pre
    post = SQL[SQL.index("do $post$"): SQL.index("$post$;")]
    assert "like '%drop table if exists browse_list_next%'" in post and _md5(_statement(SQL)) in post

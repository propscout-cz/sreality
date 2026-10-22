"""Migration 653's rails, offline (notifications push W3, PROGRAM.md § 5 "Migration C"): what only the text
can show. The event columns are filled since 652 by 652's own statements before anything is marked; the
repeats are marked before the identity index is built; the build is CONCURRENTLY outside every transaction
block, an INVALID leftover is moved aside first and a $post$ proves the index; nothing is dropped but that
leftover. The identity's columns are pinned to EVENT_IDENTITY in tests/api/test_notifications.py, and the
index is executed by tests/test_property_carriers_live.py."""

from __future__ import annotations

import re

from tests.migration_defs import MIGRATIONS

SQL = (MIGRATIONS / "653_notifications_w3_event_identity.sql").read_text(encoding="utf-8")
CODE = " ".join(re.sub(r"--[^\n]*", "", SQL).split())
CODE_652 = " ".join(re.sub(r"--[^\n]*", "",
                           (MIGRATIONS / "652_notifications_w2a_event_columns.sql").read_text(
                               encoding="utf-8")).split())


def _updates(code: str) -> list[str]:
    return re.findall(r"update public\.notification_dispatches d set .*?;", code)


def test_the_fills_since_652_are_652s_own_backfills_and_run_before_the_marking() -> None:
    """event_ref (system history keyed on its old key included), the actor and the 'shared' relabel:
    the same three guarded statements, so a row written between 652 and the W2a deploy gets exactly the
    identity its repeat will render, and no 'shared' row is left for the feed to parse."""
    fills = _updates(CODE.split("do $repeats$", 1)[0])
    assert fills == _updates(CODE_652) and len(fills) == 3
    assert "when d.source_kind = 'system_health' then d.dedupe_key" in fills[0]
    assert "set change_kind = 'shared_' || split_part(d.dedupe_key, ':', 2)" in fills[2]


def test_repeats_are_marked_in_the_transaction_and_the_index_is_built_after_it() -> None:
    begin, commit = CODE.index("begin;"), CODE.index("commit;")
    marking = CODE.index("do $repeats$")
    build = CODE.index("create unique index concurrently if not exists notification_dispatches_identity_uq")
    assert begin < marking < commit < build
    assert CODE.count("begin;") == 1 and CODE.count("commit;") == 1
    assert "set duplicate_of = r.first_id" in CODE and "where duplicate_of is null)" in CODE
    assert "alter table public.notification_dispatches alter column dedupe_key drop not null;" in CODE
    for index in ("notification_dispatches_account_feed_idx on public.notification_dispatches "
                  "(account_id, dispatched_at desc, id desc);",
                  "notification_dispatches_duplicate_of_idx on public.notification_dispatches "
                  "(duplicate_of) where duplicate_of is not null;"):
        assert f"create index if not exists {index}" in CODE[begin:commit]


def test_a_failed_build_is_recovered_by_a_re_run_and_proved_by_post() -> None:
    aside = CODE.index("alter index public.notification_dispatches_identity_uq rename to "
                       "notification_dispatches_identity_uq_invalid;")
    drop = CODE.index("drop index concurrently if exists public.notification_dispatches_identity_uq_invalid;")
    build = CODE.index("create unique index concurrently if not exists notification_dispatches_identity_uq")
    post = CODE.index("do $post$")
    assert aside < drop < build < post
    proof = CODE[post:]
    for fact in ("i.indisvalid", "i.indisready", "i.indisunique", "i.indnullsnotdistinct",
                 "pg_get_expr(i.indpred, i.indrelid) = '(duplicate_of IS NULL)'"):
        assert fact in proof, fact
    assert "re-run the file" in proof
    assert "set lock_timeout = 0; set statement_timeout = '1900s';" in CODE[:build]


def test_nothing_is_dropped_but_a_failed_builds_leftover() -> None:
    drops = re.findall(r"\bdrop\s+\w+(?:\s+\w+)?", CODE)
    assert drops == ["drop not null", "drop index concurrently"], drops


def test_the_header_says_when_it_may_be_applied() -> None:
    assert "only AFTER W2a's code is deployed" in SQL
    assert "Never through the MCP" in SQL

"""Migration 652's rails, offline (notifications push W2a, PROGRAM.md § 3.2 Migration A): what only the
text can show. A watchdog restarts on create, on a switch-on and on a filter edit (ruling R6 (a), extended
2026-10-21), and nowhere else; a share notification goes with its sharer's account; every backfill keys off
today's dedupe_key grammar, system history included; the file re-runs as a no-op and says when to re-run it.
The trigger's behaviour is tests/test_652_notifications_live.py's subject."""

from __future__ import annotations

import re

from tests.migration_defs import MIGRATIONS

SQL = (MIGRATIONS / "652_notifications_w2a_event_columns.sql").read_text(encoding="utf-8")
CODE = " ".join(re.sub(r"--[^\n]*", "", SQL).split())


def test_the_trigger_restarts_a_watchdog_on_create_switch_on_and_filter_edit_only() -> None:
    assert ("create trigger notification_subscriptions_active_since before insert or update of "
            "is_active, filter_spec on public.notification_subscriptions for each row execute "
            "function public.notification_subscriptions_stamp_active_since();") in CODE
    body = CODE.split("function public.notification_subscriptions_stamp_active_since()", 1)[1]
    body = body.split("$$;", 1)[0]
    assert ("if TG_OP = 'INSERT' or (new.is_active and old.is_active is distinct from true) "
            "or new.filter_spec is distinct from old.filter_spec then new.active_since := now();"
            ) in body
    assert "set search_path = ''" in body
    assert ("revoke all on function public.notification_subscriptions_stamp_active_since() "
            "from public, anon, authenticated;") in CODE


def test_a_share_notification_goes_with_its_sharer() -> None:
    assert ("add column if not exists actor_account_id uuid references public.accounts(id) "
            "on delete cascade;") in CODE
    assert "add column if not exists duplicate_of uuid references public.notification_dispatches(id) " \
           "on delete set null;" in CODE


def test_system_history_keeps_its_old_key_as_its_event_ref() -> None:
    """Else the 904 system rows would collide under W3's NULLS NOT DISTINCT identity tuple."""
    backfill = CODE.split("update public.notification_dispatches d set event_ref = case", 1)[1]
    backfill = backfill.split(";", 1)[0]
    assert "when d.source_kind = 'system_health' then d.dedupe_key" in backfill
    assert backfill.rstrip().endswith("or d.source_kind = 'system_health')")
    post = CODE.split("do $post$", 1)[1]
    assert "or source_kind = 'system_health'" in post


def test_every_statement_reruns_as_a_no_op_and_the_header_says_when_to_rerun() -> None:
    assert "RE-RUN THIS FILE AFTER THE W2a DEPLOY" in SQL
    for update in re.findall(r"update public\.notification_dispatches d set .*?;", CODE):
        assert re.search(r"where .*is null|change_kind = 'shared'", update), update
    # active_since is backfilled only by the run that adds it, so a re-run keeps every restart.
    block = CODE.split("do $active_since$", 1)[1].split("$active_since$;", 1)[0]
    assert "if not exists (select 1 from information_schema.columns" in block
    assert "update public.notification_subscriptions set active_since = created_at;" in block

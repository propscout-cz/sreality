"""Migration 654's rails, offline (notifications push W4, PROGRAM.md § 5 "Migration D"): what only the text can show.
Additive (two new tables, nothing else touched); RLS on, no policy, every browser privilege revoked, the identity
sequences too; one live row per endpoint; one row per (event, device); a reason column that cannot hold a sentence.
The statements themselves run in tests/test_notifications_push_live.py."""

from __future__ import annotations

import re

from tests.migration_defs import MIGRATIONS

SQL = (MIGRATIONS / "654_notifications_w4_push_ledger.sql").read_text(encoding="utf-8")
CODE = " ".join(re.sub(r"--[^\n]*", "", SQL).split())


def test_it_is_additive_two_new_tables_and_nothing_else_changes() -> None:
    assert re.findall(r"create table if not exists public\.(\w+)", CODE) == [
        "push_subscriptions", "notification_deliveries"]
    for verb in ("drop ", "alter table public.notification_dispatches", "alter column", "delete from",
                 "update public.", "insert into", "grant "):
        assert verb not in CODE, verb
    assert set(re.findall(r"alter table public\.(\w+)", CODE)) == {
        "push_subscriptions", "notification_deliveries"}


def test_both_tables_are_service_role_only() -> None:
    for table in ("push_subscriptions", "notification_deliveries"):
        assert f"alter table public.{table} enable row level security;" in CODE
        assert f"revoke all on public.{table} from public, anon, authenticated;" in CODE
        assert f"revoke all on sequence public.{table}_id_seq from public, anon, authenticated;" in CODE
        assert f"create table if not exists public.{table} ( id bigint generated always as identity primary key," in CODE
    assert "create policy" not in CODE
    post = CODE[CODE.index("do $post$"):]
    assert "has_table_privilege(v_role, v_rel, 'select')" in post and "pg_policies" in post


def test_a_device_has_one_live_row_per_endpoint_and_ends_without_its_secrets() -> None:
    assert ("create unique index if not exists push_subscriptions_live_endpoint_uq on public.push_subscriptions "
            "(endpoint) where ended_at is null;") in CODE
    assert "check ((ended_at is null) = (endpoint is not null))" in CODE
    assert "check ((endpoint is null) = (p256dh is null) and (endpoint is null) = (auth is null))" in CODE
    assert "check (end_reason in ('gone', 'signed_out', 'switched_off', 'replaced'))" in CODE
    assert "references public.accounts(id) on delete cascade" in CODE
    assert "check (origin ~ '^https://')" in CODE
    # A 429 holds the device, not its rows (no attempt is spent waiting out Retry-After).
    assert "last_seen_at timestamptz not null default now(), hold_until timestamptz, ended_at timestamptz," in CODE
    assert "comment on column public.push_subscriptions.hold_until is" in CODE


def test_a_delivery_is_one_row_per_event_and_device_and_one_word_of_reason() -> None:
    assert "constraint notification_deliveries_pair_uq unique (notification_id, push_subscription_id)" in CODE
    assert "references public.notification_dispatches(id) on delete cascade" in CODE
    assert "references public.push_subscriptions(id) on delete cascade" in CODE
    assert "check (status in ('pending', 'sent', 'skipped', 'gone', 'failed'))" in CODE
    assert "check (reason ~ '^[A-Za-z0-9_]{1,64}$')" in CODE
    assert ("create index if not exists notification_deliveries_due_idx on public.notification_deliveries "
            "(status, next_attempt_at) where status in ('pending', 'failed');") in CODE
    assert "status text not null default 'pending'" in CODE
    assert "next_attempt_at timestamptz not null default now()" in CODE

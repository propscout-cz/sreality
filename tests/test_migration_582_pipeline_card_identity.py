"""Offline guard over migration 582's TEXT (rule 1's additive gate, rule 22): two columns, a
unique index and two lookup indexes; nothing dropped, deleted, revoked or rewritten. Its effect
on rows is executed in CI's migrations job (tests/test_property_carriers_live.py)."""

from __future__ import annotations

import re
from pathlib import Path

_SQL = (Path(__file__).resolve().parent.parent / "migrations"
        / "582_pipeline_card_identity.sql").read_text(encoding="utf-8")
_BODY = " ".join(re.sub(r"--[^\n]*", "", _SQL).split()).lower()


def test_582_is_purely_additive() -> None:
    for forbidden in ("drop ", "delete from", "truncate", "revoke", "alter column", "rename",
                      "create or replace", "update "):
        assert forbidden not in _BODY, f"582 must not contain {forbidden!r}"


def test_every_not_null_column_it_adds_carries_a_default() -> None:
    added = re.findall(r"add column if not exists ([^;]*);", _BODY)
    assert added, "582 adds no column"
    for clause in added:
        assert "not null" not in clause or " default " in clause, clause


def test_a_card_is_minted_its_id_and_an_event_is_never_given_one() -> None:
    assert ("alter table property_pipeline add column if not exists card_id uuid not null "
            "default gen_random_uuid();") in _BODY
    assert ("create unique index if not exists property_pipeline_card_id_uq "
            "on property_pipeline (card_id);") in _BODY
    assert "alter table property_pipeline_events add column if not exists card_id uuid;" in _BODY


def test_its_lock_wait_is_bounded_inside_one_transaction() -> None:
    assert _BODY.startswith("begin; set local lock_timeout = '6s';"), _BODY[:80]
    assert _BODY.endswith("commit;")


def test_the_merge_that_last_retired_a_property_is_an_index_read() -> None:
    assert ("create index if not exists property_merge_events_retired_idx "
            "on property_merge_events (retired_property_id, id);") in _BODY

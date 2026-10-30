"""Migration 695: saved presets lose `locationMode` and `centerRadius`, the keys the map area no longer has.

Offline, text only: one UPDATE of filter_presets, removing exactly the two keys from either blob shape, with the
touch trigger paused around it (so updated_at stays the operator's last save) and a post-condition that none is
left. The schema replay applies it to an empty table.
"""

from __future__ import annotations

import re

from tests.migration_defs import MIGRATIONS

SQL = (MIGRATIONS / "695_presets_drop_dead_area_keys.sql").read_text(encoding="utf-8")
CODE = re.sub(r"--[^\n]*", "", SQL)
KEYS = "'locationMode' - 'centerRadius'"


def test_one_update_of_filter_presets_and_nothing_else() -> None:
    statements = [s.strip().split()[0].lower() for s in CODE.split(";") if s.strip()]
    assert statements.count("update") == 1
    assert not {"create", "drop", "delete", "insert", "grant", "revoke"} & set(statements)
    assert "update public.filter_presets" in CODE


def test_it_removes_exactly_the_two_keys_from_either_blob_shape() -> None:
    assert CODE.count(KEYS) == 2
    assert "jsonb_set(filter_spec, '{filters}', (filter_spec->'filters') - 'locationMode' - 'centerRadius')" in CODE
    assert "else filter_spec - 'locationMode' - 'centerRadius'" in CODE


def test_the_touch_trigger_is_paused_only_around_the_update() -> None:
    off = CODE.index("disable trigger filter_presets_touch_updated_at_trg")
    on = CODE.index("enable trigger filter_presets_touch_updated_at_trg")
    assert off < CODE.index("update public.filter_presets") < on < CODE.index("commit")


def test_it_proves_no_preset_keeps_either_key() -> None:
    assert "raise exception '695 did not land" in CODE
